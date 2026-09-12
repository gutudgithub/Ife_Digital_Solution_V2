import uuid
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.expenses.models import (
    ExpenseCategory,
    OperatingExpense,
    OperatingExpensePayment,
    OperationalPaymentMethod,
    SupplierPayment,
)
from apps.expenses.services import (
    create_operating_expense_draft,
    post_operating_expense,
    post_supplier_payment,
)
from apps.purchasing.models import Purchase, PurchaseLine, Supplier
from apps.purchasing.services import approve_purchase


@skipUnlessDBFeature("has_select_for_update")
class ExpenseSettlementConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    business: Business
    branch: Branch
    owner: BusinessMembership
    category: ExpenseCategory
    purchase: Purchase

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Concurrent Stage 4B Shop",
            slug="concurrent-stage-4b-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        owner_user = User.objects.create_user(
            email="concurrent-stage4b-owner@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.category = ExpenseCategory.objects.create(
            business=self.business,
            name="Utilities",
        )
        supplier = Supplier.objects.create(
            business=self.business,
            name="Concurrent Supplier",
        )
        product = Product.objects.create(
            business=self.business,
            name="Concurrent Shoe",
        )
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CONCURRENT-SHOE-42",
            size="42",
            color="Black",
            selling_price=Decimal("1200.00"),
            stock_unit=StockUnit.PAIR,
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=supplier,
            internal_number="PUR-CONCURRENT-4B",
            purchase_date=timezone.localdate(),
            created_by=self.owner,
        )
        PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=variant,
            ordered_quantity=Decimal("10.000"),
            unit_cost=Decimal("500.00"),
        )
        approve_purchase(actor=self.owner, purchase=self.purchase)

    def test_concurrent_supplier_payments_cannot_overpay_purchase(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post_payment(reference: str) -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                purchase = Purchase.objects.get(pk=self.purchase.pk)
                barrier.wait()
                post_supplier_payment(
                    actor=actor,
                    purchase=purchase,
                    amount=Decimal("4000.00"),
                    method=OperationalPaymentMethod.TELEBIRR,
                    supplier_reference="",
                    telebirr_reference=reference,
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [
            Thread(target=post_payment, args=("TX-CONCURRENT-A",)),
            Thread(target=post_payment, args=("TX-CONCURRENT-B",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(SupplierPayment.objects.count(), 1)

    def test_concurrent_telebirr_reference_reuse_posts_one_expense(self) -> None:
        expenses = [
            create_operating_expense_draft(
                actor=self.owner,
                branch=self.branch,
                category=self.category,
                payee=f"Payee {index}",
                description="Concurrent Telebirr expense",
                amount=Decimal("10.00"),
            )
            for index in range(2)
        ]
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post_expense(expense_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                expense = OperatingExpense.objects.get(pk=expense_id)
                barrier.wait()
                post_operating_expense(
                    actor=actor,
                    expense=expense,
                    method=OperationalPaymentMethod.TELEBIRR,
                    telebirr_reference="TX-SHARED-4B",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=post_expense, args=(expense.id,)) for expense in expenses]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(OperatingExpensePayment.objects.count(), 1)

    def test_concurrent_exact_replay_creates_one_expense_payment(self) -> None:
        expense = create_operating_expense_draft(
            actor=self.owner,
            branch=self.branch,
            category=self.category,
            payee="Concurrent replay payee",
            description="Concurrent exact replay",
            amount=Decimal("20.00"),
        )
        key = uuid.uuid4()
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()

        def post_expense() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_expense = OperatingExpense.objects.get(pk=expense.pk)
                barrier.wait()
                result = post_operating_expense(
                    actor=actor,
                    expense=current_expense,
                    method=OperationalPaymentMethod.TELEBIRR,
                    telebirr_reference="TX-REPLAY-4B",
                    idempotency_key=key,
                )
                outcomes.put(result.id)
            finally:
                connections.close_all()

        threads = [Thread(target=post_expense), Thread(target=post_expense)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(OperatingExpensePayment.objects.count(), 1)
