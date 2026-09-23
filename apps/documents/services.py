import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import Branch, BusinessMembership, MembershipRole
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.documents.models import (
    CapturedDocument,
    DocumentAccessAction,
    DocumentAccessEvent,
    DocumentFile,
    DocumentKind,
    DocumentPurgeReason,
    DocumentScanStatus,
    DocumentStatus,
    DocumentTranscription,
    DocumentTranscriptionLine,
    DocumentTranscriptionRevision,
    TranscriptionRevisionAction,
    TranscriptionStatus,
)
from apps.documents.scanning import ScanVerdict, configured_scanner
from apps.documents.storage import document_storage
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseStatus,
    OperationalPaymentMethod,
)
from apps.expenses.services import create_operating_expense_draft
from apps.inventory.services import post_opening_balance
from apps.purchasing.models import PurchaseStatus, Supplier
from apps.purchasing.services import DraftPurchaseLine, create_purchase_draft


@dataclass(frozen=True)
class TranscriptionLineInput:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    note: str = ""


@dataclass(frozen=True)
class PurchaseTranscriptionInput:
    branch: Branch
    supplier: Supplier
    purchase_date: date
    supplier_reference: str
    expected_date: date | None
    settlement_terms: str
    lines: list[TranscriptionLineInput]


@dataclass(frozen=True)
class ExpenseTranscriptionInput:
    branch: Branch
    category: ExpenseCategory
    document_date: date
    payee: str
    description: str
    amount: Decimal
    payment_method: str
    telebirr_reference: str


@dataclass(frozen=True)
class OpeningStockTranscriptionInput:
    branch: Branch
    lines: list[TranscriptionLineInput]


@dataclass(frozen=True)
class DocumentStorageReconciliation:
    checked: int
    missing: tuple[str, ...]
    orphaned: tuple[str, ...]
    hash_mismatches: tuple[str, ...]
    stale_quarantine: tuple[UUID, ...]
    incomplete_targets: tuple[UUID, ...]
    listing_failed: bool

    @property
    def issue_count(self) -> int:
        return (
            len(self.missing)
            + len(self.orphaned)
            + len(self.hash_mismatches)
            + len(self.stale_quarantine)
            + len(self.incomplete_targets)
            + int(self.listing_failed)
        )


@dataclass(frozen=True)
class DocumentPurgeSummary:
    business_id: UUID
    document_id: UUID
    file_count: int


@dataclass(frozen=True)
class DocumentPurgeResult:
    summaries: tuple[DocumentPurgeSummary, ...]

    @property
    def file_count(self) -> int:
        return sum(summary.file_count for summary in self.summaries)


@dataclass(frozen=True)
class PreparedUpload:
    name: str
    media_type: str
    content: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.content)


ALLOWED_SIGNATURES = (
    ("image/jpeg", (b"\xff\xd8\xff",)),
    ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ("application/pdf", (b"%PDF-",)),
)
ALLOWED_EXTENSIONS = {
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "application/pdf": {".pdf"},
}


