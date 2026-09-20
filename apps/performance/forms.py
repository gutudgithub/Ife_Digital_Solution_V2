from collections.abc import Mapping
from datetime import date, timedelta
from typing import cast

from django import forms
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business


class TimeBucket:
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"

    choices = (
        (DAILY, _("Daily")),
        (WEEKLY, _("Weekly")),
        (MONTHLY, _("Monthly")),
    )
    values = {DAILY, WEEKLY, MONTHLY}


class ProductOrder:
    NET_SALES = "net_sales"
    GROSS_RESULT = "gross_result"
    SKU = "sku"

    choices = (
        (NET_SALES, _("Net sales, highest first")),
        (GROSS_RESULT, _("Gross operating result, highest first")),
        (SKU, _("SKU")),
    )
    values = {NET_SALES, GROSS_RESULT, SKU}


def performance_branch_queryset(business: Business) -> QuerySet[Branch]:
    return Branch.objects.filter(business=business, is_active=True)


class PerformanceFilterForm(forms.Form):
    start_date = forms.DateField(
        label=_("Start date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end_date = forms.DateField(
        label=_("End date"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        empty_label=_("All branches"),
        label=_("Branch"),
    )
    bucket = forms.ChoiceField(
        choices=TimeBucket.choices,
        label=_("Time buckets"),
    )
    product_search = forms.CharField(
        required=False,
        label=_("Product or SKU"),
    )
    product_order = forms.ChoiceField(
        choices=ProductOrder.choices,
        label=_("Product order"),
    )

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        today: date,
    ) -> None:
        initial = {
            "start_date": today.replace(day=1),
            "end_date": today,
            "bucket": TimeBucket.DAILY,
            "product_order": ProductOrder.NET_SALES,
        }
        super().__init__(data=data, initial=initial)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = performance_branch_queryset(business)

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")
        if isinstance(start_date, date) and isinstance(end_date, date):
            if end_date < start_date:
                self.add_error("end_date", _("End date cannot be earlier than start date."))
            elif end_date - start_date > timedelta(days=365):
                self.add_error("end_date", _("Performance reports are limited to 366 days."))
        cleaned_data["product_search"] = str(cleaned_data.get("product_search") or "").strip()
        return cleaned_data
