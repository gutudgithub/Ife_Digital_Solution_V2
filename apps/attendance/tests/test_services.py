from datetime import date, datetime, timedelta
from queue import Queue
from threading import Barrier, Thread
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

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

        with self.assertRaisesMessage(ValidationError, "already checked in"):
            check_in(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time + timedelta(minutes=5),
            )

        record = AttendanceRecord.objects.get()
        self.assertEqual(record.check_in_at, self.check_in_time)

    def test_duplicate_race_uses_the_standard_check_in_message(self) -> None:
        AttendanceRecord.objects.create(
            business=self.business,
            employee=self.cashier_membership,
            branch=self.branch,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.PRESENT,
            check_in_at=self.check_in_time,
        )

        with (
            patch("apps.attendance.services.open_shift_for", return_value=None),
            self.assertRaisesMessage(ValidationError, "already checked in"),
        ):
            check_in(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time,
            )

    def test_plain_validation_error_is_preserved_during_check_in(self) -> None:
        with (
            patch(
                "apps.attendance.services.AttendanceRecord.objects.create",
                side_effect=ValidationError("Invalid attendance"),
            ),
            self.assertRaisesMessage(ValidationError, "Invalid attendance"),
        ):
            check_in(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time,
            )

    def test_overnight_check_out_closes_previous_business_date(self) -> None:
        check_in_time = datetime(
            2026,
            9,
            8,
            23,
            50,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )
        check_out_time = datetime(
            2026,
            9,
            9,
            0,
            10,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )
        check_in(membership=self.cashier_membership, recorded_at=check_in_time)

        record = check_out(
            membership=self.cashier_membership,
            recorded_at=check_out_time,
        )

        self.assertEqual(record.work_date, date(2026, 9, 8))
        self.assertEqual(record.check_out_at, check_out_time)

    def test_stale_open_attendance_cannot_be_checked_out(self) -> None:
        check_in(membership=self.cashier_membership, recorded_at=self.check_in_time)

        with self.assertRaisesMessage(ValidationError, "Check in before checking out"):
            check_out(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time + timedelta(hours=18, minutes=1),
            )

    def test_open_overnight_attendance_blocks_a_new_check_in(self) -> None:
        check_in_time = datetime(
            2026,
            9,
            8,
            23,
            50,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )
        check_in(membership=self.cashier_membership, recorded_at=check_in_time)

        with self.assertRaisesMessage(
            ValidationError,
            "Attendance for 2026-09-08 is still open",
        ):
            check_in(
                membership=self.cashier_membership,
                recorded_at=check_in_time + timedelta(minutes=20),
            )

    def test_existing_absence_explains_that_manager_correction_is_required(self) -> None:
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )

        with self.assertRaisesMessage(ValidationError, "Ask a manager to correct it"):
            check_in(
                membership=self.cashier_membership,
                recorded_at=self.check_in_time,
            )

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

    def test_manager_can_correct_attendance_for_deactivated_employee(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        self.cashier_membership.is_active = False
        self.cashier_membership.save(update_fields=("is_active",))

        corrected = correct_attendance(
            actor=self.owner_membership,
            attendance=record,
            status=AttendanceStatus.EXCUSED,
            check_in_at=None,
            check_out_at=None,
            reason="Approved after departure",
        )

        self.assertEqual(corrected.status, AttendanceStatus.EXCUSED)
        self.assertEqual(AttendanceCorrection.objects.count(), 1)

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


@skipUnlessDBFeature("has_select_for_update")
class AttendanceConcurrencyTests(TransactionTestCase):
    def test_simultaneous_check_ins_create_one_record(self) -> None:
        user = User.objects.create_user(
            email="cashier-concurrency@example.com",
            password="strong-test-password",
            full_name="Cashier",
        )
        business = Business.objects.create(name="First Shop", slug="first-shop-concurrency")
        branch = Branch.objects.create(
            business=business,
            name="Main Store",
            code="main",
        )
        membership = BusinessMembership.objects.create(
            business=business,
            user=user,
            assigned_branch=branch,
            role=MembershipRole.CASHIER,
        )
        barrier = Barrier(2)
        outcomes: Queue[str] = Queue()
        check_in_time = datetime(
            2026,
            9,
            8,
            9,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )

        def attempt_check_in() -> None:
            close_old_connections()
            worker_membership = BusinessMembership.objects.select_related(
                "business",
                "assigned_branch",
            ).get(pk=membership.pk)
            barrier.wait()
            try:
                check_in(membership=worker_membership, recorded_at=check_in_time)
            except ValidationError:
                outcomes.put("rejected")
            else:
                outcomes.put("created")
            finally:
                connections.close_all()

        threads = [Thread(target=attempt_check_in) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertCountEqual(
            [outcomes.get_nowait(), outcomes.get_nowait()],
            ["created", "rejected"],
        )
        self.assertEqual(AttendanceRecord.objects.count(), 1)
