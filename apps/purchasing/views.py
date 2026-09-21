from datetime import date, datetime, time, timedelta
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.documents.provenance import (
    purchase_confirmed_source_document,
    purchase_has_confirmed_source,
)
from apps.expenses.models import SupplierPayment, SupplierReturnSettlement
from apps.expenses.services import (
    purchase_return_settlement_totals,
    purchase_settlement_totals,
)
from apps.forms import add_accessible_error_attributes
from apps.purchasing.forms import (
    BasePurchaseLineFormSet,
    PurchaseCostHistoryFilterForm,
    PurchaseForm,
    PurchaseLineFormSet,
    PurchaseReceiptForm,
    PurchaseReturnDraftForm,
    PurchaseReturnPostForm,
    PurchaseReturnReversalForm,
    SupplierForm,
)
from apps.purchasing.models import (
    GoodsReceiptLine,
    Purchase,
    PurchaseReturn,
    PurchaseReturnStatus,
    PurchaseStatus,
    Supplier,
)
from apps.purchasing.services import (
    ReceiptQuantity,
    ReturnQuantity,
    approve_purchase,
    cancel_purchase,
    cancel_purchase_return,
    new_purchase_number,
    post_purchase_return,
    purchase_line_progress,
    purchase_return_source_progress,
    receive_purchase,
    reverse_purchase_return,
    save_purchase_return_draft,
    supplier_activity_summaries,
)


