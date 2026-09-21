import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant
from apps.documents.storage import document_storage, document_upload_path
from apps.expenses.models import ExpenseCategory, OperatingExpense, OperationalPaymentMethod
from apps.inventory.models import StockOperation
from apps.purchasing.models import Purchase, PurchaseLine, Supplier


class DocumentKind(models.TextChoices):
    PURCHASE = "purchase", _("Supplier purchase document")
    EXPENSE = "expense", _("Operating-expense document")
    OPENING_STOCK = "opening_stock", _("Opening-stock document")


class DocumentStatus(models.TextChoices):
    QUARANTINED = "quarantined", _("Quarantined")
    AVAILABLE = "available", _("Available")
    REJECTED = "rejected", _("Rejected")
    CANCELLED = "cancelled", _("Cancelled")


class DocumentScanStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    CLEAN = "clean", _("Clean")
    INFECTED = "infected", _("Malicious")
    ERROR = "error", _("Scanner error")


class TranscriptionStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    AWAITING_OWNER_CONFIRMATION = (
        "awaiting_owner_confirmation",
        _("Awaiting owner confirmation"),
    )
    CONFIRMED = "confirmed", _("Confirmed")
    CANCELLED = "cancelled", _("Cancelled")


class TranscriptionRevisionAction(models.TextChoices):
    CREATED = "created", _("Created")
    UPDATED = "updated", _("Updated")
    SUBMITTED = "submitted", _("Submitted")
    RETURNED = "returned", _("Returned")
    CONFIRMED = "confirmed", _("Confirmed")
    CANCELLED = "cancelled", _("Cancelled")
    REPLACED = "replaced", _("Replaced")


class DocumentAccessAction(models.TextChoices):
    PREVIEW = "preview", _("Preview")
    DOWNLOAD = "download", _("Download")


class DocumentPurgeReason(models.TextChoices):
    INFECTED = "infected", _("Malware scan rejection")
    CANCELLED_RETENTION_EXPIRED = (
        "cancelled_retention_expired",
        _("Cancelled-document retention expired"),
    )


class CapturedDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="captured_documents",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="captured_documents",
    )
    kind = models.CharField(max_length=24, choices=DocumentKind.choices)
    status = models.CharField(
        max_length=24,
        choices=DocumentStatus.choices,
        default=DocumentStatus.QUARANTINED,
    )
    title = models.CharField(max_length=180, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True)
    total_size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="documents_uploaded",
    )
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="documents_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    rejection_reason = models.CharField(max_length=180, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=DocumentKind.values),
                name="documents_kind_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=DocumentStatus.values),
                name="documents_status_is_valid",
            ),
            models.UniqueConstraint(
                fields=("business", "branch", "kind", "fingerprint"),
                condition=~Q(fingerprint="") & ~Q(status=DocumentStatus.CANCELLED),
                name="documents_unique_active_capture_scope",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=DocumentStatus.CANCELLED,
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                    )
                    & ~Q(cancellation_reason="")
                )
                | (
                    ~Q(status=DocumentStatus.CANCELLED)
                    & Q(
                        cancelled_by__isnull=True,
                        cancelled_at__isnull=True,
                        cancellation_reason="",
                    )
                ),
                name="documents_cancellation_evidence_matches",
            ),
        ]

    def __str__(self) -> str:
        return self.title or f"{self.get_kind_display()} — {self.created_at:%Y-%m-%d}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.uploaded_by_id and self.uploaded_by.business_id != self.business_id:
            errors["uploaded_by"] = ValidationError(_("Uploader must belong to this business."))
        cancelled_by = self.cancelled_by
        if cancelled_by is not None and cancelled_by.business_id != self.business_id:
            errors["cancelled_by"] = ValidationError(
                _("Cancelling membership must belong to this business.")
            )
        if errors:
            raise ValidationError(errors)

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Captured documents cannot be deleted."))

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = CapturedDocument.objects.get(pk=self.pk)
            immutable = (
                self.business_id,
                self.branch_id,
                self.kind,
                self.title,
                self.fingerprint,
                self.total_size,
                self.uploaded_by_id,
            )
            stored_immutable = (
                stored.business_id,
                stored.branch_id,
                stored.kind,
                stored.title,
                stored.fingerprint,
                stored.total_size,
                stored.uploaded_by_id,
            )
            if immutable != stored_immutable:
                raise ValidationError(_("Captured source details cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class DocumentFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="document_files",
    )
    document = models.ForeignKey(
        CapturedDocument,
        on_delete=models.PROTECT,
        related_name="files",
    )
    position = models.PositiveSmallIntegerField()
    source = models.FileField(
        storage=document_storage,
        upload_to=document_upload_path,
        max_length=255,
    )
    original_name = models.CharField(max_length=255)
    media_type = models.CharField(max_length=64)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    scan_status = models.CharField(
        max_length=16,
        choices=DocumentScanStatus.choices,
        default=DocumentScanStatus.PENDING,
    )
    scan_engine = models.CharField(max_length=120, blank=True)
    malware_signature = models.CharField(max_length=180, blank=True)
    scan_error = models.CharField(max_length=180, blank=True)
    scanned_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)
    purge_reason = models.CharField(
        max_length=40,
        choices=DocumentPurgeReason.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("document", "position"),
                name="documents_unique_file_position",
            ),
            models.CheckConstraint(
                condition=Q(position__gte=1) & Q(position__lte=5),
                name="documents_file_position_range",
            ),
            models.CheckConstraint(
                condition=Q(scan_status__in=DocumentScanStatus.values),
                name="documents_scan_status_is_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(purged_at__isnull=True, purge_reason="")
                    | (Q(purged_at__isnull=False) & Q(purge_reason__in=DocumentPurgeReason.values))
                ),
                name="documents_purge_evidence_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document} — {self.position}"

    def clean(self) -> None:
        super().clean()
        if self.document_id and self.document.business_id != self.business_id:
            raise ValidationError(
                {"document": _("Document file must belong to the same business.")}
            )

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = DocumentFile.objects.get(pk=self.pk)
            immutable = (
                self.business_id,
                self.document_id,
                self.position,
                self.source.name,
                self.original_name,
                self.media_type,
                self.size,
                self.sha256,
            )
            stored_immutable = (
                stored.business_id,
                stored.document_id,
                stored.position,
                stored.source.name,
                stored.original_name,
                stored.media_type,
                stored.size,
                stored.sha256,
            )
            if immutable != stored_immutable:
                raise ValidationError(_("Document source files cannot be replaced."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class DocumentTranscription(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="document_transcriptions",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="document_transcriptions",
    )
    document = models.ForeignKey(
        CapturedDocument,
        on_delete=models.PROTECT,
        related_name="transcriptions",
    )
    attempt_number = models.PositiveSmallIntegerField()
    replacement_of = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="replacements",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=32,
        choices=TranscriptionStatus.choices,
        default=TranscriptionStatus.DRAFT,
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="document_transcriptions",
        null=True,
        blank=True,
    )
    purchase_date = models.DateField(null=True, blank=True)
    supplier_reference = models.CharField(max_length=120, blank=True)
    expected_date = models.DateField(null=True, blank=True)
    settlement_terms = models.CharField(max_length=180, blank=True)
    expense_category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name="document_transcriptions",
        null=True,
        blank=True,
    )
    expense_document_date = models.DateField(null=True, blank=True)
    expense_payee = models.CharField(max_length=180, blank=True)
    expense_description = models.TextField(blank=True)
    expense_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        null=True,
        blank=True,
    )
    expense_payment_method = models.CharField(
        max_length=16,
        choices=OperationalPaymentMethod.choices,
        blank=True,
    )
    expense_telebirr_reference = models.CharField(max_length=120, blank=True)
    target_purchase = models.OneToOneField(
        Purchase,
        on_delete=models.PROTECT,
        related_name="source_document_transcription",
        null=True,
        blank=True,
    )
    target_expense = models.OneToOneField(
        OperatingExpense,
        on_delete=models.PROTECT,
        related_name="source_document_transcription",
        null=True,
        blank=True,
    )
    confirmation_key = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_transcriptions_created",
    )
    submitted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_transcriptions_submitted",
        null=True,
        blank=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_transcriptions_confirmed",
        null=True,
        blank=True,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_transcriptions_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    return_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("document", "attempt_number")
        constraints = [
            models.UniqueConstraint(
                fields=("document", "attempt_number"),
                name="documents_unique_transcription_attempt",
            ),
            models.UniqueConstraint(
                fields=("document",),
                condition=Q(
                    status__in=(
                        TranscriptionStatus.DRAFT,
                        TranscriptionStatus.AWAITING_OWNER_CONFIRMATION,
                    )
                ),
                name="documents_one_active_transcription",
            ),
            models.UniqueConstraint(
                fields=("business", "confirmation_key"),
                condition=Q(confirmation_key__isnull=False),
                name="documents_unique_confirmation_key",
            ),
            models.CheckConstraint(
                condition=Q(status__in=TranscriptionStatus.values),
                name="documents_transcription_status_is_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=TranscriptionStatus.CANCELLED,
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                    )
                    & ~Q(cancellation_reason="")
                )
                | (
                    ~Q(status=TranscriptionStatus.CANCELLED)
                    & Q(
                        cancelled_by__isnull=True,
                        cancelled_at__isnull=True,
                        cancellation_reason="",
                    )
                ),
                name="documents_transcription_cancellation_evidence_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.document} — attempt {self.attempt_number}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.document_id and self.document.business_id != self.business_id:
            errors["document"] = ValidationError(_("Source document must belong to this business."))
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        supplier = self.supplier
        if supplier is not None and supplier.business_id != self.business_id:
            errors["supplier"] = ValidationError(_("Supplier must belong to this business."))
        expense_category = self.expense_category
        if expense_category is not None and expense_category.business_id != self.business_id:
            errors["expense_category"] = ValidationError(
                _("Expense category must belong to this business.")
            )
        target_purchase = self.target_purchase
        if target_purchase is not None and target_purchase.business_id != self.business_id:
            errors["target_purchase"] = ValidationError(
                _("Target purchase must belong to this business.")
            )
        target_expense = self.target_expense
        if target_expense is not None and target_expense.business_id != self.business_id:
            errors["target_expense"] = ValidationError(
                _("Target expense must belong to this business.")
            )
        memberships = (
            ("created_by", self.created_by),
            ("submitted_by", self.submitted_by),
            ("confirmed_by", self.confirmed_by),
            ("cancelled_by", self.cancelled_by),
        )
        for field_name, membership in memberships:
            if membership is not None and membership.business_id != self.business_id:
                errors[field_name] = ValidationError(
                    _("Document actors must belong to this business.")
                )
        replacement_of = self.replacement_of
        if replacement_of is not None and (
            replacement_of.business_id != self.business_id
            or replacement_of.document_id != self.document_id
        ):
            errors["replacement_of"] = ValidationError(
                _("Replacement evidence must belong to the same source document.")
            )
        if errors:
            raise ValidationError(errors)

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = DocumentTranscription.objects.get(pk=self.pk)
            if stored.status != TranscriptionStatus.DRAFT:
                immutable = (
                    self.business_id,
                    self.branch_id,
                    self.document_id,
                    self.attempt_number,
                    self.replacement_of_id,
                    self.supplier_id,
                    self.purchase_date,
                    self.supplier_reference,
                    self.expected_date,
                    self.settlement_terms,
                    self.expense_category_id,
                    self.expense_document_date,
                    self.expense_payee,
                    self.expense_description,
                    self.expense_amount,
                    self.expense_payment_method,
                    self.expense_telebirr_reference,
                    self.created_by_id,
                    self.submitted_by_id,
                    self.submitted_at,
                )
                stored_immutable = (
                    stored.business_id,
                    stored.branch_id,
                    stored.document_id,
                    stored.attempt_number,
                    stored.replacement_of_id,
                    stored.supplier_id,
                    stored.purchase_date,
                    stored.supplier_reference,
                    stored.expected_date,
                    stored.settlement_terms,
                    stored.expense_category_id,
                    stored.expense_document_date,
                    stored.expense_payee,
                    stored.expense_description,
                    stored.expense_amount,
                    stored.expense_payment_method,
                    stored.expense_telebirr_reference,
                    stored.created_by_id,
                    stored.submitted_by_id,
                    stored.submitted_at,
                )
                if immutable != stored_immutable:
                    raise ValidationError(_("Submitted transcription evidence cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class DocumentTranscriptionLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="document_transcription_lines",
    )
    transcription = models.ForeignKey(
        DocumentTranscription,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    position = models.PositiveSmallIntegerField()
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="document_transcription_lines",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6)
    note = models.CharField(max_length=180, blank=True)
    target_purchase_line = models.OneToOneField(
        PurchaseLine,
        on_delete=models.PROTECT,
        related_name="source_document_line",
        null=True,
        blank=True,
    )
    target_stock_operation = models.OneToOneField(
        StockOperation,
        on_delete=models.PROTECT,
        related_name="source_document_line",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=("transcription", "position"),
                name="documents_unique_transcription_line_position",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0")),
                name="documents_line_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__gte=Decimal("0")),
                name="documents_line_unit_cost_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transcription} — {self.position}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.transcription_id and self.transcription.business_id != self.business_id:
            errors["transcription"] = ValidationError(
                _("Transcription line must belong to the same business.")
            )
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Product variant must belong to this business."))
        target_purchase_line = self.target_purchase_line
        if (
            target_purchase_line is not None
            and target_purchase_line.business_id != self.business_id
        ):
            errors["target_purchase_line"] = ValidationError(
                _("Target purchase line must belong to this business.")
            )
        target_stock_operation = self.target_stock_operation
        if (
            target_stock_operation is not None
            and target_stock_operation.business_id != self.business_id
        ):
            errors["target_stock_operation"] = ValidationError(
                _("Target stock operation must belong to this business.")
            )
        if errors:
            raise ValidationError(errors)

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        transcription_status = (
            DocumentTranscription.objects.filter(pk=self.transcription_id)
            .values_list("status", flat=True)
            .first()
        )
        if not self._state.adding:
            stored = DocumentTranscriptionLine.objects.get(pk=self.pk)
            immutable = (
                self.business_id,
                self.transcription_id,
                self.position,
                self.variant_id,
                self.quantity,
                self.unit_cost,
                self.note,
            )
            stored_immutable = (
                stored.business_id,
                stored.transcription_id,
                stored.position,
                stored.variant_id,
                stored.quantity,
                stored.unit_cost,
                stored.note,
            )
            if transcription_status != TranscriptionStatus.DRAFT and (
                immutable != stored_immutable
                or (
                    stored.target_purchase_line_id is not None
                    and self.target_purchase_line_id != stored.target_purchase_line_id
                )
                or (
                    stored.target_stock_operation_id is not None
                    and self.target_stock_operation_id != stored.target_stock_operation_id
                )
            ):
                raise ValidationError(_("Submitted transcription lines cannot be modified."))
        elif transcription_status != TranscriptionStatus.DRAFT:
            raise ValidationError(_("Lines can be added only to a draft transcription."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        if self.transcription.status != TranscriptionStatus.DRAFT:
            raise ValidationError(_("Submitted transcription lines cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)


class DocumentTranscriptionRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="document_transcription_revisions",
    )
    transcription = models.ForeignKey(
        DocumentTranscription,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    sequence = models.PositiveIntegerField()
    action = models.CharField(max_length=16, choices=TranscriptionRevisionAction.choices)
    snapshot = models.JSONField()
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_transcription_revisions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sequence",)
        constraints = [
            models.UniqueConstraint(
                fields=("transcription", "sequence"),
                name="documents_unique_revision_sequence",
            ),
            models.CheckConstraint(
                condition=Q(action__in=TranscriptionRevisionAction.values),
                name="documents_revision_action_is_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transcription} — revision {self.sequence}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.transcription_id and self.transcription.business_id != self.business_id:
            errors["transcription"] = ValidationError(
                _("Revision must belong to the transcription business.")
            )
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Revision actor must belong to this business."))
        if errors:
            raise ValidationError(errors)

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Transcription revisions are immutable."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Transcription revisions cannot be deleted."))


class DocumentAccessEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="document_access_events",
    )
    document = models.ForeignKey(
        CapturedDocument,
        on_delete=models.PROTECT,
        related_name="access_events",
    )
    document_file = models.ForeignKey(
        DocumentFile,
        on_delete=models.PROTECT,
        related_name="access_events",
    )
    action = models.CharField(max_length=16, choices=DocumentAccessAction.choices)
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="document_access_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=DocumentAccessAction.values),
                name="documents_access_action_is_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.document} — {self.get_action_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.document_id and self.document.business_id != self.business_id:
            errors["document"] = ValidationError(
                _("Accessed document must belong to this business.")
            )
        if self.document_file_id and (
            self.document_file.business_id != self.business_id
            or self.document_file.document_id != self.document_id
        ):
            errors["document_file"] = ValidationError(
                _("Accessed file must belong to this captured document.")
            )
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Access actor must belong to this business."))
        if errors:
            raise ValidationError(errors)

    def save(  # noqa: DJ012
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Document access events are immutable."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Document access events cannot be deleted."))
