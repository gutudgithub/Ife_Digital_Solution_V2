from typing import cast

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


@register(Tags.security, deploy=True)
def check_document_production_configuration(
    app_configs: object,
    **kwargs: object,
) -> list[Error | Warning]:
    del app_configs, kwargs
    messages: list[Error | Warning] = []
    storage_backend = settings.STORAGES["documents"]["BACKEND"]
    if storage_backend == "django.core.files.storage.FileSystemStorage":
        messages.append(
            Warning(
                "Document sources use local filesystem storage.",
                hint="Use private durable object storage before processing real documents.",
                id="documents.W001",
            )
        )
    elif storage_backend == "storages.backends.s3.S3Storage" and not cast(
        dict[str, object],
        settings.STORAGES["documents"]["OPTIONS"],
    ).get("bucket_name"):
        messages.append(
            Error(
                "The private document object-storage bucket is not configured.",
                hint="Set DOCUMENT_S3_BUCKET before processing real documents.",
                id="documents.E002",
            )
        )
    if settings.DOCUMENT_SCANNER_BACKEND.endswith("DevelopmentDocumentScanner"):
        messages.append(
            Error(
                "The deterministic development document scanner is configured.",
                hint="Configure ClamAVDocumentScanner before processing real documents.",
                id="documents.E001",
            )
        )
    if not settings.DOCUMENT_CONFIRMED_RETENTION_POLICY_APPROVED:
        messages.append(
            Warning(
                "Confirmed-document retention policy approval is not recorded.",
                hint="Approve retention, deletion, export, and backup policies before pilot use.",
                id="documents.W002",
            )
        )
    return messages
