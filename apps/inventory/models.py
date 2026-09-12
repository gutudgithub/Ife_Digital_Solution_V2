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
    SALE = "sale", _("Sale")
    SALE_RETURN = "sale_return", _("Sale return")
    SALE_RETURN_REVERSAL = "sale_return_reversal", _("Sale return reversal")
    ADJUSTMENT_IN = "adjustment_in", _("Positive adjustment")
    ADJUSTMENT_OUT = "adjustment_out", _("Negative adjustment")
    STOCK_COUNT_ADJUSTMENT_IN = (
        "stock_count_adjustment_in",
        _("Stock count adjustment in"),
    )
    STOCK_COUNT_ADJUSTMENT_OUT = (
        "stock_count_adjustment_out",
        _("Stock count adjustment out"),
    )
    STOCK_COUNT_REVERSAL_IN = "stock_count_reversal_in", _("Stock count reversal in")
    STOCK_COUNT_REVERSAL_OUT = "stock_count_reversal_out", _("Stock count reversal out")


class InventorySourceType(models.TextChoices):
    STOCK_OPERATION = "stock_operation", _("Stock operation")
    GOODS_RECEIPT_LINE = "goods_receipt_line", _("Goods receipt line")
    PURCHASE_RETURN_LINE = "purchase_return_line", _("Purchase return line")
    PURCHASE_RETURN_REVERSAL = "purchase_return_reversal", _("Purchase return reversal")
    SALE_LINE = "sale_line", _("Sale line")
    SALE_RETURN_LINE = "sale_return_line", _("Sale return line")
    SALE_RETURN_REVERSAL = "sale_return_reversal", _("Sale return reversal")
    STOCK_COUNT_LINE = "stock_count_line", _("Stock count line")
    STOCK_COUNT_REVERSAL = "stock_count_reversal", _("Stock count reversal")


class StockCountStatus(models.TextChoices):
    COUNTING = "counting", _("Counting")
    SUBMITTED = "submitted", _("Submitted for review")
    APPROVED = "approved", _("Approved")
    CANCELLED = "cancelled", _("Cancelled")


class StockCountOperationType(models.TextChoices):
    START = "start", _("Start stock count")
    APPROVE = "approve", _("Approve stock count")
    REVERSE = "reverse", _("Reverse stock count")


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
    movement_type = models.CharField(max_length=32, choices=InventoryMovementType.choices)
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


class StockCountPostingKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_posting_keys",
    )
    key = models.UUIDField()
    operation_type = models.CharField(
        max_length=16,
        choices=StockCountOperationType.choices,
    )
    source_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("business", "key"),
                name="inventory_unique_stock_count_key_per_business",
            ),
            models.CheckConstraint(
                condition=Q(operation_type__in=StockCountOperationType.values),
                name="inventory_stock_count_key_operation_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_operation_type_display()} — {self.key}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock-count posting keys cannot be modified."))
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
        raise ValidationError(_("Stock-count posting keys cannot be deleted."))


class StockCountSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_sessions",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_sessions",
    )
    status = models.CharField(
        max_length=16,
        choices=StockCountStatus.choices,
        default=StockCountStatus.COUNTING,
    )
    business_date = models.DateField()
    count_method_note = models.TextField()
    start_key = models.OneToOneField(
        StockCountPostingKey,
        on_delete=models.PROTECT,
        related_name="started_stock_count",
    )
    started_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_counts_started",
    )
    started_at = models.DateTimeField()
    submitted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_counts_submitted",
        null=True,
        blank=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_counts_cancelled",
        null=True,
        blank=True,
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-started_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "branch"),
                condition=Q(status__in=(StockCountStatus.COUNTING, StockCountStatus.SUBMITTED)),
                name="inventory_unique_open_stock_count_per_branch",
            ),
            models.CheckConstraint(
                condition=Q(status__in=StockCountStatus.values),
                name="inventory_stock_count_status_valid",
            ),
            models.CheckConstraint(
                condition=Q(submitted_by__isnull=True, submitted_at__isnull=True)
                | Q(submitted_by__isnull=False, submitted_at__isnull=False),
                name="inventory_stock_count_submission_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(cancelled_by__isnull=True, cancelled_at__isnull=True)
                | Q(cancelled_by__isnull=False, cancelled_at__isnull=False),
                name="inventory_stock_count_cancellation_fields_match",
            ),
            models.CheckConstraint(
                condition=Q(
                    status=StockCountStatus.COUNTING,
                    cancelled_by__isnull=True,
                )
                | Q(
                    status=StockCountStatus.SUBMITTED,
                    submitted_by__isnull=False,
                    cancelled_by__isnull=True,
                )
                | Q(
                    status=StockCountStatus.APPROVED,
                    submitted_by__isnull=False,
                    cancelled_by__isnull=True,
                )
                | Q(
                    status=StockCountStatus.CANCELLED,
                    cancelled_by__isnull=False,
                ),
                name="inventory_stock_count_status_has_audit",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.branch} — {self.business_date}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = StockCountSession.objects.get(pk=self.pk)
            immutable_values = (
                self.business_id,
                self.branch_id,
                self.business_date,
                self.count_method_note,
                self.start_key_id,
                self.started_by_id,
                self.started_at,
            )
            stored_values = (
                stored.business_id,
                stored.branch_id,
                stored.business_date,
                stored.count_method_note,
                stored.start_key_id,
                stored.started_by_id,
                stored.started_at,
            )
            if immutable_values != stored_values:
                raise ValidationError(_("Stock-count opening evidence cannot be modified."))
            allowed_transitions: dict[str, set[str]] = {
                StockCountStatus.COUNTING: {
                    StockCountStatus.COUNTING,
                    StockCountStatus.SUBMITTED,
                    StockCountStatus.CANCELLED,
                },
                StockCountStatus.SUBMITTED: {
                    StockCountStatus.COUNTING,
                    StockCountStatus.SUBMITTED,
                    StockCountStatus.APPROVED,
                    StockCountStatus.CANCELLED,
                },
                StockCountStatus.APPROVED: {StockCountStatus.APPROVED},
                StockCountStatus.CANCELLED: {StockCountStatus.CANCELLED},
            }
            if self.status not in allowed_transitions[stored.status]:
                raise ValidationError(_("This stock-count status transition is not allowed."))
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
        raise ValidationError(_("Stock-count sessions cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.start_key_id and self.start_key.business_id != self.business_id:
            errors["start_key"] = ValidationError(
                _("Stock-count key must belong to this business.")
            )
        elif self.start_key_id and (
            self.start_key.operation_type != StockCountOperationType.START
            or self.start_key.source_id != self.id
        ):
            errors["start_key"] = ValidationError(
                _("Stock-count start key must identify this session.")
            )
        if self.started_by_id and self.started_by.business_id != self.business_id:
            errors["started_by"] = ValidationError(_("Starter must belong to this business."))
        submitted_by = self.submitted_by
        if submitted_by is not None and submitted_by.business_id != self.business_id:
            errors["submitted_by"] = ValidationError(_("Submitter must belong to this business."))
        cancelled_by = self.cancelled_by
        if cancelled_by is not None and cancelled_by.business_id != self.business_id:
            errors["cancelled_by"] = ValidationError(_("Canceller must belong to this business."))
        if not self.count_method_note.strip():
            errors["count_method_note"] = ValidationError(_("A count method note is required."))
        if self.status == StockCountStatus.CANCELLED and not self.cancellation_reason.strip():
            errors["cancellation_reason"] = ValidationError(
                _("A stock-count cancellation reason is required.")
            )
        if errors:
            raise ValidationError(errors)


class StockCountLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
    )
    session = models.ForeignKey(
        StockCountSession,
        on_delete=models.PROTECT,
        related_name="lines",
    )
    variant = models.ForeignKey(
        ProductVariant,
        on_delete=models.PROTECT,
        related_name="stock_count_lines",
    )
    product_name_snapshot = models.CharField(max_length=160)
    variant_label_snapshot = models.CharField(max_length=200, blank=True)
    sku_snapshot = models.CharField(max_length=80)
    stock_unit_snapshot = models.CharField(max_length=16)
    system_quantity_snapshot = models.DecimalField(max_digits=18, decimal_places=3)
    average_unit_cost_snapshot = models.DecimalField(max_digits=18, decimal_places=6)
    inventory_value_snapshot = models.DecimalField(max_digits=24, decimal_places=6)
    physical_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
    )
    variance_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
    )
    variance_explanation = models.TextField(blank=True)
    assigned_count_adjustment_unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
    )
    exceptional_cost_evidence_note = models.TextField(blank=True)
    counted_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_count_lines_counted",
        null=True,
        blank=True,
    )
    counted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("product_name_snapshot", "sku_snapshot")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "variant"),
                name="inventory_unique_stock_count_line_variant",
            ),
            models.CheckConstraint(
                condition=Q(system_quantity_snapshot__gte=Decimal("0.000")),
                name="inventory_stock_count_system_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(average_unit_cost_snapshot__gte=Decimal("0.000000")),
                name="inventory_stock_count_average_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(inventory_value_snapshot__gte=Decimal("0.000000")),
                name="inventory_stock_count_value_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(physical_quantity__isnull=True)
                | Q(physical_quantity__gte=Decimal("0.000")),
                name="inventory_stock_count_physical_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(assigned_count_adjustment_unit_cost__isnull=True)
                | Q(assigned_count_adjustment_unit_cost__gte=Decimal("0.000000")),
                name="inventory_stock_count_assigned_cost_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(counted_by__isnull=True, counted_at__isnull=True)
                | Q(counted_by__isnull=False, counted_at__isnull=False),
                name="inventory_stock_count_counted_fields_match",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.session} — {self.sku_snapshot}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            stored = StockCountLine.objects.select_related("session").get(pk=self.pk)
            immutable_values = (
                self.business_id,
                self.branch_id,
                self.session_id,
                self.variant_id,
                self.product_name_snapshot,
                self.variant_label_snapshot,
                self.sku_snapshot,
                self.stock_unit_snapshot,
                self.system_quantity_snapshot,
                self.average_unit_cost_snapshot,
                self.inventory_value_snapshot,
            )
            stored_values = (
                stored.business_id,
                stored.branch_id,
                stored.session_id,
                stored.variant_id,
                stored.product_name_snapshot,
                stored.variant_label_snapshot,
                stored.sku_snapshot,
                stored.stock_unit_snapshot,
                stored.system_quantity_snapshot,
                stored.average_unit_cost_snapshot,
                stored.inventory_value_snapshot,
            )
            if immutable_values != stored_values:
                raise ValidationError(_("Stock-count snapshot evidence cannot be modified."))
            if stored.session.status in {
                StockCountStatus.APPROVED,
                StockCountStatus.CANCELLED,
            }:
                raise ValidationError(_("Completed stock-count lines cannot be modified."))
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
        raise ValidationError(_("Stock-count lines cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id and (
            self.session.business_id != self.business_id or self.session.branch_id != self.branch_id
        ):
            errors["session"] = ValidationError(
                _("Stock-count session must belong to this business and branch.")
            )
        if self.variant_id and self.variant.business_id != self.business_id:
            errors["variant"] = ValidationError(_("Variant must belong to this business."))
        counted_by = self.counted_by
        if counted_by is not None and counted_by.business_id != self.business_id:
            errors["counted_by"] = ValidationError(_("Counter must belong to this business."))
        if self.physical_quantity is None and (
            self.variance_quantity is not None
            or self.assigned_count_adjustment_unit_cost is not None
        ):
            errors["physical_quantity"] = ValidationError(
                _("An uncounted line cannot have adjustment evidence.")
            )
        if errors:
            raise ValidationError(errors)


class StockCountLineRevision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_line_revisions",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_line_revisions",
    )
    session = models.ForeignKey(
        StockCountSession,
        on_delete=models.PROTECT,
        related_name="line_revisions",
    )
    line = models.ForeignKey(
        StockCountLine,
        on_delete=models.PROTECT,
        related_name="revisions",
    )
    sequence = models.PositiveIntegerField()
    previous_quantity = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
    )
    replacement_quantity = models.DecimalField(max_digits=18, decimal_places=3)
    reason = models.TextField(blank=True)
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_count_line_revisions",
    )
    revised_at = models.DateTimeField()

    class Meta:
        ordering = ("line", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("line", "sequence"),
                name="inventory_unique_stock_count_revision_sequence",
            ),
            models.CheckConstraint(
                condition=Q(previous_quantity__isnull=True)
                | Q(previous_quantity__gte=Decimal("0.000")),
                name="inventory_stock_count_previous_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(replacement_quantity__gte=Decimal("0.000")),
                name="inventory_stock_count_replacement_quantity_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.line} — {self.sequence}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock-count line revisions cannot be modified."))
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
        raise ValidationError(_("Stock-count line revisions cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id and (
            self.session.business_id != self.business_id or self.session.branch_id != self.branch_id
        ):
            errors["session"] = ValidationError(
                _("Stock-count session must belong to this business and branch.")
            )
        if self.line_id and (
            self.line.business_id != self.business_id
            or self.line.branch_id != self.branch_id
            or self.line.session_id != self.session_id
        ):
            errors["line"] = ValidationError(_("Stock-count line must belong to this session."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        if errors:
            raise ValidationError(errors)


class StockCountReviewReturn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_review_returns",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_review_returns",
    )
    session = models.ForeignKey(
        StockCountSession,
        on_delete=models.PROTECT,
        related_name="review_returns",
    )
    sequence = models.PositiveIntegerField()
    reason = models.TextField()
    returned_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_count_review_returns",
    )
    returned_at = models.DateTimeField()

    class Meta:
        ordering = ("session", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("session", "sequence"),
                name="inventory_unique_stock_count_review_return_sequence",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.session} — {self.sequence}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock-count review returns cannot be modified."))
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
        raise ValidationError(_("Stock-count review returns cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id and (
            self.session.business_id != self.business_id or self.session.branch_id != self.branch_id
        ):
            errors["session"] = ValidationError(
                _("Stock-count session must belong to this business and branch.")
            )
        if self.returned_by_id and self.returned_by.business_id != self.business_id:
            errors["returned_by"] = ValidationError(_("Reviewer must belong to this business."))
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A recount reason is required."))
        if errors:
            raise ValidationError(errors)


class StockCountApproval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_approvals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_approvals",
    )
    session = models.OneToOneField(
        StockCountSession,
        on_delete=models.PROTECT,
        related_name="approval",
    )
    posting_key = models.OneToOneField(
        StockCountPostingKey,
        on_delete=models.PROTECT,
        related_name="stock_count_approval",
    )
    approved_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_counts_approved",
    )
    approved_at = models.DateTimeField()
    line_count = models.PositiveIntegerField()
    positive_variance_line_count = models.PositiveIntegerField()
    negative_variance_line_count = models.PositiveIntegerField()
    zero_variance_line_count = models.PositiveIntegerField()
    total_inventory_value_adjustment = models.DecimalField(
        max_digits=24,
        decimal_places=6,
    )
    evidence_checksum = models.CharField(max_length=64)

    class Meta:
        ordering = ("-approved_at",)

    def __str__(self) -> str:
        return f"{self.session} — {self.approved_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock-count approvals cannot be modified."))
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
        raise ValidationError(_("Stock-count approvals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.session_id and (
            self.session.business_id != self.business_id or self.session.branch_id != self.branch_id
        ):
            errors["session"] = ValidationError(
                _("Stock-count session must belong to this business and branch.")
            )
        if self.posting_key_id and (
            self.posting_key.business_id != self.business_id
            or self.posting_key.operation_type != StockCountOperationType.APPROVE
            or self.posting_key.source_id != self.session_id
        ):
            errors["posting_key"] = ValidationError(
                _("Stock-count approval key must identify this session.")
            )
        if self.approved_by_id and self.approved_by.business_id != self.business_id:
            errors["approved_by"] = ValidationError(_("Approver must belong to this business."))
        if (
            self.line_count
            != self.positive_variance_line_count
            + self.negative_variance_line_count
            + self.zero_variance_line_count
        ):
            errors["line_count"] = ValidationError(
                _("Stock-count approval line totals do not reconcile.")
            )
        if errors:
            raise ValidationError(errors)


class StockCountReversal(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="stock_count_reversals",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="stock_count_reversals",
    )
    approval = models.OneToOneField(
        StockCountApproval,
        on_delete=models.PROTECT,
        related_name="reversal",
    )
    posting_key = models.OneToOneField(
        StockCountPostingKey,
        on_delete=models.PROTECT,
        related_name="stock_count_reversal",
    )
    reason = models.TextField()
    reversed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="stock_counts_reversed",
    )
    reversed_at = models.DateTimeField()

    class Meta:
        ordering = ("-reversed_at",)

    def __str__(self) -> str:
        return f"{self.approval.session} — {self.reversed_at}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Stock-count reversals cannot be modified."))
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
        raise ValidationError(_("Stock-count reversals cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("Branch must belong to this business."))
        if self.approval_id and (
            self.approval.business_id != self.business_id
            or self.approval.branch_id != self.branch_id
        ):
            errors["approval"] = ValidationError(
                _("Stock-count approval must belong to this business and branch.")
            )
        if self.posting_key_id and (
            self.posting_key.business_id != self.business_id
            or self.posting_key.operation_type != StockCountOperationType.REVERSE
            or self.posting_key.source_id != self.approval.session_id
        ):
            errors["posting_key"] = ValidationError(
                _("Stock-count reversal key must identify this session.")
            )
        if self.reversed_by_id and self.reversed_by.business_id != self.business_id:
            errors["reversed_by"] = ValidationError(_("Reverser must belong to this business."))
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A stock-count reversal reason is required."))
        if errors:
            raise ValidationError(errors)
