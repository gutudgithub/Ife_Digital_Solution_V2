from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, BusinessMembership
from apps.cash.models import (
    CashMovement,
    CashMovementType,
    CashOperationType,
    CashPostingKey,
    CashSession,
    CashSessionClosure,
    CashSessionReopening,
    CashSessionStatus,
)

MONEY_QUANTUM = Decimal("0.01")
CASH_CONSTRAINT_NAMES = frozenset(
    {
        "cash_unique_posting_key_per_business",
        "cash_posting_operation_type_valid",
        "cash_unique_session_per_branch_date",
        "cash_unique_open_session_per_branch",
        "cash_session_status_valid",
        "cash_session_opening_float_nonnegative",
        "cash_movement_type_valid",
        "cash_movement_evidence_matches_type",
        "cash_unique_movement_source_per_business",
        "cash_unique_closure_sequence_per_session",
        "cash_closure_expected_nonnegative",
        "cash_closure_actual_nonnegative",
    }
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


@contextmanager
def _translate_cash_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any(name in message for message in error.messages for name in CASH_CONSTRAINT_NAMES):
            raise ValidationError(
                _("Posting could not be completed because a cash-session rule was violated.")
            ) from error
        raise


def _validate_actor(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active:
        raise PermissionDenied(_("An active business membership is required."))
    if not actor.can_use_cash_sessions:
        raise PermissionDenied(_("Cash-session permission is required."))


def ensure_cash_branch_access(actor: BusinessMembership, branch: Branch) -> None:
    _validate_actor(actor)
    if branch.business_id != actor.business_id or not branch.is_active:
        raise PermissionDenied(_("Cash sessions require an active branch in this business."))
    if actor.can_manage_cash_movements:
        return
    if actor.assigned_branch_id is not None:
        if actor.assigned_branch_id != branch.id:
            raise PermissionDenied(_("You may use cash sessions only for your assigned branch."))
        return
    active_branches = list(actor.business.branches.filter(is_active=True)[:2])
    if len(active_branches) != 1 or active_branches[0].id != branch.id:
        raise PermissionDenied(_("Ask a manager to assign your branch before using cash sessions."))


def _claim_posting_key(
    *,
    actor: BusinessMembership,
    key: UUID,
    operation_type: str,
    source_id: UUID,
) -> CashPostingKey:
    existing = CashPostingKey.objects.filter(business=actor.business, key=key).first()
    if existing is not None:
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            )
        return existing
    try:
        with transaction.atomic():
            with _translate_cash_constraint_errors():
                return CashPostingKey.objects.create(
                    business=actor.business,
                    key=key,
                    operation_type=operation_type,
                    source_id=source_id,
                )
    except (IntegrityError, ValidationError) as error:
        existing = CashPostingKey.objects.filter(business=actor.business, key=key).first()
        if existing is None:
            raise
        if existing.operation_type != operation_type or existing.source_id != source_id:
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            ) from error
        return existing


def expected_cash(session: CashSession) -> Decimal:
    total = session.movements.aggregate(total=Sum("amount_delta"))["total"]
    return _money(total or Decimal("0.00"))


@transaction.atomic
def open_cash_session(
    *,
    actor: BusinessMembership,
    branch: Branch,
    opening_float: Decimal,
    idempotency_key: UUID,
    opened_at: datetime | None = None,
) -> CashSession:
    ensure_cash_branch_access(actor, branch)
    timestamp = opened_at or timezone.now()
    business_date = timezone.localtime(timestamp).date()
    if business_date != timezone.localdate():
        raise ValidationError(_("Cash sessions can be opened only for the current date."))
    amount = _money(opening_float)
    if amount < 0:
        raise ValidationError(_("Opening float cannot be negative."))
    locked_branch = Branch.objects.select_for_update().get(
        pk=branch.pk,
        business=actor.business,
        is_active=True,
    )
    existing_key = CashPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != CashOperationType.OPEN
            or existing_key.source_id != locked_branch.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            )
        existing_session = CashSession.objects.filter(opening_key=existing_key).first()
        if existing_session is not None:
            return existing_session
        raise ValidationError(_("The cash-session opening is incomplete."))
    if CashSession.objects.filter(
        business=actor.business,
        branch=locked_branch,
        business_date=business_date,
    ).exists():
        raise ValidationError(_("A cash session already exists for this branch and date."))
    if CashSession.objects.filter(
        business=actor.business,
        branch=locked_branch,
        status=CashSessionStatus.OPEN,
    ).exists():
        raise ValidationError(_("This branch already has an open cash session."))
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=CashOperationType.OPEN,
        source_id=locked_branch.id,
    )
    with _translate_cash_constraint_errors():
        session = CashSession.objects.create(
            business=actor.business,
            branch=locked_branch,
            business_date=business_date,
            opening_float=amount,
            opening_key=posting_key,
            opened_by=actor,
            opened_at=timestamp,
        )
        CashMovement.objects.create(
            business=actor.business,
            branch=locked_branch,
            session=session,
            movement_type=CashMovementType.OPENING_FLOAT,
            amount_delta=amount,
            source_id=session.id,
            actor=actor,
            posted_at=timestamp,
        )
    return session


