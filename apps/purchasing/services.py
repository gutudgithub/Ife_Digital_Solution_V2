import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import BusinessMembership
from apps.inventory.services import (
    PurchaseReceiptInventoryItem,
    PurchaseReturnInventoryItem,
    PurchaseReturnReversalInventoryItem,
    ensure_branch_operation_access,
    record_purchase_receipt_inventory,
    record_purchase_return_inventory,
    record_purchase_return_reversal_inventory,
)
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseReturnOperationType,
    PurchaseReturnPostingKey,
    PurchaseReturnReversal,
    PurchaseReturnStatus,
    PurchaseStatus,
    Supplier,
)

PURCHASING_CONSTRAINT_NAMES = frozenset(
    {
        "purchasing_unique_supplier_name_per_business",
        "purchasing_unique_purchase_number_per_business",
        "purchasing_purchase_status_is_valid",
        "purchasing_purchase_approval_fields_match",
        "purchasing_post_approval_status_has_audit",
        "purchasing_unique_variant_per_purchase",
        "purchasing_line_quantity_positive",
        "purchasing_line_unit_cost_nonnegative",
        "purchasing_unique_receipt_number_per_business",
        "purchasing_unique_receipt_idempotency_per_business",
        "purchasing_unique_line_per_receipt",
        "purchasing_receipt_quantity_positive",
        "purchasing_receipt_unit_cost_nonnegative",
        "purchasing_unique_return_posting_key_per_business",
        "purchasing_return_posting_key_type_is_valid",
        "purchasing_unique_return_number_per_business",
        "purchasing_return_status_is_valid",
        "purchasing_return_posting_fields_match",
        "purchasing_return_cancellation_fields_match",
        "purchasing_return_status_has_audit",
        "purchasing_unique_receipt_line_per_return",
        "purchasing_return_line_quantity_positive",
        "purchasing_return_receipt_cost_nonnegative",
        "purchasing_return_supplier_total_nonnegative",
        "purchasing_return_inventory_cost_nonnegative",
        "purchasing_return_inventory_value_nonpositive",
    }
)
QUANTITY_QUANTUM = Decimal("0.001")
VALUE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class PurchaseLineProgress:
    line: PurchaseLine
    received_quantity: Decimal
    returned_quantity: Decimal
    net_received_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True)
class ReceiptQuantity:
    purchase_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class ReturnQuantity:
    receipt_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class ReceiptLineReturnProgress:
    receipt_line: GoodsReceiptLine
    returned_quantity: Decimal
    remaining_quantity: Decimal


@dataclass(frozen=True)
class SupplierActivitySummary:
    supplier: Supplier
    purchase_count: int
    receipt_count: int
    posted_return_count: int
    return_reference_total: Decimal
    latest_purchase_date: date | None
    latest_receipt_at: datetime | None
    latest_return_date: date | None


def _number(prefix: str) -> str:
    return f"{prefix}-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"


def new_purchase_number() -> str:
    return _number("PUR")


def new_purchase_return_number() -> str:
    return _number("PRN")


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)


@contextmanager
def _translate_purchasing_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any(
            name in message for message in error.messages for name in PURCHASING_CONSTRAINT_NAMES
        ):
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
    returned = {
        row["receipt_line__purchase_line_id"]: row["quantity"] or Decimal("0.000")
        for row in PurchaseReturnLine.objects.filter(
            receipt_line__purchase_line__purchase=purchase,
            purchase_return__status=PurchaseReturnStatus.POSTED,
        )
        .values("receipt_line__purchase_line_id")
        .annotate(quantity=Sum("returned_quantity"))
    }
    return [
        PurchaseLineProgress(
            line=line,
            received_quantity=received.get(line.id, Decimal("0.000")),
            returned_quantity=returned.get(line.id, Decimal("0.000")),
            net_received_quantity=received.get(line.id, Decimal("0.000"))
            - returned.get(line.id, Decimal("0.000")),
            remaining_quantity=line.ordered_quantity - received.get(line.id, Decimal("0.000")),
        )
        for line in purchase.lines.select_related("variant", "variant__product").all()
    ]


