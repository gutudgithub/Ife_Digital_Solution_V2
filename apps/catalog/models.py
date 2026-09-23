import uuid
from collections.abc import Iterable
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Business, BusinessMembership
from apps.catalog.storage import catalog_media_storage, product_image_upload_path


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


class ProductImageAction(models.TextChoices):
    ADDED = "added", _("Added")
    REPLACED = "replaced", _("Replaced")
    REMOVED = "removed", _("Removed")
    RECONCILED = "reconciled", _("Reconciled")


class ProductImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="product_images",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="images",
    )
    source = models.FileField(
        storage=catalog_media_storage,
        upload_to=product_image_upload_path,
        max_length=255,
    )
    media_type = models.CharField(max_length=32)
    width = models.PositiveIntegerField()
    height = models.PositiveIntegerField()
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    alt_text = models.CharField(max_length=240)
    uploaded_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="product_images_uploaded",
    )
    removed_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="product_images_removed",
        null=True,
        blank=True,
    )
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("product",),
                condition=Q(removed_at__isnull=True),
                name="catalog_one_current_image_per_product",
            ),
            models.CheckConstraint(
                condition=Q(width__gte=1) & Q(height__gte=1) & Q(size__gte=1),
                name="catalog_product_image_dimensions_size_positive",
            ),
            models.CheckConstraint(
                condition=Q(media_type="image/webp"),
                name="catalog_product_image_media_type_webp",
            ),
            models.CheckConstraint(
                condition=(
                    Q(removed_at__isnull=True, removed_by__isnull=True)
                    | Q(removed_at__isnull=False, removed_by__isnull=False)
                ),
                name="catalog_product_image_removal_evidence_matches",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.product} — {self.sha256[:12]}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.product_id and self.product.business_id != self.business_id:
            errors["product"] = ValidationError(_("Product image must belong to this business."))
        if self.uploaded_by_id and self.uploaded_by.business_id != self.business_id:
            errors["uploaded_by"] = ValidationError(_("Uploader must belong to this business."))
        removed_by = self.removed_by
        if removed_by is not None and removed_by.business_id != self.business_id:
            errors["removed_by"] = ValidationError(
                _("Removing membership must belong to this business.")
            )
        if not self.alt_text.strip():
            errors["alt_text"] = ValidationError(_("Describe the product image."))
        if errors:
            raise ValidationError(errors)

    @property
    def is_current(self) -> bool:
        return self.removed_at is None

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Product image records cannot be deleted."))


class ProductImageEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="product_image_events",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="image_events",
    )
    image = models.ForeignKey(
        ProductImage,
        on_delete=models.PROTECT,
        related_name="events",
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="product_image_events",
    )
    action = models.CharField(max_length=16, choices=ProductImageAction.choices)
    previous_sha256 = models.CharField(max_length=64, blank=True)
    resulting_sha256 = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=ProductImageAction.values),
                name="catalog_product_image_event_action_valid",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product} — {self.get_action_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Product image events cannot be modified."))
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
        raise ValidationError(_("Product image events cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.product_id and self.product.business_id != self.business_id:
            errors["product"] = ValidationError(_("Product must belong to this business."))
        image = self.image
        if image is not None and image.business_id != self.business_id:
            errors["image"] = ValidationError(_("Image must belong to this business."))
        if self.actor_id and self.actor.business_id != self.business_id:
            errors["actor"] = ValidationError(_("Actor must belong to this business."))
        if errors:
            raise ValidationError(errors)
