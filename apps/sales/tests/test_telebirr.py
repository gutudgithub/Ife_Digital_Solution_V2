from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant
from apps.catalog.tests.media_support import IsolatedCatalogMediaMixin, qr_upload
from apps.sales.models import BranchTelebirrEvent, BranchTelebirrProfile
from apps.sales.services import SaleQuantity, save_sale_draft
from apps.sales.telebirr import (
    activate_telebirr_profile,
    configure_telebirr_profile,
    deactivate_telebirr_profile,
    remove_telebirr_profile,
)


class TelebirrProfileTests(IsolatedCatalogMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.business = Business.objects.create(name="Telebirr Shop", slug="telebirr-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner = self._membership("owner", MembershipRole.OWNER)
        self.manager = self._membership("manager", MembershipRole.MANAGER)
        self.cashier = self._membership("cashier", MembershipRole.CASHIER)

    def _membership(self, label: str, role: str) -> BusinessMembership:
        user = User.objects.create_user(
            email=f"telebirr-{label}@example.com",
            password="strong-test-password",
        )
        return BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=role,
        )

    def _profile(self) -> BranchTelebirrProfile:
        return configure_telebirr_profile(
            actor=self.owner,
            branch=self.branch,
            merchant_display_name="Ife Main",
            merchant_identifier="MERCHANT-100",
            upload=qr_upload(),
        )

    def test_only_owner_can_configure_and_activate_profile(self) -> None:
        with self.assertRaises(PermissionDenied):
            configure_telebirr_profile(
                actor=self.manager,
                branch=self.branch,
                merchant_display_name="Ife Main",
                merchant_identifier="MERCHANT-100",
                upload=qr_upload(),
            )

        profile = self._profile()
        self.assertFalse(profile.is_active)
        activated = activate_telebirr_profile(actor=self.owner, profile=profile)
        self.assertTrue(activated.is_active)
        self.assertEqual(activated.confirmed_by, self.owner)
        self.assertEqual(BranchTelebirrEvent.objects.count(), 2)

    def test_event_is_immutable(self) -> None:
        self._profile()
        event = BranchTelebirrEvent.objects.get()
        event.merchant_identifier = "changed"

        with self.assertRaises(ValidationError):
            event.save()

    def test_owner_can_deactivate_and_remove_profile_with_audit_evidence(self) -> None:
        profile = self._profile()
        activate_telebirr_profile(actor=self.owner, profile=profile)

        deactivated = deactivate_telebirr_profile(actor=self.owner, profile=profile)
        self.assertFalse(deactivated.is_active)
        with self.captureOnCommitCallbacks(execute=True):
            removed = remove_telebirr_profile(actor=self.owner, profile=profile)

        self.assertIsNotNone(removed.removed_at)
        self.assertFalse(removed.qr_source.storage.exists(removed.qr_source.name))
        self.assertEqual(BranchTelebirrEvent.objects.filter(profile=profile).count(), 4)

    def test_cashier_sees_active_qr_exact_amount_and_non_verification_warning(self) -> None:
        profile = self._profile()
        activate_telebirr_profile(actor=self.owner, profile=profile)
        product = Product.objects.create(business=self.business, name="Running Shoe")
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="RUN-42",
            selling_price=Decimal("1800.00"),
        )
        sale = save_sale_draft(
            actor=self.cashier,
            branch=self.branch,
            sale_date=timezone.localdate(),
            quantities=[SaleQuantity(variant.id, Decimal("2"))],
        )
        self.client.force_login(self.cashier.user)

        response = self.client.get(reverse("sales:sale-post", args=[sale.id]))

        self.assertContains(response, reverse("sales:telebirr-qr", args=[profile.id]))
        self.assertContains(response, "Customer must pay exactly ETB 3600.00")
        self.assertContains(response, "does not confirm payment")

    def test_manager_cannot_open_owner_configuration_page(self) -> None:
        self.client.force_login(self.manager.user)

        response = self.client.get(reverse("sales:telebirr-settings"))

        self.assertEqual(response.status_code, 403)

    def test_only_owner_can_preview_inactive_qr(self) -> None:
        profile = self._profile()
        qr_url = reverse("sales:telebirr-qr", args=[profile.id])

        self.client.force_login(self.cashier.user)
        self.assertEqual(self.client.get(qr_url).status_code, 404)

        self.client.force_login(self.owner.user)
        owner_response = self.client.get(qr_url)
        self.assertEqual(owner_response.status_code, 200)

        activate_telebirr_profile(actor=self.owner, profile=profile)
        self.client.force_login(self.cashier.user)
        cashier_response = self.client.get(qr_url)
        self.assertEqual(cashier_response.status_code, 200)

        deactivate_telebirr_profile(actor=self.owner, profile=profile)
        self.assertEqual(self.client.get(qr_url).status_code, 404)

    def test_visual_sale_picker_preserves_server_form_and_hides_cost(self) -> None:
        product = Product.objects.create(business=self.business, name="Canvas Shoe")
        variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CANVAS-41",
            size="41",
            selling_price=Decimal("1200.00"),
            cost_price=Decimal("500.00"),
        )
        self.client.force_login(self.cashier.user)

        response = self.client.get(reverse("sales:sale-create"))

        self.assertContains(response, "Visual product picker")
        self.assertContains(response, f'data-sale-picker-variant="{variant.id}"')
        self.assertContains(response, 'name="lines-0-variant"')
        self.assertNotContains(response, "500.00")
