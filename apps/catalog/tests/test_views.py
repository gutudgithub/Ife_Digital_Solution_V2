from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership, MembershipRole
from apps.catalog.models import Category, Product


class ProductViewTests(TestCase):
    owner: User
    cashier: User
    first_business: Business
    second_business: Business
    category: Category

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
        BusinessMembership.objects.create(
            business=self.first_business,
            user=self.owner,
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
