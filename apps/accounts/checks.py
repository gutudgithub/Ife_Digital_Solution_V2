from typing import cast

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register
from django.core.exceptions import ValidationError

from apps.accounts.readiness import load_recovery_attestation
from apps.accounts.views import staff_entry_url


@register(Tags.security, deploy=True)
def stage12_deployment_checks(
    app_configs: object,
    **kwargs: object,
) -> list[Error | Warning]:
    del app_configs, kwargs
    messages: list[Error | Warning] = []
    try:
        staff_entry_url()
    except ValidationError:
        messages.append(
            Error(
                "PUBLIC_SITE_ORIGIN must be a safe HTTPS origin for staff-entry QR.",
                id="stage12.E001",
            )
        )
    if "*" in settings.ALLOWED_HOSTS or not settings.ALLOWED_HOSTS:
        messages.append(
            Error(
                "Use an explicit production host allowlist.",
                id="stage12.E002",
            )
        )
    if not settings.SESSION_COOKIE_SECURE or not settings.CSRF_COOKIE_SECURE:
        messages.append(
            Error(
                "Secure session and CSRF cookies are required.",
                id="stage12.E003",
            )
        )
    if not settings.CONTENT_SECURITY_POLICY:
        messages.append(Error("Content Security Policy is not configured.", id="stage12.E004"))
    if not settings.RATE_LIMIT_BACKEND_APPROVED:
        messages.append(
            Error(
                "Production cross-instance rate limiting is not approved.",
                id="stage12.E005",
            )
        )
    cache_backend = str(settings.CACHES[settings.RATE_LIMIT_CACHE_ALIAS]["BACKEND"])
    if cache_backend.endswith(("LocMemCache", "DummyCache")):
        messages.append(
            Error(
                "The rate-limit cache is not shared across production instances.",
                id="stage12.E006",
            )
        )
    storage = settings.STORAGES["catalog_media"]
    if storage["BACKEND"] == "django.core.files.storage.FileSystemStorage":
        messages.append(
            Error(
                "Catalog media uses local filesystem storage.",
                id="stage12.E007",
            )
        )
    elif storage["BACKEND"] == "storages.backends.s3.S3Storage":
        options = cast(dict[str, object], storage["OPTIONS"])
        if (
            not options.get("bucket_name")
            or options.get("querystring_auth") is not True
            or options.get("default_acl") is not None
        ):
            messages.append(
                Error(
                    "Catalog media object storage is not private and complete.",
                    id="stage12.E008",
                )
            )
    try:
        attestation = load_recovery_attestation()
    except ValidationError:
        messages.append(
            Error(
                "Database and object-storage restore evidence is not recorded.",
                id="stage12.E009",
            )
        )
    else:
        if attestation.measured_rpo_hours > settings.STAGE12_MAX_RPO_HOURS:
            messages.append(
                Warning(
                    "Measured RPO exceeds the recommended pilot objective.",
                    id="stage12.W001",
                )
            )
        if attestation.measured_rto_hours > settings.STAGE12_MAX_RTO_HOURS:
            messages.append(
                Warning(
                    "Measured RTO exceeds the recommended pilot objective.",
                    id="stage12.W002",
                )
            )
    if not settings.STAGE12_SECURITY_REVIEW_APPROVED:
        messages.append(Error("Independent security review is not approved.", id="stage12.E010"))
    if not settings.STAGE12_PRIVACY_LEGAL_APPROVED:
        messages.append(Error("Privacy and legal review is not approved.", id="stage12.E011"))
    if not settings.STAGE12_INCIDENT_RESPONSE_APPROVED:
        messages.append(
            Error("Incident response and rollback are not approved.", id="stage12.E012")
        )
    if not settings.STAGE12_OPERATOR_TRAINING_APPROVED:
        messages.append(Error("Pilot operator training is not approved.", id="stage12.E013"))
    if not settings.STAGE12_TRANSLATION_REVIEW_APPROVED:
        messages.append(Error("Native-language review is not approved.", id="stage12.E014"))
    return messages