def _visible_purchases(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[Purchase]:
    purchases = Purchase.objects.filter(business=business)
    if not membership.can_manage_purchasing:
        purchases = purchases.filter(
            status__in=(
                PurchaseStatus.APPROVED,
                PurchaseStatus.PARTIALLY_RECEIVED,
                PurchaseStatus.RECEIVED,
            )
        )
    return purchases


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    return tenant_request


def _visible_purchase_returns(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[PurchaseReturn]:
    purchase_returns = PurchaseReturn.objects.filter(business=business)
    if membership.can_manage_purchasing:
        return purchase_returns
    assigned_branch = membership.assigned_branch
    if assigned_branch is not None and assigned_branch.business_id == business.id:
        return purchase_returns.filter(branch=assigned_branch)
    active_branch_ids = list(
        business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
    )
    if len(active_branch_ids) == 1:
        return purchase_returns.filter(branch_id=active_branch_ids[0])
    return purchase_returns.none()


def _require_purchasing_view(request: HttpRequest) -> TenantRequest:
    tenant_request = _tenant(request)
    membership = tenant_request.active_membership
    if membership is None or not (
        membership.can_manage_purchasing or membership.can_receive_inventory
    ):
        raise PermissionDenied(_("Purchasing access is required."))
    return tenant_request


def _require_purchasing_manager(request: HttpRequest) -> TenantRequest:
    tenant_request = _tenant(request)
    membership = tenant_request.active_membership
    if membership is None or not membership.can_manage_purchasing:
        raise PermissionDenied(_("Purchasing management permission is required."))
    return tenant_request


@login_required
def supplier_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    supplier_summaries = supplier_activity_summaries(actor=membership)
    return render(
        request,
        "purchasing/supplier_list.html",
        {
            "supplier_summaries": supplier_summaries,
            "can_manage_purchasing": membership.can_manage_purchasing,
        },
    )


@login_required
def supplier_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    form = SupplierForm(request.POST or None)
    form.instance.business = business
    if request.method == "POST" and form.is_valid():
        supplier = form.save(commit=False)
        supplier.business = business
        supplier.save()
        messages.success(request, _("Supplier created."))
        return redirect("purchasing:supplier-list")
    add_accessible_error_attributes(form)
    return render(request, "purchasing/supplier_form.html", {"form": form})


@login_required
def supplier_edit(request: HttpRequest, supplier_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    supplier = get_object_or_404(
        Supplier,
        pk=supplier_id,
        business=business,
    )
    form = SupplierForm(request.POST or None, instance=supplier)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Supplier updated."))
        return redirect("purchasing:supplier-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "purchasing/supplier_form.html",
        {"form": form, "supplier": supplier},
    )


@login_required
def purchase_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchases = _visible_purchases(
        business=business,
        membership=membership,
    ).select_related(
        "supplier",
        "branch",
    )
    return render(
        request,
        "purchasing/purchase_list.html",
        {
            "purchases": purchases,
            "can_manage_purchasing": membership.can_manage_purchasing,
        },
    )


@login_required
def purchase_cost_history(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    lines = GoodsReceiptLine.objects.filter(
        business=business,
    ).select_related(
        "receipt",
        "receipt__purchase",
        "receipt__purchase__supplier",
        "receipt__branch",
        "variant",
        "variant__product",
    )
    if not membership.can_manage_purchasing:
        assigned_branch = membership.assigned_branch
        if assigned_branch is not None and assigned_branch.business_id == business.id:
            lines = lines.filter(receipt__branch=assigned_branch)
        else:
            active_branch_ids = list(
                business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
            )
            if len(active_branch_ids) == 1:
                lines = lines.filter(receipt__branch_id=active_branch_ids[0])
            else:
                lines = lines.none()
    filter_form = PurchaseCostHistoryFilterForm(request.GET or None)
    filter_form.scope_to_business(business)
    if filter_form.is_valid():
        supplier = filter_form.cleaned_data.get("supplier")
        variant = filter_form.cleaned_data.get("variant")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")
        if supplier is not None:
            lines = lines.filter(receipt__purchase__supplier=supplier)
        if variant is not None:
            lines = lines.filter(variant=variant)
        default_timezone = timezone.get_default_timezone()
        if isinstance(date_from, date):
            start = timezone.make_aware(
                datetime.combine(date_from, time.min),
                default_timezone,
            )
            lines = lines.filter(receipt__posted_at__gte=start)
        if isinstance(date_to, date):
            end = timezone.make_aware(
                datetime.combine(date_to + timedelta(days=1), time.min),
                default_timezone,
            )
            lines = lines.filter(receipt__posted_at__lt=end)
    page_obj = Paginator(lines, 50).get_page(request.GET.get("page"))
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "purchasing/purchase_cost_history.html",
        {
            "filter_form": filter_form,
            "receipt_lines": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


def _purchase_form_response(
    request: HttpRequest,
    *,
    purchase: Purchase,
    tenant_request: TenantRequest,
) -> HttpResponse:
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    form = PurchaseForm(request.POST or None, instance=purchase)
    form.scope_to_business(business)
    formset = cast(
        BasePurchaseLineFormSet,
        PurchaseLineFormSet(request.POST or None, instance=purchase),
    )
    formset.scope_to_business(business)
    if request.method == "POST":
        form_is_valid = form.is_valid()
        formset_is_valid = formset.is_valid()
        if form_is_valid and formset_is_valid:
            with transaction.atomic():
                saved_purchase = form.save(commit=False)
                saved_purchase.business = business
                saved_purchase.created_by = membership
                if not saved_purchase.internal_number:
                    saved_purchase.internal_number = new_purchase_number()
                saved_purchase.save()
                formset.instance = saved_purchase
                formset.save()
            messages.success(request, _("Purchase draft saved."))
            return redirect("purchasing:purchase-detail", purchase_id=saved_purchase.id)
    add_accessible_error_attributes(form)
    for line_form in formset.forms:
        add_accessible_error_attributes(line_form)
    return render(
        request,
        "purchasing/purchase_form.html",
        {"form": form, "formset": formset, "purchase": purchase},
    )


@login_required
def purchase_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = Purchase(
        business=business,
        created_by=membership,
        internal_number=new_purchase_number(),
    )
    return _purchase_form_response(
        request,
        purchase=purchase,
        tenant_request=tenant_request,
    )


@login_required
def purchase_edit(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    purchase = get_object_or_404(
        Purchase,
        pk=purchase_id,
        business=business,
    )
    if purchase.status != PurchaseStatus.DRAFT:
        raise PermissionDenied(_("Only draft purchases can be edited."))
    if purchase_has_confirmed_source(purchase):
        raise PermissionDenied(
            _(
                "This purchase matches owner-confirmed document evidence. "
                "Cancel it and start a replacement transcription to correct it."
            )
        )
    return _purchase_form_response(
        request,
        purchase=purchase,
        tenant_request=tenant_request,
    )


@login_required
def purchase_detail(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = get_object_or_404(
        _visible_purchases(
            business=business,
            membership=membership,
        ).select_related("supplier", "branch", "approved_by__user"),
        pk=purchase_id,
    )
    can_manage_settlement = membership.can_manage_supplier_settlement
    return render(
        request,
        "purchasing/purchase_detail.html",
        {
            "purchase": purchase,
            "line_progress": purchase_line_progress(purchase),
            "receipts": purchase.receipts.select_related("received_by__user"),
            "purchase_returns": _visible_purchase_returns(
                business=business,
                membership=membership,
            )
            .filter(purchase=purchase)
            .select_related("created_by__user", "posted_by__user"),
            "can_manage_purchasing": membership.can_manage_purchasing,
            "can_receive_inventory": membership.can_receive_inventory,
            "has_receipts": purchase.receipts.exists(),
            "can_manage_settlement": can_manage_settlement,
            "source_document": (
                purchase_confirmed_source_document(purchase)
                if membership.can_manage_documents
                else None
            ),
            "settlement_totals": (
                purchase_settlement_totals(purchase) if can_manage_settlement else None
            ),
            "supplier_payments": (
                SupplierPayment.objects.filter(
                    business=business,
                    purchase=purchase,
                ).select_related("posted_by__user", "reversal")
                if can_manage_settlement
                else SupplierPayment.objects.none()
            ),
        },
    )


@login_required
@require_POST
def purchase_approve(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = get_object_or_404(
        Purchase,
        pk=purchase_id,
        business=business,
    )
    try:
        approve_purchase(actor=membership, purchase=purchase)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, _("Purchase approved."))
    return redirect("purchasing:purchase-detail", purchase_id=purchase.id)


@login_required
@require_POST
def purchase_cancel(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = get_object_or_404(
        Purchase,
        pk=purchase_id,
        business=business,
    )
    try:
        cancel_purchase(actor=membership, purchase=purchase)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, _("Purchase cancelled."))
    return redirect("purchasing:purchase-detail", purchase_id=purchase.id)


@login_required
def purchase_receive(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_receive_inventory:
        raise PermissionDenied(_("Inventory receiving permission is required."))
    purchase = get_object_or_404(
        _visible_purchases(
            business=business,
            membership=membership,
        )
        .filter(
            status__in=(
                PurchaseStatus.APPROVED,
                PurchaseStatus.PARTIALLY_RECEIVED,
            )
        )
        .select_related("branch", "supplier"),
        pk=purchase_id,
    )
    progress = purchase_line_progress(purchase)
    form = PurchaseReceiptForm(request.POST or None, progress=progress)
    if request.method == "POST" and form.is_valid():
        quantities = [
            ReceiptQuantity(purchase_line_id=line_id, quantity=quantity)
            for line_id, quantity in form.receipt_quantities().items()
        ]
        try:
            receive_purchase(
                actor=membership,
                purchase=purchase,
                quantities=quantities,
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
                supplier_document_reference=cast(
                    str,
                    form.cleaned_data["supplier_document_reference"],
                ),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Purchase receipt posted."))
            return redirect("purchasing:purchase-detail", purchase_id=purchase.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "purchasing/purchase_receive.html",
        {"form": form, "purchase": purchase},
    )


@login_required
def purchase_return_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_returns = _visible_purchase_returns(
        business=business,
        membership=membership,
    ).select_related("purchase", "supplier", "branch")
    return render(
        request,
        "purchasing/purchase_return_list.html",
        {
            "purchase_returns": purchase_returns,
            "can_manage_purchasing": membership.can_manage_purchasing,
        },
    )


def _purchase_return_form_response(
    request: HttpRequest,
    *,
    purchase: Purchase,
    membership: BusinessMembership,
    purchase_return: PurchaseReturn | None = None,
) -> HttpResponse:
    progress = purchase_return_source_progress(purchase)
    form = PurchaseReturnDraftForm(
        request.POST or None,
        progress=progress,
        purchase_return=purchase_return,
    )
    if request.method == "POST" and form.is_valid():
        quantities = [
            ReturnQuantity(receipt_line_id=line_id, quantity=quantity)
            for line_id, quantity in form.return_quantities().items()
        ]
        try:
            saved_return = save_purchase_return_draft(
                actor=membership,
                purchase=purchase,
                purchase_return=purchase_return,
                return_date=cast(date, form.cleaned_data["return_date"]),
                reason=cast(str, form.cleaned_data["reason"]),
                supplier_document_reference=cast(
                    str,
                    form.cleaned_data["supplier_document_reference"],
                ),
                quantities=quantities,
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Purchase return draft saved."))
            return redirect(
                "purchasing:purchase-return-detail",
                return_id=saved_return.id,
            )
    add_accessible_error_attributes(form)
    return render(
        request,
        "purchasing/purchase_return_form.html",
        {
            "form": form,
            "purchase": purchase,
            "purchase_return": purchase_return,
        },
    )


@login_required
def purchase_return_create(request: HttpRequest, purchase_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase = get_object_or_404(
        _visible_purchases(
            business=business,
            membership=membership,
        ).select_related("branch", "supplier"),
        pk=purchase_id,
    )
    if not purchase.receipts.exists():
        raise PermissionDenied(_("A purchase return requires posted goods receipts."))
    return _purchase_return_form_response(
        request,
        purchase=purchase,
        membership=membership,
    )


@login_required
def purchase_return_edit(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_return = get_object_or_404(
        _visible_purchase_returns(
            business=business,
            membership=membership,
        ).select_related("purchase", "purchase__branch", "purchase__supplier"),
        pk=return_id,
        status=PurchaseReturnStatus.DRAFT,
    )
    return _purchase_return_form_response(
        request,
        purchase=purchase_return.purchase,
        membership=membership,
        purchase_return=purchase_return,
    )


@login_required
def purchase_return_detail(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_view(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_return = get_object_or_404(
        _visible_purchase_returns(
            business=business,
            membership=membership,
        ).select_related(
            "purchase",
            "supplier",
            "branch",
            "created_by__user",
            "posted_by__user",
            "cancelled_by__user",
        ),
        pk=return_id,
    )
    can_manage_settlement = membership.can_manage_supplier_settlement
    return render(
        request,
        "purchasing/purchase_return_detail.html",
        {
            "purchase_return": purchase_return,
            "return_lines": purchase_return.lines.select_related(
                "receipt_line__receipt",
                "variant",
            ),
            "can_manage_purchasing": membership.can_manage_purchasing,
            "can_view_inventory_value": membership.can_manage_purchasing,
            "can_manage_settlement": can_manage_settlement,
            "settlement_totals": (
                purchase_return_settlement_totals(purchase_return)
                if can_manage_settlement
                else None
            ),
            "supplier_settlements": (
                SupplierReturnSettlement.objects.filter(
                    business=business,
                    purchase_return=purchase_return,
                ).select_related("posted_by__user", "reversal")
                if can_manage_settlement
                else SupplierReturnSettlement.objects.none()
            ),
        },
    )


@login_required
def purchase_return_post(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related("purchase", "supplier", "branch"),
        pk=return_id,
        business=business,
        status=PurchaseReturnStatus.DRAFT,
    )
    form = PurchaseReturnPostForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            post_purchase_return(
                actor=membership,
                purchase_return=purchase_return,
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Purchase return posted."))
            return redirect("purchasing:purchase-return-detail", return_id=purchase_return.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "purchasing/purchase_return_post.html",
        {"form": form, "purchase_return": purchase_return},
    )


@login_required
@require_POST
def purchase_return_cancel(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related("purchase"),
        pk=return_id,
        business=business,
    )
    try:
        cancel_purchase_return(actor=membership, purchase_return=purchase_return)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, _("Purchase return cancelled."))
    return redirect("purchasing:purchase-return-detail", return_id=purchase_return.id)


@login_required
def purchase_return_reverse(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_purchasing_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    purchase_return = get_object_or_404(
        PurchaseReturn.objects.select_related("purchase", "supplier", "branch"),
        pk=return_id,
        business=business,
        status=PurchaseReturnStatus.POSTED,
    )
    form = PurchaseReturnReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_purchase_return(
                actor=membership,
                purchase_return=purchase_return,
                reason=cast(str, form.cleaned_data["reason"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except (PermissionDenied, ValidationError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, _("Purchase return reversed."))
            return redirect("purchasing:purchase-return-detail", return_id=purchase_return.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "purchasing/purchase_return_reverse.html",
        {"form": form, "purchase_return": purchase_return},
    )
