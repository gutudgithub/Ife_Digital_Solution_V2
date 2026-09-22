import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlparse
from uuid import UUID

import segno
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch, QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import User
from apps.businesses.models import BusinessMembership
from apps.catalog.models import Product, ProductImage, ProductVariant
from apps.public_profiles.models import (
    ActorKind,
    MetricSource,
    PublicationStatus,
    PublicBusinessProfile,
    PublicContactLink,
    PublicOpeningHour,
    PublicProductIdentity,
    PublicProfileAction,
    PublicProfileEvent,
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
    PublicStorefrontDailyMetric,
    PublicVerificationDecision,
    PublicVerificationRequest,
    VerificationDecisionAction,
    VerificationRequestStatus,
    VerificationType,
    verification_is_current,
)
from apps.sales.models import (
    InternalReceipt,
    InternalReturnReceipt,
    SaleReturnPurpose,
    SaleReturnStatus,
)


@dataclass(frozen=True)
class ProfileDraftData:
    display_name: str
    description: str
    phone: str
    email: str
    website: str
    address: str
    map_url: str
    supported_languages: tuple[str, ...]


@dataclass(frozen=True)
class OpeningHourData:
    weekday: int
    is_closed: bool
    opens_at: time | None
    closes_at: time | None


@dataclass(frozen=True)
class ContactLinkData:
    link_type: str
    label: str
    url: str
    display_order: int
    is_active: bool


@dataclass(frozen=True)
class PublicOpeningHourView:
    weekday: str
    is_closed: bool
    opens_at: time | None
    closes_at: time | None


@dataclass(frozen=True)
class PublicContactLinkView:
    label: str
    url: str


@dataclass(frozen=True)
class PublicVerificationView:
    label: str
    reviewed_on: date
    expires_on: date | None


@dataclass(frozen=True)
class PublicVariantView:
    size: str
    color: str
    stock_unit: str
    selling_price: Decimal | None


@dataclass(frozen=True)
class PublicProductView:
    public_id: UUID
    name: str
    description: str
    category: str
    image_alt_text: str
    has_image: bool
    show_public_prices: bool
    variants: tuple[PublicVariantView, ...]


@dataclass(frozen=True)
class PublicProfileView:
    public_id: UUID
    display_name: str
    description: str
    phone: str
    email: str
    website: str
    address: str
    map_url: str
    supported_languages: tuple[str, ...]
    allow_search_indexing: bool
    opening_hours: tuple[PublicOpeningHourView, ...]
    contact_links: tuple[PublicContactLinkView, ...]
    verifications: tuple[PublicVerificationView, ...]
    products: tuple[PublicProductView, ...]


@dataclass(frozen=True)
class PublicReceiptView:
    business_name: str
    receipt_number: str
    receipt_type: str
    issued_at: datetime
    status: str


def _require_profile_manager(actor: BusinessMembership) -> None:
    if not actor.is_active or not actor.business.is_active or not actor.can_manage_public_profile:
        raise PermissionDenied(_("Public profile management permission is required."))


def _require_profile_owner(actor: BusinessMembership) -> None:
    _require_profile_manager(actor)
    if not actor.can_publish_public_profile:
        raise PermissionDenied(_("An owner membership is required for this action."))


def _require_staff_permission(user: User, permission: str) -> None:
    if not user.is_active or not user.is_staff or not user.has_perm(permission):
        raise PermissionDenied(_("Dedicated platform permission is required."))


def _membership_event(
    *,
    profile: PublicBusinessProfile,
    actor: BusinessMembership,
    action: str,
    details: dict[str, object] | None = None,
) -> PublicProfileEvent:
    return PublicProfileEvent.objects.create(
        business=profile.business,
        profile=profile,
        action=action,
        actor_kind=ActorKind.MEMBERSHIP,
        actor_membership=actor,
        details=details or {},
    )


def _staff_event(
    *,
    profile: PublicBusinessProfile,
    user: User,
    action: str,
    details: dict[str, object] | None = None,
) -> PublicProfileEvent:
    return PublicProfileEvent.objects.create(
        business=profile.business,
        profile=profile,
        action=action,
        actor_kind=ActorKind.PLATFORM_STAFF,
        actor_staff=user,
        details=details or {},
    )


def _system_event(
    *,
    profile: PublicBusinessProfile,
    action: str,
    details: dict[str, object] | None = None,
) -> PublicProfileEvent:
    return PublicProfileEvent.objects.create(
        business=profile.business,
        profile=profile,
        action=action,
        actor_kind=ActorKind.SYSTEM,
        details=details or {},
    )


