import uuid
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovement, CashMovementType
from apps.cash.services import close_cash_session, open_cash_session
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import InventoryBalance, InventoryMovement, InventoryMovementType
from apps.inventory.services import post_inventory_adjustment, post_opening_balance
from apps.sales.models import (
    InternalReturnReceipt,
    Sale,
    SaleRefundEvidence,
    SaleReturn,
    SaleReturnPurpose,
    SaleReturnReversal,
    SaleReturnStatus,
)
from apps.sales.services import (
    ReturnQuantity,
    SaleQuantity,
    post_sale,
    post_sale_return,
    reverse_sale_return,
    sale_line_return_progress,
    save_sale_draft,
    save_sale_return_draft,
)


class SaleReturnServiceTests(TestCase):
    business: Business
    branch: Branch
    owner: BusinessMembership
    cashier: BusinessMembership
    stock_employee: BusinessMembership
    first_variant: ProductVariant
    second_variant: ProductVariant

    def setUp(self) -> None:
        owner_user = User.objects.create_user(
            email="return-owner@example.com",
            password="strong-test-password",
            full_name="Return Owner",
        )
        cashier_user = User.objects.create_user(
            email="return-cashier@example.com",
            password="strong-test-password",
            full_name="Return Cashier",
        )
        stock_user = User.objects.create_user(
            email="return-stock@example.com",
            password="strong-test-password",
            full_name="Return Stock",
        )
        self.business = Business.objects.create(name="Return Shop", slug="return-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
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
        self.stock_employee = BusinessMembership.objects.create(
            business=self.business,
            user=stock_user,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=uuid.uuid4(),
        )
        product = Product.objects.create(business=self.business, name="Return Shoe")
        self.first_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RETURN-BLK-42",
            selling_price=Decimal("1500"),
            stock_unit=StockUnit.PAIR,
        )
        self.second_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RETURN-BRN-43",
            selling_price=Decimal("1200"),
            stock_unit=StockUnit.PAIR,
        )
        for variant, cost in (
            (self.first_variant, Decimal("500")),
            (self.second_variant, Decimal("400")),
        ):
            post_opening_balance(
                actor=self.owner,
                branch=self.branch,
                variant=variant,
                quantity=Decimal("10"),
                unit_cost=cost,
                idempotency_key=uuid.uuid4(),
            )

    def _posted_sale(self, *, telebirr_reference: str = "") -> Sale:
        sale = save_sale_draft(
            actor=self.cashier,
            branch=self.branch,
            sale_date=timezone.localdate(),
            quantities=[
                SaleQuantity(self.first_variant.id, Decimal("3")),
                SaleQuantity(self.second_variant.id, Decimal("2")),
            ],
        )
        return post_sale(
            actor=self.cashier,
            sale=sale,
            payment_method="telebirr" if telebirr_reference else "cash",
            telebirr_reference=telebirr_reference,
            idempotency_key=uuid.uuid4(),
        )

    def _draft_return(
        self,
        *,
        sale: Sale | None = None,
        quantities: list[ReturnQuantity] | None = None,
        purpose: str = SaleReturnPurpose.CUSTOMER_RETURN,
        actor: BusinessMembership | None = None,
    ) -> SaleReturn:
        source_sale = sale or self._posted_sale()
        default_line = source_sale.lines.get(variant=self.first_variant)
        return save_sale_return_draft(
            actor=actor or self.cashier,
            sale=source_sale,
            purpose=purpose,
            return_date=timezone.localdate(),
            reason="Customer returned saleable item",
            quantities=quantities or [ReturnQuantity(default_line.id, Decimal("1"))],
        )

    def test_partial_cash_return_restores_original_assigned_cost(self) -> None:
        sale = self._posted_sale()
        line = sale.lines.get(variant=self.first_variant)
        sale_return = self._draft_return(
            sale=sale,
            quantities=[ReturnQuantity(line.id, Decimal("1"))],
        )

        posted = post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )

        posted.refresh_from_db()
        return_line = posted.lines.get()
        balance = InventoryBalance.objects.get(branch=self.branch, variant=self.first_variant)
        movement = InventoryMovement.objects.get(
            movement_type=InventoryMovementType.SALE_RETURN,
            source_id=return_line.id,
        )
        self.assertEqual(posted.status, SaleReturnStatus.POSTED)
        self.assertEqual(balance.quantity_on_hand, Decimal("8.000"))
        self.assertEqual(return_line.original_assigned_inventory_unit_cost, Decimal("500.000000"))
        self.assertEqual(return_line.inventory_value_delta, Decimal("500.000000"))
        self.assertEqual(movement.unit_cost, Decimal("500.000000"))
        self.assertEqual(posted.total_refund_amount, Decimal("1500.00"))
        self.assertEqual(posted.refund.amount, posted.total_refund_amount)
        self.assertEqual(posted.refund.method, "cash")
        self.assertEqual(posted.receipt.total_amount, posted.total_refund_amount)
        cash_movement = CashMovement.objects.get(
            movement_type=CashMovementType.CASH_REFUND,
            source_id=posted.refund.id,
        )
        self.assertEqual(cash_movement.amount_delta, Decimal("-1500.00"))

    def test_cash_refund_requires_open_session_and_rolls_back_return(self) -> None:
        sale_return = self._draft_return()
        session = self.branch.cash_sessions.get()
        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=sale_return.sale.total_amount,
            explanation="",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "Open the branch cash session"):
            post_sale_return(
                actor=self.owner,
                sale_return=sale_return,
                refund_method="cash",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )

        sale_return.refresh_from_db()
        self.assertEqual(sale_return.status, SaleReturnStatus.DRAFT)
        self.assertFalse(SaleRefundEvidence.objects.exists())
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.SALE_RETURN
            ).exists()
        )

    def test_remaining_quantity_excludes_reversed_returns(self) -> None:
        sale = self._posted_sale()
        line = sale.lines.get(variant=self.first_variant)
        first = self._draft_return(
            sale=sale,
            quantities=[ReturnQuantity(line.id, Decimal("2"))],
        )
        post_sale_return(
            actor=self.owner,
            sale_return=first,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        progress = sale_line_return_progress(sale)
        first_progress = next(item for item in progress if item.line.id == line.id)
        self.assertEqual(first_progress.remaining_quantity, Decimal("1.000"))
        with self.assertRaisesMessage(ValidationError, "remaining sold quantity"):
            self._draft_return(
                sale=sale,
                quantities=[ReturnQuantity(line.id, Decimal("2"))],
            )

        reverse_sale_return(
            actor=self.owner,
            sale_return=first,
            reason="Return entered for the wrong receipt",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(
            next(
                item for item in sale_line_return_progress(sale) if item.line.id == line.id
            ).remaining_quantity,
            Decimal("3.000"),
        )

    def test_full_sale_reversal_requires_every_line_and_no_posted_return(self) -> None:
        sale = self._posted_sale()
        first_line = sale.lines.get(variant=self.first_variant)
        with self.assertRaisesMessage(ValidationError, "every sale line"):
            self._draft_return(
                sale=sale,
                purpose=SaleReturnPurpose.SALE_REVERSAL,
                actor=self.owner,
                quantities=[ReturnQuantity(first_line.id, first_line.quantity)],
            )

        customer_return = self._draft_return(sale=sale)
        post_sale_return(
            actor=self.owner,
            sale_return=customer_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        with self.assertRaisesMessage(ValidationError, "unavailable"):
            self._draft_return(
                sale=sale,
                purpose=SaleReturnPurpose.SALE_REVERSAL,
                actor=self.owner,
                quantities=[ReturnQuantity(line.id, line.quantity) for line in sale.lines.all()],
            )

    def test_telebirr_refund_reference_has_separate_unique_namespace(self) -> None:
        sale = self._posted_sale(telebirr_reference="TX-SHARED-001")
        first = self._draft_return(sale=sale)
        posted = post_sale_return(
            actor=self.owner,
            sale_return=first,
            refund_method="telebirr",
            telebirr_reference="  tx shared 001 ",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(posted.refund.telebirr_reference, "tx shared 001")
        self.assertEqual(
            posted.refund.telebirr_reference_normalized,
            "TXSHARED001",
        )
        self.assertFalse(
            CashMovement.objects.filter(movement_type=CashMovementType.CASH_REFUND).exists()
        )

        second_line = sale.lines.get(variant=self.second_variant)
        second = self._draft_return(
            sale=sale,
            quantities=[ReturnQuantity(second_line.id, Decimal("1"))],
        )
        with self.assertRaisesMessage(ValidationError, "already been used"):
            post_sale_return(
                actor=self.owner,
                sale_return=second,
                refund_method="telebirr",
                telebirr_reference="TXSHARED001",
                idempotency_key=uuid.uuid4(),
            )
        self.assertEqual(SaleRefundEvidence.objects.count(), 1)

    def test_posting_is_idempotent_and_key_cannot_cross_operations(self) -> None:
        sale_return = self._draft_return()
        key = uuid.uuid4()
        first = post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )
        replay = post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )
        self.assertEqual(replay.id, first.id)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.SALE_RETURN
            ).count(),
            1,
        )
        with self.assertRaisesMessage(ValidationError, "another sale return operation"):
            reverse_sale_return(
                actor=self.owner,
                sale_return=first,
                reason="Wrong return",
                idempotency_key=key,
            )

    def test_return_reversal_conserves_return_value_and_requires_stock(self) -> None:
        sale_return = self._draft_return()
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.first_variant,
            operation_type="adjustment_in",
            quantity=Decimal("1"),
            unit_cost=Decimal("900"),
            reason="New stock",
            idempotency_key=uuid.uuid4(),
        )
        balance_before_return = InventoryBalance.objects.get(
            branch=self.branch,
            variant=self.first_variant,
        )
        sale_return = post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        return_line = sale_return.lines.get()
        return_movement = InventoryMovement.objects.get(
            movement_type=InventoryMovementType.SALE_RETURN,
            source_id=return_line.id,
        )
        cash_movement_count = CashMovement.objects.count()
        reversal = reverse_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            reason="Return entered twice",
            idempotency_key=uuid.uuid4(),
        )
        movement = InventoryMovement.objects.get(
            movement_type=InventoryMovementType.SALE_RETURN_REVERSAL,
            source_id=return_line.id,
        )
        balance_after = InventoryBalance.objects.get(
            branch=self.branch,
            variant=self.first_variant,
        )
        self.assertEqual(movement.unit_cost, return_movement.unit_cost)
        self.assertEqual(movement.value_delta, -return_movement.value_delta)
        self.assertEqual(CashMovement.objects.count(), cash_movement_count)
        self.assertEqual(
            balance_after.inventory_value,
            balance_before_return.inventory_value,
        )
        self.assertEqual(
            balance_after.quantity_on_hand,
            balance_before_return.quantity_on_hand,
        )
        self.assertEqual(
            balance_after.average_unit_cost,
            balance_before_return.average_unit_cost,
        )
        self.assertIsInstance(reversal, SaleReturnReversal)
        sale_return.refresh_from_db()
        self.assertEqual(sale_return.status, SaleReturnStatus.REVERSED)

        other_return = post_sale_return(
            actor=self.owner,
            sale_return=self._draft_return(),
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        balance = InventoryBalance.objects.get(
            branch=self.branch,
            variant=self.first_variant,
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.first_variant,
            operation_type="adjustment_out",
            quantity=balance.quantity_on_hand,
            reason="Stock removed",
            idempotency_key=uuid.uuid4(),
        )
        with self.assertRaisesMessage(ValidationError, "negative"):
            reverse_sale_return(
                actor=self.owner,
                sale_return=other_return,
                reason="Cannot restore",
                idempotency_key=uuid.uuid4(),
            )
        other_return.refresh_from_db()
        self.assertEqual(other_return.status, SaleReturnStatus.POSTED)

    def test_return_reversal_rejects_insufficient_inventory_value(self) -> None:
        sale_return = post_sale_return(
            actor=self.owner,
            sale_return=self._draft_return(),
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.first_variant,
            operation_type="adjustment_in",
            quantity=Decimal("100"),
            unit_cost=Decimal("0"),
            reason="Free stock",
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.first_variant,
            operation_type="adjustment_out",
            quantity=Decimal("100"),
            reason="Stock removed",
            idempotency_key=uuid.uuid4(),
        )
        balance = InventoryBalance.objects.get(
            branch=self.branch,
            variant=self.first_variant,
        )
        line = sale_return.lines.get()
        self.assertGreaterEqual(balance.quantity_on_hand, line.returned_quantity)
        self.assertLess(balance.inventory_value, line.inventory_value_delta)

        with self.assertRaisesMessage(ValidationError, "inventory value negative"):
            reverse_sale_return(
                actor=self.owner,
                sale_return=sale_return,
                reason="Cannot preserve value",
                idempotency_key=uuid.uuid4(),
            )

        sale_return.refresh_from_db()
        self.assertEqual(sale_return.status, SaleReturnStatus.POSTED)
        self.assertFalse(SaleReturnReversal.objects.filter(sale_return=sale_return).exists())
        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.SALE_RETURN_REVERSAL,
                source_id=line.id,
            ).exists()
        )

    def test_cashier_is_draft_only_and_stock_employee_has_no_access(self) -> None:
        sale_return = self._draft_return()
        with self.assertRaises(PermissionDenied):
            post_sale_return(
                actor=self.cashier,
                sale_return=sale_return,
                refund_method="cash",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )
        sale = sale_return.sale
        line = sale.lines.get(variant=self.second_variant)
        with self.assertRaises(PermissionDenied):
            self._draft_return(
                sale=sale,
                actor=self.stock_employee,
                quantities=[ReturnQuantity(line.id, Decimal("1"))],
            )
        with self.assertRaises(PermissionDenied):
            self._draft_return(
                sale=sale,
                actor=self.cashier,
                purpose=SaleReturnPurpose.SALE_REVERSAL,
                quantities=[
                    ReturnQuantity(source.id, source.quantity) for source in sale.lines.all()
                ],
            )

    def test_posted_records_are_immutable(self) -> None:
        sale_return = post_sale_return(
            actor=self.owner,
            sale_return=self._draft_return(),
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        sale_return.reason = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            sale_return.save()
        line = sale_return.lines.get()
        line.returned_quantity = Decimal("2")
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            line.save()
        sale_return.refund.amount = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            sale_return.refund.save()
        self.assertEqual(InternalReturnReceipt.objects.count(), 1)

    def test_inactive_historical_variant_can_be_returned_at_original_branch(self) -> None:
        sale = self._posted_sale()
        self.first_variant.is_active = False
        self.first_variant.save(update_fields=("is_active",))
        sale_return = self._draft_return(sale=sale)

        post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )

        sale_return.refresh_from_db()
        self.assertEqual(sale_return.branch, sale.branch)
        self.assertEqual(sale_return.status, SaleReturnStatus.POSTED)

    def test_cashier_ui_is_draft_only_and_hides_inventory_value(self) -> None:
        sale_return = self._draft_return()
        self.client.force_login(self.cashier.user)

        detail = self.client.get(reverse("sales:return-detail", args=[sale_return.id]))
        post_response = self.client.get(reverse("sales:return-post", args=[sale_return.id]))
        reversal_response = self.client.get(reverse("sales:return-reverse", args=[sale_return.id]))

        self.assertEqual(detail.status_code, 200)
        self.assertNotContains(detail, "Original assigned inventory unit cost")
        self.assertNotContains(detail, "Inventory value restoration")
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(reversal_response.status_code, 403)

    def test_return_form_rejects_zero_negative_and_blank_quantities(self) -> None:
        sale = self._posted_sale()
        line = sale.lines.get(variant=self.first_variant)
        self.client.force_login(self.cashier.user)

        for quantity, message in (
            ("0", "Return quantity must be greater than zero."),
            ("-1", "Return quantity must be greater than zero."),
            ("", "This field is required."),
        ):
            with self.subTest(quantity=quantity):
                response = self.client.post(
                    reverse(
                        "sales:return-create",
                        args=[sale.id, SaleReturnPurpose.CUSTOMER_RETURN],
                    ),
                    {
                        "return_date": timezone.localdate().isoformat(),
                        "reason": "Customer returned saleable item",
                        "lines-TOTAL_FORMS": "1",
                        "lines-INITIAL_FORMS": "0",
                        "lines-MIN_NUM_FORMS": "1",
                        "lines-MAX_NUM_FORMS": "1000",
                        "lines-0-sale_line": str(line.id),
                        "lines-0-returned_quantity": quantity,
                    },
                )

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)
        self.assertFalse(SaleReturn.objects.exists())

    def test_owner_ui_posts_return_and_receipt_disclaims_provider_and_tax_status(self) -> None:
        sale_return = self._draft_return()
        self.client.force_login(self.owner.user)

        response = self.client.post(
            reverse("sales:return-post", args=[sale_return.id]),
            {
                "refund_method": "telebirr",
                "telebirr_reference": "RETURN-TX-1",
                "idempotency_key": str(uuid.uuid4()),
            },
        )

        receipt = InternalReturnReceipt.objects.get()
        self.assertRedirects(
            response,
            reverse("sales:return-receipt-detail", args=[receipt.id]),
        )
        receipt_response = self.client.get(
            reverse("sales:return-receipt-detail", args=[receipt.id])
        )
        self.assertContains(
            receipt_response,
            "This is an internal transaction record. "
            "It is not an official tax invoice or tax credit note.",
        )
        self.assertContains(
            receipt_response,
            "does not verify provider acceptance, completion, settlement, or reconciliation",
        )
        self.assertNotContains(receipt_response, "Public record verification")


