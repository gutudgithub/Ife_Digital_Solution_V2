from django.contrib import admin

from apps.public_profiles.models import (
    PublicBusinessProfile,
    PublicContactLink,
    PublicOpeningHour,
    PublicProductIdentity,
    PublicProfileEvent,
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
    PublicStorefrontDailyMetric,
    PublicVerificationDecision,
    PublicVerificationRequest,
)


@admin.register(PublicBusinessProfile)
class PublicBusinessProfileAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "business",
        "publication_status",
        "is_suspended",
        "allow_search_indexing",
    )
    list_filter = ("publication_status", "is_suspended", "allow_search_indexing")
    search_fields = ("display_name", "business__name", "public_id")


for model in (
    PublicContactLink,
    PublicOpeningHour,
    PublicProductIdentity,
    PublicProfileEvent,
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
    PublicStorefrontDailyMetric,
    PublicVerificationDecision,
    PublicVerificationRequest,
):
    admin.site.register(model)
