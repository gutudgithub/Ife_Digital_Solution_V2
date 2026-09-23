import uuid
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import cast

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.forms import BaseFormSet, formset_factory
from django.utils import timezone
from django.utils.datastructures import MultiValueDict
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business
from apps.catalog.models import ProductVariant, validate_stock_quantity
from apps.documents.models import (
    DocumentKind,
    DocumentStatus,
    DocumentTranscription,
)
from apps.documents.services import TranscriptionLineInput
from apps.expenses.models import ExpenseCategory, OperationalPaymentMethod
from apps.purchasing.models import Supplier


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(
        self,
        data: object,
        initial: object | None = None,
    ) -> list[object]:
        single_clean = super().clean
        if isinstance(data, list | tuple):
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]


class DocumentCaptureForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none(), label=_("Branch"))
    kind = forms.ChoiceField(choices=DocumentKind.choices, label=_("Document workflow"))
    title = forms.CharField(
        required=False,
        max_length=180,
        label=_("Private document label"),
        help_text=_("Use a short operational label. Do not enter unnecessary personal data."),
    )
    source_files = MultipleFileField(
        widget=MultipleFileInput(
            attrs={
                "accept": "image/jpeg,image/png,application/pdf",
                "capture": "environment",
            }
        ),
        label=_("Source files"),
        help_text=_("Select one to five JPEG, PNG, or PDF files; 10 MiB each, 25 MiB total."),
    )

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        files: MultiValueDict[str, UploadedFile] | None = None,
        *,
        business: Business,
    ) -> None:
        super().__init__(data=data, files=files)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        if not self.is_bound and branch_field.queryset.count() == 1:
            self.initial["branch"] = branch_field.queryset.first()


class DocumentFilterForm(forms.Form):
    kind = forms.ChoiceField(
        required=False,
        choices=(("", _("All workflows")), *DocumentKind.choices),
    )
    status = forms.ChoiceField(
        required=False,
        choices=(("", _("All source statuses")), *DocumentStatus.choices),
    )


class TranscriptionLineForm(forms.Form):
    variant = forms.ModelChoiceField(
        queryset=ProductVariant.objects.none(),
        label=_("Product variant"),
    )
    quantity = forms.DecimalField(
        min_value=Decimal("0.001"),
        max_digits=18,
        decimal_places=3,
        label=_("Quantity"),
    )
    unit_cost = forms.DecimalField(
        min_value=Decimal("0.000000"),
        max_digits=18,
        decimal_places=6,
        label=_("Unit cost"),
    )
    note = forms.CharField(required=False, max_length=180, label=_("Line note"))

    def scope_to_business(self, business: Business) -> None:
        variant_field = cast(forms.ModelChoiceField, self.fields["variant"])
        variant_field.queryset = ProductVariant.objects.filter(
            business=business,
            is_active=True,
        ).select_related("product")

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


class BaseTranscriptionLineFormSet(BaseFormSet):
    business: Business

    def scope_to_business(self, business: Business) -> None:
        self.business = business
        for form in self.forms:
            if not isinstance(form, TranscriptionLineForm):
                raise TypeError("Document line formset must use TranscriptionLineForm.")
            form.scope_to_business(business)

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        seen: set[object] = set()
        count = 0
        for form in self.forms:
            if self.can_delete and form.cleaned_data.get("DELETE"):
                continue
            variant = form.cleaned_data.get("variant")
            if variant is None:
                continue
            count += 1
            if variant in seen:
                form.add_error("variant", _("Select each product variant only once."))
            seen.add(variant)
        if count == 0:
            raise ValidationError(_("Enter at least one document line."))

    def line_inputs(self) -> list[TranscriptionLineInput]:
        inputs: list[TranscriptionLineInput] = []
        for form in self.forms:
            if self.can_delete and form.cleaned_data.get("DELETE"):
                continue
            variant = form.cleaned_data.get("variant")
            quantity = form.cleaned_data.get("quantity")
            unit_cost = form.cleaned_data.get("unit_cost")
            note = form.cleaned_data.get("note", "")
            if (
                isinstance(variant, ProductVariant)
                and isinstance(quantity, Decimal)
                and isinstance(unit_cost, Decimal)
                and isinstance(note, str)
            ):
                inputs.append(
                    TranscriptionLineInput(
                        variant=variant,
                        quantity=quantity,
                        unit_cost=unit_cost,
                        note=note,
                    )
                )
        return inputs


