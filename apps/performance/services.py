from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from django.db.models import Prefetch, Q, Sum
from django.utils import timezone

from apps.businesses.models import Branch, Business
from apps.cash.models import CashSession, CashSessionStatus
from apps.catalog.models import ProductVariant
from apps.expenses.models import ExpenseStatus, OperatingExpense
from apps.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryMovementType,
    StockCountApproval,
    StockCountReversal,
)
from apps.performance.forms import ProductOrder, TimeBucket
from apps.purchasing.models import PurchaseReturn, PurchaseReturnStatus
from apps.sales.models import (
    Sale,
    SaleLine,
    SaleReturn,
    SaleReturnLine,
    SaleReturnStatus,
    SaleStatus,
)

MONEY_QUANTUM = Decimal("0.01")
VALUE_QUANTUM = Decimal("0.000001")
QUANTITY_QUANTUM = Decimal("0.001")
PERCENT_QUANTUM = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")
ZERO_VALUE = Decimal("0.000000")
ZERO_QUANTITY = Decimal("0.000")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _value(value: Decimal) -> Decimal:
    return value.quantize(VALUE_QUANTUM, rounding=ROUND_HALF_UP)


def _quantity(value: Decimal) -> Decimal:
    return value.quantize(QUANTITY_QUANTUM, rounding=ROUND_HALF_UP)


