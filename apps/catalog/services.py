import hashlib
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import BusinessMembership
from apps.catalog.media import prepare_product_image
from apps.catalog.models import (
    Product,
    ProductImage,
    ProductImageAction,
    ProductImageEvent,
)
from apps.catalog.storage import catalog_media_storage


@dataclass(frozen=True)
class CatalogMediaReconciliation:
    checked: int
    missing: tuple[str, ...]
    orphaned: tuple[str, ...]
    hash_mismatches: tuple[str, ...]
    listing_failed: bool

    @property
    def issue_count(self) -> int:
        return (
            len(self.missing)
            + len(self.orphaned)
            + len(self.hash_mismatches)
            + int(self.listing_failed)
        )


def _require_catalog_manager(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active or not actor.can_manage_catalog:
        raise PermissionDenied(_("Owner or manager catalog permission is required."))


def _validate_product(actor: BusinessMembership, product: Product) -> None:
    _require_catalog_manager(actor)
    if product.business_id != actor.business_id:
        raise PermissionDenied(_("Select a product in this business."))


def _record_image_event(
    *,
    actor: BusinessMembership,
    product: Product,
    image: ProductImage | None,
    action: str,
    previous_sha256: str = "",
    resulting_sha256: str = "",
) -> ProductImageEvent:
    return ProductImageEvent.objects.create(
        business=actor.business,
        product=product,
        image=image,
        actor=actor,
        action=action,
        previous_sha256=previous_sha256,
        resulting_sha256=resulting_sha256,
    )


def set_product_image(
    *,
    actor: BusinessMembership,
    product: Product,
    upload: UploadedFile,
    alt_text: str,
) -> ProductImage:
    _validate_product(actor, product)
    prepared = prepare_product_image(upload)
    clean_alt_text = alt_text.strip()
    if not clean_alt_text:
        raise ValidationError(_("Describe the product image."))
    stored_name = ""
    old_storage_name = ""
    try:
        with transaction.atomic():
            locked_product = Product.objects.select_for_update().get(
                pk=product.pk,
                business=actor.business,
            )
            current = (
                ProductImage.objects.select_for_update()
                .filter(product=locked_product, removed_at__isnull=True)
                .first()
            )
            now = timezone.now()
            action = ProductImageAction.ADDED
            previous_sha256 = ""
            if current is not None:
                action = ProductImageAction.REPLACED
                previous_sha256 = current.sha256
                old_storage_name = current.source.name
                current.removed_by = actor
                current.removed_at = now
                current.full_clean()
                current.save(update_fields=("removed_by", "removed_at"))
            image = ProductImage(
                business=actor.business,
                product=locked_product,
                media_type=prepared.media_type,
                width=prepared.width,
                height=prepared.height,
                size=prepared.size,
                sha256=prepared.sha256,
                alt_text=clean_alt_text[:240],
                uploaded_by=actor,
            )
            image.source.save(
                prepared.filename,
                ContentFile(prepared.content),
                save=False,
            )
            stored_name = image.source.name
            image.full_clean()
            image.save()
            _record_image_event(
                actor=actor,
                product=locked_product,
                image=image,
                action=action,
                previous_sha256=previous_sha256,
                resulting_sha256=image.sha256,
            )
            if old_storage_name:
                transaction.on_commit(lambda: catalog_media_storage().delete(old_storage_name))
            return image
    except Exception:
        if stored_name:
            catalog_media_storage().delete(stored_name)
        raise


def remove_product_image(
    *,
    actor: BusinessMembership,
    product: Product,
) -> ProductImage:
    _validate_product(actor, product)
    with transaction.atomic():
        current = ProductImage.objects.select_for_update().get(
            product=product,
            business=actor.business,
            removed_at__isnull=True,
        )
        storage_name = current.source.name
        current.removed_by = actor
        current.removed_at = timezone.now()
        current.full_clean()
        current.save(update_fields=("removed_by", "removed_at"))
        _record_image_event(
            actor=actor,
            product=product,
            image=current,
            action=ProductImageAction.REMOVED,
            previous_sha256=current.sha256,
        )
        transaction.on_commit(lambda: catalog_media_storage().delete(storage_name))
        return current


def _storage_paths(prefix: str = "") -> list[str]:
    storage = catalog_media_storage()
    directories, files = storage.listdir(prefix)
    paths = [f"{prefix}/{name}".lstrip("/") for name in files]
    for directory in directories:
        nested = f"{prefix}/{directory}".lstrip("/")
        paths.extend(_storage_paths(nested))
    return paths


def _file_hash(name: str) -> str:
    digest = hashlib.sha256()
    with catalog_media_storage().open(name, "rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reconcile_catalog_media() -> CatalogMediaReconciliation:
    from apps.sales.models import BranchTelebirrProfile

    known: dict[str, str] = {
        image.source.name: image.sha256
        for image in ProductImage.objects.filter(removed_at__isnull=True)
        if image.source.name
    }
    known.update(
        {
            profile.qr_source.name: profile.qr_sha256
            for profile in BranchTelebirrProfile.objects.filter(removed_at__isnull=True)
            if profile.qr_source.name
        }
    )
    storage = catalog_media_storage()
    missing = tuple(sorted(name for name in known if not storage.exists(name)))
    hash_mismatches = tuple(
        sorted(
            name
            for name, expected_hash in known.items()
            if storage.exists(name) and _file_hash(name) != expected_hash
        )
    )
    try:
        stored = set(_storage_paths())
    except (NotImplementedError, OSError):
        return CatalogMediaReconciliation(
            checked=len(known),
            missing=missing,
            orphaned=(),
            hash_mismatches=hash_mismatches,
            listing_failed=True,
        )
    return CatalogMediaReconciliation(
        checked=len(known),
        missing=missing,
        orphaned=tuple(sorted(stored - set(known))),
        hash_mismatches=hash_mismatches,
        listing_failed=False,
    )


def purge_orphaned_catalog_media(
    *,
    names: tuple[str, ...],
    dry_run: bool,
) -> tuple[str, ...]:
    removed: list[str] = []
    storage = catalog_media_storage()
    for name in names:
        if not name.startswith(("products/", "telebirr/")):
            raise ValidationError(_("Refusing to purge an unexpected media path."))
        if not dry_run and storage.exists(name):
            storage.delete(name)
        removed.append(name)
    return tuple(removed)
