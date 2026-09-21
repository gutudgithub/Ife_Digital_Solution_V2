import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import Branch, BusinessMembership, MembershipRole
from apps.catalog.models import ProductVariant
from apps.offline.models import (
    OfflineSaleSync,
    OfflineSaleSyncKey,
    OfflineSaleSyncStatus,
)
from apps.sales.models import SalePaymentMethod
from apps.sales.services import SaleQuantity, save_sale_draft

MAX_OFFLINE_DRAFT_AGE = timedelta(days=7)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_OFFLINE_SALE_LINES = 100


@dataclass(frozen=True)
class OfflineSaleLineInput:
    variant_id: UUID
    quantity: str
    selling_price_snapshot: str
    product_name_snapshot: str
    variant_label_snapshot: str


@dataclass(frozen=True)
class OfflineSaleDraftInput:
    local_draft_id: UUID
    idempotency_key: UUID
    business_id: UUID
    branch_id: UUID
    drafted_by_id: UUID
    role_at_draft: str
    offline_created_at: datetime
    payment_method: str
    telebirr_reference: str
    lines: tuple[OfflineSaleLineInput, ...]


def _current_actor(actor: BusinessMembership) -> BusinessMembership:
    current = (
        BusinessMembership.objects.select_related("business", "assigned_branch")
        .filter(
            pk=actor.pk,
            business_id=actor.business_id,
            is_active=True,
            business__is_active=True,
        )
        .first()
    )
    if current is None or not current.can_sell:
        raise ValidationError(_("An active sales membership is required."))
    return current


def _authorized_branch(*, actor: BusinessMembership, branch_id: UUID) -> Branch:
    branch = Branch.objects.filter(
        pk=branch_id,
        business=actor.business,
        is_active=True,
    ).first()
    if branch is None:
        raise ValidationError(_("The offline branch is unavailable."))
    if not actor.can_sell_across_branches and actor.assigned_branch_id != branch.id:
        raise ValidationError(_("You cannot synchronize sales for this branch."))
    return branch


def _offline_timestamp(value: datetime, *, enforce_age: bool = True) -> datetime:
    if timezone.is_naive(value):
        raise ValidationError(_("The offline creation time must include a timezone."))
    normalized = value.astimezone(UTC)
    now = timezone.now()
    if normalized > now + MAX_CLOCK_SKEW:
        raise ValidationError(_("The offline creation time cannot be in the future."))
    if enforce_age and normalized < now - MAX_OFFLINE_DRAFT_AGE:
        raise ValidationError(_("Offline sale drafts expire after seven days."))
    return normalized


def _draft_expired(value: datetime) -> bool:
    return value < timezone.now() - MAX_OFFLINE_DRAFT_AGE


def _payment(method: str, reference: str) -> tuple[str, str]:
    clean_reference = reference.strip()
    if method == SalePaymentMethod.CASH:
        if clean_reference:
            raise ValidationError(_("Cash payments cannot include a Telebirr reference."))
        return method, ""
    if method != SalePaymentMethod.TELEBIRR:
        raise ValidationError(_("Select cash or Telebirr as the payment method."))
    if not clean_reference:
        raise ValidationError(_("Enter the Telebirr transaction reference."))
    if len(clean_reference) > 120:
        raise ValidationError(_("The Telebirr transaction reference is too long."))
    return method, clean_reference


def _quantity(value: str) -> Decimal:
    try:
        quantity = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValidationError(_("Enter a valid sale quantity.")) from error
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError(_("Sale quantity must be greater than zero."))
    exponent = quantity.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -3:
        raise ValidationError(_("Sale quantities may have at most three decimal places."))
    if quantity.adjusted() > 14:
        raise ValidationError(_("The sale quantity is too large."))
    return quantity


def _price(value: str) -> Decimal:
    try:
        price = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise ValidationError(_("The offline price snapshot is invalid.")) from error
    if not price.is_finite() or price < 0:
        raise ValidationError(_("The offline price snapshot is invalid."))
    try:
        quantized = price.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise ValidationError(_("The offline price snapshot is invalid.")) from error
    if quantized != price or len(quantized.as_tuple().digits) > 18:
        raise ValidationError(_("The offline price snapshot is invalid."))
    return quantized


