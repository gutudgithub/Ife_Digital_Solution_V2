import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, BusinessMembership
from apps.cash.services import (
    lock_open_cash_session,
    record_cash_refund,
    record_cash_sale,
)
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.inventory.services import (
    SaleInventoryItem,
    SaleReturnInventoryItem,
    SaleReturnReversalInventoryItem,
    ensure_sale_branch_access,
    record_sale_inventory,
    record_sale_return_inventory,
    record_sale_return_reversal_inventory,
)
from apps.public_profiles.services import (
    get_or_create_return_receipt_identity,
    get_or_create_sale_receipt_identity,
)
from apps.sales.models import (
    InternalReceipt,
    InternalReturnReceipt,
    Sale,
    SaleLine,
    SalePayment,
    SalePaymentMethod,
    SalePaymentStatus,
    SalePostingKey,
    SaleRefundEvidence,
    SaleReturn,
    SaleReturnLine,
    SaleReturnOperationType,
    SaleReturnPostingKey,
    SaleReturnPurpose,
    SaleReturnReversal,
    SaleReturnStatus,
    SaleStatus,
)

QUANTITY_QUANTUM = Decimal("0.001")
MONEY_QUANTUM = Decimal("0.01")
SALES_CONSTRAINT_NAMES = frozenset(
    {
        "sales_unique_posting_key_per_business",
        "sales_unique_number_per_business",
        "sales_status_is_valid",
        "sales_payment_status_is_valid",
        "sales_total_nonnegative",
        "sales_posted_total_positive",
        "sales_posting_fields_match",
        "sales_cancellation_fields_match",
        "sales_unique_variant_per_sale",
        "sales_line_quantity_positive",
        "sales_line_price_positive",
        "sales_line_total_positive",
        "sales_line_inventory_cost_nonnegative",
        "sales_line_inventory_value_nonpositive",
        "sales_payment_method_is_valid",
        "sales_payment_amount_positive",
        "sales_payment_reference_matches_method",
        "sales_unique_telebirr_reference_per_business",
        "sales_unique_receipt_number_per_business",
        "sales_receipt_total_positive",
        "sales_receipt_payment_method_is_valid",
        "sales_unique_return_key_per_business",
        "sales_return_operation_type_is_valid",
        "sales_unique_return_number_per_business",
        "sales_return_purpose_is_valid",
        "sales_return_status_is_valid",
        "sales_return_total_nonnegative",
        "sales_return_posting_fields_match",
        "sales_return_cancellation_fields_match",
        "sales_return_status_has_audit",
        "sales_unique_sale_line_per_return",
        "sales_return_line_quantity_positive",
        "sales_return_line_price_positive",
        "sales_return_line_total_positive",
        "sales_return_line_cost_nonnegative",
        "sales_return_inventory_value_nonnegative",
        "sales_refund_method_is_valid",
        "sales_refund_amount_positive",
        "sales_refund_reference_matches_method",
        "sales_unique_refund_reference_per_business",
        "sales_unique_return_receipt_number",
        "sales_return_receipt_total_positive",
        "sales_return_receipt_method_valid",
        "sales_return_reversal_method_valid",
        "sales_return_reversal_amount_positive",
    }
)


@dataclass(frozen=True)
class SaleQuantity:
    variant_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class ReturnQuantity:
    sale_line_id: UUID
    quantity: Decimal


@dataclass(frozen=True)
class SaleLineReturnProgress:
    line: SaleLine
    returned_quantity: Decimal
    remaining_quantity: Decimal


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _number(prefix: str) -> str:
    return f"{prefix}-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:10].upper()}"


def new_sale_number() -> str:
    return _number("SAL")


def new_internal_receipt_number() -> str:
    return _number("RCP")


def new_sale_return_number() -> str:
    return _number("SRT")


def new_internal_return_receipt_number() -> str:
    return _number("RRF")


def normalize_telebirr_reference(reference: str) -> str:
    return "".join(reference.split()).upper()


