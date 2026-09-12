import uuid
from collections.abc import Mapping
from decimal import Decimal
from typing import cast

from django import forms
from django.db.models import QuerySet
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership
from apps.catalog.models import ProductVariant
from apps.sales.models import (
    Sale,
    SaleLine,
    SalePaymentMethod,
    SaleStatus,
    calculate_sale_line_total,
)


def sale_branch_queryset(membership: BusinessMembership) -> QuerySet[Branch]:
    branches = Branch.objects.filter(business=membership.business, is_active=True)
    if membership.can_sell_across_branches:
        return branches
    if membership.assigned_branch_id:
        return branches.filter(pk=membership.assigned_branch_id)
    active_branch_ids = list(branches.values_list("id", flat=True)[:2])
    if len(active_branch_ids) == 1:
        return branches.filter(pk=active_branch_ids[0])
    return branches.none()


class SaleForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = ("branch", "sale_date")
        widgets = {"sale_date": forms.DateInput(attrs={"type": "date"})}

    def scope_to_membership(self, membership: BusinessMembership) -> None:
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = sale_branch_queryset(membership)
        if not self.is_bound:
            self.initial.setdefault("sale_date", timezone.localdate())
            if branch_field.queryset.count() == 1:
                self.initial.setdefault("branch", branch_field.queryset.first())


class SaleLineForm(forms.ModelForm):
    quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        max_digits=18,
        decimal_places=3,
        label=_("Quantity"),
        error_messages={"min_value": _("Sale quantity must be greater than zero.")},
    )

    class Meta:
        model = SaleLine
        fields = ("variant", "quantity")

    def _post_clean(self) -> None:
        variant = self.cleaned_data.get("variant")
        quantity = self.cleaned_data.get("quantity")
        if isinstance(variant, ProductVariant) and isinstance(quantity, Decimal):
            self.instance.product_name_snapshot = variant.product.name
            self.instance.sku_snapshot = variant.sku
            self.instance.unit_snapshot = variant.stock_unit
            self.instance.selling_unit_price = variant.selling_price
            self.instance.line_total = calculate_sale_line_total(
                quantity,
                variant.selling_price,
            )
        super()._post_clean()  # type: ignore[misc]

    def scope_to_business(self, business: Business) -> None:
        variant_field = cast(forms.ModelChoiceField, self.fields["variant"])
        variant_field.queryset = ProductVariant.objects.filter(
            business=business,
            is_active=True,
            product__is_active=True,
        ).select_related("product")


class BaseSaleLineFormSet(BaseInlineFormSet):
    business: Business

    def scope_to_business(self, business: Business) -> None:
        self.business = business
        for form in self.forms:
            if not isinstance(form, SaleLineForm):
                raise TypeError("Sale line formset must use SaleLineForm.")
            form.instance.business = business
            form.scope_to_business(business)

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        seen_variants: set[object] = set()
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            variant = form.cleaned_data.get("variant")
            if variant is None:
                continue
            if variant.pk in seen_variants:
                raise forms.ValidationError(_("A sale cannot repeat the same product variant."))
            seen_variants.add(variant.pk)


SaleLineFormSet = inlineformset_factory(
    Sale,
    SaleLine,
    form=SaleLineForm,
    formset=BaseSaleLineFormSet,
    fields=("variant", "quantity"),
    extra=2,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class SalePostForm(forms.Form):
    payment_method = forms.ChoiceField(
        choices=SalePaymentMethod.choices,
        label=_("Payment method"),
    )
    telebirr_reference = forms.CharField(
        required=False,
        max_length=120,
        label=_("Telebirr transaction reference"),
        help_text=_("Required for Telebirr. This is manually entered operational evidence."),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        if not self.is_bound:
            self.initial["idempotency_key"] = uuid.uuid4()

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        method = cleaned_data.get("payment_method")
        reference = str(cleaned_data.get("telebirr_reference") or "").strip()
        if method == SalePaymentMethod.TELEBIRR and not reference:
            self.add_error(
                "telebirr_reference",
                _("Enter the Telebirr transaction reference."),
            )
        if method == SalePaymentMethod.CASH and reference:
            self.add_error(
                "telebirr_reference",
                _("Cash payments cannot include a Telebirr reference."),
            )
        cleaned_data["telebirr_reference"] = reference
        return cleaned_data


class SaleCancelForm(forms.Form):
    reason = forms.CharField(
        label=_("Cancellation reason"),
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class SaleFilterForm(forms.Form):
    search = forms.CharField(
        required=False,
        max_length=120,
        label=_("Sale, receipt, or Telebirr reference"),
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label=_("Branch"),
    )
    status = forms.ChoiceField(
        choices=(("", _("All statuses")), *SaleStatus.choices),
        required=False,
        label=_("Status"),
    )
    payment_method = forms.ChoiceField(
        choices=(("", _("All payment methods")), *SalePaymentMethod.choices),
        required=False,
        label=_("Payment method"),
    )
    date_from = forms.DateField(
        required=False,
        label=_("Sale date from"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        label=_("Sale date to"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def scope_to_membership(self, membership: BusinessMembership) -> None:
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = sale_branch_queryset(membership)

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError(_("The start date cannot be after the end date."))
        return cleaned_data


class ReceiptLookupForm(forms.Form):
    receipt_number = forms.CharField(
        max_length=40,
        label=_("Internal receipt number"),
    )
