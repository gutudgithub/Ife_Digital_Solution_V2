from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.attendance.models import AttendanceCorrection, AttendanceRecord, AttendanceStatus
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole


class AttendanceViewTests(TestCase):
    business: Business
    other_business: Business
    branch: Branch
    other_branch: Branch
    owner: User
    cashier: User
    other_user: User
    owner_membership: BusinessMembership
    cashier_membership: BusinessMembership
    other_membership: BusinessMembership
    check_in_time: datetime

    def setUp(self) -> None:
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="strong-test-password",
            full_name="Owner",
        )
        self.cashier = User.objects.create_user(
            email="cashier@example.com",
            password="strong-test-password",
            full_name="Cashier",
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="strong-test-password",
            full_name="Other Employee",
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
            user=self.owner,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.cashier_membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.cashier,
            assigned_branch=self.branch,
            role=MembershipRole.CASHIER,
        )
        self.other_membership = BusinessMembership.objects.create(
            business=self.other_business,
            user=self.other_user,
            assigned_branch=self.other_branch,
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

    def test_cashier_list_shows_only_own_attendance(self) -> None:
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.owner_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.PRESENT,
            check_in_at=self.check_in_time,
        )
        client = Client()
        client.force_login(self.cashier)

        response = client.get(reverse("attendance:attendance-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Main Store")
        self.assertNotContains(response, "Owner")

    def test_cashier_cannot_correct_attendance(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.PRESENT,
            check_in_at=self.check_in_time,
        )
        client = Client()
        client.force_login(self.cashier)

        response = client.get(reverse("attendance:attendance-correct", args=(record.id,)))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Permission denied", status_code=403)

    def test_cashier_can_check_in_and_out_through_post_actions(self) -> None:
        client = Client()
        client.force_login(self.cashier)

        check_in_response = client.post(reverse("attendance:attendance-check-in"))
        check_out_response = client.post(reverse("attendance:attendance-check-out"))

        self.assertRedirects(check_in_response, reverse("attendance:attendance-list"))
        self.assertRedirects(check_out_response, reverse("attendance:attendance-list"))
        record = AttendanceRecord.objects.get(employee=self.cashier_membership)
        self.assertIsNotNone(record.check_in_at)
        self.assertIsNotNone(record.check_out_at)

    def test_cross_business_attendance_detail_is_not_found(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.other_business,
            branch=self.other_branch,
            employee=self.other_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("attendance:attendance-detail", args=(record.id,)))

        self.assertEqual(response.status_code, 404)

    def test_owner_can_create_absent_attendance(self) -> None:
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("attendance:attendance-create"),
            {
                "employee": str(self.cashier_membership.id),
                "branch": str(self.branch.id),
                "work_date": "2026-09-08",
                "status": AttendanceStatus.ABSENT,
                "check_in_at": "",
                "check_out_at": "",
            },
        )

        self.assertRedirects(response, reverse("attendance:attendance-list"))
        record = AttendanceRecord.objects.get(employee=self.cashier_membership)
        self.assertEqual(record.business, self.business)
        self.assertEqual(record.status, AttendanceStatus.ABSENT)

    def test_owner_correction_requires_reason_and_preserves_history(self) -> None:
        record = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        self.client.force_login(self.owner)
        correction_url = reverse("attendance:attendance-correct", args=(record.id,))

        invalid_response = self.client.post(
            correction_url,
            {
                "status": AttendanceStatus.EXCUSED,
                "check_in_at": "",
                "check_out_at": "",
                "reason": "",
            },
        )
        valid_response = self.client.post(
            correction_url,
            {
                "status": AttendanceStatus.EXCUSED,
                "check_in_at": "",
                "check_out_at": "",
                "reason": "Approved absence",
            },
        )

        self.assertEqual(invalid_response.status_code, 200)
        self.assertEqual(AttendanceCorrection.objects.count(), 1)
        self.assertRedirects(
            valid_response,
            reverse("attendance:attendance-detail", args=(record.id,)),
        )
        record.refresh_from_db()
        correction = AttendanceCorrection.objects.get()
        self.assertEqual(record.status, AttendanceStatus.EXCUSED)
        self.assertEqual(correction.previous_status, AttendanceStatus.ABSENT)
        self.assertEqual(correction.replacement_status, AttendanceStatus.EXCUSED)
