import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import WHOLE_STOCK_UNITS, ProductVariant, validate_stock_quantity
from apps.inventory.models import (
    InventoryMovementType,
    StockCountLine,
    StockCountStatus,
    StockOperationType,
)


def stock_count_branch_queryset(membership: BusinessMembership) -> QuerySet[Branch]:
    branches = Branch.objects.filter(business=membership.business, is_active=True)
    if membership.can_approve_stock_counts:
        return branches
    if membership.assigned_branch_id is not None:
        return branches.filter(pk=membership.assigned_branch_id)
    active_branch_ids = list(branches.values_list("id", flat=True)[:2])
    if len(active_branch_ids) == 1:
        return branches.filter(pk=active_branch_ids[0])
    return branches.none()


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


class StockCountStartForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label=_("Branch"))
    count_method_note = forms.CharField(
        label=_("Count method note"),
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text=_(
            "Describe how the full branch count will be performed and independently checked."
        ),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        membership: BusinessMembership,
    ) -> None:
        super().__init__(data=data)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = stock_count_branch_queryset(membership)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()
            if branch_field.queryset.count() == 1:
                self.initial["branch"] = branch_field.queryset.first()


class StockCountQuantityForm(forms.Form):
    physical_quantity = forms.DecimalField(
        min_value=Decimal("0.000"),
        max_digits=18,
        decimal_places=3,
        label=_("Physical quantity"),
        error_messages={"min_value": _("Physical quantity cannot be negative.")},
    )
    replacement_reason = forms.CharField(
        required=False,
        label=_("Replacement reason"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Required when replacing a previously recorded physical quantity."),
    )

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        line: StockCountLine,
        prefix: str,
    ) -> None:
        super().__init__(data=data, prefix=prefix)
        self.line = line
        if not self.is_bound and line.physical_quantity is not None:
            self.initial["physical_quantity"] = line.physical_quantity

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        quantity = cleaned_data.get("physical_quantity")
        replacement_reason = str(cleaned_data.get("replacement_reason") or "").strip()
        if isinstance(quantity, Decimal):
            if (
                self.line.stock_unit_snapshot in WHOLE_STOCK_UNITS
                and quantity != quantity.to_integral_value()
            ):
                self.add_error(
                    "physical_quantity",
                    _("This stock unit requires a whole-number quantity."),
                )
            if (
                self.line.physical_quantity is not None
                and quantity != self.line.physical_quantity
                and not replacement_reason
            ):
                self.add_error(
                    "replacement_reason",
                    _("Explain why the previous physical count is being replaced."),
                )
        cleaned_data["replacement_reason"] = replacement_reason
        return cleaned_data


class StockCountReviewEvidenceForm(forms.Form):
    variance_explanation = forms.CharField(
        required=False,
        label=_("Quantity-variance explanation"),
        widget=forms.Textarea(attrs={"rows": 2}),
    )
    exceptional_unit_cost = forms.DecimalField(
        required=False,
        min_value=Decimal("0.000000"),
        max_digits=18,
        decimal_places=6,
        label=_("Exceptional count-adjustment unit cost"),
    )
    exceptional_cost_evidence_note = forms.CharField(
        required=False,
        label=_("Exceptional unit-cost evidence"),
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text=_("Identify the supplier document or verified purchase record."),
    )

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        line: StockCountLine,
        prefix: str,
    ) -> None:
        super().__init__(data=data, prefix=prefix)
        self.line = line
        if not self.is_bound:
            self.initial["variance_explanation"] = line.variance_explanation
            self.initial["exceptional_unit_cost"] = line.assigned_count_adjustment_unit_cost
            self.initial["exceptional_cost_evidence_note"] = line.exceptional_cost_evidence_note

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        explanation = str(cleaned_data.get("variance_explanation") or "").strip()
        evidence = str(cleaned_data.get("exceptional_cost_evidence_note") or "").strip()
        exceptional_cost = cleaned_data.get("exceptional_unit_cost")
        variance = self.line.variance_quantity
        if variance is not None and variance != 0 and not explanation:
            self.add_error(
                "variance_explanation",
                _("Explain every nonzero stock count quantity variance."),
            )
        requires_exceptional_cost = (
            variance is not None and variance > 0 and self.line.average_unit_cost_snapshot == 0
        )
        if requires_exceptional_cost and not isinstance(exceptional_cost, Decimal):
            self.add_error(
                "exceptional_unit_cost",
                _("A non-negative exceptional count-adjustment unit cost is required."),
            )
        if requires_exceptional_cost and not evidence:
            self.add_error(
                "exceptional_cost_evidence_note",
                _("Exceptional unit-cost evidence is required."),
            )
        if not requires_exceptional_cost and (isinstance(exceptional_cost, Decimal) or evidence):
            self.add_error(
                "exceptional_unit_cost",
                _("Snapshot cost cannot be replaced for this stock-count line."),
            )
        cleaned_data["variance_explanation"] = explanation
        cleaned_data["exceptional_cost_evidence_note"] = evidence
        return cleaned_data


class StockCountReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        label: str,
        help_text: str = "",
    ) -> None:
        super().__init__(data=data)
        self.fields["reason"].label = label
        self.fields["reason"].help_text = help_text


class StockCountPostingForm(forms.Form):
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()


class StockCountFilterForm(forms.Form):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label=_("Branch"),
    )
    status = forms.ChoiceField(
        choices=(("", _("All statuses")), *StockCountStatus.choices),
        required=False,
        label=_("Status"),
    )
    starter = forms.ModelChoiceField(
        queryset=BusinessMembership.objects.none(),
        required=False,
        label=_("Started by"),
    )
    submitter = forms.ModelChoiceField(
        queryset=BusinessMembership.objects.none(),
        required=False,
        label=_("Submitted by"),
    )
    approver = forms.ModelChoiceField(
        queryset=BusinessMembership.objects.none(),
        required=False,
        label=_("Approved by"),
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

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        membership: BusinessMembership,
    ) -> None:
        super().__init__(data=data)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = stock_count_branch_queryset(membership)
        memberships = BusinessMembership.objects.filter(
            business=membership.business,
            is_active=True,
        ).select_related("user")
        for field_name in ("starter", "submitter", "approver"):
            field = cast(forms.ModelChoiceField, self.fields[field_name])
            field.queryset = memberships

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from is not None and date_to is not None and date_from > date_to:
            self.add_error("date_to", _("End date cannot be earlier than start date."))
        return cleaned_data
