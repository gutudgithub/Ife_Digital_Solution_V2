import csv
from datetime import date
from typing import cast

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.businesses.types import TenantRequest
from apps.forms import add_accessible_error_attributes
from apps.performance.charts import LineChart, build_line_chart
from apps.performance.forms import PerformanceFilterForm, ProductOrder, TimeBucket
from apps.performance.services import (
    PerformanceReport,
    ReportSelection,
    build_performance_report,
)


def _tenant(request: HttpRequest) -> tuple[Business, BusinessMembership]:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    membership = tenant_request.active_membership
    if not membership.can_view_performance:
        raise PermissionDenied(_("Performance-report permission is required."))
    return tenant_request.active_business, membership


def _default_filter_data(today: date) -> dict[str, object]:
    return {
        "start_date": today.replace(day=1),
        "end_date": today,
        "bucket": TimeBucket.DAILY,
        "product_order": ProductOrder.NET_SALES,
    }


def _report_from_request(
    request: HttpRequest,
    business: Business,
) -> tuple[PerformanceFilterForm, PerformanceReport | None]:
    today = timezone.localdate()
    data = request.GET if request.GET else _default_filter_data(today)
    form = PerformanceFilterForm(data, business=business, today=today)
    add_accessible_error_attributes(form)
    if not form.is_valid():
        return form, None
    branches: tuple[Branch, ...]
    branch = form.cleaned_data.get("branch")
    if isinstance(branch, Branch):
        branches = (branch,)
    else:
        branches = tuple(business.branches.filter(is_active=True))
    if not branches:
        raise PermissionDenied(_("At least one active branch is required for performance reports."))
    start_date = cast(date, form.cleaned_data["start_date"])
    end_date = cast(date, form.cleaned_data["end_date"])
    report = build_performance_report(
        ReportSelection(
            business=business,
            branches=branches,
            start_date=start_date,
            end_date=end_date,
            bucket=cast(str, form.cleaned_data["bucket"]),
            product_search=cast(str, form.cleaned_data["product_search"]),
            product_order=cast(str, form.cleaned_data["product_order"]),
        )
    )
    return form, report


def _charts(report: PerformanceReport) -> tuple[LineChart, LineChart]:
    money_chart = build_line_chart(
        rows=report.buckets,
        title=str(_("Performance over time")),
        description=str(
            _(
                "Net sales, gross operating result, and operational net result in ETB. "
                "Exact values are listed in the table after the graph."
            )
        ),
        series_definitions=(
            ("net_sales", str(_("Net sales")), "chart-series--sales"),
            (
                "gross_operating_result",
                str(_("Gross operating result")),
                "chart-series--gross",
            ),
            (
                "operational_net_result",
                str(_("Operational net result")),
                "chart-series--operating",
            ),
        ),
        value_suffix=" ETB",
    )
    percentage_chart = build_line_chart(
        rows=report.buckets,
        title=str(_("Margins and growth over time")),
        description=str(
            _(
                "Gross margin, operating margin, and comparable-bucket net-sales growth. "
                "Unavailable percentages are omitted and listed as unavailable in the table."
            )
        ),
        series_definitions=(
            (
                "gross_margin_percentage",
                str(_("Gross margin")),
                "chart-series--sales",
            ),
            (
                "operating_margin_percentage",
                str(_("Operating margin")),
                "chart-series--gross",
            ),
            (
                "net_sales_growth_percentage",
                str(_("Net-sales growth")),
                "chart-series--operating",
            ),
        ),
        value_suffix="%",
    )
    return money_chart, percentage_chart


def _dashboard_context(
    *,
    request: HttpRequest,
    form: PerformanceFilterForm,
    report: PerformanceReport | None,
    paginate_products: bool,
) -> dict[str, object]:
    context: dict[str, object] = {
        "filter_form": form,
        "report": report,
    }
    if report is None:
        return context
    money_chart, percentage_chart = _charts(report)
    context.update(
        {
            "money_chart": money_chart,
            "percentage_chart": percentage_chart,
            "product_rows": report.products,
        }
    )
    if paginate_products:
        product_page = Paginator(report.products, 50).get_page(request.GET.get("page"))
        context["product_page"] = product_page
        context["product_rows"] = product_page.object_list
    return context


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    business, _membership = _tenant(request)
    form, report = _report_from_request(request, business)
    return render(
        request,
        "performance/dashboard.html",
        _dashboard_context(
            request=request,
            form=form,
            report=report,
            paginate_products=True,
        ),
    )