def _clean_snapshot(value: str, *, maximum: int) -> str:
    clean_value = value.strip()
    if not clean_value or len(clean_value) > maximum:
        raise ValidationError(_("An offline catalog label is invalid."))
    return clean_value


def _normalized_payload(
    draft: OfflineSaleDraftInput,
) -> tuple[dict[str, object], list[SaleQuantity], dict[UUID, dict[str, object]]]:
    if not draft.lines:
        raise ValidationError(_("Enter at least one sale quantity."))
    if len(draft.lines) > MAX_OFFLINE_SALE_LINES:
        raise ValidationError(_("The offline sale contains too many lines."))
    seen: set[UUID] = set()
    quantities: list[SaleQuantity] = []
    line_snapshots: list[dict[str, object]] = []
    snapshot_by_variant: dict[UUID, dict[str, object]] = {}
    for line in draft.lines:
        if line.variant_id in seen:
            raise ValidationError(_("A sale cannot repeat the same product variant."))
        seen.add(line.variant_id)
        quantity = _quantity(line.quantity)
        snapshot: dict[str, object] = {
            "variant_id": str(line.variant_id),
            "quantity": format(quantity, "f"),
            "selling_price": format(_price(line.selling_price_snapshot), ".2f"),
            "product_name": _clean_snapshot(line.product_name_snapshot, maximum=160),
            "variant_label": _clean_snapshot(line.variant_label_snapshot, maximum=240),
        }
        quantities.append(SaleQuantity(variant_id=line.variant_id, quantity=quantity))
        line_snapshots.append(snapshot)
        snapshot_by_variant[line.variant_id] = snapshot
    payment_method, reference = _payment(
        draft.payment_method,
        draft.telebirr_reference,
    )
    if draft.role_at_draft not in MembershipRole.values:
        raise ValidationError(_("The offline membership role is invalid."))
    payload: dict[str, object] = {
        "local_draft_id": str(draft.local_draft_id),
        "idempotency_key": str(draft.idempotency_key),
        "business_id": str(draft.business_id),
        "branch_id": str(draft.branch_id),
        "drafted_by_id": str(draft.drafted_by_id),
        "role_at_draft": draft.role_at_draft,
        "offline_created_at": _offline_timestamp(
            draft.offline_created_at,
            enforce_age=False,
        ).isoformat(),
        "payment_method": payment_method,
        "telebirr_reference": reference,
        "lines": line_snapshots,
    }
    return payload, quantities, snapshot_by_variant


def _payload_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _claim_sync_key(
    *,
    actor: BusinessMembership,
    draft: OfflineSaleDraftInput,
    payload_hash: str,
) -> OfflineSaleSyncKey:
    existing = (
        OfflineSaleSyncKey.objects.filter(
            business=actor.business,
        )
        .filter(Q(key=draft.idempotency_key) | Q(local_draft_id=draft.local_draft_id))
        .first()
    )
    if existing is not None:
        if (
            existing.key != draft.idempotency_key
            or existing.local_draft_id != draft.local_draft_id
            or existing.payload_hash != payload_hash
        ):
            raise ValidationError(
                _("This offline draft identity belongs to different sale content.")
            )
        return existing
    try:
        with transaction.atomic():
            return OfflineSaleSyncKey.objects.create(
                business=actor.business,
                key=draft.idempotency_key,
                local_draft_id=draft.local_draft_id,
                payload_hash=payload_hash,
            )
    except (IntegrityError, ValidationError) as error:
        existing = (
            OfflineSaleSyncKey.objects.filter(
                business=actor.business,
            )
            .filter(Q(key=draft.idempotency_key) | Q(local_draft_id=draft.local_draft_id))
            .first()
        )
        if existing is None:
            raise
        if (
            existing.key != draft.idempotency_key
            or existing.local_draft_id != draft.local_draft_id
            or existing.payload_hash != payload_hash
        ):
            raise ValidationError(
                _("This offline draft identity belongs to different sale content.")
            ) from error
        return existing


