from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.catalog.models import ProductVariant
from apps.forms import add_accessible_error_attributes
from apps.sales.forms import (
    BaseSaleLineFormSet,
    BaseSaleReturnLineFormSet,
    ReceiptLookupForm,
    SaleCancelForm,
    SaleFilterForm,
    SaleForm,
    SaleLineFormSet,
    SalePostForm,
    SaleReturnCancelForm,
    SaleReturnFilterForm,
    SaleReturnForm,
    SaleReturnLineFormSet,
    SaleReturnPostForm,
    SaleReturnReversalForm,
)
from apps.sales.models import (
    InternalReceipt,
    InternalReturnReceipt,
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnPurpose,
    SaleReturnStatus,
    SaleStatus,
)
from apps.sales.services import (
    ReturnQuantity,
    SaleQuantity,
    cancel_sale,
    cancel_sale_return,
    post_sale,
    post_sale_return,
    reverse_sale_return,
    sale_line_return_progress,
    save_sale_draft,
    save_sale_return_draft,
)


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    return tenant_request


def _require_sales(request: HttpRequest) -> TenantRequest:
    tenant_request = _tenant(request)
    membership = tenant_request.active_membership
    if membership is None or not membership.can_sell:
        raise PermissionDenied(_("Sales permission is required."))
    return tenant_request


