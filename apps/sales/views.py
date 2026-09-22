from datetime import date
from decimal import Decimal
from typing import cast
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q, QuerySet
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.cash.models import CashSession, CashSessionStatus
from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.forms import add_accessible_error_attributes
from apps.public_profiles.models import (
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
)
from apps.public_profiles.services import (
    return_receipt_public_url,
    sale_receipt_public_url,
)
from apps.sales.forms import (
    BaseSaleLineFormSet,
    BaseSaleReturnLineFormSet,
    BranchTelebirrProfileForm,
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
    BranchTelebirrProfile,
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
from apps.sales.telebirr import (
    activate_telebirr_profile,
    configure_telebirr_profile,
    deactivate_telebirr_profile,
    remove_telebirr_profile,
)
from config.rate_limit import rate_limit


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
    picker_products = (
        Product.objects.filter(
            business=business,
            is_active=True,
            variants__is_active=True,
        )
        .select_related("category")
        .prefetch_related(
            Prefetch(
                "variants",
                queryset=ProductVariant.objects.filter(is_active=True).order_by(
                    "size",
                    "color",
                    "sku",
                ),
            ),
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(removed_at__isnull=True),
                to_attr="current_images",
            ),
        )
        .distinct()
        .order_by("name")
    )
    return render(
        request,
        "sales/sale_form.html",
        {
            "form": form,
            "formset": formset,
            "sale": sale,
            "picker_products": picker_products,
        },
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
        _visible_sales(business=business, membership=membership).select_related(
            "branch",
            "created_by__user",
            "offline_sync__drafted_by__user",
        ),
        pk=sale_id,
        status=SaleStatus.DRAFT,
    )
    open_cash_session = CashSession.objects.filter(
        business=business,
        branch=sale.branch,
        status=CashSessionStatus.OPEN,
    ).first()
    cash_session_date_mismatch = (
        open_cash_session is not None and open_cash_session.business_date != sale.sale_date
    )
    form = SalePostForm(request.POST or None)
    telebirr_profile = BranchTelebirrProfile.objects.filter(
        business=business,
        branch=sale.branch,
        is_active=True,
        removed_at__isnull=True,
    ).first()
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
    return render(
        request,
        "sales/sale_post.html",
        {
            "sale": sale,
            "form": form,
            "open_cash_session": open_cash_session,
            "cash_session_date_mismatch": cash_session_date_mismatch,
            "telebirr_profile": telebirr_profile,
        },
    )


@login_required
@rate_limit(
    scope="telebirr-qr-upload",
    limit=settings.UPLOAD_RATE_LIMIT,
    window_seconds=settings.UPLOAD_RATE_LIMIT_WINDOW_SECONDS,
)
def telebirr_settings(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_manage_payment_qr:
        raise PermissionDenied(_("Only an owner can manage Telebirr merchant QR settings."))
    form = BranchTelebirrProfileForm(request.POST or None, request.FILES or None)
    form.scope_to_business(business)
    if request.method == "POST" and form.is_valid():
        branch = form.cleaned_data["branch"]
        upload = form.cleaned_data["qr_image"]
        if not isinstance(branch, Branch) or not isinstance(upload, UploadedFile):
            raise TypeError("Validated Telebirr form returned invalid values.")
        try:
            configure_telebirr_profile(
                actor=membership,
                branch=branch,
                merchant_display_name=str(form.cleaned_data["merchant_display_name"]),
                merchant_identifier=str(form.cleaned_data["merchant_identifier"]),
                upload=upload,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(
                request,
                _("Merchant QR saved inactive. Test-scan it before activation."),
            )
            return redirect("sales:telebirr-settings")
    add_accessible_error_attributes(form)
    profiles = BranchTelebirrProfile.objects.filter(
        business=business,
        removed_at__isnull=True,
    ).select_related("branch", "confirmed_by__user")
    return render(
        request,
        "sales/telebirr_settings.html",
        {"form": form, "profiles": profiles},
    )


def _telebirr_profile_for_owner(
    request: HttpRequest,
    profile_id: UUID,
) -> tuple[BusinessMembership, BranchTelebirrProfile]:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_manage_payment_qr:
        raise PermissionDenied(_("Only an owner can manage Telebirr merchant QR settings."))
    profile = get_object_or_404(
        BranchTelebirrProfile,
        pk=profile_id,
        business=membership.business,
        removed_at__isnull=True,
    )
    return membership, profile


@login_required
@require_POST
def telebirr_activate(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    membership, profile = _telebirr_profile_for_owner(request, profile_id)
    activate_telebirr_profile(actor=membership, profile=profile)
    messages.success(request, _("Telebirr merchant QR activated."))
    return redirect("sales:telebirr-settings")


@login_required
@require_POST
def telebirr_deactivate(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    membership, profile = _telebirr_profile_for_owner(request, profile_id)
    deactivate_telebirr_profile(actor=membership, profile=profile)
    messages.success(request, _("Telebirr merchant QR deactivated."))
    return redirect("sales:telebirr-settings")


@login_required
@require_POST
def telebirr_remove(request: HttpRequest, profile_id: UUID) -> HttpResponse:
    membership, profile = _telebirr_profile_for_owner(request, profile_id)
    remove_telebirr_profile(actor=membership, profile=profile)
    messages.success(request, _("Telebirr merchant QR removed."))
    return redirect("sales:telebirr-settings")


@login_required
@require_safe
def telebirr_qr(request: HttpRequest, profile_id: UUID) -> FileResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    if not membership.can_sell and not membership.can_manage_payment_qr:
        raise PermissionDenied(_("Sales permission is required."))
    profile = get_object_or_404(
        BranchTelebirrProfile,
        pk=profile_id,
        business=membership.business,
        removed_at__isnull=True,
    )
    try:
        response = FileResponse(profile.qr_source.open("rb"), content_type="image/png")
    except (FileNotFoundError, OSError):
        raise Http404 from None
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = 'inline; filename="telebirr-merchant-qr.png"'
    return response


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
    public_identity = PublicSaleReceiptIdentity.objects.filter(receipt=receipt).first()
    public_url = ""
    if public_identity is not None:
        try:
            public_url = sale_receipt_public_url(public_identity)
        except ValidationError:
            pass
    return render(
        request,
        "sales/receipt_detail.html",
        {
            "receipt": receipt,
            "lines": receipt.sale.lines.all(),
            "public_identity": public_identity,
            "public_url": public_url,
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
    public_identity = PublicReturnReceiptIdentity.objects.filter(receipt=receipt).first()
    public_url = ""
    if public_identity is not None:
        try:
            public_url = return_receipt_public_url(public_identity)
        except ValidationError:
            pass
    return render(
        request,
        "sales/return_receipt_detail.html",
        {
            "receipt": receipt,
            "lines": receipt.sale_return.lines.all(),
            "public_identity": public_identity,
            "public_url": public_url,
        },
    )
