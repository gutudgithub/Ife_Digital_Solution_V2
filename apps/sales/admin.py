from django.contrib import admin
from django.http import HttpRequest

from apps.sales.models import (
    InternalReceipt,
    InternalReturnReceipt,
    Sale,
    SaleLine,
    SalePayment,
    SalePostingKey,
    SaleRefundEvidence,
    SaleReturn,
    SaleReturnLine,
    SaleReturnPostingKey,
    SaleReturnReversal,
)


class ImmutableAdmin(admin.ModelAdmin):
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object | None = None) -> bool:
        return False


class SaleLineInline(admin.TabularInline):
    model = SaleLine
    extra = 0
    readonly_fields = (
        "business",
        "variant",
        "quantity",
        "product_name_snapshot",
        "sku_snapshot",
        "unit_snapshot",
        "selling_unit_price",
        "line_total",
        "assigned_inventory_unit_cost",
        "inventory_value_delta",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest, obj: Sale | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: SaleLine | None = None) -> bool:
        return False


class SaleReturnLineInline(admin.TabularInline):
    model = SaleReturnLine
    extra = 0
    readonly_fields = (
        "business",
        "sale_line",
        "variant",
        "returned_quantity",
        "product_name_snapshot",
        "sku_snapshot",
        "unit_snapshot",
        "original_selling_unit_price",
        "refund_line_total",
        "original_assigned_inventory_unit_cost",
        "inventory_value_delta",
        "created_at",
    )

    def has_add_permission(
        self,
        request: HttpRequest,
        obj: SaleReturn | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: SaleReturnLine | None = None,
    ) -> bool:
        return False


@admin.register(Sale)
class SaleAdmin(ImmutableAdmin):
    list_display = (
        "internal_number",
        "business",
        "branch",
        "sale_date",
        "status",
        "payment_status",
        "total_amount",
    )
    list_filter = ("business", "branch", "sale_date", "status", "payment_status")
    search_fields = ("internal_number", "receipt__internal_number")
    readonly_fields = (
        "business",
        "branch",
        "internal_number",
        "sale_date",
        "status",
        "payment_status",
        "total_amount",
        "created_by",
        "posting_key",
        "posted_by",
        "posted_at",
        "cancelled_by",
        "cancelled_at",
        "cancellation_reason",
        "created_at",
        "updated_at",
    )
    inlines = (SaleLineInline,)


@admin.register(SalePayment)
class SalePaymentAdmin(ImmutableAdmin):
    list_display = ("sale", "business", "branch", "method", "amount", "posted_at")
    list_filter = ("business", "branch", "method", "posted_at")
    search_fields = ("sale__internal_number", "telebirr_reference")
    readonly_fields = (
        "business",
        "branch",
        "sale",
        "method",
        "amount",
        "telebirr_reference",
        "telebirr_reference_normalized",
        "received_by",
        "posted_at",
    )


@admin.register(InternalReceipt)
class InternalReceiptAdmin(ImmutableAdmin):
    list_display = ("internal_number", "sale", "business", "branch", "issued_at")
    list_filter = ("business", "branch", "payment_method", "issued_at")
    search_fields = ("internal_number", "sale__internal_number", "telebirr_reference")
    readonly_fields = (
        "business",
        "branch",
        "sale",
        "internal_number",
        "total_amount",
        "payment_method",
        "telebirr_reference",
        "issued_by",
        "issued_at",
    )


@admin.register(SalePostingKey)
class SalePostingKeyAdmin(ImmutableAdmin):
    list_display = ("key", "business", "source_id", "created_at")
    list_filter = ("business", "created_at")
    readonly_fields = ("business", "key", "source_id", "created_at")


@admin.register(SaleReturn)
class SaleReturnAdmin(ImmutableAdmin):
    list_display = (
        "internal_number",
        "sale",
        "business",
        "branch",
        "purpose",
        "status",
        "total_refund_amount",
    )
    list_filter = ("business", "branch", "purpose", "status", "return_date")
    search_fields = ("internal_number", "sale__internal_number")
    readonly_fields = (
        "business",
        "branch",
        "sale",
        "internal_number",
        "purpose",
        "status",
        "return_date",
        "reason",
        "total_refund_amount",
        "created_by",
        "posting_key",
        "posted_by",
        "posted_at",
        "cancelled_by",
        "cancelled_at",
        "created_at",
        "updated_at",
    )
    inlines = (SaleReturnLineInline,)


@admin.register(SaleRefundEvidence)
class SaleRefundEvidenceAdmin(ImmutableAdmin):
    list_display = ("sale_return", "business", "branch", "method", "amount", "posted_at")
    list_filter = ("business", "branch", "method", "posted_at")
    search_fields = ("sale_return__internal_number", "telebirr_reference")
    readonly_fields = (
        "business",
        "branch",
        "sale_return",
        "method",
        "amount",
        "telebirr_reference",
        "telebirr_reference_normalized",
        "refunded_by",
        "posted_at",
    )


@admin.register(InternalReturnReceipt)
class InternalReturnReceiptAdmin(ImmutableAdmin):
    list_display = ("internal_number", "sale_return", "business", "branch", "issued_at")
    list_filter = ("business", "branch", "refund_method", "issued_at")
    search_fields = (
        "internal_number",
        "sale_return__internal_number",
        "sale_return__sale__internal_number",
        "telebirr_reference",
    )
    readonly_fields = (
        "business",
        "branch",
        "sale_return",
        "original_receipt",
        "internal_number",
        "total_amount",
        "refund_method",
        "telebirr_reference",
        "issued_by",
        "issued_at",
    )


@admin.register(SaleReturnReversal)
class SaleReturnReversalAdmin(ImmutableAdmin):
    list_display = ("sale_return", "business", "branch", "refund_amount", "posted_at")
    list_filter = ("business", "branch", "refund_method", "posted_at")
    search_fields = ("sale_return__internal_number", "telebirr_reference")
    readonly_fields = (
        "business",
        "branch",
        "sale_return",
        "posting_key",
        "reason",
        "refund_method",
        "refund_amount",
        "telebirr_reference",
        "reversed_by",
        "posted_at",
    )


@admin.register(SaleReturnPostingKey)
class SaleReturnPostingKeyAdmin(ImmutableAdmin):
    list_display = ("key", "business", "operation_type", "source_id", "created_at")
    list_filter = ("business", "operation_type", "created_at")
    readonly_fields = ("business", "key", "operation_type", "source_id", "created_at")
