from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


class UserManagerTests(TestCase):
    def test_create_user_normalizes_email_and_uses_email_for_login(self) -> None:
        user = User.objects.create_user(
            email="Owner@EXAMPLE.COM",
            password="strong-test-password",
            full_name="Pilot Owner",
        )

        self.assertEqual(user.email, "Owner@example.com")
        self.assertEqual(user.get_username(), "Owner@example.com")
        self.assertTrue(user.check_password("strong-test-password"))

    def test_create_superuser_sets_required_permissions(self) -> None:
        user = User.objects.create_superuser(
            email="admin@example.com",
            password="strong-test-password",
            full_name="Platform Admin",
        )

        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_superuser_can_open_custom_user_add_admin(self) -> None:
        user = User.objects.create_superuser(
            email="admin@example.com",
            password="strong-test-password",
            full_name="Platform Admin",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("admin:accounts_user_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Email address")
        self.assertContains(response, "Full name")