def _require_manager(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active or not actor.can_manage_documents:
        raise PermissionDenied(_("Owner or manager document permission is required."))


def _require_owner(actor: BusinessMembership) -> None:
    _require_manager(actor)
    if actor.role != MembershipRole.OWNER:
        raise PermissionDenied(_("Only an active owner can confirm document transcriptions."))


def _validate_branch(actor: BusinessMembership, branch: Branch) -> None:
    _require_manager(actor)
    if branch.business_id != actor.business_id or not branch.is_active:
        raise PermissionDenied(_("Select an active branch in this business."))


def _identify_media_type(content: bytes, filename: str) -> str:
    for media_type, signatures in ALLOWED_SIGNATURES:
        if any(content.startswith(signature) for signature in signatures):
            if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS[media_type]:
                raise ValidationError(_("The filename extension does not match the file content."))
            return media_type
    raise ValidationError(_("Upload only a valid JPEG, PNG, or PDF file."))


def _prepare_upload(upload: UploadedFile) -> PreparedUpload:
    if upload.size is None or upload.size <= 0:
        raise ValidationError(_("Empty files cannot be uploaded."))
    if upload.size > settings.DOCUMENT_MAX_FILE_BYTES:
        raise ValidationError(_("Each document file must be 10 MiB or smaller."))
    digest = hashlib.sha256()
    content = bytearray()
    for chunk in upload.chunks():
        digest.update(chunk)
        content.extend(chunk)
        if len(content) > settings.DOCUMENT_MAX_FILE_BYTES:
            raise ValidationError(_("Each document file must be 10 MiB or smaller."))
    raw = bytes(content)
    upload_name = upload.name or "upload"
    return PreparedUpload(
        name=Path(upload_name).name[:255],
        media_type=_identify_media_type(raw, upload_name),
        content=raw,
        sha256=digest.hexdigest(),
    )


def _file_fingerprint(files: list[PreparedUpload]) -> str:
    digest = hashlib.sha256()
    for item in files:
        digest.update(item.sha256.encode("ascii"))
        digest.update(str(item.size).encode("ascii"))
        digest.update(item.media_type.encode("ascii"))
    return digest.hexdigest()


def _fingerprint(
    *,
    branch: Branch,
    kind: str,
    files: list[PreparedUpload],
) -> str:
    digest = hashlib.sha256()
    digest.update(str(branch.pk).encode("ascii"))
    digest.update(kind.encode("ascii"))
    digest.update(_file_fingerprint(files).encode("ascii"))
    return digest.hexdigest()


def _new_transcription(
    *,
    actor: BusinessMembership,
    document: CapturedDocument,
    replacement_of: DocumentTranscription | None = None,
) -> DocumentTranscription:
    latest_attempt = document.transcriptions.aggregate(number=Max("attempt_number"))["number"] or 0
    transcription = DocumentTranscription.objects.create(
        business=actor.business,
        branch=document.branch,
        document=document,
        attempt_number=latest_attempt + 1,
        replacement_of=replacement_of,
        created_by=actor,
    )
    _record_revision(
        transcription=transcription,
        actor=actor,
        action=TranscriptionRevisionAction.CREATED,
    )
    return transcription


def capture_document(
    *,
    actor: BusinessMembership,
    branch: Branch,
    kind: str,
    title: str,
    uploads: list[UploadedFile],
) -> tuple[CapturedDocument, bool]:
    _validate_branch(actor, branch)
    if kind not in DocumentKind.values:
        raise ValidationError(_("Select a supported document workflow."))
    if not uploads or len(uploads) > settings.DOCUMENT_MAX_FILES:
        raise ValidationError(_("Select between one and five source files."))
    prepared = [_prepare_upload(upload) for upload in uploads]
    total_size = sum(item.size for item in prepared)
    if total_size > settings.DOCUMENT_MAX_TOTAL_BYTES:
        raise ValidationError(_("The combined document files must be 25 MiB or smaller."))
    fingerprint = _fingerprint(branch=branch, kind=kind, files=prepared)
    legacy_fingerprint = _file_fingerprint(prepared)
    existing = (
        CapturedDocument.objects.filter(
            business=actor.business,
            branch=branch,
            kind=kind,
            fingerprint__in=(fingerprint, legacy_fingerprint),
        )
        .exclude(status=DocumentStatus.CANCELLED)
        .first()
    )
    if existing is not None:
        return existing, True
    stored_files: list[DocumentFile] = []
    try:
        with transaction.atomic():
            document = CapturedDocument.objects.create(
                business=actor.business,
                branch=branch,
                kind=kind,
                title=title.strip(),
                fingerprint=fingerprint,
                total_size=total_size,
                uploaded_by=actor,
            )
            for position, item in enumerate(prepared, start=1):
                document_file = DocumentFile(
                    business=actor.business,
                    document=document,
                    position=position,
                    original_name=item.name,
                    media_type=item.media_type,
                    size=item.size,
                    sha256=item.sha256,
                )
                document_file.source.save(item.name, ContentFile(item.content), save=False)
                document_file.save()
                stored_files.append(document_file)
            _new_transcription(actor=actor, document=document)
    except IntegrityError:
        for document_file in stored_files:
            document_file.source.storage.delete(document_file.source.name)
        existing = CapturedDocument.objects.get(
            business=actor.business,
            branch=branch,
            kind=kind,
            fingerprint=fingerprint,
            status__in=(
                DocumentStatus.QUARANTINED,
                DocumentStatus.AVAILABLE,
                DocumentStatus.REJECTED,
            ),
        )
        return existing, True
    scan_document_files(actor=actor, document=document)
    document.refresh_from_db()
    return document, False


def _scan_file(document_file: DocumentFile) -> None:
    scanner = configured_scanner()
    with document_file.source.open("rb") as source:
        result = scanner.scan(source)
    document_file.scan_engine = result.engine
    document_file.malware_signature = result.signature
    document_file.scan_error = result.detail
    document_file.scanned_at = timezone.now()
    if result.verdict == ScanVerdict.CLEAN:
        document_file.scan_status = DocumentScanStatus.CLEAN
    elif result.verdict == ScanVerdict.INFECTED:
        document_file.scan_status = DocumentScanStatus.INFECTED
        document_file.source.storage.delete(document_file.source.name)
        document_file.purged_at = timezone.now()
        document_file.purge_reason = DocumentPurgeReason.INFECTED
    else:
        document_file.scan_status = DocumentScanStatus.ERROR
    document_file.save(
        update_fields=(
            "scan_engine",
            "malware_signature",
            "scan_error",
            "scanned_at",
            "scan_status",
            "purged_at",
            "purge_reason",
        )
    )


def scan_document_files(
    *,
    actor: BusinessMembership,
    document: CapturedDocument,
) -> CapturedDocument:
    _require_manager(actor)
    with transaction.atomic():
        locked = CapturedDocument.objects.select_for_update().get(
            pk=document.pk,
            business=actor.business,
        )
        if locked.status not in {DocumentStatus.QUARANTINED, DocumentStatus.REJECTED}:
            return locked
        for document_file in locked.files.select_for_update():
            if document_file.purged_at is not None:
                continue
            if document_file.scan_status in {
                DocumentScanStatus.PENDING,
                DocumentScanStatus.ERROR,
            }:
                _scan_file(document_file)
        statuses = set(locked.files.values_list("scan_status", flat=True))
        if DocumentScanStatus.INFECTED in statuses:
            locked.status = DocumentStatus.REJECTED
            locked.rejection_reason = _("A source file failed the malware scan.")
        elif statuses == {DocumentScanStatus.CLEAN}:
            locked.status = DocumentStatus.AVAILABLE
            locked.rejection_reason = ""
        else:
            locked.status = DocumentStatus.QUARANTINED
            locked.rejection_reason = _("A source file is awaiting a clean malware scan.")
        locked.save(update_fields=("status", "rejection_reason", "updated_at"))
        return locked


def _line_snapshot(line: DocumentTranscriptionLine) -> dict[str, str | int]:
    return {
        "position": line.position,
        "variant_id": str(line.variant_id),
        "variant": str(line.variant),
        "quantity": str(line.quantity),
        "unit_cost": str(line.unit_cost),
        "note": line.note,
        "target_purchase_line_id": (
            str(line.target_purchase_line_id) if line.target_purchase_line_id else ""
        ),
        "target_stock_operation_id": (
            str(line.target_stock_operation_id) if line.target_stock_operation_id else ""
        ),
    }


def transcription_snapshot(transcription: DocumentTranscription) -> dict[str, object]:
    return {
        "document_id": str(transcription.document_id),
        "attempt_number": transcription.attempt_number,
        "status": transcription.status,
        "branch_id": str(transcription.branch_id),
        "supplier_id": str(transcription.supplier_id) if transcription.supplier_id else "",
        "purchase_date": str(transcription.purchase_date or ""),
        "supplier_reference": transcription.supplier_reference,
        "expected_date": str(transcription.expected_date or ""),
        "settlement_terms": transcription.settlement_terms,
        "expense_category_id": (
            str(transcription.expense_category_id) if transcription.expense_category_id else ""
        ),
        "expense_document_date": str(transcription.expense_document_date or ""),
        "expense_payee": transcription.expense_payee,
        "expense_description": transcription.expense_description,
        "expense_amount": str(transcription.expense_amount or ""),
        "expense_payment_method": transcription.expense_payment_method,
        "expense_telebirr_reference": transcription.expense_telebirr_reference,
        "target_purchase_id": (
            str(transcription.target_purchase_id) if transcription.target_purchase_id else ""
        ),
        "target_expense_id": (
            str(transcription.target_expense_id) if transcription.target_expense_id else ""
        ),
        "lines": [
            _line_snapshot(line)
            for line in transcription.lines.select_related("variant", "variant__product")
        ],
    }


def _record_revision(
    *,
    transcription: DocumentTranscription,
    actor: BusinessMembership,
    action: str,
    reason: str = "",
) -> DocumentTranscriptionRevision:
    sequence = (transcription.revisions.aggregate(number=Max("sequence"))["number"] or 0) + 1
    return DocumentTranscriptionRevision.objects.create(
        business=actor.business,
        transcription=transcription,
        sequence=sequence,
        action=action,
        snapshot=transcription_snapshot(transcription),
        reason=reason.strip(),
        actor=actor,
    )


def _lock_draft(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
) -> DocumentTranscription:
    _require_manager(actor)
    locked = (
        DocumentTranscription.objects.select_for_update(of=("self",))
        .select_related("document")
        .get(
            pk=transcription.pk,
            business=actor.business,
        )
    )
    if locked.status != TranscriptionStatus.DRAFT:
        raise ValidationError(_("Only a draft transcription can be changed."))
    if locked.document.status != DocumentStatus.AVAILABLE:
        raise ValidationError(_("A clean available source document is required."))
    return locked


def _validate_lines(
    *,
    actor: BusinessMembership,
    lines: list[TranscriptionLineInput],
) -> list[TranscriptionLineInput]:
    if not lines:
        raise ValidationError(_("Enter at least one document line."))
    variant_ids: set[UUID] = set()
    for line in lines:
        if (
            line.variant.business_id != actor.business_id
            or not line.variant.is_active
            or line.variant.id in variant_ids
        ):
            raise ValidationError(
                _("Select each active product variant in this business only once.")
            )
        validate_stock_quantity(line.quantity, line.variant.stock_unit)
        if line.unit_cost < 0:
            raise ValidationError(_("Unit cost cannot be negative."))
        variant_ids.add(line.variant.id)
    return lines


def _replace_lines(
    *,
    transcription: DocumentTranscription,
    lines: list[TranscriptionLineInput],
) -> None:
    transcription.lines.all().delete()
    for position, line in enumerate(lines, start=1):
        DocumentTranscriptionLine.objects.create(
            business=transcription.business,
            transcription=transcription,
            position=position,
            variant=line.variant,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            note=line.note.strip(),
        )


@transaction.atomic
def save_purchase_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    data: PurchaseTranscriptionInput,
) -> DocumentTranscription:
    locked = _lock_draft(actor=actor, transcription=transcription)
    if locked.document.kind != DocumentKind.PURCHASE:
        raise ValidationError(_("This source is not a supplier purchase document."))
    _validate_branch(actor, data.branch)
    if data.supplier.business_id != actor.business_id or not data.supplier.is_active:
        raise ValidationError(_("Select an active supplier in this business."))
    if data.expected_date is not None and data.expected_date < data.purchase_date:
        raise ValidationError(_("Expected date cannot be earlier than purchase date."))
    lines = _validate_lines(actor=actor, lines=data.lines)
    locked.branch = data.branch
    locked.supplier = data.supplier
    locked.purchase_date = data.purchase_date
    locked.supplier_reference = data.supplier_reference.strip()
    locked.expected_date = data.expected_date
    locked.settlement_terms = data.settlement_terms.strip()
    locked.save()
    _replace_lines(transcription=locked, lines=lines)
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.UPDATED,
    )
    return locked


