from typing import cast

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.businesses.types import TenantRequest
from apps.catalog.forms import ProductForm
from apps.catalog.models import Category, Product


@login_required
def product_list(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied("No active business membership is available.")

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
        raise PermissionDenied("Catalog management permission is required.")

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

    return render(request, "catalog/product_form.html", {"form": form})
