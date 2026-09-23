from django.contrib import admin
from django.db import models
from django.http import HttpRequest

from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    StockCountApproval,
    StockCountLine,
    StockCountLineRevision,
    StockCountPostingKey,
    StockCountReversal,
    StockCountReviewReturn,
    StockCountSession,
    StockOperation,
)


class ImmutableInventoryEvidenceAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: models.Model | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: models.Model | None = None,
    ) -> bool:
        return False


@admin.register(InventoryBalance)
class InventoryBalanceAdmin(admin.ModelAdmin):
    list_display = (
        "business",
        "branch",
        "variant",
        "quantity_on_hand",
        "average_unit_cost",
        "inventory_value",
    )
    list_filter = ("business", "branch")
    search_fields = ("variant__sku", "variant__product__name")
    readonly_fields = (
        "business",
        "branch",
        "variant",
        "quantity_on_hand",
        "average_unit_cost",
        "inventory_value",
        "updated_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: InventoryBalance | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: InventoryBalance | None = None,
    ) -> bool:
        return False


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = (
        "posted_at",
        "business",
        "branch",
        "variant",
        "movement_type",
        "quantity_delta",
        "unit_cost",
    )
    list_filter = ("movement_type", "business", "branch", "posted_at")
    search_fields = ("variant__sku", "variant__product__name", "reason")
    readonly_fields = (
        "business",
        "branch",
        "variant",
        "movement_type",
        "quantity_delta",
        "unit_cost",
        "value_delta",
        "source_type",
        "source_id",
        "actor",
        "reason",
        "posted_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: InventoryMovement | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: InventoryMovement | None = None,
    ) -> bool:
        return False


@admin.register(StockOperation)
class StockOperationAdmin(admin.ModelAdmin):
    list_display = ("posted_at", "business", "branch", "operation_type", "actor")
    list_filter = ("operation_type", "business", "branch", "posted_at")
    search_fields = ("reason", "actor__user__email")
    readonly_fields = (
        "business",
        "branch",
        "operation_type",
        "idempotency_key",
        "actor",
        "reason",
        "posted_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: StockOperation | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: StockOperation | None = None,
    ) -> bool:
        return False


@admin.register(StockCountSession)
class StockCountSessionAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = (
        "business_date",
        "business",
        "branch",
        "status",
        "started_by",
        "submitted_by",
    )
    list_filter = ("status", "business", "branch", "business_date")
    search_fields = (
        "branch__name",
        "count_method_note",
        "started_by__user__email",
        "submitted_by__user__email",
    )


@admin.register(StockCountLine)
class StockCountLineAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = (
        "session",
        "sku_snapshot",
        "system_quantity_snapshot",
        "physical_quantity",
        "variance_quantity",
    )
    list_filter = ("business", "branch", "stock_unit_snapshot")
    search_fields = ("product_name_snapshot", "variant_label_snapshot", "sku_snapshot")


@admin.register(StockCountLineRevision)
class StockCountLineRevisionAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = ("line", "sequence", "previous_quantity", "replacement_quantity", "actor")
    list_filter = ("business", "branch", "revised_at")
    search_fields = ("line__sku_snapshot", "reason", "actor__user__email")


@admin.register(StockCountReviewReturn)
class StockCountReviewReturnAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = ("session", "sequence", "returned_by", "returned_at")
    list_filter = ("business", "branch", "returned_at")
    search_fields = ("reason", "returned_by__user__email")


@admin.register(StockCountApproval)
class StockCountApprovalAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = (
        "session",
        "approved_by",
        "approved_at",
        "line_count",
        "total_inventory_value_adjustment",
    )
    list_filter = ("business", "branch", "approved_at")
    search_fields = ("approved_by__user__email", "evidence_checksum")


@admin.register(StockCountReversal)
class StockCountReversalAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = ("approval", "reversed_by", "reversed_at")
    list_filter = ("business", "branch", "reversed_at")
    search_fields = ("reason", "reversed_by__user__email")


@admin.register(StockCountPostingKey)
class StockCountPostingKeyAdmin(ImmutableInventoryEvidenceAdmin):
    list_display = ("business", "operation_type", "key", "source_id", "created_at")
    list_filter = ("business", "operation_type", "created_at")
    search_fields = ("key", "source_id")
