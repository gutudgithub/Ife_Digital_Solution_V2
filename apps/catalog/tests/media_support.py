from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, PngImagePlugin

from apps.catalog.models import ProductImage
from apps.sales.models import BranchTelebirrProfile


def image_upload(
    *,
    name: str = "product.png",
    color: str = "navy",
    metadata: bool = False,
) -> SimpleUploadedFile:
    output = BytesIO()
    image = Image.new("RGB", (80, 60), color)
    pnginfo = None
    if metadata:
        pnginfo = PngImagePlugin.PngInfo()
        pnginfo.add_text("Comment", "private metadata")
    image.save(output, "PNG", pnginfo=pnginfo)
    return SimpleUploadedFile(name, output.getvalue(), content_type="image/png")


def qr_upload() -> SimpleUploadedFile:
    output = BytesIO()
    image = Image.new("1", (240, 240), 1)
    for index in range(20, 220, 20):
        for x in range(index, min(index + 10, 220)):
            for y in range(20, 220):
                if (x + y) % 37 < 18:
                    image.putpixel((x, y), 0)
    image.save(output, "PNG")
    return SimpleUploadedFile("merchant.png", output.getvalue(), content_type="image/png")


class IsolatedCatalogMediaMixin:
    media_tempdir: TemporaryDirectory[str]

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.media_tempdir = TemporaryDirectory()
        self.addCleanup(self.media_tempdir.cleanup)  # type: ignore[attr-defined]
        storage = FileSystemStorage(location=self.media_tempdir.name, base_url=None)
        product_field = ProductImage._meta.get_field("source")
        telebirr_field = BranchTelebirrProfile._meta.get_field("qr_source")
        product_storage_patcher = patch.object(product_field, "storage", storage)
        telebirr_storage_patcher = patch.object(telebirr_field, "storage", storage)
        catalog_service_patcher = patch(
            "apps.catalog.services.catalog_media_storage",
            return_value=storage,
        )
        telebirr_service_patcher = patch(
            "apps.sales.telebirr.catalog_media_storage",
            return_value=storage,
        )
        product_storage_patcher.start()
        telebirr_storage_patcher.start()
        catalog_service_patcher.start()
        telebirr_service_patcher.start()
        self.addCleanup(product_storage_patcher.stop)  # type: ignore[attr-defined]
        self.addCleanup(telebirr_storage_patcher.stop)  # type: ignore[attr-defined]
        self.addCleanup(catalog_service_patcher.stop)  # type: ignore[attr-defined]
        self.addCleanup(telebirr_service_patcher.stop)  # type: ignore[attr-defined]
