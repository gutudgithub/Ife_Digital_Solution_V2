from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, BusinessMembership
from apps.cash.models import CashMovementType, CashSession, CashSessionStatus
from apps.cash.services import (
    expected_cash,
    lock_open_cash_session,
    record_source_cash_movement,
)
from apps.documents.provenance import (
    require_expense_not_confirmed_source,
    validate_document_expense_posting,
)
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseSettlementOperationType,
    ExpenseSettlementPostingKey,
    ExpenseStatus,
    OperatingExpense,
    OperatingExpensePayment,
    OperatingExpenseReversal,
    OperationalPaymentMethod,
    SupplierPayment,
    SupplierPaymentReversal,
    SupplierReturnSettlement,
    SupplierReturnSettlementReversal,
    SupplierReturnSettlementType,
)
from apps.purchasing.models import (
    Purchase,
    PurchaseReturn,
    PurchaseReturnStatus,
    PurchaseStatus,
)

MONEY_QUANTUM = Decimal("0.01")
EXPENSE_CONSTRAINT_NAMES = frozenset(
    {
        "expenses_unique_category_name_business",
        "expenses_unique_posting_key_business",
        "expenses_posting_operation_valid",
        "expenses_unique_number_business",
        "expenses_status_valid",
        "expenses_amount_positive",
        "expenses_lifecycle_evidence_matches",
        "expenses_payment_method_valid",
        "expenses_payment_amount_positive",
        "expenses_payment_evidence_matches",
        "expenses_unique_payment_telebirr",
        "expenses_supplier_payment_method_valid",
        "expenses_supplier_payment_positive",
        "expenses_supplier_payment_evidence",
        "expenses_unique_supplier_payment_ref",
        "expenses_return_settlement_type_valid",
        "expenses_return_settlement_positive",
        "expenses_return_settlement_evidence",
        "expenses_unique_supplier_refund_ref",
    }
)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def normalize_telebirr_reference(reference: str) -> str:
    return "".join(reference.split()).upper()


def new_operating_expense_number() -> str:
    return f"EXP-{uuid4().hex[:12].upper()}"


@contextmanager
def _translate_expense_constraint_errors() -> Iterator[None]:
    try:
        yield
    except ValidationError as error:
        if any(name in message for message in error.messages for name in EXPENSE_CONSTRAINT_NAMES):
            raise ValidationError(
                _("Posting could not be completed because an expense or settlement rule failed.")
            ) from error
        raise