def lock_open_cash_session(
    *,
    actor: BusinessMembership,
    branch: Branch,
) -> CashSession:
    ensure_cash_branch_access(actor, branch)
    session = (
        CashSession.objects.select_for_update()
        .filter(
            business=actor.business,
            branch=branch,
            status=CashSessionStatus.OPEN,
        )
        .first()
    )
    if session is None:
        raise ValidationError(_("Open the branch cash session before posting physical cash."))
    return session


def _record_source_movement(
    *,
    actor: BusinessMembership,
    session: CashSession,
    movement_type: str,
    amount_delta: Decimal,
    source_id: UUID,
    posted_at: datetime,
) -> CashMovement:
    locked = CashSession.objects.select_for_update().get(
        pk=session.pk,
        business=actor.business,
    )
    ensure_cash_branch_access(actor, locked.branch)
    if locked.status != CashSessionStatus.OPEN:
        raise ValidationError(_("The branch cash session is closed."))
    existing = CashMovement.objects.filter(
        business=actor.business,
        movement_type=movement_type,
        source_id=source_id,
    ).first()
    if existing is not None:
        if existing.session_id != locked.id:
            raise ValidationError(_("This cash source belongs to another cash session."))
        return existing
    delta = _money(amount_delta)
    if movement_type == CashMovementType.CASH_SALE and delta <= 0:
        raise ValidationError(_("Cash sale movement amount must be positive."))
    if movement_type == CashMovementType.CASH_REFUND and delta >= 0:
        raise ValidationError(_("Cash refund movement amount must be negative."))
    if delta < 0 and expected_cash(locked) + delta < 0:
        raise ValidationError(_("This cash movement would make expected cash negative."))
    try:
        with _translate_cash_constraint_errors():
            return CashMovement.objects.create(
                business=actor.business,
                branch=locked.branch,
                session=locked,
                movement_type=movement_type,
                amount_delta=delta,
                source_id=source_id,
                actor=actor,
                posted_at=posted_at,
            )
    except (IntegrityError, ValidationError) as error:
        existing = CashMovement.objects.filter(
            business=actor.business,
            movement_type=movement_type,
            source_id=source_id,
        ).first()
        if existing is None:
            raise
        if existing.session_id != locked.id:
            raise ValidationError(_("This cash source belongs to another cash session.")) from error
        return existing


@transaction.atomic
def record_cash_sale(
    *,
    actor: BusinessMembership,
    session: CashSession,
    amount: Decimal,
    source_id: UUID,
    posted_at: datetime,
) -> CashMovement:
    return _record_source_movement(
        actor=actor,
        session=session,
        movement_type=CashMovementType.CASH_SALE,
        amount_delta=amount,
        source_id=source_id,
        posted_at=posted_at,
    )


@transaction.atomic
def record_cash_refund(
    *,
    actor: BusinessMembership,
    session: CashSession,
    amount: Decimal,
    source_id: UUID,
    posted_at: datetime,
) -> CashMovement:
    return _record_source_movement(
        actor=actor,
        session=session,
        movement_type=CashMovementType.CASH_REFUND,
        amount_delta=-_money(amount),
        source_id=source_id,
        posted_at=posted_at,
    )


