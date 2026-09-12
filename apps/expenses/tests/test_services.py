import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovement, CashMovementType, CashSession
from apps.cash.services import expected_cash, open_cash_session
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseSettlementPostingKey,
    ExpenseStatus,
    OperatingExpense,
    OperatingExpensePayment,
    OperationalPaymentMethod,
    SupplierPayment,
    SupplierReturnSettlement,
    SupplierReturnSettlementType,
)
from apps.expenses.services import (
    cancel_operating_expense_draft,
    create_operating_expense_draft,
    edit_operating_expense_draft,
    post_operating_expense,
    post_supplier_payment,
    post_supplier_return_settlement,
    purchase_return_settlement_totals,
    purchase_settlement_totals,
    reverse_operating_expense,
    reverse_supplier_payment,
    reverse_supplier_return_settlement,
)
from apps.purchasing.models import Purchase, PurchaseLine, PurchaseReturn, Supplier
from apps.purchasing.services import (
    ReceiptQuantity,
    ReturnQuantity,
    approve_purchase,
    cancel_purchase,
    post_purchase_return,
    receive_purchase,
    reverse_purchase_return,
    save_purchase_return_draft,
)


class ExpenseSettlementServiceTests(TestCase):
    business: Business
    branch: Branch
    owner: BusinessMembership
    cashier: BusinessMembership
    category: ExpenseCategory
    purchase: Purchase
    purchase_line: PurchaseLine

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Stage 4B Shop", slug="stage-4b-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        owner_user = User.objects.create_user(
            email="stage4b-owner@example.com",
            password="strong-test-password",
        )
        cashier_user = User.objects.create_user(
            email="stage4b-cashier@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier = BusinessMembership.objects.create(
            business=self.business,
            user=cashier_user,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.category = ExpenseCategory.objects.create(
            business=self.business,
            name="Utilities",
        )
        supplier = Supplier.objects.create(
            business=self.business,
            name="Addis Clothing Wholesale",
        )
        product = Product.objects.create(business=self.business, name="Cotton Shirt")
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHIRT-BLK-M",
            size="M",
            color="Black",
            selling_price=Decimal("900.00"),
            stock_unit=StockUnit.PIECE,
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=supplier,
            internal_number="PUR-4B-1",
            purchase_date=timezone.localdate(),
            created_by=self.owner,
        )
        self.purchase_line = PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=variant,
            ordered_quantity=Decimal("10.000"),
            unit_cost=Decimal("500.00"),
        )
        approve_purchase(actor=self.owner, purchase=self.purchase)

    def _cash_session(self, amount: str = "10000.00") -> CashSession:
        return open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal(amount),
            opening_basis_note="Direct physical drawer count",
            idempotency_key=uuid.uuid4(),
        )

    def _expense(self, amount: str = "250.00") -> OperatingExpense:
        return create_operating_expense_draft(
            actor=self.owner,
            branch=self.branch,
            category=self.category,
            payee="City utility office",
            description="Electricity for the retail branch",
            amount=Decimal(amount),
        )

    def _posted_return(self) -> PurchaseReturn:
        receipt = receive_purchase(
            actor=self.owner,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(self.purchase_line.id, Decimal("10.000"))],
            idempotency_key=uuid.uuid4(),
        )
        receipt_line = receipt.lines.get()
        purchase_return = save_purchase_return_draft(
            actor=self.owner,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Damaged cartons",
            supplier_document_reference="SUP-RET-1",
            quantities=[ReturnQuantity(receipt_line.id, Decimal("2.000"))],
        )
        return post_purchase_return(
            actor=self.owner,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )

    def test_draft_has_no_effect_and_can_be_edited_or_cancelled(self) -> None:
        expense = self._expense()

        self.assertIsNone(expense.business_date)
        self.assertEqual(expense.status, ExpenseStatus.DRAFT)
        self.assertFalse(OperatingExpensePayment.objects.exists())
        edited = edit_operating_expense_draft(
            actor=self.owner,
            expense=expense,
            branch=self.branch,
            category=self.category,
            payee="Updated payee",
            description="Updated branch electricity evidence",
            amount=Decimal("300.00"),
        )
        cancelled = cancel_operating_expense_draft(actor=self.owner, expense=edited)

        self.assertEqual(cancelled.status, ExpenseStatus.CANCELLED)
        self.assertFalse(CashMovement.objects.exists())

    def test_expense_amount_must_be_positive(self) -> None:
        with self.assertRaisesMessage(ValidationError, "greater than zero"):
            self._expense("0.00")

    def test_cash_expense_posts_and_reverses_in_the_original_session(self) -> None:
        session = self._cash_session()
        expense = self._expense()
        posted = post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.CASH,
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(posted.status, ExpenseStatus.POSTED)
        self.assertEqual(posted.business_date, session.business_date)
        self.assertEqual(expected_cash(session), Decimal("9750.00"))
        movement = CashMovement.objects.get(movement_type=CashMovementType.OPERATING_EXPENSE)
        self.assertEqual(movement.amount_delta, Decimal("-250.00"))

        reversal = reverse_operating_expense(
            actor=self.owner,
            expense=posted,
            reason="Entered against the wrong category",
            idempotency_key=uuid.uuid4(),
        )
        posted.refresh_from_db()
        self.assertEqual(posted.status, ExpenseStatus.REVERSED)
        self.assertEqual(reversal.cash_session, session)
        self.assertEqual(expected_cash(session), Decimal("10000.00"))

    def test_expense_cash_post_rolls_back_if_expected_cash_would_be_negative(self) -> None:
        self._cash_session("100.00")
        expense = self._expense("100.01")

        with self.assertRaisesMessage(ValidationError, "expected cash negative"):
            post_operating_expense(
                actor=self.owner,
                expense=expense,
                method=OperationalPaymentMethod.CASH,
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )

        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.DRAFT)
        self.assertFalse(OperatingExpensePayment.objects.exists())
        self.assertEqual(ExpenseSettlementPostingKey.objects.count(), 0)

    def test_telebirr_expense_is_idempotent_and_has_no_cash_movement(self) -> None:
        expense = self._expense()
        key = uuid.uuid4()
        first = post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.TELEBIRR,
            telebirr_reference=" tx-exp-001 ",
            idempotency_key=key,
        )
        replay = post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.TELEBIRR,
            telebirr_reference=" tx-exp-001 ",
            idempotency_key=key,
        )

        self.assertEqual(first.id, replay.id)
        payment = OperatingExpensePayment.objects.get()
        self.assertEqual(payment.telebirr_reference, "tx-exp-001")
        self.assertEqual(payment.telebirr_reference_normalized, "TX-EXP-001")
        self.assertFalse(CashMovement.objects.exists())
        with self.assertRaisesMessage(ValidationError, "idempotent replay"):
            post_operating_expense(
                actor=self.owner,
                expense=expense,
                method=OperationalPaymentMethod.TELEBIRR,
                telebirr_reference="different",
                idempotency_key=key,
            )

    def test_cashier_cannot_manage_expenses_or_supplier_settlement(self) -> None:
        with self.assertRaises(PermissionDenied):
            self._expense_for_actor(self.cashier)
        with self.assertRaises(PermissionDenied):
            post_supplier_payment(
                actor=self.cashier,
                purchase=self.purchase,
                amount=Decimal("100.00"),
                method=OperationalPaymentMethod.TELEBIRR,
                supplier_reference="",
                telebirr_reference="TX-DENIED",
                idempotency_key=uuid.uuid4(),
            )

    def _expense_for_actor(self, actor: BusinessMembership) -> OperatingExpense:
        return create_operating_expense_draft(
            actor=actor,
            branch=self.branch,
            category=self.category,
            payee="",
            description="Denied expense",
            amount=Decimal("1.00"),
        )

    def test_partial_supplier_payments_derive_remaining_reference(self) -> None:
        first = post_supplier_payment(
            actor=self.owner,
            purchase=self.purchase,
            amount=Decimal("1200.00"),
            method=OperationalPaymentMethod.TELEBIRR,
            supplier_reference="SUP-PAY-1",
            telebirr_reference="TX-SUP-1",
            idempotency_key=uuid.uuid4(),
        )
        second = post_supplier_payment(
            actor=self.owner,
            purchase=self.purchase,
            amount=Decimal("800.00"),
            method=OperationalPaymentMethod.TELEBIRR,
            supplier_reference="SUP-PAY-2",
            telebirr_reference="TX-SUP-2",
            idempotency_key=uuid.uuid4(),
        )

        totals = purchase_settlement_totals(self.purchase)
        self.assertEqual(first.amount + second.amount, Decimal("2000.00"))
        self.assertEqual(totals["gross_purchase_reference"], Decimal("5000.00"))
        self.assertEqual(totals["remaining_operational_reference_balance"], Decimal("3000.00"))
        with self.assertRaisesMessage(ValidationError, "remaining operational"):
            post_supplier_payment(
                actor=self.owner,
                purchase=self.purchase,
                amount=Decimal("3000.01"),
                method=OperationalPaymentMethod.TELEBIRR,
                supplier_reference="",
                telebirr_reference="TX-SUP-3",
                idempotency_key=uuid.uuid4(),
            )

    def test_cash_supplier_payment_and_reversal_update_expected_cash(self) -> None:
        session = self._cash_session()
        payment = post_supplier_payment(
            actor=self.owner,
            purchase=self.purchase,
            amount=Decimal("1000.00"),
            method=OperationalPaymentMethod.CASH,
            supplier_reference="SUP-CASH-1",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(expected_cash(session), Decimal("9000.00"))

        reversal = reverse_supplier_payment(
            actor=self.owner,
            supplier_payment=payment,
            reason="Duplicate evidence",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(reversal.cash_session, session)
        self.assertEqual(expected_cash(session), Decimal("10000.00"))

    def test_active_supplier_payment_blocks_purchase_cancellation(self) -> None:
        payment = post_supplier_payment(
            actor=self.owner,
            purchase=self.purchase,
            amount=Decimal("500.00"),
            method=OperationalPaymentMethod.TELEBIRR,
            supplier_reference="SUP-PAY-CANCEL",
            telebirr_reference="TX-PAY-CANCEL",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "Reverse active supplier payments"):
            cancel_purchase(actor=self.owner, purchase=self.purchase)

        reverse_supplier_payment(
            actor=self.owner,
            supplier_payment=payment,
            reason="Payment evidence cancelled",
            idempotency_key=uuid.uuid4(),
        )
        cancelled = cancel_purchase(actor=self.owner, purchase=self.purchase)
        self.assertEqual(cancelled.status, "cancelled")

    def test_supplier_return_credit_uses_supplier_reference_not_inventory_value(self) -> None:
        purchase_return = self._posted_return()
        totals = purchase_return_settlement_totals(purchase_return)

        self.assertEqual(totals["supplier_return_reference_amount"], Decimal("1000.00"))
        settlement = post_supplier_return_settlement(
            actor=self.owner,
            purchase_return=purchase_return,
            settlement_type=SupplierReturnSettlementType.CREDIT,
            amount=Decimal("600.00"),
            method="",
            supplier_reference="CREDIT-1",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        updated = purchase_return_settlement_totals(purchase_return)
        self.assertEqual(updated["supplier_accepted_settlement_amount"], Decimal("600.00"))
        self.assertEqual(updated["unresolved_supplier_return_reference"], Decimal("400.00"))
        self.assertFalse(CashMovement.objects.filter(source_id=settlement.id).exists())

    def test_active_settlement_blocks_purchase_return_reversal(self) -> None:
        purchase_return = self._posted_return()
        settlement = post_supplier_return_settlement(
            actor=self.owner,
            purchase_return=purchase_return,
            settlement_type=SupplierReturnSettlementType.CREDIT,
            amount=Decimal("200.00"),
            method="",
            supplier_reference="SUP-CREDIT-BLOCK",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Reverse active supplier-return settlements",
        ):
            reverse_purchase_return(
                actor=self.owner,
                purchase_return=purchase_return,
                reason="Wrong return",
                idempotency_key=uuid.uuid4(),
            )

        reverse_supplier_return_settlement(
            actor=self.owner,
            settlement=settlement,
            reason="Credit withdrawn",
            idempotency_key=uuid.uuid4(),
        )
        reversal = reverse_purchase_return(
            actor=self.owner,
            purchase_return=purchase_return,
            reason="Wrong return",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(reversal.purchase_return_id, purchase_return.id)

    def test_supplier_refund_cannot_exceed_net_payments_and_cash_refund_reverses(self) -> None:
        session = self._cash_session()
        post_supplier_payment(
            actor=self.owner,
            purchase=self.purchase,
            amount=Decimal("500.00"),
            method=OperationalPaymentMethod.CASH,
            supplier_reference="SUP-CASH-2",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        purchase_return = self._posted_return()
        with self.assertRaisesMessage(ValidationError, "net of refunds"):
            post_supplier_return_settlement(
                actor=self.owner,
                purchase_return=purchase_return,
                settlement_type=SupplierReturnSettlementType.REFUND,
                amount=Decimal("600.00"),
                method=OperationalPaymentMethod.CASH,
                supplier_reference="",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )
        refund = post_supplier_return_settlement(
            actor=self.owner,
            purchase_return=purchase_return,
            settlement_type=SupplierReturnSettlementType.REFUND,
            amount=Decimal("400.00"),
            method=OperationalPaymentMethod.CASH,
            supplier_reference="SUP-REFUND-1",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(expected_cash(session), Decimal("9900.00"))

        reversal = reverse_supplier_return_settlement(
            actor=self.owner,
            settlement=refund,
            reason="Refund evidence entered twice",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(reversal.cash_session, session)
        self.assertEqual(expected_cash(session), Decimal("9500.00"))

    def test_posting_key_cannot_cross_operations(self) -> None:
        key = uuid.uuid4()
        expense = self._expense()
        post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.TELEBIRR,
            telebirr_reference="TX-CROSS-1",
            idempotency_key=key,
        )

        with self.assertRaisesMessage(ValidationError, "another expense or settlement"):
            post_supplier_payment(
                actor=self.owner,
                purchase=self.purchase,
                amount=Decimal("100.00"),
                method=OperationalPaymentMethod.TELEBIRR,
                supplier_reference="",
                telebirr_reference="TX-CROSS-2",
                idempotency_key=key,
            )

        self.assertEqual(SupplierPayment.objects.count(), 0)
        self.assertEqual(SupplierReturnSettlement.objects.count(), 0)
