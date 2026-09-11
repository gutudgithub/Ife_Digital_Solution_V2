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
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
)
from apps.inventory.services import post_inventory_adjustment
from apps.purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Purchase,
    PurchaseLine,
    PurchaseReturn,
    PurchaseReturnLine,
    PurchaseReturnOperationType,
    PurchaseReturnPostingKey,
    PurchaseReturnReversal,
    PurchaseReturnStatus,
    PurchaseStatus,
    Supplier,
)
from apps.purchasing.services import (
    ReceiptQuantity,
    ReturnQuantity,
    approve_purchase,
    cancel_purchase_return,
    post_purchase_return,
    purchase_line_progress,
    receive_purchase,
    reverse_purchase_return,
    save_purchase_return_draft,
    supplier_activity_summaries,
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
        raw_error = ValidationError('Constraint "purchasing_unique_line_per_receipt" is violated.')

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

    def test_unrelated_purchasing_prefix_validation_is_not_translated(self) -> None:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        raw_error = ValidationError("The purchasing_reference field is invalid.")

        with (
            patch(
                "apps.purchasing.services.GoodsReceiptLine.objects.create",
                side_effect=raw_error,
            ),
            self.assertRaises(ValidationError) as raised,
        ):
            receive_purchase(
                actor=self.owner_membership,
                purchase=self.purchase,
                quantities=[ReceiptQuantity(self.first_line.id, Decimal("1"))],
                idempotency_key=uuid.uuid4(),
            )

        self.assertIs(raised.exception, raw_error)

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

    def _receive_return_source(
        self,
        *,
        first_quantity: Decimal = Decimal("10"),
        second_quantity: Decimal | None = None,
    ) -> tuple[GoodsReceiptLine, GoodsReceiptLine | None]:
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        quantities = [ReceiptQuantity(self.first_line.id, first_quantity)]
        if second_quantity is not None:
            quantities.append(ReceiptQuantity(self.second_line.id, second_quantity))
        receipt = receive_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
            quantities=quantities,
            idempotency_key=uuid.uuid4(),
        )
        first_receipt_line = receipt.lines.get(purchase_line=self.first_line)
        second_receipt_line = (
            receipt.lines.get(purchase_line=self.second_line)
            if second_quantity is not None
            else None
        )
        return first_receipt_line, second_receipt_line

    def _draft_return(
        self,
        receipt_line: GoodsReceiptLine,
        quantity: Decimal,
        *,
        actor: BusinessMembership | None = None,
    ) -> PurchaseReturn:
        return save_purchase_return_draft(
            actor=actor or self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Incorrect size delivered",
            quantities=[ReturnQuantity(receipt_line.id, quantity)],
            supplier_document_reference="SUP-RETURN-1",
        )

    def test_stock_employee_can_prepare_draft_without_inventory_effect(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("4"))
        movement_count = InventoryMovement.objects.count()

        purchase_return = self._draft_return(
            receipt_line,
            Decimal("2"),
            actor=self.stock_membership,
        )

        line = purchase_return.lines.get()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.DRAFT)
        self.assertEqual(line.receipt_line, receipt_line)
        self.assertEqual(line.product_name_snapshot, "Running Shoe")
        self.assertEqual(line.sku_snapshot, "RUN-BLK-42")
        self.assertEqual(line.receipt_unit_cost, Decimal("500.000000"))
        self.assertEqual(line.supplier_reference_total, Decimal("1000.000000"))
        self.assertEqual(InventoryMovement.objects.count(), movement_count)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_line.variant).quantity_on_hand,
            Decimal("4.000"),
        )

    def test_post_uses_current_average_and_preserves_supplier_reference_cost(self) -> None:
        receipt_line, _ = self._receive_return_source()
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.first_line.variant,
            operation_type="adjustment_in",
            quantity=Decimal("10"),
            unit_cost=Decimal("700"),
            reason="Additional valued stock",
            idempotency_key=uuid.uuid4(),
        )
        purchase_return = self._draft_return(receipt_line, Decimal("2"))

        posted = post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )

        line = posted.lines.get()
        movement = InventoryMovement.objects.get(
            movement_type=InventoryMovementType.PURCHASE_RETURN,
        )
        balance = InventoryBalance.objects.get(variant=self.first_line.variant)
        self.assertEqual(posted.status, PurchaseReturnStatus.POSTED)
        self.assertEqual(line.receipt_unit_cost, Decimal("500.000000"))
        self.assertEqual(line.supplier_reference_total, Decimal("1000.000000"))
        self.assertEqual(line.assigned_inventory_unit_cost, Decimal("600.000000"))
        self.assertEqual(line.inventory_value_delta, Decimal("-1200.000000"))
        self.assertEqual(movement.source_type, InventorySourceType.PURCHASE_RETURN_LINE)
        self.assertEqual(movement.source_id, line.id)
        self.assertEqual(balance.quantity_on_hand, Decimal("18.000"))
        self.assertEqual(balance.inventory_value, Decimal("10800.000000"))

    def test_multiple_partial_returns_update_derived_net_received_quantity(self) -> None:
        receipt_line, _ = self._receive_return_source()
        first = self._draft_return(receipt_line, Decimal("3"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=first,
            idempotency_key=uuid.uuid4(),
        )
        second = self._draft_return(receipt_line, Decimal("2"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=second,
            idempotency_key=uuid.uuid4(),
        )

        progress = purchase_line_progress(self.purchase)[0]

        self.assertEqual(progress.received_quantity, Decimal("10.000"))
        self.assertEqual(progress.returned_quantity, Decimal("5.000"))
        self.assertEqual(progress.net_received_quantity, Decimal("5.000"))
        self.assertEqual(progress.remaining_quantity, Decimal("0.000"))

    def test_draft_and_post_reject_quantity_above_unreturned_receipt_amount(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("4"))

        with self.assertRaisesMessage(ValidationError, "unreturned receipt quantity"):
            self._draft_return(receipt_line, Decimal("5"))

        first = self._draft_return(receipt_line, Decimal("3"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=first,
            idempotency_key=uuid.uuid4(),
        )
        stale = PurchaseReturn.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            purchase=self.purchase,
            internal_number="PRN-STALE",
            return_date=timezone.localdate(),
            reason="Stale draft",
            created_by=self.owner_membership,
        )
        PurchaseReturnLine.objects.create(
            business=self.business,
            purchase_return=stale,
            receipt_line=receipt_line,
            variant=receipt_line.variant,
            returned_quantity=Decimal("2"),
            unit_snapshot=receipt_line.unit_snapshot,
        )

        with self.assertRaisesMessage(ValidationError, "unreturned receipt quantity"):
            post_purchase_return(
                actor=self.owner_membership,
                purchase_return=stale,
                idempotency_key=uuid.uuid4(),
            )

        stale.refresh_from_db()
        self.assertEqual(stale.status, PurchaseReturnStatus.DRAFT)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.first_line.variant).quantity_on_hand,
            Decimal("1.000"),
        )

    def test_stock_shortage_rolls_back_every_return_line(self) -> None:
        first_receipt_line, second_receipt_line = self._receive_return_source(
            first_quantity=Decimal("2"),
            second_quantity=Decimal("2"),
        )
        assert second_receipt_line is not None
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.first_line.variant,
            operation_type="adjustment_out",
            quantity=Decimal("2"),
            reason="Stock consumed before return",
            idempotency_key=uuid.uuid4(),
        )
        purchase_return = save_purchase_return_draft(
            actor=self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Return both sizes",
            quantities=[
                ReturnQuantity(first_receipt_line.id, Decimal("1")),
                ReturnQuantity(second_receipt_line.id, Decimal("1")),
            ],
        )
        movement_count = InventoryMovement.objects.count()

        with self.assertRaisesMessage(ValidationError, "make stock negative"):
            post_purchase_return(
                actor=self.owner_membership,
                purchase_return=purchase_return,
                idempotency_key=uuid.uuid4(),
            )

        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.DRAFT)
        self.assertEqual(InventoryMovement.objects.count(), movement_count)
        self.assertEqual(
            InventoryBalance.objects.get(variant=self.second_line.variant).quantity_on_hand,
            Decimal("2.000"),
        )
        self.assertFalse(PurchaseReturnPostingKey.objects.exists())

    def test_return_posting_and_reversal_are_idempotent_and_keys_cannot_cross_operations(
        self,
    ) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("4"))
        purchase_return = self._draft_return(receipt_line, Decimal("2"))
        posting_key = uuid.uuid4()

        first_post = post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=posting_key,
        )
        replay = post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=posting_key,
        )

        self.assertEqual(first_post.id, replay.id)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.PURCHASE_RETURN
            ).count(),
            1,
        )
        with self.assertRaisesMessage(ValidationError, "another purchase return operation"):
            reverse_purchase_return(
                actor=self.owner_membership,
                purchase_return=purchase_return,
                reason="Wrong supplier return",
                idempotency_key=posting_key,
            )

        reversal_key = uuid.uuid4()
        reversal = reverse_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            reason="Supplier rejected shipment",
            idempotency_key=reversal_key,
        )
        reversal_replay = reverse_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            reason="Supplier rejected shipment",
            idempotency_key=reversal_key,
        )

        self.assertEqual(reversal.id, reversal_replay.id)
        self.assertEqual(PurchaseReturnReversal.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.PURCHASE_RETURN_REVERSAL
            ).count(),
            1,
        )
        with self.assertRaisesMessage(ValidationError, "Only a posted"):
            reverse_purchase_return(
                actor=self.owner_membership,
                purchase_return=purchase_return,
                reason="Second reversal",
                idempotency_key=uuid.uuid4(),
            )

    def test_reversal_restores_original_assigned_inventory_cost(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("5"))
        purchase_return = self._draft_return(receipt_line, Decimal("2"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )
        returned_line = purchase_return.lines.get()
        returned_cost = returned_line.assigned_inventory_unit_cost
        post_inventory_adjustment(
            actor=self.owner_membership,
            branch=self.branch,
            variant=self.first_line.variant,
            operation_type="adjustment_in",
            quantity=Decimal("1"),
            unit_cost=Decimal("1000"),
            reason="Cost changed before reversal",
            idempotency_key=uuid.uuid4(),
        )

        reverse_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            reason="Return entered against wrong supplier document",
            idempotency_key=uuid.uuid4(),
        )

        reversal_movement = InventoryMovement.objects.get(
            movement_type=InventoryMovementType.PURCHASE_RETURN_REVERSAL
        )
        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.REVERSED)
        self.assertEqual(reversal_movement.unit_cost, returned_cost)
        self.assertEqual(reversal_movement.value_delta, Decimal("1000.000000"))
        self.assertEqual(
            reversal_movement.source_type,
            InventorySourceType.PURCHASE_RETURN_REVERSAL,
        )
        self.assertEqual(reversal_movement.source_id, returned_line.id)

    def test_inactive_historical_supplier_and_variant_remain_returnable(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("2"))
        self.supplier.is_active = False
        self.supplier.save(update_fields=("is_active", "updated_at"))
        self.first_line.variant.is_active = False
        self.first_line.variant.save(update_fields=("is_active", "updated_at"))
        purchase_return = self._draft_return(receipt_line, Decimal("1"))

        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )

        purchase_return.refresh_from_db()
        self.assertEqual(purchase_return.status, PurchaseReturnStatus.POSTED)

    def test_role_boundaries_apply_to_prepare_post_cancel_and_reverse(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("3"))
        with self.assertRaises(PermissionDenied):
            self._draft_return(
                receipt_line,
                Decimal("1"),
                actor=self.cashier_membership,
            )
        stock_draft = self._draft_return(
            receipt_line,
            Decimal("1"),
            actor=self.stock_membership,
        )

        with self.assertRaises(PermissionDenied):
            post_purchase_return(
                actor=self.stock_membership,
                purchase_return=stock_draft,
                idempotency_key=uuid.uuid4(),
            )
        with self.assertRaises(PermissionDenied):
            cancel_purchase_return(
                actor=self.stock_membership,
                purchase_return=stock_draft,
            )

        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=stock_draft,
            idempotency_key=uuid.uuid4(),
        )
        with self.assertRaises(PermissionDenied):
            reverse_purchase_return(
                actor=self.stock_membership,
                purchase_return=stock_draft,
                reason="Unauthorized reversal",
                idempotency_key=uuid.uuid4(),
            )

    def test_cancelling_draft_has_no_inventory_effect(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("3"))
        purchase_return = self._draft_return(receipt_line, Decimal("1"))
        movement_count = InventoryMovement.objects.count()

        cancelled = cancel_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
        )

        self.assertEqual(cancelled.status, PurchaseReturnStatus.CANCELLED)
        self.assertEqual(cancelled.cancelled_by, self.owner_membership)
        self.assertEqual(InventoryMovement.objects.count(), movement_count)

    def test_posted_return_models_are_immutable_through_instance_operations(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("2"))
        purchase_return = self._draft_return(receipt_line, Decimal("1"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )
        purchase_return.refresh_from_db()
        line = purchase_return.lines.get()
        key = purchase_return.posting_key
        purchase_return.reason = "Changed"
        line.returned_quantity = Decimal("2")

        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            purchase_return.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            purchase_return.delete()
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            line.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            line.delete()
        assert key is not None
        key.source_id = uuid.uuid4()
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            key.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            key.delete()

    def test_supplier_activity_summary_uses_posted_branch_scoped_evidence(self) -> None:
        receipt_line, _ = self._receive_return_source(first_quantity=Decimal("3"))
        draft = self._draft_return(receipt_line, Decimal("1"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=draft,
            idempotency_key=uuid.uuid4(),
        )
        Purchase.objects.create(
            business=self.business,
            branch=self.branch,
            supplier=self.supplier,
            internal_number="PUR-DRAFT-NOT-ACTIVITY",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )

        owner_summary = supplier_activity_summaries(actor=self.owner_membership)[0]
        stock_summary = supplier_activity_summaries(actor=self.stock_membership)[0]

        for summary in (owner_summary, stock_summary):
            self.assertEqual(summary.purchase_count, 1)
            self.assertEqual(summary.receipt_count, 1)
            self.assertEqual(summary.posted_return_count, 1)
            self.assertEqual(summary.return_reference_total, Decimal("500.000000"))
            self.assertEqual(summary.latest_purchase_date, self.purchase.purchase_date)
            self.assertIsNotNone(summary.latest_receipt_at)
            self.assertEqual(summary.latest_return_date, draft.return_date)


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


@skipUnlessDBFeature("has_select_for_update")
class ConcurrentPurchaseReturnTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="return-concurrent-owner@example.com",
            password="strong-test-password",
            full_name="Return Concurrent Owner",
        )
        business = Business.objects.create(
            name="Return Concurrent Shop",
            slug="return-concurrent-shop",
        )
        branch = Branch.objects.create(business=business, name="Main", code="main")
        self.owner_membership = BusinessMembership.objects.create(
            business=business,
            user=owner,
            assigned_branch=branch,
            role=MembershipRole.OWNER,
        )
        supplier = Supplier.objects.create(business=business, name="Supplier")
        product = Product.objects.create(business=business, name="Leather Boot")
        variant = ProductVariant.objects.create(
            business=business,
            product=product,
            sku="BOOT-42",
            selling_price=Decimal("1800"),
            stock_unit=StockUnit.PAIR,
        )
        self.purchase = Purchase.objects.create(
            business=business,
            branch=branch,
            supplier=supplier,
            internal_number="PUR-RETURN-CONCURRENT",
            purchase_date=timezone.localdate(),
            created_by=self.owner_membership,
        )
        line = PurchaseLine.objects.create(
            business=business,
            purchase=self.purchase,
            variant=variant,
            ordered_quantity=Decimal("10"),
            unit_cost=Decimal("600"),
        )
        approve_purchase(actor=self.owner_membership, purchase=self.purchase)
        receipt = receive_purchase(
            actor=self.owner_membership,
            purchase=self.purchase,
            quantities=[ReceiptQuantity(line.id, Decimal("10"))],
            idempotency_key=uuid.uuid4(),
        )
        self.receipt_line = receipt.lines.get()

    def _create_return(self, quantity: Decimal) -> PurchaseReturn:
        return save_purchase_return_draft(
            actor=self.owner_membership,
            purchase=self.purchase,
            return_date=timezone.localdate(),
            reason="Concurrent supplier return",
            quantities=[ReturnQuantity(self.receipt_line.id, quantity)],
        )

    def test_concurrent_returns_cannot_exceed_receipt_or_lose_balance_update(self) -> None:
        first = self._create_return(Decimal("7"))
        second = self._create_return(Decimal("7"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post(return_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.owner_membership.pk)
                purchase_return = PurchaseReturn.objects.get(pk=return_id)
                barrier.wait()
                post_purchase_return(
                    actor=membership,
                    purchase_return=purchase_return,
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [
            Thread(target=post, args=(first.id,)),
            Thread(target=post, args=(second.id,)),
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
        self.assertEqual(
            PurchaseReturn.objects.filter(status=PurchaseReturnStatus.POSTED).count(),
            1,
        )
        balance = InventoryBalance.objects.get(variant=self.receipt_line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("3.000"))

    def test_concurrent_returns_reusing_one_key_post_exactly_once(self) -> None:
        first = self._create_return(Decimal("1"))
        second = self._create_return(Decimal("1"))
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()
        key = uuid.uuid4()

        def post(return_id: uuid.UUID) -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.owner_membership.pk)
                purchase_return = PurchaseReturn.objects.get(pk=return_id)
                barrier.wait()
                post_purchase_return(
                    actor=membership,
                    purchase_return=purchase_return,
                    idempotency_key=key,
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [
            Thread(target=post, args=(first.id,)),
            Thread(target=post, args=(second.id,)),
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
        self.assertEqual(
            PurchaseReturnPostingKey.objects.filter(
                operation_type=PurchaseReturnOperationType.RETURN
            ).count(),
            1,
        )
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.PURCHASE_RETURN
            ).count(),
            1,
        )
        balance = InventoryBalance.objects.get(variant=self.receipt_line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("9.000"))

    def test_concurrent_reversal_requests_create_one_compensating_document(self) -> None:
        purchase_return = self._create_return(Decimal("2"))
        post_purchase_return(
            actor=self.owner_membership,
            purchase_return=purchase_return,
            idempotency_key=uuid.uuid4(),
        )
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def reverse() -> None:
            connections.close_all()
            try:
                membership = BusinessMembership.objects.get(pk=self.owner_membership.pk)
                current_return = PurchaseReturn.objects.get(pk=purchase_return.pk)
                barrier.wait()
                reverse_purchase_return(
                    actor=membership,
                    purchase_return=current_return,
                    reason="Concurrent reversal",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("posted")
            finally:
                connections.close_all()

        threads = [Thread(target=reverse), Thread(target=reverse)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["posted", "rejected"],
        )
        self.assertEqual(PurchaseReturnReversal.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                movement_type=InventoryMovementType.PURCHASE_RETURN_REVERSAL
            ).count(),
            1,
        )
        balance = InventoryBalance.objects.get(variant=self.receipt_line.variant)
        self.assertEqual(balance.quantity_on_hand, Decimal("10.000"))
