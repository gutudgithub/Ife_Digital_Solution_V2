from typing import cast
from uuid import UUID

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.businesses.types import TenantRequest
from apps.catalog.forms import ProductForm, ProductVariantForm
from apps.catalog.models import Category, Product, ProductVariant
from apps.forms import add_accessible_error_attributes
from apps.inventory.models import InventoryMovement
from apps.purchasing.models import PurchaseLine, PurchaseStatus


@login_required
def product_list(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))

    products = (
        Product.objects.filter(business=tenant_request.active_business)
        .select_related("category")
        .annotate(variant_count=Count("variants"))
    )
    return render(
        request,
        "catalog/product_list.html",
        {
            "products": products,
            "can_manage_catalog": tenant_request.active_membership.can_manage_catalog,
        },
    )


@login_required
def product_create(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None or not membership.can_manage_catalog:
        raise PermissionDenied(_("Catalog management permission is required."))

    form = ProductForm(request.POST or None)
    form.instance.business = business
    category_field = form.fields["category"]
    if not isinstance(category_field, forms.ModelChoiceField):
        raise TypeError("Product category must be a model choice field.")
    category_field.queryset = Category.objects.filter(business=business, is_active=True)

    if request.method == "POST" and form.is_valid():
        product = form.save(commit=False)
        product.business = business
        product.save()
        messages.success(request, _("Product created."))
        return redirect("catalog:product-list")

    add_accessible_error_attributes(form)
    return render(request, "catalog/product_form.html", {"form": form})


@login_required
def variant_list(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None:
        raise PermissionDenied(_("No active business membership is available."))
    variants = ProductVariant.objects.filter(business=business).select_related("product")
    return render(
        request,
        "catalog/variant_list.html",
        {
            "variants": variants,
            "can_manage_catalog": membership.can_manage_catalog,
            "can_view_inventory_cost": membership.can_view_inventory_cost,
        },
    )


def _variant_form_response(
    request: HttpRequest,
    *,
    variant: ProductVariant,
    stock_unit_locked: bool,
) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    business = tenant_request.active_business
    if membership is None or business is None or not membership.can_manage_catalog:
        raise PermissionDenied(_("Catalog management permission is required."))
    form = ProductVariantForm(request.POST or None, instance=variant)
    form.scope_to_business(business, stock_unit_locked=stock_unit_locked)
    if request.method == "POST" and form.is_valid():
        saved_variant = form.save(commit=False)
        saved_variant.business = business
        saved_variant.full_clean()
        saved_variant.save()
        messages.success(request, _("Product variant saved."))
        return redirect("catalog:variant-list")
    add_accessible_error_attributes(form)
    return render(
        request,
        "catalog/variant_form.html",
        {"form": form, "variant": variant},
    )


@login_required
def variant_create(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None:
        raise PermissionDenied(_("No active business membership is available."))
    return _variant_form_response(
        request,
        variant=ProductVariant(business=tenant_request.active_business),
        stock_unit_locked=False,
    )


@login_required
def variant_edit(request: HttpRequest, variant_id: UUID) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    business = tenant_request.active_business
    if business is None:
        raise PermissionDenied(_("No active business membership is available."))
    variant = get_object_or_404(ProductVariant, pk=variant_id, business=business)
    stock_unit_locked = (
        InventoryMovement.objects.filter(business=business, variant=variant).exists()
        or PurchaseLine.objects.filter(
            business=business,
            variant=variant,
            purchase__status__in=(
                PurchaseStatus.APPROVED,
                PurchaseStatus.PARTIALLY_RECEIVED,
            ),
        ).exists()
    )
    return _variant_form_response(
        request,
        variant=variant,
        stock_unit_locked=stock_unit_locked,
    )