@transaction.atomic
def save_expense_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    data: ExpenseTranscriptionInput,
) -> DocumentTranscription:
    locked = _lock_draft(actor=actor, transcription=transcription)
    if locked.document.kind != DocumentKind.EXPENSE:
        raise ValidationError(_("This source is not an operating-expense document."))
    _validate_branch(actor, data.branch)
    if data.category.business_id != actor.business_id or not data.category.is_active:
        raise ValidationError(_("Select an active expense category in this business."))
    amount = data.amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError(_("Expense amount must be greater than zero."))
    reference = data.telebirr_reference.strip()
    if data.payment_method == OperationalPaymentMethod.CASH and reference:
        raise ValidationError(_("Cash evidence cannot include a Telebirr reference."))
    if data.payment_method == OperationalPaymentMethod.TELEBIRR and not reference:
        raise ValidationError(_("A Telebirr reference is required."))
    if data.payment_method not in OperationalPaymentMethod.values:
        raise ValidationError(_("Select cash or Telebirr."))
    locked.branch = data.branch
    locked.expense_category = data.category
    locked.expense_document_date = data.document_date
    locked.expense_payee = data.payee.strip()
    locked.expense_description = data.description.strip()
    locked.expense_amount = amount
    locked.expense_payment_method = data.payment_method
    locked.expense_telebirr_reference = reference
    locked.save()
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.UPDATED,
    )
    return locked


