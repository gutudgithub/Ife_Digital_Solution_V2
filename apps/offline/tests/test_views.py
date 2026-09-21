import json
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.offline.models import OfflineSaleSync
from apps.sales.models import InternalReceipt, Sale, SalePayment


class OfflineSaleViewTests(TestCase):
    cashier: User
    stock_employee: User
    business: Business
    branch: Branch
    other_branch: Branch
    variant: ProductVariant

    def setUp(self) -> None:
        self.cashier = User.objects.create_user(
            email="offline-view-cashier@example.com",
            password="strong-test-password",
            full_name="Offline View Cashier",
        )
        self.stock_employee = User.objects.create_user(
            email="offline-view-stock@example.com",
            password="strong-test-password",
            full_name="Offline View Stock",
        )
        self.business = Business.objects.create(
            name="Offline View Shop",
            slug="offline-view-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.other_branch = Branch.objects.create(
            business=self.business,
            name="Other",
            code="other",
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.stock_employee,
            assigned_branch=self.branch,
            role=MembershipRole.STOCK_EMPLOYEE,
        )
        product = Product.objects.create(business=self.business, name="Offline Jacket")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="OFF-JACKET",
            size="M",
            color="Blue",
            selling_price=Decimal("1200.00"),
            cost_price=Decimal("450.00"),
            stock_unit=StockUnit.PIECE,
        )

    def _payload(self, **changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "local_draft_id": str(uuid.uuid4()),
            "idempotency_key": str(uuid.uuid4()),
            "business_id": str(self.business.id),
            "branch_id": str(self.branch.id),
            "drafted_by_id": str(self.cashier_membership.id),
            "role_at_draft": MembershipRole.CASHIER,
            "offline_created_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            "payment_method": "cash",
            "telebirr_reference": "",
            "lines": [
                {
                    "variant_id": str(self.variant.id),
                    "quantity": "1",
                    "selling_price": "1200.00",
                    "product_name": self.variant.product.name,
                    "variant_label": str(self.variant),
                }
            ],
        }
        payload.update(changes)
        return payload

    def test_manifest_service_worker_and_offline_fallback_are_safe_read_only(self) -> None:
        self.client.force_login(self.cashier)
        manifest = self.client.get(reverse("offline:manifest"))
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest["Content-Type"], "application/manifest+json")
        self.assertEqual(manifest.json()["display"], "standalone")
        self.assertEqual(manifest.json()["scope"], "/offline/")
        self.assertEqual(
            manifest.json()["start_url"],
            f"{reverse('offline:sales')}?business={self.business.id}",
        )
        self.assertContains(manifest, "/static/img/ife-app-icon.svg")

        worker = self.client.get(reverse("offline:service-worker"))
        self.assertEqual(worker.status_code, 200)
        self.assertEqual(worker["Service-Worker-Allowed"], "/offline/")
        self.assertContains(worker, 'request.method !== "GET"')
        self.assertContains(worker, 'response.headers.get("X-Ife-Offline-Cache")')
        self.assertContains(worker, 'const CACHE_NAME = "ife-stage-9a-v2"')
        self.assertContains(worker, "fetch(request)")
        self.assertNotContains(worker, "/documents/")
        self.assertNotContains(worker, "/performance/")
        self.assertNotContains(worker, "/receipts/")

        fallback = self.client.get(reverse("offline:unavailable"))
        self.assertEqual(fallback["X-Ife-Offline-Cache"], "public-shell")
        self.assertContains(fallback, "not available offline")

        for name in ("offline:manifest", "offline:service-worker", "offline:unavailable"):
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)

    def test_cashier_screen_has_provisional_disclaimers_and_pwa_metadata(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.get(
            reverse("offline:sales"),
            {"business": str(self.business.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Ife-Offline-Cache"], "private-shell")
        self.assertEqual(response["X-Ife-Offline-Membership"], str(self.cashier_membership.id))
        self.assertContains(response, reverse("offline:manifest"))
        self.assertContains(response, 'crossorigin="use-credentials"')
        self.assertContains(response, f'data-membership-id="{self.cashier_membership.id}"')
        self.assertContains(
            response, f'data-service-worker-url="{reverse("offline:service-worker")}"'
        )
        self.assertContains(response, "Offline sales drafts")
        self.assertContains(response, "Draft only — not a receipt or completed sale.")
        self.assertContains(response, "does not move inventory")
        self.assertContains(response, "PROVISIONAL OFFLINE NOTE")
        self.assertContains(response, str(self.branch.id))
        self.assertNotContains(response, "450.00")

    def test_offline_screen_rejects_another_business_identity(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.get(
            reverse("offline:sales"),
            {"business": str(uuid.uuid4())},
        )

        self.assertEqual(response.status_code, 403)

    def test_catalog_snapshot_contains_only_authorized_sale_fields(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.get(
            reverse("offline:catalog"),
            {"branch": str(self.branch.id)},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        variant = response.json()["variants"][0]
        self.assertEqual(
            set(variant),
            {"id", "product_name", "label", "selling_price", "stock_unit"},
        )
        serialized = response.content.decode()
        self.assertNotIn("cost", serialized)
        self.assertNotIn("inventory", serialized)
        self.assertNotIn("450.00", serialized)

    def test_stock_employee_cannot_open_screen_catalog_or_sync(self) -> None:
        client = Client()
        client.force_login(self.stock_employee)

        self.assertEqual(
            client.get(
                reverse("offline:sales"),
                {"business": str(self.business.id)},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.get(
                reverse("offline:catalog"),
                {"branch": str(self.branch.id)},
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post(
                reverse("offline:sync-sale"),
                data=json.dumps(self._payload()),
                content_type="application/json",
            ).status_code,
            403,
        )

    def test_sync_endpoint_creates_draft_without_ledger_evidence(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.post(
            reverse("offline:sync-sale"),
            data=json.dumps(self._payload()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "synced")
        sale = Sale.objects.get()
        self.assertEqual(response.json()["sale_id"], str(sale.id))
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(InternalReceipt.objects.exists())
        self.assertEqual(OfflineSaleSync.objects.get().sale, sale)

    def test_malformed_json_and_payload_return_bounded_errors(self) -> None:
        self.client.force_login(self.cashier)

        malformed = self.client.post(
            reverse("offline:sync-sale"),
            data="{",
            content_type="application/json",
        )
        missing = self.client.post(
            reverse("offline:sync-sale"),
            data=json.dumps({"lines": []}),
            content_type="application/json",
        )

        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json(), {"errors": ["Enter valid JSON."]})
        self.assertEqual(missing.status_code, 400)
        self.assertIn("must be a UUID", missing.json()["errors"][0])
        self.assertNotIn("Traceback", malformed.content.decode())

    def test_cashier_cross_branch_submission_is_rejected_without_evidence(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.post(
            reverse("offline:sync-sale"),
            data=json.dumps(self._payload(branch_id=str(self.other_branch.id))),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "cannot synchronize", status_code=400)
        self.assertFalse(Sale.objects.exists())
        self.assertFalse(OfflineSaleSync.objects.exists())

    def test_logout_requests_cache_clearing_without_destroying_indexeddb(self) -> None:
        self.client.force_login(self.cashier)

        response = self.client.post(reverse("logout"))

        self.assertEqual(response["Clear-Site-Data"], '"cache"')

    def test_offline_queue_script_uses_manual_versioned_indexeddb_storage(self) -> None:
        script_path = finders.find("js/offline-sales.js")
        assert isinstance(script_path, str)
        source = Path(script_path).read_text(encoding="utf-8")

        self.assertIn('indexedDB.open("ife-offline-v1", 2)', source)
        self.assertIn('keyPath: "catalog_key"', source)
        self.assertIn('createObjectStore("drafts"', source)
        self.assertIn("crypto.randomUUID()", source)
        self.assertIn("sevenDays", source)
        self.assertIn("drafted_by_id: app.dataset.membershipId", source)
        self.assertIn("drafted_by_id: draft.drafted_by_id", source)
        self.assertIn("copiedOfflineCreatedAt = draft.offline_created_at", source)
        self.assertNotIn("removedDraft", source)
        self.assertNotIn("purgeExpiredData", source)
        self.assertIn("role_at_draft: draft.role", source)
        self.assertIn("draft.business_id === app.dataset.businessId", source)
        self.assertIn("draft.branch_id === app.dataset.branchId", source)
        self.assertIn("draft.drafted_by_id === app.dataset.membershipId", source)
        self.assertIn("requirePrivateSession()", source)
        self.assertIn("async function establishPrivateSession()", source)
        self.assertIn("if (!response.ok)", source)
        self.assertIn("rememberPrivateSession();", source)
        self.assertIn('window.addEventListener("pageshow", restorePaymentSelection)', source)
        self.assertNotIn('addEventListener("sync"', source)
        self.assertNotIn("SyncManager", source)
        self.assertNotIn("Number(quantity)", source)

    def test_pwa_shell_uses_reversed_worker_url_and_logout_guard(self) -> None:
        script_path = finders.find("js/pwa-shell.js")
        assert isinstance(script_path, str)
        source = Path(script_path).read_text(encoding="utf-8")

        self.assertIn("document.body.dataset.serviceWorkerUrl", source)
        self.assertIn("document.body.dataset.serviceWorkerScope", source)
        self.assertNotIn('register("/offline/service-worker.js"', source)
        self.assertIn("unsafeLocalDraftCount", source)
        self.assertIn("draft.drafted_by_id === membershipId", source)
        self.assertIn('clear("catalogs")', source)
        self.assertNotIn('clear("drafts")', source)
        self.assertIn("sessionStorage.removeItem(sessionKey)", source)
        self.assertNotIn("rememberActiveMembership", source)
