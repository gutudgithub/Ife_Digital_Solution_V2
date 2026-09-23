import uuid
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.offline.models import OfflineSaleSync, OfflineSaleSyncKey
from apps.offline.services import (
    OfflineSaleDraftInput,
    OfflineSaleLineInput,
    sync_offline_sale,
)
from apps.sales.models import Sale


@skipUnlessDBFeature("has_select_for_update")
class OfflineSaleSyncConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        user = User.objects.create_user(
            email="offline-concurrency-cashier@example.com",
            password="strong-test-password",
            full_name="Offline Concurrency Cashier",
        )
        business = Business.objects.create(
            name="Offline Concurrency Shop",
            slug="offline-concurrency-shop",
        )
        branch = Branch.objects.create(
            business=business,
            name="Main",
            code="main",
        )
        self.membership = BusinessMembership.objects.create(
            business=business,
            user=user,
            assigned_branch=branch,
            role=MembershipRole.CASHIER,
        )
        product = Product.objects.create(
            business=business,
            name="Concurrent Leather Shoe",
        )
        variant = ProductVariant.objects.create(
            business=business,
            product=product,
            sku="OFF-CONCURRENT-42",
            size="42",
            selling_price=Decimal("1700.00"),
            stock_unit=StockUnit.PAIR,
        )
        self.draft = OfflineSaleDraftInput(
            local_draft_id=uuid.uuid4(),
            idempotency_key=uuid.uuid4(),
            business_id=business.id,
            branch_id=branch.id,
            drafted_by_id=self.membership.id,
            role_at_draft=MembershipRole.CASHIER,
            offline_created_at=timezone.now() - timedelta(minutes=1),
            payment_method="cash",
            telebirr_reference="",
            lines=(
                OfflineSaleLineInput(
                    variant_id=variant.id,
                    quantity="1",
                    selling_price_snapshot="1700.00",
                    product_name_snapshot=product.name,
                    variant_label_snapshot=str(variant),
                ),
            ),
        )

    def test_concurrent_exact_replay_creates_one_server_draft(self) -> None:
        barrier = Barrier(2)
        results: Queue[uuid.UUID] = Queue()
        errors: Queue[BaseException] = Queue()

        def synchronize() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.membership.pk)
                barrier.wait()
                result = sync_offline_sale(actor=actor, draft=self.draft)
                results.put(result.pk)
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        threads = [Thread(target=synchronize), Thread(target=synchronize)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(list(errors.queue), [])
        self.assertEqual(results.qsize(), 2)
        self.assertEqual(len(set(results.queue)), 1)
        self.assertEqual(OfflineSaleSyncKey.objects.count(), 1)
        self.assertEqual(OfflineSaleSync.objects.count(), 1)
        self.assertEqual(Sale.objects.count(), 1)