def purchase_return_source_progress(purchase: Purchase) -> list[ReceiptLineReturnProgress]:
    returned = {
        row["receipt_line_id"]: row["quantity"] or Decimal("0.000")
        for row in PurchaseReturnLine.objects.filter(
            receipt_line__receipt__purchase=purchase,
            purchase_return__status=PurchaseReturnStatus.POSTED,
        )
        .values("receipt_line_id")
        .annotate(quantity=Sum("returned_quantity"))
    }
    return [
        ReceiptLineReturnProgress(
            receipt_line=receipt_line,
            returned_quantity=returned.get(receipt_line.id, Decimal("0.000")),
            remaining_quantity=receipt_line.received_quantity
            - returned.get(receipt_line.id, Decimal("0.000")),
        )
        for receipt_line in GoodsReceiptLine.objects.filter(
            receipt__purchase=purchase,
        )
        .select_related("variant", "purchase_line", "receipt")
        .order_by("receipt__posted_at", "created_at")
    ]


def supplier_activity_summaries(
    *,
    actor: BusinessMembership,
) -> list[SupplierActivitySummary]:
    if not actor.can_receive_inventory:
        raise PermissionDenied(_("Purchasing access is required."))
    business = actor.business
    suppliers = list(business.suppliers.all())
    branch_ids: list[UUID] | None = None
    if not actor.can_manage_purchasing:
        if actor.assigned_branch_id is not None:
            branch_ids = [actor.assigned_branch_id]
        else:
            active_branch_ids = list(
                business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
            )
            branch_ids = active_branch_ids if len(active_branch_ids) == 1 else []
    purchases_query = Purchase.objects.filter(
        business=business,
        status__in=(
            PurchaseStatus.APPROVED,
            PurchaseStatus.PARTIALLY_RECEIVED,
            PurchaseStatus.RECEIVED,
        ),
    )
    receipts_query = GoodsReceipt.objects.filter(business=business)
    returns_query = PurchaseReturn.objects.filter(
        business=business,
        status=PurchaseReturnStatus.POSTED,
    )
    return_lines_query = PurchaseReturnLine.objects.filter(
        business=business,
        purchase_return__status=PurchaseReturnStatus.POSTED,
    )
    if branch_ids is not None:
        purchases_query = purchases_query.filter(branch_id__in=branch_ids)
        receipts_query = receipts_query.filter(branch_id__in=branch_ids)
        returns_query = returns_query.filter(branch_id__in=branch_ids)
        return_lines_query = return_lines_query.filter(purchase_return__branch_id__in=branch_ids)
    purchase_stats = {
        row["supplier_id"]: row
        for row in purchases_query.values("supplier_id").annotate(
            count=Count("id"), latest=Max("purchase_date")
        )
    }
    receipt_stats = {
        row["purchase__supplier_id"]: row
        for row in receipts_query.values("purchase__supplier_id").annotate(
            count=Count("id"), latest=Max("posted_at")
        )
    }
    return_stats = {
        row["supplier_id"]: row
        for row in returns_query.values("supplier_id").annotate(
            count=Count("id"), latest=Max("return_date")
        )
    }
    return_totals = {
        row["purchase_return__supplier_id"]: row["total"] or Decimal("0.000000")
        for row in return_lines_query.values("purchase_return__supplier_id").annotate(
            total=Sum("supplier_reference_total")
        )
    }
    summaries: list[SupplierActivitySummary] = []
    for supplier in suppliers:
        purchases = purchase_stats.get(supplier.id, {})
        receipts = receipt_stats.get(supplier.id, {})
        returns = return_stats.get(supplier.id, {})
        summaries.append(
            SupplierActivitySummary(
                supplier=supplier,
                purchase_count=int(purchases.get("count", 0)),
                receipt_count=int(receipts.get("count", 0)),
                posted_return_count=int(returns.get("count", 0)),
                return_reference_total=return_totals.get(
                    supplier.id,
                    Decimal("0.000000"),
                ),
                latest_purchase_date=purchases.get("latest"),
                latest_receipt_at=receipts.get("latest"),
                latest_return_date=returns.get("latest"),
            )
        )
    return summaries


def _validate_return_preparer(actor: BusinessMembership, purchase: Purchase) -> None:
    if actor.business_id != purchase.business_id:
        raise PermissionDenied(_("Inventory receiving permission is required."))
    ensure_branch_operation_access(actor, purchase.branch, management_required=False)


