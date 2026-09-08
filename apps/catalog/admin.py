from django.contrib import admin

from apps.catalog.models import Category, Product, ProductVariant


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0


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
