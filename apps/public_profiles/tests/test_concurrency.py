from collections.abc import Callable
from queue import Queue
from threading import Barrier, Thread

from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.public_profiles.models import (
    MetricKind,
    MetricSource,
    MetricTargetType,
    PublicProfileAction,
    PublicStorefrontDailyMetric,
)
from apps.public_profiles.services import (
    ProfileDraftData,
    get_or_create_profile,
    increment_metric,
    publish_profile,
    update_profile,
)


@skipUnlessDBFeature("has_select_for_update")
@override_settings(PUBLIC_SITE_ORIGIN="https://public.example.test")
class PublicProfileConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        business = Business.objects.create(name="Concurrent Shop", slug="concurrent-shop")
        branch = Branch.objects.create(business=business, name="Main", code="main")
        user = User.objects.create_user(
            email="concurrent-owner@example.com",
            password="strong-test-password",
            full_name="Concurrent Owner",
        )
        self.owner = BusinessMembership.objects.create(
            business=business,
            user=user,
            assigned_branch=branch,
            role=MembershipRole.OWNER,
        )
        self.profile = get_or_create_profile(self.owner)
        self.profile = update_profile(
            actor=self.owner,
            profile=self.profile,
            data=ProfileDraftData(
                display_name="Concurrent Shop",
                description="Concurrent public profile",
                phone="+251900000000",
                email="",
                website="",
                address="",
                map_url="",
                supported_languages=("en",),
            ),
        )

    def _run_threads(self, action: Callable[[], None], count: int) -> list[BaseException]:
        barrier = Barrier(count)
        errors: Queue[BaseException] = Queue()

        def worker() -> None:
            connections.close_all()
            try:
                barrier.wait()
                action()
            except BaseException as exc:
                errors.put(exc)
            finally:
                connections.close_all()

        threads = [Thread(target=worker) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return list(errors.queue)

    def test_concurrent_metric_increments_are_not_lost(self) -> None:
        def action() -> None:
            increment_metric(
                profile=self.profile,
                source=MetricSource.QR,
                metric=MetricKind.PROFILE_VIEW,
                target_type=MetricTargetType.PROFILE,
                target_public_id=self.profile.public_id,
            )

        errors = self._run_threads(action, 8)

        self.assertEqual(errors, [])
        self.assertEqual(PublicStorefrontDailyMetric.objects.get().count, 8)

    def test_concurrent_publish_is_idempotent(self) -> None:
        def action() -> None:
            publish_profile(actor=self.owner, profile=self.profile)

        errors = self._run_threads(action, 2)

        self.assertEqual(errors, [])
        self.assertEqual(
            self.profile.events.filter(action=PublicProfileAction.PUBLISHED).count(),
            1,
        )