@login_required
def performance_print(request: HttpRequest) -> HttpResponse:
    business, _membership = _tenant(request)
    form, report = _report_from_request(request, business)
    status = 200 if report is not None else 400
    return render(
        request,
        "performance/print.html",
        _dashboard_context(
            request=request,
            form=form,
            report=report,
            paginate_products=False,
        ),
        status=status,
    )


def _csv_response(filename: str) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("\ufeff")
    return response


@login_required
def time_series_csv(request: HttpRequest) -> HttpResponse:
    business, _membership = _tenant(request)
    form, report = _report_from_request(request, business)
    if report is None:
        return render(
            request,
            "performance/dashboard.html",
            _dashboard_context(
                request=request,
                form=form,
                report=None,
                paginate_products=True,
            ),
            status=400,
        )
    response = _csv_response("performance-time-series.csv")
    writer = csv.writer(response)
    writer.writerow(
        [
            "formula_version",
            "bucket_start",
            "bucket_end",
            "gross_sales_etb",
            "sale_refunds_and_reversals_etb",
            "net_sales_etb",
            "net_assigned_inventory_cost_etb",
            "gross_operating_result_etb",
            "net_operating_expenses_etb",
            "operational_net_result_etb",
            "gross_margin_percent",
            "operating_margin_percent",
            "net_sales_growth_percent",
        ]
    )
    for bucket in report.buckets:
        writer.writerow(
            [
                report.formula_version,
                bucket.start_date.isoformat(),
                bucket.end_date.isoformat(),
                bucket.metrics.gross_sales,
                bucket.metrics.sale_refunds_and_reversals,
                bucket.metrics.net_sales,
                bucket.metrics.net_assigned_inventory_cost,
                bucket.metrics.gross_operating_result,
                bucket.metrics.net_operating_expenses,
                bucket.metrics.operational_net_result,
                bucket.metrics.gross_margin_percentage
                if bucket.metrics.gross_margin_percentage is not None
                else "",
                bucket.metrics.operating_margin_percentage
                if bucket.metrics.operating_margin_percentage is not None
                else "",
                bucket.net_sales_growth_percentage
                if bucket.net_sales_growth_percentage is not None
                else "",
            ]
        )
    return response


@login_required
def product_performance_csv(request: HttpRequest) -> HttpResponse:
    business, _membership = _tenant(request)
    form, report = _report_from_request(request, business)
    if report is None:
        return render(
            request,
            "performance/dashboard.html",
            _dashboard_context(
                request=request,
                form=form,
                report=None,
                paginate_products=True,
            ),
            status=400,
        )
    response = _csv_response("product-performance.csv")
    writer = csv.writer(response)
    writer.writerow(
        [
            "formula_version",
            "product",
            "sku",
            "size",
            "color",
            "stock_unit",
            "gross_quantity_sold",
            "returned_and_reversed_quantity",
            "net_quantity_sold",
            "gross_sales_etb",
            "sale_refunds_and_reversals_etb",
            "net_sales_etb",
            "net_assigned_inventory_cost_etb",
            "gross_operating_result_etb",
            "gross_margin_percent",
        ]
    )
    for product in report.products:
        writer.writerow(
            [
                report.formula_version,
                product.product_name,
                product.sku,
                product.size,
                product.color,
                product.stock_unit,
                product.gross_quantity_sold,
                product.returned_quantity,
                product.net_quantity_sold,
                product.gross_sales,
                product.sale_refunds_and_reversals,
                product.net_sales,
                product.net_assigned_inventory_cost,
                product.gross_operating_result,
                product.gross_margin_percentage
                if product.gross_margin_percentage is not None
                else "",
            ]
        )
    return response
