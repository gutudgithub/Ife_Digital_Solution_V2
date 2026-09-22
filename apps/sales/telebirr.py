from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import Branch, BusinessMembership
from apps.catalog.media import prepare_telebirr_qr
from apps.catalog.storage import catalog_media_storage
from apps.sales.models import (
    BranchTelebirrEvent,
    BranchTelebirrProfile,
    TelebirrProfileAction,
)


def _require_owner(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active or not actor.can_manage_payment_qr:
        raise PermissionDenied(_("Only an active owner can manage Telebirr merchant QR settings."))


def _validate_branch(actor: BusinessMembership, branch: Branch) -> None:
    _require_owner(actor)
    if branch.business_id != actor.business_id or not branch.is_active:
        raise PermissionDenied(_("Select an active branch in this business."))


def _event(
    *,
    actor: BusinessMembership,
    profile: BranchTelebirrProfile,
    action: str,
    previous_sha256: str = "",
    resulting_sha256: str = "",
) -> BranchTelebirrEvent:
    return BranchTelebirrEvent.objects.create(
        business=actor.business,
        branch=profile.branch,
        profile=profile,
        actor=actor,
        action=action,
        previous_sha256=previous_sha256,
        resulting_sha256=resulting_sha256,
        merchant_display_name=profile.merchant_display_name,
        merchant_identifier=profile.merchant_identifier,
    )


def configure_telebirr_profile(
    *,
    actor: BusinessMembership,
    branch: Branch,
    merchant_display_name: str,
    merchant_identifier: str,
    upload: UploadedFile,
) -> BranchTelebirrProfile:
    _validate_branch(actor, branch)
    name = merchant_display_name.strip()
    identifier = merchant_identifier.strip()
    if not name or not identifier:
        raise ValidationError(_("Enter the Telebirr merchant name and identifier."))
    prepared = prepare_telebirr_qr(upload)
    stored_name = ""
    old_storage_name = ""
    try:
        with transaction.atomic():
            profile = (
                BranchTelebirrProfile.objects.select_for_update()
                .filter(business=actor.business, branch=branch)
                .first()
            )
            previous_sha256 = ""
            action = TelebirrProfileAction.CREATED
            if profile is None:
                profile = BranchTelebirrProfile(
                    business=actor.business,
                    branch=branch,
                )
            else:
                action = TelebirrProfileAction.REPLACED
                previous_sha256 = profile.qr_sha256
                old_storage_name = profile.qr_source.name
            profile.merchant_display_name = name
            profile.merchant_identifier = identifier
            profile.qr_media_type = prepared.media_type
            profile.qr_width = prepared.width
            profile.qr_height = prepared.height
            profile.qr_size = prepared.size
            profile.qr_sha256 = prepared.sha256
            profile.is_active = False
            profile.confirmed_by = None
            profile.confirmed_at = None
            profile.removed_by = None
            profile.removed_at = None
            profile.qr_source.save(
                prepared.filename,
                ContentFile(prepared.content),
                save=False,
            )
            stored_name = profile.qr_source.name
            profile.full_clean()
            profile.save()
            _event(
                actor=actor,
                profile=profile,
                action=action,
                previous_sha256=previous_sha256,
                resulting_sha256=profile.qr_sha256,
            )
            if old_storage_name:
                transaction.on_commit(lambda: catalog_media_storage().delete(old_storage_name))
            return profile
    except Exception:
        if stored_name:
            catalog_media_storage().delete(stored_name)
        raise


def activate_telebirr_profile(
    *,
    actor: BusinessMembership,
    profile: BranchTelebirrProfile,
) -> BranchTelebirrProfile:
    _require_owner(actor)
    with transaction.atomic():
        locked = BranchTelebirrProfile.objects.select_for_update().get(
            pk=profile.pk,
            business=actor.business,
            removed_at__isnull=True,
        )
        if not locked.qr_source.name:
            raise ValidationError(_("Upload a Telebirr merchant QR before activation."))
        locked.is_active = True
        locked.confirmed_by = actor
        locked.confirmed_at = timezone.now()
        locked.full_clean()
        locked.save(update_fields=("is_active", "confirmed_by", "confirmed_at", "updated_at"))
        _event(actor=actor, profile=locked, action=TelebirrProfileAction.ACTIVATED)
        return locked


def deactivate_telebirr_profile(
    *,
    actor: BusinessMembership,
    profile: BranchTelebirrProfile,
) -> BranchTelebirrProfile:
    _require_owner(actor)
    with transaction.atomic():
        locked = BranchTelebirrProfile.objects.select_for_update().get(
            pk=profile.pk,
            business=actor.business,
            removed_at__isnull=True,
        )
        locked.is_active = False
        locked.full_clean()
        locked.save(update_fields=("is_active", "updated_at"))
        _event(actor=actor, profile=locked, action=TelebirrProfileAction.DEACTIVATED)
        return locked


def remove_telebirr_profile(
    *,
    actor: BusinessMembership,
    profile: BranchTelebirrProfile,
) -> BranchTelebirrProfile:
    _require_owner(actor)
    with transaction.atomic():
        locked = BranchTelebirrProfile.objects.select_for_update().get(
            pk=profile.pk,
            business=actor.business,
            removed_at__isnull=True,
        )
        storage_name = locked.qr_source.name
        locked.is_active = False
        locked.removed_by = actor
        locked.removed_at = timezone.now()
        locked.full_clean()
        locked.save(update_fields=("is_active", "removed_by", "removed_at", "updated_at"))
        _event(
            actor=actor,
            profile=locked,
            action=TelebirrProfileAction.REMOVED,
            previous_sha256=locked.qr_sha256,
        )
        transaction.on_commit(lambda: catalog_media_storage().delete(storage_name))
        return locked
