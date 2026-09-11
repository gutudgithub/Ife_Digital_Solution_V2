from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.businesses.models import Business
from apps.catalog.models import Category, Product, ProductVariant, StockUnit


class CatalogTenantIntegrityTests(TestCase):
    first_business: Business
    second_business: Business
    first_product: Product
    second_product: Product

    def setUp(self) -> None:
        self.first_business = Business.objects.create(name="First Shop", slug="first-shop")
        self.second_business = Business.objects.create(name="Second Shop", slug="second-shop")
        self.first_product = Product.objects.create(
            business=self.first_business,
            name="Leather Shoe",
        )
        self.second_product = Product.objects.create(
            business=self.second_business,
            name="Leather Shoe",
        )

    def test_product_rejects_category_from_another_business(self) -> None:
        category = Category.objects.create(
            business=self.second_business,
            name="Shoes",
            slug="shoes",
        )
        product = Product(
            business=self.first_business,
            category=category,
            name="Cross-tenant Product",
        )

        with self.assertRaises(ValidationError):
            product.full_clean()

    def test_variant_rejects_product_from_another_business(self) -> None:
        variant = ProductVariant(
            business=self.first_business,
            product=self.second_product,
            sku="SHOE-42-BLK",
            selling_price=Decimal("2500.00"),
        )

        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_sku_is_unique_within_business_but_reusable_across_businesses(self) -> None:
        ProductVariant.objects.create(
            business=self.first_business,
            product=self.first_product,
            sku="SHOE-42-BLK",
            selling_price=Decimal("2500.00"),
        )
        ProductVariant.objects.create(
            business=self.second_business,
            product=self.second_product,
            sku="SHOE-42-BLK",
            selling_price=Decimal("2600.00"),
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(
                business=self.first_business,
                product=self.first_product,
                sku="SHOE-42-BLK",
                selling_price=Decimal("2700.00"),
            )

    def test_whole_stock_unit_requires_whole_low_stock_threshold(self) -> None:
        variant = ProductVariant(
            business=self.first_business,
            product=self.first_product,
            sku="SHOE-42-WHT",
            selling_price=Decimal("2500.00"),
            stock_unit=StockUnit.PAIR,
            low_stock_threshold=Decimal("1.5"),
        )

        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_measured_stock_unit_accepts_decimal_low_stock_threshold(self) -> None:
        variant = ProductVariant(
            business=self.first_business,
            product=self.first_product,
            sku="LACE-BULK",
            selling_price=Decimal("100.00"),
            stock_unit=StockUnit.METRE,
            low_stock_threshold=Decimal("1.500"),
        )

        variant.full_clean()
