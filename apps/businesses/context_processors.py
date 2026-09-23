from typing import cast

from django.http import HttpRequest

from apps.businesses.types import TenantRequest


def active_business(request: HttpRequest) -> dict[str, object]:
    tenant_request = cast(TenantRequest, request)
    return {
        "active_business": tenant_request.active_business,
        "active_membership": tenant_request.active_membership,
    }
