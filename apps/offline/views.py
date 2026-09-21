import json
from datetime import datetime
from typing import cast
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.catalog.models import ProductVariant
from apps.offline.models import OfflineSaleSyncStatus
from apps.offline.services import (
    OfflineSaleDraftInput,
    OfflineSaleLineInput,
    sync_offline_sale,
)
from apps.sales.forms import sale_branch_queryset
from apps.sales.models import SalePaymentMethod


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if tenant_request.active_business is None or membership is None or not membership.can_sell:
        raise PermissionDenied(_("Sales permission is required."))
    return tenant_request


def _selected_branch(
    *,
    request: HttpRequest,
    membership: BusinessMembership,
) -> tuple[Branch | None, list[Branch]]:
    branches = list(sale_branch_queryset(membership).order_by("name"))
    if not membership.can_sell_across_branches:
        return (branches[0] if len(branches) == 1 else None), branches
    raw_branch = request.GET.get("branch")
    if raw_branch:
        try:
            branch_id = UUID(raw_branch)
        except ValueError:
            branch_id = None
        if branch_id is not None:
            for branch in branches:
                if branch.id == branch_id:
                    return branch, branches
    return (branches[0] if len(branches) == 1 else None), branches


def _uuid(value: object, *, field: str) -> UUID:
    if not isinstance(value, str):
        raise ValidationError(_("%(field)s must be a UUID.") % {"field": field})
    try:
        return UUID(value)
    except ValueError as error:
        raise ValidationError(_("%(field)s must be a UUID.") % {"field": field}) from error


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(_("%(field)s must be text.") % {"field": field})
    return value


def _timestamp(value: object) -> datetime:
    raw_value = _text(value, field=_("Offline creation time"))
    try:
        return datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError(_("Enter a valid offline creation time.")) from error


def _line(value: object) -> OfflineSaleLineInput:
    if not isinstance(value, dict):
        raise ValidationError(_("Every offline sale line must be an object."))
    return OfflineSaleLineInput(
        variant_id=_uuid(value.get("variant_id"), field=_("Variant")),
        quantity=_text(value.get("quantity"), field=_("Quantity")),
        selling_price_snapshot=_text(
            value.get("selling_price"),
            field=_("Selling price"),
        ),
        product_name_snapshot=_text(
            value.get("product_name"),
            field=_("Product name"),
        ),
        variant_label_snapshot=_text(
            value.get("variant_label"),
            field=_("Variant label"),
        ),
    )


def _draft_input(payload: object) -> OfflineSaleDraftInput:
    if not isinstance(payload, dict):
        raise ValidationError(_("The offline sale payload must be an object."))
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise ValidationError(_("Offline sale lines must be a list."))
    return OfflineSaleDraftInput(
        local_draft_id=_uuid(payload.get("local_draft_id"), field=_("Local draft")),
        idempotency_key=_uuid(payload.get("idempotency_key"), field=_("Idempotency key")),
        business_id=_uuid(payload.get("business_id"), field=_("Business")),
        branch_id=_uuid(payload.get("branch_id"), field=_("Branch")),
        drafted_by_id=_uuid(payload.get("drafted_by_id"), field=_("Drafting membership")),
        role_at_draft=_text(payload.get("role_at_draft"), field=_("Membership role")),
        offline_created_at=_timestamp(payload.get("offline_created_at")),
        payment_method=_text(payload.get("payment_method"), field=_("Payment method")),
        telebirr_reference=_text(
            payload.get("telebirr_reference", ""),
            field=_("Telebirr reference"),
        ),
        lines=tuple(_line(line) for line in raw_lines),
    )


