import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from apps.businesses.models import Branch, Business, BusinessMembership


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", _("Present")
    ABSENT = "absent", _("Absent")
    EXCUSED = "excused", _("Excused")


class AttendanceRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    employee = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    work_date = models.DateField()
    status = models.CharField(
        max_length=16,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.PRESENT,
    )
    check_in_at = models.DateTimeField(blank=True, null=True)
    check_out_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-work_date", "-check_in_at", "employee")
        constraints = [
            models.UniqueConstraint(
                fields=("business", "employee", "work_date"),
                name="attendance_unique_employee_business_date",
            ),
            models.CheckConstraint(
                condition=Q(check_out_at__isnull=True) | Q(check_in_at__isnull=False),
                name="attendance_checkout_requires_checkin",
            ),
            models.CheckConstraint(
                condition=Q(check_out_at__isnull=True)
                | Q(check_out_at__gte=models.F("check_in_at")),
                name="attendance_checkout_not_before_checkin",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.employee.user} — {self.work_date}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.branch_id and self.branch.business_id != self.business_id:
            errors["branch"] = ValidationError(_("The branch must belong to this business."))
        if self.employee_id and self.employee.business_id != self.business_id:
            errors["employee"] = ValidationError(_("The employee must belong to this business."))
        if self.check_out_at is not None and self.check_in_at is None:
            errors["check_out_at"] = ValidationError(_("Check-out requires a check-in time."))
        if (
            self.check_in_at is not None
            and self.check_out_at is not None
            and self.check_out_at < self.check_in_at
        ):
            errors["check_out_at"] = ValidationError(
                _("Check-out cannot be earlier than check-in.")
            )
        if self.status == AttendanceStatus.PRESENT and self.check_in_at is None:
            errors["check_in_at"] = ValidationError(
                _("Present attendance requires a check-in time.")
            )
        if self.status in {AttendanceStatus.ABSENT, AttendanceStatus.EXCUSED} and (
            self.check_in_at is not None or self.check_out_at is not None
        ):
            errors["status"] = ValidationError(
                _("Absent or excused attendance cannot include check-in times.")
            )
        if errors:
            raise ValidationError(errors)


class AttendanceCorrection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="attendance_corrections",
    )
    attendance = models.ForeignKey(
        AttendanceRecord,
        on_delete=models.PROTECT,
        related_name="corrections",
    )
    corrected_by = models.ForeignKey(
        BusinessMembership,
        on_delete=models.PROTECT,
        related_name="attendance_corrections_made",
    )
    reason = models.TextField()
    previous_status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    replacement_status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    previous_check_in_at = models.DateTimeField(blank=True, null=True)
    replacement_check_in_at = models.DateTimeField(blank=True, null=True)
    previous_check_out_at = models.DateTimeField(blank=True, null=True)
    replacement_check_out_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.attendance} corrected by {self.corrected_by.user}"

    def clean(self) -> None:
        super().clean()
        errors: dict[str, ValidationError] = {}
        if self.attendance_id and self.attendance.business_id != self.business_id:
            errors["attendance"] = ValidationError(
                _("The attendance record must belong to this business.")
            )
        if self.corrected_by_id and self.corrected_by.business_id != self.business_id:
            errors["corrected_by"] = ValidationError(
                _("The correcting employee must belong to this business.")
            )
        if self.corrected_by_id and not self.corrected_by.can_manage_attendance:
            errors["corrected_by"] = ValidationError(
                _("The correcting employee cannot manage attendance.")
            )
        if not self.reason.strip():
            errors["reason"] = ValidationError(_("A correction reason is required."))
        if errors:
            raise ValidationError(errors)