@transaction.atomic
def save_opening_stock_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    data: OpeningStockTranscriptionInput,
) -> DocumentTranscription:
    locked = _lock_draft(actor=actor, transcription=transcription)
    if locked.document.kind != DocumentKind.OPENING_STOCK:
        raise ValidationError(_("This source is not an opening-stock document."))
    _validate_branch(actor, data.branch)
    lines = _validate_lines(actor=actor, lines=data.lines)
    locked.branch = data.branch
    locked.save()
    _replace_lines(transcription=locked, lines=lines)
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.UPDATED,
    )
    return locked


def _validate_complete(transcription: DocumentTranscription) -> None:
    if transcription.document.kind == DocumentKind.PURCHASE:
        if transcription.supplier_id is None or transcription.purchase_date is None:
            raise ValidationError(_("Complete the purchase transcription before submitting."))
        if not transcription.lines.exists():
            raise ValidationError(_("Enter at least one purchase line."))
    elif transcription.document.kind == DocumentKind.EXPENSE:
        if (
            transcription.expense_category_id is None
            or transcription.expense_document_date is None
            or transcription.expense_amount is None
            or not transcription.expense_payee.strip()
            or not transcription.expense_description.strip()
        ):
            raise ValidationError(_("Complete the expense transcription before submitting."))
    elif not transcription.lines.exists():
        raise ValidationError(_("Enter at least one opening-stock line."))


