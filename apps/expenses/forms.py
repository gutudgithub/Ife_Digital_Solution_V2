import uuid
from collections.abc import Mapping
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business
from apps.expenses.models import (
    ExpenseCategory,
    ExpenseStatus,
    OperatingExpense,
    OperationalPaymentMethod,
    SupplierReturnSettlementType,
)


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ("name",)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        instance: ExpenseCategory | None = None,
    ) -> None:
        super().__init__(data=data, instance=instance)
        self.business = business

    def clean_name(self) -> str:
        name = str(self.cleaned_data["name"]).strip()
        duplicate = ExpenseCategory.objects.filter(
            business=self.business,
            name__iexact=name,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError(_("An expense category with this name already exists."))
        return name


class OperatingExpenseForm(forms.ModelForm):
    class Meta:
        model = OperatingExpense
        fields = ("branch", "category", "payee", "description", "amount")
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        instance: OperatingExpense | None = None,
    ) -> None:
        super().__init__(data=data, instance=instance)
        branch_field = self.fields["branch"]
        category_field = self.fields["category"]
        if not isinstance(branch_field, forms.ModelChoiceField) or not isinstance(
            category_field,
            forms.ModelChoiceField,
        ):
            raise TypeError("Expense fields must be model choices.")
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        categories = ExpenseCategory.objects.filter(business=business, is_active=True)
        if self.instance.category_id:
            categories = ExpenseCategory.objects.filter(business=business).filter(
                Q(id=self.instance.category_id) | Q(is_active=True)
            )
        category_field.queryset = categories


class ExpensePostForm(forms.Form):
    method = forms.ChoiceField(choices=OperationalPaymentMethod.choices)
    telebirr_reference = forms.CharField(
        required=False,
        max_length=120,
        help_text=_("Required only for Telebirr evidence."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        self.initial.setdefault("idempotency_key", uuid.uuid4())


class ReversalForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        self.initial.setdefault("idempotency_key", uuid.uuid4())


class SupplierPaymentForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    method = forms.ChoiceField(choices=OperationalPaymentMethod.choices)
    supplier_reference = forms.CharField(required=False, max_length=120)
    telebirr_reference = forms.CharField(
        required=False,
        max_length=120,
        help_text=_("Required only for Telebirr evidence."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        self.initial.setdefault("idempotency_key", uuid.uuid4())


class SupplierReturnSettlementForm(forms.Form):
    settlement_type = forms.ChoiceField(choices=SupplierReturnSettlementType.choices)
    amount = forms.DecimalField(
        max_digits=18,
        decimal_places=2,
        min_value=Decimal("0.01"),
    )
    method = forms.ChoiceField(
        choices=(("", _("Not applicable for credit")), *OperationalPaymentMethod.choices),
        required=False,
    )
    supplier_reference = forms.CharField(required=False, max_length=120)
    telebirr_reference = forms.CharField(
        required=False,
        max_length=120,
        help_text=_("Required only for a Telebirr refund."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        self.initial.setdefault("idempotency_key", uuid.uuid4())


class ExpenseFilterForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)
    category = forms.ModelChoiceField(queryset=ExpenseCategory.objects.none(), required=False)
    status = forms.ChoiceField(
        choices=(("", _("All statuses")), *ExpenseStatus.choices),
        required=False,
    )
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    search = forms.CharField(required=False, max_length=180)

    def scope_to_business(self, business: Business) -> None:
        branch_field = self.fields["branch"]
        category_field = self.fields["category"]
        if not isinstance(branch_field, forms.ModelChoiceField) or not isinstance(
            category_field,
            forms.ModelChoiceField,
        ):
            raise TypeError("Expense filter fields must be model choices.")
        branch_field.queryset = Branch.objects.filter(business=business)
        category_field.queryset = ExpenseCategory.objects.filter(business=business)
