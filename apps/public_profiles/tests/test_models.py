from datetime import date, time

from django.core.exceptions import ValidationError

from apps.public_profiles.models import (
    ActorKind,
    PublicOpeningHour,
    PublicProfileAction,
    PublicProfileEvent,
    PublicVerificationDecision,
    PublicVerificationRequest,
    VerificationDecisionAction,
    VerificationType,
)
from apps.public_profiles.tests.base import PublicProfileTestMixin


class PublicProfileModelTests(PublicProfileTestMixin):
    def test_profile_requires_https_public_links(self) -> None:
        self.profile.website = "http://example.com"
        self.profile.map_url = "http://maps.example.com"

        with self.assertRaises(ValidationError) as context:
            self.profile.full_clean()

        self.assertIn("website", context.exception.message_dict)
        self.assertIn("map_url", context.exception.message_dict)

    def test_opening_hour_rejects_incomplete_or_reversed_range(self) -> None:
        hour = PublicOpeningHour(
            business=self.business,
            profile=self.profile,
            weekday=0,
            opens_at=time(17),
            closes_at=time(9),
        )
        with self.assertRaises(ValidationError):
            hour.full_clean()

    def test_profile_events_are_immutable(self) -> None:
        event = PublicProfileEvent.objects.create(
            business=self.business,
            profile=self.profile,
            action=PublicProfileAction.PROFILE_UPDATED,
            actor_kind=ActorKind.MEMBERSHIP,
            actor_membership=self.owner,
        )
        event.details = {"changed": True}
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_verification_decisions_are_immutable(self) -> None:
        request = PublicVerificationRequest.objects.create(
            business=self.business,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            requester=self.owner,
            request_reason="Review contact",
        )
        decision = PublicVerificationDecision.objects.create(
            business=self.business,
            profile=self.profile,
            request=request,
            indicator_type=VerificationType.CONTACT,
            action=VerificationDecisionAction.APPROVE,
            staff_user=self.staff_user,
            evidence_reference="private-reference",
            private_reason="private note",
            public_reviewed_on=date.today(),
            subject_fingerprint="fingerprint",
        )
        decision.private_reason = "changed"
        with self.assertRaises(ValidationError):
            decision.save()
        with self.assertRaises(ValidationError):
            decision.delete()
