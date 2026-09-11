import uuid
from decimal import Decimal
from typing import cast
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.inventory.models import InventoryMovementType, StockOperationType


class StockOperationForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    variant = forms.ModelChoiceField(queryset=ProductVariant.objects.none())
    quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        max_digits=18,
        decimal_places=3,
    )
    unit_cost = forms.DecimalField(
        required=False,
        min_value=Decimal("0.000000"),
        max_digits=18,
        decimal_places=6,
    )
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def scope_to_business(self, business: Business) -> None:
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        variant_field = cast(forms.ModelChoiceField, self.fields["variant"])
        variant_field.queryset = ProductVariant.objects.filter(
            business=business,
            is_active=True,
        ).select_related("product")
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        variant = cleaned_data.get("variant")
        quantity = cleaned_data.get("quantity")
        if isinstance(variant, ProductVariant) and isinstance(quantity, Decimal):
            try:
                validate_stock_quantity(quantity, variant.stock_unit)
            except ValidationError as error:
                self.add_error("quantity", error)
        return cleaned_data


class OpeningBalanceForm(StockOperationForm):
    reason = forms.CharField(required=False, widget=forms.HiddenInput)
    unit_cost = forms.DecimalField(
        min_value=Decimal("0.000000"),
        max_digits=18,
        decimal_places=6,
    )


class InventoryAdjustmentForm(StockOperationForm):
    operation_type = forms.ChoiceField(
        choices=(
            (StockOperationType.ADJUSTMENT_IN, _("Increase stock")),
            (StockOperationType.ADJUSTMENT_OUT, _("Decrease stock")),
        )
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean()
        operation_type = cleaned_data.get("operation_type")
        unit_cost = cleaned_data.get("unit_cost")
        if operation_type == StockOperationType.ADJUSTMENT_IN and unit_cost is None:
            self.add_error("unit_cost", _("Unit cost is required when increasing stock."))
        return cleaned_data


class InventoryMovementFilterForm(forms.Form):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label=_("Branch"),
    )
    variant = forms.ModelChoiceField(
        queryset=ProductVariant.objects.none(),
        required=False,
        label=_("Product variant"),
    )
    movement_type = forms.ChoiceField(
        required=False,
        label=_("Movement type"),
        choices=(("", _("All movement types")), *InventoryMovementType.choices),
    )
    date_from = forms.DateField(
        required=False,
        label=_("Business date from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Business date to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def scope_to_business(
        self,
        business: Business,
        *,
        branch_ids: list[UUID] | None = None,
    ) -> None:
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branches = Branch.objects.filter(business=business)
        if branch_ids is not None:
            branches = branches.filter(id__in=branch_ids)
        branch_field.queryset = branches
        variant_field = cast(forms.ModelChoiceField, self.fields["variant"])
        variant_field.queryset = ProductVariant.objects.filter(
            business=business,
        ).select_related("product")

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from is not None and date_to is not None and date_from > date_to:
            self.add_error("date_to", _("End date cannot be earlier than start date."))
        return cleaned_data
