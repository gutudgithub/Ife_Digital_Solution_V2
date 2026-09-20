import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Business


class StockUnit(models.TextChoices):
    PIECE = "piece", _("Piece")
    PAIR = "pair", _("Pair")
    PACK = "pack", _("Pack")
    KILOGRAM = "kilogram", _("Kilogram")
    GRAM = "gram", _("Gram")
    LITRE = "litre", _("Litre")
    MILLILITRE = "millilitre", _("Millilitre")
    METRE = "metre", _("Metre")


WHOLE_STOCK_UNITS = frozenset({StockUnit.PIECE, StockUnit.PAIR, StockUnit.PACK})


def validate_stock_quantity(quantity: Decimal, unit: str) -> None:
    if quantity <= 0:
        raise ValidationError(_("Quantity must be greater than zero."))
    if unit in WHOLE_STOCK_UNITS and quantity != quantity.to_integral_value():
        raise ValidationError(_("This stock unit requires a whole-number quantity."))


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="categories")
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "slug"),
                name="catalog_unique_category_slug_per_business",
            )
        ]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="products")
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    public_visibility = models.BooleanField(default=False)
    show_public_prices = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("business", "name"),
                name="catalog_unique_product_name_per_business",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if (
            self.category_id
            and self.category is not None
            and self.category.business_id != self.business_id
        ):
            raise ValidationError({"category": _("Category must belong to the same business.")})


class ProductVariant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(Business, on_delete=models.PROTECT, related_name="variants")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="variants")
    sku = models.CharField(max_length=80)
    size = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=60, blank=True)
    selling_price = models.DecimalField(max_digits=14, decimal_places=2)
    cost_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    stock_unit = models.CharField(
        max_length=16,
        choices=StockUnit.choices,
        default=StockUnit.PIECE,
    )
    low_stock_threshold = models.DecimalField(
        max_digits=18,
        decimal_places=3,
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product", "size", "color", "sku")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "sku"),
                name="catalog_unique_variant_sku_per_business",
            ),
            models.CheckConstraint(
                condition=Q(selling_price__gte=Decimal("0.00")),
                name="catalog_variant_selling_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(cost_price__isnull=True) | Q(cost_price__gte=Decimal("0.00")),
                name="catalog_variant_cost_price_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(stock_unit__in=StockUnit.values),
                name="catalog_variant_stock_unit_is_valid",
            ),
            models.CheckConstraint(
                condition=Q(low_stock_threshold__isnull=True)
                | Q(low_stock_threshold__gte=Decimal("0.000")),
                name="catalog_variant_low_stock_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        attributes = " / ".join(value for value in (self.size, self.color) if value)
        return f"{self.product.name} — {attributes or self.sku}"

    def clean(self) -> None:
        super().clean()
        if self.product_id and self.product.business_id != self.business_id:
            raise ValidationError({"product": _("Product must belong to the same business.")})
        if self.low_stock_threshold is not None:
            if self.low_stock_threshold < 0:
                raise ValidationError(
                    {"low_stock_threshold": _("Low-stock threshold cannot be negative.")}
                )
            if (
                self.stock_unit in WHOLE_STOCK_UNITS
                and self.low_stock_threshold != self.low_stock_threshold.to_integral_value()
            ):
                raise ValidationError(
                    {"low_stock_threshold": _("This stock unit requires a whole-number threshold.")}
                )
