from django.contrib import admin
from django.http import HttpRequest

from apps.catalog.models import Category, Product, ProductVariant
from apps.inventory.models import InventoryMovement
from apps.purchasing.models import PurchaseLine, PurchaseStatus


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    readonly_fields = (
        "business",
        "sku",
        "size",
        "color",
        "selling_price",
        "cost_price",
        "stock_unit",
        "low_stock_threshold",
        "is_active",
    )

    def has_add_permission(self, request: HttpRequest, obj: Product | None = None) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: ProductVariant | None = None,
    ) -> bool:
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "business__name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "business", "category", "is_active")
    list_filter = ("is_active", "public_visibility")
    search_fields = ("name", "business__name")
    inlines = (ProductVariantInline,)


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("sku", "product", "size", "color", "selling_price", "is_active")
    list_filter = ("is_active",)
    search_fields = ("sku", "product__name", "size", "color")

    def get_readonly_fields(
        self,
        request: HttpRequest,
        obj: ProductVariant | None = None,
    ) -> tuple[str, ...]:
        if obj is not None and (
            InventoryMovement.objects.filter(variant=obj).exists()
            or PurchaseLine.objects.filter(
                variant=obj,
                purchase__status__in=(
                    PurchaseStatus.APPROVED,
                    PurchaseStatus.PARTIALLY_RECEIVED,
                ),
            ).exists()
        ):
            return ("business", "stock_unit")
        return ()
