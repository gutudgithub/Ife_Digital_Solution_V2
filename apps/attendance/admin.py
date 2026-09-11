from django.contrib import admin
from django.http import HttpRequest

from apps.attendance.models import AttendanceCorrection, AttendanceRecord


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "work_date",
        "employee",
        "business",
        "branch",
        "status",
        "check_in_at",
        "check_out_at",
    )
    list_filter = ("status", "work_date", "branch")
    search_fields = (
        "employee__user__email",
        "employee__user__full_name",
        "business__name",
        "branch__name",
    )
    readonly_fields = (
        "business",
        "branch",
        "employee",
        "work_date",
        "status",
        "check_in_at",
        "check_out_at",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: AttendanceRecord | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: AttendanceRecord | None = None,
    ) -> bool:
        return False


@admin.register(AttendanceCorrection)
class AttendanceCorrectionAdmin(admin.ModelAdmin):
    list_display = ("attendance", "corrected_by", "reason", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "attendance__employee__user__email",
        "corrected_by__user__email",
        "reason",
    )
    readonly_fields = (
        "business",
        "attendance",
        "corrected_by",
        "reason",
        "previous_status",
        "replacement_status",
        "previous_check_in_at",
        "replacement_check_in_at",
        "previous_check_out_at",
        "replacement_check_out_at",
        "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self,
        request: HttpRequest,
        obj: AttendanceCorrection | None = None,
    ) -> bool:
        return False

    def has_delete_permission(
        self,
        request: HttpRequest,
        obj: AttendanceCorrection | None = None,
    ) -> bool:
        return False