def _validate_actor(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active:
        raise PermissionDenied(_("An active business membership is required."))
    if not actor.can_manage_operating_expenses:
        raise PermissionDenied(_("Owner or manager permission is required."))


def _validate_branch(actor: BusinessMembership, branch: Branch) -> None:
    _validate_actor(actor)
    if branch.business_id != actor.business_id or not branch.is_active:
        raise PermissionDenied(_("An active branch in this business is required."))


def _payment_reference(
    *,
    method: str,
    telebirr_reference: str,
) -> tuple[str, str]:
    reference = telebirr_reference.strip()
    if method == OperationalPaymentMethod.CASH:
        if reference:
            raise ValidationError(_("Cash evidence cannot include a Telebirr reference."))
        return "", ""
    if method != OperationalPaymentMethod.TELEBIRR:
        raise ValidationError(_("Select cash or Telebirr."))
    normalized = normalize_telebirr_reference(reference)
    if not normalized:
        raise ValidationError(_("A Telebirr reference is required."))
    return reference, normalized


def _claim_posting_key(
    *,
    actor: BusinessMembership,
    key: UUID,
    operation_type: str,
    source_id: UUID,
) -> ExpenseSettlementPostingKey:
    existing = ExpenseSettlementPostingKey.objects.filter(
        business=actor.business,
        key=key,
    ).first()
    if existing is not None:
        if existing.operation_type != operation_type:
            raise ValidationError(
                _("This idempotency key belongs to another expense or settlement operation.")
            )
        return existing
    try:
        with transaction.atomic():
            with _translate_expense_constraint_errors():
                return ExpenseSettlementPostingKey.objects.create(
                    business=actor.business,
                    key=key,
                    operation_type=operation_type,
                    source_id=source_id,
                )
    except (IntegrityError, ValidationError) as error:
        existing = ExpenseSettlementPostingKey.objects.filter(
            business=actor.business,
            key=key,
        ).first()
        if existing is None:
            raise
        if existing.operation_type != operation_type:
            raise ValidationError(
                _("This idempotency key belongs to another expense or settlement operation.")
            ) from error
        return existing


def _require_exact_source(
    key: ExpenseSettlementPostingKey,
    source_id: UUID,
) -> None:
    if key.source_id != source_id:
        raise ValidationError(
            _("This idempotency key belongs to another expense or settlement operation.")
        )


def _locked_original_cash_session(
    *,
    actor: BusinessMembership,
    session: CashSession | None,
    branch: Branch,
) -> CashSession:
    if session is None:
        raise ValidationError(_("The original cash-session evidence is missing."))
    locked = CashSession.objects.select_for_update().get(
        pk=session.pk,
        business=actor.business,
        branch=branch,
    )
    if locked.status != CashSessionStatus.OPEN:
        raise ValidationError(
            _("Reopen the original cash session before reversing this cash evidence.")
        )
    return locked


def _business_date(
    *,
    method: str,
    cash_session: CashSession | None,
    posted_at: datetime,
) -> date:
    if method == OperationalPaymentMethod.CASH:
        if cash_session is None:
            raise ValidationError(_("Cash posting requires an open cash session."))
        return cash_session.business_date
    return timezone.localtime(posted_at).date()


def purchase_settlement_totals(purchase: Purchase) -> dict[str, Decimal]:
    gross_reference = _money(purchase.total_amount)
    active_payments = SupplierPayment.objects.filter(
        business=purchase.business,
        purchase=purchase,
        reversal__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    active_credits = SupplierReturnSettlement.objects.filter(
        business=purchase.business,
        purchase=purchase,
        settlement_type=SupplierReturnSettlementType.CREDIT,
        reversal__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    active_refunds = SupplierReturnSettlement.objects.filter(
        business=purchase.business,
        purchase=purchase,
        settlement_type=SupplierReturnSettlementType.REFUND,
        reversal__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    accepted_reductions = _money(active_credits + active_refunds)
    net_purchase_reference = _money(gross_reference - accepted_reductions)
    net_transferred = _money(active_payments - active_refunds)
    remaining_reference = _money(net_purchase_reference - net_transferred)
    return {
        "gross_purchase_reference": gross_reference,
        "accepted_return_reductions": accepted_reductions,
        "net_purchase_reference": net_purchase_reference,
        "active_supplier_payments": _money(active_payments),
        "active_supplier_refunds": _money(active_refunds),
        "net_transferred_to_supplier": net_transferred,
        "remaining_operational_reference_balance": remaining_reference,
    }


def purchase_return_settlement_totals(
    purchase_return: PurchaseReturn,
) -> dict[str, Decimal]:
    supplier_reference_total = _money(purchase_return.supplier_reference_total)
    accepted = SupplierReturnSettlement.objects.filter(
        business=purchase_return.business,
        purchase_return=purchase_return,
        reversal__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    accepted_amount = _money(accepted)
    return {
        "supplier_return_reference_amount": supplier_reference_total,
        "inventory_value_reduction": purchase_return.inventory_value_reduction,
        "supplier_accepted_settlement_amount": accepted_amount,
        "unresolved_supplier_return_reference": _money(supplier_reference_total - accepted_amount),
    }


@transaction.atomic
def create_operating_expense_draft(
    *,
    actor: BusinessMembership,
    branch: Branch,
    category: ExpenseCategory,
    payee: str,
    description: str,
    amount: Decimal,
) -> OperatingExpense:
    _validate_branch(actor, branch)
    if category.business_id != actor.business_id or not category.is_active:
        raise ValidationError(_("Select an active expense category in this business."))
    clean_amount = _money(amount)
    if clean_amount <= 0:
        raise ValidationError(_("Expense amount must be greater than zero."))
    with _translate_expense_constraint_errors():
        return OperatingExpense.objects.create(
            business=actor.business,
            branch=branch,
            category=category,
            internal_number=new_operating_expense_number(),
            payee=payee,
            description=description,
            amount=clean_amount,
            created_by=actor,
        )


@transaction.atomic
def edit_operating_expense_draft(
    *,
    actor: BusinessMembership,
    expense: OperatingExpense,
    branch: Branch,
    category: ExpenseCategory,
    payee: str,
    description: str,
    amount: Decimal,
) -> OperatingExpense:
    _validate_branch(actor, branch)
    locked = OperatingExpense.objects.select_for_update().get(
        pk=expense.pk,
        business=actor.business,
    )
    if locked.status != ExpenseStatus.DRAFT:
        raise ValidationError(_("Only draft operating expenses can be edited."))
    require_expense_not_confirmed_source(locked)
    if category.business_id != actor.business_id or not category.is_active:
        raise ValidationError(_("Select an active expense category in this business."))
    clean_amount = _money(amount)
    if clean_amount <= 0:
        raise ValidationError(_("Expense amount must be greater than zero."))
    locked.branch = branch
    locked.category = category
    locked.payee = payee
    locked.description = description
    locked.amount = clean_amount
    locked.save()
    return locked


@transaction.atomic
def cancel_operating_expense_draft(
    *,
    actor: BusinessMembership,
    expense: OperatingExpense,
) -> OperatingExpense:
    _validate_actor(actor)
    locked = OperatingExpense.objects.select_for_update().get(
        pk=expense.pk,
        business=actor.business,
    )
    if locked.status != ExpenseStatus.DRAFT:
        raise ValidationError(_("Only draft operating expenses can be cancelled."))
    locked.status = ExpenseStatus.CANCELLED
    locked.cancelled_by = actor
    locked.cancelled_at = timezone.now()
    locked.save()
    return locked


@transaction.atomic
def post_operating_expense(
    *,
    actor: BusinessMembership,
    expense: OperatingExpense,
    method: str,
    telebirr_reference: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> OperatingExpense:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    locked = (
        OperatingExpense.objects.select_for_update()
        .select_related("branch")
        .get(
            pk=expense.pk,
            business=actor.business,
        )
    )
    if locked.status == ExpenseStatus.POSTED:
        if locked.posting_key is None:
            raise ValidationError(_("The expense posting evidence is incomplete."))
        if locked.posting_key.key != idempotency_key:
            raise ValidationError(_("This operating expense is already posted."))
        existing_payment = locked.payment
        reference, normalized_reference = _payment_reference(
            method=method,
            telebirr_reference=telebirr_reference,
        )
        if (
            existing_payment.method != method
            or existing_payment.telebirr_reference != reference
            or existing_payment.telebirr_reference_normalized != normalized_reference
        ):
            raise ValidationError(
                _("An idempotent replay must match the original expense posting.")
            )
        return locked
    if locked.status != ExpenseStatus.DRAFT:
        raise ValidationError(_("Only draft operating expenses can be posted."))
    validate_document_expense_posting(
        expense=locked,
        method=method,
        telebirr_reference=telebirr_reference,
    )
    reference, normalized_reference = _payment_reference(
        method=method,
        telebirr_reference=telebirr_reference,
    )
    cash_session = (
        lock_open_cash_session(actor=actor, branch=locked.branch)
        if method == OperationalPaymentMethod.CASH
        else None
    )
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.EXPENSE,
        source_id=locked.id,
    )
    _require_exact_source(posting_key, locked.id)
    stray_payment = OperatingExpensePayment.objects.filter(expense=locked).first()
    if stray_payment is not None:
        raise ValidationError(_("The expense posting evidence is incomplete."))
    business_date = _business_date(
        method=method,
        cash_session=cash_session,
        posted_at=timestamp,
    )
    with _translate_expense_constraint_errors():
        OperatingExpensePayment.objects.create(
            business=actor.business,
            branch=locked.branch,
            expense=locked,
            method=method,
            amount=locked.amount,
            telebirr_reference=reference,
            telebirr_reference_normalized=normalized_reference,
            cash_session=cash_session,
            posted_by=actor,
            posted_at=timestamp,
        )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.OPERATING_EXPENSE,
            amount_delta=-locked.amount,
            source_id=locked.id,
            posted_at=timestamp,
        )
    locked.status = ExpenseStatus.POSTED
    locked.business_date = business_date
    locked.posting_key = posting_key
    locked.posted_by = actor
    locked.posted_at = timestamp
    locked.save()
    return locked


@transaction.atomic
def reverse_operating_expense(
    *,
    actor: BusinessMembership,
    expense: OperatingExpense,
    reason: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> OperatingExpenseReversal:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    locked = (
        OperatingExpense.objects.select_for_update()
        .select_related("branch")
        .get(pk=expense.pk, business=actor.business)
    )
    if locked.status == ExpenseStatus.REVERSED:
        existing = OperatingExpenseReversal.objects.filter(expense=locked).first()
        if existing is not None and existing.posting_key.key == idempotency_key:
            return existing
        raise ValidationError(_("This operating expense is already reversed."))
    if locked.status != ExpenseStatus.POSTED:
        raise ValidationError(_("Only posted operating expenses can be reversed."))
    payment = OperatingExpensePayment.objects.select_for_update().get(
        expense=locked,
        business=actor.business,
    )
    cash_session = (
        _locked_original_cash_session(
            actor=actor,
            session=payment.cash_session,
            branch=locked.branch,
        )
        if payment.method == OperationalPaymentMethod.CASH
        else None
    )
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.EXPENSE_REVERSAL,
        source_id=locked.id,
    )
    _require_exact_source(posting_key, locked.id)
    reversal = OperatingExpenseReversal.objects.create(
        business=actor.business,
        branch=locked.branch,
        expense=locked,
        posting_key=posting_key,
        reason=reason,
        cash_session=cash_session,
        reversed_by=actor,
        posted_at=timestamp,
    )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.OPERATING_EXPENSE_REVERSAL,
            amount_delta=locked.amount,
            source_id=reversal.id,
            posted_at=timestamp,
        )
    locked.status = ExpenseStatus.REVERSED
    locked.save()
    return reversal


def _lock_purchase(actor: BusinessMembership, purchase: Purchase) -> Purchase:
    locked = (
        Purchase.objects.select_for_update()
        .select_related("branch", "supplier")
        .get(pk=purchase.pk, business=actor.business)
    )
    if locked.status not in {
        PurchaseStatus.APPROVED,
        PurchaseStatus.PARTIALLY_RECEIVED,
        PurchaseStatus.RECEIVED,
    }:
        raise ValidationError(_("Supplier settlement requires an approved or received purchase."))
    return locked


@transaction.atomic
def post_supplier_payment(
    *,
    actor: BusinessMembership,
    purchase: Purchase,
    amount: Decimal,
    method: str,
    supplier_reference: str,
    telebirr_reference: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> SupplierPayment:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    locked_purchase = _lock_purchase(actor, purchase)
    ExpenseSettlementPostingKey.objects.select_for_update().filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    existing_key = ExpenseSettlementPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if existing_key.operation_type != ExpenseSettlementOperationType.SUPPLIER_PAYMENT:
            raise ValidationError(
                _("This idempotency key belongs to another expense or settlement operation.")
            )
        existing_payment = SupplierPayment.objects.filter(posting_key=existing_key).first()
        if existing_payment is not None:
            clean_amount = _money(amount)
            reference, normalized_reference = _payment_reference(
                method=method,
                telebirr_reference=telebirr_reference,
            )
            if (
                existing_payment.purchase_id != locked_purchase.id
                or existing_payment.amount != clean_amount
                or existing_payment.method != method
                or existing_payment.supplier_reference != supplier_reference.strip()
                or existing_payment.telebirr_reference != reference
                or existing_payment.telebirr_reference_normalized != normalized_reference
            ):
                raise ValidationError(
                    _("An idempotent replay must match the original supplier payment.")
                )
            return existing_payment
        raise ValidationError(_("The supplier-payment posting is incomplete."))
    clean_amount = _money(amount)
    if clean_amount <= 0:
        raise ValidationError(_("Supplier payment amount must be greater than zero."))
    totals = purchase_settlement_totals(locked_purchase)
    if clean_amount > totals["remaining_operational_reference_balance"]:
        raise ValidationError(
            _("Supplier payment cannot exceed the remaining operational reference balance.")
        )
    reference, normalized_reference = _payment_reference(
        method=method,
        telebirr_reference=telebirr_reference,
    )
    cash_session = (
        lock_open_cash_session(actor=actor, branch=locked_purchase.branch)
        if method == OperationalPaymentMethod.CASH
        else None
    )
    payment_id = uuid4()
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.SUPPLIER_PAYMENT,
        source_id=payment_id,
    )
    business_date = _business_date(
        method=method,
        cash_session=cash_session,
        posted_at=timestamp,
    )
    with _translate_expense_constraint_errors():
        payment = SupplierPayment.objects.create(
            id=posting_key.source_id,
            business=actor.business,
            branch=locked_purchase.branch,
            supplier=locked_purchase.supplier,
            purchase=locked_purchase,
            business_date=business_date,
            amount=clean_amount,
            method=method,
            supplier_reference=supplier_reference,
            telebirr_reference=reference,
            telebirr_reference_normalized=normalized_reference,
            cash_session=cash_session,
            posting_key=posting_key,
            posted_by=actor,
            posted_at=timestamp,
        )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.SUPPLIER_PAYMENT,
            amount_delta=-clean_amount,
            source_id=payment.id,
            posted_at=timestamp,
        )
    return payment


@transaction.atomic
def reverse_supplier_payment(
    *,
    actor: BusinessMembership,
    supplier_payment: SupplierPayment,
    reason: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> SupplierPaymentReversal:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    purchase_id = (
        SupplierPayment.objects.only("purchase_id")
        .get(
            pk=supplier_payment.pk,
            business=actor.business,
        )
        .purchase_id
    )
    purchase = Purchase.objects.only("id").get(pk=purchase_id, business=actor.business)
    _lock_purchase(actor, purchase)
    locked = (
        SupplierPayment.objects.select_for_update()
        .select_related("branch", "purchase")
        .get(pk=supplier_payment.pk, business=actor.business)
    )
    existing = SupplierPaymentReversal.objects.filter(supplier_payment=locked).first()
    if existing is not None:
        if existing.posting_key.key == idempotency_key:
            return existing
        raise ValidationError(_("This supplier payment is already reversed."))
    cash_session = (
        _locked_original_cash_session(
            actor=actor,
            session=locked.cash_session,
            branch=locked.branch,
        )
        if locked.method == OperationalPaymentMethod.CASH
        else None
    )
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.SUPPLIER_PAYMENT_REVERSAL,
        source_id=locked.id,
    )
    _require_exact_source(posting_key, locked.id)
    reversal = SupplierPaymentReversal.objects.create(
        business=actor.business,
        branch=locked.branch,
        supplier_payment=locked,
        posting_key=posting_key,
        reason=reason,
        cash_session=cash_session,
        reversed_by=actor,
        posted_at=timestamp,
    )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.SUPPLIER_PAYMENT_REVERSAL,
            amount_delta=locked.amount,
            source_id=reversal.id,
            posted_at=timestamp,
        )
    return reversal


@transaction.atomic
def post_supplier_return_settlement(
    *,
    actor: BusinessMembership,
    purchase_return: PurchaseReturn,
    settlement_type: str,
    amount: Decimal,
    method: str,
    supplier_reference: str,
    telebirr_reference: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> SupplierReturnSettlement:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    purchase_id = (
        PurchaseReturn.objects.only("purchase_id")
        .get(
            pk=purchase_return.pk,
            business=actor.business,
        )
        .purchase_id
    )
    purchase = Purchase.objects.only("id").get(pk=purchase_id, business=actor.business)
    locked_purchase = _lock_purchase(actor, purchase)
    locked_return = (
        PurchaseReturn.objects.select_for_update()
        .select_related("branch", "supplier", "purchase")
        .get(pk=purchase_return.pk, business=actor.business)
    )
    if locked_return.status != PurchaseReturnStatus.POSTED:
        raise ValidationError(_("Only posted, unreversed purchase returns can be settled."))
    ExpenseSettlementPostingKey.objects.select_for_update().filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    existing_key = ExpenseSettlementPostingKey.objects.filter(
        business=actor.business,
        key=idempotency_key,
    ).first()
    if existing_key is not None:
        if existing_key.operation_type != ExpenseSettlementOperationType.RETURN_SETTLEMENT:
            raise ValidationError(
                _("This idempotency key belongs to another expense or settlement operation.")
            )
        existing_settlement = SupplierReturnSettlement.objects.filter(
            posting_key=existing_key
        ).first()
        if existing_settlement is not None:
            clean_amount = _money(amount)
            expected_method = (
                "" if settlement_type == SupplierReturnSettlementType.CREDIT else method
            )
            expected_reference = (
                ""
                if settlement_type == SupplierReturnSettlementType.CREDIT
                else telebirr_reference.strip()
            )
            if (
                existing_settlement.purchase_return_id != locked_return.id
                or existing_settlement.settlement_type != settlement_type
                or existing_settlement.amount != clean_amount
                or existing_settlement.method != expected_method
                or existing_settlement.supplier_reference != supplier_reference.strip()
                or existing_settlement.telebirr_reference != expected_reference
            ):
                raise ValidationError(
                    _("An idempotent replay must match the original return settlement.")
                )
            return existing_settlement
        raise ValidationError(_("The supplier-return settlement posting is incomplete."))
    clean_amount = _money(amount)
    if clean_amount <= 0:
        raise ValidationError(_("Settlement amount must be greater than zero."))
    return_totals = purchase_return_settlement_totals(locked_return)
    if clean_amount > return_totals["unresolved_supplier_return_reference"]:
        raise ValidationError(
            _("Settlement cannot exceed the unresolved supplier-return reference.")
        )
    if settlement_type == SupplierReturnSettlementType.CREDIT:
        if method or telebirr_reference.strip():
            raise ValidationError(_("Supplier credit does not use cash or Telebirr evidence."))
        purchase_totals = purchase_settlement_totals(locked_purchase)
        if clean_amount > purchase_totals["remaining_operational_reference_balance"]:
            raise ValidationError(
                _("Supplier credit cannot make the operational purchase balance negative.")
            )
        payment_method = ""
        reference = ""
        normalized_reference = ""
        cash_session = None
        business_date = timezone.localtime(timestamp).date()
    elif settlement_type == SupplierReturnSettlementType.REFUND:
        purchase_totals = purchase_settlement_totals(locked_purchase)
        net_paid = _money(
            purchase_totals["active_supplier_payments"] - purchase_totals["active_supplier_refunds"]
        )
        if clean_amount > net_paid:
            raise ValidationError(
                _("Supplier refund cannot exceed active supplier payments net of refunds.")
            )
        reference, normalized_reference = _payment_reference(
            method=method,
            telebirr_reference=telebirr_reference,
        )
        payment_method = method
        cash_session = (
            lock_open_cash_session(actor=actor, branch=locked_return.branch)
            if method == OperationalPaymentMethod.CASH
            else None
        )
        business_date = _business_date(
            method=method,
            cash_session=cash_session,
            posted_at=timestamp,
        )
    else:
        raise ValidationError(_("Select supplier credit or refund recovered."))
    settlement_id = uuid4()
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.RETURN_SETTLEMENT,
        source_id=settlement_id,
    )
    with _translate_expense_constraint_errors():
        settlement = SupplierReturnSettlement.objects.create(
            id=posting_key.source_id,
            business=actor.business,
            branch=locked_return.branch,
            supplier=locked_return.supplier,
            purchase=locked_purchase,
            purchase_return=locked_return,
            business_date=business_date,
            settlement_type=settlement_type,
            amount=clean_amount,
            method=payment_method,
            supplier_reference=supplier_reference,
            telebirr_reference=reference,
            telebirr_reference_normalized=normalized_reference,
            cash_session=cash_session,
            posting_key=posting_key,
            posted_by=actor,
            posted_at=timestamp,
        )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.SUPPLIER_REFUND,
            amount_delta=clean_amount,
            source_id=settlement.id,
            posted_at=timestamp,
        )
    return settlement


