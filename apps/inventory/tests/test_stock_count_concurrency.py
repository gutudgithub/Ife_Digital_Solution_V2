import uuid
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import (
    InventoryMovement,
    InventorySourceType,
    StockCountApproval,
    StockCountPostingKey,
    StockCountReversal,
    StockCountSession,
)
from apps.inventory.services import (
    approve_stock_count,
    post_inventory_adjustment,
    post_opening_balance,
    record_stock_count_quantity,
    record_stock_count_review_evidence,
    reverse_stock_count,
    start_stock_count,
    submit_stock_count,
)


@skipUnlessDBFeature("has_select_for_update")
class StockCountConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    business: Business
    branch: Branch
    owner: BusinessMembership
    variant: ProductVariant

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Concurrent Count Shop",
            slug="concurrent-count-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        owner_user = User.objects.create_user(
            email="concurrent-count-owner@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        product = Product.objects.create(business=self.business, name="Count Product")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="COUNT-CONCURRENT-1",
            selling_price=Decimal("500.00"),
            stock_unit=StockUnit.PIECE,
        )

    def _submitted_count(self) -> StockCountSession:
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("2"),
            unit_cost=Decimal("100"),
            idempotency_key=uuid.uuid4(),
        )
        session = start_stock_count(
            actor=self.owner,
            branch=self.branch,
            count_method_note="Concurrent approval preparation.",
            idempotency_key=uuid.uuid4(),
        )
        line = session.lines.get()
        record_stock_count_quantity(
            actor=self.owner,
            line=line,
            physical_quantity=Decimal("1"),
        )
        submit_stock_count(actor=self.owner, session=session)
        record_stock_count_review_evidence(
            actor=self.owner,
            line=line,
            variance_explanation="One item was not found.",
        )
        return session

    def test_concurrent_count_starts_create_one_active_session(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def start() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                barrier.wait()
                start_stock_count(
                    actor=actor,
                    branch=branch,
                    count_method_note="Concurrent complete branch count.",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("started")
            finally:
                connections.close_all()

        threads = [Thread(target=start), Thread(target=start)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["rejected", "started"],
        )
        self.assertEqual(StockCountSession.objects.count(), 1)
        self.assertEqual(StockCountPostingKey.objects.count(), 1)

    def test_concurrent_same_key_count_start_returns_one_session(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()
        key = uuid.uuid4()

        def start() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                barrier.wait()
                session = start_stock_count(
                    actor=actor,
                    branch=branch,
                    count_method_note="Concurrent idempotent complete branch count.",
                    idempotency_key=key,
                )
                outcomes.put(session.id)
            finally:
                connections.close_all()

        threads = [Thread(target=start), Thread(target=start)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(StockCountSession.objects.count(), 1)
        self.assertEqual(StockCountPostingKey.objects.count(), 1)

    def test_inventory_posting_and_count_start_serialize_on_branch(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def start() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                barrier.wait()
                start_stock_count(
                    actor=actor,
                    branch=branch,
                    count_method_note="Concurrent posting boundary count.",
                    idempotency_key=uuid.uuid4(),
                )
                outcomes.put("count-started")
            finally:
                connections.close_all()

        def post() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                variant = ProductVariant.objects.get(pk=self.variant.pk)
                barrier.wait()
                post_inventory_adjustment(
                    actor=actor,
                    branch=branch,
                    variant=variant,
                    operation_type="adjustment_in",
                    quantity=Decimal("1"),
                    unit_cost=Decimal("100"),
                    reason="Concurrent inventory posting.",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("posting-rejected")
            else:
                outcomes.put("posting-posted")
            finally:
                connections.close_all()

        threads = [Thread(target=start), Thread(target=post)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        results = {outcomes.get_nowait(), outcomes.get_nowait()}
        self.assertIn("count-started", results)
        session = StockCountSession.objects.get()
        line = session.lines.get()
        if "posting-posted" in results:
            self.assertEqual(line.system_quantity_snapshot, Decimal("1.000"))
            self.assertEqual(InventoryMovement.objects.count(), 1)
        else:
            self.assertEqual(results, {"count-started", "posting-rejected"})
            self.assertEqual(line.system_quantity_snapshot, Decimal("0.000"))
            self.assertFalse(InventoryMovement.objects.exists())

    def test_concurrent_same_key_approval_posts_exactly_once(self) -> None:
        session = self._submitted_count()
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()
        key = uuid.uuid4()

        def approve() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = StockCountSession.objects.get(pk=session.pk)
                barrier.wait()
                approval = approve_stock_count(
                    actor=actor,
                    session=current_session,
                    idempotency_key=key,
                )
                outcomes.put(approval.id)
            finally:
                connections.close_all()

        threads = [Thread(target=approve), Thread(target=approve)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(StockCountApproval.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                source_type=InventorySourceType.STOCK_COUNT_LINE
            ).count(),
            1,
        )

    def test_concurrent_distinct_approval_keys_allow_one_approval(self) -> None:
        session = self._submitted_count()
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def approve() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = StockCountSession.objects.get(pk=session.pk)
                barrier.wait()
                approve_stock_count(
                    actor=actor,
                    session=current_session,
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("approved")
            finally:
                connections.close_all()

        threads = [Thread(target=approve), Thread(target=approve)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["approved", "rejected"],
        )
        self.assertEqual(StockCountApproval.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                source_type=InventorySourceType.STOCK_COUNT_LINE
            ).count(),
            1,
        )

    def test_concurrent_same_key_reversal_posts_exactly_once(self) -> None:
        session = self._submitted_count()
        approval = approve_stock_count(
            actor=self.owner,
            session=session,
            idempotency_key=uuid.uuid4(),
        )
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()
        key = uuid.uuid4()

        def reverse() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_approval = StockCountApproval.objects.get(pk=approval.pk)
                barrier.wait()
                reversal = reverse_stock_count(
                    actor=actor,
                    approval=current_approval,
                    reason="Concurrent exact-cost reversal.",
                    idempotency_key=key,
                )
                outcomes.put(reversal.id)
            finally:
                connections.close_all()

        threads = [Thread(target=reverse), Thread(target=reverse)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(StockCountReversal.objects.count(), 1)
        self.assertEqual(
            InventoryMovement.objects.filter(
                source_type=InventorySourceType.STOCK_COUNT_REVERSAL
            ).count(),
            1,
        )
