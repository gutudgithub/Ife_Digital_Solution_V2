import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant, StockUnit, validate_stock_quantity


class PurchaseStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    APPROVED = "approved", _("Approved")
    PARTIALLY_RECEIVED = "partially_received", _("Partially received")
    RECEIVED = "received", _("Received")
    CANCELLED = "cancelled", _("Cancelled")


class Supplier(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="suppliers")
    name = models.CharField(max_length=180)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "name"),
                name="purchasing_unique_supplier_name_per_business",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class Purchase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="purchases")
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="purchases")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchases")
    internal_number = models.CharField(max_length=40)
    supplier_reference = models.CharField(max_length=120, blank=True)
    purchase_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    settlement_terms = models.CharField(max_length=180, blank=True)
    status = models.CharField(
        max_length=24,
        choices=PurchaseStatus.choices,
        default=PurchaseStatus.DRAFT,
    )
    created_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="purchases_created",
    )
    approved_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="purchases_approved",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-purchase_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="purchasing_unique_purchase_number_per_business",
            ),
            models.CheckConstraint(
                condition=Q(status__in=PurchaseStatus.values),
                name="purchasing_purchase_status_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(approved_at__isnull=True, approved_by__isnull=True)
                | Q(approved_at__isnull=False, approved_by__isnull=False),
                name="purchasing_purchase_approval_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(status__in=(PurchaseStatus.DRAFT, PurchaseStatus.CANCELLED))
                | Q(approved_at__isnull=False, approved_by__isnull=False),
                name="purchasing_post_approval_status_has_audit",
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
            stored = Purchase.objects.get(pk=self.pk)
            if stored.status != PurchaseStatus.DRAFT:
                immutable_values = (
                    self.business_id,
                    self.branch_id,
                    self.supplier_id,
                    self.internal_number,
                    self.supplier_reference,
                    self.purchase_date,
                    self.expected_date,
                    self.settlement_terms,
                    self.created_by_id,
                    self.approved_by_id,
                    self.approved_at,
                )
                stored_values = (
                    stored.business_id,
                    stored.branch_id,
                    stored.supplier_id,
                    stored.internal_number,
                    stored.supplier_reference,
                    stored.purchase_date,
                    stored.expected_date,
                    stored.settlement_terms,
                    stored.created_by_id,
                    stored.approved_by_id,
                    stored.approved_at,
                )
                if immutable_values != stored_values:
                    raise ValidationError(_("Approved purchase details cannot be modified."))
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
        stored_status = Purchase.objects.filter(pk=self.pk).values_list("status", flat=True).first()
        if stored_status != PurchaseStatus.DRAFT:
            raise ValidationError(_("Approved purchases cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.supplier_id and self.supplier.business_id != self.business_id:
            errors["supplier"] = ValidationError(_("Supplier must belong to this business."))
        if self.created_by_id and self.created_by.business_id != self.business_id:
            errors["created_by"] = ValidationError(_("Creator must belong to this business."))
        approved_by = self.approved_by
        if approved_by is not None and approved_by.business_id != self.business_id:
            errors["approved_by"] = ValidationError(_("Approver must belong to this business."))
        if self.expected_date is not None and self.expected_date < self.purchase_date:
            errors["expected_date"] = ValidationError(
                _("Expected date cannot be earlier than purchase date.")
            )
        if self.status == PurchaseStatus.DRAFT and (
            self.approved_by_id is not None or self.approved_at is not None
        ):
            errors["status"] = ValidationError(_("A draft purchase cannot be approved."))
        if self.status in {
            PurchaseStatus.APPROVED,
            PurchaseStatus.PARTIALLY_RECEIVED,
            PurchaseStatus.RECEIVED,
        } and (self.approved_by_id is None or self.approved_at is None):
            errors["status"] = ValidationError(_("An approved purchase requires approval details."))
        if errors:
            raise ValidationError(errors)

    @property
    def total_amount(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))


class PurchaseLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="purchase_lines",
    )
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="lines")
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="purchase_lines",
    )
    ordered_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6)
    product_name_snapshot = models.CharField(max_length=180, blank=True)
    sku_snapshot = models.CharField(max_length=80, blank=True)
    unit_snapshot = models.CharField(max_length=16, choices=StockUnit.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("purchase", "variant"),
                name="purchasing_unique_variant_per_purchase",
            ),
            models.CheckConstraint(
                condition=Q(ordered_quantity__gt=Decimal("0.000")),
                name="purchasing_line_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__gte=Decimal("0.000000")),
                name="purchasing_line_unit_cost_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.purchase} — {self.variant}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        purchase_status = (
            Purchase.objects.filter(pk=self.purchase_id)
            .values_list(
                "status",
                flat=True,
            )
            .first()
        )
        if purchase_status != PurchaseStatus.DRAFT:
            raise ValidationError(_("Approved purchase lines cannot be modified."))
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
        purchase_status = (
            Purchase.objects.filter(pk=self.purchase_id)
            .values_list(
                "status",
                flat=True,
            )
            .first()
        )
        if purchase_status != PurchaseStatus.DRAFT:
            raise ValidationError(_("Approved purchase lines cannot be deleted."))
        return super().delete(using=using, keep_parents=keep_parents)

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.purchase_id and self.purchase.business_id != self.business_id:
            errors["purchase"] = ValidationError(_("Purchase must belong to this business."))
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if self.variant_id:
            try:
                validate_stock_quantity(self.ordered_quantity, self.variant.stock_unit)
            except ValidationError as error:
                errors["ordered_quantity"] = error
        if errors:
            raise ValidationError(errors)

    @property
    def line_total(self) -> Decimal:
        return self.ordered_quantity * self.unit_cost


class GoodsReceipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="goods_receipts",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="goods_receipts",
    )
    purchase = models.ForeignKey(Purchase, on_delete=models.PROTECT, related_name="receipts")
    internal_number = models.CharField(max_length=40)
    supplier_document_reference = models.CharField(max_length=120, blank=True)
    idempotency_key = models.UUIDField()
    received_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="goods_receipts_received",
    )
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "internal_number"),
                name="purchasing_unique_receipt_number_per_business",
            ),
            models.UniqueConstraint(
                fields=("business", "idempotency_key"),
                name="purchasing_unique_receipt_idempotency_per_business",
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
            raise ValidationError(_("Posted goods receipts cannot be modified."))
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
        raise ValidationError(_("Posted goods receipts cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.purchase_id and self.purchase.business_id != self.business_id:
            errors["purchase"] = ValidationError(_("Purchase must belong to this business."))
        if self.purchase_id and self.purchase.branch_id != self.branch_id:
            errors["purchase"] = ValidationError(_("Purchase must belong to this branch."))
        if self.received_by_id and self.received_by.business_id != self.business_id:
            errors["received_by"] = ValidationError(_("Receiver must belong to this business."))
        if errors:
            raise ValidationError(errors)


class GoodsReceiptLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="goods_receipt_lines",
    )
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.PROTECT, related_name="lines")
    purchase_line = models.ForeignKey(
        PurchaseLine,
        on_delete=models.PROTECT,
        related_name="receipt_lines",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="goods_receipt_lines",
    )
    received_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    unit_snapshot = models.CharField(max_length=16, choices=StockUnit.choices)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("receipt", "purchase_line"),
                name="purchasing_unique_line_per_receipt",
            ),
            models.CheckConstraint(
                condition=Q(received_quantity__gt=Decimal("0.000")),
                name="purchasing_receipt_quantity_positive",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__gte=Decimal("0.000000")),
                name="purchasing_receipt_unit_cost_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.receipt} — {self.variant}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Posted goods receipt lines cannot be modified."))
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
        raise ValidationError(_("Posted goods receipt lines cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.receipt_id and self.receipt.business_id != self.business_id:
            errors["receipt"] = ValidationError(_("Receipt must belong to this business."))
        if self.purchase_line_id and self.purchase_line.business_id != self.business_id:
            errors["purchase_line"] = ValidationError(
                _("Purchase line must belong to this business.")
            )
        if self.receipt_id and self.purchase_line_id:
            if self.purchase_line.purchase_id != self.receipt.purchase_id:
                errors["purchase_line"] = ValidationError(
                    _("Purchase line must belong to the receipt purchase.")
                )
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if self.purchase_line_id and self.variant_id:
            if self.purchase_line.variant_id != self.variant_id:
                errors["variant"] = ValidationError(_("Variant must match the purchase line."))
            if self.unit_snapshot != self.purchase_line.unit_snapshot:
                errors["unit_snapshot"] = ValidationError(
                    _("Stock unit must match the approved purchase line.")
                )
        if self.unit_snapshot:
            try:
                validate_stock_quantity(self.received_quantity, self.unit_snapshot)
            except ValidationError as error:
                errors["received_quantity"] = error
        if errors:
            raise ValidationError(errors)

    @property
    def line_total(self) -> Decimal:
        return self.received_quantity * self.unit_cost