@transaction.atomic
def reverse_supplier_return_settlement(
    *,
    actor: BusinessMembership,
    settlement: SupplierReturnSettlement,
    reason: str,
    idempotency_key: UUID,
    posted_at: datetime | None = None,
) -> SupplierReturnSettlementReversal:
    _validate_actor(actor)
    timestamp = posted_at or timezone.now()
    settlement_ids = SupplierReturnSettlement.objects.only(
        "purchase_id",
        "purchase_return_id",
    ).get(
        pk=settlement.pk,
        business=actor.business,
    )
    purchase = Purchase.objects.only("id").get(
        pk=settlement_ids.purchase_id,
        business=actor.business,
    )
    _lock_purchase(actor, purchase)
    PurchaseReturn.objects.select_for_update().get(
        pk=settlement_ids.purchase_return_id,
        business=actor.business,
    )
    locked = (
        SupplierReturnSettlement.objects.select_for_update()
        .select_related("branch", "purchase", "purchase_return")
        .get(pk=settlement.pk, business=actor.business)
    )
    existing = SupplierReturnSettlementReversal.objects.filter(settlement=locked).first()
    if existing is not None:
        if existing.posting_key.key == idempotency_key:
            return existing
        raise ValidationError(_("This supplier-return settlement is already reversed."))
    cash_session = (
        _locked_original_cash_session(
            actor=actor,
            session=locked.cash_session,
            branch=locked.branch,
        )
        if locked.settlement_type == SupplierReturnSettlementType.REFUND
        and locked.method == OperationalPaymentMethod.CASH
        else None
    )
    if cash_session is not None:
        if expected_cash(cash_session) - locked.amount < 0:
            raise ValidationError(_("This cash movement would make expected cash negative."))
    posting_key = _claim_posting_key(
        actor=actor,
        key=idempotency_key,
        operation_type=ExpenseSettlementOperationType.RETURN_SETTLEMENT_REVERSAL,
        source_id=locked.id,
    )
    _require_exact_source(posting_key, locked.id)
    reversal = SupplierReturnSettlementReversal.objects.create(
        business=actor.business,
        branch=locked.branch,
        settlement=locked,
        posting_key=posting_key,
        reason=reason,
        cash_session=cash_session,
        reversed_by=actor,
        posted_at=timestamp,
    )
    if cash_session is not None:
        record_source_cash_movement(
            actor=actor,
            session=cash_session,
            movement_type=CashMovementType.SUPPLIER_REFUND_REVERSAL,
            amount_delta=-locked.amount,
            source_id=reversal.id,
            posted_at=timestamp,
        )
    return reversal
