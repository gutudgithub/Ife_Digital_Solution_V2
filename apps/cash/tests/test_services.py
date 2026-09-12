import uuid
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import (
    CashMovement,
    CashMovementType,
    CashPostingKey,
    CashSession,
    CashSessionClosure,
    CashSessionReopening,
    CashSessionStatus,
)
from apps.cash.services import (
    close_cash_session,
    expected_cash,
    open_cash_session,
    post_manual_cash_movement,
    record_cash_refund,
    record_cash_sale,
    reopen_cash_session,
)


class CashServiceTests(TestCase):
    business: Business
    branch: Branch
    other_branch: Branch
    owner: BusinessMembership
    cashier: BusinessMembership
    stock_employee: BusinessMembership

    def setUp(self) -> None:
        self.business = Business.objects.create(name="Cash Shop", slug="cash-shop")
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
        owner_user = User.objects.create_user(
            email="cash-owner@example.com",
            password="strong-test-password",
        )
        cashier_user = User.objects.create_user(
            email="cashier@example.com",
            password="strong-test-password",
        )
        stock_user = User.objects.create_user(
            email="cash-stock@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
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

    def _open(self, *, opening_float: str = "100.00") -> CashSession:
        return open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal(opening_float),
            idempotency_key=uuid.uuid4(),
        )

    def test_opening_is_idempotent_and_zero_float_is_recorded(self) -> None:
        key = uuid.uuid4()

        first = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=key,
        )
        replay = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=key,
        )

        self.assertEqual(first.id, replay.id)
        self.assertEqual(CashSession.objects.count(), 1)
        self.assertEqual(CashPostingKey.objects.count(), 1)
        movement = CashMovement.objects.get()
        self.assertEqual(movement.movement_type, CashMovementType.OPENING_FLOAT)
        self.assertEqual(movement.amount_delta, Decimal("0.00"))
        self.assertEqual(movement.source_id, first.id)
        self.assertEqual(expected_cash(first), Decimal("0.00"))
        self.assertEqual(
            first.opening_basis_note,
            "Physical drawer count at system adoption",
        )
        first.opening_basis_note = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            first.save()

    def test_first_session_requires_manager_and_physical_count_note(self) -> None:
        with self.assertRaisesMessage(PermissionDenied, "first cash session"):
            open_cash_session(
                actor=self.cashier,
                branch=self.branch,
                opening_float=Decimal("0.00"),
                opening_basis_note="Cashier count",
                idempotency_key=uuid.uuid4(),
            )
        with self.assertRaisesMessage(ValidationError, "physical-count basis"):
            open_cash_session(
                actor=self.owner,
                branch=self.branch,
                opening_float=Decimal("0.00"),
                opening_basis_note=" ",
                idempotency_key=uuid.uuid4(),
            )

    def test_cashier_can_open_a_later_session(self) -> None:
        historical_time = timezone.now() - timedelta(days=1)
        with patch(
            "apps.cash.services.timezone.localdate",
            return_value=timezone.localtime(historical_time).date(),
        ):
            historical = open_cash_session(
                actor=self.owner,
                branch=self.branch,
                opening_float=Decimal("0.00"),
                opening_basis_note="Initial physical count",
                idempotency_key=uuid.uuid4(),
                opened_at=historical_time,
            )
            close_cash_session(
                actor=self.owner,
                session=historical,
                actual_cash=Decimal("0.00"),
                explanation="",
                idempotency_key=uuid.uuid4(),
                closed_at=historical_time,
            )

        later = open_cash_session(
            actor=self.cashier,
            branch=self.branch,
            opening_float=Decimal("0.00"),
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(later.opened_by, self.cashier)
        self.assertEqual(later.opening_basis_note, "")

    def test_opening_rejects_negative_float_noncurrent_date_and_duplicate_session(
        self,
    ) -> None:
        with self.assertRaisesMessage(ValidationError, "cannot be negative"):
            open_cash_session(
                actor=self.cashier,
                branch=self.branch,
                opening_float=Decimal("-0.01"),
                idempotency_key=uuid.uuid4(),
            )
        with self.assertRaisesMessage(ValidationError, "current date"):
            open_cash_session(
                actor=self.cashier,
                branch=self.branch,
                opening_float=Decimal("0.00"),
                idempotency_key=uuid.uuid4(),
                opened_at=timezone.now() - timedelta(days=1),
            )
        self._open()
        with self.assertRaisesMessage(ValidationError, "cash session already exists"):
            open_cash_session(
                actor=self.cashier,
                branch=self.branch,
                opening_float=Decimal("0.00"),
                idempotency_key=uuid.uuid4(),
            )

    def test_cash_movements_close_reopen_and_reclose_preserve_history(self) -> None:
        session = self._open()
        sale_source = uuid.uuid4()
        refund_source = uuid.uuid4()
        sale = record_cash_sale(
            actor=self.cashier,
            session=session,
            amount=Decimal("250.00"),
            source_id=sale_source,
            posted_at=session.opened_at,
        )
        replay = record_cash_sale(
            actor=self.cashier,
            session=session,
            amount=Decimal("250.00"),
            source_id=sale_source,
            posted_at=session.opened_at,
        )
        record_cash_refund(
            actor=self.owner,
            session=session,
            amount=Decimal("50.00"),
            source_id=refund_source,
            posted_at=session.opened_at,
        )
        post_manual_cash_movement(
            actor=self.owner,
            session=session,
            movement_type=CashMovementType.CASH_REMOVED,
            amount=Decimal("25.00"),
            reason="Moved to the branch safe",
            idempotency_key=uuid.uuid4(),
        )

        self.assertEqual(sale.id, replay.id)
        self.assertEqual(expected_cash(session), Decimal("275.00"))
        close_key = uuid.uuid4()
        closure = close_cash_session(
            actor=self.cashier,
            session=session,
            actual_cash=Decimal("270.00"),
            explanation="Five birr short after physical count",
            idempotency_key=close_key,
        )
        close_replay = close_cash_session(
            actor=self.cashier,
            session=session,
            actual_cash=Decimal("999.00"),
            explanation="Ignored during replay",
            idempotency_key=close_key,
        )
        self.assertEqual(closure.id, close_replay.id)
        self.assertEqual(closure.expected_cash, Decimal("275.00"))
        self.assertEqual(closure.variance, Decimal("-5.00"))

        reopen_key = uuid.uuid4()
        reopening = reopen_cash_session(
            actor=self.owner,
            session=session,
            reason="Manager approved a recount",
            idempotency_key=reopen_key,
        )
        reopen_replay = reopen_cash_session(
            actor=self.owner,
            session=session,
            reason="Ignored during replay",
            idempotency_key=reopen_key,
        )
        self.assertEqual(reopening.id, reopen_replay.id)
        second_closure = close_cash_session(
            actor=self.cashier,
            session=session,
            actual_cash=Decimal("275.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )

        session.refresh_from_db()
        self.assertEqual(session.status, CashSessionStatus.CLOSED)
        self.assertEqual(second_closure.sequence, 2)
        self.assertEqual(CashSessionClosure.objects.count(), 2)
        self.assertEqual(CashSessionReopening.objects.count(), 1)

    def test_outflows_cannot_make_expected_cash_negative(self) -> None:
        session = self._open(opening_float="10.00")

        with self.assertRaisesMessage(ValidationError, "expected cash negative"):
            record_cash_refund(
                actor=self.owner,
                session=session,
                amount=Decimal("10.01"),
                source_id=uuid.uuid4(),
                posted_at=session.opened_at,
            )
        with self.assertRaisesMessage(ValidationError, "expected cash negative"):
            post_manual_cash_movement(
                actor=self.owner,
                session=session,
                movement_type=CashMovementType.CASH_REMOVED,
                amount=Decimal("10.01"),
                reason="Invalid removal",
                idempotency_key=uuid.uuid4(),
            )

        self.assertEqual(CashMovement.objects.count(), 1)
        self.assertEqual(expected_cash(session), Decimal("10.00"))

    def test_manual_movement_is_idempotent_and_key_cannot_cross_operations(
        self,
    ) -> None:
        session = self._open()
        key = uuid.uuid4()
        movement = post_manual_cash_movement(
            actor=self.owner,
            session=session,
            movement_type=CashMovementType.CASH_ADDED,
            amount=Decimal("5.00"),
            reason="Authorized drawer addition",
            idempotency_key=key,
        )
        replay = post_manual_cash_movement(
            actor=self.owner,
            session=session,
            movement_type=CashMovementType.CASH_ADDED,
            amount=Decimal("5.00"),
            reason="Authorized drawer addition",
            idempotency_key=key,
        )
        self.assertEqual(movement.id, replay.id)
        with self.assertRaisesMessage(ValidationError, "another cash-session operation"):
            close_cash_session(
                actor=self.owner,
                session=session,
                actual_cash=Decimal("105.00"),
                explanation="",
                idempotency_key=key,
            )
        self.assertEqual(
            CashMovement.objects.filter(movement_type=CashMovementType.CASH_ADDED).count(),
            1,
        )

    def test_close_rejects_negative_actual_cash(self) -> None:
        session = self._open()
        with self.assertRaisesMessage(ValidationError, "cannot be negative"):
            close_cash_session(
                actor=self.cashier,
                session=session,
                actual_cash=Decimal("-0.01"),
                explanation="Invalid count",
                idempotency_key=uuid.uuid4(),
            )
        session.refresh_from_db()
        self.assertEqual(session.status, CashSessionStatus.OPEN)

    def test_historical_session_cannot_reopen_after_a_later_session(self) -> None:
        historical_time = timezone.now() - timedelta(days=1)
        with patch(
            "apps.cash.services.timezone.localdate",
            return_value=timezone.localtime(historical_time).date(),
        ):
            historical = open_cash_session(
                actor=self.owner,
                branch=self.branch,
                opening_float=Decimal("10.00"),
                idempotency_key=uuid.uuid4(),
                opened_at=historical_time,
            )
            close_cash_session(
                actor=self.owner,
                session=historical,
                actual_cash=Decimal("10.00"),
                explanation="",
                idempotency_key=uuid.uuid4(),
                closed_at=historical_time,
            )
        later = self._open()
        close_cash_session(
            actor=self.owner,
            session=later,
            actual_cash=Decimal("100.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )

        with self.assertRaisesMessage(ValidationError, "latest branch cash session"):
            reopen_cash_session(
                actor=self.owner,
                session=historical,
                reason="Invalid historical reopening",
                idempotency_key=uuid.uuid4(),
            )

    def test_permissions_and_branch_scope_are_enforced(self) -> None:
        with self.assertRaises(PermissionDenied):
            open_cash_session(
                actor=self.cashier,
                branch=self.other_branch,
                opening_float=Decimal("10.00"),
                idempotency_key=uuid.uuid4(),
            )
        with self.assertRaises(PermissionDenied):
            open_cash_session(
                actor=self.stock_employee,
                branch=self.branch,
                opening_float=Decimal("10.00"),
                idempotency_key=uuid.uuid4(),
            )
        with self.assertRaisesMessage(PermissionDenied, "first cash session"):
            open_cash_session(
                actor=self.cashier,
                branch=self.branch,
                opening_float=Decimal("10.00"),
                idempotency_key=uuid.uuid4(),
            )
        session = self._open()
        with self.assertRaises(PermissionDenied):
            post_manual_cash_movement(
                actor=self.cashier,
                session=session,
                movement_type=CashMovementType.CASH_ADDED,
                amount=Decimal("1.00"),
                reason="Cashier cannot post",
                idempotency_key=uuid.uuid4(),
            )
        close_cash_session(
            actor=self.cashier,
            session=session,
            actual_cash=Decimal("100.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )
        with self.assertRaises(PermissionDenied):
            reopen_cash_session(
                actor=self.cashier,
                session=session,
                reason="Cashier cannot reopen",
                idempotency_key=uuid.uuid4(),
            )

    def test_nonzero_variance_requires_explanation_and_posted_records_are_immutable(
        self,
    ) -> None:
        session = self._open()
        with self.assertRaisesMessage(ValidationError, "Explain every nonzero"):
            close_cash_session(
                actor=self.cashier,
                session=session,
                actual_cash=Decimal("99.00"),
                explanation="",
                idempotency_key=uuid.uuid4(),
            )
        closure = close_cash_session(
            actor=self.cashier,
            session=session,
            actual_cash=Decimal("100.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )
        movement = session.movements.first()
        if movement is None:
            self.fail("Opening movement was not created.")
        movement.reason = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            movement.save()
        closure.explanation = "Changed"
        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            closure.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            session.delete()


@skipUnlessDBFeature("has_select_for_update")
class CashConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    business: Business
    branch: Branch
    owner: BusinessMembership

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Concurrent Cash Shop",
            slug="concurrent-cash-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        owner_user = User.objects.create_user(
            email="concurrent-cash-owner@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            role=MembershipRole.OWNER,
        )

    def _open(self, opening_float: str = "100.00") -> CashSession:
        return open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal(opening_float),
            idempotency_key=uuid.uuid4(),
        )

    def test_concurrent_opening_creates_one_session(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def open_session() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                barrier.wait()
                open_cash_session(
                    actor=actor,
                    branch=branch,
                    opening_float=Decimal("10.00"),
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("opened")
            finally:
                connections.close_all()

        threads = [Thread(target=open_session), Thread(target=open_session)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["opened", "rejected"],
        )
        self.assertEqual(CashSession.objects.count(), 1)
        self.assertEqual(CashMovement.objects.count(), 1)
        self.assertEqual(CashPostingKey.objects.count(), 1)

    def test_concurrent_duplicate_source_creates_one_movement(self) -> None:
        session = self._open()
        source_id = uuid.uuid4()
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()

        def post_sale_movement() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = CashSession.objects.get(pk=session.pk)
                barrier.wait()
                movement = record_cash_sale(
                    actor=actor,
                    session=current_session,
                    amount=Decimal("20.00"),
                    source_id=source_id,
                    posted_at=session.opened_at,
                )
                outcomes.put(movement.id)
            finally:
                connections.close_all()

        threads = [Thread(target=post_sale_movement), Thread(target=post_sale_movement)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(
            CashMovement.objects.filter(movement_type=CashMovementType.CASH_SALE).count(),
            1,
        )

    def test_concurrent_movement_and_close_serialize(self) -> None:
        session = self._open()
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def post_movement() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = CashSession.objects.get(pk=session.pk)
                barrier.wait()
                post_manual_cash_movement(
                    actor=actor,
                    session=current_session,
                    movement_type=CashMovementType.CASH_ADDED,
                    amount=Decimal("25.00"),
                    reason="Concurrent drawer movement",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("movement-rejected")
            else:
                outcomes.put("movement-posted")
            finally:
                connections.close_all()

        def close_session() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = CashSession.objects.get(pk=session.pk)
                barrier.wait()
                close_cash_session(
                    actor=actor,
                    session=current_session,
                    actual_cash=Decimal("100.00"),
                    explanation="Concurrent close count",
                    idempotency_key=uuid.uuid4(),
                )
            finally:
                outcomes.put("closed")
                connections.close_all()

        threads = [Thread(target=post_movement), Thread(target=close_session)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        results = {outcomes.get_nowait(), outcomes.get_nowait()}
        self.assertIn("closed", results)
        closure = CashSessionClosure.objects.get()
        movement_posted = "movement-posted" in results
        self.assertEqual(
            closure.expected_cash,
            Decimal("125.00") if movement_posted else Decimal("100.00"),
        )
        self.assertEqual(closure.expected_cash, expected_cash(session))

    def test_concurrent_close_creates_one_closure(self) -> None:
        session = self._open()
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def close_session() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = CashSession.objects.get(pk=session.pk)
                barrier.wait()
                close_cash_session(
                    actor=actor,
                    session=current_session,
                    actual_cash=Decimal("100.00"),
                    explanation="",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("closed")
            finally:
                connections.close_all()

        threads = [Thread(target=close_session), Thread(target=close_session)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["closed", "rejected"],
        )
        self.assertEqual(CashSessionClosure.objects.count(), 1)

    def test_concurrent_reopen_creates_one_event(self) -> None:
        session = self._open()
        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=Decimal("100.00"),
            explanation="",
            idempotency_key=uuid.uuid4(),
        )
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()

        def reopen_session() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current_session = CashSession.objects.get(pk=session.pk)
                barrier.wait()
                reopen_cash_session(
                    actor=actor,
                    session=current_session,
                    reason="Concurrent approved recount",
                    idempotency_key=uuid.uuid4(),
                )
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("reopened")
            finally:
                connections.close_all()

        threads = [Thread(target=reopen_session), Thread(target=reopen_session)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(
            sorted([outcomes.get_nowait(), outcomes.get_nowait()]),
            ["rejected", "reopened"],
        )
        self.assertEqual(CashSessionReopening.objects.count(), 1)