@contextmanager
def _translate_sales_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any(name in message for message in error.messages for name in SALES_CONSTRAINT_NAMES):
            raise ValidationError(
                _("Posting could not be completed because a sales rule was violated.")
            ) from error
        raise


def _validate_sale_actor(actor: BusinessMembership, branch: Branch) -> None:
    ensure_sale_branch_access(actor, branch)


def _selected_variants(
    *,
    actor: BusinessMembership,
    quantities: list[SaleQuantity],
) -> list[tuple[ProductVariant, Decimal]]:
    if not quantities:
        raise ValidationError(_("Enter at least one sale quantity."))
    seen_variants: set[UUID] = set()
    quantity_by_variant: dict[UUID, Decimal] = {}
    for item in quantities:
        if item.variant_id in seen_variants:
            raise ValidationError(_("A sale cannot repeat the same product variant."))
        seen_variants.add(item.variant_id)
        quantity = _quantity(item.quantity)
        if quantity <= 0:
            raise ValidationError(_("Sale quantity must be greater than zero."))
        quantity_by_variant[item.variant_id] = quantity
    variants = list(
        ProductVariant.objects.filter(
            business=actor.business,
            id__in=quantity_by_variant,
            is_active=True,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("id")
    )
    if len(variants) != len(quantity_by_variant):
        raise ValidationError(_("One or more product variants are unavailable."))
    selected: list[tuple[ProductVariant, Decimal]] = []
    for variant in variants:
        quantity = quantity_by_variant[variant.id]
        validate_stock_quantity(quantity, variant.stock_unit)
        if variant.selling_price <= 0:
            raise ValidationError(_("Sale lines require a positive catalog selling price."))
        selected.append((variant, quantity))
    return selected


@transaction.atomic
def save_sale_draft(
    *,
    actor: BusinessMembership,
    branch: Branch,
    sale_date: date,
    quantities: list[SaleQuantity],
    sale: Sale | None = None,
) -> Sale:
    _validate_sale_actor(actor, branch)
    selected = _selected_variants(actor=actor, quantities=quantities)
    if sale is None:
        saved_sale = Sale.objects.create(
            business=actor.business,
            branch=branch,
            internal_number=new_sale_number(),
            sale_date=sale_date,
            created_by=actor,
        )
    else:
        saved_sale = Sale.objects.select_for_update().get(
            pk=sale.pk,
            business=actor.business,
        )
        if saved_sale.status != SaleStatus.DRAFT:
            raise ValidationError(_("Only draft sales can be edited."))
        saved_sale.branch = branch
        saved_sale.sale_date = sale_date
        for line in saved_sale.lines.all():
            line.delete()

    total = Decimal("0.00")
    for variant, quantity in selected:
        line = SaleLine.objects.create(
            business=actor.business,
            sale=saved_sale,
            variant=variant,
            quantity=quantity,
            product_name_snapshot=variant.product.name,
            sku_snapshot=variant.sku,
            unit_snapshot=variant.stock_unit,
            selling_unit_price=variant.selling_price,
            line_total=_money(quantity * variant.selling_price),
        )
        total += line.line_total
    saved_sale.total_amount = _money(total)
    saved_sale.save(update_fields=("branch", "sale_date", "total_amount", "updated_at"))
    return saved_sale


@transaction.atomic
def cancel_sale(
    *,
    actor: BusinessMembership,
    sale: Sale,
    reason: str,
    cancelled_at: datetime | None = None,
) -> Sale:
    locked_sale = (
        Sale.objects.select_for_update()
        .select_related("branch")
        .get(pk=sale.pk, business=actor.business)
    )
    _validate_sale_actor(actor, locked_sale.branch)
    if locked_sale.status != SaleStatus.DRAFT:
        raise ValidationError(_("Only a draft sale can be cancelled."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A cancellation reason is required."))
    locked_sale.status = SaleStatus.CANCELLED
    locked_sale.cancelled_by = actor
    locked_sale.cancelled_at = cancelled_at or timezone.now()
    locked_sale.cancellation_reason = clean_reason
    locked_sale.save(
        update_fields=(
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "updated_at",
        )
    )
    return locked_sale


def _claim_posting_key(
    *,
    actor: BusinessMembership,
    key: UUID,
    sale_id: UUID,
) -> SalePostingKey:
    existing = SalePostingKey.objects.filter(business=actor.business, key=key).first()
    if existing is not None:
        if existing.source_id != sale_id:
            raise ValidationError(_("This idempotency key belongs to another sale."))
        return existing
    try:
        with transaction.atomic():
            with _translate_sales_constraint_errors():
                posting_key = SalePostingKey.objects.create(
                    business=actor.business,
                    key=key,
                    source_id=sale_id,
                )
    except (IntegrityError, ValidationError) as error:
        existing = SalePostingKey.objects.filter(business=actor.business, key=key).first()
        if existing is None:
            raise
        if existing.source_id != sale_id:
            raise ValidationError(_("This idempotency key belongs to another sale.")) from error
        return existing
    return posting_key


def _payment_reference(method: str, telebirr_reference: str) -> tuple[str, str]:
    reference = telebirr_reference.strip()
    if method == SalePaymentMethod.CASH:
        if reference:
            raise ValidationError(_("Cash payments cannot include a Telebirr reference."))
        return "", ""
    if method != SalePaymentMethod.TELEBIRR:
        raise ValidationError(_("Select cash or Telebirr as the payment method."))
    normalized = normalize_telebirr_reference(reference)
    if not normalized:
        raise ValidationError(_("Enter the Telebirr transaction reference."))
    return reference, normalized


def _create_payment(
    *,
    actor: BusinessMembership,
    sale: Sale,
    method: str,
    reference: str,
    normalized_reference: str,
    posted_at: datetime,
) -> SalePayment:
    if (
        method == SalePaymentMethod.TELEBIRR
        and SalePayment.objects.filter(
            business=actor.business,
            method=SalePaymentMethod.TELEBIRR,
            telebirr_reference_normalized=normalized_reference,
        ).exists()
    ):
        raise ValidationError(_("This Telebirr transaction reference has already been used."))
    try:
        with transaction.atomic():
            payment = SalePayment.objects.create(
                business=actor.business,
                branch=sale.branch,
                sale=sale,
                method=method,
                amount=sale.total_amount,
                telebirr_reference=reference,
                telebirr_reference_normalized=normalized_reference,
                received_by=actor,
                posted_at=posted_at,
            )
    except (IntegrityError, ValidationError) as error:
        if (
            method == SalePaymentMethod.TELEBIRR
            and SalePayment.objects.filter(
                business=actor.business,
                method=SalePaymentMethod.TELEBIRR,
                telebirr_reference_normalized=normalized_reference,
            ).exists()
        ):
            raise ValidationError(
                _("This Telebirr transaction reference has already been used.")
            ) from error
        raise
    return payment


@transaction.atomic
def post_sale(
    *,
    actor: BusinessMembership,
    sale: Sale,
    payment_method: str,
    telebirr_reference: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> Sale:
    locked_sale = (
        Sale.objects.select_for_update()
        .select_related("branch")
        .get(pk=sale.pk, business=actor.business)
    )
    _validate_sale_actor(actor, locked_sale.branch)
    if locked_sale.status == SaleStatus.POSTED:
        if locked_sale.posting_key is not None and locked_sale.posting_key.key == idempotency_key:
            return locked_sale
        raise ValidationError(_("This sale has already been posted."))
    if locked_sale.status != SaleStatus.DRAFT:
        raise ValidationError(_("Only a draft sale can be posted."))

    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        sale_id=locked_sale.id,
    )
    reference, normalized_reference = _payment_reference(
        payment_method,
        telebirr_reference,
    )
    lines = list(
        locked_sale.lines.select_for_update()
        .select_related("variant", "variant__product")
        .order_by("variant_id")
    )
    if not lines:
        raise ValidationError(_("A sale requires at least one line."))
    total = Decimal("0.00")
    for line in lines:
        if (
            not line.variant.is_active
            or not line.variant.product.is_active
            or line.variant.business_id != actor.business_id
        ):
            raise ValidationError(_("One or more product variants are unavailable."))
        if line.variant.stock_unit != line.unit_snapshot:
            raise ValidationError(_("The variant stock unit changed after the sale draft."))
        validate_stock_quantity(line.quantity, line.unit_snapshot)
        if line.variant.selling_price != line.selling_unit_price:
            raise ValidationError(
                _("A catalog selling price changed. Refresh the draft before posting.")
            )
        total += line.line_total
    if _money(total) != locked_sale.total_amount:
        raise ValidationError(_("Sale line totals do not match the sale total."))

    timestamp = posted_at or timezone.now()
    cash_session = (
        lock_open_cash_session(actor=actor, branch=locked_sale.branch)
        if payment_method == SalePaymentMethod.CASH
        else None
    )
    movements = record_sale_inventory(
        actor=actor,
        business=actor.business,
        branch=locked_sale.branch,
        items=[
            SaleInventoryItem(
                variant=line.variant,
                quantity=line.quantity,
                unit_snapshot=line.unit_snapshot,
                source_id=line.id,
            )
            for line in lines
        ],
        posted_at=timestamp,
    )
    movement_by_line = {movement.source_id: movement for movement in movements}
    for line in lines:
        movement = movement_by_line[line.id]
        line.assigned_inventory_unit_cost = movement.unit_cost
        line.inventory_value_delta = movement.value_delta
        line.save(
            update_fields=(
                "assigned_inventory_unit_cost",
                "inventory_value_delta",
            )
        )

    payment = _create_payment(
        actor=actor,
        sale=locked_sale,
        method=payment_method,
        reference=reference,
        normalized_reference=normalized_reference,
        posted_at=timestamp,
    )
    if cash_session is not None:
        record_cash_sale(
            actor=actor,
            session=cash_session,
            amount=payment.amount,
            source_id=payment.id,
            posted_at=timestamp,
        )
    with _translate_sales_constraint_errors():
        receipt = InternalReceipt.objects.create(
            business=actor.business,
            branch=locked_sale.branch,
            sale=locked_sale,
            internal_number=new_internal_receipt_number(),
            total_amount=locked_sale.total_amount,
            payment_method=payment.method,
            telebirr_reference=payment.telebirr_reference,
            issued_by=actor,
            issued_at=timestamp,
        )
    get_or_create_sale_receipt_identity(receipt)
    locked_sale.status = SaleStatus.POSTED
    locked_sale.payment_status = SalePaymentStatus.PAID
    locked_sale.posting_key = posting_key
    locked_sale.posted_by = actor
    locked_sale.posted_at = timestamp
    with _translate_sales_constraint_errors():
        locked_sale.save(
            update_fields=(
                "status",
                "payment_status",
                "posting_key",
                "posted_by",
                "posted_at",
                "updated_at",
            )
        )
    return locked_sale


def sale_line_return_progress(sale: Sale) -> list[SaleLineReturnProgress]:
    returned = {
        row["sale_line_id"]: row["quantity"] or Decimal("0.000")
        for row in SaleReturnLine.objects.filter(
            sale_line__sale=sale,
            sale_return__status=SaleReturnStatus.POSTED,
        )
        .values("sale_line_id")
        .annotate(quantity=Sum("returned_quantity"))
    }
    return [
        SaleLineReturnProgress(
            line=line,
            returned_quantity=returned.get(line.id, Decimal("0.000")),
            remaining_quantity=line.quantity - returned.get(line.id, Decimal("0.000")),
        )
        for line in sale.lines.select_related("variant").order_by("created_at")
    ]


def _validate_return_preparer(
    actor: BusinessMembership,
    sale: Sale,
    purpose: str,
) -> None:
    ensure_sale_branch_access(actor, sale.branch)
    if purpose not in SaleReturnPurpose.values:
        raise ValidationError(_("Select a valid sale correction purpose."))
    if purpose == SaleReturnPurpose.SALE_REVERSAL and not actor.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required for a full reversal."))


def _validate_return_manager(actor: BusinessMembership, sale: Sale) -> None:
    ensure_sale_branch_access(actor, sale.branch)
    if not actor.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required."))


def _selected_return_lines(
    *,
    sale: Sale,
    quantities: list[ReturnQuantity],
) -> list[tuple[SaleLine, Decimal]]:
    if not quantities:
        raise ValidationError(_("Enter at least one return quantity."))
    seen_lines: set[UUID] = set()
    quantity_by_line: dict[UUID, Decimal] = {}
    for item in quantities:
        if item.sale_line_id in seen_lines:
            raise ValidationError(_("A sale return cannot repeat the same sale line."))
        seen_lines.add(item.sale_line_id)
        quantity = _quantity(item.quantity)
        if quantity <= 0:
            raise ValidationError(_("Return quantity must be greater than zero."))
        quantity_by_line[item.sale_line_id] = quantity
    lines = list(
        SaleLine.objects.filter(
            id__in=quantity_by_line,
            business=sale.business,
            sale=sale,
        )
        .select_related("variant")
        .order_by("id")
    )
    if len(lines) != len(quantity_by_line):
        raise ValidationError(_("Sale line does not belong to the original sale."))
    for line in lines:
        validate_stock_quantity(quantity_by_line[line.id], line.unit_snapshot)
    return [(line, quantity_by_line[line.id]) for line in lines]


def _validate_return_quantities(
    *,
    sale: Sale,
    selected: list[tuple[SaleLine, Decimal]],
    purpose: str,
    excluded_return: SaleReturn | None = None,
) -> None:
    selected_line_ids = [line.id for line, _quantity_value in selected]
    returned_query = SaleReturnLine.objects.filter(
        sale_line_id__in=selected_line_ids,
        sale_return__status=SaleReturnStatus.POSTED,
    )
    if excluded_return is not None:
        returned_query = returned_query.exclude(sale_return=excluded_return)
    already_returned = {
        row["sale_line_id"]: row["quantity"] or Decimal("0.000")
        for row in returned_query.values("sale_line_id").annotate(quantity=Sum("returned_quantity"))
    }
    if purpose == SaleReturnPurpose.SALE_REVERSAL:
        existing_returns = SaleReturn.objects.filter(
            sale=sale,
            status=SaleReturnStatus.POSTED,
        )
        if excluded_return is not None:
            existing_returns = existing_returns.exclude(pk=excluded_return.pk)
        if existing_returns.exists():
            raise ValidationError(
                _("A full sale reversal is unavailable after a posted customer return.")
            )
    for line, quantity in selected:
        remaining = line.quantity - already_returned.get(line.id, Decimal("0.000"))
        if quantity > remaining:
            raise ValidationError(_("Return quantity cannot exceed the remaining sold quantity."))
    if purpose != SaleReturnPurpose.SALE_REVERSAL:
        return
    original_lines = list(sale.lines.order_by("id"))
    selected_by_id = {line.id: quantity for line, quantity in selected}
    if len(selected_by_id) != len(original_lines) or any(
        selected_by_id.get(line.id) != line.quantity for line in original_lines
    ):
        raise ValidationError(
            _("A full sale reversal must include every sale line for its full quantity.")
        )


@transaction.atomic
def save_sale_return_draft(
    *,
    actor: BusinessMembership,
    sale: Sale,
    purpose: str,
    return_date: date,
    reason: str,
    quantities: list[ReturnQuantity],
    sale_return: SaleReturn | None = None,
) -> SaleReturn:
    locked_sale = (
        Sale.objects.select_for_update()
        .select_related("branch")
        .get(pk=sale.pk, business=actor.business)
    )
    if locked_sale.status != SaleStatus.POSTED:
        raise ValidationError(_("Only a posted sale can be returned."))
    _validate_return_preparer(actor, locked_sale, purpose)
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A return reason is required."))
    selected = _selected_return_lines(sale=locked_sale, quantities=quantities)
    _validate_return_quantities(
        sale=locked_sale,
        selected=selected,
        purpose=purpose,
        excluded_return=sale_return,
    )
    if sale_return is None:
        draft = SaleReturn(
            business=actor.business,
            branch=locked_sale.branch,
            sale=locked_sale,
            internal_number=new_sale_return_number(),
            purpose=purpose,
            return_date=return_date,
            reason=clean_reason,
            created_by=actor,
        )
        with _translate_sales_constraint_errors():
            draft.save()
    else:
        draft = SaleReturn.objects.select_for_update().get(
            pk=sale_return.pk,
            business=actor.business,
            sale=locked_sale,
        )
        if draft.status != SaleReturnStatus.DRAFT:
            raise ValidationError(_("Only a draft sale return can be edited."))
        if draft.purpose != purpose:
            raise ValidationError(_("A sale correction purpose cannot be changed."))
        draft.return_date = return_date
        draft.reason = clean_reason
    for existing_line in draft.lines.all():
        existing_line.delete()
    total = Decimal("0.00")
    for sale_line, quantity in selected:
        with _translate_sales_constraint_errors():
            line = SaleReturnLine.objects.create(
                business=actor.business,
                sale_return=draft,
                sale_line=sale_line,
                variant=sale_line.variant,
                returned_quantity=quantity,
                product_name_snapshot=sale_line.product_name_snapshot,
                sku_snapshot=sale_line.sku_snapshot,
                unit_snapshot=sale_line.unit_snapshot,
                original_selling_unit_price=sale_line.selling_unit_price,
                refund_line_total=_money(quantity * sale_line.selling_unit_price),
                original_assigned_inventory_unit_cost=sale_line.assigned_inventory_unit_cost,
            )
        total += line.refund_line_total
    draft.total_refund_amount = _money(total)
    with _translate_sales_constraint_errors():
        draft.save(update_fields=("return_date", "reason", "total_refund_amount", "updated_at"))
    return draft


@transaction.atomic
def cancel_sale_return(
    *,
    actor: BusinessMembership,
    sale_return: SaleReturn,
    cancelled_at: datetime | None = None,
) -> SaleReturn:
    locked = (
        SaleReturn.objects.select_for_update()
        .select_related("sale", "branch")
        .get(pk=sale_return.pk, business=actor.business)
    )
    _validate_return_preparer(actor, locked.sale, locked.purpose)
    if locked.status != SaleReturnStatus.DRAFT:
        raise ValidationError(_("Only a draft sale return can be cancelled."))
    locked.status = SaleReturnStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = cancelled_at or timezone.now()
    with _translate_sales_constraint_errors():
        locked.save(update_fields=("status", "cancelled_by", "cancelled_at", "updated_at"))
    return locked


def _claim_return_posting_key(
    *,
    actor: BusinessMembership,
    key: UUID,
    operation_type: str,
    source_id: UUID,
) -> SaleReturnPostingKey:
    existing = SaleReturnPostingKey.objects.filter(
        business=actor.business,
        key=key,
    ).first()
    if existing is not None:
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another sale return operation.")
            )
        return existing
    try:
        with transaction.atomic(), _translate_sales_constraint_errors():
            posting_key = SaleReturnPostingKey.objects.create(
                business=actor.business,
                key=key,
                operation_type=operation_type,
                source_id=source_id,
            )
    except (IntegrityError, ValidationError) as error:
        existing = SaleReturnPostingKey.objects.filter(
            business=actor.business,
            key=key,
        ).first()
        if existing is None:
            raise
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another sale return operation.")
            ) from error
        return existing
    return posting_key


def _refund_reference(method: str, telebirr_reference: str) -> tuple[str, str]:
    reference = telebirr_reference.strip()
    if method == SalePaymentMethod.CASH:
        if reference:
            raise ValidationError(_("Cash refunds cannot include a Telebirr reference."))
        return "", ""
    if method != SalePaymentMethod.TELEBIRR:
        raise ValidationError(_("Select cash or Telebirr as the refund method."))
    normalized = normalize_telebirr_reference(reference)
    if not normalized:
        raise ValidationError(_("Enter the Telebirr refund transaction reference."))
    return reference, normalized


def _create_refund_evidence(
    *,
    actor: BusinessMembership,
    sale_return: SaleReturn,
    method: str,
    reference: str,
    normalized_reference: str,
    posted_at: datetime,
) -> SaleRefundEvidence:
    if (
        method == SalePaymentMethod.TELEBIRR
        and SaleRefundEvidence.objects.filter(
            business=actor.business,
            method=SalePaymentMethod.TELEBIRR,
            telebirr_reference_normalized=normalized_reference,
        ).exists()
    ):
        raise ValidationError(_("This Telebirr refund reference has already been used."))
    try:
        with transaction.atomic(), _translate_sales_constraint_errors():
            return SaleRefundEvidence.objects.create(
                business=actor.business,
                branch=sale_return.branch,
                sale_return=sale_return,
                method=method,
                amount=sale_return.total_refund_amount,
                telebirr_reference=reference,
                telebirr_reference_normalized=normalized_reference,
                refunded_by=actor,
                posted_at=posted_at,
            )
    except (IntegrityError, ValidationError) as error:
        if (
            method == SalePaymentMethod.TELEBIRR
            and SaleRefundEvidence.objects.filter(
                business=actor.business,
                method=SalePaymentMethod.TELEBIRR,
                telebirr_reference_normalized=normalized_reference,
            ).exists()
        ):
            raise ValidationError(
                _("This Telebirr refund reference has already been used.")
            ) from error
        raise


@transaction.atomic
def post_sale_return(
    *,
    actor: BusinessMembership,
    sale_return: SaleReturn,
    refund_method: str,
    telebirr_reference: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> SaleReturn:
    locked = (
        SaleReturn.objects.select_for_update()
        .select_related("sale", "branch")
        .get(pk=sale_return.pk, business=actor.business)
    )
    _validate_return_manager(actor, locked.sale)
    if locked.status == SaleReturnStatus.POSTED:
        if locked.posting_key is not None and locked.posting_key.key == idempotency_key:
            return locked
        raise ValidationError(_("This sale return has already been posted."))
    if locked.status != SaleReturnStatus.DRAFT:
        raise ValidationError(_("Only a draft sale return can be posted."))
    reference, normalized_reference = _refund_reference(
        refund_method,
        telebirr_reference,
    )
    lines = list(
        locked.lines.select_for_update()
        .select_related("sale_line", "variant")
        .order_by("sale_line_id")
    )
    if not lines:
        raise ValidationError(_("Add at least one sale return line before posting."))
    sale_line_ids = [line.sale_line_id for line in lines]
    locked_sale_lines = {
        line.id: line
        for line in SaleLine.objects.select_for_update()
        .filter(
            id__in=sale_line_ids,
            business=actor.business,
            sale=locked.sale,
        )
        .order_by("id")
    }
    if len(locked_sale_lines) != len(lines):
        raise ValidationError(_("One or more return lines no longer belong to the sale."))
    selected = [(locked_sale_lines[line.sale_line_id], line.returned_quantity) for line in lines]
    _validate_return_quantities(
        sale=locked.sale,
        selected=selected,
        purpose=locked.purpose,
        excluded_return=locked,
    )
    total = sum((line.refund_line_total for line in lines), Decimal("0.00"))
    if _money(total) != locked.total_refund_amount:
        raise ValidationError(_("Return line totals do not match the refund total."))
    posting_key = _claim_return_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=SaleReturnOperationType.RETURN,
        source_id=locked.id,
    )
    timestamp = posted_at or timezone.now()
    cash_session = (
        lock_open_cash_session(actor=actor, branch=locked.branch)
        if refund_method == SalePaymentMethod.CASH
        else None
    )
    movements = record_sale_return_inventory(
        actor=actor,
        business=actor.business,
        branch=locked.branch,
        items=[
            SaleReturnInventoryItem(
                variant=line.variant,
                quantity=line.returned_quantity,
                unit_cost=line.original_assigned_inventory_unit_cost,
                unit_snapshot=line.unit_snapshot,
                source_id=line.id,
            )
            for line in lines
        ],
        reason=locked.reason,
        posted_at=timestamp,
    )
    movement_by_source = {movement.source_id: movement for movement in movements}
    for line in lines:
        line.inventory_value_delta = movement_by_source[line.id].value_delta
        with _translate_sales_constraint_errors():
            line.save(update_fields=("inventory_value_delta",))
    refund = _create_refund_evidence(
        actor=actor,
        sale_return=locked,
        method=refund_method,
        reference=reference,
        normalized_reference=normalized_reference,
        posted_at=timestamp,
    )
    if cash_session is not None:
        record_cash_refund(
            actor=actor,
            session=cash_session,
            amount=refund.amount,
            source_id=refund.id,
            posted_at=timestamp,
        )
    with _translate_sales_constraint_errors():
        return_receipt = InternalReturnReceipt.objects.create(
            business=actor.business,
            branch=locked.branch,
            sale_return=locked,
            original_receipt=locked.sale.receipt,
            internal_number=new_internal_return_receipt_number(),
            total_amount=locked.total_refund_amount,
            refund_method=refund.method,
            telebirr_reference=refund.telebirr_reference,
            issued_by=actor,
            issued_at=timestamp,
        )
    get_or_create_return_receipt_identity(return_receipt)
    locked.status = SaleReturnStatus.POSTED
    locked.posting_key = posting_key
    locked.posted_by = actor
    locked.posted_at = timestamp
    with _translate_sales_constraint_errors():
        locked.save(update_fields=("status", "posting_key", "posted_by", "posted_at", "updated_at"))
    return locked


@transaction.atomic
def reverse_sale_return(
    *,
    actor: BusinessMembership,
    sale_return: SaleReturn,
    reason: str,
    idempotency_key: UUID,
    reversed_at: datetime | None = None,
) -> SaleReturnReversal:
    locked = (
        SaleReturn.objects.select_for_update()
        .select_related("sale", "branch")
        .get(pk=sale_return.pk, business=actor.business)
    )
    _validate_return_manager(actor, locked.sale)
    existing_key = SaleReturnPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != SaleReturnOperationType.REVERSAL
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another sale return operation.")
            )
        existing_reversal = SaleReturnReversal.objects.filter(
            sale_return=locked,
            posting_key=existing_key,
        ).first()
        if existing_reversal is not None:
            return existing_reversal
        raise ValidationError(_("The sale return reversal is incomplete."))
    if locked.status != SaleReturnStatus.POSTED:
        raise ValidationError(_("Only a posted sale return can be reversed."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A reversal reason is required."))
    lines = list(locked.lines.select_related("variant").order_by("id"))
    posting_key = _claim_return_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=SaleReturnOperationType.REVERSAL,
        source_id=locked.id,
    )
    timestamp = reversed_at or timezone.now()
    record_sale_return_reversal_inventory(
        actor=actor,
        business=actor.business,
        branch=locked.branch,
        items=[
            SaleReturnReversalInventoryItem(
                variant=line.variant,
                quantity=line.returned_quantity,
                unit_cost=line.original_assigned_inventory_unit_cost,
                unit_snapshot=line.unit_snapshot,
                source_id=line.id,
            )
            for line in lines
        ],
        reason=clean_reason,
        posted_at=timestamp,
    )
    with _translate_sales_constraint_errors():
        reversal = SaleReturnReversal.objects.create(
            business=actor.business,
            branch=locked.branch,
            sale_return=locked,
            posting_key=posting_key,
            reason=clean_reason,
            refund_method=locked.refund.method,
            refund_amount=locked.refund.amount,
            telebirr_reference=locked.refund.telebirr_reference,
            reversed_by=actor,
            posted_at=timestamp,
        )
    locked.status = SaleReturnStatus.REVERSED
    with _translate_sales_constraint_errors():
        locked.save(update_fields=("status", "updated_at"))
    return reversal
