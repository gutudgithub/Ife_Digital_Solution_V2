from django.http import HttpRequest

from apps.businesses.models import Business, BusinessMembership


class TenantRequest(HttpRequest):
    active_business: Business | None
    active_membership: BusinessMembership | None
