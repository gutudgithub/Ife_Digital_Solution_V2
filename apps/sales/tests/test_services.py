import uuid
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import PropertyMock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
)
from apps.inventory.services import post_opening_balance
from apps.sales.models import (
    InternalReceipt,
    Sale,
    SalePayment,
    SalePostingKey,
    SaleStatus,
)
from apps.sales.services import SaleQuantity, cancel_sale, post_sale, save_sale_draft


class SalesServiceTests(TestCase):
    business: Business
    branch: Branch
    other_branch: Branch
    owner_membership: BusinessMembership
    cashier_membership: BusinessMembership
    stock_membership: BusinessMembership
    first_variant: ProductVariant
    second_variant: ProductVariant

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="sales-owner@example.com",
            password="strong-test-password",
            full_name="Sales Owner",
        )
        cashier = User.objects.create_user(
            email="sales-cashier@example.com",
            password="strong-test-password",
            full_name="Sales Cashier",
        )
        stock_employee = User.objects.create_user(
            email="sales-stock@example.com",
            password="strong-test-password",
            full_name="Sales Stock",
        )
        self.business = Business.objects.create(name="Sales Shop", slug="sales-shop")
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
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.stock_membership = BusinessMembership.objects.create(
            business=self.business,
            user=stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        product = Product.objects.create(business=self.business, name="Leather Shoe")
        self.first_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BLK-42",
            selling_price=Decimal("1500.00"),
            stock_unit=StockUnit.PAIR,
        )
        self.second_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BRN-43",
            selling_price=Decimal("1200.00"),
            stock_unit=StockUnit.PAIR,
        )
        for variant, quantity, cost in (
            (self.first_variant, Decimal("10"), Decimal("500")),
            (self.second_variant, Decimal("5"), Decimal("400")),
        ):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=variant,
                quantity=quantity,
                unit_cost=cost,
                idempotency_key=uuid.uuid4(),
            )

    def _draft(
        self,
        *,
        actor: BusinessMembership | None = None,
        quantities: list[SaleQuantity] | None = None,
        branch: Branch | None = None,
    ) -> Sale:
        return save_sale_draft(
            actor=actor or self.cashier_membership,
            branch=branch or self.branch,
            sale_date=timezone.localdate(),
            quantities=quantities or [SaleQuantity(self.first_variant.id, Decimal("2"))],
        )

    def test_draft_snapshots_current_price_without_ledger_or_payment_effect(self) -> None:
        sale = self._draft()

        line = sale.lines.get()
        self.assertEqual(sale.status, SaleStatus.DRAFT)
        self.assertEqual(sale.total_amount, Decimal("3000.00"))
        self.assertEqual(line.product_name_snapshot, "Leather Shoe")
        self.assertEqual(line.sku_snapshot, "SHOE-BLK-42")
        self.assertEqual(line.selling_unit_price, Decimal("1500.00"))
        self.assertEqual(InventoryMovement.objects.count(), 2)
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(InternalReceipt.objects.exists())

    def test_draft_rejects_fractional_whole_unit_and_cross_business_variant(self) -> None:
        with self.assertRaisesMessage(ValidationError, "whole-number"):
            self._draft(quantities=[SaleQuantity(self.first_variant.id, Decimal("1.5"))])

        other_business = Business.objects.create(name="Other Shop", slug="other-sales-shop")
        other_product = Product.objects.create(business=other_business, name="Other Shoe")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="OTHER-SHOE",
            selling_price=Decimal("10"),
        )
        with self.assertRaisesMessage(ValidationError, "unavailable"):
            self._draft(quantities=[SaleQuantity(other_variant.id, Decimal("1"))])

        self.assertFalse(Sale.objects.exists())

    def test_inactive_and_zero_price_variants_cannot_start_new_drafts(self) -> None:
        self.first_variant.is_active = False
        self.first_variant.save(update_fields=("is_active",))
        with self.assertRaisesMessage(ValidationError, "unavailable"):
            self._draft()

        self.first_variant.is_active = True
        self.first_variant.selling_price = Decimal("0")
        self.first_variant.save(update_fields=("is_active", "selling_price"))
        with self.assertRaisesMessage(ValidationError, "positive catalog selling price"):
            self._draft()

    def test_cashier_is_branch_scoped_and_stock_employee_has_no_sales_access(self) -> None:
        with (
            patch.object(
                BusinessMembership,
                "can_view_sale_cost",
                new_callable=PropertyMock,
                return_value=True,
            ),
            self.assertRaises(PermissionDenied),
        ):
            self._draft(branch=self.other_branch)
        with self.assertRaises(PermissionDenied):
            self._draft(actor=self.stock_membership)

        owner_sale = self._draft(actor=self.owner_membership, branch=self.other_branch)
        self.assertEqual(owner_sale.branch, self.other_branch)

    def test_posted_cash_sale_reduces_inventory_once_and_preserves_cost(self) -> None:
        sale = self._draft()
        key = uuid.uuid4()

        posted = post_sale(
            actor=self.cashier_membership,
            sale=sale,
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )

        line = posted.lines.get()
        balance = InventoryBalance.objects.get(
            branch=self.branch,
            variant=self.first_variant,
        )
        movement = InventoryMovement.objects.get(movement_type=InventoryMovementType.SALE)
        self.assertEqual(posted.status, SaleStatus.POSTED)
        self.assertEqual(balance.quantity_on_hand, Decimal("8.000"))
        self.assertEqual(balance.inventory_value, Decimal("4000.000000"))
        self.assertEqual(line.assigned_inventory_unit_cost, Decimal("500.000000"))
        self.assertEqual(line.inventory_value_delta, Decimal("-1000.000000"))
        self.assertEqual(movement.source_type, InventorySourceType.SALE_LINE)
        self.assertEqual(movement.source_id, line.id)
        self.assertEqual(posted.payment.amount, posted.total_amount)
        self.assertEqual(posted.payment.method, "cash")
        self.assertEqual(posted.receipt.total_amount, posted.total_amount)
        posting_key = posted.posting_key
        self.assertIsNotNone(posting_key)
        assert posting_key is not None
        self.assertEqual(posting_key.key, key)

    def test_telebirr_reference_is_required_normalized_and_business_unique(self) -> None:
        first = self._draft()
        with self.assertRaisesMessage(ValidationError, "Telebirr transaction reference"):
            post_sale(
                actor=self.cashier_membership,
                sale=first,
                payment_method="telebirr",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )

        post_sale(
            actor=self.cashier_membership,
            sale=first,
            payment_method="telebirr",
            telebirr_reference="  tx 123 abc  ",
            idempotency_key=uuid.uuid4(),
        )
        payment = first.payment
        self.assertEqual(payment.telebirr_reference, "tx 123 abc")
        self.assertEqual(payment.telebirr_reference_normalized, "TX123ABC")

        second = self._draft(quantities=[SaleQuantity(self.second_variant.id, Decimal("1"))])
        with self.assertRaisesMessage(ValidationError, "already been used"):
            post_sale(
                actor=self.cashier_membership,
                sale=second,
                payment_method="telebirr",
                telebirr_reference="TX123ABC",
                idempotency_key=uuid.uuid4(),
            )
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.second_variant).quantity_on_hand,
            Decimal("5.000"),
        )

    def test_cash_payment_rejects_external_reference(self) -> None:
        with self.assertRaisesMessage(ValidationError, "cannot include"):
            post_sale(
                actor=self.cashier_membership,
                sale=self._draft(),
                payment_method="cash",
                telebirr_reference="TX-UNEXPECTED",
                idempotency_key=uuid.uuid4(),
            )

    def test_stale_catalog_price_rejects_posting_without_silent_repricing(self) -> None:
        sale = self._draft()
        self.first_variant.selling_price = Decimal("1550.00")
        self.first_variant.save(update_fields=("selling_price",))

        with self.assertRaisesMessage(ValidationError, "selling price changed"):
            post_sale(
                actor=self.cashier_membership,
                sale=sale,
                payment_method="cash",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )

        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleStatus.DRAFT)
        self.assertEqual(sale.lines.get().selling_unit_price, Decimal("1500.00"))
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(
            InventoryMovement.objects.filter(movement_type=InventoryMovementType.SALE).exists()
        )

    def test_negative_stock_and_multiline_failure_roll_back_complete_posting(self) -> None:
        sale = self._draft(
            quantities=[
                SaleQuantity(self.first_variant.id, Decimal("2")),
                SaleQuantity(self.second_variant.id, Decimal("6")),
            ]
        )

        with self.assertRaisesMessage(ValidationError, "make stock negative"):
            post_sale(
                actor=self.cashier_membership,
                sale=sale,
                payment_method="cash",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )

        sale.refresh_from_db()
        self.assertEqual(sale.status, SaleStatus.DRAFT)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_variant).quantity_on_hand,
            Decimal("10.000"),
        )
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.second_variant).quantity_on_hand,
            Decimal("5.000"),
        )
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(InternalReceipt.objects.exists())
        self.assertFalse(SalePostingKey.objects.exists())

    def test_idempotency_replay_returns_existing_sale_without_duplicates(self) -> None:
        sale = self._draft()
        key = uuid.uuid4()
        first = post_sale(
            actor=self.cashier_membership,
            sale=sale,
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )
        replay = post_sale(
            actor=self.cashier_membership,
            sale=sale,
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )

        self.assertEqual(first.id, replay.id)
        self.assertEqual(SalePayment.objects.count(), 1)
        self.assertEqual(InternalReceipt.objects.count(), 1)
        self.assertEqual(SalePostingKey.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(movement_type=InventoryMovementType.SALE).count(),
            1,
        )

    def test_idempotency_key_cannot_move_to_another_sale(self) -> None:
        key = uuid.uuid4()
        post_sale(
            actor=self.cashier_membership,
            sale=self._draft(),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=key,
        )
        second = self._draft(quantities=[SaleQuantity(self.second_variant.id, Decimal("1"))])

        with self.assertRaisesMessage(ValidationError, "belongs to another sale"):
            post_sale(
                actor=self.cashier_membership,
                sale=second,
                payment_method="cash",
                telebirr_reference="",
                idempotency_key=key,
            )

        second.refresh_from_db()
        self.assertEqual(second.status, SaleStatus.DRAFT)

    def test_payment_or_receipt_failure_rolls_back_inventory_and_posting(self) -> None:
        for target in (
            "apps.sales.services.SalePayment.objects.create",
            "apps.sales.services.InternalReceipt.objects.create",
        ):
            sale = self._draft()
            initial_movement_count = InventoryMovement.objects.count()
            with (
                patch(target, side_effect=ValidationError("simulated failure")),
                self.assertRaisesMessage(ValidationError, "simulated failure"),
            ):
                post_sale(
                    actor=self.cashier_membership,
                    sale=sale,
                    payment_method="cash",
                    telebirr_reference="",
                    idempotency_key=uuid.uuid4(),
                )
            sale.refresh_from_db()
            self.assertEqual(sale.status, SaleStatus.DRAFT)
            self.assertEqual(InventoryMovement.objects.count(), initial_movement_count)
            self.assertFalse(SalePayment.objects.filter(sale=sale).exists())
            self.assertFalse(InternalReceipt.objects.filter(sale=sale).exists())
            self.assertFalse(SalePostingKey.objects.filter(source_id=sale.id).exists())

    def test_posted_records_are_immutable_and_cancelled_draft_cannot_post(self) -> None:
        sale = post_sale(
            actor=self.cashier_membership,
            sale=self._draft(),
            payment_method="cash",
            telebirr_reference="",
            idempotency_key=uuid.uuid4(),
        )
        sale.total_amount = Decimal("1.00")
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            sale.save()
        line = sale.lines.get()
        line.quantity = Decimal("1")
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            sale.payment.delete()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            sale.receipt.delete()

        cancelled = self._draft(quantities=[SaleQuantity(self.second_variant.id, Decimal("1"))])
        cancel_sale(
            actor=self.cashier_membership,
            sale=cancelled,
            reason="Customer changed the selection before payment.",
        )
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, SaleStatus.CANCELLED)
        with self.assertRaisesMessage(ValidationError, "Only a draft sale"):
            post_sale(
                actor=self.cashier_membership,
                sale=cancelled,
                payment_method="cash",
                telebirr_reference="",
                idempotency_key=uuid.uuid4(),
            )


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentSalesTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="concurrent-sales-owner@example.com",
            password="strong-test-password",
            full_name="Concurrent Sales Owner",
        )
        cashier = User.objects.create_user(
            email="concurrent-sales-cashier@example.com",
            password="strong-test-password",
            full_name="Concurrent Sales Cashier",
        )
        self.business = Business.objects.create(
            name="Concurrent Sales Shop",
            slug="concurrent-sales-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        product = Product.objects.create(business=self.business, name="Canvas Shoe")
        self.first_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CANVAS-40",
            selling_price=Decimal("900"),
            stock_unit=StockUnit.PAIR,
        )
        self.second_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CANVAS-41",
            selling_price=Decimal("900"),
            stock_unit=StockUnit.PAIR,
        )
        for variant in (self.first_variant, self.second_variant):
            post_opening_balance(
                actor=self.owner_membership,
                branch=self.branch,
                variant=variant,
                quantity=Decimal("5"),
                unit_cost=Decimal("300"),
                idempotency_key=uuid.uuid4(),
            )

    def _draft(self, variant: ProductVariant, quantity: Decimal) -> Sale:
        return save_sale_draft(
            actor=self.cashier_membership,
            branch=self.branch,
            sale_date=timezone.localdate(),
            quantities=[SaleQuantity(variant.id, quantity)],
        )

    def test_concurrent_sales_cannot_oversell_or_lose_balance_updates(self) -> None:
        first = self._draft(self.first_variant, Decimal("4"))
        second = self._draft(self.first_variant, Decimal("4"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post(sale_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.cashier_membership.pk)
                sale = Sale.objects.get(pk=sale_id)
                barrier.wait()
                post_sale(
                    actor=membership,
                    sale=sale,
                    payment_method="cash",
                    telebirr_reference="",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=post, args=(first.id,)), Thread(target=post, args=(second.id,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_variant).quantity_on_hand,
            Decimal("1.000"),
        )
        self.assertEqual(Sale.objects.filter(status=SaleStatus.POSTED).count(), 1)
        self.assertEqual(SalePayment.objects.count(), 1)
        self.assertEqual(InternalReceipt.objects.count(), 1)

    def test_concurrent_telebirr_reference_reuse_posts_only_one_sale(self) -> None:
        first = self._draft(self.first_variant, Decimal("1"))
        second = self._draft(self.second_variant, Decimal("1"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post(sale_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.cashier_membership.pk)
                sale = Sale.objects.get(pk=sale_id)
                barrier.wait()
                post_sale(
                    actor=membership,
                    sale=sale,
                    payment_method="telebirr",
                    telebirr_reference="TX-CONCURRENT-1",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=post, args=(first.id,)), Thread(target=post, args=(second.id,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(SalePayment.objects.count(), 1)
        self.assertEqual(Sale.objects.filter(status=SaleStatus.POSTED).count(), 1)
        total_quantity = sum(
            InventoryBalance.objects.filter(
                variant__in=(self.first_variant, self.second_variant)
            ).values_list("quantity_on_hand", flat=True),
            Decimal("0.000"),
        )
        self.assertEqual(total_quantity, Decimal("9.000"))

    def test_concurrent_same_sale_and_key_posts_exactly_once(self) -> None:
        sale = self._draft(self.first_variant, Decimal("2"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()
        key = uuid.uuid4()

        def post() -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.cashier_membership.pk)
                current_sale = Sale.objects.get(pk=sale.pk)
                barrier.wait()
                post_sale(
                    actor=membership,
                    sale=current_sale,
                    payment_method="cash",
                    telebirr_reference="",
                    idempotency_key=key,
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=post), Thread(target=post)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([outcomes.get_nowait(), outcomes.get_nowait()], ["posted", "posted"])
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_variant).quantity_on_hand,
            Decimal("3.000"),
        )
        self.assertEqual(SalePayment.objects.count(), 1)
        self.assertEqual(InternalReceipt.objects.count(), 1)
        self.assertEqual(SalePostingKey.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(movement_type=InventoryMovementType.SALE).count(),
            1,
        )

    def test_concurrent_cross_sale_key_reuse_posts_only_one_sale(self) -> None:
        first = self._draft(self.first_variant, Decimal("1"))
        second = self._draft(self.second_variant, Decimal("1"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()
        key = uuid.uuid4()

        def post(sale_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.cashier_membership.pk)
                sale = Sale.objects.get(pk=sale_id)
                barrier.wait()
                post_sale(
                    actor=membership,
                    sale=sale,
                    payment_method="cash",
                    telebirr_reference="",
                    idempotency_key=key,
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=post, args=(first.id,)), Thread(target=post, args=(second.id,))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(SalePostingKey.objects.count(), 1)
        self.assertEqual(SalePayment.objects.count(), 1)
        self.assertEqual(InternalReceipt.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(movement_type=InventoryMovementType.SALE).count(),
            1,
        )
