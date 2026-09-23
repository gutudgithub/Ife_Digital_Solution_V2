import uuid
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    InventorySourceType,
    StockCountApproval,
    StockCountLineRevision,
    StockCountReversal,
    StockCountSession,
    StockCountStatus,
)
from apps.inventory.services import (
    approve_stock_count,
    cancel_stock_count,
    post_inventory_adjustment,
    post_opening_balance,
    record_purchase_receipt_inventory,
    record_purchase_return_inventory,
    record_purchase_return_reversal_inventory,
    record_sale_inventory,
    record_sale_return_inventory,
    record_sale_return_reversal_inventory,
    record_stock_count_quantity,
    record_stock_count_review_evidence,
    return_stock_count_for_recount,
    reverse_stock_count,
    start_stock_count,
    stock_count_review_summary,
    submit_stock_count,
)


class StockCountServiceTests(TestCase):
    business: Business
    branch: Branch
    owner: BusinessMembership
    manager: BusinessMembership
    stock_employee: BusinessMembership
    cashier: BusinessMembership
    pair_variant: ProductVariant
    piece_variant: ProductVariant

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Clothing Shop", slug="clothing-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        memberships: dict[str, BusinessMembership] = {}
        for role in (
            MembershipRole.OWNER,
            MembershipRole.MANAGER,
            MembershipRole.STOCK_EMPLOYEE,
            MembershipRole.CASHIER,
        ):
            user = User.objects.create_user(
                email=f"{role}@example.com",
                password="strong-test-password",
                full_name=role.title(),
            )
            memberships[role] = BusinessMembership.objects.create(
                business=self.business,
                user=user,
                assigned_branch=self.branch,
                role=role,
            )
        self.owner = memberships[MembershipRole.OWNER]
        self.manager = memberships[MembershipRole.MANAGER]
        self.stock_employee = memberships[MembershipRole.STOCK_EMPLOYEE]
        self.cashier = memberships[MembershipRole.CASHIER]
        product = Product.objects.create(business=self.business, name="Pilot Stock")
        self.pair_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHOE-BLK-42",
            size="42",
            color="Black",
            selling_price=Decimal("1500.00"),
            stock_unit=StockUnit.PAIR,
        )
        self.piece_variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="SHIRT-WHT-M",
            size="M",
            color="White",
            selling_price=Decimal("800.00"),
            stock_unit=StockUnit.PIECE,
        )

    def _opening_stock(
        self,
        *,
        variant: ProductVariant,
        quantity: str,
        unit_cost: str,
    ) -> None:
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=variant,
            quantity=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
            idempotency_key=uuid.uuid4(),
        )

    def _start(self) -> StockCountSession:
        return start_stock_count(
            actor=self.owner,
            branch=self.branch,
            count_method_note="Two-person shelf-to-shelf physical count.",
            idempotency_key=uuid.uuid4(),
        )

    def test_start_snapshots_full_branch_and_freezes_inventory_posting(self) -> None:
        self._opening_stock(variant=self.pair_variant, quantity="4", unit_cost="500")
        self.pair_variant.is_active = False
        self.pair_variant.save()

        session = self._start()

        self.assertEqual(session.lines.count(), 2)
        pair_line = session.lines.get(variant=self.pair_variant)
        self.assertEqual(pair_line.system_quantity_snapshot, Decimal("4.000"))
        self.assertEqual(pair_line.average_unit_cost_snapshot, Decimal("500.000000"))
        self.assertEqual(pair_line.inventory_value_snapshot, Decimal("2000.000000"))
        with self.assertRaisesMessage(ValidationError, "temporarily paused"):
            post_inventory_adjustment(
                actor=self.owner,
                branch=self.branch,
                variant=self.piece_variant,
                operation_type="adjustment_in",
                quantity=Decimal("1"),
                unit_cost=Decimal("200"),
                reason="Must wait for count completion",
                idempotency_key=uuid.uuid4(),
            )

    def test_freeze_blocks_every_inventory_posting_entry_point(self) -> None:
        session = self._start()
        timestamp = timezone.now()
        posting_calls = (
            lambda: record_purchase_receipt_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                posted_at=timestamp,
            ),
            lambda: record_purchase_return_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                reason="",
                posted_at=timestamp,
            ),
            lambda: record_purchase_return_reversal_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                reason="",
                posted_at=timestamp,
            ),
            lambda: record_sale_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                posted_at=timestamp,
            ),
            lambda: record_sale_return_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                reason="",
                posted_at=timestamp,
            ),
            lambda: record_sale_return_reversal_inventory(
                actor=self.owner,
                business=self.business,
                branch=self.branch,
                items=[],
                reason="",
                posted_at=timestamp,
            ),
            lambda: post_opening_balance(
                actor=self.owner,
                branch=self.branch,
                variant=self.pair_variant,
                quantity=Decimal("1"),
                unit_cost=Decimal("1"),
                idempotency_key=uuid.uuid4(),
            ),
            lambda: post_inventory_adjustment(
                actor=self.owner,
                branch=self.branch,
                variant=self.pair_variant,
                operation_type="adjustment_in",
                quantity=Decimal("1"),
                unit_cost=Decimal("1"),
                reason="Blocked during count",
                idempotency_key=uuid.uuid4(),
            ),
        )

        for posting_call in posting_calls:
            with self.subTest(posting_call=posting_call):
                with self.assertRaisesMessage(ValidationError, "temporarily paused"):
                    posting_call()

        with self.assertRaisesMessage(ValidationError, "Ask a manager for help"):
            record_sale_inventory(
                actor=self.cashier,
                business=self.business,
                branch=self.branch,
                items=[],
                posted_at=timestamp,
            )
        self.assertEqual(
            StockCountSession.objects.get(pk=session.pk).status,
            StockCountStatus.COUNTING,
        )

    def test_blind_entry_accepts_explicit_zero_and_records_replacements(self) -> None:
        session = self._start()
        line = session.lines.get(variant=self.pair_variant)

        record_stock_count_quantity(
            actor=self.stock_employee,
            line=line,
            physical_quantity=Decimal("0"),
        )
        record_stock_count_quantity(
            actor=self.stock_employee,
            line=line,
            physical_quantity=Decimal("2"),
            replacement_reason="Manager requested a second shelf count.",
        )

        line.refresh_from_db()
        self.assertEqual(line.physical_quantity, Decimal("2.000"))
        self.assertEqual(StockCountLineRevision.objects.filter(line=line).count(), 2)
        with self.assertRaisesMessage(ValidationError, "whole-number"):
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=Decimal("2.5"),
                replacement_reason="Invalid fractional recount.",
            )

    def test_submit_review_approve_and_reverse_reconcile_exactly(self) -> None:
        self._opening_stock(variant=self.pair_variant, quantity="4", unit_cost="500")
        session = self._start()
        for line in session.lines.all():
            quantity = Decimal("3") if line.variant_id == self.pair_variant.id else Decimal("2")
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=quantity,
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        for line in session.lines.all():
            if line.variance_quantity != 0:
                record_stock_count_review_evidence(
                    actor=self.manager,
                    line=line,
                    variance_explanation="Physical shelf count differs from the system snapshot.",
                    exceptional_unit_cost=(
                        Decimal("200")
                        if line.average_unit_cost_snapshot == 0
                        and line.variance_quantity is not None
                        and line.variance_quantity > 0
                        else None
                    ),
                    exceptional_cost_evidence_note=(
                        "Verified against supplier document SUP-COUNT-1."
                        if line.average_unit_cost_snapshot == 0
                        and line.variance_quantity is not None
                        and line.variance_quantity > 0
                        else ""
                    ),
                )

        summary = stock_count_review_summary(actor=self.manager, session=session)
        self.assertEqual(summary.line_count, 2)
        self.assertEqual(summary.positive_variance_line_count, 1)
        self.assertEqual(summary.negative_variance_line_count, 1)
        approval = approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(approval.total_inventory_value_adjustment, Decimal("-100.000000"))
        count_movements = InventoryMovement.objects.filter(source_type="stock_count_line")
        self.assertEqual(count_movements.count(), 2)
        pair_balance = InventoryBalance.objects.get(variant=self.pair_variant)
        piece_balance = InventoryBalance.objects.get(variant=self.piece_variant)
        self.assertEqual(pair_balance.quantity_on_hand, Decimal("3.000"))
        self.assertEqual(pair_balance.inventory_value, Decimal("1500.000000"))
        self.assertEqual(piece_balance.quantity_on_hand, Decimal("2.000"))
        self.assertEqual(piece_balance.inventory_value, Decimal("400.000000"))

        reversal = reverse_stock_count(
            actor=self.owner,
            approval=approval,
            reason="Count evidence was later found to identify the wrong shelf.",
            idempotency_key=uuid.uuid4(),
        )

        self.assertIsInstance(reversal, StockCountReversal)
        pair_balance.refresh_from_db()
        piece_balance.refresh_from_db()
        self.assertEqual(pair_balance.quantity_on_hand, Decimal("4.000"))
        self.assertEqual(pair_balance.inventory_value, Decimal("2000.000000"))
        self.assertEqual(piece_balance.quantity_on_hand, Decimal("0.000"))
        self.assertEqual(piece_balance.inventory_value, Decimal("0.000000"))

    def test_zero_snapshot_cost_positive_variance_requires_evidence(self) -> None:
        session = self._start()
        for line in session.lines.all():
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=Decimal("1"),
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        line = session.lines.get(variant=self.pair_variant)

        with self.assertRaisesMessage(ValidationError, "unit-cost evidence"):
            record_stock_count_review_evidence(
                actor=self.manager,
                line=line,
                variance_explanation="One pair was physically present.",
                exceptional_unit_cost=Decimal("300"),
            )

        self.assertFalse(StockCountApproval.objects.exists())

    def test_submission_requires_every_line_and_snapshot_cost_cannot_be_replaced(self) -> None:
        self._opening_stock(variant=self.pair_variant, quantity="2", unit_cost="300")
        session = self._start()
        pair_line = session.lines.get(variant=self.pair_variant)
        record_stock_count_quantity(
            actor=self.stock_employee,
            line=pair_line,
            physical_quantity=Decimal("1"),
        )
        with self.assertRaisesMessage(ValidationError, "including explicit zeroes"):
            submit_stock_count(actor=self.stock_employee, session=session)

        piece_line = session.lines.get(variant=self.piece_variant)
        record_stock_count_quantity(
            actor=self.stock_employee,
            line=piece_line,
            physical_quantity=Decimal("0"),
        )
        submit_stock_count(actor=self.stock_employee, session=session)
        with self.assertRaisesMessage(ValidationError, "Snapshot cost cannot be replaced"):
            record_stock_count_review_evidence(
                actor=self.manager,
                line=pair_line,
                variance_explanation="One pair was absent.",
                exceptional_unit_cost=Decimal("1"),
                exceptional_cost_evidence_note="Attempted replacement.",
            )

    def test_approval_and_reversal_are_idempotent_and_approved_evidence_is_immutable(
        self,
    ) -> None:
        self._opening_stock(variant=self.pair_variant, quantity="2", unit_cost="300")
        session = self._start()
        for line in session.lines.all():
            physical_quantity = (
                Decimal("1") if line.variant_id == self.pair_variant.id else Decimal("0")
            )
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=physical_quantity,
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        pair_line = session.lines.get(variant=self.pair_variant)
        record_stock_count_review_evidence(
            actor=self.manager,
            line=pair_line,
            variance_explanation="One pair was not found.",
        )
        approval_key = uuid.uuid4()
        approval = approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=approval_key,
        )
        replayed_approval = approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=approval_key,
        )

        self.assertEqual(replayed_approval.id, approval.id)
        self.assertEqual(
            InventoryMovement.objects.filter(
                source_type=InventorySourceType.STOCK_COUNT_LINE
            ).count(),
            1,
        )
        pair_line.refresh_from_db()
        pair_line.variance_explanation = "Changed evidence"
        with self.assertRaisesMessage(ValidationError, "Completed stock-count lines"):
            pair_line.save()

        reversal_key = uuid.uuid4()
        reversal = reverse_stock_count(
            actor=self.owner,
            approval=approval,
            reason="Approved evidence identified the wrong shelf.",
            idempotency_key=reversal_key,
        )
        replayed_reversal = reverse_stock_count(
            actor=self.owner,
            approval=approval,
            reason="Approved evidence identified the wrong shelf.",
            idempotency_key=reversal_key,
        )
        self.assertEqual(replayed_reversal.id, reversal.id)
        self.assertEqual(
            InventoryMovement.objects.filter(
                source_type=InventorySourceType.STOCK_COUNT_REVERSAL
            ).count(),
            1,
        )

    def test_approval_rolls_back_all_adjustments_if_snapshot_balance_changed(self) -> None:
        self._opening_stock(variant=self.pair_variant, quantity="2", unit_cost="300")
        self._opening_stock(variant=self.piece_variant, quantity="2", unit_cost="200")
        session = self._start()
        for line in session.lines.all():
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=Decimal("1"),
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        for line in session.lines.all():
            record_stock_count_review_evidence(
                actor=self.manager,
                line=line,
                variance_explanation="One unit was not found.",
            )
        ordered_lines = list(session.lines.order_by("variant_id"))
        changed_line = ordered_lines[-1]
        InventoryBalance.objects.filter(
            business=self.business,
            branch=self.branch,
            variant=changed_line.variant,
        ).update(inventory_value=Decimal("1.000000"))

        with self.assertRaisesMessage(ValidationError, "Inventory changed"):
            approve_stock_count(
                actor=self.manager,
                session=session,
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(
            InventoryMovement.objects.filter(
                movement_type__in=(
                    InventoryMovementType.STOCK_COUNT_ADJUSTMENT_IN,
                    InventoryMovementType.STOCK_COUNT_ADJUSTMENT_OUT,
                )
            ).exists()
        )
        session.refresh_from_db()
        self.assertEqual(session.status, StockCountStatus.SUBMITTED)

    def test_failed_reversal_creates_no_evidence_and_new_count_can_recover(self) -> None:
        session = self._start()
        for line in session.lines.all():
            physical_quantity = (
                Decimal("1") if line.variant_id == self.pair_variant.id else Decimal("0")
            )
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=physical_quantity,
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        pair_line = session.lines.get(variant=self.pair_variant)
        record_stock_count_review_evidence(
            actor=self.manager,
            line=pair_line,
            variance_explanation="One previously unrecorded pair was counted.",
            exceptional_unit_cost=Decimal("300"),
            exceptional_cost_evidence_note="Verified purchase document SUP-COUNT-2.",
        )
        approval = approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.pair_variant,
            operation_type="adjustment_out",
            quantity=Decimal("1"),
            reason="Unit sold through an external recovery test.",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "make stock negative"):
            reverse_stock_count(
                actor=self.owner,
                approval=approval,
                reason="Cannot reverse after later stock consumption.",
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(StockCountReversal.objects.exists())
        recovery = self._start()
        self.assertEqual(recovery.status, StockCountStatus.COUNTING)

    def test_reversal_fails_closed_when_inventory_value_is_insufficient(self) -> None:
        session = self._start()
        for line in session.lines.all():
            physical_quantity = (
                Decimal("1") if line.variant_id == self.pair_variant.id else Decimal("0")
            )
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=physical_quantity,
            )
        submit_stock_count(actor=self.stock_employee, session=session)
        pair_line = session.lines.get(variant=self.pair_variant)
        record_stock_count_review_evidence(
            actor=self.manager,
            line=pair_line,
            variance_explanation="One previously unrecorded pair was counted.",
            exceptional_unit_cost=Decimal("300"),
            exceptional_cost_evidence_note="Verified purchase document SUP-COUNT-3.",
        )
        approval = approve_stock_count(
            actor=self.manager,
            session=session,
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.pair_variant,
            operation_type="adjustment_in",
            quantity=Decimal("1"),
            unit_cost=Decimal("0"),
            reason="Zero-cost donated test stock.",
            idempotency_key=uuid.uuid4(),
        )
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.pair_variant,
            operation_type="adjustment_out",
            quantity=Decimal("1"),
            reason="Later moving-average stock use.",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "inventory value"):
            reverse_stock_count(
                actor=self.owner,
                approval=approval,
                reason="Exact value is no longer available.",
                idempotency_key=uuid.uuid4(),
            )

        self.assertFalse(StockCountReversal.objects.exists())

    def test_return_for_recount_preserves_review_return_and_releases_review_state(self) -> None:
        session = self._start()
        for line in session.lines.all():
            record_stock_count_quantity(
                actor=self.stock_employee,
                line=line,
                physical_quantity=Decimal("0"),
            )
        submit_stock_count(actor=self.stock_employee, session=session)

        review_return = return_stock_count_for_recount(
            actor=self.manager,
            session=session,
            reason="Repeat the footwear wall with a second counter.",
        )

        session.refresh_from_db()
        self.assertEqual(review_return.sequence, 1)
        self.assertEqual(session.status, StockCountStatus.COUNTING)
        self.assertIsNone(session.submitted_by)
        self.assertFalse(session.lines.exclude(variance_quantity=None).exists())

    def test_cancellation_releases_freeze(self) -> None:
        session = self._start()

        cancel_stock_count(
            actor=self.manager,
            session=session,
            reason="Store closed before the physical count could finish.",
        )
        operation = post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.piece_variant,
            operation_type="adjustment_in",
            quantity=Decimal("1"),
            unit_cost=Decimal("200"),
            reason="Posting resumed after count cancellation.",
            idempotency_key=uuid.uuid4(),
        )

        self.assertIsNotNone(operation.pk)

    def test_role_boundaries_are_enforced(self) -> None:
        with self.assertRaises(PermissionDenied):
            start_stock_count(
                actor=self.stock_employee,
                branch=self.branch,
                count_method_note="Unauthorized start.",
                idempotency_key=uuid.uuid4(),
            )
        session = self._start()
        line = session.lines.first()
        if line is None:
            self.fail("Expected a stock-count line.")
        with self.assertRaises(PermissionDenied):
            record_stock_count_quantity(
                actor=self.cashier,
                line=line,
                physical_quantity=Decimal("0"),
            )
