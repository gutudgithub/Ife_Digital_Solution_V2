import uuid
from decimal import Decimal
from unittest.mock import PropertyMock, patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.services import post_opening_balance
from apps.sales.models import InternalReceipt, Sale, SaleStatus
from apps.sales.services import SaleQuantity, post_sale, save_sale_draft


class SalesViewTests(TestCase):
    owner: User
    cashier: User
    stock_employee: User
    business: Business
    branch: Branch
    other_branch: Branch
    owner_membership: BusinessMembership
    cashier_membership: BusinessMembership
    variant: ProductVariant

    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="sales-view-owner@example.com",
            password="strong-test-password",
            full_name="Sales View Owner",
        )
        self.cashier = User.objects.create_user(
            email="sales-view-cashier@example.com",
            password="strong-test-password",
            full_name="Sales View Cashier",
        )
        self.stock_employee = User.objects.create_user(
            email="sales-view-stock@example.com",
            password="strong-test-password",
            full_name="Sales View Stock",
        )
        self.business = Business.objects.create(name="Sales View Shop", slug="sales-view-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.other_branch = Branch.objects.create(
            business=self.business,
            name="Second",
            code="second",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier_membership = BusinessMembership.objects.create(
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
        product = Product.objects.create(business=self.business, name="Running Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RUN-BLK-42",
            selling_price=Decimal("1800"),
            stock_unit=StockUnit.PAIR,
        )
        post_opening_balance(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("100"),
            unit_cost=Decimal("700"),
            idempotency_key=uuid.uuid4(),
        )

    def _draft(
        self,
        *,
        actor: BusinessMembership | None = None,
        branch: Branch | None = None,
    ) -> Sale:
        return save_sale_draft(
            actor=actor or self.cashier_membership,
            branch=branch or self.branch,
            sale_date=timezone.localdate(),
            quantities=[SaleQuantity(self.variant.id, Decimal("1"))],
        )

    def test_cashier_can_create_post_and_view_internal_receipt(self) -> None:
        self.client.force_login(self.cashier)
        create_response = self.client.post(
            reverse("sales:sale-create"),
            {
                "branch": str(self.branch.id),
                "sale_date": str(timezone.localdate()),
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-variant": str(self.variant.id),
                "lines-0-quantity": "2",
            },
        )

        sale = Sale.objects.get()
        self.assertRedirects(
            create_response,
            reverse("sales:sale-detail", args=[sale.id]),
        )
        self.assertEqual(sale.status, SaleStatus.DRAFT)
        post_response = self.client.post(
            reverse("sales:sale-post", args=[sale.id]),
            {
                "payment_method": "telebirr",
                "telebirr_reference": "DEMO TX 001",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        receipt = InternalReceipt.objects.get()
        self.assertRedirects(
            post_response,
            reverse("sales:receipt-detail", args=[receipt.id]),
        )
        receipt_response = self.client.get(reverse("sales:receipt-detail", args=[receipt.id]))
        self.assertContains(
            receipt_response,
            "This is an internal transaction record. It is not an official tax invoice.",
        )
        self.assertContains(receipt_response, "DEMO TX 001")
        self.assertContains(receipt_response, "ETB 3600.00")

    def test_cashier_never_sees_inventory_cost_or_value(self) -> None:
        sale = post_sale(
            actor=self.cashier_membership,
            sale=self._draft(),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.cashier)

        response = self.client.get(reverse("sales:sale-detail", args=[sale.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Assigned inventory unit cost")
        self.assertNotContains(response, "Inventory value reduction")
        self.assertNotContains(response, "700.000000")

    def test_owner_can_view_assigned_cost_without_profit_claims(self) -> None:
        sale = post_sale(
            actor=self.owner_membership,
            sale=self._draft(actor=self.owner_membership),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("sales:sale-detail", args=[sale.id]))

        self.assertContains(response, "Assigned inventory unit cost")
        self.assertContains(response, "Inventory value reduction")
        self.assertNotContains(response, "Profit")
        self.assertNotContains(response, "Margin")
        self.assertNotContains(response, "Income")

    def test_stock_employee_cannot_access_sales_or_receipts(self) -> None:
        client = Client()
        client.force_login(self.stock_employee)
        sale = self._draft()

        for url in (
            reverse("sales:sale-list"),
            reverse("sales:sale-detail", args=[sale.id]),
            reverse("sales:receipt-lookup"),
        ):
            self.assertEqual(client.get(url).status_code, 403)

    def test_platform_staff_status_alone_grants_no_sales_access(self) -> None:
        staff = User.objects.create_user(
            email="sales-platform-staff@example.com",
            password="strong-test-password",
            full_name="Sales Platform Staff",
            is_staff=True,
        )
        self.client.force_login(staff)

        self.assertEqual(self.client.get(reverse("sales:sale-list")).status_code, 403)

    def test_cashier_cannot_view_or_submit_another_branch(self) -> None:
        other_sale = self._draft(actor=self.owner_membership, branch=self.other_branch)
        self.client.force_login(self.cashier)

        with patch.object(
            BusinessMembership,
            "can_view_sale_cost",
            new_callable=PropertyMock,
            return_value=True,
        ):
            self.assertEqual(
                self.client.get(reverse("sales:sale-detail", args=[other_sale.id])).status_code,
                404,
            )
            response = self.client.post(
                reverse("sales:sale-create"),
                {
                    "branch": str(self.other_branch.id),
                    "sale_date": str(timezone.localdate()),
                    "lines-TOTAL_FORMS": "1",
                    "lines-INITIAL_FORMS": "0",
                    "lines-MIN_NUM_FORMS": "1",
                    "lines-MAX_NUM_FORMS": "1000",
                    "lines-0-variant": str(self.variant.id),
                    "lines-0-quantity": "1",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")

    def test_sale_form_rejects_cross_business_variant(self) -> None:
        other_business = Business.objects.create(name="Other View", slug="other-sales-view")
        other_product = Product.objects.create(business=other_business, name="Hidden Shoe")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="HIDDEN-SHOE",
            selling_price=Decimal("100"),
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("sales:sale-create"),
            {
                "branch": str(self.branch.id),
                "sale_date": str(timezone.localdate()),
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "1",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-variant": str(other_variant.id),
                "lines-0-quantity": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(Sale.objects.exists())

    def test_sale_form_reports_nonpositive_quantity_without_constraint_name(self) -> None:
        self.client.force_login(self.owner)

        for quantity in ("0", "-1"):
            response = self.client.post(
                reverse("sales:sale-create"),
                {
                    "branch": str(self.branch.id),
                    "sale_date": str(timezone.localdate()),
                    "lines-TOTAL_FORMS": "1",
                    "lines-INITIAL_FORMS": "0",
                    "lines-MIN_NUM_FORMS": "1",
                    "lines-MAX_NUM_FORMS": "1000",
                    "lines-0-variant": str(self.variant.id),
                    "lines-0-quantity": quantity,
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Sale quantity must be greater than zero.")
            self.assertNotContains(response, "sales_line_quantity_positive")
        self.assertFalse(Sale.objects.exists())

    def test_telebirr_error_is_accessibly_associated_with_field(self) -> None:
        sale = self._draft()
        self.client.force_login(self.cashier)

        response = self.client.post(
            reverse("sales:sale-post", args=[sale.id]),
            {
                "payment_method": "telebirr",
                "telebirr_reference": "",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, 'aria-describedby="id_telebirr_reference_errors"')
        self.assertContains(response, 'id="id_telebirr_reference_errors"')

    def test_receipt_lookup_is_tenant_and_branch_scoped(self) -> None:
        sale = post_sale(
            actor=self.cashier_membership,
            sale=self._draft(),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.cashier)

        response = self.client.get(
            reverse("sales:receipt-lookup"),
            {"receipt_number": sale.receipt.internal_number.lower()},
        )

        self.assertRedirects(
            response,
            reverse("sales:receipt-detail", args=[sale.receipt.id]),
        )
        missing = self.client.get(
            reverse("sales:receipt-lookup"),
            {"receipt_number": "RCP-OTHER-BUSINESS"},
        )
        self.assertContains(missing, "No matching internal receipt was found.")

    def test_filters_and_pagination_preserve_each_other(self) -> None:
        for _ in range(51):
            self._draft()
        self.client.force_login(self.owner)

        first_page = self.client.get(
            reverse("sales:sale-list"),
            {
                "branch": str(self.branch.id),
                "status": SaleStatus.DRAFT,
                "payment_method": "",
                "date_from": "",
                "date_to": "",
            },
        )
        second_page = self.client.get(
            reverse("sales:sale-list"),
            {
                "branch": str(self.branch.id),
                "status": SaleStatus.DRAFT,
                "page": "2",
            },
        )

        self.assertEqual(len(first_page.context["sales"]), 50)
        self.assertContains(first_page, "page=2")
        self.assertContains(first_page, f"branch={self.branch.id}")
        self.assertContains(first_page, "status=draft")
        self.assertEqual(len(second_page.context["sales"]), 1)

    def test_search_matches_sale_receipt_and_telebirr_reference(self) -> None:
        sale = post_sale(
            actor=self.cashier_membership,
            sale=self._draft(),
            payment_method="telebirr",
            telebirr_reference="SEARCH-TX-900",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner)

        for search in (
            sale.internal_number[-8:],
            sale.receipt.internal_number[-8:],
            "tx-900",
        ):
            response = self.client.get(reverse("sales:sale-list"), {"search": search})
            self.assertContains(response, sale.internal_number)

    def test_cancelled_draft_is_visible_but_cannot_be_edited_or_posted(self) -> None:
        sale = self._draft()
        self.client.force_login(self.cashier)

        response = self.client.post(
            reverse("sales:sale-cancel", args=[sale.id]),
            {"reason": "Customer left before payment."},
        )

        self.assertRedirects(response, reverse("sales:sale-detail", args=[sale.id]))
        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleStatus.CANCELLED)
        self.assertEqual(
            self.client.get(reverse("sales:sale-edit", args=[sale.id])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("sales:sale-post", args=[sale.id])).status_code,
            404,
        )