@skipUnlessDBFeature("has_select_for_update")
class SaleReturnConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        owner_user = User.objects.create_user(
            email="return-concurrency-owner@example.com",
            password="strong-test-password",
            full_name="Return Concurrency Owner",
        )
        self.business = Business.objects.create(
            name="Return Concurrency Shop",
            slug="return-concurrency-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=uuid.uuid4(),
        )
        product = Product.objects.create(business=self.business, name="Concurrent Shoe")
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CONCURRENT-42",
            selling_price=Decimal("1000"),
            stock_unit=StockUnit.PAIR,
        )
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=variant,
            quantity=Decimal("3"),
            unit_cost=Decimal("400"),
            idempotency_key=uuid.uuid4(),
        )
        sale = save_sale_draft(
            actor=self.owner,
            branch=self.branch,
            sale_date=timezone.localdate(),
            quantities=[SaleQuantity(variant.id, Decimal("1"))],
        )
        self.sale = post_sale(
            actor=self.owner,
            sale=sale,
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        line = self.sale.lines.get()
        self.first_return = save_sale_return_draft(
            actor=self.owner,
            sale=self.sale,
            purpose=SaleReturnPurpose.CUSTOMER_RETURN,
            return_date=timezone.localdate(),
            reason="First return",
            quantities=[ReturnQuantity(line.id, Decimal("1"))],
        )
        self.second_return = save_sale_return_draft(
            actor=self.owner,
            sale=self.sale,
            purpose=SaleReturnPurpose.CUSTOMER_RETURN,
            return_date=timezone.localdate(),
            reason="Second return",
            quantities=[ReturnQuantity(line.id, Decimal("1"))],
        )

    def test_concurrent_returns_cannot_exceed_original_sale_quantity(self) -> None:
        barrier = Barrier(2)
        results: Queue[object] = Queue()

        def post(return_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                sale_return = SaleReturn.objects.get(pk=return_id)
                barrier.wait()
                result = post_sale_return(
                    actor=actor,
                    sale_return=sale_return,
                    refund_method="cash",
                    telebirr_reference="",
                    idempotency_key=uuid.uuid4(),
                )
                results.put(result.id)
            except Exception as error:
                results.put(error)
            finally:
                connections.close_all()

        threads = [
            Thread(target=post, args=(self.first_return.id,)),
            Thread(target=post, args=(self.second_return.id,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        outcomes = [results.get(), results.get()]
        self.assertEqual(SaleReturn.objects.filter(status=SaleReturnStatus.POSTED).count(), 1)
        self.assertEqual(sum(isinstance(item, ValidationError) for item in outcomes), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.SALE_RETURN
            ).count(),
            1,
        )

    def test_concurrent_duplicate_refund_reference_posts_only_one_return(self) -> None:
        variant = self.sale.lines.get().variant
        second_sale = post_sale(
            actor=self.owner,
            sale=save_sale_draft(
                actor=self.owner,
                branch=self.branch,
                sale_date=timezone.localdate(),
                quantities=[SaleQuantity(variant.id, Decimal("1"))],
            ),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        second_line = second_sale.lines.get()
        other_return = save_sale_return_draft(
            actor=self.owner,
            sale=second_sale,
            purpose=SaleReturnPurpose.CUSTOMER_RETURN,
            return_date=timezone.localdate(),
            reason="Other sale return",
            quantities=[ReturnQuantity(second_line.id, Decimal("1"))],
        )
        barrier = Barrier(2)
        results: Queue[object] = Queue()

        def post(return_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                sale_return = SaleReturn.objects.get(pk=return_id)
                barrier.wait()
                result = post_sale_return(
                    actor=actor,
                    sale_return=sale_return,
                    refund_method="telebirr",
                    telebirr_reference="SAME-REFUND-TX",
                    idempotency_key=uuid.uuid4(),
                )
                results.put(result.id)
            except Exception as error:
                results.put(error)
            finally:
                connections.close_all()

        threads = [
            Thread(target=post, args=(self.first_return.id,)),
            Thread(target=post, args=(other_return.id,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        outcomes = [results.get(), results.get()]
        self.assertEqual(SaleRefundEvidence.objects.count(), 1)
        self.assertEqual(SaleReturn.objects.filter(status=SaleReturnStatus.POSTED).count(), 1)
        self.assertEqual(sum(isinstance(item, ValidationError) for item in outcomes), 1)
