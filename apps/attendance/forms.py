from datetime import datetime
from typing import cast

from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import AttendanceRecord, AttendanceStatus
from apps.businesses.models import Branch, Business, BusinessMembership


class AttendanceCreateForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ("employee", "branch", "work_date", "status", "check_in_at", "check_out_at")
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "check_in_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
            "check_out_at": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M",
                attrs={"type": "datetime-local"},
            ),
        }

    def scope_to_business(self, business: Business) -> None:
        self.business = business
        employee_field = cast(forms.ModelChoiceField, self.fields["employee"])
        employee_field.queryset = BusinessMembership.objects.filter(
            business=business,
            is_active=True,
        ).select_related("user")
        branch_field = cast(forms.ModelChoiceField, self.fields["branch"])
        branch_field.queryset = Branch.objects.filter(
            business=business,
            is_active=True,
        )
        if not self.is_bound:
            self.initial["work_date"] = timezone.localdate()
            self.initial["status"] = AttendanceStatus.PRESENT

    def clean(self) -> dict[str, object]:
        return super().clean() or {}


class AttendanceCorrectionForm(forms.Form):
    status = forms.ChoiceField(choices=AttendanceStatus.choices)
    check_in_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    check_out_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(
            format="%Y-%m-%dT%H:%M",
            attrs={"type": "datetime-local"},
        ),
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))

    def set_attendance_initial(self, attendance: AttendanceRecord) -> None:
        self.initial.update(
            {
                "status": attendance.status,
                "check_in_at": attendance.check_in_at,
                "check_out_at": attendance.check_out_at,
            }
        )

    def clean(self) -> dict[str, object]:
        cleaned_data = super().clean() or {}
        status = cleaned_data.get("status")
        check_in_at = cleaned_data.get("check_in_at")
        check_out_at = cleaned_data.get("check_out_at")
        if status == AttendanceStatus.PRESENT and check_in_at is None:
            self.add_error("check_in_at", _("Present attendance requires a check-in time."))
        if status in {AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED} and (
            check_in_at is not None or check_out_at is not None
        ):
            self.add_error(
                "status",
                _("Absent or excused attendance cannot include check-in times."),
            )
        if check_out_at is not None and check_in_at is None:
            self.add_error("check_out_at", _("Check-out requires a check-in time."))
        if (
            isinstance(check_in_at, datetime)
            and isinstance(check_out_at, datetime)
            and check_out_at < check_in_at
        ):
            self.add_error("check_out_at", _("Check-out cannot be earlier than check-in."))
        return cleaned_data
