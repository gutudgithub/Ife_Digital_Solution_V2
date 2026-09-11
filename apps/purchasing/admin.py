from django.contrib import admin
from django.http import HttpRequest

from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseReturnPostingKey,
    PurchaseReturnReversal,
    Supplier,
)


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


class PurchaseReturnLineInline(admin.TabularInline):
    model = PurchaseReturnLine
    extra = 0
    readonly_fields = (
        "business",
        "receipt_line",
        "variant",
        "returned_quantity",
        "product_name_snapshot",
        "sku_snapshot",
        "unit_snapshot",
        "supplier_name_snapshot",
        "receipt_unit_cost",
        "supplier_reference_total",
        "assigned_inventory_unit_cost",
        "inventory_value_delta",
        "created_at",
    )

    def has_add_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturn | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturnLine | None = None,
    ) -> bool:
        return False


@admin.register(PurchaseReturn)
class PurchaseReturnAdmin(admin.ModelAdmin):
    list_display = (
        "internal_number",
        "business",
        "branch",
        "supplier",
        "status",
        "return_date",
    )
    list_filter = ("status", "business", "branch", "return_date")
    search_fields = (
        "internal_number",
        "supplier_document_reference",
        "supplier__name",
        "purchase__internal_number",
    )
    readonly_fields = (
        "business",
        "branch",
        "supplier",
        "purchase",
        "internal_number",
        "return_date",
        "reason",
        "supplier_document_reference",
        "status",
        "created_by",
        "posting_key",
        "posted_by",
        "posted_at",
        "cancelled_by",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    inlines = (PurchaseReturnLineInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturn | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturn | None = None,
    ) -> bool:
        return False


@admin.register(PurchaseReturnReversal)
class PurchaseReturnReversalAdmin(admin.ModelAdmin):
    list_display = ("purchase_return", "business", "branch", "reversed_by", "posted_at")
    list_filter = ("business", "branch", "posted_at")
    search_fields = ("purchase_return__internal_number", "reason")
    readonly_fields = (
        "business",
        "branch",
        "purchase_return",
        "posting_key",
        "reason",
        "reversed_by",
        "posted_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturnReversal | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturnReversal | None = None,
    ) -> bool:
        return False


@admin.register(PurchaseReturnPostingKey)
class PurchaseReturnPostingKeyAdmin(admin.ModelAdmin):
    list_display = ("key", "business", "operation_type", "source_id", "created_at")
    list_filter = ("business", "operation_type", "created_at")
    readonly_fields = ("business", "key", "operation_type", "source_id", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturnPostingKey | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: PurchaseReturnPostingKey | None = None,
    ) -> bool:
        return False
