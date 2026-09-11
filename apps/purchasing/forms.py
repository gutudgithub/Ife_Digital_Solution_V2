import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import cast
from uuid import UUID

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.purchasing.models import Purchase, PurchaseLine, Supplier
from apps.purchasing.services import PurchaseLineProgress


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ("name", "phone", "email", "address", "notes", "is_active")
        widgets = {
            "address": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class PurchaseForm(forms.ModelForm):
    class Meta:
        model = Purchase
        fields = (
            "branch",
            "supplier",
            "supplier_reference",
            "purchase_date",
            "expected_date",
            "settlement_terms",
        )
        widgets = {
            "purchase_date": forms.DateInput(attrs={"type": "date"}),
            "expected_date": forms.DateInput(attrs={"type": "date"}),
        }

    def scope_to_business(self, business: Business) -> None:
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        supplier_field = cast(forms.ModelChoiceField, self.fields["supplier"])
        supplier_field.queryset = Supplier.objects.filter(business=business, is_active=True)
        if not self.is_bound:
            self.initial.setdefault("purchase_date", timezone.localdate())


class PurchaseLineForm(forms.ModelForm):
    class Meta:
        model = PurchaseLine
        fields = ("variant", "ordered_quantity", "unit_cost")

    def scope_to_business(self, business: Business) -> None:
        variant_field = cast(forms.ModelChoiceField, self.fields["variant"])
        variant_field.queryset = ProductVariant.objects.filter(
            business=business,
            is_active=True,
        ).select_related("product")


class BasePurchaseLineFormSet(BaseInlineFormSet):
    business: Business

    def scope_to_business(self, business: Business) -> None:
        self.business = business
        for form in self.forms:
            if not isinstance(form, PurchaseLineForm):
                raise TypeError("Purchase line formset must use PurchaseLineForm.")
            form.instance.business = business
            form.scope_to_business(business)

    def save_new(
        self,
        form: forms.ModelForm,
        commit: bool = True,
    ) -> PurchaseLine:
        line = cast(PurchaseLine, super().save_new(form, commit=False))
        line.business = self.business
        if commit:
            line.save()
        return line


PurchaseLineFormSet = inlineformset_factory(
    Purchase,
    PurchaseLine,
    form=PurchaseLineForm,
    formset=BasePurchaseLineFormSet,
    fields=("variant", "ordered_quantity", "unit_cost"),
    extra=2,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class PurchaseReceiptForm(forms.Form):
    supplier_document_reference = forms.CharField(
        required=False,
        label=_("Supplier document reference"),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        progress: list[PurchaseLineProgress],
    ) -> None:
        super().__init__(data=data)
        self.progress = progress
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()
        for item in progress:
            if item.remaining_quantity <= 0:
                continue
            field_name = self.quantity_field_name(item.line.id)
            self.fields[field_name] = forms.DecimalField(
                required=False,
                min_value=Decimal("0.001"),
                max_digits=18,
                decimal_places=3,
                label=_("%(product)s (%(sku)s), remaining %(remaining)s %(unit)s")
                % {
                    "product": item.line.product_name_snapshot,
                    "sku": item.line.sku_snapshot,
                    "remaining": item.remaining_quantity,
                    "unit": item.line.get_unit_snapshot_display(),
                },
            )

    @staticmethod
    def quantity_field_name(line_id: UUID) -> str:
        return f"quantity_{line_id.hex}"

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        has_quantity = False
        for item in self.progress:
            field_name = self.quantity_field_name(item.line.id)
            quantity = cleaned_data.get(field_name)
            if quantity is None:
                continue
            if not isinstance(quantity, Decimal):
                continue
            has_quantity = True
            try:
                validate_stock_quantity(quantity, item.line.unit_snapshot)
            except ValidationError as error:
                self.add_error(field_name, error)
        if not has_quantity:
            raise forms.ValidationError(_("Enter at least one received quantity."))
        return cleaned_data

    def receipt_quantities(self) -> dict[UUID, Decimal]:
        quantities: dict[UUID, Decimal] = {}
        for item in self.progress:
            value = self.cleaned_data.get(self.quantity_field_name(item.line.id))
            if isinstance(value, Decimal):
                quantities[item.line.id] = value
        return quantities
