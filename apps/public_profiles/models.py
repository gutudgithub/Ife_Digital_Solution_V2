import uuid
from collections.abc import Iterable
from datetime import date
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.db.models.base import ModelBase
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Business, BusinessMembership
from apps.catalog.models import Product
from apps.sales.models import InternalReceipt, InternalReturnReceipt


class PublicationStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    UNPUBLISHED = "unpublished", _("Unpublished")


class PublicLanguage(models.TextChoices):
    ENGLISH = "en", _("English")
    AMHARIC = "am", _("Amharic")
    AFAAN_OROMOO = "om", _("Afaan Oromoo")


class ContactLinkType(models.TextChoices):
    FACEBOOK = "facebook", _("Facebook")
    INSTAGRAM = "instagram", _("Instagram")
    TELEGRAM = "telegram", _("Telegram")
    TIKTOK = "tiktok", _("TikTok")
    YOUTUBE = "youtube", _("YouTube")
    OTHER = "other", _("Other")


class PublicProfileAction(models.TextChoices):
    PROFILE_UPDATED = "profile_updated", _("Profile updated")
    HOURS_UPDATED = "hours_updated", _("Opening hours updated")
    CONTACT_LINK_UPDATED = "contact_link_updated", _("Contact link updated")
    PUBLISHED = "published", _("Published")
    UNPUBLISHED = "unpublished", _("Unpublished")
    SUSPENDED = "suspended", _("Suspended")
    REINSTATED = "reinstated", _("Reinstated")
    INDEXING_CHANGED = "indexing_changed", _("Indexing changed")
    PRODUCT_VISIBILITY_CHANGED = (
        "product_visibility_changed",
        _("Product visibility changed"),
    )
    VERIFICATION_REQUESTED = "verification_requested", _("Verification requested")
    VERIFICATION_DECIDED = "verification_decided", _("Verification decided")
    VERIFICATION_STALE = "verification_stale", _("Verification became stale")


class ActorKind(models.TextChoices):
    MEMBERSHIP = "membership", _("Business membership")
    PLATFORM_STAFF = "platform_staff", _("Platform staff")
    SYSTEM = "system", _("System")


class VerificationType(models.TextChoices):
    CONTACT = "contact", _("Contact reviewed")
    LOCATION = "location", _("Location reviewed")
    BUSINESS_DOCUMENT = "business_document", _("Business document reviewed")


class VerificationRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn")


class VerificationDecisionAction(models.TextChoices):
    APPROVE = "approve", _("Approve")
    REJECT = "reject", _("Reject")
    REVOKE = "revoke", _("Revoke")
    RENEW = "renew", _("Renew")
    EXPIRE = "expire", _("Expire")
    STALE = "stale", _("Stale")


class MetricSource(models.TextChoices):
    DIRECT = "direct", _("Direct")
    SHARED = "shared", _("Shared link")
    QR = "qr", _("QR code")


class MetricKind(models.TextChoices):
    PROFILE_VIEW = "profile_view", _("Profile view")
    PRODUCT_VIEW = "product_view", _("Product view")


class MetricTargetType(models.TextChoices):
    PROFILE = "profile", _("Profile")
    PRODUCT = "product", _("Product")


