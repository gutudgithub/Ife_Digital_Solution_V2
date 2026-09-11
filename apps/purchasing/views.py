from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.businesses.models import Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.forms import add_accessible_error_attributes
from apps.purchasing.forms import (
    BasePurchaseLineFormSet,
    PurchaseForm,
    PurchaseLineFormSet,
    PurchaseReceiptForm,
    SupplierForm,
)
from apps.purchasing.models import Purchase, PurchaseStatus, Supplier
from apps.purchasing.services import (
    ReceiptQuantity,
    approve_purchase,
    cancel_purchase,
    new_purchase_number,
    purchase_line_progress,
    receive_purchase,
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
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    suppliers = Supplier.objects.filter(business=business)
    return render(
        request,
        "purchasing/supplier_list.html",
        {
            "suppliers": suppliers,
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
    return render(
        request,
        "purchasing/purchase_detail.html",
        {
            "purchase": purchase,
            "line_progress": purchase_line_progress(purchase),
            "receipts": purchase.receipts.select_related("received_by__user"),
            "can_manage_purchasing": membership.can_manage_purchasing,
            "can_receive_inventory": membership.can_receive_inventory,
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
