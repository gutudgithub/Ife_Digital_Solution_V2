from datetime import date, datetime, timedelta

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.attendance.models import AttendanceCorrection, AttendanceRecord, AttendanceStatus
from apps.businesses.models import Branch, BusinessMembership

OPEN_SHIFT_WINDOW = timedelta(hours=18)


def _is_duplicate_attendance(error: ValidationError) -> bool:
    return any(
        item.code == "unique_together" for item in error.error_dict.get(NON_FIELD_ERRORS, ())
    )


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


def open_shift_for(
    membership: BusinessMembership,
    *,
    at: datetime | None = None,
    lock: bool = False,
) -> AttendanceRecord | None:
    timestamp = at or timezone.now()
    scoped = AttendanceRecord.objects.filter(
        business=membership.business,
        employee=membership,
    )
    if lock:
        scoped = scoped.select_for_update()
    today = scoped.filter(work_date=timezone.localdate(timestamp)).first()
    if today is not None:
        return today
    return (
        scoped.filter(
            check_in_at__isnull=False,
            check_out_at__isnull=True,
            check_in_at__gte=timestamp - OPEN_SHIFT_WINDOW,
            check_in_at__lte=timestamp,
        )
        .order_by("-check_in_at")
        .first()
    )


@transaction.atomic
def check_in(
    *,
    membership: BusinessMembership,
    recorded_at: datetime | None = None,
) -> AttendanceRecord:
    timestamp = recorded_at or timezone.now()
    branch = resolve_self_service_branch(membership)
    work_date = timezone.localdate(timestamp)
    existing = open_shift_for(membership, at=timestamp, lock=True)
    if existing is not None:
        if existing.work_date != work_date:
            raise ValidationError(_("Check out before checking in again."))
        if existing.check_in_at is not None:
            raise ValidationError(_("You have already checked in today."))
        raise ValidationError(
            _("Today's attendance was recorded as %(status)s. Ask a manager to correct it.")
            % {"status": existing.get_status_display()}
        )
    try:
        with transaction.atomic():
            return AttendanceRecord.objects.create(
                business=membership.business,
                employee=membership,
                branch=branch,
                work_date=work_date,
                status=AttendanceStatus.PRESENT,
                check_in_at=timestamp,
            )
    except ValidationError as error:
        if not _is_duplicate_attendance(error):
            raise
        raise ValidationError(_("You have already checked in today.")) from error
    except IntegrityError as error:
        raise ValidationError(_("You have already checked in today.")) from error


@transaction.atomic
def check_out(
    *,
    membership: BusinessMembership,
    recorded_at: datetime | None = None,
) -> AttendanceRecord:
    timestamp = recorded_at or timezone.now()
    record = open_shift_for(membership, at=timestamp, lock=True)
    if record is None or record.check_in_at is None:
        raise ValidationError(_("Check in before checking out."))
    if record.check_out_at is not None:
        raise ValidationError(_("Attendance has already been checked out."))
    record.check_out_at = timestamp
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
    correction.save()
    locked.save()
    return locked
