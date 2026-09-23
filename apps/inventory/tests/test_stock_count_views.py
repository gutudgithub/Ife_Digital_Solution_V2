import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import StockCountSession
from apps.inventory.services import (
    approve_stock_count,
    post_opening_balance,
    record_stock_count_quantity,
    record_stock_count_review_evidence,
    start_stock_count,
    submit_stock_count,
)


class StockCountViewTests(TestCase):
    business: Business
    branch: Branch
    owner_user: User
    manager_user: User
    stock_user: User
    cashier_user: User
    owner: BusinessMembership
    manager: BusinessMembership
    stock_employee: BusinessMembership
    variant: ProductVariant

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Count Views", slug="count-views")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        users: dict[str, User] = {}
        memberships: dict[str, BusinessMembership] = {}
        for role in (
            MembershipRole.OWNER,
            MembershipRole.MANAGER,
            MembershipRole.STOCK_EMPLOYEE,
            MembershipRole.CASHIER,
        ):
            user = User.objects.create_user(
                email=f"count-view-{role}@example.com",
                password="strong-test-password",
                full_name=f"Count {role.title()}",
            )
            users[role] = user
            memberships[role] = BusinessMembership.objects.create(
                business=self.business,
                user=user,
                assigned_branch=self.branch,
                role=role,
            )
        self.owner_user = users[MembershipRole.OWNER]
        self.manager_user = users[MembershipRole.MANAGER]
        self.stock_user = users[MembershipRole.STOCK_EMPLOYEE]
        self.cashier_user = users[MembershipRole.CASHIER]
        self.owner = memberships[MembershipRole.OWNER]
        self.manager = memberships[MembershipRole.MANAGER]
        self.stock_employee = memberships[MembershipRole.STOCK_EMPLOYEE]
        product = Product.objects.create(business=self.business, name="Count Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="COUNT-SHOE-42",
            size="42",
            color="Black",
            selling_price=Decimal("1200.00"),
            stock_unit=StockUnit.PAIR,
        )
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("37"),
            unit_cost=Decimal("412.345678"),
            idempotency_key=uuid.uuid4(),
        )

    def _start(self) -> StockCountSession:
        return start_stock_count(
            actor=self.owner,
            branch=self.branch,
            count_method_note="Two people count every shelf and cross-check totals.",
            idempotency_key=uuid.uuid4(),
        )

    def _submit_with_variance(self) -> StockCountSession:
        session = self._start()
        line = session.lines.get()
        record_stock_count_quantity(
            actor=self.stock_employee,
            line=line,
            physical_quantity=Decimal("36"),
        )
        submit_stock_count(actor=self.stock_employee, session=session)
        return session

    def test_owner_can_start_count_through_confirming_form(self) -> None:
        self.client.force_login(self.owner_user)
        response = self.client.post(
            reverse("inventory:stock-count-start"),
            {
                "branch": str(self.branch.id),
                "count_method_note": "Count the complete store with two counters.",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        session = StockCountSession.objects.get()
        self.assertRedirects(
            response,
            reverse("inventory:stock-count-worksheet", args=[session.id]),
        )
        self.assertEqual(session.lines.count(), 1)

    def test_stock_employee_worksheet_is_blind_and_accepts_explicit_zero(self) -> None:
        session = self._start()
        line = session.lines.get()
        self.client.force_login(self.stock_user)

        page = self.client.get(reverse("inventory:stock-count-worksheet", args=[session.id]))

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "412.345678")
        self.assertNotContains(page, "37.000")
        self.assertNotContains(page, "ETB")
        response = self.client.post(
            reverse("inventory:stock-count-worksheet", args=[session.id]),
            {
                "line_id": str(line.id),
                f"line-{line.id}-physical_quantity": "0",
                f"line-{line.id}-replacement_reason": "",
                "q": "COUNT-SHOE",
                "page": "1",
            },
        )
        self.assertRedirects(
            response,
            f"{reverse('inventory:stock-count-worksheet', args=[session.id])}?q=COUNT-SHOE&page=1",
        )
        line.refresh_from_db()
        self.assertEqual(line.physical_quantity, Decimal("0.000"))

    def test_invalid_blind_quantity_keeps_field_error_and_accessibility_state(self) -> None:
        session = self._start()
        line = session.lines.get()
        self.client.force_login(self.stock_user)

        for quantity, message in (
            ("-1", "Physical quantity cannot be negative."),
            ("1.5", "This stock unit requires a whole-number quantity."),
        ):
            with self.subTest(quantity=quantity):
                response = self.client.post(
                    reverse("inventory:stock-count-worksheet", args=[session.id]),
                    {
                        "line_id": str(line.id),
                        f"line-{line.id}-physical_quantity": quantity,
                        f"line-{line.id}-replacement_reason": "",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)
                self.assertContains(response, 'aria-invalid="true"')

    def test_worksheet_pagination_preserves_search_filter(self) -> None:
        for index in range(25):
            ProductVariant.objects.create(
                business=self.business,
                product=self.variant.product,
                sku=f"COUNT-PAGE-{index:02d}",
                selling_price=Decimal("10.00"),
                stock_unit=StockUnit.PIECE,
            )
        session = self._start()
        self.client.force_login(self.stock_user)

        response = self.client.get(
            reverse("inventory:stock-count-worksheet", args=[session.id]),
            {"q": "COUNT", "page": "1"},
        )

        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "q=COUNT")
        self.assertContains(response, "page=2")

    def test_review_requires_explanation_and_preserves_bound_form_error(self) -> None:
        session = self._submit_with_variance()
        line = session.lines.get()
        self.client.force_login(self.manager_user)

        response = self.client.post(
            reverse("inventory:stock-count-review", args=[session.id]),
            {
                "line_id": str(line.id),
                f"review-{line.id}-variance_explanation": "",
                f"review-{line.id}-exceptional_unit_cost": "",
                f"review-{line.id}-exceptional_cost_evidence_note": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Explain every nonzero stock count quantity variance.",
        )
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(response, "412.345678")
        self.assertContains(response, "Calculated inventory-value adjustment")

    def test_print_evidence_has_disclaimers_and_inventory_links_to_count(self) -> None:
        session = self._submit_with_variance()
        line = session.lines.get()
        record_stock_count_review_evidence(
            actor=self.manager,
            line=line,
            variance_explanation="One pair could not be located during the complete recount.",
        )
        approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner_user)

        print_response = self.client.get(reverse("inventory:stock-count-print", args=[session.id]))
        movement_response = self.client.get(reverse("inventory:inventory-list"))

        self.assertContains(print_response, "Internal operational evidence")
        self.assertContains(print_response, "not an official tax document")
        self.assertContains(print_response, "audited stock certificate")
        self.assertContains(print_response, "proof of theft or loss")
        self.assertContains(print_response, "Pair")
        self.assertContains(
            movement_response,
            reverse("inventory:stock-count-detail", args=[session.id]),
        )

    def test_roles_and_tenant_scope_protect_stock_count_evidence(self) -> None:
        session = self._start()
        other_business = Business.objects.create(name="Other Count", slug="other-count")
        other_branch = Branch.objects.create(
            business=other_business,
            name="Other",
            code="other",
        )
        other_user = User.objects.create_user(
            email="other-count-owner@example.com",
            password="strong-test-password",
        )
        BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            assigned_branch=other_branch,
            role=MembershipRole.OWNER,
        )

        cashier_client = Client()
        cashier_client.force_login(self.cashier_user)
        self.assertEqual(
            cashier_client.get(reverse("inventory:stock-count-list")).status_code,
            403,
        )
        other_client = Client()
        other_client.force_login(other_user)
        self.assertEqual(
            other_client.get(
                reverse("inventory:stock-count-detail", args=[session.id])
            ).status_code,
            404,
        )