TranscriptionLineFormSet = formset_factory(
    TranscriptionLineForm,
    formset=BaseTranscriptionLineFormSet,
    extra=3,
    can_delete=True,
)


def transcription_line_initial(
    transcription: DocumentTranscription,
) -> list[dict[str, object]]:
    return [
        {
            "variant": line.variant,
            "quantity": line.quantity,
            "unit_cost": line.unit_cost,
            "note": line.note,
        }
        for line in transcription.lines.select_related("variant")
    ]


class PurchaseTranscriptionForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    purchase_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    supplier_reference = forms.CharField(required=False, max_length=120)
    expected_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    settlement_terms = forms.CharField(required=False, max_length=180)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        transcription: DocumentTranscription,
    ) -> None:
        super().__init__(data=data)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        supplier_field = cast(forms.ModelChoiceField, self.fields["supplier"])
        supplier_field.queryset = Supplier.objects.filter(business=business, is_active=True)
        self.initial.update(
            {
                "branch": transcription.branch,
                "supplier": transcription.supplier,
                "purchase_date": transcription.purchase_date or timezone.localdate(),
                "supplier_reference": transcription.supplier_reference,
                "expected_date": transcription.expected_date,
                "settlement_terms": transcription.settlement_terms,
            }
        )

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        purchase_date = cleaned_data.get("purchase_date")
        expected_date = cleaned_data.get("expected_date")
        if (
            isinstance(purchase_date, date)
            and isinstance(expected_date, date)
            and expected_date < purchase_date
        ):
            self.add_error(
                "expected_date",
                _("Expected date cannot be earlier than purchase date."),
            )
        return cleaned_data


class ExpenseTranscriptionForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())
    expense_category = forms.ModelChoiceField(
        queryset=ExpenseCategory.objects.none(),
        label=_("Expense category"),
    )
    document_date = forms.DateField(
        label=_("Date shown on source"),
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    payee = forms.CharField(max_length=180)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    amount = forms.DecimalField(
        min_value=Decimal("0.01"),
        max_digits=18,
        decimal_places=2,
    )
    payment_method = forms.ChoiceField(choices=OperationalPaymentMethod.choices)
    telebirr_reference = forms.CharField(required=False, max_length=120)

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        transcription: DocumentTranscription,
    ) -> None:
        super().__init__(data=data)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        category_field = cast(forms.ModelChoiceField, self.fields["expense_category"])
        category_field.queryset = ExpenseCategory.objects.filter(
            business=business,
            is_active=True,
        )
        self.initial.update(
            {
                "branch": transcription.branch,
                "expense_category": transcription.expense_category,
                "document_date": (transcription.expense_document_date or timezone.localdate()),
                "payee": transcription.expense_payee,
                "description": transcription.expense_description,
                "amount": transcription.expense_amount,
                "payment_method": (
                    transcription.expense_payment_method or OperationalPaymentMethod.CASH
                ),
                "telebirr_reference": transcription.expense_telebirr_reference,
            }
        )

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        method = cleaned_data.get("payment_method")
        reference = str(cleaned_data.get("telebirr_reference", "")).strip()
        if method == OperationalPaymentMethod.CASH and reference:
            self.add_error(
                "telebirr_reference",
                _("Cash evidence cannot include a Telebirr reference."),
            )
        if method == OperationalPaymentMethod.TELEBIRR and not reference:
            self.add_error(
                "telebirr_reference",
                _("A Telebirr reference is required."),
            )
        return cleaned_data


class OpeningStockTranscriptionForm(forms.Form):
    branch = forms.ModelChoiceField(queryset=Branch.objects.none())

    def __init__(
        self,
        data: Mapping[str, object] | None = None,
        *,
        business: Business,
        transcription: DocumentTranscription,
    ) -> None:
        super().__init__(data=data)
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(business=business, is_active=True)
        self.initial["branch"] = transcription.branch


class ConfirmationForm(forms.Form):
    confirmation_key = forms.UUIDField(widget=forms.HiddenInput)
    confirmed = forms.BooleanField(
        label=_(
            "I compared every transcribed field with the source files and confirm this snapshot."
        )
    )

    def __init__(self, data: Mapping[str, object] | None = None) -> None:
        super().__init__(data=data)
        if not self.is_bound:
            self.initial["confirmation_key"] = uuid.uuid4()


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