def _percentage(value: Decimal) -> Decimal:
    return value.quantize(PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def _local_date(value: datetime) -> date:
    return timezone.localtime(value).date()


@dataclass(frozen=True)
class ReportSelection:
    business: Business
    branches: tuple[Branch, ...]
    start_date: date
    end_date: date
    bucket: str
    product_search: str = ""
    product_order: str = ProductOrder.NET_SALES

    @property
    def branch_ids(self) -> tuple[UUID, ...]:
        return tuple(branch.id for branch in self.branches)

    @property
    def branch_label(self) -> str:
        if len(self.branches) == 1:
            return self.branches[0].name
        return "All branches"


@dataclass(frozen=True)
class PerformanceMetrics:
    gross_sales: Decimal
    sale_refunds_and_reversals: Decimal
    net_sales: Decimal
    gross_assigned_inventory_cost: Decimal
    returned_assigned_inventory_cost: Decimal
    net_assigned_inventory_cost: Decimal
    gross_operating_result: Decimal
    net_operating_expenses: Decimal
    operational_net_result: Decimal
    gross_margin_percentage: Decimal | None
    operating_margin_percentage: Decimal | None


@dataclass(frozen=True)
class TimeBucketRow:
    start_date: date
    end_date: date
    label: str
    metrics: PerformanceMetrics
    net_sales_growth_percentage: Decimal | None


@dataclass(frozen=True)
class ProductPerformanceRow:
    variant_id: UUID
    product_name: str
    sku: str
    size: str
    color: str
    stock_unit: str
    gross_quantity_sold: Decimal
    returned_quantity: Decimal
    net_quantity_sold: Decimal
    gross_sales: Decimal
    sale_refunds_and_reversals: Decimal
    net_sales: Decimal
    net_assigned_inventory_cost: Decimal
    gross_operating_result: Decimal
    gross_margin_percentage: Decimal | None


@dataclass(frozen=True)
class ExpenseCategoryRow:
    category_id: UUID
    category_name: str
    posted_amount: Decimal
    reversed_amount: Decimal
    net_amount: Decimal
    percentage_of_expenses: Decimal | None


@dataclass(frozen=True)
class InventoryQuantityRow:
    stock_unit: str
    quantity_on_hand: Decimal


@dataclass(frozen=True)
class InventoryCostRow:
    variant_id: UUID
    product_name: str
    sku: str
    size: str
    color: str
    stock_unit: str
    quantity_on_hand: Decimal
    average_unit_cost: Decimal
    inventory_value: Decimal
    low_stock_threshold: Decimal | None
    is_low_stock: bool


@dataclass(frozen=True)
class InventoryContext:
    quantity_by_unit: tuple[InventoryQuantityRow, ...]
    inventory_value: Decimal
    out_of_stock_variant_count: int
    low_stock_variant_count: int
    rows: tuple[InventoryCostRow, ...]
    generated_at: datetime


@dataclass(frozen=True)
class OperationalControls:
    cash_variance: Decimal
    unresolved_supplier_return_reference: Decimal
    manual_inventory_adjustment_value: Decimal
    stock_count_adjustment_value: Decimal
    stock_count_reversal_value: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    selection: ReportSelection
    metrics: PerformanceMetrics
    comparison_net_sales: Decimal
    net_sales_growth_percentage: Decimal | None
    buckets: tuple[TimeBucketRow, ...]
    products: tuple[ProductPerformanceRow, ...]
    expense_categories: tuple[ExpenseCategoryRow, ...]
    inventory: InventoryContext
    controls: OperationalControls
    generated_at: datetime
    formula_version: str = "stage-5-v1"


@dataclass(frozen=True)
class _Event:
    event_date: date
    gross_sales: Decimal = ZERO_MONEY
    sale_refunds: Decimal = ZERO_MONEY
    gross_cost: Decimal = ZERO_VALUE
    returned_cost: Decimal = ZERO_VALUE
    expenses: Decimal = ZERO_MONEY
    variant_id: UUID | None = None
    product_name: str = ""
    sku: str = ""
    size: str = ""
    color: str = ""
    stock_unit: str = ""
    gross_quantity: Decimal = ZERO_QUANTITY
    returned_quantity: Decimal = ZERO_QUANTITY
    category_id: UUID | None = None
    category_name: str = ""
    posted_expense: Decimal = ZERO_MONEY
    reversed_expense: Decimal = ZERO_MONEY


@dataclass
class _Totals:
    gross_sales: Decimal = ZERO_MONEY
    sale_refunds: Decimal = ZERO_MONEY
    gross_cost: Decimal = ZERO_VALUE
    returned_cost: Decimal = ZERO_VALUE
    expenses: Decimal = ZERO_MONEY

    def add(self, event: _Event) -> None:
        self.gross_sales += event.gross_sales
        self.sale_refunds += event.sale_refunds
        self.gross_cost += event.gross_cost
        self.returned_cost += event.returned_cost
        self.expenses += event.expenses


@dataclass
class _ProductTotals:
    variant_id: UUID
    product_name: str
    sku: str
    size: str
    color: str
    stock_unit: str
    gross_quantity: Decimal = ZERO_QUANTITY
    returned_quantity: Decimal = ZERO_QUANTITY
    gross_sales: Decimal = ZERO_MONEY
    sale_refunds: Decimal = ZERO_MONEY
    gross_cost: Decimal = ZERO_VALUE
    returned_cost: Decimal = ZERO_VALUE

    def add(self, event: _Event) -> None:
        self.gross_quantity += event.gross_quantity
        self.returned_quantity += event.returned_quantity
        self.gross_sales += event.gross_sales
        self.sale_refunds += event.sale_refunds
        self.gross_cost += event.gross_cost
        self.returned_cost += event.returned_cost


@dataclass
class _ExpenseTotals:
    category_id: UUID
    category_name: str
    posted_amount: Decimal = ZERO_MONEY
    reversed_amount: Decimal = ZERO_MONEY

    def add(self, event: _Event) -> None:
        self.posted_amount += event.posted_expense
        self.reversed_amount += event.reversed_expense


def _metrics(totals: _Totals) -> PerformanceMetrics:
    gross_sales = _money(totals.gross_sales)
    refunds = _money(totals.sale_refunds)
    net_sales = _money(gross_sales - refunds)
    gross_cost = _value(totals.gross_cost)
    returned_cost = _value(totals.returned_cost)
    net_cost = _value(gross_cost - returned_cost)
    gross_result = _value(net_sales - net_cost)
    expenses = _money(totals.expenses)
    operating_result = _value(gross_result - expenses)
    gross_margin = None
    operating_margin = None
    if net_sales > ZERO_MONEY:
        gross_margin = _percentage(gross_result / net_sales * Decimal("100"))
        operating_margin = _percentage(operating_result / net_sales * Decimal("100"))
    return PerformanceMetrics(
        gross_sales=gross_sales,
        sale_refunds_and_reversals=refunds,
        net_sales=net_sales,
        gross_assigned_inventory_cost=gross_cost,
        returned_assigned_inventory_cost=returned_cost,
        net_assigned_inventory_cost=net_cost,
        gross_operating_result=gross_result,
        net_operating_expenses=expenses,
        operational_net_result=operating_result,
        gross_margin_percentage=gross_margin,
        operating_margin_percentage=operating_margin,
    )


def growth_percentage(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous < ZERO_MONEY:
        return None
    if previous == ZERO_MONEY:
        if current == ZERO_MONEY:
            return Decimal("0.00")
        return None
    return _percentage((current - previous) / previous * Decimal("100"))


def _bucket_start(value: date, bucket: str) -> date:
    if bucket == TimeBucket.DAILY:
        return value
    if bucket == TimeBucket.WEEKLY:
        return value - timedelta(days=value.weekday())
    if bucket == TimeBucket.MONTHLY:
        return value.replace(day=1)
    raise ValueError("Unsupported time bucket.")


def _next_bucket(value: date, bucket: str) -> date:
    if bucket == TimeBucket.DAILY:
        return value + timedelta(days=1)
    if bucket == TimeBucket.WEEKLY:
        return value + timedelta(days=7)
    if bucket == TimeBucket.MONTHLY:
        if value.month == 12:
            return value.replace(year=value.year + 1, month=1, day=1)
        return value.replace(month=value.month + 1, day=1)
    raise ValueError("Unsupported time bucket.")


def _previous_bucket(value: date, bucket: str) -> date:
    if bucket == TimeBucket.DAILY:
        return value - timedelta(days=1)
    if bucket == TimeBucket.WEEKLY:
        return value - timedelta(days=7)
    if bucket == TimeBucket.MONTHLY:
        if value.month == 1:
            return value.replace(year=value.year - 1, month=12, day=1)
        return value.replace(month=value.month - 1, day=1)
    raise ValueError("Unsupported time bucket.")


def _bucket_end(value: date, bucket: str) -> date:
    return _next_bucket(value, bucket) - timedelta(days=1)


def _bucket_label(start: date, end: date, bucket: str) -> str:
    if bucket == TimeBucket.DAILY:
        return start.isoformat()
    return f"{start.isoformat()} - {end.isoformat()}"


def _load_events(
    *,
    business: Business,
    branch_ids: tuple[UUID, ...],
    query_start: date,
    query_end: date,
) -> list[_Event]:
    events: list[_Event] = []
    sale_lines = SaleLine.objects.select_related("variant__product")
    sales = Sale.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        status=SaleStatus.POSTED,
        sale_date__range=(query_start, query_end),
    ).prefetch_related(Prefetch("lines", queryset=sale_lines))
    for sale in sales:
        for sale_line in sale.lines.all():
            variant = sale_line.variant
            events.append(
                _Event(
                    event_date=sale.sale_date,
                    gross_sales=sale_line.line_total,
                    gross_cost=-sale_line.inventory_value_delta,
                    variant_id=variant.id,
                    product_name=sale_line.product_name_snapshot,
                    sku=sale_line.sku_snapshot,
                    size=variant.size,
                    color=variant.color,
                    stock_unit=sale_line.unit_snapshot,
                    gross_quantity=sale_line.quantity,
                )
            )

    return_lines = SaleReturnLine.objects.select_related("variant__product")
    sale_returns = (
        SaleReturn.objects.filter(
            business=business,
            branch_id__in=branch_ids,
            status__in=(SaleReturnStatus.POSTED, SaleReturnStatus.REVERSED),
            posted_at__isnull=False,
        )
        .filter(
            Q(return_date__range=(query_start, query_end))
            | Q(reversal__posted_at__date__range=(query_start, query_end))
        )
        .select_related("reversal")
        .prefetch_related(Prefetch("lines", queryset=return_lines))
    )
    for sale_return in sale_returns:
        reversal_date = (
            _local_date(sale_return.reversal.posted_at)
            if sale_return.status == SaleReturnStatus.REVERSED
            else None
        )
        for return_line in sale_return.lines.all():
            variant = return_line.variant
            if query_start <= sale_return.return_date <= query_end:
                events.append(
                    _Event(
                        event_date=sale_return.return_date,
                        sale_refunds=return_line.refund_line_total,
                        returned_cost=return_line.inventory_value_delta,
                        returned_quantity=return_line.returned_quantity,
                        variant_id=variant.id,
                        product_name=return_line.product_name_snapshot,
                        sku=return_line.sku_snapshot,
                        size=variant.size,
                        color=variant.color,
                        stock_unit=return_line.unit_snapshot,
                    )
                )
            if reversal_date is not None and query_start <= reversal_date <= query_end:
                events.append(
                    _Event(
                        event_date=reversal_date,
                        sale_refunds=-return_line.refund_line_total,
                        returned_cost=-return_line.inventory_value_delta,
                        returned_quantity=-return_line.returned_quantity,
                        variant_id=variant.id,
                        product_name=return_line.product_name_snapshot,
                        sku=return_line.sku_snapshot,
                        size=variant.size,
                        color=variant.color,
                        stock_unit=return_line.unit_snapshot,
                    )
                )

    expenses = (
        OperatingExpense.objects.filter(
            business=business,
            branch_id__in=branch_ids,
            status__in=(ExpenseStatus.POSTED, ExpenseStatus.REVERSED),
            posted_at__isnull=False,
        )
        .filter(
            Q(business_date__range=(query_start, query_end))
            | Q(reversal__posted_at__date__range=(query_start, query_end))
        )
        .select_related("category", "reversal")
    )
    for expense in expenses:
        if expense.business_date is not None and query_start <= expense.business_date <= query_end:
            events.append(
                _Event(
                    event_date=expense.business_date,
                    expenses=expense.amount,
                    category_id=expense.category_id,
                    category_name=expense.category.name,
                    posted_expense=expense.amount,
                )
            )
        if expense.status == ExpenseStatus.REVERSED:
            reversal_date = _local_date(expense.reversal.posted_at)
            if query_start <= reversal_date <= query_end:
                events.append(
                    _Event(
                        event_date=reversal_date,
                        expenses=-expense.amount,
                        category_id=expense.category_id,
                        category_name=expense.category.name,
                        reversed_expense=expense.amount,
                    )
                )
    return events


def _aggregate(events: list[_Event], start_date: date, end_date: date) -> PerformanceMetrics:
    totals = _Totals()
    for event in events:
        if start_date <= event.event_date <= end_date:
            totals.add(event)
    return _metrics(totals)


def _time_buckets(
    *,
    events: list[_Event],
    start_date: date,
    end_date: date,
    bucket: str,
) -> tuple[TimeBucketRow, ...]:
    grouped: dict[date, _Totals] = {}
    previous_grouped: dict[date, _Totals] = {}
    first_key = _bucket_start(start_date, bucket)
    last_key = _bucket_start(end_date, bucket)
    key = _previous_bucket(first_key, bucket)
    while key <= last_key:
        previous_grouped[key] = _Totals()
        key = _next_bucket(key, bucket)
    for event in events:
        event_key = _bucket_start(event.event_date, bucket)
        if event_key in previous_grouped:
            previous_grouped[event_key].add(event)
        if start_date <= event.event_date <= end_date:
            grouped.setdefault(event_key, _Totals()).add(event)

    rows: list[TimeBucketRow] = []
    key = first_key
    while key <= last_key:
        visible_start = max(key, start_date)
        visible_end = min(_bucket_end(key, bucket), end_date)
        current = _metrics(grouped.get(key, _Totals()))
        previous_key = _previous_bucket(key, bucket)
        previous = _metrics(previous_grouped.get(previous_key, _Totals()))
        rows.append(
            TimeBucketRow(
                start_date=visible_start,
                end_date=visible_end,
                label=_bucket_label(visible_start, visible_end, bucket),
                metrics=current,
                net_sales_growth_percentage=growth_percentage(
                    current.net_sales,
                    previous.net_sales,
                ),
            )
        )
        key = _next_bucket(key, bucket)
    return tuple(rows)


def _product_rows(
    *,
    events: list[_Event],
    start_date: date,
    end_date: date,
    search: str,
    order: str,
) -> tuple[ProductPerformanceRow, ...]:
    totals: dict[UUID, _ProductTotals] = {}
    for event in events:
        if not (start_date <= event.event_date <= end_date) or event.variant_id is None:
            continue
        product = totals.setdefault(
            event.variant_id,
            _ProductTotals(
                variant_id=event.variant_id,
                product_name=event.product_name,
                sku=event.sku,
                size=event.size,
                color=event.color,
                stock_unit=event.stock_unit,
            ),
        )
        product.add(event)

    rows: list[ProductPerformanceRow] = []
    normalized_search = search.casefold()
    for product in totals.values():
        if normalized_search and normalized_search not in (
            f"{product.product_name} {product.sku} {product.size} {product.color}".casefold()
        ):
            continue
        gross_sales = _money(product.gross_sales)
        refunds = _money(product.sale_refunds)
        net_sales = _money(gross_sales - refunds)
        net_cost = _value(product.gross_cost - product.returned_cost)
        gross_result = _value(net_sales - net_cost)
        margin = (
            _percentage(gross_result / net_sales * Decimal("100"))
            if net_sales > ZERO_MONEY
            else None
        )
        rows.append(
            ProductPerformanceRow(
                variant_id=product.variant_id,
                product_name=product.product_name,
                sku=product.sku,
                size=product.size,
                color=product.color,
                stock_unit=product.stock_unit,
                gross_quantity_sold=_quantity(product.gross_quantity),
                returned_quantity=_quantity(product.returned_quantity),
                net_quantity_sold=_quantity(product.gross_quantity - product.returned_quantity),
                gross_sales=gross_sales,
                sale_refunds_and_reversals=refunds,
                net_sales=net_sales,
                net_assigned_inventory_cost=net_cost,
                gross_operating_result=gross_result,
                gross_margin_percentage=margin,
            )
        )
    if order == ProductOrder.SKU:
        rows.sort(key=lambda row: (row.sku.casefold(), row.product_name.casefold()))
    elif order == ProductOrder.GROSS_RESULT:
        rows.sort(
            key=lambda row: (
                -row.gross_operating_result,
                row.sku.casefold(),
            )
        )
    else:
        rows.sort(key=lambda row: (-row.net_sales, row.sku.casefold()))
    return tuple(rows)


def _expense_rows(
    *,
    events: list[_Event],
    start_date: date,
    end_date: date,
    total_expenses: Decimal,
) -> tuple[ExpenseCategoryRow, ...]:
    totals: dict[UUID, _ExpenseTotals] = {}
    for event in events:
        if not (start_date <= event.event_date <= end_date) or event.category_id is None:
            continue
        category = totals.setdefault(
            event.category_id,
            _ExpenseTotals(
                category_id=event.category_id,
                category_name=event.category_name,
            ),
        )
        category.add(event)
    rows = []
    for category in totals.values():
        posted = _money(category.posted_amount)
        reversed_amount = _money(category.reversed_amount)
        net = _money(posted - reversed_amount)
        share = (
            _percentage(net / total_expenses * Decimal("100"))
            if total_expenses > ZERO_MONEY
            else None
        )
        rows.append(
            ExpenseCategoryRow(
                category_id=category.category_id,
                category_name=category.category_name,
                posted_amount=posted,
                reversed_amount=reversed_amount,
                net_amount=net,
                percentage_of_expenses=share,
            )
        )
    rows.sort(key=lambda row: (-row.net_amount, row.category_name.casefold()))
    return tuple(rows)


def _inventory_context(
    *,
    business: Business,
    branch_ids: tuple[UUID, ...],
    generated_at: datetime,
) -> InventoryContext:
    balances = list(
        InventoryBalance.objects.filter(
            business=business,
            branch_id__in=branch_ids,
        ).select_related("variant__product")
    )
    balance_totals: dict[UUID, tuple[Decimal, Decimal]] = {}
    for balance in balances:
        quantity, value = balance_totals.get(
            balance.variant_id,
            (ZERO_QUANTITY, ZERO_VALUE),
        )
        balance_totals[balance.variant_id] = (
            quantity + balance.quantity_on_hand,
            value + balance.inventory_value,
        )
    variants = (
        ProductVariant.objects.filter(business=business)
        .filter(
            Q(is_active=True)
            | Q(
                inventory_balances__branch_id__in=branch_ids,
                inventory_balances__quantity_on_hand__gt=ZERO_QUANTITY,
            )
        )
        .select_related("product")
        .distinct()
    )
    quantity_by_unit: dict[str, Decimal] = {}
    rows: list[InventoryCostRow] = []
    out_of_stock_count = 0
    low_stock_count = 0
    inventory_value = ZERO_VALUE
    for variant in variants:
        quantity, value = balance_totals.get(
            variant.id,
            (ZERO_QUANTITY, ZERO_VALUE),
        )
        average = _value(value / quantity) if quantity > ZERO_QUANTITY else ZERO_VALUE
        is_low = (
            quantity > ZERO_QUANTITY
            and variant.low_stock_threshold is not None
            and quantity <= variant.low_stock_threshold
        )
        if quantity == ZERO_QUANTITY:
            out_of_stock_count += 1
        if is_low:
            low_stock_count += 1
        inventory_value += value
        quantity_by_unit[variant.stock_unit] = (
            quantity_by_unit.get(variant.stock_unit, ZERO_QUANTITY) + quantity
        )
        rows.append(
            InventoryCostRow(
                variant_id=variant.id,
                product_name=variant.product.name,
                sku=variant.sku,
                size=variant.size,
                color=variant.color,
                stock_unit=variant.stock_unit,
                quantity_on_hand=_quantity(quantity),
                average_unit_cost=average,
                inventory_value=_value(value),
                low_stock_threshold=variant.low_stock_threshold,
                is_low_stock=is_low,
            )
        )
    rows.sort(key=lambda row: (row.product_name.casefold(), row.sku.casefold()))
    quantity_rows = tuple(
        InventoryQuantityRow(stock_unit=unit, quantity_on_hand=_quantity(quantity))
        for unit, quantity in sorted(quantity_by_unit.items())
    )
    return InventoryContext(
        quantity_by_unit=quantity_rows,
        inventory_value=_value(inventory_value),
        out_of_stock_variant_count=out_of_stock_count,
        low_stock_variant_count=low_stock_count,
        rows=tuple(rows),
        generated_at=generated_at,
    )


def _cash_variance(
    *,
    business: Business,
    branch_ids: tuple[UUID, ...],
    start_date: date,
    end_date: date,
) -> Decimal:
    sessions = CashSession.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        business_date__range=(start_date, end_date),
        status=CashSessionStatus.CLOSED,
    ).prefetch_related("closures")
    total = ZERO_MONEY
    for session in sessions:
        closures = list(session.closures.all())
        if closures:
            total += closures[-1].variance
    return _money(total)


