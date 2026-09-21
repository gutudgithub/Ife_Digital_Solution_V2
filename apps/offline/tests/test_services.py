import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovement
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.inventory.models import InventoryMovement
from apps.offline.models import (
    OfflineSaleSync,
    OfflineSaleSyncKey,
    OfflineSaleSyncStatus,
)
from apps.offline.services import (
    OfflineSaleDraftInput,
    OfflineSaleLineInput,
    sync_offline_sale,
)
from apps.sales.models import InternalReceipt, Sale, SalePayment, SaleStatus
from apps.sales.services import post_sale


class OfflineSaleSyncServiceTests(TestCase):
    business: Business
    branch: Branch
    other_branch: Branch
    cashier_membership: BusinessMembership
    owner_membership: BusinessMembership
    variant: ProductVariant

    def setUp(self) -> None:
        cashier = User.objects.create_user(
            email="offline-cashier@example.com",
            password="strong-test-password",
            full_name="Offline Cashier",
        )
        owner = User.objects.create_user(
            email="offline-owner@example.com",
            password="strong-test-password",
            full_name="Offline Owner",
        )
        self.business = Business.objects.create(name="Offline Shop", slug="offline-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.other_branch = Branch.objects.create(
            business=self.business,
            name="Second",
            code="second",
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        product = Product.objects.create(business=self.business, name="Canvas Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="CANVAS-42",
            size="42",
            color="Black",
            selling_price=Decimal("900.00"),
            stock_unit=StockUnit.PAIR,
        )

    def _input(
        self,
        *,
        local_draft_id: uuid.UUID | None = None,
        idempotency_key: uuid.UUID | None = None,
        branch: Branch | None = None,
        variant: ProductVariant | None = None,
        quantity: str = "2",
        price: str | None = None,
        offline_created_at: datetime | None = None,
    ) -> OfflineSaleDraftInput:
        selected_variant = variant or self.variant
        created_at = offline_created_at or timezone.now() - timedelta(minutes=5)
        return OfflineSaleDraftInput(
            local_draft_id=local_draft_id or uuid.uuid4(),
            idempotency_key=idempotency_key or uuid.uuid4(),
            business_id=self.business.id,
            branch_id=(branch or self.branch).id,
            role_at_draft=MembershipRole.CASHIER,
            offline_created_at=created_at,
            payment_method="cash",
            telebirr_reference="",
            lines=(
                OfflineSaleLineInput(
                    variant_id=selected_variant.id,
                    quantity=quantity,
                    selling_price_snapshot=price or format(selected_variant.selling_price, ".2f"),
                    product_name_snapshot=selected_variant.product.name,
                    variant_label_snapshot=str(selected_variant),
                ),
            ),
        )

    def test_sync_creates_only_an_ordinary_server_draft(self) -> None:
        result = sync_offline_sale(
            actor=self.cashier_membership,
            draft=self._input(),
        )

        sale = Sale.objects.get()
        self.assertEqual(result.status, OfflineSaleSyncStatus.SYNCED)
        self.assertEqual(result.sale, sale)
        self.assertEqual(sale.status, SaleStatus.DRAFT)
        self.assertEqual(sale.branch, self.branch)
        self.assertEqual(sale.total_amount, Decimal("1800.00"))
        self.assertFalse(InventoryMovement.objects.exists())
        self.assertFalse(CashMovement.objects.exists())
        self.assertFalse(SalePayment.objects.exists())
        self.assertFalse(InternalReceipt.objects.exists())

    def test_exact_replay_returns_the_same_evidence_and_sale(self) -> None:
        draft = self._input()

        first = sync_offline_sale(actor=self.cashier_membership, draft=draft)
        replay = sync_offline_sale(actor=self.cashier_membership, draft=draft)

        self.assertEqual(replay.id, first.id)
        self.assertEqual(replay.sale_id, first.sale_id)
        self.assertEqual(OfflineSaleSyncKey.objects.count(), 1)
        self.assertEqual(OfflineSaleSync.objects.count(), 1)
        self.assertEqual(Sale.objects.count(), 1)

    def test_key_or_local_id_reuse_with_different_payload_is_rejected(self) -> None:
        original = self._input()
        sync_offline_sale(actor=self.cashier_membership, draft=original)

        with self.assertRaisesMessage(ValidationError, "different sale content"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(
                    idempotency_key=original.idempotency_key,
                    quantity="3",
                ),
            )
        with self.assertRaisesMessage(ValidationError, "different sale content"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(
                    local_draft_id=original.local_draft_id,
                    quantity="3",
                ),
            )
        self.assertEqual(Sale.objects.count(), 1)

    def test_current_price_is_authoritative_and_change_requires_review(self) -> None:
        draft = self._input(price="800.00")

        result = sync_offline_sale(actor=self.cashier_membership, draft=draft)

        self.assertEqual(result.status, OfflineSaleSyncStatus.NEEDS_REVIEW)
        self.assertEqual(result.conflict_messages, ["price_changed"])
        assert result.sale is not None
        self.assertEqual(result.sale.lines.get().selling_unit_price, Decimal("900.00"))
        self.assertEqual(result.snapshot["lines"][0]["selling_price"], "800.00")

    def test_inactive_or_cross_business_variant_is_rejected_without_sale(self) -> None:
        self.variant.is_active = False
        self.variant.save(update_fields=("is_active",))

        inactive = sync_offline_sale(
            actor=self.cashier_membership,
            draft=self._input(),
        )

        self.assertEqual(inactive.status, OfflineSaleSyncStatus.REJECTED)
        self.assertIsNone(inactive.sale_id)
        self.assertFalse(Sale.objects.exists())

        other_business = Business.objects.create(name="Other", slug="other-offline")
        other_product = Product.objects.create(business=other_business, name="Hidden")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="HIDDEN",
            selling_price=Decimal("100.00"),
        )
        cross_business = sync_offline_sale(
            actor=self.cashier_membership,
            draft=self._input(variant=other_variant),
        )
        self.assertEqual(cross_business.status, OfflineSaleSyncStatus.REJECTED)
        self.assertNotIn("Hidden", cross_business.conflict_messages)

    def test_cashier_cannot_sync_another_branch_and_inactive_actor_is_rejected(self) -> None:
        with self.assertRaisesMessage(ValidationError, "cannot synchronize"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(branch=self.other_branch),
            )
        self.assertFalse(OfflineSaleSyncKey.objects.exists())

        wrong_business = self._input()
        wrong_business = OfflineSaleDraftInput(
            local_draft_id=wrong_business.local_draft_id,
            idempotency_key=wrong_business.idempotency_key,
            business_id=uuid.uuid4(),
            branch_id=wrong_business.branch_id,
            role_at_draft=wrong_business.role_at_draft,
            offline_created_at=wrong_business.offline_created_at,
            payment_method=wrong_business.payment_method,
            telebirr_reference=wrong_business.telebirr_reference,
            lines=wrong_business.lines,
        )
        with self.assertRaisesMessage(ValidationError, "does not match"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=wrong_business,
            )

        self.cashier_membership.is_active = False
        self.cashier_membership.save(update_fields=("is_active",))
        with self.assertRaisesMessage(ValidationError, "active sales membership"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(),
            )

    def test_owner_can_select_another_active_branch(self) -> None:
        result = sync_offline_sale(
            actor=self.owner_membership,
            draft=self._input(branch=self.other_branch),
        )

        self.assertEqual(result.branch, self.other_branch)
        assert result.sale is not None
        self.assertEqual(result.sale.branch, self.other_branch)

    def test_expired_future_and_invalid_quantity_drafts_are_not_synchronized(self) -> None:
        with self.assertRaisesMessage(ValidationError, "expire after seven days"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(
                    offline_created_at=timezone.now() - timedelta(days=8),
                ),
            )
        with self.assertRaisesMessage(ValidationError, "cannot be in the future"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(
                    offline_created_at=timezone.now() + timedelta(hours=1),
                ),
            )
        with self.assertRaisesMessage(ValidationError, "three decimal places"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(quantity="1.0001"),
            )
        with self.assertRaisesMessage(ValidationError, "too large"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(quantity="1e100"),
            )
        with self.assertRaisesMessage(ValidationError, "price snapshot is invalid"):
            sync_offline_sale(
                actor=self.cashier_membership,
                draft=self._input(price="1e100"),
            )
        self.assertFalse(Sale.objects.exists())

    def test_insufficient_stock_is_allowed_at_sync_but_online_posting_rejects_it(self) -> None:
        result = sync_offline_sale(
            actor=self.cashier_membership,
            draft=self._input(quantity="10"),
        )
        assert result.sale is not None
        self.assertEqual(result.sale.status, SaleStatus.DRAFT)

        with self.assertRaisesMessage(ValidationError, "make stock negative"):
            post_sale(
                actor=self.cashier_membership,
                sale=result.sale,
                payment_method="telebirr",
                telebirr_reference="OFFLINE-TEST",
                idempotency_key=uuid.uuid4(),
            )
