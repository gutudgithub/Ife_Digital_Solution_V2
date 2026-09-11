from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
    StockOperation,
    StockOperationType,
)

QUANTITY_QUANTUM = Decimal("0.001")
COST_QUANTUM = Decimal("0.000001")
VALUE_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class PurchaseReceiptInventoryItem:
    variant: ProductVariant
    quantity: Decimal
    unit_cost: Decimal
    unit_snapshot: str
    source_id: UUID


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _cost(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)


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
        new_average = assigned_cost
        new_value = _value(new_quantity * new_average)
    value_delta = _value(new_value - balance.inventory_value)
    balance.quantity_on_hand = new_quantity
    balance.inventory_value = new_value
    balance.average_unit_cost = new_average
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
        movements.append(
            InventoryMovement.objects.create(
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
        )
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