def _unresolved_supplier_reference(
    *,
    business: Business,
    branch_ids: tuple[UUID, ...],
) -> Decimal:
    purchase_returns = PurchaseReturn.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        status=PurchaseReturnStatus.POSTED,
    ).prefetch_related("lines")
    unresolved = ZERO_VALUE
    for purchase_return in purchase_returns:
        reference = sum(
            (line.supplier_reference_total for line in purchase_return.lines.all()),
            ZERO_VALUE,
        )
        accepted = (
            purchase_return.supplier_settlements.filter(reversal__isnull=True).aggregate(
                total=Sum("amount")
            )["total"]
            or ZERO_MONEY
        )
        unresolved += reference - accepted
    return _money(unresolved)


def _operational_controls(
    *,
    business: Business,
    branch_ids: tuple[UUID, ...],
    start_date: date,
    end_date: date,
) -> OperationalControls:
    manual_adjustments = InventoryMovement.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        movement_type__in=(
            InventoryMovementType.ADJUSTMENT_IN,
            InventoryMovementType.ADJUSTMENT_OUT,
        ),
        posted_at__date__range=(start_date, end_date),
    )
    manual_value = sum(
        (movement.value_delta for movement in manual_adjustments),
        ZERO_VALUE,
    )
    approvals = StockCountApproval.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        session__business_date__range=(start_date, end_date),
    )
    approval_value = sum(
        (approval.total_inventory_value_adjustment for approval in approvals),
        ZERO_VALUE,
    )
    reversals = StockCountReversal.objects.filter(
        business=business,
        branch_id__in=branch_ids,
        reversed_at__date__range=(start_date, end_date),
    ).select_related("approval")
    reversal_value = sum(
        (-reversal.approval.total_inventory_value_adjustment for reversal in reversals),
        ZERO_VALUE,
    )
    return OperationalControls(
        cash_variance=_cash_variance(
            business=business,
            branch_ids=branch_ids,
            start_date=start_date,
            end_date=end_date,
        ),
        unresolved_supplier_return_reference=_unresolved_supplier_reference(
            business=business,
            branch_ids=branch_ids,
        ),
        manual_inventory_adjustment_value=_value(manual_value),
        stock_count_adjustment_value=_value(approval_value),
        stock_count_reversal_value=_value(reversal_value),
    )