def _claim_return_posting_key(
    *,
    actor: BusinessMembership,
    key: UUID,
    operation_type: str,
    source_id: UUID,
) -> PurchaseReturnPostingKey:
    existing = PurchaseReturnPostingKey.objects.filter(
        business=actor.business,
        key=key,
    ).first()
    if existing is not None:
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another purchase return operation.")
            )
        return existing
    try:
        with transaction.atomic():
            posting_key = PurchaseReturnPostingKey.objects.create(
                business=actor.business,
                key=key,
                operation_type=operation_type,
                source_id=source_id,
            )
    except (IntegrityError, ValidationError) as error:
        existing = PurchaseReturnPostingKey.objects.filter(
            business=actor.business,
            key=key,
        ).first()
        if existing is None:
            raise
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another purchase return operation.")
            ) from error
        return existing
    return posting_key


def _selected_return_lines(
    *,
    purchase: Purchase,
    quantities: list[ReturnQuantity],
) -> list[tuple[GoodsReceiptLine, Decimal]]:
    if not quantities:
        raise ValidationError(_("Enter at least one return quantity."))
    seen_lines: set[UUID] = set()
    quantity_by_line: dict[UUID, Decimal] = {}
    for item in quantities:
        if item.receipt_line_id in seen_lines:
            raise ValidationError(_("A purchase return cannot repeat the same receipt line."))
        seen_lines.add(item.receipt_line_id)
        if item.quantity <= 0:
            raise ValidationError(_("Return quantity must be greater than zero."))
        quantity_by_line[item.receipt_line_id] = _quantity(item.quantity)
    receipt_lines = list(
        GoodsReceiptLine.objects.filter(
            id__in=quantity_by_line,
            business=purchase.business,
            receipt__purchase=purchase,
            receipt__branch=purchase.branch,
        )
        .select_related("variant", "purchase_line", "receipt")
        .order_by("id")
    )
    if len(receipt_lines) != len(quantity_by_line):
        raise ValidationError(_("Receipt line does not belong to this purchase."))
    return [(line, quantity_by_line[line.id]) for line in receipt_lines]


@transaction.atomic
def save_purchase_return_draft(
    *,
    actor: BusinessMembership,
    purchase: Purchase,
    return_date: date,
    reason: str,
    quantities: list[ReturnQuantity],
    supplier_document_reference: str = "",
    purchase_return: PurchaseReturn | None = None,
) -> PurchaseReturn:
    _validate_return_preparer(actor, purchase)
    locked_purchase = (
        Purchase.objects.select_for_update()
        .select_related("branch", "supplier")
        .get(
            pk=purchase.pk,
            business=actor.business,
        )
    )
    selected = _selected_return_lines(purchase=locked_purchase, quantities=quantities)
    if not reason.strip():
        raise ValidationError(_("A return reason is required."))
    selected_line_ids = [item[0].id for item in selected]
    already_returned = {
        row["receipt_line_id"]: row["quantity"] or Decimal("0.000")
        for row in PurchaseReturnLine.objects.filter(
            receipt_line_id__in=selected_line_ids,
            purchase_return__status=PurchaseReturnStatus.POSTED,
        )
        .values("receipt_line_id")
        .annotate(quantity=Sum("returned_quantity"))
    }
    for receipt_line, quantity in selected:
        remaining = receipt_line.received_quantity - already_returned.get(
            receipt_line.id,
            Decimal("0.000"),
        )
        if quantity > remaining:
            raise ValidationError(
                _("Return quantity cannot exceed the unreturned receipt quantity.")
            )
    if purchase_return is None:
        draft = PurchaseReturn(
            business=actor.business,
            branch=locked_purchase.branch,
            supplier=locked_purchase.supplier,
            purchase=locked_purchase,
            internal_number=new_purchase_return_number(),
            return_date=return_date,
            reason=reason.strip(),
            supplier_document_reference=supplier_document_reference.strip(),
            created_by=actor,
        )
    else:
        draft = PurchaseReturn.objects.select_for_update().get(
            pk=purchase_return.pk,
            business=actor.business,
            purchase=locked_purchase,
        )
        if draft.status != PurchaseReturnStatus.DRAFT:
            raise ValidationError(_("Only a draft purchase return can be edited."))
        draft.return_date = return_date
        draft.reason = reason.strip()
        draft.supplier_document_reference = supplier_document_reference.strip()
    with _translate_purchasing_constraint_errors():
        draft.save()
    for existing_line in draft.lines.all():
        existing_line.delete()
    for receipt_line, quantity in selected:
        with _translate_purchasing_constraint_errors():
            PurchaseReturnLine.objects.create(
                business=actor.business,
                purchase_return=draft,
                receipt_line=receipt_line,
                variant=receipt_line.variant,
                returned_quantity=quantity,
                product_name_snapshot=receipt_line.purchase_line.product_name_snapshot,
                sku_snapshot=receipt_line.purchase_line.sku_snapshot,
                unit_snapshot=receipt_line.unit_snapshot,
                supplier_name_snapshot=locked_purchase.supplier.name,
                receipt_unit_cost=receipt_line.unit_cost,
                supplier_reference_total=_value(quantity * receipt_line.unit_cost),
            )
    return draft


