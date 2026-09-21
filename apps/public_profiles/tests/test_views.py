import uuid
from datetime import date
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.catalog.models import ProductVariant, StockUnit
from apps.inventory.services import post_opening_balance
from apps.public_profiles.models import (
    PublicationStatus,
    PublicReturnReceiptIdentity,
    PublicSaleReceiptIdentity,
    PublicStorefrontDailyMetric,
    VerificationType,
)
from apps.public_profiles.services import (
    decide_verification_request,
    submit_verification_request,
)
from apps.public_profiles.tests.base import PublicProfileTestMixin
from apps.sales.models import SalePaymentMethod, SaleReturnPurpose
from apps.sales.services import (
    ReturnQuantity,
    SaleQuantity,
    post_sale,
    post_sale_return,
    save_sale_draft,
    save_sale_return_draft,
)


class PublicProfileViewTests(PublicProfileTestMixin):
    def test_owner_and_manager_can_manage_but_cashier_and_stock_cannot(self) -> None:
        for user in (self.owner_user, self.manager_user):
            with self.subTest(user=user.email):
                client = Client()
                client.force_login(user)
                response = client.get(reverse("public_profiles:manage"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Public profile and storefront")
        for user in (self.cashier_user, self.stock_user):
            with self.subTest(user=user.email):
                client = Client()
                client.force_login(user)
                response = client.get(reverse("public_profiles:manage"))
                self.assertEqual(response.status_code, 403)

    def test_manager_page_has_no_owner_publication_controls(self) -> None:
        self.client.force_login(self.manager_user)
        response = self.client.get(reverse("public_profiles:manage"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, reverse("public_profiles:publish"))
        self.assertNotContains(response, reverse("public_profiles:unpublish"))
        self.assertContains(response, "Only an owner may publish")

    def test_owner_sees_search_indexing_cache_disclosure(self) -> None:
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("public_profiles:manage"))

        self.assertContains(
            response,
            "Search engines and third-party caches may retain earlier copies after unpublishing.",
        )
        self.assertContains(response, "Removal is not instantaneous")

    def test_anonymous_read_only_endpoints_reject_post(self) -> None:
        identifier = uuid.uuid4()
        urls = (
            reverse("public_profiles:public-profile", args=(identifier,)),
            reverse(
                "public_profiles:public-product",
                args=(identifier, identifier),
            ),
            reverse("public_profiles:verify-sale-receipt", args=(identifier,)),
            reverse("public_profiles:verify-return-receipt", args=(identifier,)),
            reverse("public_profiles:sale-receipt-qr", args=(identifier,)),
            reverse("public_profiles:return-receipt-qr", args=(identifier,)),
            reverse("public_profiles:robots"),
            reverse("public_profiles:sitemap"),
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_public_page_posts_do_not_increment_open_metrics(self) -> None:
        self.publish()
        profile_url = reverse(
            "public_profiles:public-profile",
            args=(self.profile.public_id,),
        )
        product_url = reverse(
            "public_profiles:public-product",
            args=(self.profile.public_id, self.product.public_identity.public_id),
        )

        self.assertEqual(self.client.post(profile_url).status_code, 405)
        self.assertEqual(self.client.post(product_url).status_code, 405)
        self.assertFalse(PublicStorefrontDailyMetric.objects.exists())

    def test_authenticated_read_only_endpoints_reject_post(self) -> None:
        self.publish()
        self.client.force_login(self.owner_user)
        urls = (
            reverse("public_profiles:manage"),
            reverse("public_profiles:preview"),
            reverse("public_profiles:profile-qr"),
            reverse("public_profiles:poster"),
            reverse("public_profiles:product-qr", args=(self.product.id,)),
        )

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)

    def test_public_profile_is_generic_not_found_for_all_ineligible_states(self) -> None:
        url = reverse("public_profiles:public-profile", args=(self.profile.public_id,))
        for status, active, suspended in (
            (PublicationStatus.DRAFT, True, False),
            (PublicationStatus.UNPUBLISHED, True, False),
            (PublicationStatus.PUBLISHED, False, False),
            (PublicationStatus.PUBLISHED, True, True),
        ):
            with self.subTest(status=status, active=active, suspended=suspended):
                self.profile.publication_status = status
                self.profile.is_suspended = suspended
                self.profile.suspended_at = self.profile.updated_at if suspended else None
                self.profile.suspension_reason = "private" if suspended else ""
                self.profile.suspended_by = self.staff_user if suspended else None
                self.profile.save(
                    update_fields=(
                        "publication_status",
                        "is_suspended",
                        "suspended_at",
                        "suspension_reason",
                        "suspended_by",
                    )
                )
                self.business.is_active = active
                self.business.save(update_fields=("is_active",))
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)

    def test_public_profile_has_safe_metadata_and_private_data_denylist(self) -> None:
        self.publish()
        response = self.client.get(
            reverse("public_profiles:public-profile", args=(self.profile.public_id,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Public Test Shop")
        self.assertContains(response, 'property="og:title"', html=False)
        self.assertEqual(response["X-Robots-Tag"], "noindex")
        self.assertEqual(response["Cache-Control"], "no-store")
        for private_value in (
            "PRIVATE-SKU-42",
            "487.65",
            self.branch.name,
            self.owner_user.email,
            "low_stock_threshold",
        ):
            self.assertNotContains(response, private_value)
        metric = PublicStorefrontDailyMetric.objects.get()
        self.assertEqual(metric.count, 1)

    def test_public_product_without_price_hides_price_and_private_fields(self) -> None:
        self.publish(prices=False)
        identity = self.product.public_identity
        response = self.client.get(
            reverse(
                "public_profiles:public-product",
                args=(self.profile.public_id, identity.public_id),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contact business")
        self.assertNotContains(response, "1500.00")
        self.assertNotContains(response, "PRIVATE-SKU-42")
        self.assertNotContains(response, "487.65")

    def test_indexing_controls_robots_header_and_sitemap(self) -> None:
        self.publish()
        url = reverse("public_profiles:public-profile", args=(self.profile.public_id,))
        sitemap = self.client.get(reverse("public_profiles:sitemap"))
        self.assertNotContains(sitemap, str(self.profile.public_id))

        self.profile.allow_search_indexing = True
        self.profile.save(update_fields=("allow_search_indexing",))
        response = self.client.get(url)
        sitemap = self.client.get(reverse("public_profiles:sitemap"))
        robots = self.client.get(reverse("public_profiles:robots"))

        self.assertEqual(response["X-Robots-Tag"], "index, follow")
        self.assertContains(sitemap, str(self.profile.public_id))
        self.assertContains(robots, "Sitemap:")

    def test_staff_status_alone_does_not_grant_platform_access(self) -> None:
        self.client.force_login(self.staff_user)
        queue = self.client.get(reverse("public_profiles:staff-queue"))

        self.assertEqual(queue.status_code, 403)

    def test_platform_permission_boundaries_are_independent(self) -> None:
        self.grant_staff_permission("suspend_public_profile")
        self.client.force_login(self.staff_user)
        queue = self.client.get(reverse("public_profiles:staff-queue"))

        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, "Suspend exposure")
        self.assertNotContains(queue, "Pending verification requests")
        review_url = reverse(
            "public_profiles:staff-verification-lifecycle",
            args=(self.profile.id,),
        )
        self.assertEqual(self.client.post(review_url).status_code, 403)

    def test_management_posts_enforce_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner_user)
        response = client.post(reverse("public_profiles:publish"))

        self.assertEqual(response.status_code, 403)

    def test_qr_is_svg_and_contains_no_private_catalog_value(self) -> None:
        self.publish()
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("public_profiles:product-qr", args=(self.product.id,)))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/svg+xml")
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertNotIn(b"PRIVATE-SKU-42", response.content)
        self.assertNotIn(b"487.65", response.content)

    def test_public_verification_shows_only_specific_indicator_metadata(self) -> None:
        self.publish()
        self.grant_staff_permission("review_public_verification")
        request = submit_verification_request(
            actor=self.owner,
            profile=self.profile,
            indicator_type=VerificationType.CONTACT,
            reason="Private owner request",
        )
        decide_verification_request(
            user=self.staff_user,
            request=request,
            approved=True,
            evidence_reference="PRIVATE-EVIDENCE",
            private_reason="PRIVATE-STAFF-REASON",
        )
        response = self.client.get(
            reverse("public_profiles:public-profile", args=(self.profile.public_id,))
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contact reviewed")
        self.assertNotContains(response, "PRIVATE-EVIDENCE")
        self.assertNotContains(response, "PRIVATE-STAFF-REASON")
        self.assertNotContains(response, self.staff_user.email)

    def test_receipt_identity_is_created_atomically_and_public_view_is_minimal(self) -> None:
        self.publish()
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=self.variant,
            quantity=Decimal("3"),
            unit_cost=Decimal("500"),
            idempotency_key=uuid.uuid4(),
        )
        sale = save_sale_draft(
            actor=self.owner,
            branch=self.branch,
            sale_date=date.today(),
            quantities=[SaleQuantity(self.variant.id, Decimal("1"))],
        )
        posted_sale = post_sale(
            actor=self.owner,
            sale=sale,
            payment_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference="PRIVATE-PAYMENT-REFERENCE",
            idempotency_key=uuid.uuid4(),
        )
        receipt = posted_sale.receipt
        identity = PublicSaleReceiptIdentity.objects.get(receipt=receipt)
        response = self.client.get(
            reverse(
                "public_profiles:verify-sale-receipt",
                args=(identity.public_token,),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, receipt.internal_number)
        for private_value in (
            "PRIVATE-PAYMENT-REFERENCE",
            "PRIVATE-SKU-42",
            "1500.00",
            self.branch.name,
            self.owner_user.email,
        ):
            self.assertNotContains(response, private_value)
        sale_return = save_sale_return_draft(
            actor=self.owner,
            sale=posted_sale,
            purpose=SaleReturnPurpose.CUSTOMER_RETURN,
            return_date=date.today(),
            reason="Size exchange",
            quantities=[
                ReturnQuantity(
                    sale_line_id=posted_sale.lines.get().id,
                    quantity=Decimal("1"),
                )
            ],
        )
        posted_return = post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference="PRIVATE-REFUND-REFERENCE",
            idempotency_key=uuid.uuid4(),
        )
        return_identity = PublicReturnReceiptIdentity.objects.get(receipt=posted_return.receipt)
        return_response = self.client.get(
            reverse(
                "public_profiles:verify-return-receipt",
                args=(return_identity.public_token,),
            )
        )
        self.assertEqual(return_response.status_code, 200)
        self.assertNotContains(return_response, "PRIVATE-REFUND-REFERENCE")
        self.assertNotContains(return_response, "PRIVATE-SKU-42")

    def test_public_product_shows_price_only_when_enabled(self) -> None:
        self.publish(prices=True)
        ProductVariant.objects.create(
            business=self.business,
            product=self.product,
            sku="PRIVATE-SKU-43",
            size="43",
            color="Black",
            selling_price=Decimal("1600.00"),
            cost_price=Decimal("500.00"),
            stock_unit=StockUnit.PAIR,
        )
        identity = self.product.public_identity
        response = self.client.get(
            reverse(
                "public_profiles:public-product",
                args=(self.profile.public_id, identity.public_id),
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1500.00")
        self.assertContains(response, "1600.00")
        self.assertNotContains(response, "PRIVATE-SKU-42")
        self.assertNotContains(response, "PRIVATE-SKU-43")
        self.assertNotContains(response, "487.65")
