import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class PreparedMediaImage:
    content: bytes
    media_type: str
    width: int
    height: int
    sha256: str
    filename: str

    @property
    def size(self) -> int:
        return len(self.content)


ALLOWED_IMAGE_EXTENSIONS = {
    "JPEG": {".jpg", ".jpeg"},
    "PNG": {".png"},
    "WEBP": {".webp"},
}


def _upload_bytes(upload: UploadedFile, *, max_bytes: int) -> bytes:
    if upload.size is None or upload.size <= 0:
        raise ValidationError(_("Empty images cannot be uploaded."))
    if upload.size > max_bytes:
        raise ValidationError(_("The image file is larger than the allowed limit."))
    content = bytearray()
    for chunk in upload.chunks():
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ValidationError(_("The image file is larger than the allowed limit."))
    return bytes(content)


def _decoded_image(
    upload: UploadedFile,
    *,
    max_bytes: int,
    max_pixels: int,
) -> Image.Image:
    raw = _upload_bytes(upload, max_bytes=max_bytes)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            probe = Image.open(BytesIO(raw))
            source_format = probe.format or ""
            suffix = Path(upload.name or "").suffix.lower()
            if source_format not in ALLOWED_IMAGE_EXTENSIONS:
                raise ValidationError(_("Upload a valid JPEG, PNG, or WebP image."))
            if suffix not in ALLOWED_IMAGE_EXTENSIONS[source_format]:
                raise ValidationError(_("The filename extension does not match the image content."))
            width, height = probe.size
            if width < 1 or height < 1 or width * height > max_pixels:
                raise ValidationError(_("The image dimensions are not allowed."))
            try:
                probe.seek(1)
            except EOFError:
                pass
            else:
                raise ValidationError(_("Animated images are not supported."))
            probe.verify()
            decoded = Image.open(BytesIO(raw))
            decoded.load()
    except ValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError(_("The image dimensions are not allowed.")) from exc
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValidationError(_("Upload a valid, readable image.")) from exc
    return ImageOps.exif_transpose(decoded)


def prepare_product_image(upload: UploadedFile) -> PreparedMediaImage:
    image = _decoded_image(
        upload,
        max_bytes=settings.PRODUCT_IMAGE_MAX_FILE_BYTES,
        max_pixels=settings.PRODUCT_IMAGE_MAX_PIXELS,
    )
    image.thumbnail(
        (settings.PRODUCT_IMAGE_MAX_EDGE, settings.PRODUCT_IMAGE_MAX_EDGE),
        Image.Resampling.LANCZOS,
    )
    output_mode = "RGBA" if "A" in image.getbands() else "RGB"
    normalized = image.convert(output_mode)
    output = BytesIO()
    normalized.save(output, format="WEBP", quality=84, method=6)
    content = output.getvalue()
    return PreparedMediaImage(
        content=content,
        media_type="image/webp",
        width=normalized.width,
        height=normalized.height,
        sha256=hashlib.sha256(content).hexdigest(),
        filename="product.webp",
    )


def prepare_telebirr_qr(upload: UploadedFile) -> PreparedMediaImage:
    image = _decoded_image(
        upload,
        max_bytes=settings.TELEBIRR_QR_MAX_FILE_BYTES,
        max_pixels=settings.TELEBIRR_QR_MAX_PIXELS,
    )
    if image.width > settings.TELEBIRR_QR_MAX_EDGE or image.height > settings.TELEBIRR_QR_MAX_EDGE:
        raise ValidationError(_("The QR image dimensions are larger than the allowed limit."))
    normalized = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    grayscale = normalized.convert("L")
    minimum, maximum = cast(tuple[int, int], grayscale.getextrema())
    if maximum - minimum < 80:
        raise ValidationError(_("The QR image does not have enough contrast."))
    output = BytesIO()
    normalized.save(output, format="PNG", optimize=True)
    content = output.getvalue()
    return PreparedMediaImage(
        content=content,
        media_type="image/png",
        width=normalized.width,
        height=normalized.height,
        sha256=hashlib.sha256(content).hexdigest(),
        filename="merchant-qr.png",
    )
