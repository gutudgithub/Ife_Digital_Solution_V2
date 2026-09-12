from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import WHOLE_STOCK_UNITS, ProductVariant, validate_stock_quantity
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
    StockCountApproval,
    StockCountLine,
    StockCountLineRevision,
    StockCountOperationType,
    StockCountPostingKey,
    StockCountReversal,
    StockCountReviewReturn,
    StockCountSession,
    StockCountStatus,
    StockOperation,
    StockOperationType,
)

QUANTITY_QUANTUM = Decimal("0.001")
COST_QUANTUM = Decimal("0.000001")
VALUE_QUANTUM = Decimal("0.000001")
INVENTORY_CONSTRAINT_NAMES = frozenset(
    {
        "inventory_unique_operation_idempotency_per_business",
        "inventory_operation_type_is_valid",
        "inventory_unique_movement_source_variant",
        "inventory_movement_quantity_nonzero",
        "inventory_movement_unit_cost_nonnegative",
        "inventory_movement_value_direction_matches",
        "inventory_movement_type_is_valid",
        "inventory_source_type_is_valid",
        "inventory_unique_balance_per_branch_variant",
        "inventory_balance_quantity_nonnegative",
        "inventory_balance_cost_nonnegative",
        "inventory_balance_value_nonnegative",
        "inventory_zero_balance_has_zero_cost_and_value",
        "inventory_unique_stock_count_key_per_business",
        "inventory_stock_count_key_operation_valid",
        "inventory_unique_open_stock_count_per_branch",
        "inventory_stock_count_status_valid",
        "inventory_stock_count_submission_fields_match",
        "inventory_stock_count_cancellation_fields_match",
        "inventory_stock_count_status_has_audit",
        "inventory_unique_stock_count_review_return_sequence",
        "inventory_unique_stock_count_revision_sequence",
        "inventory_stock_count_previous_quantity_nonnegative",
        "inventory_stock_count_replacement_quantity_nonnegative",
        "inventory_unique_stock_count_line_variant",
        "inventory_stock_count_system_quantity_nonnegative",
        "inventory_stock_count_average_cost_nonnegative",
        "inventory_stock_count_value_nonnegative",
        "inventory_stock_count_physical_quantity_nonnegative",
        "inventory_stock_count_assigned_cost_nonnegative",
        "inventory_stock_count_counted_fields_match",
    }
)


@dataclass(frozen=True)
class PurchaseReceiptInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class PurchaseReturnInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class PurchaseReturnReversalInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class SaleInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class SaleReturnInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class SaleReturnReversalInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    unit_snapshot: str
    source_id: UUID


@dataclass(frozen=True)
class StockCountUnitVarianceSummary:
    stock_unit: str
    positive_quantity: Decimal
    negative_quantity: Decimal
    zero_variance_line_count: int


@dataclass(frozen=True)
class StockCountReviewSummary:
    line_count: int
    counted_line_count: int
    remaining_line_count: int
    positive_variance_line_count: int
    negative_variance_line_count: int
    zero_variance_line_count: int
    missing_exceptional_cost_line_count: int
    total_inventory_value_adjustment: Decimal
    unit_summaries: tuple[StockCountUnitVarianceSummary, ...]


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _cost(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)


@contextmanager
def _translate_inventory_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any(
            name in message for message in error.messages for name in INVENTORY_CONSTRAINT_NAMES
        ):
            raise ValidationError(
                _(
                    "Inventory posting could not be completed because an inventory rule "
                    "was violated."
                )
            ) from error
        raise


def _validate_actor(actor: BusinessMembership, business: Business) -> None:
    if not actor.is_active or not business.is_active or actor.business_id != business.id:
        raise PermissionDenied(_("An active business membership is required."))


def ensure_branch_operation_access(
    actor: BusinessMembership,
    branch: Branch,
    *,
    management_required: bool,
) -> None:
    _validate_actor(actor, branch.business)
    if management_required:
        if not actor.can_manage_inventory:
            raise PermissionDenied(_("Inventory management permission is required."))
        return
    if not actor.can_receive_inventory:
        raise PermissionDenied(_("Inventory receiving permission is required."))
    if actor.can_manage_inventory:
        return
    assigned_branch = actor.assigned_branch
    if assigned_branch is not None:
        if assigned_branch.id != branch.id:
            raise PermissionDenied(_("You may receive stock only for your assigned branch."))
        return
    active_branches = list(actor.business.branches.filter(is_active=True)[:2])
    if len(active_branches) != 1 or active_branches[0].id != branch.id:
        raise PermissionDenied(_("Ask a manager to assign your branch before receiving stock."))


def ensure_sale_branch_access(actor: BusinessMembership, branch: Branch) -> None:
    _validate_actor(actor, branch.business)
    if not branch.is_active:
        raise PermissionDenied(_("Sales require an active branch."))
    if not actor.can_sell:
        raise PermissionDenied(_("Sales permission is required."))
    if actor.can_sell_across_branches:
        return
    assigned_branch = actor.assigned_branch
    if assigned_branch is not None:
        if assigned_branch.id != branch.id:
            raise PermissionDenied(_("You may record sales only for your assigned branch."))
        return
    active_branches = list(actor.business.branches.filter(is_active=True)[:2])
    if len(active_branches) != 1 or active_branches[0].id != branch.id:
        raise PermissionDenied(_("Ask a manager to assign your branch before recording sales."))


def ensure_stock_count_branch_access(
    actor: BusinessMembership,
    branch: Branch,
    *,
    management_required: bool,
) -> None:
    _validate_actor(actor, branch.business)
    if not branch.is_active:
        raise PermissionDenied(_("Stock counting requires an active branch."))
    if management_required:
        if not actor.can_approve_stock_counts:
            raise PermissionDenied(_("Stock-count management permission is required."))
        return
    if not actor.can_count_inventory:
        raise PermissionDenied(_("Stock-count entry permission is required."))
    if actor.can_approve_stock_counts:
        return
    assigned_branch = actor.assigned_branch
    if assigned_branch is not None:
        if assigned_branch.id != branch.id:
            raise PermissionDenied(_("You may count stock only for your assigned branch."))
        return
    active_branches = list(actor.business.branches.filter(is_active=True)[:2])
    if len(active_branches) != 1 or active_branches[0].id != branch.id:
        raise PermissionDenied(_("Ask a manager to assign your branch before counting stock."))