@transaction.atomic
def cancel_purchase_return(
    *,
    actor: BusinessMembership,
    purchase_return: PurchaseReturn,
    cancelled_at: datetime | None = None,
) -> PurchaseReturn:
    _validate_manager(actor, purchase_return.purchase)
    locked = PurchaseReturn.objects.select_for_update().get(
        pk=purchase_return.pk,
        business=actor.business,
    )
    if locked.status != PurchaseReturnStatus.DRAFT:
        raise ValidationError(_("Only a draft purchase return can be cancelled."))
    locked.status = PurchaseReturnStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = cancelled_at or timezone.now()
    with _translate_purchasing_constraint_errors():
        locked.save(
            update_fields=("status", "cancelled_by", "cancelled_at", "updated_at"),
        )
    return locked


@transaction.atomic
def post_purchase_return(
    *,
    actor: BusinessMembership,
    purchase_return: PurchaseReturn,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> PurchaseReturn:
    _validate_manager(actor, purchase_return.purchase)
    locked = (
        PurchaseReturn.objects.select_for_update()
        .select_related("branch", "purchase", "supplier")
        .get(pk=purchase_return.pk, business=actor.business)
    )
    existing_key = PurchaseReturnPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != PurchaseReturnOperationType.RETURN
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another purchase return operation.")
            )
        if locked.posting_key_id == existing_key.id and locked.status in {
            PurchaseReturnStatus.POSTED,
            PurchaseReturnStatus.REVERSED,
        }:
            return locked
        raise ValidationError(_("The purchase return posting is incomplete."))
    if locked.status != PurchaseReturnStatus.DRAFT:
        raise ValidationError(_("Only a draft purchase return can be posted."))

    lines = list(
        locked.lines.select_related(
            "receipt_line",
            "receipt_line__purchase_line",
            "variant",
        ).order_by("receipt_line_id")
    )
    if not lines:
        raise ValidationError(_("Add at least one purchase return line before posting."))
    receipt_line_ids = [line.receipt_line_id for line in lines]
    locked_receipts = {
        receipt_line.id: receipt_line
        for receipt_line in GoodsReceiptLine.objects.select_for_update()
        .filter(
            id__in=receipt_line_ids,
            business=actor.business,
            receipt__purchase=locked.purchase,
            receipt__branch=locked.branch,
        )
        .order_by("id")
    }
    if len(locked_receipts) != len(lines):
        raise ValidationError(_("One or more receipt lines no longer belong to this purchase."))
    already_returned = {
        row["receipt_line_id"]: row["quantity"] or Decimal("0.000")
        for row in PurchaseReturnLine.objects.filter(
            receipt_line_id__in=receipt_line_ids,
            purchase_return__status=PurchaseReturnStatus.POSTED,
        )
        .exclude(purchase_return=locked)
        .values("receipt_line_id")
        .annotate(quantity=Sum("returned_quantity"))
    }
    for line in lines:
        remaining = locked_receipts[line.receipt_line_id].received_quantity - already_returned.get(
            line.receipt_line_id,
            Decimal("0.000"),
        )
        if line.returned_quantity > remaining:
            raise ValidationError(
                _("Return quantity cannot exceed the unreturned receipt quantity.")
            )

    posting_key = _claim_return_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=PurchaseReturnOperationType.RETURN,
        source_id=locked.id,
    )
    timestamp = posted_at or timezone.now()
    inventory_items = [
        PurchaseReturnInventoryItem(
            variant=line.variant,
            quantity=line.returned_quantity,
            unit_snapshot=line.unit_snapshot,
            source_id=line.id,
        )
        for line in lines
    ]
    movements = record_purchase_return_inventory(
        actor=actor,
        business=actor.business,
        branch=locked.branch,
        items=inventory_items,
        reason=locked.reason,
        posted_at=timestamp,
    )
    movement_by_source = {movement.source_id: movement for movement in movements}
    for line in lines:
        movement = movement_by_source[line.id]
        line.product_name_snapshot = line.receipt_line.purchase_line.product_name_snapshot
        line.sku_snapshot = line.receipt_line.purchase_line.sku_snapshot
        line.unit_snapshot = line.receipt_line.unit_snapshot
        line.supplier_name_snapshot = locked.supplier.name
        line.receipt_unit_cost = line.receipt_line.unit_cost
        line.supplier_reference_total = _value(line.returned_quantity * line.receipt_line.unit_cost)
        line.assigned_inventory_unit_cost = movement.unit_cost
        line.inventory_value_delta = movement.value_delta
        with _translate_purchasing_constraint_errors():
            line.save(
                update_fields=(
                    "product_name_snapshot",
                    "sku_snapshot",
                    "unit_snapshot",
                    "supplier_name_snapshot",
                    "receipt_unit_cost",
                    "supplier_reference_total",
                    "assigned_inventory_unit_cost",
                    "inventory_value_delta",
                )
            )
    locked.status = PurchaseReturnStatus.POSTED
    locked.posting_key = posting_key
    locked.posted_by = actor
    locked.posted_at = timestamp
    with _translate_purchasing_constraint_errors():
        locked.save(
            update_fields=("status", "posting_key", "posted_by", "posted_at", "updated_at"),
        )
    return locked


