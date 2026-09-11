import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import BusinessMembership
from apps.inventory.services import (
    PurchaseReceiptInventoryItem,
    ensure_branch_operation_access,
    record_purchase_receipt_inventory,
)
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseStatus,
)


@dataclass(frozen=True)
class PurchaseLineProgress:
    line: PurchaseLine
    received_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True)
class ReceiptQuantity:
    purchase_line_id: UUID
    quantity: Decimal


def _number(prefix: str) -> str:
    return f"{prefix}-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"


def new_purchase_number() -> str:
    return _number("PUR")


@contextmanager
def _translate_purchasing_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any("purchasing_" in message for message in error.messages):
            raise ValidationError(
                _("Posting could not be completed because a purchasing rule was violated.")
            ) from error
        raise


def _is_receipt_idempotency_validation(error: ValidationError) -> bool:
    try:
        non_field_errors = error.error_dict.get(NON_FIELD_ERRORS, ())
    except AttributeError:
        return False
    for item in non_field_errors:
        if item.code != "unique_together" or item.params is None:
            continue
        if item.params.get("unique_check") == ("business", "idempotency_key"):
            return True
    return False


def _validate_manager(actor: BusinessMembership, purchase: Purchase) -> None:
    if (
        not actor.is_active
        or not actor.business.is_active
        or actor.business_id != purchase.business_id
        or not actor.can_manage_purchasing
    ):
        raise PermissionDenied(_("Purchasing management permission is required."))


def purchase_line_progress(purchase: Purchase) -> list[PurchaseLineProgress]:
    received = {
        row["purchase_line_id"]: row["quantity"] or Decimal("0.000")
        for row in GoodsReceiptLine.objects.filter(
            purchase_line__purchase=purchase,
        )
        .values("purchase_line_id")
        .annotate(quantity=Sum("received_quantity"))
    }
    return [
        PurchaseLineProgress(
            line=line,
            received_quantity=received.get(line.id, Decimal("0.000")),
            remaining_quantity=line.ordered_quantity - received.get(line.id, Decimal("0.000")),
        )
        for line in purchase.lines.select_related("variant", "variant__product").all()
    ]


@transaction.atomic
def approve_purchase(
    *,
    actor: BusinessMembership,
    purchase: Purchase,
    approved_at: datetime | None = None,
) -> Purchase:
    _validate_manager(actor, purchase)
    locked = Purchase.objects.select_for_update().get(
        pk=purchase.pk,
        business=actor.business,
    )
    if locked.status != PurchaseStatus.DRAFT:
        raise ValidationError(_("Only a draft purchase can be approved."))
    lines = list(locked.lines.select_related("variant", "variant__product"))
    if not lines:
        raise ValidationError(_("Add at least one purchase line before approval."))
    for line in lines:
        if not line.variant.is_active:
            raise ValidationError(_("Every purchase variant must be active before approval."))
        line.product_name_snapshot = line.variant.product.name
        line.sku_snapshot = line.variant.sku
        line.unit_snapshot = line.variant.stock_unit
        with _translate_purchasing_constraint_errors():
            line.save(update_fields=("product_name_snapshot", "sku_snapshot", "unit_snapshot"))
    locked.status = PurchaseStatus.APPROVED
    locked.approved_by = actor
    locked.approved_at = approved_at or timezone.now()
    with _translate_purchasing_constraint_errors():
        locked.save(update_fields=("status", "approved_by", "approved_at", "updated_at"))
    return locked


@transaction.atomic
def cancel_purchase(
    *,
    actor: BusinessMembership,
    purchase: Purchase,
) -> Purchase:
    _validate_manager(actor, purchase)
    locked = Purchase.objects.select_for_update().get(
        pk=purchase.pk,
        business=actor.business,
    )
    if locked.status not in {PurchaseStatus.DRAFT, PurchaseStatus.APPROVED}:
        raise ValidationError(_("This purchase can no longer be cancelled."))
    if locked.receipts.exists():
        raise ValidationError(_("A received purchase cannot be cancelled."))
    locked.status = PurchaseStatus.CANCELLED
    with _translate_purchasing_constraint_errors():
        locked.save(update_fields=("status", "updated_at"))
    return locked


