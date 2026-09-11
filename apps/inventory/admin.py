from django.contrib import admin
from django.http import HttpRequest

from apps.inventory.models import InventoryBalance, InventoryMovement, StockOperation


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
