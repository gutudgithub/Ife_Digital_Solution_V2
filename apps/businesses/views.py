from typing import cast

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.businesses.types import TenantRequest
from apps.catalog.models import Product, ProductVariant


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied("No active business membership is available.")

    business = tenant_request.active_business
    context = {
        "product_count": Product.objects.filter(business=business, is_active=True).count(),
        "variant_count": ProductVariant.objects.filter(
            business=business,
            is_active=True,
            product__is_active=True,
        ).count(),
    }
    return render(request, "businesses/dashboard.html", context)
