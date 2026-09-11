from datetime import date, datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import AttendanceCorrection, AttendanceRecord, AttendanceStatus
from apps.businesses.models import Branch, BusinessMembership


def resolve_self_service_branch(membership: BusinessMembership) -> Branch:
    if not membership.is_active or not membership.business.is_active:
        raise ValidationError(_("An active business membership is required."))
    if membership.assigned_branch_id is not None:
        branch = membership.assigned_branch
        if branch is None:
            raise ValidationError(_("Your assigned branch is not available."))
        if branch.business_id != membership.business_id or not branch.is_active:
            raise ValidationError(_("Your assigned branch is not available."))
        return branch
    branches = list(membership.business.branches.filter(is_active=True)[:2])
    if len(branches) != 1:
        raise ValidationError(_("Ask a manager to assign your branch before recording attendance."))
    return branches[0]


@transaction.atomic
def check_in(
    *,
    membership: BusinessMembership,
    recorded_at: datetime | None = None,
) -> AttendanceRecord:
    timestamp = recorded_at or timezone.now()
    branch = resolve_self_service_branch(membership)
    record, created = AttendanceRecord.objects.get_or_create(
        business=membership.business,
        employee=membership,
        work_date=timezone.localdate(timestamp),
        defaults={
            "branch": branch,
            "status": AttendanceStatus.PRESENT,
            "check_in_at": timestamp,
        },
    )
    if created:
        return record
    raise ValidationError(_("An attendance record already exists for today."))


@transaction.atomic
def check_out(
    *,
    membership: BusinessMembership,
    recorded_at: datetime | None = None,
) -> AttendanceRecord:
    timestamp = recorded_at or timezone.now()
    record = (
        AttendanceRecord.objects.select_for_update()
        .filter(
            business=membership.business,
            employee=membership,
            work_date=timezone.localdate(timestamp),
        )
        .first()
    )
    if record is None or record.check_in_at is None:
        raise ValidationError(_("Check in before checking out."))
    if record.check_out_at is not None:
        raise ValidationError(_("Attendance has already been checked out for today."))
    record.check_out_at = timestamp
    record.full_clean()
    record.save()
    return record


@transaction.atomic
def create_attendance(
    *,
    actor: BusinessMembership,
    employee: BusinessMembership,
    branch: Branch,
    work_date: date,
    status: str,
    check_in_at: datetime | None,
    check_out_at: datetime | None,
) -> AttendanceRecord:
    if not actor.can_manage_attendance:
        raise ValidationError(_("You cannot manage attendance."))
    if employee.business_id != actor.business_id or branch.business_id != actor.business_id:
        raise ValidationError(_("Employee and branch must belong to the active business."))
    if not employee.is_active or not branch.is_active:
        raise ValidationError(_("Employee and branch must be active."))
    record = AttendanceRecord(
        business=actor.business,
        employee=employee,
        branch=branch,
        work_date=work_date,
        status=status,
        check_in_at=check_in_at,
        check_out_at=check_out_at,
    )
    record.full_clean()
    record.save()
    return record


@transaction.atomic
def correct_attendance(
    *,
    actor: BusinessMembership,
    attendance: AttendanceRecord,
    status: str,
    check_in_at: datetime | None,
    check_out_at: datetime | None,
    reason: str,
) -> AttendanceRecord:
    if not actor.can_manage_attendance or actor.business_id != attendance.business_id:
        raise ValidationError(_("You cannot manage this attendance record."))
    locked = AttendanceRecord.objects.select_for_update().get(
        pk=attendance.pk,
        business=actor.business,
    )
    previous_status = locked.status
    previous_check_in_at = locked.check_in_at
    previous_check_out_at = locked.check_out_at
    locked.status = status
    locked.check_in_at = check_in_at
    locked.check_out_at = check_out_at
    locked.full_clean()
    correction = AttendanceCorrection(
        business=locked.business,
        attendance=locked,
        corrected_by=actor,
        reason=reason.strip(),
        previous_status=previous_status,
        replacement_status=status,
        previous_check_in_at=previous_check_in_at,
        replacement_check_in_at=check_in_at,
        previous_check_out_at=previous_check_out_at,
        replacement_check_out_at=check_out_at,
    )
    correction.full_clean()
    correction.save()
    locked.save()
    return locked
