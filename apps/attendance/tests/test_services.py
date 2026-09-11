from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.attendance.models import AttendanceCorrection, AttendanceRecord, AttendanceStatus
from apps.attendance.services import (
    check_in,
    check_out,
    correct_attendance,
    create_attendance,
    resolve_self_service_branch,
)
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole


class AttendanceServiceTests(TestCase):
    business: Business
    branch: Branch
    owner_membership: BusinessMembership
    cashier_membership: BusinessMembership
    check_in_time: datetime

    def setUp(self) -> None:
        owner = User.objects.create_user(
            email="owner@example.com",
            password="strong-test-password",
            full_name="Owner",
        )
        cashier = User.objects.create_user(
            email="cashier@example.com",
            password="strong-test-password",
            full_name="Cashier",
        )
        self.business = Business.objects.create(name="First Shop", slug="first-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        self.owner_membership = BusinessMembership.objects.create(
            business=self.business,
            user=owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.check_in_time = datetime(
            2026,
            9,
            8,
            8,
            30,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )

    def test_check_in_and_out_record_server_times_once(self) -> None:
        record = check_in(
            membership=self.cashier_membership,
            recorded_at=self.check_in_time,
        )
        check_out_time = self.check_in_time + timedelta(hours=8)

        checked_out = check_out(
            membership=self.cashier_membership,
            recorded_at=check_out_time,
        )

        self.assertEqual(AttendanceRecord.objects.count(), 1)
        self.assertEqual(record.id, checked_out.id)
        self.assertEqual(checked_out.check_in_at, self.check_in_time)
        self.assertEqual(checked_out.check_out_at, check_out_time)

    def test_repeated_check_in_is_rejected_without_overwrite(self) -> None:
        check_in(
            membership=self.cashier_membership,
            recorded_at=self.check_in_time,
        )

        with self.assertRaisesMessage(ValidationError, "already exists"):
            check_in(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time + timedelta(minutes=5),
            )

        record = AttendanceRecord.objects.get()
        self.assertEqual(record.check_in_at, self.check_in_time)

    def test_unassigned_employee_uses_only_active_branch(self) -> None:
        self.cashier_membership.assigned_branch = None
        self.cashier_membership.save(update_fields=("assigned_branch",))

        branch = resolve_self_service_branch(self.cashier_membership)

        self.assertEqual(branch, self.branch)

    def test_unassigned_employee_requires_assignment_when_multiple_branches_exist(self) -> None:
        self.cashier_membership.assigned_branch = None
        self.cashier_membership.save(update_fields=("assigned_branch",))
        Branch.objects.create(
            business=self.business,
            name="Second Store",
            code="second",
        )

        with self.assertRaisesMessage(ValidationError, "assign your branch"):
            resolve_self_service_branch(self.cashier_membership)

    def test_manager_correction_preserves_before_and_after_values(self) -> None:
        record = check_in(
            membership=self.cashier_membership,
            recorded_at=self.check_in_time,
        )

        corrected = correct_attendance(
            actor=self.owner_membership,
            attendance=record,
            status=AttendanceStatus.EXCUSED,
            check_in_at=None,
            check_out_at=None,
            reason="Approved medical absence",
        )

        correction = AttendanceCorrection.objects.get()
        self.assertEqual(corrected.status, AttendanceStatus.EXCUSED)
        self.assertIsNone(corrected.check_in_at)
        self.assertEqual(correction.previous_status, AttendanceStatus.PRESENT)
        self.assertEqual(correction.previous_check_in_at, self.check_in_time)
        self.assertEqual(correction.replacement_status, AttendanceStatus.EXCUSED)
        self.assertEqual(correction.reason, "Approved medical absence")
        self.assertEqual(correction.corrected_by, self.owner_membership)

    def test_create_attendance_rejects_cross_business_employee_and_branch(self) -> None:
        other_user = User.objects.create_user(
            email="other@example.com",
            password="strong-test-password",
            full_name="Other Cashier",
        )
        other_business = Business.objects.create(name="Other Shop", slug="other-shop")
        other_branch = Branch.objects.create(
            business=other_business,
            name="Other Branch",
            code="other",
        )
        other_membership = BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
            assigned_branch=other_branch,
            role=MembershipRole.CASHIER,
        )

        with self.assertRaisesMessage(ValidationError, "active business"):
            create_attendance(
                actor=self.owner_membership,
                employee=other_membership,
                branch=other_branch,
                work_date=date(2026, 9, 8),
                status=AttendanceStatus.ABSENT,
                check_in_at=None,
                check_out_at=None,
            )
