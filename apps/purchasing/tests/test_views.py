import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant
from apps.inventory.models import InventoryBalance
from apps.inventory.services import post_opening_balance
from apps.purchasing.models import Purchase, PurchaseLine, Supplier
from apps.purchasing.services import approve_purchase


class StageTwoViewTests(TestCase):
    owner: User
    cashier: User
    stock_employee: User
    business: Business
    branch: Branch
    owner_membership: BusinessMembership
    supplier: Supplier
    purchase: Purchase
    variant: ProductVariant

    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="view-owner@example.com",
            password="strong-test-password",
            full_name="View Owner",
        )
        self.cashier = User.objects.create_user(
            email="view-cashier@example.com",
            password="strong-test-password",
            full_name="View Cashier",
        )
        self.stock_employee = User.objects.create_user(
            email="view-stock@example.com",
            password="strong-test-password",
            full_name="View Stock",
        )
        self.business = Business.objects.create(name="View Shop", slug="view-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        self.supplier = Supplier.objects.create(
            business=self.business,
            name="Private Supplier",
            phone="+251900000000",
        )
        product = Product.objects.create(business=self.business, name="Dress Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="DRESS-42",
            selling_price=Decimal("1700"),
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-VIEW",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=self.variant,
            ordered_quantity=Decimal("2"),
            unit_cost=Decimal("700"),
        )

    def test_cashier_cannot_access_supplier_or_purchase_cost_pages(self) -> None:
        client = Client()
        client.force_login(self.cashier)

        supplier_response = client.get(reverse("purchasing:supplier-list"))
        purchase_response = client.get(reverse("purchasing:purchase-list"))

        self.assertEqual(supplier_response.status_code, 403)
        self.assertEqual(purchase_response.status_code, 403)

    def test_stock_employee_can_view_purchases_but_cannot_create_or_approve(self) -> None:
        client = Client()
        client.force_login(self.stock_employee)

        list_response = client.get(reverse("purchasing:purchase-list"))

        self.assertEqual(list_response.status_code, 200)
        self.assertNotContains(list_response, self.purchase.internal_number)
        self.assertEqual(
            client.get(reverse("purchasing:purchase-detail", args=[self.purchase.id])).status_code,
            404,
        )
        self.assertEqual(client.get(reverse("purchasing:purchase-create")).status_code, 403)
        self.assertEqual(
            client.post(
                reverse("purchasing:purchase-approve", args=[self.purchase.id])
            ).status_code,
            403,
        )
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

        approved_list_response = client.get(reverse("purchasing:purchase-list"))

        self.assertContains(approved_list_response, self.purchase.internal_number)
        self.assertEqual(
            client.get(reverse("purchasing:purchase-detail", args=[self.purchase.id])).status_code,
            200,
        )

    def test_cross_business_purchase_url_returns_not_found(self) -> None:
        other_business = Business.objects.create(name="Hidden", slug="hidden")
        other_branch = Branch.objects.create(
            business=other_business,
            name="Hidden",
            code="hidden",
        )
        other_supplier = Supplier.objects.create(business=other_business, name="Hidden Supplier")
        other_user = User.objects.create_user(
            email="hidden-owner@example.com",
            password="strong-test-password",
            full_name="Hidden Owner",
        )
        other_membership = BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            role=MembershipRole.OWNER,
        )
        hidden_purchase = Purchase.objects.create(
            business=other_business,
            branch=other_branch,
            supplier=other_supplier,
            internal_number="PUR-HIDDEN",
            purchase_date=timezone.localdate(),
            created_by=other_membership,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("purchasing:purchase-detail", args=[hidden_purchase.id]))

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "PUR-HIDDEN", status_code=404)

    def test_cashier_inventory_page_hides_cost_and_value(self) -> None:
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("700"),
            idempotency_key=uuid.uuid4(),
        )
        client = Client()
        client.force_login(self.cashier)

        response = client.get(reverse("inventory:inventory-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2.000")
        self.assertNotContains(response, "Average cost")
        self.assertNotContains(response, "Inventory value")
        self.assertNotContains(response, "700.00")
        self.assertNotContains(response, "Recent movements")
        self.assertNotContains(response, self.owner.full_name)

    def test_inventory_balances_are_paginated(self) -> None:
        for index in range(51):
            variant = ProductVariant.objects.create(
                business=self.business,
                product=self.variant.product,
                sku=f"PAGE-{index:02d}",
                selling_price=Decimal("100"),
            )
            InventoryBalance.objects.create(
                business=self.business,
                branch=self.branch,
                variant=variant,
            )
        self.client.force_login(self.owner)

        first_page = self.client.get(reverse("inventory:inventory-list"))
        second_page = self.client.get(
            reverse("inventory:inventory-list"),
            {"page": "2"},
        )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context["balances"]), 50)
        self.assertContains(first_page, "?page=2")
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.context["balances"]), 1)

    def test_owner_can_post_opening_balance_from_scoped_form(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("inventory:opening-create"),
            {
                "branch": str(self.branch.id),
                "variant": str(self.variant.id),
                "quantity": "3",
                "unit_cost": "650",
                "idempotency_key": "b0b50e69-420e-4420-b896-854665127371",
            },
        )

        self.assertRedirects(response, reverse("inventory:inventory-list"))
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.variant).quantity_on_hand,
            Decimal("3.000"),
        )

    def test_inventory_form_rejects_cross_business_variant(self) -> None:
        other_business = Business.objects.create(name="Other Form", slug="other-form")
        other_product = Product.objects.create(business=other_business, name="Other Shoe")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="OTHER-VIEW",
            selling_price=Decimal("100"),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("inventory:opening-create"),
            {
                "branch": str(self.branch.id),
                "variant": str(other_variant.id),
                "quantity": "1",
                "unit_cost": "50",
                "idempotency_key": "00577250-a8bb-433e-b21d-27f720735b9b",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(InventoryBalance.objects.exists())

    def test_owner_create_approve_and_stock_employee_receive_workflow(self) -> None:
        self.client.force_login(self.owner)
        create_response = self.client.post(
            reverse("purchasing:purchase-create"),
            {
                "branch": str(self.branch.id),
                "supplier": str(self.supplier.id),
                "purchase_date": str(timezone.localdate()),
                "expected_date": "",
                "supplier_reference": "SUP-INV-42",
                "settlement_terms": "Settlement tracked outside Stage 2A",
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-variant": str(self.variant.id),
                "lines-0-ordered_quantity": "2",
                "lines-0-unit_cost": "700",
            },
        )

        self.assertEqual(create_response.status_code, 302)
        created_purchase = Purchase.objects.exclude(pk=self.purchase.pk).get()
        self.assertRedirects(
            create_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        approve_response = self.client.post(
            reverse("purchasing:purchase-approve", args=[created_purchase.id])
        )
        self.assertRedirects(
            approve_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        created_line = created_purchase.lines.get()
        stock_client = Client()
        stock_client.force_login(self.stock_employee)

        receive_response = stock_client.post(
            reverse("purchasing:purchase-receive", args=[created_purchase.id]),
            {
                "idempotency_key": "468414fd-6c78-4639-9335-6fd93e5b9113",
                "supplier_document_reference": "DELIVERY-42",
                f"quantity_{created_line.id.hex}": "2",
            },
        )

        self.assertRedirects(
            receive_response,
            reverse("purchasing:purchase-detail", args=[created_purchase.id]),
        )
        created_purchase.refresh_from_db()
        self.assertEqual(created_purchase.status, "received")
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.variant).quantity_on_hand,
            Decimal("2.000"),
        )
