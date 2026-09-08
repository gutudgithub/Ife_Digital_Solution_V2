from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.businesses.models import Business, BusinessMembership, MembershipRole


class ActiveBusinessMiddlewareTests(TestCase):
    user: User
    first_business: Business
    second_business: Business

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="strong-test-password",
            full_name="Pilot Owner",
        )
        self.first_business = Business.objects.create(name="First Shop", slug="first-shop")
        self.second_business = Business.objects.create(name="Second Shop", slug="second-shop")
        BusinessMembership.objects.create(
            business=self.first_business,
            user=self.user,
            role=MembershipRole.OWNER,
        )
        BusinessMembership.objects.create(
            business=self.second_business,
            user=self.user,
            role=MembershipRole.MANAGER,
        )
        self.client.force_login(self.user)

    def test_dashboard_uses_membership_selected_in_session(self) -> None:
        session = self.client.session
        session["active_business_id"] = str(self.second_business.id)
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.second_business.name)
        self.assertNotContains(response, self.first_business.name)

    def test_invalid_session_business_falls_back_to_first_membership(self) -> None:
        unavailable_business = Business.objects.create(name="Unavailable", slug="unavailable")
        session = self.client.session
        session["active_business_id"] = str(unavailable_business.id)
        session.save()

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.first_business.name)
        self.assertNotContains(response, unavailable_business.name)


class BusinessAccessTests(TestCase):
    def test_authenticated_user_without_membership_is_denied(self) -> None:
        user = User.objects.create_user(
            email="unassigned@example.com",
            password="strong-test-password",
            full_name="Unassigned User",
        )
        client = Client()
        client.force_login(user)

        response = client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 403)