def get_or_create_profile(actor: BusinessMembership) -> PublicBusinessProfile:
    _require_profile_manager(actor)
    profile, _ = PublicBusinessProfile.objects.get_or_create(
        business=actor.business,
        defaults={
            "display_name": actor.business.name,
            "supported_languages": "en",
        },
    )
    return profile


def _fingerprint(parts: tuple[str, ...]) -> str:
    normalized = "\x1f".join(part.strip().casefold() for part in parts)
    return hashlib.sha256(normalized.encode()).hexdigest()


def verification_subject_fingerprint(
    profile: PublicBusinessProfile,
    indicator_type: str,
) -> str:
    if indicator_type == VerificationType.CONTACT:
        return _fingerprint((profile.phone, profile.email, profile.website))
    if indicator_type == VerificationType.LOCATION:
        return _fingerprint((profile.address, profile.map_url))
    if indicator_type == VerificationType.BUSINESS_DOCUMENT:
        return _fingerprint((str(profile.business_id), profile.display_name))
    raise ValidationError(_("Unknown verification indicator type."))


def _verified_subject_present(profile: PublicBusinessProfile, indicator_type: str) -> bool:
    if indicator_type == VerificationType.CONTACT:
        return bool(profile.phone or profile.email or profile.website)
    if indicator_type == VerificationType.LOCATION:
        return bool(profile.address or profile.map_url)
    if indicator_type == VerificationType.BUSINESS_DOCUMENT:
        return bool(profile.display_name)
    return False