@transaction.atomic
def submit_transcription_for_confirmation(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
) -> DocumentTranscription:
    locked = _lock_draft(actor=actor, transcription=transcription)
    _validate_complete(locked)
    locked.status = TranscriptionStatus.AWAITING_OWNER_CONFIRMATION
    locked.submitted_by = actor
    locked.submitted_at = timezone.now()
    locked.return_reason = ""
    locked.save()
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.SUBMITTED,
    )
    return locked


@transaction.atomic
def return_transcription_to_draft(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    reason: str,
) -> DocumentTranscription:
    _require_owner(actor)
    if not reason.strip():
        raise ValidationError(_("Explain what must be corrected."))
    locked = DocumentTranscription.objects.select_for_update().get(
        pk=transcription.pk,
        business=actor.business,
    )
    if locked.status != TranscriptionStatus.AWAITING_OWNER_CONFIRMATION:
        raise ValidationError(_("Only a submitted transcription can be returned."))
    locked.status = TranscriptionStatus.DRAFT
    locked.return_reason = reason.strip()
    locked.save()
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.RETURNED,
        reason=reason,
    )
    return locked


@transaction.atomic
def confirm_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    confirmation_key: UUID,
) -> DocumentTranscription:
    _require_owner(actor)
    locked = (
        DocumentTranscription.objects.select_for_update(of=("self",))
        .select_related(
            "document",
            "branch",
            "supplier",
            "expense_category",
        )
        .get(pk=transcription.pk, business=actor.business)
    )
    if locked.status == TranscriptionStatus.CONFIRMED:
        if locked.confirmation_key != confirmation_key:
            raise ValidationError(_("This transcription is already confirmed."))
        return locked
    if locked.status != TranscriptionStatus.AWAITING_OWNER_CONFIRMATION:
        raise ValidationError(_("Only a submitted transcription can be confirmed."))
    _validate_complete(locked)
    if locked.document.kind == DocumentKind.PURCHASE:
        if locked.supplier is None or locked.purchase_date is None:
            raise ValidationError(_("Purchase evidence is incomplete."))
        purchase = create_purchase_draft(
            actor=actor,
            branch=locked.branch,
            supplier=locked.supplier,
            supplier_reference=locked.supplier_reference,
            purchase_date=locked.purchase_date,
            expected_date=locked.expected_date,
            settlement_terms=locked.settlement_terms,
            lines=[
                DraftPurchaseLine(
                    variant=line.variant,
                    quantity=line.quantity,
                    unit_cost=line.unit_cost,
                )
                for line in locked.lines.select_related("variant")
            ],
        )
        locked.target_purchase = purchase
        purchase_lines = {line.variant_id: line for line in purchase.lines.all()}
        for line in locked.lines.all():
            line.target_purchase_line = purchase_lines[line.variant_id]
            line.save(update_fields=("target_purchase_line",))
    elif locked.document.kind == DocumentKind.EXPENSE:
        if locked.expense_category is None or locked.expense_amount is None:
            raise ValidationError(_("Expense evidence is incomplete."))
        locked.target_expense = create_operating_expense_draft(
            actor=actor,
            branch=locked.branch,
            category=locked.expense_category,
            payee=locked.expense_payee,
            description=locked.expense_description,
            amount=locked.expense_amount,
        )
    locked.status = TranscriptionStatus.CONFIRMED
    locked.confirmation_key = confirmation_key
    locked.confirmed_by = actor
    locked.confirmed_at = timezone.now()
    locked.save()
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.CONFIRMED,
    )
    return locked


