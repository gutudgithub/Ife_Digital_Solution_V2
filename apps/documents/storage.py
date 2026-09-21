import uuid
from pathlib import Path

from django.core.files.storage import Storage, storages


def document_storage() -> Storage:
    return storages["documents"]


def document_upload_path(instance: object, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return f"documents/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}{suffix}"
