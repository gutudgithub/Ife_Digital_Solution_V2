from django.contrib import admin
from django.http import HttpRequest

from apps.purchasing.models import GoodsReceipt, GoodsReceiptLine, Purchase, PurchaseLine, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "phone", "email", "is_active")
    list_filter = ("is_active", "business")
    search_fields = ("name", "phone", "email")


class PurchaseLineInline(admin.TabularInline):
    model = PurchaseLine
    extra = 0
    readonly_fields = (
        "business",
        "variant",
        "ordered_quantity",
        "unit_cost",
        "product_name_snapshot",
        "sku_snapshot",
        "unit_snapshot",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest, obj: Purchase | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: PurchaseLine | None = None) -> bool:
        return False


@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = (
        "internal_number",
        "business",
        "branch",
        "supplier",
        "status",
        "purchase_date",
    )
    list_filter = ("status", "business", "branch", "purchase_date")
    search_fields = ("internal_number", "supplier_reference", "supplier__name")
    readonly_fields = (
        "business",
        "branch",
        "supplier",
        "internal_number",
        "supplier_reference",
        "purchase_date",
        "expected_date",
        "settlement_terms",
        "status",
        "created_by",
        "approved_by",
        "approved_at",
        "created_at",
        "updated_at",
    )
    inlines = (PurchaseLineInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: Purchase | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: Purchase | None = None,
    ) -> bool:
        return False


class GoodsReceiptLineInline(admin.TabularInline):
    model = GoodsReceiptLine
    extra = 0
    readonly_fields = (
        "business",
        "purchase_line",
        "variant",
        "received_quantity",
        "unit_snapshot",
        "unit_cost",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest, obj: GoodsReceipt | None = None) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: GoodsReceiptLine | None = None,
    ) -> bool:
        return False


@admin.register(GoodsReceipt)
class GoodsReceiptAdmin(admin.ModelAdmin):
    list_display = ("internal_number", "purchase", "business", "branch", "posted_at")
    list_filter = ("business", "branch", "posted_at")
    search_fields = ("internal_number", "supplier_document_reference")
    readonly_fields = (
        "business",
        "branch",
        "purchase",
        "internal_number",
        "supplier_document_reference",
        "idempotency_key",
        "received_by",
        "posted_at",
    )
    inlines = (GoodsReceiptLineInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: GoodsReceipt | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: GoodsReceipt | None = None,
    ) -> bool:
        return False
