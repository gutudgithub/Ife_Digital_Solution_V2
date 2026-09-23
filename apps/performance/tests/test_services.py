import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.cash.services import close_cash_session, open_cash_session, reopen_cash_session
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.expenses.models import ExpenseCategory, OperationalPaymentMethod
from apps.expenses.services import (
    create_operating_expense_draft,
    post_operating_expense,
    reverse_operating_expense,
)
from apps.inventory.models import StockOperationType
from apps.inventory.services import post_inventory_adjustment, post_opening_balance
from apps.performance.charts import build_line_chart
from apps.performance.forms import TimeBucket
from apps.performance.services import (
    PerformanceMetrics,
    PerformanceReport,
    ReportSelection,
    TimeBucketRow,
    build_performance_report,
    growth_percentage,
)
from apps.sales.models import Sale, SalePaymentMethod, SaleReturn, SaleReturnPurpose
from apps.sales.services import (
    ReturnQuantity,
    SaleQuantity,
    post_sale,
    post_sale_return,
    reverse_sale_return,
    save_sale_draft,
    save_sale_return_draft,
)

UTC = ZoneInfo("UTC")


class PerformanceServiceTests(TestCase):
    business: Business
    branch: Branch
    other_branch: Branch
    owner: BusinessMembership
    variant: ProductVariant
    expense_category: ExpenseCategory

    def setUp(self) -> None:
        owner_user = User.objects.create_user(
            email="performance-owner@example.com",
            password="strong-test-password",
        )
        self.business = Business.objects.create(
            name="Performance Fashion",
            slug="performance-fashion",
        )
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
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=owner_user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        product = Product.objects.create(
            business=self.business,
            name="Cotton T-shirt",
        )
        self.variant = ProductVariant.objects.create(
            business=self.business,
            product=product,
            sku="TSHIRT-BLK-M",
            size="M",
            color="Black",
            selling_price=Decimal("100.00"),
            stock_unit=StockUnit.PIECE,
            low_stock_threshold=Decimal("5.000"),
        )
        for branch in (self.branch, self.other_branch):
            post_opening_balance(
                actor=self.owner,
                branch=branch,
                variant=self.variant,
                quantity=Decimal("100.000"),
                unit_cost=Decimal("33.333333"),
                idempotency_key=uuid.uuid4(),
            )
        self.expense_category = ExpenseCategory.objects.create(
            business=self.business,
            name="Utilities",
        )

    def _post_sale(
        self,
        *,
        branch: Branch | None = None,
        sale_date: date,
        quantity: str,
        posted_at: datetime | None = None,
    ) -> Sale:
        sale = save_sale_draft(
            actor=self.owner,
            branch=branch or self.branch,
            sale_date=sale_date,
            quantities=[SaleQuantity(self.variant.id, Decimal(quantity))],
        )
        return post_sale(
            actor=self.owner,
            sale=sale,
            payment_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference=f"SALE-{uuid.uuid4()}",
            idempotency_key=uuid.uuid4(),
            posted_at=posted_at,
        )

    def _post_return(
        self,
        *,
        sale: Sale,
        return_date: date,
        quantity: str,
        posted_at: datetime | None = None,
    ) -> SaleReturn:
        sale_return = save_sale_return_draft(
            actor=self.owner,
            sale=sale,
            purpose=SaleReturnPurpose.CUSTOMER_RETURN,
            return_date=return_date,
            reason="Customer returned unworn item",
            quantities=[ReturnQuantity(sale.lines.get().id, Decimal(quantity))],
        )
        return post_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            refund_method=SalePaymentMethod.TELEBIRR,
            telebirr_reference=f"RETURN-{uuid.uuid4()}",
            idempotency_key=uuid.uuid4(),
            posted_at=posted_at,
        )

    def _report(
        self,
        start_date: date,
        end_date: date,
        *,
        branches: tuple[Branch, ...] | None = None,
        bucket: str = TimeBucket.DAILY,
    ) -> PerformanceReport:
        return build_performance_report(
            ReportSelection(
                business=self.business,
                branches=branches or (self.branch,),
                start_date=start_date,
                end_date=end_date,
                bucket=bucket,
            )
        )

    def test_sales_returns_reversals_and_expenses_reconcile(self) -> None:
        sale = self._post_sale(
            sale_date=date(2026, 1, 10),
            quantity="3.000",
        )
        sale_return = self._post_return(
            sale=sale,
            return_date=date(2026, 1, 12),
            quantity="1.000",
        )
        reverse_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            reason="Return evidence entered in error",
            idempotency_key=uuid.uuid4(),
            reversed_at=datetime(2026, 1, 13, 9, tzinfo=UTC),
        )
        expense = create_operating_expense_draft(
            actor=self.owner,
            branch=self.branch,
            category=self.expense_category,
            payee="Utility provider",
            description="Branch electricity",
            amount=Decimal("40.00"),
        )
        post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.TELEBIRR,
            telebirr_reference="EXPENSE-1",
            idempotency_key=uuid.uuid4(),
            posted_at=datetime(2026, 1, 10, 9, tzinfo=UTC),
        )
        reverse_operating_expense(
            actor=self.owner,
            expense=expense,
            reason="Duplicate expense",
            idempotency_key=uuid.uuid4(),
            posted_at=datetime(2026, 1, 11, 9, tzinfo=UTC),
        )

        before_return_reversal = self._report(date(2026, 1, 10), date(2026, 1, 12))
        self.assertEqual(before_return_reversal.metrics.gross_sales, Decimal("300.00"))
        self.assertEqual(
            before_return_reversal.metrics.sale_refunds_and_reversals,
            Decimal("100.00"),
        )
        self.assertEqual(before_return_reversal.metrics.net_sales, Decimal("200.00"))
        self.assertEqual(
            before_return_reversal.metrics.net_assigned_inventory_cost,
            Decimal("66.666666"),
        )
        self.assertEqual(
            before_return_reversal.metrics.gross_operating_result,
            Decimal("133.333334"),
        )
        self.assertEqual(
            before_return_reversal.metrics.net_operating_expenses,
            Decimal("0.00"),
        )
        self.assertEqual(
            sum(
                (row.net_amount for row in before_return_reversal.expense_categories),
                Decimal("0.00"),
            ),
            before_return_reversal.metrics.net_operating_expenses,
        )
        self.assertEqual(
            before_return_reversal.metrics.operational_net_result,
            Decimal("133.333334"),
        )
        self.assertEqual(
            sum(
                (row.metrics.net_sales for row in before_return_reversal.buckets),
                Decimal("0.00"),
            ),
            before_return_reversal.metrics.net_sales,
        )
        self.assertEqual(
            sum((row.net_sales for row in before_return_reversal.products), Decimal("0.00")),
            before_return_reversal.metrics.net_sales,
        )
        self.assertEqual(
            sum(
                (row.metrics.gross_operating_result for row in before_return_reversal.buckets),
                Decimal("0.000000"),
            ),
            before_return_reversal.metrics.gross_operating_result,
        )
        self.assertEqual(
            sum(
                (row.gross_operating_result for row in before_return_reversal.products),
                Decimal("0.000000"),
            ),
            before_return_reversal.metrics.gross_operating_result,
        )

        after_return_reversal = self._report(date(2026, 1, 10), date(2026, 1, 13))
        self.assertEqual(
            after_return_reversal.metrics.sale_refunds_and_reversals,
            Decimal("0.00"),
        )
        self.assertEqual(
            after_return_reversal.metrics.returned_assigned_inventory_cost,
            Decimal("0.000000"),
        )
        self.assertEqual(after_return_reversal.metrics.net_sales, Decimal("300.00"))

    def test_later_period_return_can_make_net_sales_negative_without_margin(self) -> None:
        sale = self._post_sale(sale_date=date(2026, 1, 31), quantity="1.000")
        self._post_return(
            sale=sale,
            return_date=date(2026, 2, 1),
            quantity="1.000",
        )

        report = self._report(date(2026, 2, 1), date(2026, 2, 1))

        self.assertEqual(report.metrics.net_sales, Decimal("-100.00"))
        self.assertEqual(report.metrics.net_assigned_inventory_cost, Decimal("-33.333333"))
        self.assertEqual(report.metrics.gross_operating_result, Decimal("-66.666667"))
        self.assertIsNone(report.metrics.gross_margin_percentage)
        self.assertIsNone(report.metrics.operating_margin_percentage)

    def test_reversal_uses_addis_ababa_local_posting_date(self) -> None:
        sale = self._post_sale(sale_date=date(2026, 1, 1), quantity="1.000")
        sale_return = self._post_return(
            sale=sale,
            return_date=date(2026, 1, 31),
            quantity="1.000",
        )
        reverse_sale_return(
            actor=self.owner,
            sale_return=sale_return,
            reason="Corrected after local midnight",
            idempotency_key=uuid.uuid4(),
            reversed_at=datetime(2026, 1, 31, 21, 30, tzinfo=UTC),
        )

        report = self._report(date(2026, 2, 1), date(2026, 2, 1))

        self.assertEqual(report.metrics.net_sales, Decimal("100.00"))
        self.assertEqual(report.metrics.net_assigned_inventory_cost, Decimal("33.333333"))

    def test_daily_weekly_and_monthly_buckets_include_leap_day(self) -> None:
        self._post_sale(sale_date=date(2024, 2, 29), quantity="1.000")
        self._post_sale(sale_date=date(2024, 3, 4), quantity="1.000")

        daily = self._report(date(2024, 2, 29), date(2024, 3, 4))
        weekly = self._report(
            date(2024, 2, 29),
            date(2024, 3, 4),
            bucket=TimeBucket.WEEKLY,
        )
        monthly = self._report(
            date(2024, 2, 29),
            date(2024, 3, 4),
            bucket=TimeBucket.MONTHLY,
        )

        self.assertEqual(len(daily.buckets), 5)
        self.assertEqual(weekly.buckets[0].start_date, date(2024, 2, 29))
        self.assertEqual(weekly.buckets[1].start_date, date(2024, 3, 4))
        self.assertEqual(len(monthly.buckets), 2)
        self.assertEqual(
            sum((row.metrics.net_sales for row in monthly.buckets), Decimal("0.00")),
            Decimal("200.00"),
        )

    def test_branch_filter_and_current_inventory_reconcile(self) -> None:
        self._post_sale(
            branch=self.branch,
            sale_date=date(2026, 1, 10),
            quantity="2.000",
        )
        self._post_sale(
            branch=self.other_branch,
            sale_date=date(2026, 1, 10),
            quantity="1.000",
        )

        main_report = self._report(date(2026, 1, 10), date(2026, 1, 10))
        all_report = self._report(
            date(2026, 1, 10),
            date(2026, 1, 10),
            branches=(self.branch, self.other_branch),
        )

        self.assertEqual(main_report.metrics.net_sales, Decimal("200.00"))
        self.assertEqual(all_report.metrics.net_sales, Decimal("300.00"))
        self.assertEqual(main_report.inventory.inventory_value, Decimal("3266.666634"))
        self.assertEqual(all_report.inventory.inventory_value, Decimal("6566.666601"))
        self.assertEqual(
            main_report.inventory.quantity_by_unit[0].quantity_on_hand,
            Decimal("98.000"),
        )

    def test_growth_rules_and_366_day_limit(self) -> None:
        self.assertEqual(growth_percentage(Decimal("0"), Decimal("0")), Decimal("0.00"))
        self.assertIsNone(growth_percentage(Decimal("10"), Decimal("0")))
        self.assertIsNone(growth_percentage(Decimal("10"), Decimal("-1")))
        self.assertEqual(growth_percentage(Decimal("150"), Decimal("100")), Decimal("50.00"))

        build_performance_report(
            ReportSelection(
                business=self.business,
                branches=(self.branch,),
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                bucket=TimeBucket.MONTHLY,
            )
        )
        with self.assertRaisesMessage(ValueError, "366 days"):
            build_performance_report(
                ReportSelection(
                    business=self.business,
                    branches=(self.branch,),
                    start_date=date(2024, 1, 1),
                    end_date=date(2025, 1, 1),
                    bucket=TimeBucket.MONTHLY,
                )
            )

    def test_chart_labels_use_round_half_up_display_values(self) -> None:
        metrics = PerformanceMetrics(
            gross_sales=Decimal("0.00"),
            sale_refunds_and_reversals=Decimal("0.00"),
            net_sales=Decimal("0.00"),
            gross_assigned_inventory_cost=Decimal("0.000000"),
            returned_assigned_inventory_cost=Decimal("0.000000"),
            net_assigned_inventory_cost=Decimal("0.000000"),
            gross_operating_result=Decimal("0.005000"),
            net_operating_expenses=Decimal("0.00"),
            operational_net_result=Decimal("-0.005000"),
            gross_margin_percentage=None,
            operating_margin_percentage=None,
        )
        row = TimeBucketRow(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 1),
            label="2026-01-01",
            metrics=metrics,
            net_sales_growth_percentage=None,
        )

        chart = build_line_chart(
            rows=(row,),
            title="Rounding",
            description="Display values",
            series_definitions=(
                ("gross_operating_result", "Gross", "gross"),
                ("operational_net_result", "Operational", "operational"),
            ),
            value_suffix=" ETB",
        )

        self.assertEqual(chart.series[0].points[0][3], "0.01 ETB")
        self.assertEqual(chart.series[1].points[0][3], "-0.01 ETB")

    def test_previous_comparable_period_growth(self) -> None:
        self._post_sale(sale_date=date(2026, 1, 1), quantity="1.000")
        self._post_sale(sale_date=date(2026, 1, 2), quantity="2.000")

        report = self._report(date(2026, 1, 2), date(2026, 1, 2))

        self.assertEqual(report.comparison_net_sales, Decimal("100.00"))
        self.assertEqual(report.net_sales_growth_percentage, Decimal("100.00"))
        self.assertEqual(report.buckets[0].net_sales_growth_percentage, Decimal("100.00"))

    def test_cross_business_branch_is_rejected(self) -> None:
        other_business = Business.objects.create(name="Other", slug="other-performance")
        foreign_branch = Branch.objects.create(
            business=other_business,
            name="Foreign",
            code="foreign",
        )
        with self.assertRaisesMessage(ValueError, "report business"):
            self._report(
                date(2026, 1, 1),
                date(2026, 1, 1),
                branches=(foreign_branch,),
            )

    def test_no_python_float_values_are_exposed(self) -> None:
        self._post_sale(sale_date=date(2026, 1, 1), quantity="1.000")
        report = self._report(date(2026, 1, 1), date(2026, 1, 1))

        self.assertIsInstance(report.metrics.net_sales, Decimal)
        self.assertIsInstance(report.metrics.net_assigned_inventory_cost, Decimal)
        self.assertFalse(
            any(
                isinstance(value, float)
                for value in (
                    report.metrics.net_sales,
                    report.metrics.gross_operating_result,
                    report.net_sales_growth_percentage,
                )
            )
        )

    def test_inclusive_366_day_range_uses_exact_bounds(self) -> None:
        start = date(2024, 2, 29)
        end = start + timedelta(days=365)
        self._post_sale(sale_date=start, quantity="1.000")
        report = self._report(start, end, bucket=TimeBucket.MONTHLY)
        self.assertEqual(report.metrics.net_sales, Decimal("100.00"))

    def test_manual_adjustment_and_latest_cash_closure_are_controls_not_result(self) -> None:
        post_inventory_adjustment(
            actor=self.owner,
            branch=self.branch,
            variant=self.variant,
            operation_type=StockOperationType.ADJUSTMENT_OUT,
            quantity=Decimal("2.000"),
            reason="Verified damaged stock",
            idempotency_key=uuid.uuid4(),
        )
        session = open_cash_session(
            actor=self.owner,
            branch=self.branch,
            opening_float=Decimal("100.00"),
            opening_basis_note="Verified physical drawer count",
            idempotency_key=uuid.uuid4(),
        )
        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=Decimal("90.00"),
            explanation="Ten birr physical shortage",
            idempotency_key=uuid.uuid4(),
        )

        today = timezone.localdate()
        report = self._report(today, today)

        self.assertEqual(report.metrics.net_sales, Decimal("0.00"))
        self.assertEqual(report.metrics.operational_net_result, Decimal("0.00"))
        self.assertEqual(
            report.controls.manual_inventory_adjustment_value,
            Decimal("-66.666666"),
        )
        self.assertEqual(report.controls.cash_variance, Decimal("-10.00"))

        reopen_cash_session(
            actor=self.owner,
            session=session,
            reason="Recount the physical drawer",
            idempotency_key=uuid.uuid4(),
        )
        reopened_report = self._report(today, today)
        self.assertEqual(reopened_report.controls.cash_variance, Decimal("0.00"))

        close_cash_session(
            actor=self.owner,
            session=session,
            actual_cash=Decimal("80.00"),
            explanation="Twenty birr physical shortage after recount",
            idempotency_key=uuid.uuid4(),
        )
        reclosed_report = self._report(today, today)
        self.assertEqual(reclosed_report.controls.cash_variance, Decimal("-20.00"))
