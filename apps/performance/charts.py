from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.performance.services import TimeBucketRow

CHART_WIDTH = Decimal("900")
CHART_HEIGHT = Decimal("260")
PADDING_X = Decimal("42")
PADDING_Y = Decimal("24")
COORDINATE_QUANTUM = Decimal("0.1")


@dataclass(frozen=True)
class ChartSeries:
    label: str
    css_class: str
    segments: tuple[str, ...]
    points: tuple[tuple[str, str, str, str], ...]


@dataclass(frozen=True)
class LineChart:
    title: str
    description: str
    width: str
    height: str
    zero_y: str
    min_label: str
    max_label: str
    series: tuple[ChartSeries, ...]
    has_values: bool


def _coordinate(value: Decimal) -> str:
    return str(value.quantize(COORDINATE_QUANTUM, rounding=ROUND_HALF_UP))


def _display_value(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _metric_value(row: TimeBucketRow, metric: str) -> Decimal | None:
    if metric == "net_sales":
        return row.metrics.net_sales
    if metric == "gross_operating_result":
        return row.metrics.gross_operating_result
    if metric == "operational_net_result":
        return row.metrics.operational_net_result
    if metric == "gross_margin_percentage":
        return row.metrics.gross_margin_percentage
    if metric == "operating_margin_percentage":
        return row.metrics.operating_margin_percentage
    if metric == "net_sales_growth_percentage":
        return row.net_sales_growth_percentage
    raise ValueError("Unsupported chart metric.")


def build_line_chart(
    *,
    rows: tuple[TimeBucketRow, ...],
    title: str,
    description: str,
    series_definitions: tuple[tuple[str, str, str], ...],
    value_suffix: str,
) -> LineChart:
    values = [
        value
        for row in rows
        for metric, _, _ in series_definitions
        if (value := _metric_value(row, metric)) is not None
    ]
    if not values:
        return LineChart(
            title=title,
            description=description,
            width=str(CHART_WIDTH),
            height=str(CHART_HEIGHT),
            zero_y=_coordinate(CHART_HEIGHT / Decimal("2")),
            min_label=f"0{value_suffix}",
            max_label=f"0{value_suffix}",
            series=tuple(),
            has_values=False,
        )
    minimum = min(min(values), Decimal("0"))
    maximum = max(max(values), Decimal("0"))
    if minimum == maximum:
        maximum += Decimal("1")
    plot_width = CHART_WIDTH - PADDING_X * Decimal("2")
    plot_height = CHART_HEIGHT - PADDING_Y * Decimal("2")
    spread = maximum - minimum

    def x_coordinate(index: int) -> Decimal:
        if len(rows) <= 1:
            return PADDING_X + plot_width / Decimal("2")
        return PADDING_X + Decimal(index) / Decimal(len(rows) - 1) * plot_width

    def y_coordinate(value: Decimal) -> Decimal:
        return PADDING_Y + (maximum - value) / spread * plot_height

    chart_series: list[ChartSeries] = []
    for metric, label, css_class in series_definitions:
        segments: list[str] = []
        current_segment: list[str] = []
        points: list[tuple[str, str, str, str]] = []
        for index, row in enumerate(rows):
            value = _metric_value(row, metric)
            if value is None:
                if current_segment:
                    segments.append(" ".join(current_segment))
                    current_segment = []
                continue
            x = _coordinate(x_coordinate(index))
            y = _coordinate(y_coordinate(value))
            current_segment.append(f"{x},{y}")
            points.append((x, y, row.label, f"{_display_value(value)}{value_suffix}"))
        if current_segment:
            segments.append(" ".join(current_segment))
        chart_series.append(
            ChartSeries(
                label=label,
                css_class=css_class,
                segments=tuple(segments),
                points=tuple(points),
            )
        )
    return LineChart(
        title=title,
        description=description,
        width=str(CHART_WIDTH),
        height=str(CHART_HEIGHT),
        zero_y=_coordinate(y_coordinate(Decimal("0"))),
        min_label=f"{_display_value(minimum)}{value_suffix}",
        max_label=f"{_display_value(maximum)}{value_suffix}",
        series=tuple(chart_series),
        has_values=True,
    )
