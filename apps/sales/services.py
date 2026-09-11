import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, BusinessMembership
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.inventory.services import (
    SaleInventoryItem,
    ensure_sale_branch_access,
    record_sale_inventory,
)
from apps.sales.models import (
    InternalReceipt,
    Sale,
    SaleLine,
    SalePayment,
    SalePaymentMethod,
    SalePaymentStatus,
    SalePostingKey,
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
    }
)


@dataclass(frozen=True)
class SaleQuantity:
    variant_id: UUID
    quantity: Decimal


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
    with _translate_sales_constraint_errors():
        InternalReceipt.objects.create(
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