@login_required
@require_safe
def manifest(request: HttpRequest) -> JsonResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    return JsonResponse(
        {
            "name": "Ife Digital Solution",
            "short_name": "Ife",
            "start_url": (f"{reverse('offline:sales')}?business={business.id}"),
            "scope": "/offline/",
            "display": "standalone",
            "background_color": "#f7f4ee",
            "theme_color": "#173f35",
            "icons": [
                {
                    "src": "/static/img/ife-app-icon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        },
        content_type="application/manifest+json",
    )


@require_safe
def service_worker(request: HttpRequest) -> HttpResponse:
    response = render(
        request,
        "offline/service-worker.js",
        content_type="application/javascript",
    )
    response.headers["Service-Worker-Allowed"] = "/offline/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@require_safe
def unavailable(request: HttpRequest) -> HttpResponse:
    response = render(request, "offline/unavailable.html")
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["X-Ife-Offline-Cache"] = "public-shell"
    return response


@login_required
@require_safe
def offline_sales(request: HttpRequest) -> HttpResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    business = cast(Business, tenant_request.active_business)
    raw_business = request.GET.get("business")
    if raw_business is None:
        return redirect(f"{reverse('offline:sales')}?business={business.id}")
    try:
        submitted_business_id = UUID(raw_business)
    except ValueError as error:
        raise PermissionDenied(_("The offline business is unavailable.")) from error
    if submitted_business_id != business.id:
        raise PermissionDenied(_("The offline business is unavailable."))
    branch, branches = _selected_branch(request=request, membership=membership)
    response = render(
        request,
        "offline/sales.html",
        {
            "branch": branch,
            "branches": branches,
            "payment_methods": SalePaymentMethod.choices,
        },
    )
    response.headers["Cache-Control"] = "private, no-cache"
    response.headers["X-Ife-Offline-Cache"] = "private-shell"
    response.headers["X-Ife-Offline-Membership"] = str(membership.id)
    return response


@login_required
@require_safe
def catalog_snapshot(request: HttpRequest) -> JsonResponse:
    tenant_request = _tenant(request)
    business = cast(Business, tenant_request.active_business)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    branch, branch_options = _selected_branch(request=request, membership=membership)
    del branch_options
    if branch is None:
        return JsonResponse({"error": _("Select a branch first.")}, status=400)
    variants = (
        ProductVariant.objects.filter(
            business=business,
            is_active=True,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("product__name", "size", "color", "sku")
    )
    response = JsonResponse(
        {
            "business_id": str(business.id),
            "branch_id": str(branch.id),
            "branch_name": branch.name,
            "generated_at": timezone.now().isoformat(),
            "variants": [
                {
                    "id": str(variant.id),
                    "product_name": variant.product.name,
                    "label": str(variant),
                    "selling_price": format(variant.selling_price, ".2f"),
                    "stock_unit": variant.stock_unit,
                }
                for variant in variants
            ],
        }
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@login_required
@require_POST
def sync_sale(request: HttpRequest) -> JsonResponse:
    tenant_request = _tenant(request)
    membership = cast(BusinessMembership, tenant_request.active_membership)
    try:
        payload: object = json.loads(request.body)
        draft = _draft_input(payload)
        result = sync_offline_sale(actor=membership, draft=draft)
    except json.JSONDecodeError:
        return JsonResponse({"errors": [_("Enter valid JSON.")]}, status=400)
    except ValidationError as error:
        return JsonResponse({"errors": list(error.messages)}, status=400)
    sale_url = (
        reverse("sales:sale-detail", kwargs={"sale_id": result.sale_id}) if result.sale_id else None
    )
    return JsonResponse(
        {
            "status": result.status,
            "sale_id": str(result.sale_id) if result.sale_id else None,
            "sale_url": sale_url,
            "conflicts": result.conflict_messages,
            "message": _sync_message(result.status),
        }
    )


def _sync_message(status: str) -> str:
    if status == OfflineSaleSyncStatus.SYNCED:
        return _("The offline sale was synchronized as a server draft.")
    if status == OfflineSaleSyncStatus.NEEDS_REVIEW:
        return _("The server draft was created and needs catalog review before posting.")
    return _("The offline sale was rejected and remains on this device.")
