import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant


class StockOperationType(models.TextChoices):
    OPENING = "opening", _("Opening balance")
    ADJUSTMENT_IN = "adjustment_in", _("Positive adjustment")
    ADJUSTMENT_OUT = "adjustment_out", _("Negative adjustment")


class InventoryMovementType(models.TextChoices):
    OPENING = "opening", _("Opening balance")
    PURCHASE_RECEIPT = "purchase_receipt", _("Purchase receipt")
    PURCHASE_RETURN = "purchase_return", _("Purchase return")
    PURCHASE_RETURN_REVERSAL = "purchase_return_reversal", _("Purchase return reversal")
    ADJUSTMENT_IN = "adjustment_in", _("Positive adjustment")
    ADJUSTMENT_OUT = "adjustment_out", _("Negative adjustment")


class InventorySourceType(models.TextChoices):
    STOCK_OPERATION = "stock_operation", _("Stock operation")
    GOODS_RECEIPT_LINE = "goods_receipt_line", _("Goods receipt line")
    PURCHASE_RETURN_LINE = "purchase_return_line", _("Purchase return line")
    PURCHASE_RETURN_REVERSAL = "purchase_return_reversal", _("Purchase return reversal")


class StockOperation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_operations",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_operations",
    )
    operation_type = models.CharField(max_length=20, choices=StockOperationType.choices)
    idempotency_key = models.UUIDField()
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_operations_posted",
    )
    reason = models.TextField(blank=True)
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "idempotency_key"),
                name="inventory_unique_operation_idempotency_per_business",
            ),
            models.CheckConstraint(
                condition=Q(operation_type__in=StockOperationType.values),
                name="inventory_operation_type_is_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_operation_type_display()} — {self.posted_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Posted stock operations cannot be modified."))
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
        raise ValidationError(_("Posted stock operations cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        if self.operation_type != StockOperationType.OPENING and not self.reason.strip():
            errors["reason"] = ValidationError(_("An adjustment reason is required."))
        if errors:
            raise ValidationError(errors)


class InventoryMovement(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    movement_type = models.CharField(max_length=24, choices=InventoryMovementType.choices)
    quantity_delta = models.DecimalField(max_digits=18, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6)
    value_delta = models.DecimalField(max_digits=24, decimal_places=6)
    source_type = models.CharField(max_length=24, choices=InventorySourceType.choices)
    source_id = models.UUIDField()
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="inventory_movements_posted",
    )
    reason = models.TextField(blank=True)
    posted_at = models.DateTimeField()

    class Meta:
        ordering = ("-posted_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "source_type", "source_id", "variant"),
                name="inventory_unique_movement_source_variant",
            ),
            models.CheckConstraint(
                condition=~Q(quantity_delta=Decimal("0.000")),
                name="inventory_movement_quantity_nonzero",
            ),
            models.CheckConstraint(
                condition=Q(unit_cost__gte=Decimal("0.000000")),
                name="inventory_movement_unit_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(
                    quantity_delta__gt=Decimal("0.000"),
                    value_delta__gte=Decimal("0.000000"),
                )
                | Q(
                    quantity_delta__lt=Decimal("0.000"),
                    value_delta__lte=Decimal("0.000000"),
                ),
                name="inventory_movement_value_direction_matches",
            ),
            models.CheckConstraint(
                condition=Q(movement_type__in=InventoryMovementType.values),
                name="inventory_movement_type_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(source_type__in=InventorySourceType.values),
                name="inventory_source_type_is_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.variant} — {self.quantity_delta}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Inventory movements cannot be modified."))
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
        raise ValidationError(_("Inventory movements cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class InventoryBalance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="inventory_balances",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="inventory_balances",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="inventory_balances",
    )
    quantity_on_hand = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        default=Decimal("0.000"),
    )
    average_unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal("0.000000"),
    )
    inventory_value = models.DecimalField(
        max_digits=24,
        decimal_places=6,
        default=Decimal("0.000000"),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("branch", "variant")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "branch", "variant"),
                name="inventory_unique_balance_per_branch_variant",
            ),
            models.CheckConstraint(
                condition=Q(quantity_on_hand__gte=Decimal("0.000")),
                name="inventory_balance_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(average_unit_cost__gte=Decimal("0.000000")),
                name="inventory_balance_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(inventory_value__gte=Decimal("0.000000")),
                name="inventory_balance_value_nonnegative",
            ),
            models.CheckConstraint(
                condition=~Q(quantity_on_hand=Decimal("0.000"))
                | Q(
                    average_unit_cost=Decimal("0.000000"),
                    inventory_value=Decimal("0.000000"),
                ),
                name="inventory_zero_balance_has_zero_cost_and_value",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.branch} — {self.variant}"

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

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        if errors:
            raise ValidationError(errors)
