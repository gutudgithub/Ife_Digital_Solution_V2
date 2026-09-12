from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import Business, BusinessMembership, MembershipRole
from apps.businesses.types import TenantRequest
from apps.catalog.models import StockUnit
from apps.forms import add_accessible_error_attributes
from apps.inventory.forms import (
    InventoryAdjustmentForm,
    InventoryMovementFilterForm,
    OpeningBalanceForm,
    StockCountFilterForm,
    StockCountPostingForm,
    StockCountQuantityForm,
    StockCountReasonForm,
    StockCountReviewEvidenceForm,
    StockCountStartForm,
)
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventorySourceType,
    StockCountApproval,
    StockCountLine,
    StockCountReversal,
    StockCountSession,
    StockCountStatus,
)
from apps.inventory.services import (
    StockCountReviewSummary,
    approve_stock_count,
    cancel_stock_count,
    post_inventory_adjustment,
    post_opening_balance,
    record_stock_count_quantity,
    record_stock_count_review_evidence,
    return_stock_count_for_recount,
    reverse_stock_count,
    start_stock_count,
    stock_count_review_summary,
    submit_stock_count,
)


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    return tenant_request


def _stock_count_tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_count_inventory:
        raise PermissionDenied(_("Stock-count permission is required."))
    return tenant_request