@transaction.atomic
def post_confirmed_opening_stock(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
) -> DocumentTranscription:
    _require_manager(actor)
    locked = (
        DocumentTranscription.objects.select_for_update()
        .select_related("document", "branch")
        .get(pk=transcription.pk, business=actor.business)
    )
    if (
        locked.status != TranscriptionStatus.CONFIRMED
        or locked.document.kind != DocumentKind.OPENING_STOCK
    ):
        raise ValidationError(_("Confirm an opening-stock transcription before posting it."))
    for line in locked.lines.select_for_update().select_related("variant"):
        operation = post_opening_balance(
            actor=actor,
            branch=locked.branch,
            variant=line.variant,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            idempotency_key=uuid.uuid5(locked.id, str(line.id)),
        )
        if line.target_stock_operation_id is None:
            line.target_stock_operation = operation
            line.save(update_fields=("target_stock_operation",))
        elif line.target_stock_operation_id != operation.id:
            raise ValidationError(_("Opening-stock posting evidence is inconsistent."))
    return locked


@transaction.atomic
def cancel_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
    reason: str,
) -> DocumentTranscription:
    _require_manager(actor)
    cancellation_reason = reason.strip()
    if not cancellation_reason:
        raise ValidationError(_("Explain why this transcription is being cancelled."))
    locked = DocumentTranscription.objects.select_for_update().get(
        pk=transcription.pk,
        business=actor.business,
    )
    if locked.status not in {
        TranscriptionStatus.DRAFT,
        TranscriptionStatus.AWAITING_OWNER_CONFIRMATION,
    }:
        raise ValidationError(_("Only unconfirmed work can be cancelled."))
    locked.status = TranscriptionStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.cancellation_reason = cancellation_reason
    locked.save()
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.CANCELLED,
        reason=cancellation_reason,
    )
    return locked


