import uuid
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant, StockUnit, validate_stock_quantity

MONEY_QUANTUM = Decimal("0.01")


def calculate_sale_line_total(quantity: Decimal, selling_unit_price: Decimal) -> Decimal:
    return (quantity * selling_unit_price).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


class SaleStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")
    CANCELLED = "cancelled", _("Cancelled")


class SalePaymentStatus(models.TextChoices):
    UNPAID = "unpaid", _("Unpaid")
    PAID = "paid", _("Paid")


class SalePaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    TELEBIRR = "telebirr", _("Telebirr")


class SalePostingKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_posting_keys",
    )
    key = models.UUIDField()
    source_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="sales_unique_posting_key_per_business",
            )
        ]

    def __str__(self) -> str:
        return str(self.key)

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Sale posting keys cannot be modified."))
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
        raise ValidationError(_("Sale posting keys cannot be deleted."))


class Sale(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="sales")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="sales")
    internal_number = models.CharField(max_length=40)
    sale_date = models.DateField(default=timezone.localdate)
    status = models.CharField(
        max_length=16,
        choices=SaleStatus.choices,
        default=SaleStatus.DRAFT,
    )
    payment_status = models.CharField(
        max_length=16,
        choices=SalePaymentStatus.choices,
        default=SalePaymentStatus.UNPAID,
    )
    total_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sales_created",
    )
    posting_key = models.OneToOneField(
        SalePostingKey,
        on_delete=models.PROTECT,
        related_name="posted_sale",
        null=True,
        blank=True,
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sales_posted",
        null=True,
        blank=True,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sales_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-sale_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="sales_unique_number_per_business",
            ),
            models.CheckConstraint(
                condition=Q(status__in=SaleStatus.values),
                name="sales_status_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(payment_status__in=SalePaymentStatus.values),
                name="sales_payment_status_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gte=Decimal("0.00")),
                name="sales_total_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(status__in=(SaleStatus.DRAFT, SaleStatus.CANCELLED))
                | Q(status=SaleStatus.POSTED, total_amount__gt=Decimal("0.00")),
                name="sales_posted_total_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=SaleStatus.POSTED,
                        posting_key__isnull=False,
                        posted_by__isnull=False,
                        posted_at__isnull=False,
                        payment_status=SalePaymentStatus.PAID,
                    )
                    | Q(
                        status__in=(SaleStatus.DRAFT, SaleStatus.CANCELLED),
                        posting_key__isnull=True,
                        posted_by__isnull=True,
                        posted_at__isnull=True,
                        payment_status=SalePaymentStatus.UNPAID,
                    )
                ),
                name="sales_posting_fields_match",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        status=SaleStatus.CANCELLED,
                        cancelled_by__isnull=False,
                        cancelled_at__isnull=False,
                    )
                    & ~Q(cancellation_reason="")
                )
                | Q(
                    status__in=(SaleStatus.DRAFT, SaleStatus.POSTED),
                    cancelled_by__isnull=True,
                    cancelled_at__isnull=True,
                    cancellation_reason="",
                ),
                name="sales_cancellation_fields_match",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_number

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = Sale.objects.get(pk=self.pk)
            if stored.status != SaleStatus.DRAFT:
                raise ValidationError(_("Posted or cancelled sales cannot be modified."))
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
        raise ValidationError(_("Sales cannot be deleted. Cancel an unposted draft instead."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.created_by_id and self.created_by.business_id != self.business_id:
            errors["created_by"] = ValidationError(_("Creator must belong to this business."))
        if (
            self.posted_by_id
            and self.posted_by is not None
            and self.posted_by.business_id != self.business_id
        ):
            errors["posted_by"] = ValidationError(_("Poster must belong to this business."))
        if (
            self.cancelled_by_id
            and self.cancelled_by is not None
            and self.cancelled_by.business_id != self.business_id
        ):
            errors["cancelled_by"] = ValidationError(_("Canceller must belong to this business."))
        if (
            self.posting_key_id
            and self.posting_key is not None
            and self.posting_key.business_id != self.business_id
        ):
            errors["posting_key"] = ValidationError(_("Posting key must belong to this business."))
        if not self._state.adding:
            line_total = self.lines.aggregate(total=models.Sum("line_total"))["total"] or Decimal(
                "0.00"
            )
            if self.total_amount != line_total:
                errors["total_amount"] = ValidationError(
                    _("Sale total must equal the sum of its line totals.")
                )
        if errors:
            raise ValidationError(errors)


class SaleLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="sale_lines")
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="sale_lines",
    )
    quantity = models.DecimalField(max_digits=18, decimal_places=3)
    product_name_snapshot = models.CharField(max_length=180)
    sku_snapshot = models.CharField(max_length=80)
    unit_snapshot = models.CharField(max_length=16, choices=StockUnit.choices)
    selling_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=18, decimal_places=2)
    assigned_inventory_unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0.000000"),
    )
    inventory_value_delta = models.DecimalField(
        max_digits=24,
        decimal_places=6,
        default=Decimal("0.000000"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("sale", "variant"),
                name="sales_unique_variant_per_sale",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gt=Decimal("0.000")),
                name="sales_line_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(selling_unit_price__gt=Decimal("0.00")),
                name="sales_line_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(line_total__gt=Decimal("0.00")),
                name="sales_line_total_positive",
            ),
            models.CheckConstraint(
                condition=Q(assigned_inventory_unit_cost__gte=Decimal("0.000000")),
                name="sales_line_inventory_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(inventory_value_delta__lte=Decimal("0.000000")),
                name="sales_line_inventory_value_nonpositive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale} — {self.sku_snapshot}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        sale_status = Sale.objects.filter(pk=self.sale_id).values_list("status", flat=True).first()
        if sale_status != SaleStatus.DRAFT:
            raise ValidationError(_("Posted or cancelled sale lines cannot be modified."))
        self.line_total = calculate_sale_line_total(self.quantity, self.selling_unit_price)
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
        sale_status = Sale.objects.filter(pk=self.sale_id).values_list("status", flat=True).first()
        if sale_status != SaleStatus.DRAFT:
            raise ValidationError(_("Posted or cancelled sale lines cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.sale_id and self.sale.business_id != self.business_id:
            errors["sale"] = ValidationError(_("Sale must belong to this business."))
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if (
            self.quantity is not None
            and self.selling_unit_price is not None
            and self.line_total is not None
            and self.line_total != calculate_sale_line_total(self.quantity, self.selling_unit_price)
        ):
            errors["line_total"] = ValidationError(
                _("Line total must equal quantity multiplied by selling unit price.")
            )
        if self.variant_id and self.unit_snapshot:
            try:
                validate_stock_quantity(self.quantity, self.unit_snapshot)
            except ValidationError as error:
                errors["quantity"] = error
            if self.variant.stock_unit != self.unit_snapshot:
                errors["unit_snapshot"] = ValidationError(
                    _("Stock unit must match the product variant.")
                )
        if errors:
            raise ValidationError(errors)


class SalePayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_payments",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="sale_payments",
    )
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="payment")
    method = models.CharField(max_length=16, choices=SalePaymentMethod.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference_normalized = models.CharField(max_length=120, blank=True)
    received_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_payments_received",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(method__in=SalePaymentMethod.values),
                name="sales_payment_method_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="sales_payment_amount_positive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        method=SalePaymentMethod.CASH,
                        telebirr_reference="",
                        telebirr_reference_normalized="",
                    )
                    | (
                        Q(method=SalePaymentMethod.TELEBIRR)
                        & ~Q(telebirr_reference="")
                        & ~Q(telebirr_reference_normalized="")
                    )
                ),
                name="sales_payment_reference_matches_method",
            ),
            models.UniqueConstraint(
                fields=("business", "telebirr_reference_normalized"),
                condition=Q(method=SalePaymentMethod.TELEBIRR),
                name="sales_unique_telebirr_reference_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale} — {self.get_method_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Posted sale payments cannot be modified."))
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
        raise ValidationError(_("Posted sale payments cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_id and self.sale.business_id != self.business_id:
            errors["sale"] = ValidationError(_("Sale must belong to this business."))
        if self.sale_id and self.sale.branch_id != self.branch_id:
            errors["sale"] = ValidationError(_("Sale must belong to this branch."))
        if self.received_by_id and self.received_by.business_id != self.business_id:
            errors["received_by"] = ValidationError(_("Receiver must belong to this business."))
        if self.sale_id and self.amount != self.sale.total_amount:
            errors["amount"] = ValidationError(_("Payment must equal the full sale total."))
        if errors:
            raise ValidationError(errors)


class InternalReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="internal_receipts",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="internal_receipts",
    )
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="receipt")
    internal_number = models.CharField(max_length=40)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    payment_method = models.CharField(max_length=16, choices=SalePaymentMethod.choices)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    issued_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="internal_receipts_issued",
    )
    issued_at = models.DateTimeField()

    class Meta:
        ordering = ("-issued_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="sales_unique_receipt_number_per_business",
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gt=Decimal("0.00")),
                name="sales_receipt_total_positive",
            ),
            models.CheckConstraint(
                condition=Q(payment_method__in=SalePaymentMethod.values),
                name="sales_receipt_payment_method_is_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.internal_number

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Internal receipts cannot be modified."))
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
        raise ValidationError(_("Internal receipts cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_id and self.sale.business_id != self.business_id:
            errors["sale"] = ValidationError(_("Sale must belong to this business."))
        if self.sale_id and self.sale.branch_id != self.branch_id:
            errors["sale"] = ValidationError(_("Sale must belong to this branch."))
        if self.issued_by_id and self.issued_by.business_id != self.business_id:
            errors["issued_by"] = ValidationError(_("Issuer must belong to this business."))
        if self.sale_id and self.total_amount != self.sale.total_amount:
            errors["total_amount"] = ValidationError(_("Receipt total must equal the sale total."))
        if self.sale_id:
            try:
                payment = self.sale.payment
            except SalePayment.DoesNotExist:
                errors["sale"] = ValidationError(_("Receipt requires a posted sale payment."))
            else:
                if self.payment_method != payment.method:
                    errors["payment_method"] = ValidationError(
                        _("Receipt payment method must match the sale payment.")
                    )
                if self.telebirr_reference != payment.telebirr_reference:
                    errors["telebirr_reference"] = ValidationError(
                        _("Receipt reference must match the sale payment.")
                    )
        if errors:
            raise ValidationError(errors)