def _visible_sales(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[Sale]:
    sales = Sale.objects.filter(business=business)
    if membership.can_sell_across_branches:
        return sales
    if membership.assigned_branch_id:
        return sales.filter(branch_id=membership.assigned_branch_id)
    active_branch_ids = list(
        business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
    )
    if len(active_branch_ids) == 1:
        return sales.filter(branch_id=active_branch_ids[0])
    return sales.none()


def _visible_returns(
    *,
    business: Business,
    membership: BusinessMembership,
) -> QuerySet[SaleReturn]:
    returns = SaleReturn.objects.filter(
        business=business,
        sale__in=_visible_sales(business=business, membership=membership),
    )
    if membership.can_sell_across_branches:
        return returns
    return returns.filter(
        purpose=SaleReturnPurpose.CUSTOMER_RETURN,
        status=SaleReturnStatus.DRAFT,
    )


def _require_return_manager(request: HttpRequest) -> TenantRequest:
    tenant_request = _require_sales(request)
    membership = tenant_request.active_membership
    if membership is None or not membership.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required."))
    return tenant_request


def _sale_quantities(formset: BaseSaleLineFormSet) -> list[SaleQuantity]:
    quantities: list[SaleQuantity] = []
    for form in formset.forms:
        if not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        variant = form.cleaned_data.get("variant")
        quantity = form.cleaned_data.get("quantity")
        if isinstance(variant, ProductVariant) and isinstance(quantity, Decimal):
            quantities.append(SaleQuantity(variant_id=variant.id, quantity=quantity))
    return quantities


def _return_quantities(formset: BaseSaleReturnLineFormSet) -> list[ReturnQuantity]:
    quantities: list[ReturnQuantity] = []
    for form in formset.forms:
        if not form.cleaned_data or form.cleaned_data.get("DELETE"):
            continue
        sale_line = form.cleaned_data.get("sale_line")
        quantity = form.cleaned_data.get("returned_quantity")
        if isinstance(sale_line, SaleLine) and isinstance(quantity, Decimal):
            quantities.append(ReturnQuantity(sale_line_id=sale_line.id, quantity=quantity))
    return quantities


def _sale_form_response(
    request: HttpRequest,
    *,
    sale: Sale,
    tenant_request: TenantRequest,
) -> HttpResponse:
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    form = SaleForm(request.POST or None, instance=sale)
    form.scope_to_membership(membership)
    formset = cast(
        BaseSaleLineFormSet,
        SaleLineFormSet(request.POST or None, instance=sale),
    )
    formset.scope_to_business(business)
    if request.method == "POST":
        form_is_valid = form.is_valid()
        formset_is_valid = formset.is_valid()
        if form_is_valid and formset_is_valid:
            branch = form.cleaned_data["branch"]
            sale_date = form.cleaned_data["sale_date"]
            if not isinstance(branch, Branch) or not isinstance(sale_date, date):
                raise TypeError("Validated sale form returned invalid values.")
            try:
                saved_sale = save_sale_draft(
                    actor=membership,
                    branch=branch,
                    sale_date=sale_date,
                    quantities=_sale_quantities(formset),
                    sale=sale if sale.pk else None,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _("Sale draft saved."))
                return redirect("sales:sale-detail", sale_id=saved_sale.id)
    add_accessible_error_attributes(form)
    for line_form in formset.forms:
        add_accessible_error_attributes(line_form)
    return render(
        request,
        "sales/sale_form.html",
        {"form": form, "formset": formset, "sale": sale},
    )


@login_required
def sale_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sales = _visible_sales(business=business, membership=membership).select_related(
        "branch",
        "payment",
        "receipt",
    )
    filter_form = SaleFilterForm(request.GET or None)
    filter_form.scope_to_membership(membership)
    if filter_form.is_valid():
        search = str(filter_form.cleaned_data.get("search") or "").strip()
        branch = filter_form.cleaned_data.get("branch")
        status = filter_form.cleaned_data.get("status")
        payment_method = filter_form.cleaned_data.get("payment_method")
        date_from = filter_form.cleaned_data.get("date_from")
        date_to = filter_form.cleaned_data.get("date_to")
        if search:
            sales = sales.filter(
                Q(internal_number__icontains=search)
                | Q(receipt__internal_number__icontains=search)
                | Q(payment__telebirr_reference__icontains=search)
            )
        if isinstance(branch, Branch):
            sales = sales.filter(branch=branch)
        if status:
            sales = sales.filter(status=status)
        if payment_method:
            sales = sales.filter(payment__method=payment_method)
        if isinstance(date_from, date):
            sales = sales.filter(sale_date__gte=date_from)
        if isinstance(date_to, date):
            sales = sales.filter(sale_date__lte=date_to)
    page_obj = Paginator(sales, 50).get_page(request.GET.get("page"))
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "sales/sale_list.html",
        {
            "filter_form": filter_form,
            "sales": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@login_required
def sale_create(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = Sale(
        business=business,
        created_by=membership,
        internal_number="",
    )
    return _sale_form_response(request, sale=sale, tenant_request=tenant_request)


@login_required
def sale_edit(request: HttpRequest, sale_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = get_object_or_404(
        _visible_sales(business=business, membership=membership),
        pk=sale_id,
        status=SaleStatus.DRAFT,
    )
    return _sale_form_response(request, sale=sale, tenant_request=tenant_request)


@login_required
def sale_detail(request: HttpRequest, sale_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = get_object_or_404(
        _visible_sales(business=business, membership=membership).select_related(
            "branch",
            "created_by__user",
            "posted_by__user",
            "cancelled_by__user",
            "payment",
            "payment__received_by__user",
            "receipt",
        ),
        pk=sale_id,
    )
    return render(
        request,
        "sales/sale_detail.html",
        {
            "sale": sale,
            "lines": sale.lines.select_related("variant", "variant__product"),
            "can_view_sale_cost": membership.can_view_sale_cost,
            "return_progress": sale_line_return_progress(sale)
            if sale.status == SaleStatus.POSTED
            else [],
            "sale_returns": _visible_returns(
                business=business,
                membership=membership,
            )
            .filter(sale=sale)
            .select_related("receipt"),
            "can_manage_returns": membership.can_sell_across_branches,
        },
    )


@login_required
def sale_post(request: HttpRequest, sale_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = get_object_or_404(
        _visible_sales(business=business, membership=membership).select_related("branch"),
        pk=sale_id,
        status=SaleStatus.DRAFT,
    )
    form = SalePostForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            posted_sale = post_sale(
                actor=membership,
                sale=sale,
                payment_method=str(form.cleaned_data["payment_method"]),
                telebirr_reference=str(form.cleaned_data["telebirr_reference"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Sale posted and paid in full."))
            return redirect("sales:receipt-detail", receipt_id=posted_sale.receipt.id)
    add_accessible_error_attributes(form)
    return render(request, "sales/sale_post.html", {"sale": sale, "form": form})


@login_required
def sale_cancel(request: HttpRequest, sale_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = get_object_or_404(
        _visible_sales(business=business, membership=membership).select_related("branch"),
        pk=sale_id,
        status=SaleStatus.DRAFT,
    )
    form = SaleCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancel_sale(
                actor=membership,
                sale=sale,
                reason=str(form.cleaned_data["reason"]),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Sale draft cancelled."))
            return redirect("sales:sale-detail", sale_id=sale.id)
    add_accessible_error_attributes(form)
    return render(request, "sales/sale_cancel.html", {"sale": sale, "form": form})


@login_required
def receipt_lookup(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    form = ReceiptLookupForm(request.GET or None)
    if form.is_valid():
        receipt_number = str(form.cleaned_data["receipt_number"]).strip()
        receipt = (
            InternalReceipt.objects.filter(
                business=business,
                internal_number__iexact=receipt_number,
                sale__in=_visible_sales(business=business, membership=membership),
            )
            .only("id")
            .first()
        )
        if receipt is None:
            form.add_error("receipt_number", _("No matching internal receipt was found."))
        else:
            return redirect("sales:receipt-detail", receipt_id=receipt.id)
    add_accessible_error_attributes(form)
    return render(request, "sales/receipt_lookup.html", {"form": form})


@login_required
def receipt_detail(request: HttpRequest, receipt_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    receipt = get_object_or_404(
        InternalReceipt.objects.filter(
            business=business,
            sale__in=_visible_sales(business=business, membership=membership),
        ).select_related(
            "business",
            "branch",
            "sale",
            "sale__posted_by__user",
        ),
        pk=receipt_id,
    )
    return render(
        request,
        "sales/receipt_detail.html",
        {
            "receipt": receipt,
            "lines": receipt.sale.lines.all(),
        },
    )


def _sale_return_form_response(
    request: HttpRequest,
    *,
    tenant_request: TenantRequest,
    sale: Sale,
    purpose: str,
    sale_return: SaleReturn,
) -> HttpResponse:
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if purpose not in SaleReturnPurpose.values:
        raise PermissionDenied(_("Select a valid sale correction purpose."))
    if purpose == SaleReturnPurpose.SALE_REVERSAL and not membership.can_sell_across_branches:
        raise PermissionDenied(_("Sales management permission is required."))
    form = SaleReturnForm(request.POST or None, instance=sale_return)
    initial: list[dict[str, object]] | None = None
    if not request.POST and not sale_return.pk and purpose == SaleReturnPurpose.SALE_REVERSAL:
        initial = [
            {"sale_line": line.id, "returned_quantity": line.quantity}
            for line in sale.lines.order_by("created_at")
        ]
    formset = cast(
        BaseSaleReturnLineFormSet,
        SaleReturnLineFormSet(
            request.POST or None,
            instance=sale_return,
            initial=initial,
        ),
    )
    formset.scope_to_sale(sale)
    if request.method == "POST":
        form_is_valid = form.is_valid()
        formset_is_valid = formset.is_valid()
        if form_is_valid and formset_is_valid:
            return_date = form.cleaned_data["return_date"]
            if not isinstance(return_date, date):
                raise TypeError("Validated return form returned an invalid date.")
            try:
                saved_return = save_sale_return_draft(
                    actor=membership,
                    sale=sale,
                    purpose=purpose,
                    return_date=return_date,
                    reason=str(form.cleaned_data["reason"]),
                    quantities=_return_quantities(formset),
                    sale_return=sale_return if sale_return.pk else None,
                )
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, _("Sale return draft saved."))
                return redirect("sales:return-detail", return_id=saved_return.id)
    add_accessible_error_attributes(form)
    for line_form in formset.forms:
        add_accessible_error_attributes(line_form)
    return render(
        request,
        "sales/return_form.html",
        {
            "form": form,
            "formset": formset,
            "sale": sale,
            "sale_return": sale_return,
            "purpose": purpose,
        },
    )


@login_required
def sale_return_create(
    request: HttpRequest,
    sale_id: UUID,
    purpose: str,
) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale = get_object_or_404(
        _visible_sales(business=business, membership=membership).select_related("branch"),
        pk=sale_id,
        status=SaleStatus.POSTED,
    )
    sale_return = SaleReturn(
        business=business,
        branch=sale.branch,
        sale=sale,
        internal_number="",
        purpose=purpose,
        created_by=membership,
    )
    return _sale_return_form_response(
        request,
        tenant_request=tenant_request,
        sale=sale,
        purpose=purpose,
        sale_return=sale_return,
    )


@login_required
def sale_return_edit(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale_return = get_object_or_404(
        _visible_returns(business=business, membership=membership).select_related(
            "sale",
            "sale__branch",
        ),
        pk=return_id,
        status=SaleReturnStatus.DRAFT,
    )
    return _sale_return_form_response(
        request,
        tenant_request=tenant_request,
        sale=sale_return.sale,
        purpose=sale_return.purpose,
        sale_return=sale_return,
    )


@login_required
def sale_return_list(request: HttpRequest) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    returns = _visible_returns(business=business, membership=membership).select_related(
        "sale",
        "branch",
        "refund",
        "receipt",
    )
    filter_form = SaleReturnFilterForm(request.GET or None)
    if filter_form.is_valid():
        search = str(filter_form.cleaned_data.get("search") or "").strip()
        status = filter_form.cleaned_data.get("status")
        if search:
            returns = returns.filter(
                Q(internal_number__icontains=search)
                | Q(sale__internal_number__icontains=search)
                | Q(receipt__internal_number__icontains=search)
                | Q(refund__telebirr_reference__icontains=search)
            )
        if status:
            returns = returns.filter(status=status)
    page_obj = Paginator(returns, 50).get_page(request.GET.get("page"))
    add_accessible_error_attributes(filter_form)
    return render(
        request,
        "sales/return_list.html",
        {
            "filter_form": filter_form,
            "sale_returns": page_obj.object_list,
            "page_obj": page_obj,
        },
    )


@login_required
def sale_return_detail(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale_return = get_object_or_404(
        _visible_returns(business=business, membership=membership).select_related(
            "sale",
            "branch",
            "created_by__user",
            "posted_by__user",
            "cancelled_by__user",
            "refund",
            "receipt",
            "reversal",
        ),
        pk=return_id,
    )
    return render(
        request,
        "sales/return_detail.html",
        {
            "sale_return": sale_return,
            "lines": sale_return.lines.select_related("sale_line", "variant"),
            "can_view_sale_cost": membership.can_view_sale_cost,
            "can_manage_returns": membership.can_sell_across_branches,
        },
    )


@login_required
def sale_return_post(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_return_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale_return = get_object_or_404(
        _visible_returns(business=business, membership=membership),
        pk=return_id,
        status=SaleReturnStatus.DRAFT,
    )
    form = SaleReturnPostForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            posted_return = post_sale_return(
                actor=membership,
                sale_return=sale_return,
                refund_method=str(form.cleaned_data["refund_method"]),
                telebirr_reference=str(form.cleaned_data["telebirr_reference"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Sale return and refund evidence posted."))
            return redirect(
                "sales:return-receipt-detail",
                receipt_id=posted_return.receipt.id,
            )
    add_accessible_error_attributes(form)
    return render(
        request,
        "sales/return_post.html",
        {"sale_return": sale_return, "form": form},
    )


@login_required
def sale_return_cancel(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_sales(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale_return = get_object_or_404(
        _visible_returns(business=business, membership=membership),
        pk=return_id,
        status=SaleReturnStatus.DRAFT,
    )
    form = SaleReturnCancelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            cancel_sale_return(actor=membership, sale_return=sale_return)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Sale return draft cancelled."))
            return redirect("sales:sale-detail", sale_id=sale_return.sale_id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "sales/return_cancel.html",
        {"sale_return": sale_return, "form": form},
    )


@login_required
def sale_return_reverse(request: HttpRequest, return_id: UUID) -> HttpResponse:
    tenant_request = _require_return_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    sale_return = get_object_or_404(
        _visible_returns(business=business, membership=membership),
        pk=return_id,
        status=SaleReturnStatus.POSTED,
    )
    form = SaleReturnReversalForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            reverse_sale_return(
                actor=membership,
                sale_return=sale_return,
                reason=str(form.cleaned_data["reason"]),
                idempotency_key=cast(UUID, form.cleaned_data["idempotency_key"]),
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Sale return reversed."))
            return redirect("sales:return-detail", return_id=sale_return.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "sales/return_reverse.html",
        {"sale_return": sale_return, "form": form},
    )


@login_required
def return_receipt_detail(request: HttpRequest, receipt_id: UUID) -> HttpResponse:
    tenant_request = _require_return_manager(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    receipt = get_object_or_404(
        InternalReturnReceipt.objects.filter(
            business=business,
            sale_return__in=_visible_returns(
                business=business,
                membership=membership,
            ),
        ).select_related(
            "business",
            "branch",
            "sale_return",
            "sale_return__sale",
            "issued_by__user",
        ),
        pk=receipt_id,
    )
    return render(
        request,
        "sales/return_receipt_detail.html",
        {
            "receipt": receipt,
            "lines": receipt.sale_return.lines.all(),
        },
    )
