import uuid
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovementType
from apps.cash.services import open_cash_session
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.expenses.models import ExpenseCategory, ExpenseStatus, OperatingExpense
from apps.expenses.services import (
    create_operating_expense_draft,
    post_operating_expense,
)
from apps.purchasing.models import Purchase, PurchaseLine, Supplier
from apps.purchasing.services import approve_purchase


class ExpenseViewTests(TestCase):
    business: Business
    branch: Branch
    owner_user: User
    cashier_user: User
    stock_user: User
    owner: BusinessMembership
    category: ExpenseCategory
    purchase: Purchase

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Expense Views", slug="expense-views")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_user = User.objects.create_user(
            email="expense-view-owner@example.com",
            password="strong-test-password",
        )
        self.cashier_user = User.objects.create_user(
            email="expense-view-cashier@example.com",
            password="strong-test-password",
        )
        self.stock_user = User.objects.create_user(
            email="expense-view-stock@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=self.owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        BusinessMembership.objects.create(
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
        self.category = ExpenseCategory.objects.create(
            business=self.business,
            name="Utilities",
        )
        supplier = Supplier.objects.create(
            business=self.business,
            name="View Supplier",
        )
        product = Product.objects.create(
            business=self.business,
            name="View Shirt",
        )
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="VIEW-SHIRT-M",
            size="M",
            color="Blue",
            selling_price=Decimal("700.00"),
            stock_unit=StockUnit.PIECE,
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=supplier,
            internal_number="PUR-VIEW-4B",
            purchase_date=timezone.localdate(),
            created_by=self.owner,
        )
        PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=variant,
            ordered_quantity=Decimal("2.000"),
            unit_cost=Decimal("300.00"),
        )
        approve_purchase(actor=self.owner, purchase=self.purchase)

    def test_category_duplicate_is_a_field_error_and_inactive_history_remains_visible(
        self,
    ) -> None:
        self.client.force_login(self.owner_user)
        duplicate = self.client.post(
            reverse("expenses:category-create"),
            {"name": " utilities "},
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(duplicate, "An expense category with this name already exists.")
        self.assertContains(duplicate, 'aria-invalid="true"')

        expense = create_operating_expense_draft(
            actor=self.owner,
            branch=self.branch,
            category=self.category,
            payee="Utility office",
            description="Monthly electricity",
            amount=Decimal("100.00"),
        )
        self.client.post(
            reverse("expenses:category-toggle", args=[self.category.id]),
        )
        self.category.refresh_from_db()
        self.assertFalse(self.category.is_active)
        detail = self.client.get(reverse("expenses:expense-detail", args=[expense.id]))
        self.assertContains(detail, "Utilities")

    def test_owner_can_create_post_and_print_telebirr_expense(self) -> None:
        self.client.force_login(self.owner_user)
        create_response = self.client.post(
            reverse("expenses:expense-create"),
            {
                "branch": str(self.branch.id),
                "category": str(self.category.id),
                "payee": "Internet provider",
                "description": "Branch internet service",
                "amount": "450.00",
            },
        )
        expense = OperatingExpense.objects.get()
        self.assertRedirects(
            create_response,
            reverse("expenses:expense-detail", args=[expense.id]),
        )
        self.assertIsNone(expense.business_date)

        post_response = self.client.post(
            reverse("expenses:expense-post", args=[expense.id]),
            {
                "method": "telebirr",
                "telebirr_reference": "TX-VIEW-001",
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        expense.refresh_from_db()
        self.assertRedirects(
            post_response,
            reverse("expenses:expense-detail", args=[expense.id]),
        )
        self.assertEqual(expense.status, ExpenseStatus.POSTED)
        self.assertIsNotNone(expense.business_date)

        print_response = self.client.get(reverse("expenses:expense-print", args=[expense.id]))
        self.assertContains(print_response, "Internal operating expense evidence")
        self.assertContains(print_response, "Not an official tax document")
        self.assertContains(print_response, "TX-VIEW-001")

        stale_cancel = self.client.post(
            reverse("expenses:expense-cancel", args=[expense.id]),
            follow=True,
        )
        self.assertRedirects(
            stale_cancel,
            reverse("expenses:expense-detail", args=[expense.id]),
        )
        self.assertContains(stale_cancel, "Only draft operating expenses can be cancelled.")

    def test_expense_filters_paginate_and_preserve_query_parameters(self) -> None:
        for index in range(51):
            create_operating_expense_draft(
                actor=self.owner,
                branch=self.branch,
                category=self.category,
                payee=f"Payee {index:02}",
                description=f"Expense evidence {index:02}",
                amount=Decimal("1.00"),
            )
        self.client.force_login(self.owner_user)

        response = self.client.get(
            reverse("expenses:expense-list"),
            {
                "branch": str(self.branch.id),
                "status": ExpenseStatus.DRAFT,
                "search": "Expense evidence",
            },
        )
        self.assertEqual(len(response.context["expenses"]), 50)
        self.assertContains(response, "page=2")
        self.assertContains(response, f"branch={self.branch.id}")
        self.assertContains(response, "status=draft")
        self.assertContains(response, "search=Expense+evidence")

    def test_cashier_stock_employee_and_platform_staff_have_no_expense_access(
        self,
    ) -> None:
        platform_staff = User.objects.create_user(
            email="expense-platform-staff@example.com",
            password="strong-test-password",
            is_staff=True,
        )
        for user in (self.cashier_user, self.stock_user, platform_staff):
            client = Client()
            client.force_login(user)
            self.assertEqual(
                client.get(reverse("expenses:expense-list")).status_code,
                403,
            )

    def test_cashier_cash_view_hides_expense_details(self) -> None:
        session = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("100.00"),
            opening_basis_note="Direct physical count",
            idempotency_key=uuid.uuid4(),
        )
        expense = create_operating_expense_draft(
            actor=self.owner,
            branch=self.branch,
            category=self.category,
            payee="Sensitive payee",
            description="Sensitive expense details",
            amount=Decimal("10.00"),
        )
        post_operating_expense(
            actor=self.owner,
            expense=expense,
            method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.client.force_login(self.cashier_user)

        response = self.client.get(reverse("cash:session-detail", args=[session.id]))

        self.assertContains(response, "Restricted operational evidence")
        self.assertContains(response, CashMovementType.OPERATING_EXPENSE.label)
        self.assertNotContains(response, "Sensitive payee")
        self.assertNotContains(response, "Sensitive expense details")

    def test_purchase_detail_shows_operational_settlement_summary_to_owner(self) -> None:
        self.client.force_login(self.owner_user)

        response = self.client.get(reverse("purchasing:purchase-detail", args=[self.purchase.id]))

        self.assertContains(response, "Operational settlement summary")
        self.assertContains(response, "Record supplier payment")
        self.assertContains(response, "not a certified payable")

        payment_form = self.client.get(
            reverse("expenses:supplier-payment-create", args=[self.purchase.id])
        )
        self.assertContains(payment_form, "Gross purchase reference")
        self.assertContains(payment_form, "Remaining operational reference balance")
        self.assertNotContains(payment_form, "gross_purchase_reference")

    def test_other_business_expense_is_not_visible(self) -> None:
        other_business = Business.objects.create(name="Other Shop", slug="other-shop")
        other_branch = Branch.objects.create(
            business=other_business,
            name="Other",
            code="other",
        )
        other_user = User.objects.create_user(
            email="other-expense-owner@example.com",
            password="strong-test-password",
        )
        other_owner = BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            assigned_branch=other_branch,
            role=MembershipRole.OWNER,
        )
        other_category = ExpenseCategory.objects.create(
            business=other_business,
            name="Utilities",
        )
        other_expense = create_operating_expense_draft(
            actor=other_owner,
            branch=other_branch,
            category=other_category,
            payee="Other payee",
            description="Other expense",
            amount=Decimal("10.00"),
        )
        self.client.force_login(self.owner_user)

        self.assertEqual(
            self.client.get(
                reverse("expenses:expense-detail", args=[other_expense.id])
            ).status_code,
            404,
        )
