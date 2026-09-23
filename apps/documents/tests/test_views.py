import uuid
from decimal import Decimal

from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.documents.models import CapturedDocument, DocumentKind, DocumentTranscription
from apps.documents.services import (
    ExpenseTranscriptionInput,
    PurchaseTranscriptionInput,
    TranscriptionLineInput,
    capture_document,
    confirm_transcription,
    save_expense_transcription,
    save_purchase_transcription,
    submit_transcription_for_confirmation,
)
from apps.documents.tests.base import DocumentTestMixin
from apps.expenses.models import OperationalPaymentMethod
from apps.purchasing.services import approve_purchase


class DocumentViewTests(DocumentTestMixin):
    def _captured_purchase(
        self,
    ) -> tuple[CapturedDocument, DocumentTranscription]:
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.PURCHASE,
            title="Private supplier invoice",
            uploads=[self.png_upload()],
        )
        transcription = save_purchase_transcription(
            actor=self.manager,
            transcription=document.transcriptions.get(),
            data=PurchaseTranscriptionInput(
                branch=self.branch,
                supplier=self.supplier,
                purchase_date=timezone.localdate(),
                supplier_reference="PRIVATE-REF-8",
                expected_date=None,
                settlement_terms="",
                lines=[
                    TranscriptionLineInput(
                        variant=self.variant,
                        quantity=Decimal("2.000"),
                        unit_cost=Decimal("500.000000"),
                    )
                ],
            ),
        )
        return document, transcription

    def test_owner_and_manager_have_access_but_cashier_and_stock_do_not(self) -> None:
        for membership in (self.owner, self.manager):
            with self.subTest(role=membership.role):
                client = Client()
                client.force_login(membership.user)
                self.assertEqual(client.get(reverse("documents:list")).status_code, 200)
        for membership in (self.cashier, self.stock_employee):
            with self.subTest(role=membership.role):
                client = Client()
                client.force_login(membership.user)
                self.assertEqual(client.get(reverse("documents:list")).status_code, 403)

    def test_platform_staff_has_no_document_access_without_membership(self) -> None:
        staff_user = self.owner.user.__class__.objects.create_user(
            email="platform-documents@example.com",
            password="strong-test-password",
            is_staff=True,
        )
        self.client.force_login(staff_user)

        self.assertEqual(self.client.get(reverse("documents:list")).status_code, 403)

    def test_transcription_workspace_supports_incremental_line_entry(self) -> None:
        _document, transcription = self._captured_purchase()
        self.client.force_login(self.manager.user)

        response = self.client.get(
            reverse("documents:transcription-edit", args=(transcription.id,))
        )

        self.assertContains(response, "Add another line")
        self.assertContains(response, "js/document-transcription-lines.js")
        self.assertContains(response, "data-document-empty-line")

    def test_safe_routes_reject_post_and_direct_mutations_reject_get(self) -> None:
        document, transcription = self._captured_purchase()
        self.client.force_login(self.owner.user)
        source = document.files.get()
        safe_urls = (
            reverse("documents:list"),
            reverse("documents:detail", args=(document.id,)),
            reverse("documents:file-preview", args=(document.id, source.id)),
            reverse("documents:file-download", args=(document.id, source.id)),
        )
        mutation_urls = (
            reverse("documents:retry-scan", args=(document.id,)),
            reverse("documents:transcription-submit", args=(transcription.id,)),
            reverse("documents:transcription-replace", args=(transcription.id,)),
            reverse(
                "documents:transcription-post-opening-stock",
                args=(transcription.id,),
            ),
        )
        for url in safe_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)
        for url in mutation_urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_cancellation_requires_reason_and_records_it(self) -> None:
        document, transcription = self._captured_purchase()
        self.client.force_login(self.owner.user)
        transcription_url = reverse(
            "documents:transcription-cancel",
            args=(transcription.id,),
        )

        self.assertEqual(self.client.get(transcription_url).status_code, 200)
        response = self.client.post(transcription_url, {"reason": ""})
        self.assertEqual(response.status_code, 200)
        transcription.refresh_from_db()
        self.assertEqual(transcription.status, "draft")

        response = self.client.post(
            reverse("documents:cancel", args=(document.id,)),
            {"reason": "Duplicate source captured during intake."},
        )
        self.assertRedirects(
            response,
            reverse("documents:detail", args=(document.id,)),
        )
        document.refresh_from_db()
        transcription.refresh_from_db()
        self.assertEqual(document.cancellation_reason, "Duplicate source captured during intake.")
        self.assertEqual(
            transcription.cancellation_reason,
            "Duplicate source captured during intake.",
        )

    def test_download_is_private_no_store_and_records_access(self) -> None:
        document, _ = self._captured_purchase()
        source = document.files.get()
        self.client.force_login(self.owner.user)
        response = self.client.get(
            reverse("documents:file-download", args=(document.id, source.id))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(document.access_events.count(), 1)

    def test_other_tenant_cannot_discover_document(self) -> None:
        document, _ = self._captured_purchase()
        other_business = self.business.__class__.objects.create(
            name="Other",
            slug="other-view-shop",
        )
        other_branch = self.branch.__class__.objects.create(
            business=other_business,
            name="Other",
            code="other",
        )
        other_user = self.owner.user.__class__.objects.create_user(
            email="other-owner@example.com",
            password="strong-test-password",
        )
        self.owner.__class__.objects.create(
            business=other_business,
            user=other_user,
            assigned_branch=other_branch,
            role=self.owner.role,
        )
        self.client.force_login(other_user)
        response = self.client.get(reverse("documents:detail", args=(document.id,)))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Private supplier invoice", status_code=404)

    def test_capture_form_ignores_browser_mime_and_uses_signature(self) -> None:
        self.client.force_login(self.owner.user)
        response = self.client.post(
            reverse("documents:capture"),
            {
                "branch": self.branch.id,
                "kind": DocumentKind.EXPENSE,
                "title": "Taxi receipt",
                "source_files": self.png_upload(),
            },
        )

        self.assertEqual(response.status_code, 302)
        document = self.business.captured_documents.get()
        self.assertEqual(document.files.get().media_type, "image/png")

    def test_owner_confirmation_page_and_manager_denial(self) -> None:
        document, transcription = self._captured_purchase()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        url = reverse("documents:transcription-confirm", args=(transcription.id,))
        manager_client = Client()
        manager_client.force_login(self.manager.user)
        self.assertEqual(manager_client.get(url).status_code, 403)

        self.client.force_login(self.owner.user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm immutable snapshot")
        self.assertContains(response, "PRIVATE-REF-8")
        self.assertContains(
            response,
            reverse(
                "documents:file-preview",
                args=(document.id, document.files.get().id),
            ),
        )
        post_response = self.client.post(
            url,
            {"confirmation_key": uuid.uuid4(), "confirmed": True},
        )
        self.assertRedirects(
            post_response,
            reverse("documents:detail", args=(document.id,)),
        )
        transcription.refresh_from_db()
        self.assertIsNotNone(transcription.target_purchase_id)

        purchase_url = reverse(
            "purchasing:purchase-detail",
            args=(transcription.target_purchase_id,),
        )
        purchase_response = self.client.get(purchase_url)
        self.assertContains(
            purchase_response,
            reverse("documents:detail", args=(document.id,)),
        )

        purchase = transcription.target_purchase
        assert purchase is not None
        approve_purchase(actor=self.owner, purchase=purchase)
        stock_client = Client()
        stock_client.force_login(self.stock_employee.user)
        stock_response = stock_client.get(purchase_url)
        self.assertEqual(stock_response.status_code, 200)
        self.assertNotContains(
            stock_response,
            reverse("documents:detail", args=(document.id,)),
        )

    def test_confirmed_source_details_are_not_exposed_outside_authorized_views(self) -> None:
        document, _ = self._captured_purchase()
        self.client.force_login(self.cashier.user)
        response = self.client.get(reverse("documents:detail", args=(document.id,)))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "PRIVATE-REF-8", status_code=403)

    def test_confirmed_expense_links_back_to_authorized_source_view(self) -> None:
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.EXPENSE,
            title="Private transport receipt",
            uploads=[self.png_upload(name="transport.png")],
        )
        transcription = save_expense_transcription(
            actor=self.manager,
            transcription=document.transcriptions.get(),
            data=ExpenseTranscriptionInput(
                branch=self.branch,
                category=self.category,
                document_date=timezone.localdate(),
                payee="Transport provider",
                description="Shop supplies transport",
                amount=Decimal("150.00"),
                payment_method=OperationalPaymentMethod.TELEBIRR,
                telebirr_reference="TX-STAGE8-EXPENSE",
            ),
        )
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=uuid.uuid4(),
        )
        expense = confirmed.target_expense
        assert expense is not None

        self.client.force_login(self.owner.user)
        response = self.client.get(reverse("expenses:expense-detail", args=(expense.id,)))

        self.assertContains(
            response,
            reverse("documents:detail", args=(document.id,)),
        )
