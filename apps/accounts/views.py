from io import BytesIO
from typing import cast
from urllib.parse import urlparse

import segno
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from apps.businesses.models import BusinessMembership
from apps.businesses.types import TenantRequest


class SecureLogoutView(LogoutView):
    def post(
        self,
        request: HttpRequest,
        *args: object,
        **kwargs: object,
    ) -> HttpResponse:
        response = super().post(request, *args, **kwargs)
        response.headers["Clear-Site-Data"] = '"cache"'
        return response


def staff_entry_url() -> str:
    origin = str(settings.PUBLIC_SITE_ORIGIN).rstrip("/")
    parsed = urlparse(origin)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(_("Configure the secure HTTPS public site origin first."))
    return f"{origin}{reverse('login')}"


def _staff_entry_manager(request: HttpRequest) -> BusinessMembership:
    tenant_request = cast(TenantRequest, request)
    membership = tenant_request.active_membership
    if (
        membership is None
        or not membership.is_active
        or not membership.business.is_active
        or not membership.can_manage_catalog
    ):
        raise PermissionDenied(_("Owner or manager permission is required."))
    return membership


@login_required
@require_safe
def staff_entry_qr(request: HttpRequest) -> HttpResponse:
    _staff_entry_manager(request)
    output = BytesIO()
    code = segno.make(staff_entry_url(), micro=False, error="m")
    code.save(
        output,
        kind="svg",
        scale=6,
        border=4,
        dark="#17365d",
        light="#ffffff",
        xmldecl=False,
    )
    response = HttpResponse(output.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "private, no-store"
    response["Content-Disposition"] = 'inline; filename="staff-browser-entry-qr.svg"'
    return response


@login_required
@require_safe
def staff_entry_poster(request: HttpRequest) -> HttpResponse:
    _staff_entry_manager(request)
    configuration_error: ValidationError | None
    try:
        login_url = staff_entry_url()
    except ValidationError as error:
        login_url = ""
        configuration_error = error
    else:
        configuration_error = None
    return render(
        request,
        "accounts/staff_entry_poster.html",
        {
            "login_url": login_url,
            "configuration_error": configuration_error,
        },
    )
