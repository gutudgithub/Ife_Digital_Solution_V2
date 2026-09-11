from decimal import Decimal
from typing import cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.businesses.models import Business, BusinessMembership, MembershipRole
from apps.businesses.types import TenantRequest
from apps.forms import add_accessible_error_attributes
from apps.inventory.forms import InventoryAdjustmentForm, OpeningBalanceForm
from apps.inventory.models import InventoryBalance, InventoryMovement
from apps.inventory.services import post_inventory_adjustment, post_opening_balance


def _tenant(request: HttpRequest) -> TenantRequest:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    return tenant_request


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
    if membership.role in {
        MembershipRole.CASHIER.value,
        MembershipRole.STOCK_EMPLOYEE.value,
    }:
        if membership.assigned_branch_id is not None:
            balances = balances.filter(branch=membership.assigned_branch)
            movements = movements.filter(branch=membership.assigned_branch)
        else:
            active_branches = list(business.branches.filter(is_active=True)[:2])
            if len(active_branches) == 1:
                balances = balances.filter(branch=active_branches[0])
                movements = movements.filter(branch=active_branches[0])
            else:
                balances = balances.none()
                movements = movements.none()
    if not can_view_movement_history:
        balances = balances.filter(branch__is_active=True, variant__is_active=True)
        movements = movements.none()
    page_obj = Paginator(balances, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "inventory/inventory_list.html",
        {
            "balances": page_obj.object_list,
            "movements": movements[:50],
            "can_manage_inventory": membership.can_manage_inventory,
            "can_view_inventory_cost": membership.can_view_inventory_cost,
            "can_view_movement_history": can_view_movement_history,
            "page_obj": page_obj,
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
