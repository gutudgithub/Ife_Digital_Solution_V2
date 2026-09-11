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
    ReceiptLookupForm,
    SaleCancelForm,
    SaleFilterForm,
    SaleForm,
    SaleLineFormSet,
    SalePostForm,
)
from apps.sales.models import InternalReceipt, Sale, SaleStatus
from apps.sales.services import SaleQuantity, cancel_sale, post_sale, save_sale_draft


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
    if membership.can_view_sale_cost:
        return sales
    if membership.assigned_branch_id:
        return sales.filter(branch_id=membership.assigned_branch_id)
    active_branch_ids = list(
        business.branches.filter(is_active=True).values_list("id", flat=True)[:2]
    )
    if len(active_branch_ids) == 1:
        return sales.filter(branch_id=active_branch_ids[0])
    return sales.none()


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
