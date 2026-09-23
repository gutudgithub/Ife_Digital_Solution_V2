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
from apps.catalog.storage import catalog_media_storage, telebirr_qr_upload_path

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


class TelebirrProfileAction(models.TextChoices):
    CREATED = "created", _("Created")
    REPLACED = "replaced", _("Replaced")
    ACTIVATED = "activated", _("Activated")
    DEACTIVATED = "deactivated", _("Deactivated")
    REMOVED = "removed", _("Removed")


class SaleReturnPurpose(models.TextChoices):
    SALE_REVERSAL = "sale_reversal", _("Full sale reversal")
    CUSTOMER_RETURN = "customer_return", _("Customer return")


class SaleReturnStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")
    REVERSED = "reversed", _("Reversed")
    CANCELLED = "cancelled", _("Cancelled")


class SaleReturnOperationType(models.TextChoices):
    RETURN = "return", _("Return posting")
    REVERSAL = "reversal", _("Return reversal")


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
        if self.variant_id and self.unit_snapshot and self.quantity is not None:
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


class SaleReturnPostingKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_return_posting_keys",
    )
    key = models.UUIDField()
    operation_type = models.CharField(
        max_length=16,
        choices=SaleReturnOperationType.choices,
    )
    source_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="sales_unique_return_key_per_business",
            ),
            models.CheckConstraint(
                condition=Q(operation_type__in=SaleReturnOperationType.values),
                name="sales_return_operation_type_is_valid",
            ),
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
            raise ValidationError(_("Sale return posting keys cannot be modified."))
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
        raise ValidationError(_("Sale return posting keys cannot be deleted."))