class PublicBusinessProfile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.OneToOneField(
        Business,
        on_delete=models.PROTECT,
        related_name="public_profile",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    display_name = models.CharField(max_length=160)
    description = models.TextField(max_length=1000, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    map_url = models.URLField(blank=True)
    supported_languages = models.CharField(max_length=16, default=PublicLanguage.ENGLISH)
    publication_status = models.CharField(
        max_length=16,
        choices=PublicationStatus.choices,
        default=PublicationStatus.DRAFT,
    )
    allow_search_indexing = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="public_profiles_published",
        null=True,
        blank=True,
    )
    unpublished_at = models.DateTimeField(null=True, blank=True)
    unpublished_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="public_profiles_unpublished",
        null=True,
        blank=True,
    )
    is_suspended = models.BooleanField(default=False)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="public_profiles_suspended",
        null=True,
        blank=True,
    )
    suspension_reason = models.TextField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("display_name",)
        permissions = [
            ("review_public_verification", "Can review public profile verification"),
            ("suspend_public_profile", "Can suspend public profiles"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(publication_status__in=PublicationStatus.values),
                name="public_profile_publication_status_valid",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        is_suspended=True,
                        suspended_at__isnull=False,
                        suspended_by__isnull=False,
                    )
                    & ~Q(suspension_reason="")
                )
                | Q(
                    is_suspended=False,
                    suspended_at__isnull=True,
                    suspended_by__isnull=True,
                    suspension_reason="",
                ),
                name="public_profile_suspension_fields_match",
            ),
        ]

    def __str__(self) -> str:
        return self.display_name or self.business.name

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        languages = tuple(
            language.strip() for language in self.supported_languages.split(",") if language.strip()
        )
        if not languages:
            errors["supported_languages"] = ValidationError(
                _("Select at least one supported language.")
            )
        elif len(set(languages)) != len(languages) or any(
            language not in PublicLanguage.values for language in languages
        ):
            errors["supported_languages"] = ValidationError(
                _("Supported languages contain an invalid or duplicate value.")
            )
        for field_name in ("website", "map_url"):
            value = str(getattr(self, field_name))
            if value and urlparse(value).scheme != "https":
                errors[field_name] = ValidationError(_("Use a secure HTTPS URL."))
        for membership_field in ("published_by", "unpublished_by"):
            membership = getattr(self, membership_field)
            if membership is not None and membership.business_id != self.business_id:
                errors[membership_field] = ValidationError(
                    _("The membership must belong to this business.")
                )
        if errors:
            raise ValidationError(errors)


class PublicOpeningHour(models.Model):
    class Weekday(models.IntegerChoices):
        MONDAY = 0, _("Monday")
        TUESDAY = 1, _("Tuesday")
        WEDNESDAY = 2, _("Wednesday")
        THURSDAY = 3, _("Thursday")
        FRIDAY = 4, _("Friday")
        SATURDAY = 5, _("Saturday")
        SUNDAY = 6, _("Sunday")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_opening_hours",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="opening_hours",
    )
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    is_closed = models.BooleanField(default=False)
    opens_at = models.TimeField(null=True, blank=True)
    closes_at = models.TimeField(null=True, blank=True)

    class Meta:
        ordering = ("weekday",)
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "weekday"),
                name="public_unique_opening_hour_per_weekday",
            ),
            models.CheckConstraint(
                condition=Q(weekday__gte=0, weekday__lte=6),
                name="public_opening_hour_weekday_valid",
            ),
            models.CheckConstraint(
                condition=Q(is_closed=True, opens_at__isnull=True, closes_at__isnull=True)
                | Q(is_closed=False, opens_at__isnull=False, closes_at__isnull=False),
                name="public_opening_hour_values_match_closed",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.get_weekday_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if (
            not self.is_closed
            and self.opens_at is not None
            and self.closes_at is not None
            and self.opens_at >= self.closes_at
        ):
            errors["closes_at"] = ValidationError(
                _("Closing time must be later than opening time.")
            )
        if errors:
            raise ValidationError(errors)


class PublicContactLink(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_contact_links",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="contact_links",
    )
    link_type = models.CharField(max_length=16, choices=ContactLinkType.choices)
    label = models.CharField(max_length=80)
    url = models.URLField()
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("display_order", "label")
        constraints = [
            models.UniqueConstraint(
                fields=("profile", "link_type"),
                name="public_unique_contact_link_type",
            ),
            models.CheckConstraint(
                condition=Q(link_type__in=ContactLinkType.values),
                name="public_contact_link_type_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.label}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if self.url and urlparse(self.url).scheme != "https":
            errors["url"] = ValidationError(_("Use a secure HTTPS URL."))
        if errors:
            raise ValidationError(errors)


class PublicProductIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_product_identities",
    )
    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        related_name="public_identity",
    )
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.product} — {self.public_id}"

    def clean(self) -> None:
        super().clean()
        if self.product_id and self.product.business_id != self.business_id:
            raise ValidationError({"product": _("Product must belong to this business.")})


class PublicProfileEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_profile_events",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="events",
    )
    action = models.CharField(max_length=40, choices=PublicProfileAction.choices)
    actor_kind = models.CharField(max_length=16, choices=ActorKind.choices)
    actor_membership = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="public_profile_events",
        null=True,
        blank=True,
    )
    actor_staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="platform_public_profile_events",
        null=True,
        blank=True,
    )
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(action__in=PublicProfileAction.values),
                name="public_profile_event_action_valid",
            ),
            models.CheckConstraint(
                condition=Q(actor_kind__in=ActorKind.values),
                name="public_profile_event_actor_kind_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.get_action_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Public profile events cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Public profile events cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if (
            self.actor_membership_id
            and self.actor_membership is not None
            and self.actor_membership.business_id != self.business_id
        ):
            errors["actor_membership"] = ValidationError(
                _("Actor membership must belong to this business.")
            )
        if self.actor_kind == ActorKind.MEMBERSHIP and self.actor_membership_id is None:
            errors["actor_membership"] = ValidationError(_("Membership actor is required."))
        if self.actor_kind == ActorKind.PLATFORM_STAFF and self.actor_staff_id is None:
            errors["actor_staff"] = ValidationError(_("Platform staff actor is required."))
        if self.actor_kind == ActorKind.SYSTEM and (
            self.actor_membership_id is not None or self.actor_staff_id is not None
        ):
            errors["actor_kind"] = ValidationError(_("System events cannot name a user actor."))
        if errors:
            raise ValidationError(errors)


class PublicVerificationRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_verification_requests",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="verification_requests",
    )
    indicator_type = models.CharField(max_length=24, choices=VerificationType.choices)
    status = models.CharField(
        max_length=16,
        choices=VerificationRequestStatus.choices,
        default=VerificationRequestStatus.PENDING,
    )
    requester = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="public_verification_requests",
    )
    parent_request = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="appeals",
        null=True,
        blank=True,
    )
    request_reason = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(indicator_type__in=VerificationType.values),
                name="public_verification_request_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(status__in=VerificationRequestStatus.values),
                name="public_verification_request_status_valid",
            ),
            models.UniqueConstraint(
                fields=("profile", "indicator_type"),
                condition=Q(status=VerificationRequestStatus.PENDING),
                name="public_unique_pending_verification_type",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.get_indicator_type_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if self.requester_id and self.requester.business_id != self.business_id:
            errors["requester"] = ValidationError(_("Requester must belong to this business."))
        if self.parent_request_id:
            parent_request = self.parent_request
            if parent_request is not None and parent_request.business_id != self.business_id:
                errors["parent_request"] = ValidationError(
                    _("Appealed request must belong to this business.")
                )
            elif (
                parent_request is not None and parent_request.indicator_type != self.indicator_type
            ):
                errors["parent_request"] = ValidationError(
                    _("Appeal must use the original indicator type.")
                )
        if errors:
            raise ValidationError(errors)


class PublicVerificationDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_verification_decisions",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="verification_decisions",
    )
    request = models.ForeignKey(
        PublicVerificationRequest,
        on_delete=models.PROTECT,
        related_name="decisions",
    )
    indicator_type = models.CharField(max_length=24, choices=VerificationType.choices)
    action = models.CharField(max_length=16, choices=VerificationDecisionAction.choices)
    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="public_verification_decisions",
    )
    evidence_reference = models.CharField(max_length=200, blank=True)
    private_reason = models.TextField(max_length=1000)
    public_reviewed_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)
    subject_fingerprint = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=Q(indicator_type__in=VerificationType.values),
                name="public_verification_decision_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(action__in=VerificationDecisionAction.values),
                name="public_verification_decision_action_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.get_action_display()}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Verification decisions cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Verification decisions cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if self.request_id:
            if self.request.business_id != self.business_id:
                errors["request"] = ValidationError(
                    _("Verification request must belong to this business.")
                )
            elif self.request.indicator_type != self.indicator_type:
                errors["request"] = ValidationError(
                    _("Decision must use the request indicator type.")
                )
        if (
            self.expires_on
            and self.public_reviewed_on
            and self.expires_on <= self.public_reviewed_on
        ):
            errors["expires_on"] = ValidationError(
                _("Expiry must be later than the public review date.")
            )
        if errors:
            raise ValidationError(errors)


