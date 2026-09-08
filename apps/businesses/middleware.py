from collections.abc import Callable
from typing import cast

from django.http import HttpRequest, HttpResponse

from apps.businesses.models import BusinessMembership
from apps.businesses.types import TenantRequest


class ActiveBusinessMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        tenant_request = cast(TenantRequest, request)
        tenant_request.active_business = None
        tenant_request.active_membership = None

        if request.user.is_authenticated:
            memberships = BusinessMembership.objects.select_related("business").filter(
                user=request.user,
                is_active=True,
                business__is_active=True,
            )
            session_business_id = request.session.get("active_business_id")
            active_business_id = session_business_id if isinstance(session_business_id, str) else ""
            membership = (
                memberships.filter(business_id=active_business_id).first()
                if active_business_id
                else None
            )
            if membership is None:
                membership = memberships.first()
            if membership:
                tenant_request.active_membership = membership
                tenant_request.active_business = membership.business
                request.session["active_business_id"] = str(membership.business_id)

        return self.get_response(request)