def _catalog_conflicts(
    *,
    actor: BusinessMembership,
    snapshot_by_variant: dict[UUID, dict[str, object]],
) -> list[str]:
    variants = list(
        ProductVariant.objects.filter(
            business=actor.business,
            id__in=snapshot_by_variant,
            is_active=True,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("id")
    )
    if len(variants) != len(snapshot_by_variant):
        raise ValidationError(_("One or more product variants are unavailable."))
    conflicts: set[str] = set()
    for variant in variants:
        snapshot = snapshot_by_variant[variant.id]
        if format(variant.selling_price, ".2f") != snapshot["selling_price"]:
            conflicts.add("price_changed")
        if (
            variant.product.name != snapshot["product_name"]
            or str(variant) != snapshot["variant_label"]
        ):
            conflicts.add("catalog_label_changed")
    return sorted(conflicts)


def _drafting_actor(
    *,
    current_actor: BusinessMembership,
    draft: OfflineSaleDraftInput,
    branch: Branch,
) -> BusinessMembership:
    drafted_by = (
        BusinessMembership.objects.select_related("business", "assigned_branch")
        .filter(
            pk=draft.drafted_by_id,
            business=current_actor.business,
            is_active=True,
            business__is_active=True,
        )
        .first()
    )
    if drafted_by is None or not drafted_by.can_sell:
        raise ValidationError(_("The drafting sales membership is no longer active."))
    if drafted_by.role != draft.role_at_draft:
        raise ValidationError(_("The drafting membership role changed before synchronization."))
    _authorized_branch(actor=drafted_by, branch_id=branch.id)
    if drafted_by.id != current_actor.id and not current_actor.can_sell_across_branches:
        raise ValidationError(
            _("Only an owner or manager can synchronize another operator's offline draft.")
        )
    return drafted_by


@transaction.atomic
def sync_offline_sale(
    *,
    actor: BusinessMembership,
    draft: OfflineSaleDraftInput,
) -> OfflineSaleSync:
    current_actor = _current_actor(actor)
    if draft.business_id != current_actor.business_id:
        raise ValidationError(_("The offline business does not match the active business."))
    branch = _authorized_branch(actor=current_actor, branch_id=draft.branch_id)
    payload, quantities, snapshot_by_variant = _normalized_payload(draft)
    payload_hash = _payload_hash(payload)
    claimed = _claim_sync_key(
        actor=current_actor,
        draft=draft,
        payload_hash=payload_hash,
    )
    sync_key = OfflineSaleSyncKey.objects.select_for_update().get(pk=claimed.pk)
    existing = OfflineSaleSync.objects.select_related("sale").filter(sync_key=sync_key).first()
    if existing is not None:
        return existing
    drafted_by = _drafting_actor(
        current_actor=current_actor,
        draft=draft,
        branch=branch,
    )
    offline_created_at = _offline_timestamp(
        draft.offline_created_at,
        enforce_age=False,
    )
    if _draft_expired(offline_created_at):
        return OfflineSaleSync.objects.create(
            business=current_actor.business,
            branch=branch,
            actor=current_actor,
            drafted_by=drafted_by,
            sync_key=sync_key,
            status=OfflineSaleSyncStatus.REJECTED,
            offline_created_at=offline_created_at,
            synced_at=timezone.now(),
            snapshot=payload,
            conflict_messages=[_("Offline sale drafts expire after seven days.")],
        )

    try:
        conflicts = _catalog_conflicts(
            actor=drafted_by,
            snapshot_by_variant=snapshot_by_variant,
        )
        sale = save_sale_draft(
            actor=drafted_by,
            branch=branch,
            sale_date=timezone.localtime(offline_created_at).date(),
            quantities=quantities,
        )
    except ValidationError as error:
        return OfflineSaleSync.objects.create(
            business=current_actor.business,
            branch=branch,
            actor=current_actor,
            drafted_by=drafted_by,
            sync_key=sync_key,
            status=OfflineSaleSyncStatus.REJECTED,
            offline_created_at=offline_created_at,
            synced_at=timezone.now(),
            snapshot=payload,
            conflict_messages=list(error.messages),
        )

    status = OfflineSaleSyncStatus.NEEDS_REVIEW if conflicts else OfflineSaleSyncStatus.SYNCED
    return OfflineSaleSync.objects.create(
        business=current_actor.business,
        branch=branch,
        actor=current_actor,
        drafted_by=drafted_by,
        sync_key=sync_key,
        sale=sale,
        status=status,
        offline_created_at=offline_created_at,
        synced_at=timezone.now(),
        snapshot=payload,
        conflict_messages=conflicts,
    )
