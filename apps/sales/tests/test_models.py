from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.sales.models import Sale, SaleLine, SalePayment


class SalesModelTests(TestCase):
    business: Business
    branch: Branch
    membership: BusinessMembership
    sale: Sale
    variant: ProductVariant

    def setUp(self) -> None:
        user = User.objects.create_user(
            email="sales-model-owner@example.com",
            password="strong-test-password",
            full_name="Sales Model Owner",
        )
        self.business = Business.objects.create(name="Sales Model Shop", slug="sales-model-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        self.membership = BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        product = Product.objects.create(business=self.business, name="Model Shoe")
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="MODEL-42",
            selling_price=Decimal("1000"),
            stock_unit=StockUnit.PAIR,
        )
        self.sale = Sale.objects.create(
            business=self.business,
            branch=self.branch,
            internal_number="SAL-MODEL-1",
            sale_date=timezone.localdate(),
            total_amount=Decimal("2000"),
            created_by=self.membership,
        )
        SaleLine.objects.create(
            business=self.business,
            sale=self.sale,
            variant=self.variant,
            quantity=Decimal("2"),
            product_name_snapshot=self.variant.product.name,
            sku_snapshot=self.variant.sku,
            unit_snapshot=self.variant.stock_unit,
            selling_unit_price=self.variant.selling_price,
            line_total=Decimal("2000"),
        )

    def test_payment_must_equal_full_sale_total(self) -> None:
        payment = SalePayment(
            business=self.business,
            branch=self.branch,
            sale=self.sale,
            method="cash",
            amount=Decimal("1000"),
            received_by=self.membership,
            posted_at=timezone.now(),
        )

        with self.assertRaisesMessage(ValidationError, "equal the full sale total"):
            payment.save()

    def test_cash_payment_cannot_preserve_telebirr_evidence(self) -> None:
        payment = SalePayment(
            business=self.business,
            branch=self.branch,
            sale=self.sale,
            method="cash",
            amount=self.sale.total_amount,
            telebirr_reference="TX-INVALID",
            telebirr_reference_normalized="TX-INVALID",
            received_by=self.membership,
            posted_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            payment.save()

    def test_sale_line_rejects_cross_business_variant(self) -> None:
        other_business = Business.objects.create(name="Other Model", slug="other-sales-model")
        other_product = Product.objects.create(business=other_business, name="Other Model Shoe")
        other_variant = ProductVariant.objects.create(
            business=other_business,
            product=other_product,
            sku="OTHER-MODEL",
            selling_price=Decimal("1"),
        )
        line = SaleLine(
            business=self.business,
            sale=self.sale,
            variant=other_variant,
            quantity=Decimal("1"),
            product_name_snapshot=other_product.name,
            sku_snapshot=other_variant.sku,
            unit_snapshot=other_variant.stock_unit,
            selling_unit_price=other_variant.selling_price,
            line_total=Decimal("1"),
        )

        with self.assertRaisesMessage(ValidationError, "Variant must belong"):
            line.save()