def build_performance_report(selection: ReportSelection) -> PerformanceReport:
    if selection.bucket not in TimeBucket.values:
        raise ValueError("Unsupported time bucket.")
    if selection.product_order not in ProductOrder.values:
        raise ValueError("Unsupported product ordering.")
    if not selection.branches:
        raise ValueError("At least one authorized branch is required.")
    if any(branch.business_id != selection.business.id for branch in selection.branches):
        raise ValueError("Every selected branch must belong to the report business.")
    if selection.end_date < selection.start_date:
        raise ValueError("End date cannot precede start date.")
    if selection.end_date - selection.start_date > timedelta(days=365):
        raise ValueError("Performance reports are limited to 366 days.")

    range_days = (selection.end_date - selection.start_date).days + 1
    comparison_start = selection.start_date - timedelta(days=range_days)
    first_bucket_start = _bucket_start(selection.start_date, selection.bucket)
    first_previous_bucket = _previous_bucket(first_bucket_start, selection.bucket)
    query_start = min(comparison_start, first_previous_bucket)
    generated_at = timezone.now()
    events = _load_events(
        business=selection.business,
        branch_ids=selection.branch_ids,
        query_start=query_start,
        query_end=selection.end_date,
    )
    metrics = _aggregate(events, selection.start_date, selection.end_date)
    comparison = _aggregate(
        events,
        comparison_start,
        selection.start_date - timedelta(days=1),
    )
    return PerformanceReport(
        selection=selection,
        metrics=metrics,
        comparison_net_sales=comparison.net_sales,
        net_sales_growth_percentage=growth_percentage(
            metrics.net_sales,
            comparison.net_sales,
        ),
        buckets=_time_buckets(
            events=events,
            start_date=selection.start_date,
            end_date=selection.end_date,
            bucket=selection.bucket,
        ),
        products=_product_rows(
            events=events,
            start_date=selection.start_date,
            end_date=selection.end_date,
            search=selection.product_search,
            order=selection.product_order,
        ),
        expense_categories=_expense_rows(
            events=events,
            start_date=selection.start_date,
            end_date=selection.end_date,
            total_expenses=metrics.net_operating_expenses,
        ),
        inventory=_inventory_context(
            business=selection.business,
            branch_ids=selection.branch_ids,
            generated_at=generated_at,
        ),
        controls=_operational_controls(
            business=selection.business,
            branch_ids=selection.branch_ids,
            start_date=selection.start_date,
            end_date=selection.end_date,
        ),
        generated_at=generated_at,
    )
