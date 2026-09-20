import uuid
from datetime import date
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.services import post_opening_balance
from apps.sales.models import SalePaymentMethod
from apps.sales.services import SaleQuantity, post_sale, save_sale_draft


class PerformanceViewTests(TestCase):
    password = "strong-test-password"
    business: Business
    branch: Branch
    owner_user: User
    manager_user: User
    cashier_user: User
    stock_user: User
    inactive_user: User
    owner: BusinessMembership

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Performance View Shop",
            slug="performance-view-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_user = self._user("owner")
        self.manager_user = self._user("manager")
        self.cashier_user = self._user("cashier")
        self.stock_user = self._user("stock")
        self.inactive_user = self._user("inactive")
        self.owner = self._membership(self.owner_user, MembershipRole.OWNER)
        self._membership(self.manager_user, MembershipRole.MANAGER)
        self._membership(self.cashier_user, MembershipRole.CASHIER)
        self._membership(self.stock_user, MembershipRole.STOCK_EMPLOYEE)
        self._membership(self.inactive_user, MembershipRole.MANAGER, is_active=False)
        product = Product.objects.create(business=self.business, name="Leather Shoe")
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BRN-42",
            size="42",
            color="Brown",
            selling_price=Decimal("1500.00"),
            stock_unit=StockUnit.PAIR,
        )
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=variant,
            quantity=Decimal("10.000"),
            unit_cost=Decimal("500.123456"),
            idempotency_key=uuid.uuid4(),
        )
        sale = save_sale_draft(
            actor=self.owner,
            branch=self.branch,
            sale_date=date(2026, 9, 10),
            quantities=[SaleQuantity(variant.id, Decimal("1.000"))],
        )
        post_sale(
            actor=self.owner,
            sale=sale,
            payment_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference="PERFORMANCE-VIEW-SALE",
            idempotency_key=uuid.uuid4(),
        )

    def _user(self, label: str) -> User:
        return User.objects.create_user(
            email=f"performance-{label}@example.com",
            password=self.password,
        )

    def _membership(
        self,
        user: User,
        role: str,
        *,
        is_active: bool = True,
    ) -> BusinessMembership:
        return BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=role,
            is_active=is_active,
        )

    def _url(self, name: str = "performance:dashboard", **extra: str) -> str:
        query = {
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "bucket": "daily",
            "product_order": "net_sales",
            **extra,
        }
        return f"{reverse(name)}?" + "&".join(f"{key}={value}" for key, value in query.items())

    def test_owner_and_manager_can_view_report(self) -> None:
        for user in (self.owner_user, self.manager_user):
            with self.subTest(user=user.email):
                client = Client()
                client.force_login(user)
                response = client.get(self._url())
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Performance intelligence")
                self.assertContains(response, "Operational net result")
                self.assertContains(response, "Exact time-series values")
                self.assertContains(response, "Gross sales (ETB)")
                self.assertContains(response, "Return deduction (ETB)")
                self.assertContains(response, "<svg", html=False)
                self.assertContains(response, "text alternative")
                self.assertContains(response, "not statutory net profit")

    def test_cashier_stock_inactive_and_unrelated_staff_are_denied(self) -> None:
        staff = User.objects.create_user(
            email="unrelated-staff@example.com",
            password=self.password,
            is_staff=True,
        )
        for user in (
            self.cashier_user,
            self.stock_user,
            self.inactive_user,
            staff,
        ):
            with self.subTest(user=user.email):
                client = Client()
                client.force_login(user)
                response = client.get(self._url())
                self.assertEqual(response.status_code, 403)
                self.assertNotContains(
                    response,
                    "Assigned cost",
                    status_code=403,
                )

    def test_navigation_does_not_leak_performance_to_cashier(self) -> None:
        client = Client()
        client.force_login(self.cashier_user)
        response = client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("performance:dashboard"))

    def test_invalid_range_is_accessible_and_does_not_generate_report(self) -> None:
        self.client.force_login(self.owner_user)
        response = self.client.get(self._url(start_date="2026-10-01", end_date="2026-09-01"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "End date cannot be earlier")
        self.assertContains(response, 'aria-invalid="true"', html=False)
        self.assertNotContains(response, "Performance summary")

    def test_cross_business_branch_is_rejected_by_filter(self) -> None:
        other_business = Business.objects.create(name="Other", slug="other-view-shop")
        foreign_branch = Branch.objects.create(
            business=other_business,
            name="Foreign",
            code="foreign",
        )
        self.client.force_login(self.owner_user)
        response = self.client.get(self._url(branch=str(foreign_branch.id)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")

    def test_csv_exports_use_permission_parity_and_exact_decimal_headers(self) -> None:
        self.client.force_login(self.owner_user)
        response = self.client.get(self._url("performance:time-series-csv"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        content = response.content.decode("utf-8-sig")
        self.assertIn("operational_net_result_etb", content)
        self.assertIn("500.123456", content)

        response = self.client.get(self._url("performance:products-csv"))
        self.assertEqual(response.status_code, 200)
        product_content = response.content.decode("utf-8-sig")
        self.assertIn("net_assigned_inventory_cost_etb", product_content)
        self.assertIn("500.123456", product_content)

        client = Client()
        client.force_login(self.cashier_user)
        denied = client.get(self._url("performance:products-csv"))
        self.assertEqual(denied.status_code, 403)
        self.assertNotContains(denied, "assigned_inventory", status_code=403)

    def test_print_output_uses_same_values_and_reporting_boundary(self) -> None:
        self.client.force_login(self.owner_user)
        dashboard = self.client.get(self._url())
        printed = self.client.get(self._url("performance:print"))

        self.assertEqual(printed.status_code, 200)
        self.assertContains(printed, "1500.00")
        self.assertContains(printed, "Operational controls")
        self.assertContains(printed, "not statutory net profit")
        self.assertEqual(
            dashboard.context["report"].metrics,
            printed.context["report"].metrics,
        )

    def test_empty_state_and_366_day_validation(self) -> None:
        self.client.force_login(self.owner_user)
        empty = self.client.get(self._url(start_date="2025-01-01", end_date="2025-01-01"))
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "No matching posted product activity")

        too_long = self.client.get(self._url(start_date="2024-01-01", end_date="2025-01-01"))
        self.assertEqual(too_long.status_code, 200)
        self.assertContains(too_long, "limited to 366 days")

    def test_product_pagination_preserves_filters(self) -> None:
        quantities: list[SaleQuantity] = []
        for number in range(50):
            product = Product.objects.create(
                business=self.business,
                name=f"Leather Shoe {number:02d}",
            )
            variant = ProductVariant.objects.create(
                business=self.business,
                product=product,
                sku=f"SHOE-PAGE-{number:02d}",
                size=str(30 + number),
                color="Black",
                selling_price=Decimal("100.00"),
                stock_unit=StockUnit.PAIR,
            )
            post_opening_balance(
                actor=self.owner,
                branch=self.branch,
                variant=variant,
                quantity=Decimal("1.000"),
                unit_cost=Decimal("40.000000"),
                idempotency_key=uuid.uuid4(),
            )
            quantities.append(SaleQuantity(variant.id, Decimal("1.000")))
        sale = save_sale_draft(
            actor=self.owner,
            branch=self.branch,
            sale_date=date(2026, 9, 10),
            quantities=quantities,
        )
        post_sale(
            actor=self.owner,
            sale=sale,
            payment_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference="PERFORMANCE-PAGINATION",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner_user)

        response = self.client.get(self._url(product_search="shoe", product_order="sku", page="2"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 2 of 2")
        self.assertContains(response, "product_search=shoe")
        self.assertContains(response, "product_order=sku")
