from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import User
from apps.attendance.models import AttendanceCorrection, AttendanceRecord, AttendanceStatus
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole


class AttendanceModelTests(TestCase):
    business: Business
    other_business: Business
    branch: Branch
    other_branch: Branch
    owner_membership: BusinessMembership
    cashier_membership: BusinessMembership

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
        self.other_business = Business.objects.create(name="Second Shop", slug="second-shop")
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main Store",
            code="main",
        )
        self.other_branch = Branch.objects.create(
            business=self.other_business,
            name="Other Store",
            code="other",
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

    def test_membership_rejects_branch_from_another_business(self) -> None:
        self.cashier_membership.assigned_branch = self.other_branch

        with self.assertRaises(ValidationError):
            self.cashier_membership.full_clean()

    def test_membership_save_rejects_branch_from_another_business(self) -> None:
        self.cashier_membership.assigned_branch = self.other_branch

        with self.assertRaises(ValidationError):
            self.cashier_membership.save()

    def test_attendance_rejects_cross_business_branch(self) -> None:
        record = AttendanceRecord(
            business=self.business,
            branch=self.other_branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )

        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_attendance_save_rejects_cross_business_branch(self) -> None:
        record = AttendanceRecord(
            business=self.business,
            branch=self.other_branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )

        with self.assertRaises(ValidationError):
            record.save()

    def test_attendance_rejects_checkout_before_checkin(self) -> None:
        check_in = datetime(2026, 9, 8, 9, tzinfo=ZoneInfo("Africa/Addis_Ababa"))
        record = AttendanceRecord(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.PRESENT,
            check_in_at=check_in,
            check_out_at=check_in - timedelta(minutes=1),
        )

        with self.assertRaises(ValidationError):
            record.full_clean()

    def test_attendance_is_unique_per_employee_and_business_date(self) -> None:
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.bulk_create(
                [
                    AttendanceRecord(
                        business=self.business,
                        branch=self.branch,
                        employee=self.cashier_membership,
                        work_date=date(2026, 9, 8),
                        status=AttendanceStatus.EXCUSED,
                    )
                ]
            )

    def test_database_rejects_unknown_attendance_status(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.bulk_create(
                [
                    AttendanceRecord(
                        business=self.business,
                        branch=self.branch,
                        employee=self.cashier_membership,
                        work_date=date(2026, 9, 8),
                        status="unknown",
                    )
                ]
            )

    def test_database_rejects_present_attendance_without_check_in(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            AttendanceRecord.objects.bulk_create(
                [
                    AttendanceRecord(
                        business=self.business,
                        branch=self.branch,
                        employee=self.cashier_membership,
                        work_date=date(2026, 9, 8),
                        status=AttendanceStatus.PRESENT,
                    )
                ]
            )

    def test_correction_rejects_non_manager_actor(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        correction = AttendanceCorrection(
            business=self.business,
            attendance=record,
            corrected_by=self.cashier_membership,
            reason="Incorrect status",
            previous_status=AttendanceStatus.ABSENT,
            replacement_status=AttendanceStatus.EXCUSED,
        )

        with self.assertRaises(ValidationError):
            correction.full_clean()

    def test_saved_correction_cannot_be_changed_or_deleted(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        correction = AttendanceCorrection.objects.create(
            business=self.business,
            attendance=record,
            corrected_by=self.owner_membership,
            reason="Approved absence",
            previous_status=AttendanceStatus.ABSENT,
            replacement_status=AttendanceStatus.EXCUSED,
        )
        correction.reason = "Changed reason"

        with self.assertRaisesMessage(ValidationError, "cannot be modified"):
            correction.save()
        with self.assertRaisesMessage(ValidationError, "cannot be deleted"):
            correction.delete()
