from datetime import date, time
from typing import cast

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.public_profiles.models import (
    ContactLinkType,
    PublicLanguage,
    VerificationType,
)


class PublicProfileForm(forms.Form):
    display_name = forms.CharField(max_length=160, label=_("Public display name"))
    description = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    phone = forms.CharField(max_length=40, required=False)
    email = forms.EmailField(required=False)
    website = forms.URLField(
        required=False,
        assume_scheme="https",
        help_text=_("Use an HTTPS URL."),
    )
    address = forms.CharField(max_length=300, required=False)
    map_url = forms.URLField(
        required=False,
        assume_scheme="https",
        help_text=_("Use an HTTPS map link. No map is embedded."),
    )
    supported_languages = forms.MultipleChoiceField(
        choices=PublicLanguage.choices,
        widget=forms.CheckboxSelectMultiple,
    )

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        for field_name in ("website", "map_url"):
            value = str(cleaned.get(field_name) or "")
            if value and not value.startswith("https://"):
                self.add_error(field_name, _("Use a secure HTTPS URL."))
        return cleaned


class OpeningHourForm(forms.Form):
    weekday = forms.IntegerField(widget=forms.HiddenInput)
    weekday_label = forms.CharField(disabled=True, required=False, label=_("Day"))
    is_closed = forms.BooleanField(required=False, label=_("Closed"))
    opens_at = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
        label=_("Opens"),
    )
    closes_at = forms.TimeField(
        required=False,
        widget=forms.TimeInput(attrs={"type": "time"}),
        label=_("Closes"),
    )

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        if cleaned.get("is_closed"):
            cleaned["opens_at"] = None
            cleaned["closes_at"] = None
            return cleaned
        opens_at = cast(time | None, cleaned.get("opens_at"))
        closes_at = cast(time | None, cleaned.get("closes_at"))
        if opens_at is None or closes_at is None:
            raise forms.ValidationError(_("Enter both opening and closing time, or mark closed."))
        if opens_at >= closes_at:
            self.add_error("closes_at", _("Closing time must be later than opening time."))
        return cleaned


OpeningHourFormSet = forms.formset_factory(OpeningHourForm, extra=0, min_num=7, max_num=7)


class ContactLinkForm(forms.Form):
    link_type = forms.ChoiceField(choices=ContactLinkType.choices)
    label = forms.CharField(max_length=80, required=False)
    url = forms.URLField(required=False, assume_scheme="https")
    is_active = forms.BooleanField(required=False, initial=True)

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        label = str(cleaned.get("label") or "").strip()
        url = str(cleaned.get("url") or "").strip()
        if bool(label) != bool(url):
            raise forms.ValidationError(_("Enter both a label and URL, or leave both blank."))
        if url and not url.startswith("https://"):
            self.add_error("url", _("Use a secure HTTPS URL."))
        return cleaned


ContactLinkFormSet = forms.formset_factory(
    ContactLinkForm,
    extra=0,
    min_num=len(ContactLinkType.values),
    max_num=len(ContactLinkType.values),
)


class ProductPublicationForm(forms.Form):
    visible = forms.BooleanField(required=False, label=_("Show publicly"))
    show_public_prices = forms.BooleanField(required=False, label=_("Show current prices"))

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        if cleaned.get("show_public_prices") and not cleaned.get("visible"):
            self.add_error(
                "show_public_prices",
                _("Prices cannot be public while the product is hidden."),
            )
        return cleaned


class IndexingPreferenceForm(forms.Form):
    enabled = forms.BooleanField(required=False)


class VerificationRequestForm(forms.Form):
    indicator_type = forms.ChoiceField(choices=VerificationType.choices)
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Review request"),
    )
    parent_request_id = forms.UUIDField(required=False, widget=forms.HiddenInput)


class VerificationDecisionForm(forms.Form):
    decision = forms.ChoiceField(choices=(("approve", _("Approve")), ("reject", _("Reject"))))
    evidence_reference = forms.CharField(
        max_length=200,
        required=False,
        help_text=_("Private reference only. It is never shown publicly."),
    )
    private_reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    reviewed_on = forms.DateField(
        required=False,
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expires_on = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        if cleaned.get("decision") == "approve" and not cleaned.get("evidence_reference"):
            self.add_error(
                "evidence_reference",
                _("Approved verification requires an evidence reference."),
            )
        reviewed_on = cast(date | None, cleaned.get("reviewed_on"))
        expires_on = cast(date | None, cleaned.get("expires_on"))
        if reviewed_on and expires_on and expires_on <= reviewed_on:
            self.add_error("expires_on", _("Expiry must be later than the review date."))
        return cleaned


class SuspensionForm(forms.Form):
    reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label=_("Private suspension reason"),
    )


class VerificationLifecycleForm(forms.Form):
    indicator_type = forms.ChoiceField(choices=VerificationType.choices)
    action = forms.ChoiceField(
        choices=(
            ("renew", _("Renew")),
            ("revoke", _("Revoke")),
            ("expire", _("Expire now")),
        )
    )
    evidence_reference = forms.CharField(max_length=200, required=False)
    private_reason = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    reviewed_on = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    expires_on = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self) -> dict[str, object]:
        cleaned = cast(dict[str, object], super().clean() or {})
        if cleaned.get("action") == "renew" and not cleaned.get("evidence_reference"):
            self.add_error(
                "evidence_reference",
                _("Renewal requires an evidence reference."),
            )
        return cleaned