@transaction.atomic
def post_manual_cash_movement(
    *,
    actor: BusinessMembership,
    session: CashSession,
    movement_type: str,
    amount: Decimal,
    reason: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> CashMovement:
    _validate_actor(actor)
    if not actor.can_manage_cash_movements:
        raise PermissionDenied(_("Cash movement management permission is required."))
    locked = (
        CashSession.objects.select_for_update()
        .select_related("branch")
        .get(
            pk=session.pk,
            business=actor.business,
        )
    )
    ensure_cash_branch_access(actor, locked.branch)
    existing_key = CashPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != CashOperationType.MANUAL_MOVEMENT
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            )
        existing_movement = CashMovement.objects.filter(posting_key=existing_key).first()
        if existing_movement is not None:
            return existing_movement
        raise ValidationError(_("The manual cash movement is incomplete."))
    if locked.status != CashSessionStatus.OPEN:
        raise ValidationError(_("Manual cash movement requires an open cash session."))
    if movement_type not in {
        CashMovementType.CASH_ADDED,
        CashMovementType.CASH_REMOVED,
    }:
        raise ValidationError(_("Select cash added or cash removed."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A manual cash movement reason is required."))
    clean_amount = _money(amount)
    if clean_amount <= 0:
        raise ValidationError(_("Cash movement amount must be greater than zero."))
    delta = clean_amount if movement_type == CashMovementType.CASH_ADDED else -clean_amount
    if delta < 0 and expected_cash(locked) + delta < 0:
        raise ValidationError(_("This cash movement would make expected cash negative."))
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=CashOperationType.MANUAL_MOVEMENT,
        source_id=locked.id,
    )
    with _translate_cash_constraint_errors():
        return CashMovement.objects.create(
            business=actor.business,
            branch=locked.branch,
            session=locked,
            movement_type=movement_type,
            amount_delta=delta,
            posting_key=posting_key,
            actor=actor,
            reason=clean_reason,
            posted_at=posted_at or timezone.now(),
        )


@transaction.atomic
def close_cash_session(
    *,
    actor: BusinessMembership,
    session: CashSession,
    actual_cash: Decimal,
    explanation: str,
    idempotency_key: UUID,
    closed_at: datetime | None = None,
) -> CashSessionClosure:
    _validate_actor(actor)
    locked = (
        CashSession.objects.select_for_update()
        .select_related("branch")
        .get(
            pk=session.pk,
            business=actor.business,
        )
    )
    ensure_cash_branch_access(actor, locked.branch)
    existing_key = CashPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != CashOperationType.CLOSE
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            )
        existing_closure = CashSessionClosure.objects.filter(posting_key=existing_key).first()
        if existing_closure is not None:
            return existing_closure
        raise ValidationError(_("The cash-session closing is incomplete."))
    if locked.status != CashSessionStatus.OPEN:
        raise ValidationError(_("Only an open cash session can be closed."))
    counted = _money(actual_cash)
    if counted < 0:
        raise ValidationError(_("Actual cash cannot be negative."))
    expected = expected_cash(locked)
    variance = _money(counted - expected)
    clean_explanation = explanation.strip()
    if variance != 0 and not clean_explanation:
        raise ValidationError(_("Explain every nonzero cash variance."))
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=CashOperationType.CLOSE,
        source_id=locked.id,
    )
    sequence = (locked.closures.aggregate(maximum=Max("sequence"))["maximum"] or 0) + 1
    with _translate_cash_constraint_errors():
        closure = CashSessionClosure.objects.create(
            business=actor.business,
            branch=locked.branch,
            session=locked,
            sequence=sequence,
            expected_cash=expected,
            actual_cash=counted,
            variance=variance,
            explanation=clean_explanation,
            posting_key=posting_key,
            closed_by=actor,
            posted_at=closed_at or timezone.now(),
        )
        locked.status = CashSessionStatus.CLOSED
        locked.save(update_fields=("status", "updated_at"))
    return closure


@transaction.atomic
def reopen_cash_session(
    *,
    actor: BusinessMembership,
    session: CashSession,
    reason: str,
    idempotency_key: UUID,
    reopened_at: datetime | None = None,
) -> CashSessionReopening:
    _validate_actor(actor)
    if not actor.can_reopen_cash_sessions:
        raise PermissionDenied(_("Cash-session reopening permission is required."))
    locked = (
        CashSession.objects.select_for_update()
        .select_related("branch")
        .get(
            pk=session.pk,
            business=actor.business,
        )
    )
    ensure_cash_branch_access(actor, locked.branch)
    Branch.objects.select_for_update().get(pk=locked.branch_id)
    existing_key = CashPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if (
            existing_key.operation_type != CashOperationType.REOPEN
            or existing_key.source_id != locked.id
        ):
            raise ValidationError(
                _("This idempotency key belongs to another cash-session operation.")
            )
        existing_reopening = CashSessionReopening.objects.filter(posting_key=existing_key).first()
        if existing_reopening is not None:
            return existing_reopening
        raise ValidationError(_("The cash-session reopening is incomplete."))
    if locked.status != CashSessionStatus.CLOSED:
        raise ValidationError(_("Only a closed cash session can be reopened."))
    clean_reason = reason.strip()
    if not clean_reason:
        raise ValidationError(_("A reopening reason is required."))
    if (
        CashSession.objects.filter(
            business=actor.business,
            branch=locked.branch,
        )
        .exclude(pk=locked.pk)
        .filter(
            Q(business_date__gt=locked.business_date)
            | Q(business_date=locked.business_date, opened_at__gt=locked.opened_at)
        )
        .exists()
    ):
        raise ValidationError(_("Only the latest branch cash session can be reopened."))
    latest_closure = locked.closures.order_by("-sequence").first()
    if latest_closure is None:
        raise ValidationError(_("This closed cash session has no closure evidence."))
    if hasattr(latest_closure, "reopening"):
        raise ValidationError(_("The latest cash-session closure was already reopened."))
    if (
        CashSession.objects.filter(
            business=actor.business,
            branch=locked.branch,
            status=CashSessionStatus.OPEN,
        )
        .exclude(pk=locked.pk)
        .exists()
    ):
        raise ValidationError(_("This branch already has an open cash session."))
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=CashOperationType.REOPEN,
        source_id=locked.id,
    )
    with _translate_cash_constraint_errors():
        reopening = CashSessionReopening.objects.create(
            business=actor.business,
            branch=locked.branch,
            session=locked,
            closure=latest_closure,
            reason=clean_reason,
            posting_key=posting_key,
            reopened_by=actor,
            posted_at=reopened_at or timezone.now(),
        )
        locked.status = CashSessionStatus.OPEN
        locked.save(update_fields=("status", "updated_at"))
    return reopening