@transaction.atomic
def receive_purchase(
    *,
    actor: BusinessMembership,
    purchase: Purchase,
    quantities: list[ReceiptQuantity],
    idempotency_key: UUID,
    supplier_document_reference: str = "",
    received_at: datetime | None = None,
) -> GoodsReceipt:
    if actor.business_id != purchase.business_id:
        raise PermissionDenied(_("Inventory receiving permission is required."))
    locked = (
        Purchase.objects.select_for_update()
        .select_related("branch")
        .get(
            pk=purchase.pk,
            business=actor.business,
        )
    )
    ensure_branch_operation_access(actor, locked.branch, management_required=False)
    existing = GoodsReceipt.objects.filter(
        business=actor.business,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if existing.purchase_id != locked.id:
            raise ValidationError(_("This idempotency key belongs to another receipt."))
        return existing
    if locked.status not in {
        PurchaseStatus.APPROVED,
        PurchaseStatus.PARTIALLY_RECEIVED,
    }:
        raise ValidationError(_("Only an approved purchase can be received."))
    if not quantities:
        raise ValidationError(_("Enter at least one received quantity."))

    progress = {item.line.id: item for item in purchase_line_progress(locked)}
    seen_lines: set[UUID] = set()
    selected: list[tuple[PurchaseLineProgress, Decimal]] = []
    for item in quantities:
        if item.purchase_line_id in seen_lines:
            raise ValidationError(_("A receipt cannot repeat the same purchase line."))
        seen_lines.add(item.purchase_line_id)
        line_progress = progress.get(item.purchase_line_id)
        if line_progress is None:
            raise ValidationError(_("Purchase line does not belong to this purchase."))
        if item.quantity <= 0:
            raise ValidationError(_("Received quantity must be greater than zero."))
        if item.quantity > line_progress.remaining_quantity:
            raise ValidationError(_("Received quantity cannot exceed the remaining quantity."))
        selected.append((line_progress, item.quantity))

    timestamp = received_at or timezone.now()
    try:
        with transaction.atomic(), _translate_purchasing_constraint_errors():
            receipt = GoodsReceipt.objects.create(
                business=actor.business,
                branch=locked.branch,
                purchase=locked,
                internal_number=_number("GRN"),
                supplier_document_reference=supplier_document_reference.strip(),
                idempotency_key=idempotency_key,
                received_by=actor,
                posted_at=timestamp,
            )
    except ValidationError as error:
        if not _is_receipt_idempotency_validation(error):
            raise
        existing = GoodsReceipt.objects.filter(
            business=actor.business,
            idempotency_key=idempotency_key,
        ).first()
        if existing is None:
            raise
        if existing.purchase_id != locked.id:
            raise ValidationError(_("This idempotency key belongs to another receipt.")) from error
        return existing
    except IntegrityError as error:
        existing = GoodsReceipt.objects.filter(
            business=actor.business,
            idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if existing.purchase_id != locked.id:
                raise ValidationError(
                    _("This idempotency key belongs to another receipt.")
                ) from error
            return existing
        raise
    inventory_items: list[PurchaseReceiptInventoryItem] = []
    newly_received: dict[UUID, Decimal] = {}
    for line_progress, quantity in selected:
        line = line_progress.line
        with _translate_purchasing_constraint_errors():
            receipt_line = GoodsReceiptLine.objects.create(
                business=actor.business,
                receipt=receipt,
                purchase_line=line,
                variant=line.variant,
                received_quantity=quantity,
                unit_snapshot=line.unit_snapshot,
                unit_cost=line.unit_cost,
            )
        inventory_items.append(
            PurchaseReceiptInventoryItem(
                variant=line.variant,
                quantity=quantity,
                unit_cost=line.unit_cost,
                unit_snapshot=line.unit_snapshot,
                source_id=receipt_line.id,
            )
        )
        newly_received[line.id] = quantity
    record_purchase_receipt_inventory(
        actor=actor,
        business=actor.business,
        branch=locked.branch,
        items=inventory_items,
        posted_at=timestamp,
    )

    all_received = all(
        line_progress.received_quantity
        + newly_received.get(line_progress.line.id, Decimal("0.000"))
        == line_progress.line.ordered_quantity
        for line_progress in progress.values()
    )
    locked.status = PurchaseStatus.RECEIVED if all_received else PurchaseStatus.PARTIALLY_RECEIVED
    with _translate_purchasing_constraint_errors():
        locked.save(update_fields=("status", "updated_at"))
    return receipt
