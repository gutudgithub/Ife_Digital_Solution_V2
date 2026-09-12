from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.cash.forms import (
    CashSessionCloseForm,
    CashSessionFilterForm,
    CashSessionOpenForm,
    CashSessionReopenForm,
    ManualCashMovementForm,
)
from apps.cash.models import CashMovementType, CashSession, CashSessionClosure
from apps.cash.services import (
    close_cash_session,
    expected_cash,
    open_cash_session,
    post_manual_cash_movement,
    reopen_cash_session,
)
from apps.forms import add_accessible_error_attributes
from apps.sales.models import SalePayment, SaleRefundEvidence


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    if not tenant_request.active_membership.can_use_cash_sessions:
        raise PermissionDenied(_("Cash-session permission is required."))
    return tenant_request


def _visible_sessions(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[CashSession]:
    sessions = CashSession.objects.filter(business=business)
    if membership.can_manage_cash_movements:
        return sessions
    if membership.assigned_branch_id:
        return sessions.filter(branch_id=membership.assigned_branch_id)
    active_branch_ids = list(
        business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
    )
    if len(active_branch_ids) == 1:
        return sessions.filter(branch_id=active_branch_ids[0])
    return sessions.none()


@login_required
def session_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sessions = (
        _visible_sessions(business=business, membership=membership)
        .select_related("branch", "opened_by__user")
        .prefetch_related("movements", "closures")
    )
    filter_form = CashSessionFilterForm(
        request.GET or None,
        membership=membership,
    )
    if filter_form.is_valid():
        branch = filter_form.cleaned_data.get("branch")
        status = filter_form.cleaned_data.get("status")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")
        if branch is not None:
            sessions = sessions.filter(branch=branch)
        if status:
            sessions = sessions.filter(status=status)
        if isinstance(date_from, date):
            sessions = sessions.filter(business_date__gte=date_from)
        if isinstance(date_to, date):
            sessions = sessions.filter(business_date__lte=date_to)
    page_obj = Paginator(sessions, 50).get_page(request.GET.get("page"))
    rows = []
    for session in page_obj.object_list:
        session_closures = list(session.closures.all())
        derived_expected = sum(
            (movement.amount_delta for movement in session.movements.all()),
            Decimal("0.00"),
        )
        rows.append(
            {
                "session": session,
                "expected_cash": derived_expected,
                "latest_closure": session_closures[-1] if session_closures else None,
            }
        )
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "cash/session_list.html",
        {
            "rows": rows,
            "filter_form": filter_form,
            "page_obj": page_obj,
            "can_manage_cash_movements": membership.can_manage_cash_movements,
        },
    )


@login_required
def session_detail(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    session = get_object_or_404(
        _visible_sessions(business=business, membership=membership).select_related(
            "branch",
            "opened_by__user",
        ),
        pk=session_id,
    )
    movement_page = Paginator(
        session.movements.select_related("actor__user", "posting_key"),
        50,
    ).get_page(request.GET.get("movement_page"))
    source_ids = [
        movement.source_id
        for movement in movement_page.object_list
        if movement.source_id is not None
    ]
    sale_payments = {
        payment.id: payment
        for payment in SalePayment.objects.filter(
            business=business,
            id__in=source_ids,
        ).select_related("sale")
    }
    refund_evidence = {
        refund.id: refund
        for refund in SaleRefundEvidence.objects.filter(
            business=business,
            id__in=source_ids,
        ).select_related("sale_return")
    }
    movement_rows = []
    for movement in movement_page.object_list:
        source_label = ""
        source_url = ""
        if movement.movement_type == CashMovementType.CASH_SALE:
            payment = sale_payments.get(movement.source_id)
            if payment is not None:
                source_label = payment.sale.internal_number
                source_url = reverse("sales:sale-detail", args=[payment.sale_id])
        elif movement.movement_type == CashMovementType.CASH_REFUND:
            refund = refund_evidence.get(movement.source_id)
            if refund is not None:
                source_label = refund.sale_return.internal_number
                source_url = reverse(
                    "sales:return-detail",
                    args=[refund.sale_return_id],
                )
        movement_rows.append(
            {
                "movement": movement,
                "source_label": source_label,
                "source_url": source_url,
            }
        )
    closures = session.closures.select_related("closed_by__user").prefetch_related("reopening")
    return render(
        request,
        "cash/session_detail.html",
        {
            "cash_session": session,
            "expected_cash": expected_cash(session),
            "movement_rows": movement_rows,
            "movement_page": movement_page,
            "closures": closures,
            "can_manage_cash_movements": membership.can_manage_cash_movements,
            "can_reopen_cash_sessions": membership.can_reopen_cash_sessions,
        },
    )


@login_required
def session_open(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    form = CashSessionOpenForm(
        request.POST or None,
        membership=membership,
    )
    if request.method == "POST" and form.is_valid():
        try:
            session = open_cash_session(
                actor=membership,
                branch=form.cleaned_data["branch"],
                opening_float=cast(Decimal, form.cleaned_data["opening_float"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Cash session opened."))
            return redirect("cash:session-detail", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "cash/session_form.html",
        {"form": form, "page_title": _("Open cash session")},
    )


@login_required
def manual_movement(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_manage_cash_movements:
        raise PermissionDenied(_("Cash movement management permission is required."))
    session = get_object_or_404(
        _visible_sessions(business=business, membership=membership),
        pk=session_id,
    )
    form = ManualCashMovementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_manual_cash_movement(
                actor=membership,
                session=session,
                movement_type=cast(str, form.cleaned_data["movement_type"]),
                amount=cast(Decimal, form.cleaned_data["amount"]),
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Cash movement posted."))
            return redirect("cash:session-detail", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "cash/session_form.html",
        {
            "form": form,
            "cash_session": session,
            "page_title": _("Post manual cash movement"),
        },
    )


@login_required
def session_close(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    session = get_object_or_404(
        _visible_sessions(business=business, membership=membership),
        pk=session_id,
    )
    derived_expected = expected_cash(session)
    form = CashSessionCloseForm(
        request.POST or None,
        expected_amount=derived_expected,
    )
    if request.method == "POST" and form.is_valid():
        try:
            closure = close_cash_session(
                actor=membership,
                session=session,
                actual_cash=cast(Decimal, form.cleaned_data["actual_cash"]),
                explanation=cast(str, form.cleaned_data["explanation"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Cash session closed."))
            return redirect("cash:closure-report", closure_id=closure.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "cash/session_close.html",
        {
            "form": form,
            "cash_session": session,
            "expected_cash": derived_expected,
        },
    )


@login_required
def session_reopen(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_reopen_cash_sessions:
        raise PermissionDenied(_("Cash-session reopening permission is required."))
    session = get_object_or_404(
        _visible_sessions(business=business, membership=membership),
        pk=session_id,
    )
    form = CashSessionReopenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reopen_cash_session(
                actor=membership,
                session=session,
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Cash session reopened."))
            return redirect("cash:session-detail", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "cash/session_form.html",
        {
            "form": form,
            "cash_session": session,
            "page_title": _("Reopen cash session"),
        },
    )


@login_required
def closure_report(request: HttpRequest, closure_id: UUID) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    closure = get_object_or_404(
        CashSessionClosure.objects.select_related(
            "session",
            "branch",
            "closed_by__user",
        ).filter(
            business=business,
            session__in=_visible_sessions(
                business=business,
                membership=membership,
            ),
        ),
        pk=closure_id,
    )
    return render(
        request,
        "cash/closure_report.html",
        {"closure": closure},
    )