@transaction.atomic
def update_profile(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    data: ProfileDraftData,
) -> PublicBusinessProfile:
    _require_profile_manager(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    previous_fingerprints = {
        indicator_type: verification_subject_fingerprint(locked, indicator_type)
        for indicator_type in VerificationType.values
    }
    currently_verified_types = {
        indicator_type
        for indicator_type in VerificationType.values
        if (
            (
                latest := PublicVerificationDecision.objects.filter(
                    business=locked.business,
                    profile=locked,
                    indicator_type=indicator_type,
                )
                .order_by("-created_at")
                .first()
            )
            is not None
            and verification_is_current(
                latest,
                on_date=timezone.localdate(),
                subject_fingerprint=previous_fingerprints[indicator_type],
            )
        )
    }
    changed_fields: list[str] = []
    values: dict[str, object] = {
        "display_name": data.display_name.strip(),
        "description": data.description.strip(),
        "phone": data.phone.strip(),
        "email": data.email.strip(),
        "website": data.website.strip(),
        "address": data.address.strip(),
        "map_url": data.map_url.strip(),
        "supported_languages": ",".join(data.supported_languages),
    }
    for field_name, value in values.items():
        if getattr(locked, field_name) != value:
            setattr(locked, field_name, value)
            changed_fields.append(field_name)
    if not changed_fields:
        return locked
    locked.full_clean()
    locked.save(update_fields=(*changed_fields, "updated_at"))
    _membership_event(
        profile=locked,
        actor=actor,
        action=PublicProfileAction.PROFILE_UPDATED,
        details={"changed_fields": changed_fields},
    )
    stale_types = [
        indicator_type
        for indicator_type in VerificationType.values
        if indicator_type in currently_verified_types
        and previous_fingerprints[indicator_type]
        != verification_subject_fingerprint(locked, indicator_type)
    ]
    if stale_types:
        _system_event(
            profile=locked,
            action=PublicProfileAction.VERIFICATION_STALE,
            details={"indicator_types": stale_types},
        )
    return locked


@transaction.atomic
def update_opening_hours(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    hours: tuple[OpeningHourData, ...],
) -> None:
    _require_profile_manager(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if {hour.weekday for hour in hours} != set(range(7)):
        raise ValidationError(_("Opening hours must include each day exactly once."))
    changed = False
    for hour in hours:
        opens_at = None if hour.is_closed else hour.opens_at
        closes_at = None if hour.is_closed else hour.closes_at
        opening_hour, created = PublicOpeningHour.objects.get_or_create(
            business=actor.business,
            profile=locked,
            weekday=hour.weekday,
            defaults={
                "is_closed": hour.is_closed,
                "opens_at": opens_at,
                "closes_at": closes_at,
            },
        )
        item_changed = created or (
            opening_hour.is_closed != hour.is_closed
            or opening_hour.opens_at != opens_at
            or opening_hour.closes_at != closes_at
        )
        changed = changed or item_changed
        opening_hour.is_closed = hour.is_closed
        opening_hour.opens_at = opens_at
        opening_hour.closes_at = closes_at
        opening_hour.full_clean()
        if item_changed:
            opening_hour.save()
    if changed:
        _membership_event(
            profile=locked,
            actor=actor,
            action=PublicProfileAction.HOURS_UPDATED,
        )


@transaction.atomic
def update_contact_links(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    links: tuple[ContactLinkData, ...],
) -> None:
    _require_profile_manager(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if len({link.link_type for link in links}) != len(links):
        raise ValidationError(_("Each contact link type may appear only once."))
    changed = False
    retained_types: set[str] = set()
    for link in links:
        if not link.label.strip() and not link.url.strip():
            continue
        retained_types.add(link.link_type)
        label = link.label.strip()
        url = link.url.strip()
        contact, created = PublicContactLink.objects.get_or_create(
            business=actor.business,
            profile=locked,
            link_type=link.link_type,
            defaults={
                "label": label,
                "url": url,
            },
        )
        item_changed = created or (
            contact.label != label
            or contact.url != url
            or contact.display_order != link.display_order
            or contact.is_active != link.is_active
        )
        changed = changed or item_changed
        contact.label = label
        contact.url = url
        contact.display_order = link.display_order
        contact.is_active = link.is_active
        contact.full_clean()
        if item_changed:
            contact.save()
    removed, _details = (
        PublicContactLink.objects.filter(
            business=actor.business,
            profile=locked,
        )
        .exclude(link_type__in=retained_types)
        .delete()
    )
    if changed or removed:
        _membership_event(
            profile=locked,
            actor=actor,
            action=PublicProfileAction.CONTACT_LINK_UPDATED,
        )


def _profile_has_public_contact(profile: PublicBusinessProfile) -> bool:
    return bool(
        profile.phone
        or profile.email
        or profile.website
        or profile.contact_links.filter(is_active=True).exists()
    )


@transaction.atomic
def publish_profile(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    published_at: datetime | None = None,
) -> PublicBusinessProfile:
    _require_profile_owner(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if locked.is_suspended:
        raise ValidationError(_("A suspended profile cannot be published."))
    if not locked.business.is_active:
        raise ValidationError(_("An inactive business cannot publish a profile."))
    if not locked.display_name.strip() or not locked.description.strip():
        raise ValidationError(_("Display name and description are required before publishing."))
    if not _profile_has_public_contact(locked):
        raise ValidationError(_("Add at least one public contact method before publishing."))
    if locked.publication_status == PublicationStatus.PUBLISHED:
        return locked
    timestamp = published_at or timezone.now()
    locked.publication_status = PublicationStatus.PUBLISHED
    locked.published_at = timestamp
    locked.published_by = actor
    locked.full_clean()
    locked.save(update_fields=("publication_status", "published_at", "published_by", "updated_at"))
    _membership_event(
        profile=locked,
        actor=actor,
        action=PublicProfileAction.PUBLISHED,
    )
    return locked


@transaction.atomic
def unpublish_profile(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    unpublished_at: datetime | None = None,
) -> PublicBusinessProfile:
    _require_profile_owner(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if locked.publication_status == PublicationStatus.UNPUBLISHED:
        return locked
    locked.publication_status = PublicationStatus.UNPUBLISHED
    locked.unpublished_at = unpublished_at or timezone.now()
    locked.unpublished_by = actor
    locked.full_clean()
    locked.save(
        update_fields=(
            "publication_status",
            "unpublished_at",
            "unpublished_by",
            "updated_at",
        )
    )
    _membership_event(
        profile=locked,
        actor=actor,
        action=PublicProfileAction.UNPUBLISHED,
    )
    return locked


@transaction.atomic
def set_search_indexing(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    enabled: bool,
) -> PublicBusinessProfile:
    _require_profile_owner(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if locked.allow_search_indexing == enabled:
        return locked
    locked.allow_search_indexing = enabled
    locked.full_clean()
    locked.save(update_fields=("allow_search_indexing", "updated_at"))
    _membership_event(
        profile=locked,
        actor=actor,
        action=PublicProfileAction.INDEXING_CHANGED,
        details={"enabled": enabled},
    )
    return locked


@transaction.atomic
def suspend_profile(
    *,
    user: User,
    profile: PublicBusinessProfile,
    reason: str,
    suspended_at: datetime | None = None,
) -> PublicBusinessProfile:
    _require_staff_permission(user, "public_profiles.suspend_public_profile")
    if not reason.strip():
        raise ValidationError(_("A private suspension reason is required."))
    locked = PublicBusinessProfile.objects.select_for_update().get(pk=profile.pk)
    if locked.is_suspended:
        return locked
    locked.is_suspended = True
    locked.suspended_at = suspended_at or timezone.now()
    locked.suspended_by = user
    locked.suspension_reason = reason.strip()
    locked.full_clean()
    locked.save(
        update_fields=(
            "is_suspended",
            "suspended_at",
            "suspended_by",
            "suspension_reason",
            "updated_at",
        )
    )
    _staff_event(
        profile=locked,
        user=user,
        action=PublicProfileAction.SUSPENDED,
    )
    return locked


@transaction.atomic
def reinstate_profile(
    *,
    user: User,
    profile: PublicBusinessProfile,
) -> PublicBusinessProfile:
    _require_staff_permission(user, "public_profiles.suspend_public_profile")
    locked = PublicBusinessProfile.objects.select_for_update().get(pk=profile.pk)
    if not locked.is_suspended:
        return locked
    locked.is_suspended = False
    locked.suspended_at = None
    locked.suspended_by = None
    locked.suspension_reason = ""
    locked.full_clean()
    locked.save(
        update_fields=(
            "is_suspended",
            "suspended_at",
            "suspended_by",
            "suspension_reason",
            "updated_at",
        )
    )
    _staff_event(
        profile=locked,
        user=user,
        action=PublicProfileAction.REINSTATED,
    )
    return locked


@transaction.atomic
def set_product_publication(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    product: Product,
    visible: bool,
    show_public_prices: bool,
) -> Product:
    _require_profile_manager(actor)
    PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    locked = (
        Product.objects.select_for_update().filter(pk=product.pk, business=actor.business).first()
    )
    if locked is None:
        raise ValidationError(_("Product is unavailable for this business."))
    if show_public_prices and not visible:
        raise ValidationError(_("Prices cannot be public while the product is hidden."))
    if visible:
        identity, _created = PublicProductIdentity.objects.get_or_create(
            business=actor.business,
            product=locked,
        )
        identity.full_clean()
    changed = locked.public_visibility != visible or locked.show_public_prices != show_public_prices
    if not changed:
        return locked
    locked.public_visibility = visible
    locked.show_public_prices = show_public_prices
    locked.full_clean()
    locked.save(update_fields=("public_visibility", "show_public_prices", "updated_at"))
    _membership_event(
        profile=profile,
        actor=actor,
        action=PublicProfileAction.PRODUCT_VISIBILITY_CHANGED,
        details={
            "product_id": str(locked.id),
            "visible": visible,
            "show_public_prices": show_public_prices,
        },
    )
    return locked


def _public_profile_queryset() -> QuerySet[PublicBusinessProfile]:
    active_variants = ProductVariant.objects.filter(is_active=True).order_by(
        "size",
        "color",
        "sku",
    )
    public_products = (
        Product.objects.filter(
            is_active=True,
            public_visibility=True,
            variants__is_active=True,
            public_identity__isnull=False,
        )
        .select_related("category", "public_identity")
        .prefetch_related(
            Prefetch("variants", queryset=active_variants),
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(removed_at__isnull=True),
            ),
        )
        .distinct()
        .order_by("name")
    )
    return (
        PublicBusinessProfile.objects.filter(
            business__is_active=True,
            publication_status=PublicationStatus.PUBLISHED,
            is_suspended=False,
        )
        .select_related("business")
        .prefetch_related(
            "opening_hours",
            Prefetch(
                "contact_links",
                queryset=PublicContactLink.objects.filter(is_active=True),
            ),
            Prefetch("business__products", queryset=public_products),
        )
    )


def _preview_profile_queryset() -> QuerySet[PublicBusinessProfile]:
    active_variants = ProductVariant.objects.filter(is_active=True).order_by(
        "size",
        "color",
        "sku",
    )
    public_products = (
        Product.objects.filter(
            is_active=True,
            public_visibility=True,
            variants__is_active=True,
            public_identity__isnull=False,
        )
        .select_related("category", "public_identity")
        .prefetch_related(
            Prefetch("variants", queryset=active_variants),
            Prefetch(
                "images",
                queryset=ProductImage.objects.filter(removed_at__isnull=True),
            ),
        )
        .distinct()
        .order_by("name")
    )
    return PublicBusinessProfile.objects.select_related("business").prefetch_related(
        "opening_hours",
        Prefetch(
            "contact_links",
            queryset=PublicContactLink.objects.filter(is_active=True),
        ),
        Prefetch("business__products", queryset=public_products),
    )


def _active_verifications(
    profile: PublicBusinessProfile,
    *,
    on_date: date,
) -> tuple[PublicVerificationView, ...]:
    views: list[PublicVerificationView] = []
    for indicator_type, label in VerificationType.choices:
        decision = (
            PublicVerificationDecision.objects.filter(
                business=profile.business,
                profile=profile,
                indicator_type=indicator_type,
            )
            .order_by("-created_at")
            .first()
        )
        if (
            decision is not None
            and decision.public_reviewed_on is not None
            and verification_is_current(
                decision,
                on_date=on_date,
                subject_fingerprint=verification_subject_fingerprint(
                    profile,
                    indicator_type,
                ),
            )
        ):
            views.append(
                PublicVerificationView(
                    label=str(label),
                    reviewed_on=decision.public_reviewed_on,
                    expires_on=decision.expires_on,
                )
            )
    return tuple(views)


def _product_view(product: Product) -> PublicProductView:
    identity = product.public_identity
    image = next(iter(product.images.all()), None)
    variants = tuple(
        PublicVariantView(
            size=variant.size,
            color=variant.color,
            stock_unit=str(variant.get_stock_unit_display()),
            selling_price=variant.selling_price if product.show_public_prices else None,
        )
        for variant in product.variants.all()
        if variant.is_active
    )
    return PublicProductView(
        public_id=identity.public_id,
        name=product.name,
        description=product.description,
        category=product.category.name if product.category and product.category.is_active else "",
        image_alt_text=image.alt_text if image else "",
        has_image=image is not None,
        show_public_prices=product.show_public_prices,
        variants=variants,
    )


def _profile_view(profile: PublicBusinessProfile) -> PublicProfileView:
    return PublicProfileView(
        public_id=profile.public_id,
        display_name=profile.display_name,
        description=profile.description,
        phone=profile.phone,
        email=profile.email,
        website=profile.website,
        address=profile.address,
        map_url=profile.map_url,
        supported_languages=tuple(profile.supported_languages.split(",")),
        allow_search_indexing=profile.allow_search_indexing,
        opening_hours=tuple(
            PublicOpeningHourView(
                weekday=str(hour.get_weekday_display()),
                is_closed=hour.is_closed,
                opens_at=hour.opens_at,
                closes_at=hour.closes_at,
            )
            for hour in profile.opening_hours.all()
        ),
        contact_links=tuple(
            PublicContactLinkView(label=link.label, url=link.url)
            for link in profile.contact_links.all()
        ),
        verifications=_active_verifications(profile, on_date=timezone.localdate()),
        products=tuple(_product_view(product) for product in profile.business.products.all()),
    )


def get_public_profile(public_id: UUID) -> PublicProfileView:
    profile = _public_profile_queryset().filter(public_id=public_id).first()
    if profile is None:
        raise PublicBusinessProfile.DoesNotExist
    return _profile_view(profile)


def build_profile_preview(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
) -> PublicProfileView:
    _require_profile_manager(actor)
    scoped_profile = _preview_profile_queryset().get(
        pk=profile.pk,
        business=actor.business,
    )
    return _profile_view(scoped_profile)


def get_public_product(
    *,
    profile_public_id: UUID,
    product_public_id: UUID,
) -> tuple[PublicProfileView, PublicProductView]:
    profile = _public_profile_queryset().filter(public_id=profile_public_id).first()
    if profile is None:
        raise PublicBusinessProfile.DoesNotExist
    profile_view = _profile_view(profile)
    product_view = next(
        (product for product in profile_view.products if product.public_id == product_public_id),
        None,
    )
    if product_view is None:
        raise Product.DoesNotExist
    return profile_view, product_view


def metric_source(raw_source: str | None) -> str:
    if raw_source == MetricSource.QR:
        return MetricSource.QR
    if raw_source == MetricSource.SHARED:
        return MetricSource.SHARED
    return MetricSource.DIRECT


def increment_metric(
    *,
    profile: PublicBusinessProfile,
    source: str,
    metric: str,
    target_type: str,
    target_public_id: UUID,
    product: Product | None = None,
) -> None:
    lookup = {
        "business": profile.business,
        "profile": profile,
        "product": product,
        "local_date": timezone.localdate(),
        "source": source,
        "metric": metric,
        "target_type": target_type,
        "target_public_id": target_public_id,
    }
    for attempt in range(2):
        try:
            with transaction.atomic():
                bucket, created = PublicStorefrontDailyMetric.objects.get_or_create(
                    **lookup,
                    defaults={"count": 1},
                )
                if not created:
                    PublicStorefrontDailyMetric.objects.filter(pk=bucket.pk).update(
                        count=F("count") + 1
                    )
            return
        except IntegrityError:
            if attempt == 1:
                raise


def get_profile_model_for_public_id(public_id: UUID) -> PublicBusinessProfile:
    return _public_profile_queryset().get(public_id=public_id)


def get_product_model_for_public_id(
    *,
    profile: PublicBusinessProfile,
    product_public_id: UUID,
) -> Product:
    return (
        Product.objects.filter(
            business=profile.business,
            is_active=True,
            public_visibility=True,
            variants__is_active=True,
            public_identity__public_id=product_public_id,
        )
        .distinct()
        .get()
    )


def public_origin() -> str:
    origin = str(settings.PUBLIC_SITE_ORIGIN).rstrip("/")
    parsed = urlparse(origin)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError(_("Public site origin is invalid."))
    if parsed.scheme == "https" and parsed.netloc:
        return origin
    if settings.DEBUG and parsed.scheme == "http" and parsed.hostname in local_hosts:
        return origin
    raise ValidationError(_("Configure a secure HTTPS public site origin."))


def absolute_public_url(path: str, *, source: str | None = None) -> str:
    suffix = f"?source={source}" if source else ""
    return f"{public_origin()}{path}{suffix}"


def qr_svg(payload: str) -> bytes:
    output = BytesIO()
    code = segno.make(payload, micro=False, error="m")
    code.save(
        output,
        kind="svg",
        scale=6,
        border=4,
        dark="#17365d",
        light="#ffffff",
        xmldecl=False,
    )
    return output.getvalue()


def profile_public_url(profile: PublicBusinessProfile, *, source: str | None = None) -> str:
    path = reverse("public_profiles:public-profile", args=(profile.public_id,))
    return absolute_public_url(path, source=source)


def product_public_url(
    *,
    profile: PublicBusinessProfile,
    identity: PublicProductIdentity,
    source: str | None = None,
) -> str:
    path = reverse(
        "public_profiles:public-product",
        args=(profile.public_id, identity.public_id),
    )
    return absolute_public_url(path, source=source)


def sale_receipt_public_url(
    identity: PublicSaleReceiptIdentity,
    *,
    source: str | None = None,
) -> str:
    path = reverse("public_profiles:verify-sale-receipt", args=(identity.public_token,))
    return absolute_public_url(path, source=source)


def return_receipt_public_url(
    identity: PublicReturnReceiptIdentity,
    *,
    source: str | None = None,
) -> str:
    path = reverse("public_profiles:verify-return-receipt", args=(identity.public_token,))
    return absolute_public_url(path, source=source)


def get_or_create_sale_receipt_identity(
    receipt: InternalReceipt,
) -> PublicSaleReceiptIdentity:
    identity, _created = PublicSaleReceiptIdentity.objects.get_or_create(
        business=receipt.business,
        receipt=receipt,
    )
    return identity


def get_or_create_return_receipt_identity(
    receipt: InternalReturnReceipt,
) -> PublicReturnReceiptIdentity:
    identity, _created = PublicReturnReceiptIdentity.objects.get_or_create(
        business=receipt.business,
        receipt=receipt,
    )
    return identity


def public_sale_receipt(token: UUID) -> PublicReceiptView:
    identity = (
        PublicSaleReceiptIdentity.objects.select_related(
            "receipt__business",
            "receipt__sale",
            "receipt__business__public_profile",
        )
        .filter(
            public_token=token,
            receipt__business__is_active=True,
            receipt__business__public_profile__publication_status=PublicationStatus.PUBLISHED,
            receipt__business__public_profile__is_suspended=False,
        )
        .first()
    )
    if identity is None:
        raise PublicSaleReceiptIdentity.DoesNotExist
    receipt = identity.receipt
    is_reversed = receipt.sale.returns.filter(
        purpose=SaleReturnPurpose.SALE_REVERSAL,
        status=SaleReturnStatus.POSTED,
    ).exists()
    status = _("Reversed") if is_reversed else _("Recorded")
    return PublicReceiptView(
        business_name=receipt.business.public_profile.display_name,
        receipt_number=receipt.internal_number,
        receipt_type=str(_("Sale receipt")),
        issued_at=receipt.issued_at,
        status=str(status),
    )


def public_return_receipt(token: UUID) -> PublicReceiptView:
    identity = (
        PublicReturnReceiptIdentity.objects.select_related(
            "receipt__business",
            "receipt__sale_return",
            "receipt__business__public_profile",
        )
        .filter(
            public_token=token,
            receipt__business__is_active=True,
            receipt__business__public_profile__publication_status=PublicationStatus.PUBLISHED,
            receipt__business__public_profile__is_suspended=False,
        )
        .first()
    )
    if identity is None:
        raise PublicReturnReceiptIdentity.DoesNotExist
    receipt = identity.receipt
    status = (
        _("Reversed") if receipt.sale_return.status == SaleReturnStatus.REVERSED else _("Recorded")
    )
    return PublicReceiptView(
        business_name=receipt.business.public_profile.display_name,
        receipt_number=receipt.internal_number,
        receipt_type=str(_("Return receipt")),
        issued_at=receipt.issued_at,
        status=str(status),
    )


@transaction.atomic
def submit_verification_request(
    *,
    actor: BusinessMembership,
    profile: PublicBusinessProfile,
    indicator_type: str,
    reason: str,
    parent_request: PublicVerificationRequest | None = None,
) -> PublicVerificationRequest:
    _require_profile_owner(actor)
    locked = PublicBusinessProfile.objects.select_for_update().get(
        pk=profile.pk,
        business=actor.business,
    )
    if indicator_type not in VerificationType.values:
        raise ValidationError(_("Unknown verification indicator type."))
    if not _verified_subject_present(locked, indicator_type):
        raise ValidationError(_("Add the related public profile information first."))
    if not reason.strip():
        raise ValidationError(_("Explain what should be reviewed."))
    if parent_request is not None:
        parent_request = PublicVerificationRequest.objects.get(
            pk=parent_request.pk,
            business=actor.business,
            profile=locked,
            indicator_type=indicator_type,
        )
        if parent_request.status == VerificationRequestStatus.PENDING:
            raise ValidationError(_("A pending request cannot be appealed."))
    try:
        request = PublicVerificationRequest.objects.create(
            business=actor.business,
            profile=locked,
            indicator_type=indicator_type,
            requester=actor,
            parent_request=parent_request,
            request_reason=reason.strip(),
        )
    except IntegrityError as exc:
        raise ValidationError(_("A request for this indicator is already pending.")) from exc
    _membership_event(
        profile=locked,
        actor=actor,
        action=PublicProfileAction.VERIFICATION_REQUESTED,
        details={
            "indicator_type": indicator_type,
            "is_appeal": parent_request is not None,
        },
    )
    return request


@transaction.atomic
def decide_verification_request(
    *,
    user: User,
    request: PublicVerificationRequest,
    approved: bool,
    evidence_reference: str,
    private_reason: str,
    reviewed_on: date | None = None,
    expires_on: date | None = None,
) -> PublicVerificationDecision:
    _require_staff_permission(user, "public_profiles.review_public_verification")
    locked = (
        PublicVerificationRequest.objects.select_for_update()
        .select_related(
            "profile",
            "business",
        )
        .get(pk=request.pk)
    )
    if locked.status != VerificationRequestStatus.PENDING:
        raise ValidationError(_("Only pending verification requests can be decided."))
    if not private_reason.strip():
        raise ValidationError(_("A private decision reason is required."))
    if approved and not evidence_reference.strip():
        raise ValidationError(_("Approved verification requires an evidence reference."))
    if approved and not _verified_subject_present(locked.profile, locked.indicator_type):
        raise ValidationError(_("The related public profile information is missing."))
    action = VerificationDecisionAction.APPROVE if approved else VerificationDecisionAction.REJECT
    public_reviewed_on = (reviewed_on or timezone.localdate()) if approved else None
    decision = PublicVerificationDecision.objects.create(
        business=locked.business,
        profile=locked.profile,
        request=locked,
        indicator_type=locked.indicator_type,
        action=action,
        staff_user=user,
        evidence_reference=evidence_reference.strip(),
        private_reason=private_reason.strip(),
        public_reviewed_on=public_reviewed_on,
        expires_on=expires_on if approved else None,
        subject_fingerprint=(
            verification_subject_fingerprint(locked.profile, locked.indicator_type)
            if approved
            else ""
        ),
    )
    locked.status = (
        VerificationRequestStatus.APPROVED if approved else VerificationRequestStatus.REJECTED
    )
    locked.full_clean()
    locked.save(update_fields=("status", "updated_at"))
    _staff_event(
        profile=locked.profile,
        user=user,
        action=PublicProfileAction.VERIFICATION_DECIDED,
        details={
            "indicator_type": locked.indicator_type,
            "approved": approved,
        },
    )
    return decision


@transaction.atomic
def revoke_verification(
    *,
    user: User,
    profile: PublicBusinessProfile,
    indicator_type: str,
    private_reason: str,
) -> PublicVerificationDecision:
    _require_staff_permission(user, "public_profiles.review_public_verification")
    if not private_reason.strip():
        raise ValidationError(_("A private revocation reason is required."))
    locked = PublicBusinessProfile.objects.select_for_update().get(pk=profile.pk)
    latest = (
        PublicVerificationDecision.objects.filter(
            profile=locked,
            indicator_type=indicator_type,
        )
        .select_related("request")
        .order_by("-created_at")
        .first()
    )
    if latest is None or not verification_is_current(
        latest,
        on_date=timezone.localdate(),
        subject_fingerprint=verification_subject_fingerprint(locked, indicator_type),
    ):
        raise ValidationError(_("No current verification indicator can be revoked."))
    decision = PublicVerificationDecision.objects.create(
        business=locked.business,
        profile=locked,
        request=latest.request,
        indicator_type=indicator_type,
        action=VerificationDecisionAction.REVOKE,
        staff_user=user,
        private_reason=private_reason.strip(),
    )
    _staff_event(
        profile=locked,
        user=user,
        action=PublicProfileAction.VERIFICATION_DECIDED,
        details={"indicator_type": indicator_type, "revoked": True},
    )
    return decision


@transaction.atomic
def renew_verification(
    *,
    user: User,
    profile: PublicBusinessProfile,
    indicator_type: str,
    evidence_reference: str,
    private_reason: str,
    reviewed_on: date | None = None,
    expires_on: date | None = None,
) -> PublicVerificationDecision:
    _require_staff_permission(user, "public_profiles.review_public_verification")
    if not evidence_reference.strip() or not private_reason.strip():
        raise ValidationError(_("Renewal requires an evidence reference and private reason."))
    locked = PublicBusinessProfile.objects.select_for_update().get(pk=profile.pk)
    latest = (
        PublicVerificationDecision.objects.filter(
            profile=locked,
            indicator_type=indicator_type,
        )
        .select_related("request")
        .order_by("-created_at")
        .first()
    )
    if latest is None:
        raise ValidationError(_("No verification decision can be renewed."))
    return PublicVerificationDecision.objects.create(
        business=locked.business,
        profile=locked,
        request=latest.request,
        indicator_type=indicator_type,
        action=VerificationDecisionAction.RENEW,
        staff_user=user,
        evidence_reference=evidence_reference.strip(),
        private_reason=private_reason.strip(),
        public_reviewed_on=reviewed_on or timezone.localdate(),
        expires_on=expires_on,
        subject_fingerprint=verification_subject_fingerprint(locked, indicator_type),
    )


@transaction.atomic
def expire_verification(
    *,
    user: User,
    profile: PublicBusinessProfile,
    indicator_type: str,
    private_reason: str,
) -> PublicVerificationDecision:
    _require_staff_permission(user, "public_profiles.review_public_verification")
    if not private_reason.strip():
        raise ValidationError(_("A private expiry reason is required."))
    locked = PublicBusinessProfile.objects.select_for_update().get(pk=profile.pk)
    latest = (
        PublicVerificationDecision.objects.filter(
            profile=locked,
            indicator_type=indicator_type,
        )
        .select_related("request")
        .order_by("-created_at")
        .first()
    )
    if latest is None:
        raise ValidationError(_("No verification decision can be expired."))
    return PublicVerificationDecision.objects.create(
        business=locked.business,
        profile=locked,
        request=latest.request,
        indicator_type=indicator_type,
        action=VerificationDecisionAction.EXPIRE,
        staff_user=user,
        private_reason=private_reason.strip(),
    )
