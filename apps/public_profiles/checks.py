from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.core.exceptions import ValidationError
from django.db import OperationalError, ProgrammingError

from apps.public_profiles.models import (
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
)
from apps.public_profiles.services import public_origin
from apps.sales.models import InternalReceipt, InternalReturnReceipt


@register(Tags.security, deploy=True)
def public_profile_deployment_checks(
    app_configs: object,
    **kwargs: object,
) -> list[Error | Warning]:
    messages: list[Error | Warning] = []
    try:
        public_origin()
    except ValidationError:
        messages.append(
            Error(
                "PUBLIC_SITE_ORIGIN must be a safe HTTPS origin in production.",
                id="public_profiles.E001",
            )
        )
    if not settings.PUBLIC_SUPPORT_URL:
        messages.append(
            Warning(
                "PUBLIC_SUPPORT_URL is not configured.",
                id="public_profiles.W001",
            )
        )
    try:
        missing_sales = InternalReceipt.objects.exclude(
            id__in=PublicSaleReceiptIdentity.objects.values("receipt_id")
        ).exists()
        missing_returns = InternalReturnReceipt.objects.exclude(
            id__in=PublicReturnReceiptIdentity.objects.values("receipt_id")
        ).exists()
    except (OperationalError, ProgrammingError):
        return messages
    if missing_sales or missing_returns:
        messages.append(
            Error(
                "Run backfill_public_receipt_identities before deployment.",
                id="public_profiles.E002",
            )
        )
    return messages
