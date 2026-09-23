import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovementType, CashSession, CashSessionStatus
from apps.cash.services import (
    close_cash_session,
    open_cash_session,
    record_cash_sale,
    reopen_cash_session,
)


class CashViewTests(TestCase):
    business: Business
    branch: Branch
    other_branch: Branch
    owner_user: User
    cashier_user: User
    stock_user: User
    owner: BusinessMembership
    cashier: BusinessMembership

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Cash Views", slug="cash-views")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.other_branch = Branch.objects.create(
            business=self.business,
            name="Other",
            code="other",
        )
        self.owner_user = User.objects.create_user(
            email="cash-view-owner@example.com",
            password="strong-test-password",
            full_name="Cash Owner",
        )
        self.cashier_user = User.objects.create_user(
            email="cash-view-cashier@example.com",
            password="strong-test-password",
            full_name="Cash Cashier",
        )
        self.stock_user = User.objects.create_user(
            email="cash-view-stock@example.com",
            password="strong-test-password",
            full_name="Cash Stock",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=self.owner_user,
            role=MembershipRole.OWNER,
        )
        self.cashier = BusinessMembership.objects.create(
            business=self.business,
            user=self.cashier_user,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.stock_user,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )

    def test_owner_can_open_post_manual_close_print_and_reopen(self) -> None:
        self.client.force_login(self.owner_user)
        open_response = self.client.post(
            reverse("cash:session-open"),
            {
                "branch": str(self.branch.id),
                "opening_float": "100.00",
                "opening_basis_note": "Direct physical drawer count",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        session = CashSession.objects.get()
        self.assertRedirects(
            open_response,
            reverse("cash:session-detail", args=[session.id]),
        )
        movement_response = self.client.post(
            reverse("cash:manual-movement", args=[session.id]),
            {
                "movement_type": CashMovementType.CASH_ADDED,
                "amount": "25.00",
                "reason": "Added change for the drawer",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertRedirects(
            movement_response,
            reverse("cash:session-detail", args=[session.id]),
        )

        invalid_close = self.client.post(
            reverse("cash:session-close", args=[session.id]),
            {
                "actual_cash": "120.00",
                "explanation": "",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertEqual(invalid_close.status_code, 200)
        self.assertContains(invalid_close, "Explain every nonzero cash variance.")
        self.assertContains(invalid_close, 'aria-invalid="true"')
        self.assertContains(invalid_close, 'aria-describedby="id_explanation_errors"')

        close_response = self.client.post(
            reverse("cash:session-close", args=[session.id]),
            {
                "actual_cash": "120.00",
                "explanation": "Five birr short after the physical count",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        closure = session.closures.get()
        self.assertRedirects(
            close_response,
            reverse("cash:closure-report", args=[closure.id]),
        )
        report = self.client.get(reverse("cash:closure-report", args=[closure.id]))
        self.assertContains(report, "Expected drawer cash")
        self.assertContains(report, "ETB 125.00")
        self.assertContains(report, "ETB -5.00")
        self.assertContains(report, "not an accounting statement")

        reopen_response = self.client.post(
            reverse("cash:session-reopen", args=[session.id]),
            {
                "reason": "Manager approved a recount",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        self.assertRedirects(
            reopen_response,
            reverse("cash:session-detail", args=[session.id]),
        )
        session.refresh_from_db()
        self.assertEqual(session.status, CashSessionStatus.OPEN)

    def test_cashier_sees_assigned_branch_and_cannot_manage_manual_movements(self) -> None:
        other_session = open_cash_session(
            actor=self.owner,
            branch=self.other_branch,
            opening_float=Decimal("10.00"),
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.cashier_user)

        open_page = self.client.get(reverse("cash:session-open"))
        self.assertContains(open_page, self.branch.name)
        self.assertNotContains(open_page, self.other_branch.name)
        self.assertEqual(
            self.client.get(reverse("cash:session-detail", args=[other_session.id])).status_code,
            404,
        )
        assigned_session = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(
            self.client.get(
                reverse("cash:manual-movement", args=[assigned_session.id])
            ).status_code,
            403,
        )

    def test_list_and_detail_show_cash_reconciliation_without_inventory_costs(
        self,
    ) -> None:
        session = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("50.00"),
            idempotency_key=uuid.uuid4(),
        )
        record_cash_sale(
            actor=self.cashier,
            session=session,
            amount=Decimal("200.00"),
            source_id=uuid.uuid4(),
            posted_at=session.opened_at,
        )
        self.client.force_login(self.cashier_user)

        list_response = self.client.get(
            reverse("cash:session-list"),
            {
                "branch": str(self.branch.id),
                "status": CashSessionStatus.OPEN,
            },
        )
        self.assertContains(list_response, "ETB 250.00")
        self.assertContains(
            list_response,
            f'value="{self.branch.id}" selected',
        )
        detail_response = self.client.get(reverse("cash:session-detail", args=[session.id]))
        self.assertContains(detail_response, "Cash sale")
        self.assertContains(detail_response, "ETB 200.00")
        self.assertNotContains(detail_response, "Assigned inventory unit cost")
        self.assertNotContains(detail_response, "Inventory value")
        self.assertNotContains(detail_response, "Profit")
        self.assertNotContains(detail_response, "Margin")

    def test_stock_employee_and_platform_staff_have_no_cash_access(self) -> None:
        staff_user = User.objects.create_user(
            email="cash-platform-staff@example.com",
            password="strong-test-password",
            is_staff=True,
        )
        for user in (self.stock_user, staff_user):
            client = Client()
            client.force_login(user)
            self.assertEqual(client.get(reverse("cash:session-list")).status_code, 403)

    def test_session_list_is_paginated_and_preserves_filters(self) -> None:
        open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=uuid.uuid4(),
        )
        for index in range(50):
            branch = Branch.objects.create(
                business=self.business,
                name=f"Pagination Branch {index:02}",
                code=f"page-{index:02}",
            )
            open_cash_session(
                actor=self.owner,
                branch=branch,
                opening_float=Decimal("0.00"),
                idempotency_key=uuid.uuid4(),
            )
        self.client.force_login(self.owner_user)

        response = self.client.get(
            reverse("cash:session-list"),
            {"status": str(CashSessionStatus.OPEN), "page": "2"},
        )

        self.assertContains(response, "Page 2 of 2")
        self.assertContains(response, "status=open")

    def test_session_list_shows_latest_closure_after_recount(self) -> None:
        session = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("100.00"),
            idempotency_key=uuid.uuid4(),
        )
        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=Decimal("90.00"),
            explanation="Initial count was ten birr short",
            idempotency_key=uuid.uuid4(),
        )
        reopen_cash_session(
            actor=self.owner,
            session=session,
            reason="Recount approved",
            idempotency_key=uuid.uuid4(),
        )
        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=Decimal("100.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.owner_user)

        response = self.client.get(reverse("cash:session-list"))

        self.assertContains(response, "ETB 100.00", count=3)
        self.assertNotContains(response, "ETB -10.00")