@transaction.atomic
def start_replacement_transcription(
    *,
    actor: BusinessMembership,
    transcription: DocumentTranscription,
) -> DocumentTranscription:
    _require_manager(actor)
    locked = (
        DocumentTranscription.objects.select_for_update(of=("self",))
        .select_related("document", "target_purchase", "target_expense")
        .get(pk=transcription.pk, business=actor.business)
    )
    if locked.status != TranscriptionStatus.CONFIRMED:
        raise ValidationError(_("Only confirmed evidence can be replaced."))
    if locked.document.kind == DocumentKind.PURCHASE and (
        locked.target_purchase is None or locked.target_purchase.status != PurchaseStatus.CANCELLED
    ):
        raise ValidationError(_("Cancel the source-derived purchase before replacing it."))
    if locked.document.kind == DocumentKind.EXPENSE and (
        locked.target_expense is None or locked.target_expense.status != ExpenseStatus.CANCELLED
    ):
        raise ValidationError(_("Cancel the source-derived expense before replacing it."))
    if (
        locked.document.kind == DocumentKind.OPENING_STOCK
        and locked.lines.filter(target_stock_operation__isnull=False).exists()
    ):
        raise ValidationError(_("Posted opening-stock evidence cannot be replaced."))
    replacement = _new_transcription(
        actor=actor,
        document=locked.document,
        replacement_of=locked,
    )
    replacement.branch = locked.branch
    replacement.supplier = locked.supplier
    replacement.purchase_date = locked.purchase_date
    replacement.supplier_reference = locked.supplier_reference
    replacement.expected_date = locked.expected_date
    replacement.settlement_terms = locked.settlement_terms
    replacement.expense_category = locked.expense_category
    replacement.expense_document_date = locked.expense_document_date
    replacement.expense_payee = locked.expense_payee
    replacement.expense_description = locked.expense_description
    replacement.expense_amount = locked.expense_amount
    replacement.expense_payment_method = locked.expense_payment_method
    replacement.expense_telebirr_reference = locked.expense_telebirr_reference
    replacement.save()
    for line in locked.lines.select_related("variant"):
        DocumentTranscriptionLine.objects.create(
            business=actor.business,
            transcription=replacement,
            position=line.position,
            variant=line.variant,
            quantity=line.quantity,
            unit_cost=line.unit_cost,
            note=line.note,
        )
    _record_revision(
        transcription=locked,
        actor=actor,
        action=TranscriptionRevisionAction.REPLACED,
        reason=str(replacement.id),
    )
    _record_revision(
        transcription=replacement,
        actor=actor,
        action=TranscriptionRevisionAction.UPDATED,
    )
    return replacement


@transaction.atomic
def cancel_document(
    *,
    actor: BusinessMembership,
    document: CapturedDocument,
    reason: str,
) -> CapturedDocument:
    _require_manager(actor)
    cancellation_reason = reason.strip()
    if not cancellation_reason:
        raise ValidationError(_("Explain why this captured document is being cancelled."))
    transcriptions = list(
        DocumentTranscription.objects.select_for_update()
        .filter(
            document_id=document.pk,
            business=actor.business,
        )
        .order_by("attempt_number")
    )
    locked = CapturedDocument.objects.select_for_update().get(
        pk=document.pk,
        business=actor.business,
    )
    if any(
        transcription.status == TranscriptionStatus.CONFIRMED for transcription in transcriptions
    ):
        raise ValidationError(_("A document with confirmed evidence cannot be cancelled."))
    for transcription in transcriptions:
        if transcription.status not in {
            TranscriptionStatus.DRAFT,
            TranscriptionStatus.AWAITING_OWNER_CONFIRMATION,
        }:
            continue
        cancel_transcription(
            actor=actor,
            transcription=transcription,
            reason=cancellation_reason,
        )
    locked.status = DocumentStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.cancellation_reason = cancellation_reason
    locked.save()
    return locked


def record_document_access(
    *,
    actor: BusinessMembership,
    document_file: DocumentFile,
    action: str,
) -> DocumentAccessEvent:
    _require_manager(actor)
    if (
        document_file.business_id != actor.business_id
        or document_file.document.business_id != actor.business_id
        or document_file.document.status != DocumentStatus.AVAILABLE
        or document_file.scan_status != DocumentScanStatus.CLEAN
        or document_file.purged_at is not None
    ):
        raise PermissionDenied(_("Document access is not available."))
    if action not in DocumentAccessAction.values:
        raise ValidationError(_("Select a valid document access action."))
    return DocumentAccessEvent.objects.create(
        business=actor.business,
        document=document_file.document,
        document_file=document_file,
        action=action,
        actor=actor,
    )