def _lock_branch(branch: Branch, business: Business) -> Branch:
    try:
        return (
            Branch.objects.select_for_update()
            .select_related("business")
            .get(pk=branch.pk, business=business)
        )
    except Branch.DoesNotExist as error:
        raise ValidationError(_("Branch must belong to this business.")) from error


def _active_stock_count(branch: Branch, business: Business) -> StockCountSession | None:
    return (
        StockCountSession.objects.filter(
            business=business,
            branch=branch,
            status__in=(StockCountStatus.COUNTING, StockCountStatus.SUBMITTED),
        )
        .order_by("started_at")
        .first()
    )


def lock_inventory_posting_branch(
    *,
    business: Business,
    branch: Branch,
) -> Branch:
    locked_branch = _lock_branch(branch, business)
    if _active_stock_count(locked_branch, business) is not None:
        raise ValidationError(
            _("Inventory posting is temporarily paused for this branch. Ask a manager for help.")
        )
    return locked_branch


def _validate_variant_scope(
    business: Business,
    branch: Branch,
    variant: ProductVariant,
) -> None:
    errors: dict[str, ValidationError] = {}
    if branch.business_id != business.id or not branch.is_active:
        errors["branch"] = ValidationError(_("Branch must be active in this business."))
    if variant.business_id != business.id or not variant.is_active:
        errors["variant"] = ValidationError(_("Variant must be active in this business."))
    if errors:
        raise ValidationError(errors)


def _validate_historical_variant_scope(
    business: Business,
    branch: Branch,
    variant: ProductVariant,
) -> None:
    errors: dict[str, ValidationError] = {}
    if branch.business_id != business.id or not branch.is_active:
        errors["branch"] = ValidationError(_("Branch must be active in this business."))
    if variant.business_id != business.id:
        errors["variant"] = ValidationError(_("Variant must belong to this business."))
    if errors:
        raise ValidationError(errors)


def _locked_balance(
    *,
    business: Business,
    branch: Branch,
    variant: ProductVariant,
) -> InventoryBalance:
    try:
        return InventoryBalance.objects.select_for_update().get(
            business=business,
            branch=branch,
            variant=variant,
        )
    except InventoryBalance.DoesNotExist:
        try:
            with transaction.atomic():
                with _translate_inventory_constraint_errors():
                    InventoryBalance.objects.create(
                        business=business,
                        branch=branch,
                        variant=variant,
                    )
        except IntegrityError:
            pass
        return InventoryBalance.objects.select_for_update().get(
            business=business,
            branch=branch,
            variant=variant,
        )


def _lock_variants(variants: list[ProductVariant]) -> None:
    variant_ids = sorted({variant.id for variant in variants}, key=str)
    locked_ids = list(
        ProductVariant.objects.select_for_update()
        .filter(id__in=variant_ids)
        .order_by("id")
        .values_list("id", flat=True)
    )
    if len(locked_ids) != len(variant_ids):
        raise ValidationError(_("One or more product variants no longer exist."))


