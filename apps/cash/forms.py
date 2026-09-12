import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from django import forms
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, BusinessMembership
from apps.cash.models import CashMovementType, CashSessionStatus


def cash_branch_queryset(membership: BusinessMembership) -> QuerySet[Branch]:
    branches = Branch.objects.filter(business=membership.business, is_active=True)
    if membership.can_manage_cash_movements:
        return branches
    if membership.assigned_branch_id:
        return branches.filter(pk=membership.assigned_branch_id)
    active_branch_ids = list(branches.values_list("id", flat=True)[:2])
    if len(active_branch_ids) == 1:
        return branches.filter(pk=active_branch_ids[0])
    return branches.none()


class CashSessionOpenForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label=_("Branch"))
    opening_float = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=18,
        decimal_places=2,
        label=_("Opening float"),
        help_text=_("Physical cash placed in the drawer before sales."),
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
        branch_field.queryset = cash_branch_queryset(membership)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()
            if branch_field.queryset.count() == 1:
                self.initial["branch"] = branch_field.queryset.first()


class ManualCashMovementForm(forms.Form):
    movement_type = forms.ChoiceField(
        choices=(
            (CashMovementType.CASH_ADDED, _("Cash added")),
            (CashMovementType.CASH_REMOVED, _("Cash removed")),
        ),
        label=_("Movement"),
    )
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=18,
        decimal_places=2,
        label=_("Amount"),
        error_messages={"min_value": _("Cash movement amount must be greater than zero.")},
    )
    reason = forms.CharField(
        label=_("Reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Describe only the physical drawer movement."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()


class CashSessionCloseForm(forms.Form):
    actual_cash = forms.DecimalField(
        min_value=Decimal("0.00"),
        max_digits=18,
        decimal_places=2,
        label=_("Actual physical cash"),
    )
    explanation = forms.CharField(
        required=False,
        label=_("Variance explanation"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        expected_amount: Decimal,
    ) -> None:
        super().__init__(data=data)
        self.expected_amount = expected_amount
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        actual_cash = cleaned_data.get("actual_cash")
        explanation = str(cleaned_data.get("explanation") or "").strip()
        if (
            isinstance(actual_cash, Decimal)
            and actual_cash != self.expected_amount
            and not explanation
        ):
            self.add_error("explanation", _("Explain every nonzero cash variance."))
        cleaned_data["explanation"] = explanation
        return cleaned_data


class CashSessionReopenForm(forms.Form):
    reason = forms.CharField(
        label=_("Reopening reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()


class CashSessionFilterForm(forms.Form):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label=_("Branch"),
    )
    status = forms.ChoiceField(
        choices=(("", _("All statuses")), *CashSessionStatus.choices),
        required=False,
        label=_("Status"),
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
        branch_field.queryset = cash_branch_queryset(membership)

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", _("End date cannot be earlier than start date."))
        return cleaned_data