def _visible_stock_counts(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[StockCountSession]:
    sessions = StockCountSession.objects.filter(business=business)
    if membership.can_approve_stock_counts:
        return sessions
    if membership.assigned_branch_id is not None:
        return sessions.filter(branch_id=membership.assigned_branch_id)
    active_branch_ids = list(
        business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
    )
    if len(active_branch_ids) == 1:
        return sessions.filter(branch_id=active_branch_ids[0])
    return sessions.none()


def _stock_count_session(
    *,
    business: Business,
    membership: BusinessMembership,
    session_id: UUID,
) -> StockCountSession:
    return get_object_or_404(
        _visible_stock_counts(business=business, membership=membership).select_related(
            "branch",
            "started_by__user",
            "submitted_by__user",
            "cancelled_by__user",
        ),
        pk=session_id,
    )


def _stock_count_value_adjustment(line: StockCountLine) -> Decimal | None:
    if line.variance_quantity is None or line.assigned_count_adjustment_unit_cost is None:
        return None
    return line.variance_quantity * line.assigned_count_adjustment_unit_cost


def _stock_unit_summary_rows(
    summary: StockCountReviewSummary,
) -> list[dict[str, object]]:
    return [
        {
            "label": StockUnit(unit.stock_unit).label,
            "positive_quantity": unit.positive_quantity,
            "negative_quantity": unit.negative_quantity,
            "zero_variance_line_count": unit.zero_variance_line_count,
        }
        for unit in summary.unit_summaries
    ]


@login_required
def inventory_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    balances = InventoryBalance.objects.filter(
        business=business,
    ).select_related("branch", "variant", "variant__product")
    movements = InventoryMovement.objects.filter(
        business=business,
    ).select_related("branch", "variant", "variant__product", "actor__user")
    can_view_movement_history = membership.role != MembershipRole.CASHIER.value
    visible_branch_ids: list[UUID] | None = None
    if membership.role in {
        MembershipRole.CASHIER.value,
        MembershipRole.STOCK_EMPLOYEE.value,
    }:
        if membership.assigned_branch_id is not None:
            balances = balances.filter(branch=membership.assigned_branch)
            movements = movements.filter(branch=membership.assigned_branch)
            visible_branch_ids = [membership.assigned_branch_id]
        else:
            active_branches = list(business.branches.filter(is_active=True)[:2])
            if len(active_branches) == 1:
                balances = balances.filter(branch=active_branches[0])
                movements = movements.filter(branch=active_branches[0])
                visible_branch_ids = [active_branches[0].id]
            else:
                balances = balances.none()
                movements = movements.none()
                visible_branch_ids = []
    if not can_view_movement_history:
        balances = balances.filter(branch__is_active=True, variant__is_active=True)
        movements = movements.none()
    movement_filter = InventoryMovementFilterForm(request.GET or None)
    movement_filter.scope_to_business(
        business,
        branch_ids=visible_branch_ids,
    )
    if can_view_movement_history and movement_filter.is_valid():
        branch = movement_filter.cleaned_data.get("branch")
        variant = movement_filter.cleaned_data.get("variant")
        movement_type = movement_filter.cleaned_data.get("movement_type")
        date_from = movement_filter.cleaned_data.get("date_from")
        date_to = movement_filter.cleaned_data.get("date_to")
        if branch is not None:
            movements = movements.filter(branch=branch)
        if variant is not None:
            movements = movements.filter(variant=variant)
        if movement_type:
            movements = movements.filter(movement_type=movement_type)
        default_timezone = timezone.get_default_timezone()
        if isinstance(date_from, date):
            start = timezone.make_aware(
                datetime.combine(date_from, time.min),
                default_timezone,
            )
            movements = movements.filter(posted_at__gte=start)
        if isinstance(date_to, date):
            end = timezone.make_aware(
                datetime.combine(date_to + timedelta(days=1), time.min),
                default_timezone,
            )
            movements = movements.filter(posted_at__lt=end)
    page_obj = Paginator(balances, 50).get_page(request.GET.get("page"))
    movement_page_obj = Paginator(movements, 50).get_page(request.GET.get("movement_page"))
    movement_source_ids = [movement.source_id for movement in movement_page_obj.object_list]
    count_lines = {
        line.id: line
        for line in StockCountLine.objects.filter(
            business=business,
            id__in=movement_source_ids,
        ).select_related("session")
    }
    count_reversals = {
        reversal.id: reversal
        for reversal in StockCountReversal.objects.filter(
            business=business,
            id__in=movement_source_ids,
        ).select_related("approval__session")
    }
    movement_rows = []
    for movement in movement_page_obj.object_list:
        stock_count_session_id = None
        if movement.source_type == InventorySourceType.STOCK_COUNT_LINE:
            count_line = count_lines.get(movement.source_id)
            if count_line is not None:
                stock_count_session_id = count_line.session_id
        elif movement.source_type == InventorySourceType.STOCK_COUNT_REVERSAL:
            count_reversal = count_reversals.get(movement.source_id)
            if count_reversal is not None:
                stock_count_session_id = count_reversal.approval.session_id
        movement_rows.append(
            {
                "movement": movement,
                "stock_count_session_id": stock_count_session_id,
            }
        )
    add_accessible_error_attributes(movement_filter)
    return render(
        request,
        "inventory/inventory_list.html",
        {
            "balances": page_obj.object_list,
            "movements": movement_page_obj.object_list,
            "movement_rows": movement_rows,
            "movement_filter": movement_filter,
            "can_manage_inventory": membership.can_manage_inventory,
            "can_view_inventory_cost": membership.can_view_inventory_cost,
            "can_view_movement_history": can_view_movement_history,
            "page_obj": page_obj,
            "movement_page_obj": movement_page_obj,
        },
    )


@login_required
def opening_balance_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_manage_inventory:
        raise PermissionDenied(_("Inventory management permission is required."))
    form = OpeningBalanceForm(request.POST or None)
    form.scope_to_business(business)
    if request.method == "POST" and form.is_valid():
        try:
            post_opening_balance(
                actor=membership,
                branch=form.cleaned_data["branch"],
                variant=form.cleaned_data["variant"],
                quantity=cast(Decimal, form.cleaned_data["quantity"]),
                unit_cost=cast(Decimal, form.cleaned_data["unit_cost"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Opening stock posted."))
            return redirect("inventory:inventory-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_operation_form.html",
        {"form": form, "page_title": _("Post opening stock")},
    )


@login_required
def inventory_adjust(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_manage_inventory:
        raise PermissionDenied(_("Inventory management permission is required."))
    form = InventoryAdjustmentForm(request.POST or None)
    form.scope_to_business(business)
    if request.method == "POST" and form.is_valid():
        try:
            post_inventory_adjustment(
                actor=membership,
                branch=form.cleaned_data["branch"],
                variant=form.cleaned_data["variant"],
                operation_type=cast(str, form.cleaned_data["operation_type"]),
                quantity=cast(Decimal, form.cleaned_data["quantity"]),
                unit_cost=cast(Decimal | None, form.cleaned_data["unit_cost"]),
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Inventory adjustment posted."))
            return redirect("inventory:inventory-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_operation_form.html",
        {"form": form, "page_title": _("Post inventory adjustment")},
    )


@login_required
def stock_count_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sessions = _visible_stock_counts(
        business=business,
        membership=membership,
    ).select_related(
        "branch",
        "started_by__user",
        "submitted_by__user",
        "approval__approved_by__user",
    )
    filter_form = StockCountFilterForm(
        request.GET or None,
        membership=membership,
    )
    if filter_form.is_valid():
        branch = filter_form.cleaned_data.get("branch")
        status = filter_form.cleaned_data.get("status")
        starter = filter_form.cleaned_data.get("starter")
        submitter = filter_form.cleaned_data.get("submitter")
        approver = filter_form.cleaned_data.get("approver")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")
        if branch is not None:
            sessions = sessions.filter(branch=branch)
        if status:
            sessions = sessions.filter(status=status)
        if starter is not None:
            sessions = sessions.filter(started_by=starter)
        if submitter is not None:
            sessions = sessions.filter(submitted_by=submitter)
        if approver is not None:
            sessions = sessions.filter(approval__approved_by=approver)
        if isinstance(date_from, date):
            sessions = sessions.filter(business_date__gte=date_from)
        if isinstance(date_to, date):
            sessions = sessions.filter(business_date__lte=date_to)
    page_obj = Paginator(sessions, 50).get_page(request.GET.get("page"))
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "inventory/stock_count_list.html",
        {
            "sessions": page_obj.object_list,
            "page_obj": page_obj,
            "filter_form": filter_form,
            "can_manage_stock_counts": membership.can_approve_stock_counts,
        },
    )


@login_required
def stock_count_start(request: HttpRequest) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    form = StockCountStartForm(
        request.POST or None,
        membership=membership,
    )
    if request.method == "POST" and form.is_valid():
        try:
            session = start_stock_count(
                actor=membership,
                branch=form.cleaned_data["branch"],
                count_method_note=cast(str, form.cleaned_data["count_method_note"]),
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Full-branch stock count started."))
            return redirect("inventory:stock-count-worksheet", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_count_start.html",
        {"form": form},
    )


@login_required
def stock_count_detail(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    lines = session.lines.select_related("counted_by__user").order_by(
        "product_name_snapshot",
        "sku_snapshot",
    )
    line_count = lines.count()
    counted_line_count = lines.exclude(physical_quantity=None).count()
    approval = (
        StockCountApproval.objects.filter(business=business, session=session)
        .select_related("approved_by__user")
        .first()
    )
    reversal = None
    if approval is not None:
        reversal = (
            StockCountReversal.objects.filter(business=business, approval=approval)
            .select_related("reversed_by__user")
            .first()
        )
    summary = None
    unit_summary_rows: list[dict[str, object]] = []
    if membership.can_approve_stock_counts and session.status in {
        StockCountStatus.SUBMITTED,
        StockCountStatus.APPROVED,
    }:
        summary = stock_count_review_summary(actor=membership, session=session)
        unit_summary_rows = _stock_unit_summary_rows(summary)
    approved_line_rows = [
        {
            "line": line,
            "value_adjustment": _stock_count_value_adjustment(line),
        }
        for line in lines
    ]
    return render(
        request,
        "inventory/stock_count_detail.html",
        {
            "stock_count": session,
            "line_rows": approved_line_rows,
            "line_count": line_count,
            "counted_line_count": counted_line_count,
            "remaining_line_count": line_count - counted_line_count,
            "approval": approval,
            "reversal": reversal,
            "summary": summary,
            "unit_summary_rows": unit_summary_rows,
            "can_manage_stock_counts": membership.can_approve_stock_counts,
        },
    )


def _worksheet_response(
    *,
    request: HttpRequest,
    stock_count: StockCountSession,
    membership: BusinessMembership,
    bound_line_id: UUID | None = None,
    bound_form: StockCountQuantityForm | None = None,
) -> HttpResponse:
    search = (
        request.POST.get("q", "").strip()
        if request.method == "POST"
        else request.GET.get("q", "").strip()
    )
    page_number = request.POST.get("page") if request.method == "POST" else request.GET.get("page")
    lines = stock_count.lines.select_related("counted_by__user")
    if search:
        lines = lines.filter(
            Q(product_name_snapshot__icontains=search)
            | Q(variant_label_snapshot__icontains=search)
            | Q(sku_snapshot__icontains=search)
        )
    page_obj = Paginator(lines, 25).get_page(page_number)
    line_rows = []
    for line in page_obj.object_list:
        prefix = f"line-{line.id}"
        if line.id == bound_line_id and bound_form is not None:
            form = bound_form
        else:
            form = StockCountQuantityForm(
                None,
                line=line,
                prefix=prefix,
            )
        if line.id == bound_line_id:
            add_accessible_error_attributes(form)
        line_rows.append({"line": line, "form": form})
    counted_line_count = stock_count.lines.exclude(physical_quantity=None).count()
    line_count = stock_count.lines.count()
    return render(
        request,
        "inventory/stock_count_worksheet.html",
        {
            "stock_count": stock_count,
            "line_rows": line_rows,
            "page_obj": page_obj,
            "search": search,
            "line_count": line_count,
            "counted_line_count": counted_line_count,
            "remaining_line_count": line_count - counted_line_count,
            "can_manage_stock_counts": membership.can_approve_stock_counts,
        },
    )


@login_required
def stock_count_worksheet(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    if session.status != StockCountStatus.COUNTING:
        return redirect("inventory:stock-count-detail", session_id=session.id)
    bound_line_id: UUID | None = None
    bound_form: StockCountQuantityForm | None = None
    if request.method == "POST":
        raw_line_id = request.POST.get("line_id", "")
        try:
            bound_line_id = UUID(raw_line_id)
        except ValueError as error:
            raise Http404 from error
        line = get_object_or_404(
            session.lines.all(),
            pk=bound_line_id,
        )
        form = StockCountQuantityForm(
            request.POST,
            line=line,
            prefix=f"line-{line.id}",
        )
        if form.is_valid():
            try:
                record_stock_count_quantity(
                    actor=membership,
                    line=line,
                    physical_quantity=cast(Decimal, form.cleaned_data["physical_quantity"]),
                    replacement_reason=cast(str, form.cleaned_data["replacement_reason"]),
                )
            except (PermissionDenied, ValidationError) as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("Physical quantity recorded."))
                query = urlencode(
                    {
                        key: value
                        for key, value in {
                            "q": request.POST.get("q", ""),
                            "page": request.POST.get("page", ""),
                        }.items()
                        if value
                    }
                )
                target = redirect("inventory:stock-count-worksheet", session_id=session.id)
                if query:
                    target["Location"] = f"{target['Location']}?{query}"
                return target
        add_accessible_error_attributes(form)
        bound_form = form
    return _worksheet_response(
        request=request,
        stock_count=session,
        membership=membership,
        bound_line_id=bound_line_id,
        bound_form=bound_form,
    )


@login_required
def stock_count_submit(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    if session.status != StockCountStatus.COUNTING:
        return redirect("inventory:stock-count-detail", session_id=session.id)
    if request.method == "POST":
        try:
            submit_stock_count(actor=membership, session=session)
        except (PermissionDenied, ValidationError) as error:
            messages.error(request, str(error))
        else:
            messages.success(request, _("Stock count submitted for manager review."))
            return redirect("inventory:stock-count-detail", session_id=session.id)
    line_count = session.lines.count()
    counted_line_count = session.lines.exclude(physical_quantity=None).count()
    return render(
        request,
        "inventory/stock_count_submit.html",
        {
            "stock_count": session,
            "line_count": line_count,
            "counted_line_count": counted_line_count,
            "remaining_line_count": line_count - counted_line_count,
        },
    )


@login_required
def stock_count_review(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    if session.status != StockCountStatus.SUBMITTED:
        return redirect("inventory:stock-count-detail", session_id=session.id)
    bound_line_id: UUID | None = None
    bound_form: StockCountReviewEvidenceForm | None = None
    if request.method == "POST":
        raw_line_id = request.POST.get("line_id", "")
        try:
            bound_line_id = UUID(raw_line_id)
        except ValueError as error:
            raise Http404 from error
        line = get_object_or_404(session.lines.all(), pk=bound_line_id)
        form = StockCountReviewEvidenceForm(
            request.POST,
            line=line,
            prefix=f"review-{line.id}",
        )
        if form.is_valid():
            try:
                record_stock_count_review_evidence(
                    actor=membership,
                    line=line,
                    variance_explanation=cast(
                        str,
                        form.cleaned_data["variance_explanation"],
                    ),
                    exceptional_unit_cost=cast(
                        Decimal | None,
                        form.cleaned_data["exceptional_unit_cost"],
                    ),
                    exceptional_cost_evidence_note=cast(
                        str,
                        form.cleaned_data["exceptional_cost_evidence_note"],
                    ),
                )
            except (PermissionDenied, ValidationError) as error:
                form.add_error(None, str(error))
            else:
                messages.success(request, _("Stock-count review evidence saved."))
                return redirect("inventory:stock-count-review", session_id=session.id)
        add_accessible_error_attributes(form)
        bound_form = form
    lines = list(session.lines.order_by("product_name_snapshot", "sku_snapshot"))
    line_rows = []
    for line in lines:
        if line.id == bound_line_id and bound_form is not None:
            form = bound_form
        else:
            form = StockCountReviewEvidenceForm(
                None,
                line=line,
                prefix=f"review-{line.id}",
            )
        if line.id == bound_line_id:
            add_accessible_error_attributes(form)
        line_rows.append(
            {
                "line": line,
                "form": form,
                "value_adjustment": _stock_count_value_adjustment(line),
            }
        )
    summary = stock_count_review_summary(actor=membership, session=session)
    return render(
        request,
        "inventory/stock_count_review.html",
        {
            "stock_count": session,
            "line_rows": line_rows,
            "summary": summary,
            "unit_summary_rows": _stock_unit_summary_rows(summary),
        },
    )


@login_required
def stock_count_return(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    form = StockCountReasonForm(
        request.POST or None,
        label=str(_("Recount reason")),
        help_text=str(_("Explain what the counters must verify again.")),
    )
    if request.method == "POST" and form.is_valid():
        try:
            return_stock_count_for_recount(
                actor=membership,
                session=session,
                reason=cast(str, form.cleaned_data["reason"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Stock count returned for recount."))
            return redirect("inventory:stock-count-worksheet", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_count_reason.html",
        {
            "form": form,
            "stock_count": session,
            "page_title": _("Return stock count for recount"),
            "submit_label": _("Return for recount"),
        },
    )


@login_required
def stock_count_cancel(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    form = StockCountReasonForm(
        request.POST or None,
        label=str(_("Cancellation reason")),
    )
    if request.method == "POST" and form.is_valid():
        try:
            cancel_stock_count(
                actor=membership,
                session=session,
                reason=cast(str, form.cleaned_data["reason"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Stock count cancelled and inventory posting resumed."))
            return redirect("inventory:stock-count-detail", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_count_reason.html",
        {
            "form": form,
            "stock_count": session,
            "page_title": _("Cancel stock count"),
            "submit_label": _("Cancel stock count"),
        },
    )


@login_required
def stock_count_approve(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    form = StockCountPostingForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            approve_stock_count(
                actor=membership,
                session=session,
                idempotency_key=form.cleaned_data["idempotency_key"],
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Stock count approved and inventory adjustments posted."))
            return redirect("inventory:stock-count-detail", session_id=session.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "inventory/stock_count_approve.html",
        {
            "form": form,
            "stock_count": session,
            "summary": stock_count_review_summary(actor=membership, session=session),
        },
    )


@login_required
def stock_count_print(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    approval = get_object_or_404(
        StockCountApproval.objects.select_related("approved_by__user"),
        business=business,
        session=session,
    )
    reversal = (
        StockCountReversal.objects.filter(business=business, approval=approval)
        .select_related("reversed_by__user")
        .first()
    )
    summary = stock_count_review_summary(actor=membership, session=session)
    line_rows = [
        {
            "line": line,
            "value_adjustment": _stock_count_value_adjustment(line),
        }
        for line in session.lines.order_by("product_name_snapshot", "sku_snapshot")
    ]
    return render(
        request,
        "inventory/stock_count_print.html",
        {
            "stock_count": session,
            "approval": approval,
            "reversal": reversal,
            "line_rows": line_rows,
            "summary": summary,
            "unit_summary_rows": _stock_unit_summary_rows(summary),
        },
    )


@login_required
def stock_count_reverse(request: HttpRequest, session_id: UUID) -> HttpResponse:
    tenant_request = _stock_count_tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_approve_stock_counts:
        raise PermissionDenied(_("Stock-count management permission is required."))
    session = _stock_count_session(
        business=business,
        membership=membership,
        session_id=session_id,
    )
    approval = get_object_or_404(
        StockCountApproval.objects.select_related("session"),
        business=business,
        session=session,
    )
    if StockCountReversal.objects.filter(business=business, approval=approval).exists():
        return redirect("inventory:stock-count-detail", session_id=session.id)
    reason_form = StockCountReasonForm(
        request.POST or None,
        label=str(_("Reversal reason")),
        help_text=str(
            _(
                "A reversal restores the exact quantity and inventory value "
                "contributed by this count."
            )
        ),
    )
    posting_form = StockCountPostingForm(request.POST or None)
    if request.method == "POST" and reason_form.is_valid() and posting_form.is_valid():
        try:
            reverse_stock_count(
                actor=membership,
                approval=approval,
                reason=cast(str, reason_form.cleaned_data["reason"]),
                idempotency_key=posting_form.cleaned_data["idempotency_key"],
            )
        except (PermissionDenied, ValidationError) as error:
            reason_form.add_error(None, str(error))
        else:
            messages.success(request, _("Stock-count adjustment reversal posted."))
            return redirect("inventory:stock-count-detail", session_id=session.id)
    add_accessible_error_attributes(reason_form)
    add_accessible_error_attributes(posting_form)
    return render(
        request,
        "inventory/stock_count_reverse.html",
        {
            "stock_count": session,
            "approval": approval,
            "reason_form": reason_form,
            "posting_form": posting_form,
        },
    )
