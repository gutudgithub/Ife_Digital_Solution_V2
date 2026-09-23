from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.catalog.services import set_product_image
from apps.catalog.tests.media_support import IsolatedCatalogMediaMixin, image_upload
from apps.public_profiles.models import PublicStorefrontDailyMetric
from apps.public_profiles.services import unpublish_profile
from apps.public_profiles.tests.base import PublicProfileTestMixin


class PublicProductMediaTests(IsolatedCatalogMediaMixin, PublicProfileTestMixin):
    def test_published_product_image_is_public_without_exposing_private_data(self) -> None:
        image = set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="Brown leather shoe",
        )
        self.publish()

        page_response = self.client.get(
            reverse(
                "public_profiles:public-profile",
                args=[self.profile.public_id],
            )
        )
        image_response = self.client.get(
            reverse(
                "public_profiles:public-product-image",
                args=[self.profile.public_id, self.product.public_identity.public_id],
            )
        )

        self.assertEqual(image_response.status_code, 200)
        self.assertEqual(image_response["Cache-Control"], "no-store")
        self.assertContains(page_response, "Brown leather shoe")
        self.assertNotContains(page_response, self.variant.sku)
        self.assertNotContains(page_response, "487.65")
        self.assertContains(page_response, 'class="skip-link" href="#main-content"')
        self.assertContains(
            page_response,
            '<main class="page public-page" id="main-content" tabindex="-1">',
        )
        self.assertEqual(image.product, self.product)

    def test_unpublished_profile_image_fails_generically(self) -> None:
        set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="Brown leather shoe",
        )
        self.publish()
        unpublish_profile(actor=self.owner, profile=self.profile)

        response = self.client.get(
            reverse(
                "public_profiles:public-product-image",
                args=[self.profile.public_id, self.product.public_identity.public_id],
            )
        )

        self.assertEqual(response.status_code, 404)

    @override_settings(PUBLIC_READ_RATE_LIMIT=1, PUBLIC_READ_RATE_LIMIT_WINDOW_SECONDS=60)
    def test_public_read_rate_limit_blocks_repeated_metric_inflation(self) -> None:
        cache.clear()
        self.publish()
        url = reverse("public_profiles:public-profile", args=[self.profile.public_id])

        first = self.client.get(url)
        second = self.client.get(url)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(PublicStorefrontDailyMetric.objects.get().count, 1)
