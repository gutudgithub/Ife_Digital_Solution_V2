from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.models import CashMovement, CashSession, CashSessionClosure, CashSessionStatus
from apps.catalog.models import Category, Product, ProductVariant
from apps.expenses.models import (
    ExpenseCategory,
    OperatingExpense,
    SupplierPayment,
    SupplierReturnSettlement,
)
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    StockCountApproval,
    StockCountLine,
    StockCountReversal,
    StockCountSession,
    StockCountStatus,
)
from apps.performance.forms import TimeBucket
from apps.performance.services import ReportSelection, build_performance_report
from apps.purchasing.models import GoodsReceipt, Purchase, PurchaseReturn, Supplier
from apps.sales.models import (
    InternalReceipt,
    InternalReturnReceipt,
    Sale,
    SalePayment,
    SaleReturn,
    SaleReturnReversal,
    SaleReturnStatus,
    SaleStatus,
)


@override_settings(DEBUG=True)
class SeedDemoCommandTests(TestCase):
    password = "LocalDemo123!"

    def test_seed_demo_creates_idempotent_tenant_data(self) -> None:
        call_command("seed_demo", password=self.password)
        call_command("seed_demo", password=self.password)

        business = Business.objects.get(slug="ife-demo-fashion")
        owner = User.objects.get(email="owner@demo.ife.local")
        cashier = User.objects.get(email="cashier@demo.ife.local")
        stock_employee = User.objects.get(email="stock@demo.ife.local")

        self.assertTrue(owner.check_password(self.password))
        self.assertTrue(cashier.check_password(self.password))
        self.assertTrue(stock_employee.check_password(self.password))
        self.assertEqual(Branch.objects.filter(business=business).count(), 1)
        self.assertEqual(Category.objects.filter(business=business).count(), 2)
        self.assertEqual(Product.objects.filter(business=business).count(), 2)
        self.assertEqual(ProductVariant.objects.filter(business=business).count(), 4)
        self.assertEqual(BusinessMembership.objects.filter(business=business).count(), 3)
        self.assertEqual(Supplier.objects.filter(business=business).count(), 1)
        self.assertEqual(Purchase.objects.filter(business=business).count(), 1)
        self.assertEqual(GoodsReceipt.objects.filter(business=business).count(), 1)
        self.assertEqual(PurchaseReturn.objects.filter(business=business).count(), 1)
        self.assertEqual(ExpenseCategory.objects.filter(business=business).count(), 2)
        self.assertEqual(OperatingExpense.objects.filter(business=business).count(), 2)
        self.assertEqual(SupplierPayment.objects.filter(business=business).count(), 1)
        self.assertEqual(
            SupplierReturnSettlement.objects.filter(business=business).count(),
            1,
        )
        self.assertEqual(Sale.objects.filter(business=business).count(), 4)
        self.assertEqual(
            Sale.objects.filter(business=business, status=SaleStatus.POSTED).count(),
            4,
        )
        self.assertEqual(SalePayment.objects.filter(business=business).count(), 4)
        self.assertEqual(InternalReceipt.objects.filter(business=business).count(), 4)
        self.assertEqual(SaleReturn.objects.filter(business=business).count(), 2)
        self.assertEqual(
            SaleReturn.objects.filter(
                business=business,
                status=SaleReturnStatus.POSTED,
            ).count(),
            1,
        )
        self.assertEqual(
            SaleReturn.objects.filter(
                business=business,
                status=SaleReturnStatus.REVERSED,
            ).count(),
            1,
        )
        self.assertEqual(SaleReturnReversal.objects.filter(business=business).count(), 1)
        self.assertEqual(InternalReturnReceipt.objects.filter(business=business).count(), 2)
        self.assertEqual(CashSession.objects.filter(business=business).count(), 1)
        self.assertEqual(
            CashSession.objects.get(business=business).status,
            CashSessionStatus.CLOSED,
        )
        self.assertEqual(CashMovement.objects.filter(business=business).count(), 5)
        closure = CashSessionClosure.objects.get(business=business)
        self.assertEqual(closure.expected_cash, Decimal("500.00"))
        self.assertEqual(closure.actual_cash, Decimal("490.00"))
        self.assertEqual(closure.variance, Decimal("-10.00"))
        self.assertEqual(InventoryMovement.objects.filter(business=business).count(), 14)
        balance = InventoryBalance.objects.get(
            business=business,
            variant__sku="TSHIRT-BLK-M",
        )
        self.assertEqual(balance.quantity_on_hand, Decimal("1.000"))
        shoe_balance = InventoryBalance.objects.get(
            business=business,
            variant__sku="SHOE-BRN-42",
        )
        self.assertEqual(shoe_balance.quantity_on_hand, Decimal("6.000"))
        self.assertEqual(StockCountSession.objects.filter(business=business).count(), 1)
        self.assertEqual(
            StockCountSession.objects.get(business=business).status,
            StockCountStatus.APPROVED,
        )
        self.assertEqual(StockCountLine.objects.filter(business=business).count(), 4)
        self.assertEqual(StockCountApproval.objects.filter(business=business).count(), 1)
        self.assertEqual(StockCountReversal.objects.filter(business=business).count(), 1)
        report_sale_dates = list(
            Sale.objects.filter(business=business).values_list("sale_date", flat=True)
        )
        report = build_performance_report(
            ReportSelection(
                business=business,
                branches=(Branch.objects.get(business=business),),
                start_date=min(report_sale_dates),
                end_date=max(report_sale_dates),
                bucket=TimeBucket.DAILY,
            )
        )
        self.assertGreaterEqual(len(report.buckets), 3)
        self.assertEqual(report.metrics.net_sales, Decimal("5100.00"))
        self.assertEqual(report.metrics.net_operating_expenses, Decimal("150.00"))
        self.assertEqual(report.controls.cash_variance, Decimal("-10.00"))
        self.assertNotEqual(report.controls.stock_count_adjustment_value, Decimal("0.00"))
        self.assertNotEqual(report.controls.stock_count_reversal_value, Decimal("0.00"))
        self.assertEqual(
            (
                BusinessMembership.objects.get(business=business, user=owner).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=owner,
                ).assigned_branch,
            ),
            (MembershipRole.OWNER, Branch.objects.get(business=business)),
        )
        self.assertEqual(
            (
                BusinessMembership.objects.get(business=business, user=cashier).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=cashier,
                ).assigned_branch,
            ),
            (MembershipRole.CASHIER, Branch.objects.get(business=business)),
        )
        self.assertEqual(
            (
                BusinessMembership.objects.get(
                    business=business,
                    user=stock_employee,
                ).role,
                BusinessMembership.objects.get(
                    business=business,
                    user=stock_employee,
                ).assigned_branch,
            ),
            (MembershipRole.STOCK_EMPLOYEE, Branch.objects.get(business=business)),
        )

    @override_settings(DEBUG=False)
    def test_seed_demo_is_disabled_outside_debug_mode(self) -> None:
        with self.assertRaisesMessage(CommandError, "only be created"):
            call_command("seed_demo", password=self.password)
