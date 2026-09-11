import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Category, Product, ProductVariant, StockUnit
from apps.inventory.services import post_opening_balance
from apps.purchasing.models import Purchase, PurchaseLine, Supplier
from apps.purchasing.services import approve_purchase


class ProductViewTests(TestCase):
    owner: User
    cashier: User
    first_business: Business
    second_business: Business
    category: Category
    branch: Branch
    owner_membership: BusinessMembership

    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="strong-test-password",
            full_name="Pilot Owner",
        )
        self.cashier = User.objects.create_user(
            email="cashier@example.com",
            password="strong-test-password",
            full_name="Pilot Cashier",
        )
        self.first_business = Business.objects.create(name="First Shop", slug="first-shop")
        self.second_business = Business.objects.create(name="Second Shop", slug="second-shop")
        self.branch = Branch.objects.create(
            business=self.first_business,
            name="Main",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.first_business,
            user=self.owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        BusinessMembership.objects.create(
            business=self.first_business,
            user=self.cashier,
            role=MembershipRole.CASHIER,
        )
        self.category = Category.objects.create(
            business=self.first_business,
            name="Footwear",
            slug="footwear",
        )

    def test_product_list_is_scoped_to_active_business(self) -> None:
        Product.objects.create(business=self.first_business, name="Visible Product")
        Product.objects.create(business=self.second_business, name="Hidden Product")
        self.client.force_login(self.owner)

        response = self.client.get(reverse("catalog:product-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visible Product")
        self.assertNotContains(response, "Hidden Product")

    def test_owner_can_create_product_in_active_business(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:product-create"),
            {
                "name": "Running Shoe",
                "category": str(self.category.id),
                "description": "Lightweight footwear",
            },
        )

        self.assertRedirects(response, reverse("catalog:product-list"))
        product = Product.objects.get(name="Running Shoe")
        self.assertEqual(product.business, self.first_business)
        self.assertEqual(product.category, self.category)

    def test_product_create_rejects_duplicate_name_without_server_error(self) -> None:
        Product.objects.create(business=self.first_business, name="Running Shoe")
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:product-create"),
            {
                "name": "running shoe",
                "category": str(self.category.id),
                "description": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A product with this name already exists.")
        self.assertEqual(Product.objects.filter(business=self.first_business).count(), 1)

    def test_cross_business_category_is_not_accepted(self) -> None:
        other_category = Category.objects.create(
            business=self.second_business,
            name="Clothing",
            slug="clothing",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:product-create"),
            {
                "name": "Cross-tenant Product",
                "category": str(other_category.id),
                "description": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Cross-tenant Product").exists())

    def test_cashier_cannot_create_product(self) -> None:
        client = Client()
        client.force_login(self.cashier)

        response = client.post(
            reverse("catalog:product-create"),
            {
                "name": "Unauthorized Product",
                "category": str(self.category.id),
                "description": "",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Permission denied", status_code=403)
        self.assertContains(response, reverse("dashboard"), status_code=403)
        self.assertFalse(Product.objects.filter(name="Unauthorized Product").exists())

    def test_owner_can_create_variant_with_stock_unit(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Running Shoe",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-create"),
            {
                "product": str(product.id),
                "sku": "RUN-42-BLK",
                "size": "42",
                "color": "Black",
                "selling_price": "1500",
                "cost_price": "900",
                "stock_unit": StockUnit.PAIR,
                "low_stock_threshold": "3",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("catalog:variant-list"))
        variant = ProductVariant.objects.get(sku="RUN-42-BLK")
        self.assertEqual(variant.business, self.first_business)
        self.assertEqual(variant.stock_unit, StockUnit.PAIR)

    def test_variant_create_rejects_duplicate_sku_without_server_error(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Running Shoe",
        )
        ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="RUN-42-BLK",
            selling_price=Decimal("1500"),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-create"),
            {
                "product": str(product.id),
                "sku": "run-42-blk",
                "size": "42",
                "color": "Black",
                "selling_price": "1500",
                "cost_price": "",
                "stock_unit": StockUnit.PAIR,
                "low_stock_threshold": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A product variant with this SKU already exists.")
        self.assertEqual(ProductVariant.objects.filter(business=self.first_business).count(), 1)

    def test_variant_edit_rejects_duplicate_sku_without_server_error(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Running Shoe",
        )
        ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="RUN-42-BLK",
            selling_price=Decimal("1500"),
        )
        second_variant = ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="RUN-43-BLK",
            selling_price=Decimal("1500"),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-edit", args=[second_variant.id]),
            {
                "product": str(product.id),
                "sku": "run-42-blk",
                "size": "43",
                "color": "Black",
                "selling_price": "1500",
                "cost_price": "",
                "stock_unit": StockUnit.PAIR,
                "low_stock_threshold": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "A product variant with this SKU already exists.")
        second_variant.refresh_from_db()
        self.assertEqual(second_variant.sku, "RUN-43-BLK")

    def test_variant_form_rejects_cross_business_product(self) -> None:
        other_product = Product.objects.create(
            business=self.second_business,
            name="Hidden Product",
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-create"),
            {
                "product": str(other_product.id),
                "sku": "HIDDEN-1",
                "size": "",
                "color": "",
                "selling_price": "10",
                "cost_price": "",
                "stock_unit": StockUnit.PIECE,
                "low_stock_threshold": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ProductVariant.objects.filter(sku="HIDDEN-1").exists())

    def test_posted_variant_stock_unit_is_locked_in_edit_form(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Leather Shoe",
        )
        variant = ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="LEATHER-42",
            selling_price=Decimal("2500"),
            stock_unit=StockUnit.PAIR,
        )
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("1000"),
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-edit", args=[variant.id]),
            {
                "product": str(product.id),
                "sku": variant.sku,
                "size": "",
                "color": "",
                "selling_price": "2500",
                "cost_price": "",
                "stock_unit": StockUnit.KILOGRAM,
                "low_stock_threshold": "",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("catalog:variant-list"))
        variant.refresh_from_db()
        self.assertEqual(variant.stock_unit, StockUnit.PAIR)

    def test_variant_stock_unit_is_locked_while_approved_purchase_is_open(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Canvas Shoe",
        )
        variant = ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="CANVAS-42",
            selling_price=Decimal("1800"),
            stock_unit=StockUnit.PAIR,
        )
        supplier = Supplier.objects.create(
            business=self.first_business,
            name="Shoe Supplier",
        )
        purchase = Purchase.objects.create(
            business=self.first_business,
            branch=self.branch,
            supplier=supplier,
            internal_number="PUR-OPEN-UNIT",
            purchase_date=self.branch.created_at.date(),
            created_by=self.owner_membership,
        )
        PurchaseLine.objects.create(
            business=self.first_business,
            purchase=purchase,
            variant=variant,
            ordered_quantity=Decimal("2"),
            unit_cost=Decimal("900"),
        )
        approve_purchase(actor=self.owner_membership, purchase=purchase)
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("catalog:variant-edit", args=[variant.id]),
            {
                "product": str(product.id),
                "sku": variant.sku,
                "size": "",
                "color": "",
                "selling_price": "1800",
                "cost_price": "",
                "stock_unit": StockUnit.KILOGRAM,
                "low_stock_threshold": "",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("catalog:variant-list"))
        variant.refresh_from_db()
        self.assertEqual(variant.stock_unit, StockUnit.PAIR)

    def test_cashier_variant_list_hides_reference_cost(self) -> None:
        product = Product.objects.create(
            business=self.first_business,
            name="Costed Shoe",
        )
        ProductVariant.objects.create(
            business=self.first_business,
            product=product,
            sku="COSTED-1",
            selling_price=Decimal("1500"),
            cost_price=Decimal("987.65"),
        )
        self.client.force_login(self.cashier)

        response = self.client.get(reverse("catalog:variant-list"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Reference cost")
        self.assertNotContains(response, "987.65")
