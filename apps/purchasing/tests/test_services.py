import uuid
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import IntegrityError, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import InventoryBalance, InventoryMovement
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseStatus,
    Supplier,
)
from apps.purchasing.services import (
    ReceiptQuantity,
    approve_purchase,
    receive_purchase,
)


class PurchasingServiceTests(TestCase):
    business: Business
    branch: Branch
    owner_membership: BusinessMembership
    stock_membership: BusinessMembership
    cashier_membership: BusinessMembership
    supplier: Supplier
    purchase: Purchase
    first_line: PurchaseLine
    second_line: PurchaseLine

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="purchase-owner@example.com",
            password="strong-test-password",
            full_name="Purchase Owner",
        )
        stock_employee = User.objects.create_user(
            email="purchase-stock@example.com",
            password="strong-test-password",
            full_name="Purchase Stock",
        )
        cashier = User.objects.create_user(
            email="purchase-cashier@example.com",
            password="strong-test-password",
            full_name="Purchase Cashier",
        )
        self.business = Business.objects.create(name="Fashion Shop", slug="fashion-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.stock_membership = BusinessMembership.objects.create(
            business=self.business,
            user=stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.supplier = Supplier.objects.create(
            business=self.business,
            name="Addis Footwear Wholesale",
        )
        product = Product.objects.create(business=self.business, name="Running Shoe")
        first_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RUN-BLK-42",
            size="42",
            color="Black",
            selling_price=Decimal("1600"),
            stock_unit=StockUnit.PAIR,
        )
        second_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RUN-WHT-41",
            size="41",
            color="White",
            selling_price=Decimal("1550"),
            stock_unit=StockUnit.PAIR,
        )
        self.purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-TEST-1",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        self.first_line = PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=first_variant,
            ordered_quantity=Decimal("10"),
            unit_cost=Decimal("500"),
        )
        self.second_line = PurchaseLine.objects.create(
            business=self.business,
            purchase=self.purchase,
            variant=second_variant,
            ordered_quantity=Decimal("5"),
            unit_cost=Decimal("600"),
        )

    def test_approval_captures_line_snapshots_without_stock_effect(self) -> None:
        approved = approve_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
        )

        self.first_line.refresh_from_db()
        self.assertEqual(approved.status, PurchaseStatus.APPROVED)
        self.assertEqual(self.first_line.product_name_snapshot, "Running Shoe")
        self.assertEqual(self.first_line.sku_snapshot, "RUN-BLK-42")
        self.assertEqual(self.first_line.unit_snapshot, StockUnit.PAIR)
        self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_empty_purchase_cannot_be_approved(self) -> None:
        empty = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-EMPTY",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )

        with self.assertRaisesMessage(ValidationError, "at least one"):
            approve_purchase(actor=self.owner_membership, purchase=empty)

        empty.refresh_from_db()
        self.assertEqual(empty.status, PurchaseStatus.DRAFT)

    def test_stock_employee_cannot_approve_purchase(self) -> None:
        with self.assertRaises(PermissionDenied):
            approve_purchase(actor=self.stock_membership, purchase=self.purchase)

    def test_approved_purchase_lines_cannot_be_changed_or_deleted(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        self.first_line.unit_cost = Decimal("1")

        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            self.first_line.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            self.first_line.delete()

    def test_approved_purchase_details_cannot_be_changed_or_deleted(self) -> None:
        stale_purchase = Purchase.objects.get(pk=self.purchase.pk)
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        self.purchase.supplier_reference = "REWRITTEN"

        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            self.purchase.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            stale_purchase.delete()

    def test_approved_status_requires_actor_and_timestamp(self) -> None:
        self.purchase.status = PurchaseStatus.APPROVED

        with self.assertRaises(ValidationError):
            self.purchase.save()

    def test_partial_and_complete_receipts_update_purchase_and_inventory(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        first_receipt = receive_purchase(
            actor=self.stock_membership,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(self.first_line.id, Decimal("4"))],
            idempotency_key=uuid.uuid4(),
        )

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.PARTIALLY_RECEIVED)
        self.assertEqual(first_receipt.lines.count(), 1)
        first_balance = InventoryBalance.objects.get(variant=self.first_line.variant)
        self.assertEqual(first_balance.quantity_on_hand, Decimal("4.000"))
        self.assertEqual(first_balance.average_unit_cost, Decimal("500.000000"))

        receive_purchase(
            actor=self.stock_membership,
            purchase=self.purchase,
            quantities=[
                ReceiptQuantity(self.first_line.id, Decimal("6")),
                ReceiptQuantity(self.second_line.id, Decimal("5")),
            ],
            idempotency_key=uuid.uuid4(),
        )

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, PurchaseStatus.RECEIVED)
        self.assertEqual(GoodsReceipt.objects.count(), 2)
        self.assertEqual(GoodsReceiptLine.objects.count(), 3)
        self.assertEqual(InventoryMovement.objects.count(), 3)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_line.variant).quantity_on_hand,
            Decimal("10.000"),
        )

    def test_receipt_idempotency_replays_without_duplicate_effects(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        key = uuid.uuid4()

        first = receive_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(self.first_line.id, Decimal("2"))],
            idempotency_key=key,
        )
        replay = receive_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(self.first_line.id, Decimal("2"))],
            idempotency_key=key,
        )

        self.assertEqual(first.id, replay.id)
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_line.variant).quantity_on_hand,
            Decimal("2.000"),
        )

    def test_receipt_idempotency_race_across_purchases_returns_validation_error(
        self,
    ) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        key = uuid.uuid4()
        existing = GoodsReceipt.objects.create(
            business=self.business,
            branch=self.branch,
            purchase=self.purchase,
            internal_number="GRN-EXISTING",
            idempotency_key=key,
            received_by=self.owner_membership,
            posted_at=timezone.now(),
        )
        second_purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-SECOND",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        second_line = PurchaseLine.objects.create(
            business=self.business,
            purchase=second_purchase,
            variant=self.first_line.variant,
            ordered_quantity=Decimal("1"),
            unit_cost=Decimal("500"),
        )
        approve_purchase(actor=self.owner_membership, purchase=second_purchase)
        missing_receipt = GoodsReceipt.objects.none()
        existing_receipt = GoodsReceipt.objects.filter(pk=existing.pk)
        integrity_error = IntegrityError("duplicate idempotency key")

        with (
            patch(
                "apps.purchasing.services.GoodsReceipt.objects.filter",
                side_effect=(missing_receipt, existing_receipt),
            ),
            patch(
                "apps.purchasing.services.GoodsReceipt.objects.create",
                side_effect=integrity_error,
            ),
            self.assertRaisesMessage(
                ValidationError,
                "idempotency key belongs to another receipt",
            ) as raised,
        ):
            receive_purchase(
                actor=self.owner_membership,
                purchase=second_purchase,
                quantities=[ReceiptQuantity(second_line.id, Decimal("1"))],
                idempotency_key=key,
            )

        self.assertIs(raised.exception.__cause__, integrity_error)
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_receipt_idempotency_validation_race_across_purchases_is_translated(
        self,
    ) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        key = uuid.uuid4()
        existing = GoodsReceipt.objects.create(
            business=self.business,
            branch=self.branch,
            purchase=self.purchase,
            internal_number="GRN-VALIDATION-EXISTING",
            idempotency_key=key,
            received_by=self.owner_membership,
            posted_at=timezone.now(),
        )
        second_purchase = Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-VALIDATION-SECOND",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        second_line = PurchaseLine.objects.create(
            business=self.business,
            purchase=second_purchase,
            variant=self.first_line.variant,
            ordered_quantity=Decimal("1"),
            unit_cost=Decimal("500"),
        )
        approve_purchase(actor=self.owner_membership, purchase=second_purchase)
        missing_receipt = GoodsReceipt.objects.none()
        existing_receipt = GoodsReceipt.objects.filter(pk=existing.pk)
        validation_error = ValidationError(
            {
                NON_FIELD_ERRORS: [
                    ValidationError(
                        "Receipt idempotency key must be unique.",
                        code="unique_together",
                        params={
                            "unique_check": ("business", "idempotency_key"),
                        },
                    )
                ]
            }
        )

        with (
            patch(
                "apps.purchasing.services.GoodsReceipt.objects.filter",
                side_effect=(missing_receipt, existing_receipt),
            ),
            patch(
                "apps.purchasing.services.GoodsReceipt.objects.create",
                side_effect=validation_error,
            ),
            self.assertRaisesMessage(
                ValidationError,
                "idempotency key belongs to another receipt",
            ) as raised,
        ):
            receive_purchase(
                actor=self.owner_membership,
                purchase=second_purchase,
                quantities=[ReceiptQuantity(second_line.id, Decimal("1"))],
                idempotency_key=key,
            )

        self.assertIs(raised.exception.__cause__, validation_error)
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertFalse(InventoryMovement.objects.exists())

    def test_over_receipt_rolls_back_without_stock_effect(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

        with self.assertRaisesMessage(ValidationError, "remaining quantity"):
            receive_purchase(
                actor=self.owner_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("11"))],
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_failure_on_one_inventory_line_rolls_back_entire_receipt(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        self.second_line.variant.is_active = False
        self.second_line.variant.save(update_fields=("is_active",))

        with self.assertRaises(ValidationError):
            receive_purchase(
                actor=self.owner_membership,
                purchase=self.purchase,
                quantities=[
                    ReceiptQuantity(self.first_line.id, Decimal("2")),
                    ReceiptQuantity(self.second_line.id, Decimal("2")),
                ],
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(GoodsReceiptLine.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_purchasing_constraint_names_are_translated_during_posting(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        raw_error = ValidationError(
            'Constraint "purchasing_receipt_line_scope_matches" is violated.'
        )

        with (
            patch(
                "apps.purchasing.services.GoodsReceiptLine.objects.create",
                side_effect=raw_error,
            ),
            self.assertRaisesMessage(ValidationError, "purchasing rule") as raised,
        ):
            receive_purchase(
                actor=self.owner_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("1"))],
                idempotency_key=uuid.uuid4(),
            )

        self.assertIs(raised.exception.__cause__, raw_error)
        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())

    def test_receipt_fails_closed_if_stock_unit_changed_after_approval(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        ProductVariant.objects.filter(pk=self.first_line.variant_id).update(
            stock_unit=StockUnit.KILOGRAM
        )

        with self.assertRaisesMessage(ValidationError, "changed after purchase approval"):
            receive_purchase(
                actor=self.owner_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("1"))],
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(GoodsReceipt.objects.exists())
        self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(InventoryBalance.objects.exists())

    def test_cashier_cannot_receive_purchase(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

        with self.assertRaises(PermissionDenied):
            receive_purchase(
                actor=self.cashier_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("1"))],
                idempotency_key=uuid.uuid4(),
            )

    def test_stock_employee_cannot_receive_for_another_branch(self) -> None:
        other_branch = Branch.objects.create(
            business=self.business,
            name="Second Store",
            code="second",
        )
        self.purchase.branch = other_branch
        self.purchase.save(update_fields=("branch", "updated_at"))
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

        with self.assertRaises(PermissionDenied):
            receive_purchase(
                actor=self.stock_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("1"))],
                idempotency_key=uuid.uuid4(),
            )

    def test_purchase_rejects_cross_business_supplier(self) -> None:
        other_business = Business.objects.create(name="Other", slug="other")
        other_supplier = Supplier.objects.create(business=other_business, name="Other Supplier")
        self.purchase.supplier = other_supplier

        with self.assertRaises(ValidationError):
            self.purchase.save()


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentPurchaseReceiptTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="concurrent-owner@example.com",
            password="strong-test-password",
            full_name="Concurrent Owner",
        )
        stock_employee = User.objects.create_user(
            email="concurrent-stock@example.com",
            password="strong-test-password",
            full_name="Concurrent Stock",
        )
        business = Business.objects.create(name="Concurrent Shop", slug="concurrent-shop")
        branch = Branch.objects.create(business=business, name="Main", code="main")
        self.owner_membership = BusinessMembership.objects.create(
            business=business,
            user=owner,
            assigned_branch=branch,
            role=MembershipRole.OWNER,
        )
        self.stock_membership = BusinessMembership.objects.create(
            business=business,
            user=stock_employee,
            assigned_branch=branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        supplier = Supplier.objects.create(business=business, name="Supplier")
        product = Product.objects.create(business=business, name="Canvas Shoe")
        variant = ProductVariant.objects.create(
            business=business,
            product=product,
            sku="CANVAS-42",
            selling_price=Decimal("900"),
            stock_unit=StockUnit.PAIR,
        )
        self.purchase = Purchase.objects.create(
            business=business,
            branch=branch,
            supplier=supplier,
            internal_number="PUR-CONCURRENT",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        self.line = PurchaseLine.objects.create(
            business=business,
            purchase=self.purchase,
            variant=variant,
            ordered_quantity=Decimal("10"),
            unit_cost=Decimal("300"),
        )
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)

    def test_concurrent_receipts_cannot_over_receive_or_lose_balance_update(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def receive() -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.stock_membership.pk)
                purchase = Purchase.objects.get(pk=self.purchase.pk)
                barrier.wait()
                receive_purchase(
                    actor=membership,
                    purchase=purchase,
                    quantities=[ReceiptQuantity(self.line.id, Decimal("7"))],
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=receive), Thread(target=receive)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(GoodsReceiptLine.objects.count(), 1)
        balance = InventoryBalance.objects.get(variant=self.line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("7.000"))

    def test_concurrent_first_receipts_for_same_variant_do_not_lose_an_update(self) -> None:
        second_purchase = Purchase.objects.create(
            business=self.purchase.business,
            branch=self.purchase.branch,
            supplier=self.purchase.supplier,
            internal_number="PUR-CONCURRENT-2",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        second_line = PurchaseLine.objects.create(
            business=self.purchase.business,
            purchase=second_purchase,
            variant=self.line.variant,
            ordered_quantity=Decimal("5"),
            unit_cost=Decimal("400"),
        )
        approve_purchase(actor=self.owner_membership, purchase=second_purchase)
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def receive(purchase_id: uuid.UUID, line_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.stock_membership.pk)
                purchase = Purchase.objects.get(pk=purchase_id)
                barrier.wait()
                receive_purchase(
                    actor=membership,
                    purchase=purchase,
                    quantities=[ReceiptQuantity(line_id, Decimal("5"))],
                    idempotency_key=uuid.uuid4(),
                )
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [
            Thread(target=receive, args=(self.purchase.id, self.line.id)),
            Thread(target=receive, args=(second_purchase.id, second_line.id)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual([outcomes.get_nowait(), outcomes.get_nowait()], ["posted", "posted"])
        balance = InventoryBalance.objects.get(variant=self.line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("10.000"))
        self.assertEqual(balance.inventory_value, Decimal("3500.000000"))
        self.assertEqual(balance.average_unit_cost, Decimal("350.000000"))

    def test_concurrent_same_key_across_purchases_posts_only_one_receipt(self) -> None:
        second_purchase = Purchase.objects.create(
            business=self.purchase.business,
            branch=self.purchase.branch,
            supplier=self.purchase.supplier,
            internal_number="PUR-SAME-KEY",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        second_line = PurchaseLine.objects.create(
            business=self.purchase.business,
            purchase=second_purchase,
            variant=self.line.variant,
            ordered_quantity=Decimal("5"),
            unit_cost=Decimal("400"),
        )
        approve_purchase(actor=self.owner_membership, purchase=second_purchase)
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()
        key = uuid.uuid4()

        def receive(purchase_id: uuid.UUID, line_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.stock_membership.pk)
                purchase = Purchase.objects.get(pk=purchase_id)
                barrier.wait()
                receive_purchase(
                    actor=membership,
                    purchase=purchase,
                    quantities=[ReceiptQuantity(line_id, Decimal("5"))],
                    idempotency_key=key,
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [
            Thread(target=receive, args=(self.purchase.id, self.line.id)),
            Thread(target=receive, args=(second_purchase.id, second_line.id)),
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
        self.assertEqual(GoodsReceipt.objects.count(), 1)
        self.assertEqual(InventoryMovement.objects.count(), 1)
        balance = InventoryBalance.objects.get(variant=self.line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("5.000"))