class SaleReturn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_returns",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="sale_returns",
    )
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    internal_number = models.CharField(max_length=40)
    purpose = models.CharField(max_length=20, choices=SaleReturnPurpose.choices)
    status = models.CharField(
        max_length=16,
        choices=SaleReturnStatus.choices,
        default=SaleReturnStatus.DRAFT,
    )
    return_date = models.DateField(default=timezone.localdate)
    reason = models.TextField()
    total_refund_amount = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    created_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_returns_created",
    )
    posting_key = models.OneToOneField(
        SaleReturnPostingKey,
        on_delete=models.PROTECT,
        related_name="posted_return",
        null=True,
        blank=True,
    )
    posted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_returns_posted",
        null=True,
        blank=True,
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_returns_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-return_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="sales_unique_return_number_per_business",
            ),
            models.CheckConstraint(
                condition=Q(purpose__in=SaleReturnPurpose.values),
                name="sales_return_purpose_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=SaleReturnStatus.values),
                name="sales_return_status_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(total_refund_amount__gte=Decimal("0.00")),
                name="sales_return_total_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    posting_key__isnull=True,
                    posted_by__isnull=True,
                    posted_at__isnull=True,
                )
                | Q(
                    posting_key__isnull=False,
                    posted_by__isnull=False,
                    posted_at__isnull=False,
                ),
                name="sales_return_posting_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(cancelled_by__isnull=True, cancelled_at__isnull=True)
                | Q(cancelled_by__isnull=False, cancelled_at__isnull=False),
                name="sales_return_cancellation_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(
                    status=SaleReturnStatus.DRAFT,
                    posting_key__isnull=True,
                    cancelled_by__isnull=True,
                )
                | Q(
                    status=SaleReturnStatus.CANCELLED,
                    posting_key__isnull=True,
                    cancelled_by__isnull=False,
                )
                | Q(
                    status__in=(SaleReturnStatus.POSTED, SaleReturnStatus.REVERSED),
                    posting_key__isnull=False,
                    cancelled_by__isnull=True,
                    total_refund_amount__gt=Decimal("0.00"),
                ),
                name="sales_return_status_has_audit",
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
            stored = SaleReturn.objects.get(pk=self.pk)
            if stored.status != SaleReturnStatus.DRAFT:
                immutable_values = (
                    self.business_id,
                    self.branch_id,
                    self.sale_id,
                    self.internal_number,
                    self.purpose,
                    self.return_date,
                    self.reason,
                    self.total_refund_amount,
                    self.created_by_id,
                    self.posting_key_id,
                    self.posted_by_id,
                    self.posted_at,
                    self.cancelled_by_id,
                    self.cancelled_at,
                )
                stored_values = (
                    stored.business_id,
                    stored.branch_id,
                    stored.sale_id,
                    stored.internal_number,
                    stored.purpose,
                    stored.return_date,
                    stored.reason,
                    stored.total_refund_amount,
                    stored.created_by_id,
                    stored.posting_key_id,
                    stored.posted_by_id,
                    stored.posted_at,
                    stored.cancelled_by_id,
                    stored.cancelled_at,
                )
                if immutable_values != stored_values:
                    raise ValidationError(_("Posted sale returns cannot be modified."))
                if not (
                    stored.status == SaleReturnStatus.POSTED
                    and self.status == SaleReturnStatus.REVERSED
                ):
                    raise ValidationError(_("Posted sale returns cannot be modified."))
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
        stored_status = (
            SaleReturn.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        )
        if stored_status != SaleReturnStatus.DRAFT:
            raise ValidationError(_("Posted sale returns cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_id:
            if self.sale.business_id != self.business_id:
                errors["sale"] = ValidationError(_("Sale must belong to this business."))
            elif self.sale.branch_id != self.branch_id:
                errors["sale"] = ValidationError(_("Sale return must use the original branch."))
            elif self.sale.status != SaleStatus.POSTED:
                errors["sale"] = ValidationError(_("Only a posted sale can be returned."))
        for field_name, actor_label, membership in (
            ("created_by", _("Creator"), self.created_by),
            ("posted_by", _("Posting actor"), self.posted_by),
            ("cancelled_by", _("Cancelling actor"), self.cancelled_by),
        ):
            if membership is not None and membership.business_id != self.business_id:
                errors[field_name] = ValidationError(
                    _("%(actor)s must belong to this business.") % {"actor": actor_label}
                )
        posting_key = self.posting_key
        if posting_key is not None:
            if posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif (
                posting_key.operation_type != SaleReturnOperationType.RETURN
                or posting_key.source_id != self.id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this sale return.")
                )
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A return reason is required."))
        if not self._state.adding:
            line_total = self.lines.aggregate(total=models.Sum("refund_line_total"))[
                "total"
            ] or Decimal("0.00")
            if self.total_refund_amount != line_total:
                errors["total_refund_amount"] = ValidationError(
                    _("Refund total must equal the sum of its return lines.")
                )
        if errors:
            raise ValidationError(errors)

    @property
    def inventory_value_restoration(self) -> Decimal:
        return sum((line.inventory_value_delta for line in self.lines.all()), Decimal("0.000000"))


class SaleReturnLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_return_lines",
    )
    sale_return = models.ForeignKey(
        SaleReturn,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    sale_line = models.ForeignKey(
        SaleLine,
        on_delete=models.PROTECT,
        related_name="return_lines",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="sale_return_lines",
    )
    returned_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    product_name_snapshot = models.CharField(max_length=180)
    sku_snapshot = models.CharField(max_length=80)
    unit_snapshot = models.CharField(max_length=16, choices=StockUnit.choices)
    original_selling_unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    refund_line_total = models.DecimalField(max_digits=18, decimal_places=2)
    original_assigned_inventory_unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
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
                fields=("sale_return", "sale_line"),
                name="sales_unique_sale_line_per_return",
            ),
            models.CheckConstraint(
                condition=Q(returned_quantity__gt=Decimal("0.000")),
                name="sales_return_line_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(original_selling_unit_price__gt=Decimal("0.00")),
                name="sales_return_line_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(refund_line_total__gt=Decimal("0.00")),
                name="sales_return_line_total_positive",
            ),
            models.CheckConstraint(
                condition=Q(original_assigned_inventory_unit_cost__gte=Decimal("0.000000")),
                name="sales_return_line_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(inventory_value_delta__gte=Decimal("0.000000")),
                name="sales_return_inventory_value_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale_return} — {self.sku_snapshot}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        return_status = (
            SaleReturn.objects.filter(pk=self.sale_return_id)
            .values_list("status", flat=True)
            .first()
        )
        if return_status != SaleReturnStatus.DRAFT:
            raise ValidationError(_("Posted sale return lines cannot be modified."))
        self.refund_line_total = calculate_sale_line_total(
            self.returned_quantity,
            self.original_selling_unit_price,
        )
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
        return_status = (
            SaleReturn.objects.filter(pk=self.sale_return_id)
            .values_list("status", flat=True)
            .first()
        )
        if return_status != SaleReturnStatus.DRAFT:
            raise ValidationError(_("Posted sale return lines cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.sale_return_id and self.sale_return.business_id != self.business_id:
            errors["sale_return"] = ValidationError(_("Sale return must belong to this business."))
        if self.sale_line_id:
            if self.sale_line.business_id != self.business_id:
                errors["sale_line"] = ValidationError(_("Sale line must belong to this business."))
            elif self.sale_return_id and self.sale_line.sale_id != self.sale_return.sale_id:
                errors["sale_line"] = ValidationError(
                    _("Sale line must belong to the original sale.")
                )
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if self.sale_line_id and self.variant_id and self.sale_line.variant_id != self.variant_id:
            errors["variant"] = ValidationError(_("Variant must match the original sale line."))
        if self.sale_line_id and self.returned_quantity is not None:
            try:
                validate_stock_quantity(self.returned_quantity, self.sale_line.unit_snapshot)
            except ValidationError as error:
                errors["returned_quantity"] = error
        if self.sale_line_id:
            if self.product_name_snapshot != self.sale_line.product_name_snapshot:
                errors["product_name_snapshot"] = ValidationError(
                    _("Product snapshot must match the original sale line.")
                )
            if self.sku_snapshot != self.sale_line.sku_snapshot:
                errors["sku_snapshot"] = ValidationError(
                    _("SKU snapshot must match the original sale line.")
                )
            if self.unit_snapshot != self.sale_line.unit_snapshot:
                errors["unit_snapshot"] = ValidationError(
                    _("Stock unit must match the original sale line.")
                )
            if self.original_selling_unit_price != self.sale_line.selling_unit_price:
                errors["original_selling_unit_price"] = ValidationError(
                    _("Selling price must match the original sale line.")
                )
            if (
                self.original_assigned_inventory_unit_cost
                != self.sale_line.assigned_inventory_unit_cost
            ):
                errors["original_assigned_inventory_unit_cost"] = ValidationError(
                    _("Inventory cost must match the original sale line.")
                )
        if (
            self.returned_quantity is not None
            and self.original_selling_unit_price is not None
            and self.refund_line_total is not None
        ):
            expected_total = calculate_sale_line_total(
                self.returned_quantity,
                self.original_selling_unit_price,
            )
            if self.refund_line_total != expected_total:
                errors["refund_line_total"] = ValidationError(
                    _("Refund line total must equal quantity multiplied by original selling price.")
                )
        if errors:
            raise ValidationError(errors)


class SaleRefundEvidence(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_refund_evidence",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="sale_refund_evidence",
    )
    sale_return = models.OneToOneField(
        SaleReturn,
        on_delete=models.PROTECT,
        related_name="refund",
    )
    method = models.CharField(max_length=16, choices=SalePaymentMethod.choices)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    telebirr_reference_normalized = models.CharField(max_length=120, blank=True)
    refunded_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_refunds_recorded",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(method__in=SalePaymentMethod.values),
                name="sales_refund_method_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="sales_refund_amount_positive",
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
                name="sales_refund_reference_matches_method",
            ),
            models.UniqueConstraint(
                fields=("business", "telebirr_reference_normalized"),
                condition=Q(method=SalePaymentMethod.TELEBIRR),
                name="sales_unique_refund_reference_per_business",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale_return} — {self.get_method_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Posted refund evidence cannot be modified."))
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
        raise ValidationError(_("Posted refund evidence cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_return_id:
            if self.sale_return.business_id != self.business_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this business.")
                )
            elif self.sale_return.branch_id != self.branch_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this branch.")
                )
            if self.amount != self.sale_return.total_refund_amount:
                errors["amount"] = ValidationError(
                    _("Refund evidence must equal the complete return total.")
                )
        if self.refunded_by_id and self.refunded_by.business_id != self.business_id:
            errors["refunded_by"] = ValidationError(
                _("Refunding actor must belong to this business.")
            )
        if errors:
            raise ValidationError(errors)


class InternalReturnReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="internal_return_receipts",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="internal_return_receipts",
    )
    sale_return = models.OneToOneField(
        SaleReturn,
        on_delete=models.PROTECT,
        related_name="receipt",
    )
    original_receipt = models.ForeignKey(
        InternalReceipt,
        on_delete=models.PROTECT,
        related_name="return_receipts",
    )
    internal_number = models.CharField(max_length=40)
    total_amount = models.DecimalField(max_digits=18, decimal_places=2)
    refund_method = models.CharField(max_length=16, choices=SalePaymentMethod.choices)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    issued_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="internal_return_receipts_issued",
    )
    issued_at = models.DateTimeField()

    class Meta:
        ordering = ("-issued_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="sales_unique_return_receipt_number",
            ),
            models.CheckConstraint(
                condition=Q(total_amount__gt=Decimal("0.00")),
                name="sales_return_receipt_total_positive",
            ),
            models.CheckConstraint(
                condition=Q(refund_method__in=SalePaymentMethod.values),
                name="sales_return_receipt_method_valid",
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
            raise ValidationError(_("Internal return receipts cannot be modified."))
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
        raise ValidationError(_("Internal return receipts cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_return_id:
            if self.sale_return.business_id != self.business_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this business.")
                )
            elif self.sale_return.branch_id != self.branch_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this branch.")
                )
            if self.total_amount != self.sale_return.total_refund_amount:
                errors["total_amount"] = ValidationError(
                    _("Return receipt total must equal the return total.")
                )
        if self.original_receipt_id and self.sale_return_id:
            if self.original_receipt.sale_id != self.sale_return.sale_id:
                errors["original_receipt"] = ValidationError(
                    _("Original receipt must belong to the returned sale.")
                )
        if self.issued_by_id and self.issued_by.business_id != self.business_id:
            errors["issued_by"] = ValidationError(_("Issuer must belong to this business."))
        if errors:
            raise ValidationError(errors)


class SaleReturnReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="sale_return_reversals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="sale_return_reversals",
    )
    sale_return = models.OneToOneField(
        SaleReturn,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    posting_key = models.OneToOneField(
        SaleReturnPostingKey,
        on_delete=models.PROTECT,
        related_name="return_reversal",
    )
    reason = models.TextField()
    refund_method = models.CharField(max_length=16, choices=SalePaymentMethod.choices)
    refund_amount = models.DecimalField(max_digits=18, decimal_places=2)
    telebirr_reference = models.CharField(max_length=120, blank=True)
    reversed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="sale_returns_reversed",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(refund_method__in=SalePaymentMethod.values),
                name="sales_return_reversal_method_valid",
            ),
            models.CheckConstraint(
                condition=Q(refund_amount__gt=Decimal("0.00")),
                name="sales_return_reversal_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.sale_return} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Sale return reversals cannot be modified."))
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
        raise ValidationError(_("Sale return reversals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.sale_return_id:
            if self.sale_return.business_id != self.business_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this business.")
                )
            elif self.sale_return.branch_id != self.branch_id:
                errors["sale_return"] = ValidationError(
                    _("Sale return must belong to this branch.")
                )
            if self.refund_amount != self.sale_return.total_refund_amount:
                errors["refund_amount"] = ValidationError(
                    _("Refund reversal amount must equal the original refund amount.")
                )
        if self.posting_key_id:
            if self.posting_key.business_id != self.business_id:
                errors["posting_key"] = ValidationError(
                    _("Posting key must belong to this business.")
                )
            elif self.sale_return_id and (
                self.posting_key.operation_type != SaleReturnOperationType.REVERSAL
                or self.posting_key.source_id != self.sale_return_id
            ):
                errors["posting_key"] = ValidationError(
                    _("Posting key must identify this return reversal.")
                )
        if self.reversed_by_id and self.reversed_by.business_id != self.business_id:
            errors["reversed_by"] = ValidationError(
                _("Reversing actor must belong to this business.")
            )
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A reversal reason is required."))
        if errors:
            raise ValidationError(errors)


class BranchTelebirrProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="telebirr_profiles",
    )
    branch = models.OneToOneField(
        Branch,
        on_delete=models.PROTECT,
        related_name="telebirr_profile",
    )
    merchant_display_name = models.CharField(max_length=160)
    merchant_identifier = models.CharField(max_length=120)
    qr_source = models.FileField(
        storage=catalog_media_storage,
        upload_to=telebirr_qr_upload_path,
        max_length=255,
        blank=True,
    )
    qr_media_type = models.CharField(max_length=32, blank=True)
    qr_width = models.PositiveIntegerField(default=0)
    qr_height = models.PositiveIntegerField(default=0)
    qr_size = models.PositiveBigIntegerField(default=0)
    qr_sha256 = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=False)
    confirmed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="telebirr_profiles_confirmed",
        null=True,
        blank=True,
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    removed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="telebirr_profiles_removed",
        null=True,
        blank=True,
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("branch",)
        constraints = [
            models.CheckConstraint(
                condition=Q(qr_media_type="") | Q(qr_media_type="image/png"),
                name="sales_telebirr_qr_media_type_png",
            ),
            models.CheckConstraint(
                condition=(
                    Q(removed_at__isnull=True, removed_by__isnull=True)
                    | Q(removed_at__isnull=False, removed_by__isnull=False, is_active=False)
                ),
                name="sales_telebirr_removal_evidence_matches",
            ),
            models.CheckConstraint(
                condition=Q(is_active=False)
                | (
                    Q(confirmed_at__isnull=False, confirmed_by__isnull=False)
                    & ~Q(qr_source="")
                    & ~Q(qr_sha256="")
                ),
                name="sales_active_telebirr_profile_is_confirmed",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.branch} — {self.merchant_display_name}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        for field_name in ("confirmed_by", "removed_by"):
            actor = getattr(self, field_name)
            if actor is not None and actor.business_id != self.business_id:
                errors[field_name] = ValidationError(_("Membership must belong to this business."))
        if self.confirmed_by is not None and not self.confirmed_by.can_manage_payment_qr:
            errors["confirmed_by"] = ValidationError(
                _("Only an owner can confirm a Telebirr merchant QR.")
            )
        has_source = bool(self.qr_source)
        has_metadata = bool(
            self.qr_media_type
            and self.qr_width > 0
            and self.qr_height > 0
            and self.qr_size > 0
            and self.qr_sha256
        )
        if has_source != has_metadata:
            errors["qr_source"] = ValidationError(
                _("Telebirr QR file and metadata must be recorded together.")
            )
        if self.is_active and (
            not has_source or self.confirmed_by is None or self.confirmed_at is None
        ):
            errors["is_active"] = ValidationError(
                _("An active Telebirr QR must have an owner confirmation.")
            )
        if errors:
            raise ValidationError(errors)


class BranchTelebirrEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="telebirr_profile_events",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="telebirr_profile_events",
    )
    profile = models.ForeignKey(
        BranchTelebirrProfile,
        on_delete=models.PROTECT,
        related_name="events",
    )
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="telebirr_profile_events",
    )
    action = models.CharField(max_length=16, choices=TelebirrProfileAction.choices)
    previous_sha256 = models.CharField(max_length=64, blank=True)
    resulting_sha256 = models.CharField(max_length=64, blank=True)
    merchant_display_name = models.CharField(max_length=160)
    merchant_identifier = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=TelebirrProfileAction.values),
                name="sales_telebirr_event_action_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.branch} — {self.get_action_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Telebirr profile events cannot be modified."))
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
        raise ValidationError(_("Telebirr profile events cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id:
            if self.branch.business_id != self.business_id:
                errors["branch"] = ValidationError(_("Branch must belong to this business."))
            if self.profile_id and self.profile.branch_id != self.branch_id:
                errors["profile"] = ValidationError(_("Profile must belong to this branch."))
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        if errors:
            raise ValidationError(errors)