def _apply_inbound(
    *,
    balance: InventoryBalance,
    quantity: Decimal,
    unit_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    inbound_quantity = _quantity(quantity)
    inbound_cost = _cost(unit_cost)
    current_value = _value(balance.inventory_value)
    inbound_value = _value(inbound_quantity * inbound_cost)
    new_quantity = _quantity(balance.quantity_on_hand + inbound_quantity)
    new_value = _value(current_value + inbound_value)
    new_average = _cost(new_value / new_quantity)
    balance.quantity_on_hand = new_quantity
    balance.inventory_value = new_value
    balance.average_unit_cost = new_average
    with _translate_inventory_constraint_errors():
        balance.save(
            update_fields=("quantity_on_hand", "inventory_value", "average_unit_cost", "updated_at")
        )
    return inbound_cost, inbound_value


def _apply_outbound(
    *,
    balance: InventoryBalance,
    quantity: Decimal,
) -> tuple[Decimal, Decimal]:
    outbound_quantity = _quantity(quantity)
    if balance.quantity_on_hand < outbound_quantity:
        raise ValidationError(_("This operation would make stock negative."))
    assigned_cost = _cost(balance.average_unit_cost)
    new_quantity = _quantity(balance.quantity_on_hand - outbound_quantity)
    if new_quantity == 0:
        new_value = Decimal("0.000000")
        new_average = Decimal("0.000000")
    else:
        outbound_value = _value(outbound_quantity * assigned_cost)
        new_value = max(
            _value(balance.inventory_value - outbound_value),
            Decimal("0.000000"),
        )
        new_average = Decimal("0.000000") if new_value == 0 else assigned_cost
    value_delta = _value(new_value - balance.inventory_value)
    balance.quantity_on_hand = new_quantity
    balance.inventory_value = new_value
    balance.average_unit_cost = new_average
    with _translate_inventory_constraint_errors():
        balance.save(
            update_fields=("quantity_on_hand", "inventory_value", "average_unit_cost", "updated_at")
        )
    return assigned_cost, value_delta


def _apply_outbound_at_cost(
    *,
    balance: InventoryBalance,
    quantity: Decimal,
    unit_cost: Decimal,
) -> tuple[Decimal, Decimal]:
    outbound_quantity = _quantity(quantity)
    if balance.quantity_on_hand < outbound_quantity:
        raise ValidationError(_("This operation would make stock negative."))
    assigned_cost = _cost(unit_cost)
    outbound_value = _value(outbound_quantity * assigned_cost)
    if balance.inventory_value < outbound_value:
        raise ValidationError(_("This operation would make inventory value negative."))
    new_quantity = _quantity(balance.quantity_on_hand - outbound_quantity)
    new_value = _value(balance.inventory_value - outbound_value)
    if new_quantity == 0:
        if new_value != 0:
            raise ValidationError(
                _("This operation cannot preserve inventory value at zero stock.")
            )
        new_average = Decimal("0.000000")
    else:
        new_average = Decimal("0.000000") if new_value == 0 else _cost(new_value / new_quantity)
    value_delta = -outbound_value
    balance.quantity_on_hand = new_quantity
    balance.inventory_value = new_value
    balance.average_unit_cost = new_average
    with _translate_inventory_constraint_errors():
        balance.save(
            update_fields=("quantity_on_hand", "inventory_value", "average_unit_cost", "updated_at")
        )
    return assigned_cost, value_delta


@transaction.atomic
def record_purchase_receipt_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[PurchaseReceiptInventoryItem],
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_branch_operation_access(actor, branch, management_required=False)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not items:
        raise ValidationError(_("At least one receipt line is required."))
    seen_variants: set[UUID] = set()
    for item in items:
        _validate_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after purchase approval."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
        if item.unit_cost < 0:
            raise ValidationError(_("Unit cost cannot be negative."))
        if item.variant.id in seen_variants:
            raise ValidationError(_("A receipt cannot repeat the same variant."))
        seen_variants.add(item.variant.id)
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda receipt_item: str(receipt_item.variant.id)):
        balances[item.variant.id] = _locked_balance(
            business=business,
            branch=branch,
            variant=item.variant,
        )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_inbound(
            balance=balances[item.variant.id],
            quantity=item.quantity,
            unit_cost=item.unit_cost,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.PURCHASE_RECEIPT,
                quantity_delta=_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.GOODS_RECEIPT_LINE,
                source_id=item.source_id,
                actor=actor,
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def record_purchase_return_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[PurchaseReturnInventoryItem],
    reason: str,
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_branch_operation_access(actor, branch, management_required=True)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not items:
        raise ValidationError(_("At least one purchase return line is required."))
    if not reason.strip():
        raise ValidationError(_("A purchase return reason is required."))
    for item in items:
        _validate_historical_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after purchase approval."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda return_item: str(return_item.variant.id)):
        if item.variant.id not in balances:
            balances[item.variant.id] = _locked_balance(
                business=business,
                branch=branch,
                variant=item.variant,
            )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_outbound(
            balance=balances[item.variant.id],
            quantity=item.quantity,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.PURCHASE_RETURN,
                quantity_delta=-_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.PURCHASE_RETURN_LINE,
                source_id=item.source_id,
                actor=actor,
                reason=reason.strip(),
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def record_purchase_return_reversal_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[PurchaseReturnReversalInventoryItem],
    reason: str,
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_branch_operation_access(actor, branch, management_required=True)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not items:
        raise ValidationError(_("At least one purchase return line is required."))
    if not reason.strip():
        raise ValidationError(_("A purchase return reversal reason is required."))
    for item in items:
        _validate_historical_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after purchase approval."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
        if item.unit_cost < 0:
            raise ValidationError(_("Unit cost cannot be negative."))
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda reversal_item: str(reversal_item.variant.id)):
        if item.variant.id not in balances:
            balances[item.variant.id] = _locked_balance(
                business=business,
                branch=branch,
                variant=item.variant,
            )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_inbound(
            balance=balances[item.variant.id],
            quantity=item.quantity,
            unit_cost=item.unit_cost,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.PURCHASE_RETURN_REVERSAL,
                quantity_delta=_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.PURCHASE_RETURN_REVERSAL,
                source_id=item.source_id,
                actor=actor,
                reason=reason.strip(),
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def record_sale_return_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[SaleReturnInventoryItem],
    reason: str,
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_sale_branch_access(actor, branch)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not actor.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required."))
    if not items:
        raise ValidationError(_("At least one sale return line is required."))
    if not reason.strip():
        raise ValidationError(_("A sale return reason is required."))
    for item in items:
        _validate_historical_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after sale posting."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
        if item.unit_cost < 0:
            raise ValidationError(_("Restoration cost cannot be negative."))
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda return_item: str(return_item.variant.id)):
        if item.variant.id not in balances:
            balances[item.variant.id] = _locked_balance(
                business=business,
                branch=branch,
                variant=item.variant,
            )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_inbound(
            balance=balances[item.variant.id],
            quantity=item.quantity,
            unit_cost=item.unit_cost,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.SALE_RETURN,
                quantity_delta=_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.SALE_RETURN_LINE,
                source_id=item.source_id,
                actor=actor,
                reason=reason.strip(),
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def record_sale_return_reversal_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[SaleReturnReversalInventoryItem],
    reason: str,
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_sale_branch_access(actor, branch)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not actor.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required."))
    if not items:
        raise ValidationError(_("At least one sale return reversal line is required."))
    if not reason.strip():
        raise ValidationError(_("A sale return reversal reason is required."))
    for item in items:
        _validate_historical_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after sale posting."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
        if item.unit_cost < 0:
            raise ValidationError(_("Reversal cost cannot be negative."))
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda reversal_item: str(reversal_item.variant.id)):
        if item.variant.id not in balances:
            balances[item.variant.id] = _locked_balance(
                business=business,
                branch=branch,
                variant=item.variant,
            )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_outbound_at_cost(
            balance=balances[item.variant.id],
            quantity=item.quantity,
            unit_cost=item.unit_cost,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.SALE_RETURN_REVERSAL,
                quantity_delta=-_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.SALE_RETURN_REVERSAL,
                source_id=item.source_id,
                actor=actor,
                reason=reason.strip(),
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def record_sale_inventory(
    *,
    actor: BusinessMembership,
    business: Business,
    branch: Branch,
    items: list[SaleInventoryItem],
    posted_at: datetime,
) -> list[InventoryMovement]:
    ensure_sale_branch_access(actor, branch)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    if not items:
        raise ValidationError(_("At least one sale line is required."))
    seen_variants: set[UUID] = set()
    for item in items:
        _validate_variant_scope(business, branch, item.variant)
        if item.variant.stock_unit != item.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after the sale draft."))
        validate_stock_quantity(item.quantity, item.unit_snapshot)
        if item.variant.id in seen_variants:
            raise ValidationError(_("A sale cannot repeat the same variant."))
        seen_variants.add(item.variant.id)
    _lock_variants([item.variant for item in items])

    balances: dict[UUID, InventoryBalance] = {}
    for item in sorted(items, key=lambda sale_item: str(sale_item.variant.id)):
        balances[item.variant.id] = _locked_balance(
            business=business,
            branch=branch,
            variant=item.variant,
        )

    movements: list[InventoryMovement] = []
    for item in items:
        assigned_cost, value_delta = _apply_outbound(
            balance=balances[item.variant.id],
            quantity=item.quantity,
        )
        with _translate_inventory_constraint_errors():
            movement = InventoryMovement.objects.create(
                business=business,
                branch=branch,
                variant=item.variant,
                movement_type=InventoryMovementType.SALE,
                quantity_delta=-_quantity(item.quantity),
                unit_cost=assigned_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.SALE_LINE,
                source_id=item.source_id,
                actor=actor,
                posted_at=posted_at,
            )
        movements.append(movement)
    return movements


@transaction.atomic
def post_opening_balance(
    *,
    actor: BusinessMembership,
    branch: Branch,
    variant: ProductVariant,
    quantity: Decimal,
    unit_cost: Decimal,
    idempotency_key: UUID,
    recorded_at: datetime | None = None,
) -> StockOperation:
    business = actor.business
    ensure_branch_operation_access(actor, branch, management_required=True)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    _validate_variant_scope(business, branch, variant)
    validate_stock_quantity(quantity, variant.stock_unit)
    if unit_cost < 0:
        raise ValidationError(_("Unit cost cannot be negative."))
    _lock_variants([variant])
    existing = StockOperation.objects.filter(
        business=business,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        matching_movement = InventoryMovement.objects.filter(
            business=business,
            branch=branch,
            variant=variant,
            source_type=InventorySourceType.STOCK_OPERATION,
            source_id=existing.id,
        ).exists()
        if existing.operation_type != StockOperationType.OPENING or not matching_movement:
            raise ValidationError(_("This idempotency key belongs to another stock operation."))
        return existing

    balance = _locked_balance(business=business, branch=branch, variant=variant)
    if InventoryMovement.objects.filter(
        business=business,
        branch=branch,
        variant=variant,
    ).exists():
        raise ValidationError(_("Opening stock is allowed only before the first movement."))
    timestamp = recorded_at or timezone.now()
    with _translate_inventory_constraint_errors():
        operation = StockOperation.objects.create(
            business=business,
            branch=branch,
            operation_type=StockOperationType.OPENING,
            idempotency_key=idempotency_key,
            actor=actor,
            posted_at=timestamp,
        )
    assigned_cost, value_delta = _apply_inbound(
        balance=balance,
        quantity=quantity,
        unit_cost=unit_cost,
    )
    with _translate_inventory_constraint_errors():
        InventoryMovement.objects.create(
            business=business,
            branch=branch,
            variant=variant,
            movement_type=InventoryMovementType.OPENING,
            quantity_delta=_quantity(quantity),
            unit_cost=assigned_cost,
            value_delta=value_delta,
            source_type=InventorySourceType.STOCK_OPERATION,
            source_id=operation.id,
            actor=actor,
            posted_at=timestamp,
        )
    return operation


@transaction.atomic
def post_inventory_adjustment(
    *,
    actor: BusinessMembership,
    branch: Branch,
    variant: ProductVariant,
    operation_type: str,
    quantity: Decimal,
    reason: str,
    idempotency_key: UUID,
    unit_cost: Decimal | None = None,
    recorded_at: datetime | None = None,
) -> StockOperation:
    business = actor.business
    ensure_branch_operation_access(actor, branch, management_required=True)
    branch = lock_inventory_posting_branch(business=business, branch=branch)
    _validate_variant_scope(business, branch, variant)
    validate_stock_quantity(quantity, variant.stock_unit)
    if operation_type not in {
        StockOperationType.ADJUSTMENT_IN,
        StockOperationType.ADJUSTMENT_OUT,
    }:
        raise ValidationError(_("Select a valid adjustment direction."))
    if not reason.strip():
        raise ValidationError(_("An adjustment reason is required."))
    if operation_type == StockOperationType.ADJUSTMENT_IN:
        if unit_cost is None or unit_cost < 0:
            raise ValidationError(_("A non-negative unit cost is required for stock increases."))
    _lock_variants([variant])
    existing = StockOperation.objects.filter(
        business=business,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        matching_movement = InventoryMovement.objects.filter(
            business=business,
            branch=branch,
            variant=variant,
            source_type=InventorySourceType.STOCK_OPERATION,
            source_id=existing.id,
        ).exists()
        if existing.operation_type != operation_type or not matching_movement:
            raise ValidationError(_("This idempotency key belongs to another stock operation."))
        return existing

    balance = _locked_balance(business=business, branch=branch, variant=variant)
    timestamp = recorded_at or timezone.now()
    with _translate_inventory_constraint_errors():
        operation = StockOperation.objects.create(
            business=business,
            branch=branch,
            operation_type=operation_type,
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason.strip(),
            posted_at=timestamp,
        )
    if operation_type == StockOperationType.ADJUSTMENT_IN:
        if unit_cost is None:
            raise ValidationError(_("Unit cost is required."))
        assigned_cost, value_delta = _apply_inbound(
            balance=balance,
            quantity=quantity,
            unit_cost=unit_cost,
        )
        quantity_delta = _quantity(quantity)
        movement_type = InventoryMovementType.ADJUSTMENT_IN
    else:
        assigned_cost, value_delta = _apply_outbound(balance=balance, quantity=quantity)
        quantity_delta = -_quantity(quantity)
        movement_type = InventoryMovementType.ADJUSTMENT_OUT
    with _translate_inventory_constraint_errors():
        InventoryMovement.objects.create(
            business=business,
            branch=branch,
            variant=variant,
            movement_type=movement_type,
            quantity_delta=quantity_delta,
            unit_cost=assigned_cost,
            value_delta=value_delta,
            source_type=InventorySourceType.STOCK_OPERATION,
            source_id=operation.id,
            actor=actor,
            reason=operation.reason,
            posted_at=timestamp,
        )
    return operation


def _validate_count_quantity(quantity: Decimal, stock_unit: str) -> Decimal:
    counted_quantity = _quantity(quantity)
    if counted_quantity < 0:
        raise ValidationError(_("Physical quantity cannot be negative."))
    if stock_unit in WHOLE_STOCK_UNITS and counted_quantity != counted_quantity.to_integral_value():
        raise ValidationError(_("This stock unit requires a whole-number quantity."))
    return counted_quantity


def _claim_stock_count_posting_key(
    *,
    business: Business,
    key: UUID,
    operation_type: str,
    source_id: UUID,
    allow_existing_source: bool = False,
) -> StockCountPostingKey:
    existing = (
        StockCountPostingKey.objects.select_for_update().filter(business=business, key=key).first()
    )
    if existing is not None:
        if existing.operation_type != operation_type or (
            existing.source_id != source_id and not allow_existing_source
        ):
            raise ValidationError(
                _("This idempotency key belongs to another stock-count operation.")
            )
        return existing
    try:
        with transaction.atomic():
            with _translate_inventory_constraint_errors():
                return StockCountPostingKey.objects.create(
                    business=business,
                    key=key,
                    operation_type=operation_type,
                    source_id=source_id,
                )
    except (IntegrityError, ValidationError) as error:
        existing = StockCountPostingKey.objects.select_for_update().get(
            business=business,
            key=key,
        )
        if existing.operation_type != operation_type or (
            existing.source_id != source_id and not allow_existing_source
        ):
            raise ValidationError(
                _("This idempotency key belongs to another stock-count operation.")
            ) from error
        return existing


def _locked_stock_count_session(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
    management_required: bool,
) -> tuple[Branch, StockCountSession]:
    ensure_stock_count_branch_access(
        actor,
        session.branch,
        management_required=management_required,
    )
    branch = _lock_branch(session.branch, actor.business)
    try:
        locked = (
            StockCountSession.objects.select_for_update()
            .select_related("branch")
            .get(pk=session.pk, business=actor.business, branch=branch)
        )
    except StockCountSession.DoesNotExist as error:
        raise ValidationError(_("Stock-count session was not found.")) from error
    return branch, locked


def _ensure_session_freeze(branch: Branch, session: StockCountSession) -> None:
    active = _active_stock_count(branch, session.business)
    if active is None or active.id != session.id:
        raise ValidationError(_("This branch is no longer frozen by this stock count."))


def _stock_count_variant_label(variant: ProductVariant) -> str:
    return " / ".join(value for value in (variant.size, variant.color) if value)


@transaction.atomic
def start_stock_count(
    *,
    actor: BusinessMembership,
    branch: Branch,
    count_method_note: str,
    idempotency_key: UUID,
    started_at: datetime | None = None,
) -> StockCountSession:
    ensure_stock_count_branch_access(actor, branch, management_required=True)
    business = actor.business
    clean_note = count_method_note.strip()
    if not clean_note:
        raise ValidationError(_("A count method note is required."))
    locked_branch = _lock_branch(branch, business)
    existing_key = (
        StockCountPostingKey.objects.select_for_update()
        .filter(business=business, key=idempotency_key)
        .first()
    )
    if existing_key is not None:
        if existing_key.operation_type != StockCountOperationType.START:
            raise ValidationError(
                _("This idempotency key belongs to another stock-count operation.")
            )
        try:
            existing_session = StockCountSession.objects.get(
                pk=existing_key.source_id,
                business=business,
                start_key=existing_key,
            )
        except StockCountSession.DoesNotExist as error:
            raise ValidationError(_("The stock-count start is incomplete.")) from error
        if existing_session.branch_id != locked_branch.id:
            raise ValidationError(
                _("This idempotency key belongs to another stock-count operation.")
            )
        return existing_session
    if _active_stock_count(locked_branch, business) is not None:
        raise ValidationError(_("This branch already has an active stock count."))

    timestamp = started_at or timezone.now()
    session_id = uuid4()
    start_key = _claim_stock_count_posting_key(
        business=business,
        key=idempotency_key,
        operation_type=StockCountOperationType.START,
        source_id=session_id,
        allow_existing_source=True,
    )
    if start_key.source_id != session_id:
        try:
            existing_session = StockCountSession.objects.get(
                pk=start_key.source_id,
                business=business,
                start_key=start_key,
            )
        except StockCountSession.DoesNotExist as error:
            raise ValidationError(_("The stock-count start is incomplete.")) from error
        if existing_session.branch_id != locked_branch.id:
            raise ValidationError(
                _("This idempotency key belongs to another stock-count operation.")
            )
        return existing_session
    with _translate_inventory_constraint_errors():
        session = StockCountSession.objects.create(
            id=session_id,
            business=business,
            branch=locked_branch,
            status=StockCountStatus.COUNTING,
            business_date=timezone.localdate(timestamp),
            count_method_note=clean_note,
            start_key=start_key,
            started_by=actor,
            started_at=timestamp,
        )

    nonzero_balance_variant_ids = list(
        InventoryBalance.objects.filter(
            business=business,
            branch=locked_branch,
        )
        .exclude(quantity_on_hand=Decimal("0.000"))
        .values_list("variant_id", flat=True)
    )
    variants = list(
        ProductVariant.objects.select_for_update()
        .select_related("product")
        .filter(business=business)
        .filter(Q(is_active=True) | Q(id__in=nonzero_balance_variant_ids))
        .order_by("id")
    )
    if not variants:
        raise ValidationError(_("Add at least one active product variant before starting a count."))
    balances = {
        balance.variant_id: balance
        for balance in InventoryBalance.objects.select_for_update()
        .filter(
            business=business,
            branch=locked_branch,
            variant_id__in=[variant.id for variant in variants],
        )
        .order_by("variant_id")
    }
    for variant in variants:
        balance = balances.get(variant.id)
        system_quantity = balance.quantity_on_hand if balance is not None else Decimal("0.000")
        average_cost = balance.average_unit_cost if balance is not None else Decimal("0.000000")
        inventory_value = balance.inventory_value if balance is not None else Decimal("0.000000")
        with _translate_inventory_constraint_errors():
            StockCountLine.objects.create(
                business=business,
                branch=locked_branch,
                session=session,
                variant=variant,
                product_name_snapshot=variant.product.name,
                variant_label_snapshot=_stock_count_variant_label(variant),
                sku_snapshot=variant.sku,
                stock_unit_snapshot=variant.stock_unit,
                system_quantity_snapshot=system_quantity,
                average_unit_cost_snapshot=average_cost,
                inventory_value_snapshot=inventory_value,
            )
    return session


@transaction.atomic
def record_stock_count_quantity(
    *,
    actor: BusinessMembership,
    line: StockCountLine,
    physical_quantity: Decimal,
    replacement_reason: str = "",
    counted_at: datetime | None = None,
) -> StockCountLine:
    branch, session = _locked_stock_count_session(
        actor=actor,
        session=line.session,
        management_required=False,
    )
    _ensure_session_freeze(branch, session)
    if session.status != StockCountStatus.COUNTING:
        raise ValidationError(_("Only a counting worksheet can be changed."))
    try:
        locked_line = StockCountLine.objects.select_for_update().get(
            pk=line.pk,
            business=actor.business,
            branch=branch,
            session=session,
        )
    except StockCountLine.DoesNotExist as error:
        raise ValidationError(_("Stock-count line was not found.")) from error
    quantity = _validate_count_quantity(
        physical_quantity,
        locked_line.stock_unit_snapshot,
    )
    if locked_line.physical_quantity == quantity:
        return locked_line
    clean_reason = replacement_reason.strip()
    if locked_line.physical_quantity is not None and not clean_reason:
        raise ValidationError(_("Explain why the previous physical count is being replaced."))
    sequence = (locked_line.revisions.aggregate(maximum=Max("sequence"))["maximum"] or 0) + 1
    timestamp = counted_at or timezone.now()
    with _translate_inventory_constraint_errors():
        StockCountLineRevision.objects.create(
            business=actor.business,
            branch=branch,
            session=session,
            line=locked_line,
            sequence=sequence,
            previous_quantity=locked_line.physical_quantity,
            replacement_quantity=quantity,
            reason=clean_reason,
            actor=actor,
            revised_at=timestamp,
        )
        locked_line.physical_quantity = quantity
        locked_line.variance_quantity = None
        locked_line.variance_explanation = ""
        locked_line.assigned_count_adjustment_unit_cost = None
        locked_line.exceptional_cost_evidence_note = ""
        locked_line.counted_by = actor
        locked_line.counted_at = timestamp
        locked_line.save(
            update_fields=(
                "physical_quantity",
                "variance_quantity",
                "variance_explanation",
                "assigned_count_adjustment_unit_cost",
                "exceptional_cost_evidence_note",
                "counted_by",
                "counted_at",
            )
        )
    return locked_line


@transaction.atomic
def submit_stock_count(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
    submitted_at: datetime | None = None,
) -> StockCountSession:
    branch, locked = _locked_stock_count_session(
        actor=actor,
        session=session,
        management_required=False,
    )
    _ensure_session_freeze(branch, locked)
    if locked.status == StockCountStatus.SUBMITTED:
        return locked
    if locked.status != StockCountStatus.COUNTING:
        raise ValidationError(_("Only a counting worksheet can be submitted."))
    lines = list(
        StockCountLine.objects.select_for_update().filter(session=locked).order_by("variant_id")
    )
    if not lines:
        raise ValidationError(_("A stock count must contain at least one line."))
    if any(line.physical_quantity is None for line in lines):
        raise ValidationError(_("Count every stock-count line, including explicit zeroes."))
    for line in lines:
        if line.physical_quantity is None:
            raise ValidationError(_("Count every stock-count line, including explicit zeroes."))
        variance = _quantity(line.physical_quantity - line.system_quantity_snapshot)
        line.variance_quantity = variance
        if variance == 0:
            line.assigned_count_adjustment_unit_cost = None
        elif line.average_unit_cost_snapshot > 0 or variance < 0:
            line.assigned_count_adjustment_unit_cost = line.average_unit_cost_snapshot
        else:
            line.assigned_count_adjustment_unit_cost = None
        line.save(
            update_fields=(
                "variance_quantity",
                "assigned_count_adjustment_unit_cost",
            )
        )
    locked.status = StockCountStatus.SUBMITTED
    locked.submitted_by = actor
    locked.submitted_at = submitted_at or timezone.now()
    locked.save(update_fields=("status", "submitted_by", "submitted_at", "updated_at"))
    return locked


@transaction.atomic
def return_stock_count_for_recount(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
    reason: str,
    returned_at: datetime | None = None,
) -> StockCountReviewReturn:
    branch, locked = _locked_stock_count_session(
        actor=actor,
        session=session,
        management_required=True,
    )
    _ensure_session_freeze(branch, locked)
    if locked.status != StockCountStatus.SUBMITTED:
        raise ValidationError(_("Only a submitted stock count can be returned for recount."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A recount reason is required."))
    sequence = (locked.review_returns.aggregate(maximum=Max("sequence"))["maximum"] or 0) + 1
    timestamp = returned_at or timezone.now()
    with _translate_inventory_constraint_errors():
        review_return = StockCountReviewReturn.objects.create(
            business=actor.business,
            branch=branch,
            session=locked,
            sequence=sequence,
            reason=clean_reason,
            returned_by=actor,
            returned_at=timestamp,
        )
        for line in StockCountLine.objects.select_for_update().filter(session=locked):
            line.variance_quantity = None
            line.variance_explanation = ""
            line.assigned_count_adjustment_unit_cost = None
            line.exceptional_cost_evidence_note = ""
            line.save(
                update_fields=(
                    "variance_quantity",
                    "variance_explanation",
                    "assigned_count_adjustment_unit_cost",
                    "exceptional_cost_evidence_note",
                )
            )
        locked.status = StockCountStatus.COUNTING
        locked.submitted_by = None
        locked.submitted_at = None
        locked.save(update_fields=("status", "submitted_by", "submitted_at", "updated_at"))
    return review_return


@transaction.atomic
def cancel_stock_count(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
    reason: str,
    cancelled_at: datetime | None = None,
) -> StockCountSession:
    branch, locked = _locked_stock_count_session(
        actor=actor,
        session=session,
        management_required=True,
    )
    _ensure_session_freeze(branch, locked)
    if locked.status not in {StockCountStatus.COUNTING, StockCountStatus.SUBMITTED}:
        raise ValidationError(_("Only an active stock count can be cancelled."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A stock-count cancellation reason is required."))
    locked.status = StockCountStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = cancelled_at or timezone.now()
    locked.cancellation_reason = clean_reason
    locked.save(
        update_fields=(
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "updated_at",
        )
    )
    return locked


@transaction.atomic
def record_stock_count_review_evidence(
    *,
    actor: BusinessMembership,
    line: StockCountLine,
    variance_explanation: str,
    exceptional_unit_cost: Decimal | None = None,
    exceptional_cost_evidence_note: str = "",
) -> StockCountLine:
    branch, session = _locked_stock_count_session(
        actor=actor,
        session=line.session,
        management_required=True,
    )
    _ensure_session_freeze(branch, session)
    if session.status != StockCountStatus.SUBMITTED:
        raise ValidationError(_("Review evidence requires a submitted stock count."))
    locked_line = StockCountLine.objects.select_for_update().get(
        pk=line.pk,
        business=actor.business,
        branch=branch,
        session=session,
    )
    variance = locked_line.variance_quantity
    if variance is None:
        raise ValidationError(_("The stock-count variance has not been calculated."))
    clean_explanation = variance_explanation.strip()
    if variance != 0 and not clean_explanation:
        raise ValidationError(_("Explain every nonzero stock count quantity variance."))
    clean_evidence = exceptional_cost_evidence_note.strip()
    assigned_cost: Decimal | None
    if variance > 0 and locked_line.average_unit_cost_snapshot == 0:
        if exceptional_unit_cost is None or exceptional_unit_cost < 0:
            raise ValidationError(
                _("A non-negative exceptional count-adjustment unit cost is required.")
            )
        if not clean_evidence:
            raise ValidationError(_("Exceptional unit-cost evidence is required."))
        assigned_cost = _cost(exceptional_unit_cost)
    else:
        if exceptional_unit_cost is not None or clean_evidence:
            raise ValidationError(_("Snapshot cost cannot be replaced for this stock-count line."))
        assigned_cost = None if variance == 0 else locked_line.average_unit_cost_snapshot
    locked_line.variance_explanation = clean_explanation
    locked_line.assigned_count_adjustment_unit_cost = assigned_cost
    locked_line.exceptional_cost_evidence_note = clean_evidence
    locked_line.save(
        update_fields=(
            "variance_explanation",
            "assigned_count_adjustment_unit_cost",
            "exceptional_cost_evidence_note",
        )
    )
    return locked_line


def stock_count_review_summary(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
) -> StockCountReviewSummary:
    ensure_stock_count_branch_access(actor, session.branch, management_required=True)
    if session.business_id != actor.business_id:
        raise ValidationError(_("Stock-count session was not found."))
    lines = list(session.lines.order_by("stock_unit_snapshot", "variant_id"))
    line_count = len(lines)
    counted_line_count = sum(line.physical_quantity is not None for line in lines)
    positive_count = 0
    negative_count = 0
    zero_count = 0
    missing_cost_count = 0
    total_value = Decimal("0.000000")
    grouped: dict[str, tuple[Decimal, Decimal, int]] = {}
    for line in lines:
        variance = line.variance_quantity
        positive_quantity, negative_quantity, zero_lines = grouped.get(
            line.stock_unit_snapshot,
            (Decimal("0.000"), Decimal("0.000"), 0),
        )
        if variance is None:
            grouped[line.stock_unit_snapshot] = (
                positive_quantity,
                negative_quantity,
                zero_lines,
            )
            continue
        if variance > 0:
            positive_count += 1
            positive_quantity = _quantity(positive_quantity + variance)
        elif variance < 0:
            negative_count += 1
            negative_quantity = _quantity(negative_quantity + variance)
        else:
            zero_count += 1
            zero_lines += 1
        assigned_cost = line.assigned_count_adjustment_unit_cost
        if variance != 0 and assigned_cost is None:
            missing_cost_count += 1
        elif assigned_cost is not None:
            total_value = _value(total_value + _value(variance * assigned_cost))
        grouped[line.stock_unit_snapshot] = (
            positive_quantity,
            negative_quantity,
            zero_lines,
        )
    unit_summaries = tuple(
        StockCountUnitVarianceSummary(
            stock_unit=stock_unit,
            positive_quantity=values[0],
            negative_quantity=values[1],
            zero_variance_line_count=values[2],
        )
        for stock_unit, values in sorted(grouped.items())
    )
    return StockCountReviewSummary(
        line_count=line_count,
        counted_line_count=counted_line_count,
        remaining_line_count=line_count - counted_line_count,
        positive_variance_line_count=positive_count,
        negative_variance_line_count=negative_count,
        zero_variance_line_count=zero_count,
        missing_exceptional_cost_line_count=missing_cost_count,
        total_inventory_value_adjustment=total_value,
        unit_summaries=unit_summaries,
    )


def _stock_count_evidence_checksum(
    session: StockCountSession,
    lines: list[StockCountLine],
) -> str:
    evidence = [str(session.id), str(session.business_id), str(session.branch_id)]
    for line in lines:
        evidence.extend(
            (
                str(line.id),
                str(line.variant_id),
                str(line.system_quantity_snapshot),
                str(line.physical_quantity),
                str(line.variance_quantity),
                str(line.assigned_count_adjustment_unit_cost),
                line.variance_explanation,
                line.exceptional_cost_evidence_note,
            )
        )
    return sha256("\x1f".join(evidence).encode()).hexdigest()


@transaction.atomic
def approve_stock_count(
    *,
    actor: BusinessMembership,
    session: StockCountSession,
    idempotency_key: UUID,
    approved_at: datetime | None = None,
) -> StockCountApproval:
    branch, locked = _locked_stock_count_session(
        actor=actor,
        session=session,
        management_required=True,
    )
    posting_key = _claim_stock_count_posting_key(
        business=actor.business,
        key=idempotency_key,
        operation_type=StockCountOperationType.APPROVE,
        source_id=locked.id,
    )
    existing_approval = StockCountApproval.objects.filter(posting_key=posting_key).first()
    if existing_approval is not None:
        return existing_approval
    _ensure_session_freeze(branch, locked)
    if locked.status != StockCountStatus.SUBMITTED:
        raise ValidationError(_("Only a submitted stock count can be approved."))
    lines = list(
        StockCountLine.objects.select_for_update()
        .select_related("variant")
        .filter(session=locked)
        .order_by("variant_id")
    )
    if not lines:
        raise ValidationError(_("A stock count must contain at least one line."))
    _lock_variants([line.variant for line in lines])
    balances = {
        line.variant_id: _locked_balance(
            business=actor.business,
            branch=branch,
            variant=line.variant,
        )
        for line in lines
    }
    positive_count = 0
    negative_count = 0
    zero_count = 0
    total_value = Decimal("0.000000")
    timestamp = approved_at or timezone.now()
    for line in lines:
        if line.physical_quantity is None:
            raise ValidationError(_("Count every stock-count line, including explicit zeroes."))
        quantity = _validate_count_quantity(
            line.physical_quantity,
            line.stock_unit_snapshot,
        )
        variance = _quantity(quantity - line.system_quantity_snapshot)
        if line.variance_quantity != variance:
            raise ValidationError(_("Stock-count variance evidence changed before approval."))
        if variance != 0 and not line.variance_explanation.strip():
            raise ValidationError(_("Explain every nonzero stock count quantity variance."))
        assigned_cost = line.assigned_count_adjustment_unit_cost
        if variance > 0 and line.average_unit_cost_snapshot == 0:
            if assigned_cost is None or not line.exceptional_cost_evidence_note.strip():
                raise ValidationError(
                    _("Exceptional unit-cost evidence is required before approval.")
                )
        elif variance != 0 and assigned_cost != line.average_unit_cost_snapshot:
            raise ValidationError(_("Snapshot cost cannot be replaced for this stock-count line."))
        balance = balances[line.variant_id]
        if (
            balance.quantity_on_hand != line.system_quantity_snapshot
            or balance.average_unit_cost != line.average_unit_cost_snapshot
            or balance.inventory_value != line.inventory_value_snapshot
        ):
            raise ValidationError(_("Inventory changed after the stock-count snapshot."))
        if variance > 0:
            if assigned_cost is None:
                raise ValidationError(_("A count-adjustment unit cost is required."))
            positive_count += 1
            movement_cost, value_delta = _apply_inbound(
                balance=balance,
                quantity=variance,
                unit_cost=assigned_cost,
            )
            movement_type = InventoryMovementType.STOCK_COUNT_ADJUSTMENT_IN
        elif variance < 0:
            if assigned_cost is None:
                raise ValidationError(_("A count-adjustment unit cost is required."))
            negative_count += 1
            movement_cost, value_delta = _apply_outbound_at_cost(
                balance=balance,
                quantity=-variance,
                unit_cost=assigned_cost,
            )
            movement_type = InventoryMovementType.STOCK_COUNT_ADJUSTMENT_OUT
        else:
            zero_count += 1
            continue
        total_value = _value(total_value + value_delta)
        with _translate_inventory_constraint_errors():
            InventoryMovement.objects.create(
                business=actor.business,
                branch=branch,
                variant=line.variant,
                movement_type=movement_type,
                quantity_delta=variance,
                unit_cost=movement_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.STOCK_COUNT_LINE,
                source_id=line.id,
                actor=actor,
                reason=line.variance_explanation.strip(),
                posted_at=timestamp,
            )
    with _translate_inventory_constraint_errors():
        approval = StockCountApproval.objects.create(
            business=actor.business,
            branch=branch,
            session=locked,
            posting_key=posting_key,
            approved_by=actor,
            approved_at=timestamp,
            line_count=len(lines),
            positive_variance_line_count=positive_count,
            negative_variance_line_count=negative_count,
            zero_variance_line_count=zero_count,
            total_inventory_value_adjustment=total_value,
            evidence_checksum=_stock_count_evidence_checksum(locked, lines),
        )
        locked.status = StockCountStatus.APPROVED
        locked.save(update_fields=("status", "updated_at"))
    return approval


@transaction.atomic
def reverse_stock_count(
    *,
    actor: BusinessMembership,
    approval: StockCountApproval,
    reason: str,
    idempotency_key: UUID,
    reversed_at: datetime | None = None,
) -> StockCountReversal:
    ensure_stock_count_branch_access(actor, approval.branch, management_required=True)
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A stock-count reversal reason is required."))
    branch = _lock_branch(approval.branch, actor.business)
    if _active_stock_count(branch, actor.business) is not None:
        raise ValidationError(
            _("Inventory posting is temporarily paused for this branch. Ask a manager for help.")
        )
    try:
        locked_approval = (
            StockCountApproval.objects.select_for_update()
            .select_related("session")
            .get(pk=approval.pk, business=actor.business, branch=branch)
        )
    except StockCountApproval.DoesNotExist as error:
        raise ValidationError(_("Stock-count approval was not found.")) from error
    posting_key = _claim_stock_count_posting_key(
        business=actor.business,
        key=idempotency_key,
        operation_type=StockCountOperationType.REVERSE,
        source_id=locked_approval.session_id,
    )
    existing_reversal = StockCountReversal.objects.filter(posting_key=posting_key).first()
    if existing_reversal is not None:
        return existing_reversal
    if StockCountReversal.objects.filter(approval=locked_approval).exists():
        raise ValidationError(_("This stock count was already reversed."))
    lines = list(
        StockCountLine.objects.select_for_update()
        .select_related("variant")
        .filter(session=locked_approval.session)
        .exclude(variance_quantity=Decimal("0.000"))
        .order_by("variant_id")
    )
    _lock_variants([line.variant for line in lines])
    balances = {
        line.variant_id: _locked_balance(
            business=actor.business,
            branch=branch,
            variant=line.variant,
        )
        for line in lines
    }
    timestamp = reversed_at or timezone.now()
    with _translate_inventory_constraint_errors():
        reversal = StockCountReversal.objects.create(
            business=actor.business,
            branch=branch,
            approval=locked_approval,
            posting_key=posting_key,
            reason=clean_reason,
            reversed_by=actor,
            reversed_at=timestamp,
        )
    for line in lines:
        variance = line.variance_quantity
        assigned_cost = line.assigned_count_adjustment_unit_cost
        if variance is None or assigned_cost is None:
            raise ValidationError(_("Approved stock-count adjustment evidence is incomplete."))
        balance = balances[line.variant_id]
        if variance > 0:
            movement_cost, value_delta = _apply_outbound_at_cost(
                balance=balance,
                quantity=variance,
                unit_cost=assigned_cost,
            )
            quantity_delta = -variance
            movement_type = InventoryMovementType.STOCK_COUNT_REVERSAL_OUT
        else:
            movement_cost, value_delta = _apply_inbound(
                balance=balance,
                quantity=-variance,
                unit_cost=assigned_cost,
            )
            quantity_delta = -variance
            movement_type = InventoryMovementType.STOCK_COUNT_REVERSAL_IN
        with _translate_inventory_constraint_errors():
            InventoryMovement.objects.create(
                business=actor.business,
                branch=branch,
                variant=line.variant,
                movement_type=movement_type,
                quantity_delta=quantity_delta,
                unit_cost=movement_cost,
                value_delta=value_delta,
                source_type=InventorySourceType.STOCK_COUNT_REVERSAL,
                source_id=reversal.id,
                actor=actor,
                reason=clean_reason,
                posted_at=timestamp,
            )
    return reversal