@transaction.atomic
def reverse_purchase_return(
    *,
    actor: BusinessMembership,
    purchase_return: PurchaseReturn,
    reason: str,
    idempotency_key: UUID,
    reversed_at: datetime | None = None,
) -> PurchaseReturnReversal:
    _validate_manager(actor, purchase_return.purchase)
    Purchase.objects.select_for_update().get(
        pk=purchase_return.purchase_id,
        business=actor.business,
    )
    locked = (
        PurchaseReturn.objects.select_for_update()
        .select_related("branch", "purchase")
        .get(pk=purchase_return.pk, business=actor.business)
    )
    existing_key = PurchaseReturnPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != PurchaseReturnOperationType.REVERSAL
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another purchase return operation.")
            )
        existing_reversal = PurchaseReturnReversal.objects.filter(
            purchase_return=locked,
            posting_key=existing_key,
        ).first()
        if existing_reversal is not None:
            return existing_reversal
        raise ValidationError(_("The purchase return reversal is incomplete."))
    if locked.status != PurchaseReturnStatus.POSTED:
        raise ValidationError(_("Only a posted purchase return can be reversed."))
    if locked.supplier_settlements.filter(reversal__isnull=True).exists():
        raise ValidationError(
            _("Reverse active supplier-return settlements before reversing this purchase return.")
        )
    if not reason.strip():
        raise ValidationError(_("A reversal reason is required."))

    lines = list(locked.lines.select_related("variant").order_by("id"))
    posting_key = _claim_return_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=PurchaseReturnOperationType.REVERSAL,
        source_id=locked.id,
    )
    timestamp = reversed_at or timezone.now()
    record_purchase_return_reversal_inventory(
        actor=actor,
        business=actor.business,
        branch=locked.branch,
        items=[
            PurchaseReturnReversalInventoryItem(
                variant=line.variant,
                quantity=line.returned_quantity,
                unit_cost=line.assigned_inventory_unit_cost,
                unit_snapshot=line.unit_snapshot,
                source_id=line.id,
            )
            for line in lines
        ],
        reason=reason,
        posted_at=timestamp,
    )
    with _translate_purchasing_constraint_errors():
        reversal = PurchaseReturnReversal.objects.create(
            business=actor.business,
            branch=locked.branch,
            purchase_return=locked,
            posting_key=posting_key,
            reason=reason.strip(),
            reversed_by=actor,
            posted_at=timestamp,
        )
    locked.status = PurchaseReturnStatus.REVERSED
    with _translate_purchasing_constraint_errors():
        locked.save(update_fields=("status", "updated_at"))
    return reversal


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
    if locked.supplier_payments.filter(reversal__isnull=True).exists():
        raise ValidationError(
            _("Reverse active supplier payments before cancelling this purchase.")
        )
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
