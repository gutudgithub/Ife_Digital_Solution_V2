from datetime import date, time, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings as django_override_settings

from apps.businesses.models import Business
from apps.catalog.models import Product
from apps.public_profiles.models import (
    MetricKind,
    MetricSource,
    MetricTargetType,
    PublicationStatus,
    PublicProductIdentity,
    PublicProfileAction,
    PublicProfileEvent,
    PublicStorefrontDailyMetric,
    VerificationDecisionAction,
    VerificationType,
)
from apps.public_profiles.services import (
    OpeningHourData,
    ProfileDraftData,
    absolute_public_url,
    decide_verification_request,
    expire_verification,
    get_public_profile,
    increment_metric,
    publish_profile,
    renew_verification,
    revoke_verification,
    set_product_publication,
    submit_verification_request,
    suspend_profile,
    update_opening_hours,
    update_profile,
)
from apps.public_profiles.tests.base import PublicProfileTestMixin


class PublicProfileServiceTests(PublicProfileTestMixin):
    def test_manager_prepares_content_but_only_owner_publishes(self) -> None:
        update_profile(
            actor=self.manager,
            profile=self.profile,
            data=ProfileDraftData(
                display_name="Manager Draft",
                description="Prepared by manager",
                phone="+251911111111",
                email="",
                website="",
                address="",
                map_url="",
                supported_languages=("en",),
            ),
        )
        with self.assertRaises(PermissionDenied):
            publish_profile(actor=self.manager, profile=self.profile)
        with self.assertRaises(PermissionDenied):
            update_profile(
                actor=self.cashier,
                profile=self.profile,
                data=ProfileDraftData(
                    display_name="No",
                    description="No",
                    phone="",
                    email="",
                    website="",
                    address="",
                    map_url="",
                    supported_languages=("en",),
                ),
            )

    def test_publish_requires_readiness_and_is_idempotent(self) -> None:
        with self.assertRaises(ValidationError):
            publish_profile(actor=self.owner, profile=self.profile)
        self.complete_profile()
        first = publish_profile(actor=self.owner, profile=self.profile)
        second = publish_profile(actor=self.owner, profile=first)

        self.assertEqual(second.publication_status, PublicationStatus.PUBLISHED)
        self.assertEqual(
            second.events.filter(action=PublicProfileAction.PUBLISHED).count(),
            1,
        )

    def test_unchanged_opening_hours_do_not_duplicate_events(self) -> None:
        hours = tuple(
            OpeningHourData(
                weekday=weekday,
                is_closed=weekday == 6,
                opens_at=None if weekday == 6 else time(9),
                closes_at=None if weekday == 6 else time(18),
            )
            for weekday in range(7)
        )
        update_opening_hours(actor=self.owner, profile=self.profile, hours=hours)
        update_opening_hours(actor=self.owner, profile=self.profile, hours=hours)

        self.assertEqual(
            PublicProfileEvent.objects.filter(
                profile=self.profile,
                action=PublicProfileAction.HOURS_UPDATED,
            ).count(),
            1,
        )

    def test_product_public_identity_is_stable_and_cross_tenant_is_rejected(self) -> None:
        self.complete_profile()
        set_product_publication(
            actor=self.manager,
            profile=self.profile,
            product=self.product,
            visible=True,
            show_public_prices=True,
        )
        identity = PublicProductIdentity.objects.get(product=self.product)
        set_product_publication(
            actor=self.manager,
            profile=self.profile,
            product=self.product,
            visible=True,
            show_public_prices=True,
        )
        self.assertEqual(
            PublicProductIdentity.objects.get(product=self.product).public_id,
            identity.public_id,
        )

        other = Business.objects.create(name="Other", slug="other-public")
        foreign_product = Product.objects.create(business=other, name="Foreign")
        with self.assertRaises(ValidationError):
            set_product_publication(
                actor=self.owner,
                profile=self.profile,
                product=foreign_product,
                visible=True,
                show_public_prices=False,
            )

    def test_public_projection_omits_private_catalog_fields(self) -> None:
        self.publish()
        page = get_public_profile(self.profile.public_id)
        product = page.products[0]

        self.assertEqual(product.name, "Leather Shoe")
        self.assertFalse(hasattr(product, "sku"))
        self.assertFalse(hasattr(product, "cost_price"))
        self.assertFalse(hasattr(product, "stock"))
        self.assertEqual(product.variants[0].selling_price, Decimal("1500.00"))

    def test_aggregate_metric_increments_single_anonymous_bucket(self) -> None:
        self.publish()
        for _ in range(3):
            increment_metric(
                profile=self.profile,
                source=MetricSource.DIRECT,
                metric=MetricKind.PROFILE_VIEW,
                target_type=MetricTargetType.PROFILE,
                target_public_id=self.profile.public_id,
            )

        bucket = PublicStorefrontDailyMetric.objects.get()
        self.assertEqual(bucket.count, 3)
        field_names = {field.name for field in bucket._meta.fields}
        self.assertTrue(
            {
                "ip",
                "user_agent",
                "referrer",
                "cookie",
                "session",
                "visitor",
            }.isdisjoint(field_names)
        )

    @django_override_settings(DEBUG=False, PUBLIC_SITE_ORIGIN="http://example.com")
    def test_public_origin_fails_closed_without_https(self) -> None:
        with self.assertRaises(ValidationError):
            absolute_public_url("/p/example/")

    def test_verification_requires_permission_and_becomes_stale_on_change(self) -> None:
        self.complete_profile()
        request = submit_verification_request(
            actor=self.owner,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            reason="Review public contact",
        )
        with self.assertRaises(PermissionDenied):
            decide_verification_request(
                user=self.staff_user,
                request=request,
                approved=True,
                evidence_reference="evidence",
                private_reason="reviewed",
            )
        self.grant_staff_permissions()
        decision = decide_verification_request(
            user=self.staff_user,
            request=request,
            approved=True,
            evidence_reference="evidence",
            private_reason="reviewed",
            reviewed_on=date.today(),
            expires_on=date.today() + timedelta(days=30),
        )
        self.assertEqual(decision.action, VerificationDecisionAction.APPROVE)

        update_profile(
            actor=self.owner,
            profile=self.profile,
            data=ProfileDraftData(
                display_name=self.profile.display_name,
                description=self.profile.description,
                phone="+251922222222",
                email="",
                website=self.profile.website,
                address=self.profile.address,
                map_url="",
                supported_languages=("en",),
            ),
        )
        self.assertTrue(
            self.profile.events.filter(action=PublicProfileAction.VERIFICATION_STALE).exists()
        )

    def test_verification_renew_revoke_and_expire_are_private_lifecycle_events(self) -> None:
        self.complete_profile()
        self.grant_staff_permissions()
        request = submit_verification_request(
            actor=self.owner,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            reason="Review",
        )
        decide_verification_request(
            user=self.staff_user,
            request=request,
            approved=True,
            evidence_reference="evidence",
            private_reason="approved",
        )
        renewal = renew_verification(
            user=self.staff_user,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            evidence_reference="new evidence",
            private_reason="renewed",
        )
        revocation = revoke_verification(
            user=self.staff_user,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            private_reason="revoked",
        )
        expiry = expire_verification(
            user=self.staff_user,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            private_reason="expired",
        )

        self.assertEqual(renewal.action, VerificationDecisionAction.RENEW)
        self.assertEqual(revocation.action, VerificationDecisionAction.REVOKE)
        self.assertEqual(expiry.action, VerificationDecisionAction.EXPIRE)

    def test_suspension_requires_dedicated_permission_and_hides_public_profile(self) -> None:
        self.publish()
        with self.assertRaises(PermissionDenied):
            suspend_profile(
                user=self.staff_user,
                profile=self.profile,
                reason="Private reason",
            )
        self.grant_staff_permissions()
        suspend_profile(
            user=self.staff_user,
            profile=self.profile,
            reason="Private reason",
        )
        with self.assertRaises(self.profile.DoesNotExist):
            get_public_profile(self.profile.public_id)
