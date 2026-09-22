import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.core.checks import Error
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.checks import stage12_deployment_checks
from apps.accounts.models import User
from apps.accounts.readiness import load_recovery_attestation
from apps.accounts.views import staff_entry_url
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole


@override_settings(PUBLIC_SITE_ORIGIN="https://retail.example.test")
class StaffEntryAndSecurityTests(TestCase):
    def setUp(self) -> None:
        cache.clear()
        business = Business.objects.create(name="Entry Shop", slug="entry-shop")
        branch = Branch.objects.create(business=business, name="Main", code="main")
        self.owner = User.objects.create_user(
            email="entry-owner@example.com",
            password="strong-test-password",
        )
        BusinessMembership.objects.create(
            business=business,
            user=self.owner,
            assigned_branch=branch,
            role=MembershipRole.OWNER,
        )

    def test_staff_entry_qr_contains_only_secure_login_url(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.get(reverse("staff-entry-qr"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(staff_entry_url(), "https://retail.example.test/login/")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, "strong-test-password")

    @override_settings(PUBLIC_SITE_ORIGIN="http://retail.example.test")
    def test_staff_entry_refuses_non_https_origin(self) -> None:
        with self.assertRaises(ValidationError):
            staff_entry_url()

    def test_security_headers_are_present(self) -> None:
        response = self.client.get(reverse("login"))

        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")

    @override_settings(LOGIN_RATE_LIMIT=1, LOGIN_RATE_LIMIT_WINDOW_SECONDS=300)
    def test_login_post_is_rate_limited(self) -> None:
        payload = {"username": "missing@example.com", "password": "wrong"}

        first = self.client.post(reverse("login"), payload)
        with self.assertLogs("ife.security", level="WARNING") as captured:
            second = self.client.post(reverse("login"), payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second["Retry-After"], "300")
        self.assertIn("Security-relevant request outcome.", captured.output[0])

    def test_language_switcher_sets_selected_language(self) -> None:
        response = self.client.post(
            reverse("set_language"),
            {"language": "am", "next": reverse("login")},
        )

        self.assertRedirects(response, reverse("login"))
        self.assertEqual(response.cookies["django_language"].value, "am")

    def test_language_switcher_rejects_external_redirect(self) -> None:
        response = self.client.post(
            reverse("set_language"),
            {"language": "om", "next": "https://attacker.example/path"},
        )

        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertEqual(response.cookies["django_language"].value, "om")

    def test_shared_layout_has_skip_link_landmark_and_language_label(self) -> None:
        response = self.client.get(reverse("login"))

        self.assertContains(response, 'class="skip-link" href="#main-content"')
        self.assertContains(response, '<main class="page" id="main-content" tabindex="-1">')
        self.assertContains(response, 'for="site-language">Language</label>')


class RecoveryReadinessTests(TestCase):
    def test_recovery_attestation_command_records_required_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory, "recovery.json")
            output = StringIO()
            call_command(
                "record_recovery_attestation",
                approved_by="Named recovery operator",
                evidence_url="https://evidence.example.test/restore-2",
                database_restore_tested_at="2026-09-20T08:00:00Z",
                object_restore_tested_at="2026-09-20T08:15:00Z",
                measured_rpo_hours=1,
                measured_rto_hours=4,
                output=str(path),
                stdout=output,
            )

            attestation = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(attestation["approved_by"], "Named recovery operator")
        self.assertEqual(attestation["measured_rpo_hours"], 1.0)
        self.assertIn("Recovery attestation written", output.getvalue())

    def test_readiness_command_fails_on_blocking_check(self) -> None:
        with patch(
            "apps.accounts.management.commands.check_stage12_readiness.run_checks",
            return_value=[Error("Missing pilot approval.", id="stage12.E999")],
        ):
            with self.assertRaisesMessage(CommandError, "1 blocking check"):
                call_command("check_stage12_readiness")

    def test_readiness_command_passes_without_blocking_checks(self) -> None:
        output = StringIO()
        with patch(
            "apps.accounts.management.commands.check_stage12_readiness.run_checks",
            return_value=[],
        ):
            call_command("check_stage12_readiness", stdout=output)

        self.assertIn("Stage 12 readiness checks passed.", output.getvalue())

    def test_attestation_and_stage12_checks_accept_complete_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory, "recovery.json")
            now = timezone.now().isoformat()
            path.write_text(
                json.dumps(
                    {
                        "approved_by": "Pilot owner",
                        "evidence_url": "https://evidence.example.test/restore-1",
                        "database_restore_tested_at": now,
                        "object_restore_tested_at": now,
                        "measured_rpo_hours": "1",
                        "measured_rto_hours": "4",
                    }
                ),
                encoding="utf-8",
            )
            catalog_storage = {
                "BACKEND": "storages.backends.s3.S3Storage",
                "OPTIONS": {
                    "bucket_name": "catalog-private",
                    "querystring_auth": True,
                    "default_acl": None,
                },
            }
            with override_settings(
                PUBLIC_SITE_ORIGIN="https://retail.example.test",
                ALLOWED_HOSTS=["retail.example.test"],
                SESSION_COOKIE_SECURE=True,
                CSRF_COOKIE_SECURE=True,
                RATE_LIMIT_BACKEND_APPROVED=True,
                CACHES={
                    "default": {
                        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
                        "LOCATION": "ife_rate_limit_cache",
                    }
                },
                STORAGES={"catalog_media": catalog_storage},
                STAGE12_RECOVERY_ATTESTATION_PATH=str(path),
                STAGE12_SECURITY_REVIEW_APPROVED=True,
                STAGE12_PRIVACY_LEGAL_APPROVED=True,
                STAGE12_INCIDENT_RESPONSE_APPROVED=True,
                STAGE12_OPERATOR_TRAINING_APPROVED=True,
                STAGE12_TRANSLATION_REVIEW_APPROVED=True,
            ):
                attestation = load_recovery_attestation()
                messages = stage12_deployment_checks(None)

        self.assertEqual(attestation.approved_by, "Pilot owner")
        self.assertFalse([message for message in messages if message.is_serious()])
