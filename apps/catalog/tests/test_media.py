from io import BytesIO
from pathlib import Path

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.media import prepare_product_image
from apps.catalog.models import (
    Product,
    ProductImageAction,
    ProductImageEvent,
    ProductVariant,
)
from apps.catalog.services import (
    purge_orphaned_catalog_media,
    reconcile_catalog_media,
    set_product_image,
)
from apps.catalog.tests.media_support import (
    IsolatedCatalogMediaMixin,
    image_upload,
)


class ProductMediaTests(IsolatedCatalogMediaMixin, TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.business = Business.objects.create(name="Media Shop", slug="media-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=User.objects.create_user(
                email="media-owner@example.com",
                password="strong-test-password",
            ),
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier = BusinessMembership.objects.create(
            business=self.business,
            user=User.objects.create_user(
                email="media-cashier@example.com",
                password="strong-test-password",
            ),
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.product = Product.objects.create(
            business=self.business,
            name="Leather Shoe",
        )

    def test_product_image_is_reencoded_and_metadata_is_stripped(self) -> None:
        prepared = prepare_product_image(image_upload(metadata=True))

        self.assertEqual(prepared.media_type, "image/webp")
        with Image.open(BytesIO(prepared.content)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertNotIn("Comment", image.info)

    def test_animated_image_is_rejected(self) -> None:
        output = BytesIO()
        frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue")]
        frames[0].save(output, "WEBP", save_all=True, append_images=frames[1:])
        upload = SimpleUploadedFile(
            "animated.webp",
            output.getvalue(),
            content_type="image/webp",
        )

        with self.assertRaises(ValidationError):
            prepare_product_image(upload)

    def test_malformed_and_mismatched_images_are_rejected(self) -> None:
        malformed = SimpleUploadedFile(
            "broken.png",
            b"not-an-image",
            content_type="image/png",
        )
        mismatched = image_upload(name="product.jpg")

        with self.assertRaisesMessage(ValidationError, "valid, readable image"):
            prepare_product_image(malformed)
        with self.assertRaisesMessage(ValidationError, "does not match"):
            prepare_product_image(mismatched)

    def test_image_write_is_tenant_scoped_and_audited(self) -> None:
        image = set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="Navy leather shoe",
        )

        self.assertEqual(image.business, self.business)
        self.assertTrue(Path(self.media_tempdir.name, image.source.name).exists())
        event = ProductImageEvent.objects.get()
        self.assertEqual(event.action, ProductImageAction.ADDED)
        with self.assertRaises(PermissionDenied):
            set_product_image(
                actor=self.cashier,
                product=self.product,
                upload=image_upload(color="red"),
                alt_text="Red shoe",
            )

    def test_replacement_keeps_immutable_evidence_and_one_current_image(self) -> None:
        first = set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="First shoe",
        )
        with self.captureOnCommitCallbacks(execute=True):
            second = set_product_image(
                actor=self.owner,
                product=self.product,
                upload=image_upload(color="red"),
                alt_text="Second shoe",
            )

        first.refresh_from_db()
        self.assertIsNotNone(first.removed_at)
        self.assertFalse(Path(self.media_tempdir.name, first.source.name).exists())
        self.assertTrue(Path(self.media_tempdir.name, second.source.name).exists())
        self.assertEqual(
            ProductImageEvent.objects.filter(action=ProductImageAction.REPLACED).count(),
            1,
        )

    def test_reconciliation_detects_and_dry_runs_orphan_cleanup(self) -> None:
        orphan = Path(self.media_tempdir.name, "products", "orphan.webp")
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")

        report = reconcile_catalog_media()
        self.assertEqual(report.orphaned, ("products/orphan.webp",))
        purge_orphaned_catalog_media(names=report.orphaned, dry_run=True)
        self.assertTrue(orphan.exists())
        purge_orphaned_catalog_media(names=report.orphaned, dry_run=False)
        self.assertFalse(orphan.exists())

    def test_reconciliation_detects_missing_and_hash_mismatched_objects(self) -> None:
        missing = set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="Missing image",
        )
        missing_path = Path(self.media_tempdir.name, missing.source.name)
        missing_path.unlink()

        other_product = Product.objects.create(
            business=self.business,
            name="Canvas Shoe",
        )
        mismatched = set_product_image(
            actor=self.owner,
            product=other_product,
            upload=image_upload(color="red"),
            alt_text="Changed image",
        )
        Path(self.media_tempdir.name, mismatched.source.name).write_bytes(b"changed")

        report = reconcile_catalog_media()

        self.assertEqual(report.missing, (missing.source.name,))
        self.assertEqual(report.hash_mismatches, (mismatched.source.name,))

    def test_internal_delivery_is_same_business_only(self) -> None:
        image = set_product_image(
            actor=self.owner,
            product=self.product,
            upload=image_upload(),
            alt_text="Navy shoe",
        )
        self.client.force_login(self.owner.user)

        response = self.client.get(reverse("catalog:product-image", args=[image.id]))

        self.assertEqual(response.status_code, 200)
        other_business = Business.objects.create(name="Other", slug="other-media")
        other_user = User.objects.create_user(
            email="other-media@example.com",
            password="strong-test-password",
        )
        BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            role=MembershipRole.OWNER,
        )
        self.client.force_login(other_user)
        self.assertEqual(
            self.client.get(reverse("catalog:product-image", args=[image.id])).status_code,
            404,
        )

    def test_cashier_catalog_cards_do_not_show_reference_cost(self) -> None:
        ProductVariant.objects.create(
            business=self.business,
            product=self.product,
            sku="SHOE-42",
            selling_price="1500.00",
            cost_price="700.00",
        )
        self.client.force_login(self.cashier.user)

        response = self.client.get(reverse("catalog:product-list"))

        self.assertContains(response, "ETB 1500.00")
        self.assertNotContains(response, "Reference cost")
        self.assertNotContains(response, "700.00")
