import uuid

from django.core.files.storage import Storage, storages


def catalog_media_storage() -> Storage:
    return storages["catalog_media"]


def product_image_upload_path(instance: object, filename: str) -> str:
    del instance, filename
    return f"products/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.webp"


def telebirr_qr_upload_path(instance: object, filename: str) -> str:
    del instance, filename
    return f"telebirr/{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}.png"