class PublicStorefrontDailyMetric(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_storefront_metrics",
    )
    profile = models.ForeignKey(
        PublicBusinessProfile,
        on_delete=models.PROTECT,
        related_name="daily_metrics",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="public_daily_metrics",
        null=True,
        blank=True,
    )
    local_date = models.DateField()
    source = models.CharField(max_length=16, choices=MetricSource.choices)
    metric = models.CharField(max_length=16, choices=MetricKind.choices)
    target_type = models.CharField(max_length=16, choices=MetricTargetType.choices)
    target_public_id = models.UUIDField()
    count = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ("-local_date", "metric", "source")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "profile",
                    "local_date",
                    "source",
                    "metric",
                    "target_type",
                    "target_public_id",
                ),
                name="public_unique_daily_metric_bucket",
            ),
            models.CheckConstraint(
                condition=Q(source__in=MetricSource.values),
                name="public_metric_source_valid",
            ),
            models.CheckConstraint(
                condition=Q(metric__in=MetricKind.values),
                name="public_metric_kind_valid",
            ),
            models.CheckConstraint(
                condition=Q(target_type__in=MetricTargetType.values),
                name="public_metric_target_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(count__gte=0),
                name="public_metric_count_nonnegative",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.profile} — {self.local_date} — {self.get_metric_display()}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.profile_id and self.profile.business_id != self.business_id:
            errors["profile"] = ValidationError(_("Profile must belong to this business."))
        if (
            self.product_id
            and self.product is not None
            and self.product.business_id != self.business_id
        ):
            errors["product"] = ValidationError(_("Product must belong to this business."))
        if self.target_type == MetricTargetType.PROFILE:
            if self.product_id is not None:
                errors["product"] = ValidationError(_("Profile metrics cannot name a product."))
            if self.profile_id and self.target_public_id != self.profile.public_id:
                errors["target_public_id"] = ValidationError(
                    _("Profile metric target must match the profile.")
                )
        elif self.target_type == MetricTargetType.PRODUCT:
            if self.product_id is None:
                errors["product"] = ValidationError(_("Product metric requires a product."))
            elif not PublicProductIdentity.objects.filter(
                product_id=self.product_id,
                public_id=self.target_public_id,
            ).exists():
                errors["target_public_id"] = ValidationError(
                    _("Product metric target must match the public product identity.")
                )
        if errors:
            raise ValidationError(errors)


class PublicSaleReceiptIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_sale_receipt_identities",
    )
    receipt = models.OneToOneField(
        InternalReceipt,
        on_delete=models.PROTECT,
        related_name="public_identity",
    )
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.receipt} — {self.public_token}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Public receipt identities cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Public receipt identities cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        if self.receipt_id and self.receipt.business_id != self.business_id:
            raise ValidationError({"receipt": _("Receipt must belong to this business.")})


class PublicReturnReceiptIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="public_return_receipt_identities",
    )
    receipt = models.OneToOneField(
        InternalReturnReceipt,
        on_delete=models.PROTECT,
        related_name="public_identity",
    )
    public_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.receipt} — {self.public_token}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if not self._state.adding:
            raise ValidationError(_("Public receipt identities cannot be modified."))
        self.full_clean()
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    def delete(
        self,
        using: str | None = None,
        keep_parents: bool = False,
    ) -> tuple[int, dict[str, int]]:
        raise ValidationError(_("Public receipt identities cannot be deleted."))

    def clean(self) -> None:
        super().clean()
        if self.receipt_id and self.receipt.business_id != self.business_id:
            raise ValidationError({"receipt": _("Receipt must belong to this business.")})


def verification_is_current(
    decision: PublicVerificationDecision,
    *,
    on_date: date,
    subject_fingerprint: str,
) -> bool:
    return (
        decision.action
        in {
            VerificationDecisionAction.APPROVE,
            VerificationDecisionAction.RENEW,
        }
        and decision.subject_fingerprint == subject_fingerprint
        and (decision.expires_on is None or decision.expires_on >= on_date)
    )
