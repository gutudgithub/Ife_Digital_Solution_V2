from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse
from django.utils.formats import date_format

from apps.accounts.models import User
from apps.attendance.forms import AttendanceCorrectionForm, add_accessible_error_attributes
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

    def test_attendance_messages_do_not_nest_live_regions(self) -> None:
        self.client.force_login(self.cashier)

        success_response = self.client.post(
            reverse("attendance:attendance-check-in"),
            follow=True,
        )
        error_response = self.client.post(
            reverse("attendance:attendance-check-in"),
            follow=True,
        )

        self.assertContains(success_response, 'role="status" aria-live="polite"')
        self.assertContains(error_response, 'role="alert"')
        self.assertNotContains(error_response, 'class="messages" role=')

    def test_overnight_attendance_section_discloses_the_open_work_date(self) -> None:
        work_date = date(2026, 9, 8)
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=work_date,
            status=AttendanceStatus.PRESENT,
            check_in_at=datetime(
                2026,
                9,
                8,
                20,
                tzinfo=ZoneInfo("Africa/Addis_Ababa"),
            ),
        )
        self.client.force_login(self.cashier)
        viewed_at = datetime(
            2026,
            9,
            9,
            9,
            tzinfo=ZoneInfo("Africa/Addis_Ababa"),
        )

        with patch("apps.attendance.views.timezone.now", return_value=viewed_at):
            response = self.client.get(reverse("attendance:attendance-list"))

        self.assertContains(
            response,
            f"Shift from {date_format(work_date)} is still open",
        )
        self.assertContains(
            response,
            "Checking out will close this earlier attendance record.",
        )

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

    def test_owner_can_filter_attendance_by_work_date(self) -> None:
        first = AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.cashier_membership,
            work_date=date(2026, 9, 8),
            status=AttendanceStatus.ABSENT,
        )
        AttendanceRecord.objects.create(
            business=self.business,
            branch=self.branch,
            employee=self.owner_membership,
            work_date=date(2026, 9, 9),
            status=AttendanceStatus.EXCUSED,
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("attendance:attendance-list"),
            {"work_date": "2026-09-08"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["attendance_records"]), [first])

    def test_attendance_list_is_paginated_without_dropping_filter(self) -> None:
        work_date = date(2026, 1, 1)
        users = User.objects.bulk_create(
            [
                User(
                    email=f"cashier-{offset}@example.com",
                    full_name=f"Cashier {offset}",
                )
                for offset in range(51)
            ]
        )
        memberships = BusinessMembership.objects.bulk_create(
            [
                BusinessMembership(
                    business=self.business,
                    user=user,
                    assigned_branch=self.branch,
                    role=MembershipRole.CASHIER,
                )
                for user in users
            ]
        )
        AttendanceRecord.objects.bulk_create(
            [
                AttendanceRecord(
                    business=self.business,
                    branch=self.branch,
                    employee=membership,
                    work_date=work_date,
                    status=AttendanceStatus.ABSENT,
                )
                for membership in memberships
            ]
        )
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("attendance:attendance-list"),
            {"work_date": work_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["attendance_records"]), 50)
        self.assertContains(response, "work_date=2026-01-01&amp;page=2")

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
        self.assertContains(invalid_response, 'aria-invalid="true"')
        self.assertContains(invalid_response, 'aria-describedby="id_reason_errors"')
        self.assertContains(invalid_response, 'id="id_reason_errors"')
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

    def test_accessible_error_id_uses_the_form_auto_id(self) -> None:
        form = AttendanceCorrectionForm(
            data={
                "status": AttendanceStatus.EXCUSED,
                "check_in_at": "",
                "check_out_at": "",
                "reason": "",
            },
            auto_id="field_%s",
        )
        self.assertFalse(form.is_valid())

        add_accessible_error_attributes(form)

        self.assertEqual(
            form.fields["reason"].widget.attrs["aria-describedby"],
            "field_reason_errors",
        )