def purge_eligible_document_files(
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> DocumentPurgeResult:
    timestamp = now or timezone.now()
    cutoff = timestamp - timedelta(days=settings.DOCUMENT_CANCELLED_RETENTION_DAYS)
    files = DocumentFile.objects.filter(
        document__status=DocumentStatus.CANCELLED,
        document__cancelled_at__lte=cutoff,
        purged_at__isnull=True,
    ).order_by("business_id", "document_id", "position")
    counts: dict[tuple[UUID, UUID], int] = {}
    for document_file in files.iterator():
        key = (document_file.business_id, document_file.document_id)
        counts[key] = counts.get(key, 0) + 1
        if not dry_run:
            document_file.source.storage.delete(document_file.source.name)
            document_file.purged_at = timestamp
            document_file.purge_reason = DocumentPurgeReason.CANCELLED_RETENTION_EXPIRED
            document_file.save(update_fields=("purged_at", "purge_reason"))
    return DocumentPurgeResult(
        summaries=tuple(
            DocumentPurgeSummary(
                business_id=business_id,
                document_id=document_id,
                file_count=file_count,
            )
            for (business_id, document_id), file_count in counts.items()
        )
    )


def _stored_document_names(prefix: str = "documents") -> tuple[str, ...]:
    storage = document_storage()
    pending = [prefix]
    names: list[str] = []
    while pending:
        current = pending.pop()
        try:
            directories, files = storage.listdir(current)
        except FileNotFoundError:
            continue
        names.extend(f"{current}/{filename}" for filename in files)
        pending.extend(f"{current}/{directory}" for directory in directories)
    return tuple(sorted(names))


def reconcile_document_storage(
    *,
    now: datetime | None = None,
) -> DocumentStorageReconciliation:
    timestamp = now or timezone.now()
    stale_cutoff = timestamp - timedelta(hours=settings.DOCUMENT_QUARANTINE_STALE_HOURS)
    active_files = list(
        DocumentFile.objects.filter(purged_at__isnull=True).select_related("document")
    )
    referenced_names = {document_file.source.name for document_file in active_files}
    missing: list[str] = []
    hash_mismatches: list[str] = []
    for document_file in active_files:
        storage = document_file.source.storage
        if not storage.exists(document_file.source.name):
            missing.append(document_file.source.name)
            continue
        digest = hashlib.sha256()
        with document_file.source.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != document_file.sha256:
            hash_mismatches.append(document_file.source.name)

    listing_failed = False
    orphaned: tuple[str, ...] = ()
    try:
        stored_names = set(_stored_document_names())
    except (NotImplementedError, OSError):
        listing_failed = True
    else:
        orphaned = tuple(sorted(stored_names - referenced_names))

    stale_quarantine = tuple(
        CapturedDocument.objects.filter(
            status=DocumentStatus.QUARANTINED,
            updated_at__lte=stale_cutoff,
        )
        .order_by("updated_at")
        .values_list("id", flat=True)
    )
    incomplete_targets: list[UUID] = []
    transcriptions = (
        DocumentTranscription.objects.filter(status=TranscriptionStatus.CONFIRMED)
        .select_related("document")
        .prefetch_related("lines")
    )
    for transcription in transcriptions:
        lines = list(transcription.lines.all())
        if transcription.document.kind == DocumentKind.PURCHASE:
            if transcription.target_purchase_id is None or any(
                line.target_purchase_line_id is None for line in lines
            ):
                incomplete_targets.append(transcription.id)
        elif transcription.document.kind == DocumentKind.EXPENSE:
            if transcription.target_expense_id is None:
                incomplete_targets.append(transcription.id)
        else:
            linked_lines = sum(line.target_stock_operation_id is not None for line in lines)
            if 0 < linked_lines < len(lines):
                incomplete_targets.append(transcription.id)

    return DocumentStorageReconciliation(
        checked=len(active_files),
        missing=tuple(sorted(missing)),
        orphaned=orphaned,
        hash_mismatches=tuple(sorted(hash_mismatches)),
        stale_quarantine=stale_quarantine,
        incomplete_targets=tuple(incomplete_targets),
        listing_failed=listing_failed,
    )


def purge_orphaned_document_objects(
    reconciliation: DocumentStorageReconciliation,
    *,
    now: datetime | None = None,
) -> int:
    storage = document_storage()
    cutoff = (now or timezone.now()) - timedelta(hours=settings.DOCUMENT_ORPHAN_RETENTION_HOURS)
    deleted = 0
    for name in reconciliation.orphaned:
        try:
            if storage.get_modified_time(name) > cutoff:
                continue
        except FileNotFoundError:
            continue
        storage.delete(name)
        deleted += 1
    return deleted
