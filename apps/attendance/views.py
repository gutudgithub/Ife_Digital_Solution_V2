from datetime import date, datetime
from typing import cast
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from apps.attendance.forms import (
    AttendanceCorrectionForm,
    AttendanceCreateForm,
    AttendanceFilterForm,
    add_accessible_error_attributes,
)
from apps.attendance.models import AttendanceRecord
from apps.attendance.services import (
    check_in,
    check_out,
    correct_attendance,
    create_attendance,
    open_shift_for,
)
from apps.businesses.models import Branch, Business, BusinessMembership
from apps.businesses.types import TenantRequest


def _tenant_context(request: HttpRequest) -> tuple[Business, BusinessMembership]:
    tenant_request = cast(TenantRequest, request)
    if tenant_request.active_business is None or tenant_request.active_membership is None:
        raise PermissionDenied(_("No active business membership is available."))
    return tenant_request.active_business, tenant_request.active_membership


@login_required
def attendance_list(request: HttpRequest) -> HttpResponse:
    business, membership = _tenant_context(request)
    filter_form = AttendanceFilterForm(request.GET or None)
    records = AttendanceRecord.objects.filter(business=business).select_related(
        "branch",
        "employee__user",
    )
    if not membership.can_manage_attendance:
        records = records.filter(employee=membership)
    if filter_form.is_valid():
        work_date = filter_form.cleaned_data.get("work_date")
        if isinstance(work_date, date):
            records = records.filter(work_date=work_date)
    add_accessible_error_attributes(filter_form)
    page_obj = Paginator(records, 50).get_page(request.GET.get("page"))
    open_record = open_shift_for(membership)
    return render(
        request,
        "attendance/attendance_list.html",
        {
            "attendance_records": page_obj.object_list,
            "can_manage_attendance": membership.can_manage_attendance,
            "filter_form": filter_form,
            "open_record": open_record,
            "page_obj": page_obj,
        },
    )


@login_required
@require_POST
def attendance_check_in(request: HttpRequest) -> HttpResponse:
    membership = _tenant_context(request)[1]
    try:
        check_in(membership=membership)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, _("Attendance check-in recorded."))
    return redirect("attendance:attendance-list")


@login_required
@require_POST
def attendance_check_out(request: HttpRequest) -> HttpResponse:
    membership = _tenant_context(request)[1]
    try:
        check_out(membership=membership)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, _("Attendance check-out recorded."))
    return redirect("attendance:attendance-list")


@login_required
def attendance_create(request: HttpRequest) -> HttpResponse:
    business, membership = _tenant_context(request)
    if not membership.can_manage_attendance:
        raise PermissionDenied(_("Your role cannot create attendance records."))
    form = AttendanceCreateForm(request.POST or None)
    form.instance.business = business
    form.scope_to_business(business)
    if request.method == "POST" and form.is_valid():
        employee = cast(BusinessMembership, form.cleaned_data["employee"])
        branch = cast(Branch, form.cleaned_data["branch"])
        work_date = cast(date, form.cleaned_data["work_date"])
        status = cast(str, form.cleaned_data["status"])
        check_in_at = cast(datetime | None, form.cleaned_data["check_in_at"])
        check_out_at = cast(datetime | None, form.cleaned_data["check_out_at"])
        try:
            create_attendance(
                actor=membership,
                employee=employee,
                branch=branch,
                work_date=work_date,
                status=status,
                check_in_at=check_in_at,
                check_out_at=check_out_at,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Attendance record created."))
            return redirect("attendance:attendance-list")
    add_accessible_error_attributes(form)
    return render(request, "attendance/attendance_form.html", {"form": form})


@login_required
def attendance_detail(request: HttpRequest, attendance_id: UUID) -> HttpResponse:
    business, membership = _tenant_context(request)
    record = get_object_or_404(
        AttendanceRecord.objects.select_related("branch", "employee__user"),
        pk=attendance_id,
        business=business,
    )
    if not membership.can_manage_attendance and record.employee_id != membership.id:
        raise PermissionDenied(_("Your role cannot view this attendance record."))
    return render(
        request,
        "attendance/attendance_detail.html",
        {
            "attendance_record": record,
            "corrections": record.corrections.select_related("corrected_by__user"),
            "can_manage_attendance": membership.can_manage_attendance,
        },
    )


@login_required
def attendance_correct(request: HttpRequest, attendance_id: UUID) -> HttpResponse:
    business, membership = _tenant_context(request)
    if not membership.can_manage_attendance:
        raise PermissionDenied(_("Your role cannot correct attendance records."))
    record = get_object_or_404(
        AttendanceRecord.objects.select_related("branch", "employee__user"),
        pk=attendance_id,
        business=business,
    )
    form = AttendanceCorrectionForm(request.POST or None)
    if not form.is_bound:
        form.set_attendance_initial(record)
    if request.method == "POST" and form.is_valid():
        status = cast(str, form.cleaned_data["status"])
        check_in_at = cast(datetime | None, form.cleaned_data["check_in_at"])
        check_out_at = cast(datetime | None, form.cleaned_data["check_out_at"])
        reason = cast(str, form.cleaned_data["reason"])
        try:
            correct_attendance(
                actor=membership,
                attendance=record,
                status=status,
                check_in_at=check_in_at,
                check_out_at=check_out_at,
                reason=reason,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, _("Attendance correction recorded."))
            return redirect("attendance:attendance-detail", attendance_id=record.id)
    add_accessible_error_attributes(form)
    return render(
        request,
        "attendance/attendance_correction_form.html",
        {"attendance_record": record, "form": form},
    )
