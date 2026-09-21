import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from apps.documents.models import (
    CapturedDocument,
    DocumentFile,
    DocumentKind,
    DocumentScanStatus,
    DocumentStatus,
    DocumentTranscription,
    DocumentTranscriptionRevision,
    TranscriptionStatus,
)
from apps.documents.scanning import ScanResult, ScanVerdict
from apps.documents.services import (
    ExpenseTranscriptionInput,
    OpeningStockTranscriptionInput,
    PurchaseTranscriptionInput,
    TranscriptionLineInput,
    cancel_document,
    capture_document,
    confirm_transcription,
    post_confirmed_opening_stock,
    purge_eligible_document_files,
    purge_orphaned_document_objects,
    reconcile_document_storage,
    return_transcription_to_draft,
    save_expense_transcription,
    save_opening_stock_transcription,
    save_purchase_transcription,
    start_replacement_transcription,
    submit_transcription_for_confirmation,
)
from apps.documents.tests.base import DocumentTestMixin
from apps.expenses.models import ExpenseStatus, OperationalPaymentMethod
from apps.expenses.services import (
    edit_operating_expense_draft,
    post_operating_expense,
)
from apps.inventory.models import InventoryMovement, StockOperation
from apps.inventory.services import post_opening_balance
from apps.purchasing.models import PurchaseStatus
from apps.purchasing.services import (
    ReceiptQuantity,
    approve_purchase,
    cancel_purchase,
    receive_purchase,
)


class ErrorScanner:
    def scan(self, source: object) -> ScanResult:
        del source
        return ScanResult(
            verdict=ScanVerdict.ERROR,
            engine="test-error-scanner",
            detail="timeout",
        )


class DocumentServiceTests(DocumentTestMixin):
    def _capture(
        self,
        kind: str = DocumentKind.PURCHASE,
    ) -> CapturedDocument:
        document, duplicate = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=kind,
            title="Supplier paper",
            uploads=[self.png_upload()],
        )
        self.assertFalse(duplicate)
        return document

    def _purchase_transcription(self) -> DocumentTranscription:
        document = self._capture()
        transcription = document.transcriptions.get()
        return save_purchase_transcription(
            actor=self.manager,
            transcription=transcription,
            data=PurchaseTranscriptionInput(
                branch=self.branch,
                supplier=self.supplier,
                purchase_date=timezone.localdate(),
                supplier_reference="SUP-INV-8",
                expected_date=None,
                settlement_terms="Pay after receiving",
                lines=[
                    TranscriptionLineInput(
                        variant=self.variant,
                        quantity=Decimal("2.000"),
                        unit_cost=Decimal("500.000000"),
                    )
                ],
            ),
        )

    def test_upload_validates_signature_scans_and_detects_exact_duplicate(self) -> None:
        document = self._capture()
        source = document.files.get()

        self.assertEqual(document.status, DocumentStatus.AVAILABLE)
        self.assertEqual(source.scan_status, DocumentScanStatus.CLEAN)
        self.assertEqual(source.media_type, "image/png")
        duplicate, was_duplicate = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.PURCHASE,
            title="Same bytes again",
            uploads=[self.png_upload()],
        )

        self.assertTrue(was_duplicate)
        self.assertEqual(duplicate.id, document.id)
        self.assertEqual(DocumentFile.objects.count(), 1)

    def test_upload_rejects_unknown_signature_and_mismatched_extension(self) -> None:
        for upload in (
            SimpleUploadedFile("source.png", b"not-an-image"),
            SimpleUploadedFile("source.pdf", b"\x89PNG\r\n\x1a\ncontent"),
        ):
            with self.subTest(upload=upload.name):
                with self.assertRaises(ValidationError):
                    capture_document(
                        actor=self.owner,
                        branch=self.branch,
                        kind=DocumentKind.PURCHASE,
                        title="Invalid",
                        uploads=[upload],
                    )

    @override_settings(DOCUMENT_MAX_FILE_BYTES=8)
    def test_upload_rejects_file_over_limit(self) -> None:
        with self.assertRaisesMessage(ValidationError, "10 MiB or smaller"):
            self._capture()

    def test_infected_file_is_rejected_and_purged(self) -> None:
        upload = self.png_upload(
            suffix=b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE",
        )
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.EXPENSE,
            title="Unsafe",
            uploads=[upload],
        )
        source = document.files.get()

        self.assertEqual(document.status, DocumentStatus.REJECTED)
        self.assertEqual(source.scan_status, DocumentScanStatus.INFECTED)
        self.assertIsNotNone(source.purged_at)
        self.assertEqual(source.purge_reason, "infected")
        self.assertFalse(source.source.storage.exists(source.source.name))

    @patch("apps.documents.services.configured_scanner", return_value=ErrorScanner())
    def test_scanner_error_fails_closed_in_quarantine(self, scanner: object) -> None:
        del scanner
        document = self._capture()
        source = document.files.get()

        self.assertEqual(document.status, DocumentStatus.QUARANTINED)
        self.assertEqual(source.scan_status, DocumentScanStatus.ERROR)
        self.assertTrue(source.source.storage.exists(source.source.name))

    def test_cashier_and_stock_employee_cannot_capture(self) -> None:
        for actor in (self.cashier, self.stock_employee):
            with self.subTest(role=actor.role):
                with self.assertRaises(PermissionDenied):
                    capture_document(
                        actor=actor,
                        branch=self.branch,
                        kind=DocumentKind.PURCHASE,
                        title="Denied",
                        uploads=[self.png_upload(suffix=actor.role.encode())],
                    )

    def test_purchase_confirmation_is_idempotent_and_creates_plain_draft(self) -> None:
        transcription = self._purchase_transcription()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        confirmation_key = uuid.uuid4()
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=confirmation_key,
        )
        replay = confirm_transcription(
            actor=self.owner,
            transcription=confirmed,
            confirmation_key=confirmation_key,
        )

        self.assertEqual(replay.id, confirmed.id)
        self.assertEqual(confirmed.status, TranscriptionStatus.CONFIRMED)
        self.assertIsNotNone(confirmed.target_purchase)
        purchase = confirmed.target_purchase
        if purchase is None:
            self.fail("Purchase target was not created.")
        self.assertEqual(purchase.status, PurchaseStatus.DRAFT)
        self.assertEqual(purchase.lines.count(), 1)
        self.assertEqual(purchase.receipts.count(), 0)
        self.assertEqual(
            confirmed.lines.get().target_purchase_line_id,
            purchase.lines.get().id,
        )

    def test_purchase_source_traces_through_posted_receipt_and_inventory(self) -> None:
        transcription = self._purchase_transcription()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=uuid.uuid4(),
        )
        purchase = confirmed.target_purchase
        if purchase is None:
            self.fail("Purchase target was not created.")
        approved = approve_purchase(actor=self.owner, purchase=purchase)
        purchase_line = approved.lines.get()
        receipt = receive_purchase(
            actor=self.stock_employee,
            purchase=approved,
            quantities=[
                ReceiptQuantity(
                    purchase_line_id=purchase_line.id,
                    quantity=purchase_line.ordered_quantity,
                )
            ],
            idempotency_key=uuid.uuid4(),
            supplier_document_reference=confirmed.supplier_reference,
        )

        source_line = confirmed.lines.get()
        receipt_line = receipt.lines.get()
        movement = InventoryMovement.objects.get()
        self.assertEqual(source_line.target_purchase_line_id, purchase_line.id)
        self.assertEqual(purchase_line.receipt_lines.get().id, receipt_line.id)
        self.assertEqual(receipt_line.variant_id, source_line.variant_id)
        self.assertEqual(movement.source_id, receipt_line.id)
        self.assertEqual(movement.variant_id, source_line.variant_id)

    def test_manager_cannot_confirm_and_owner_can_return_with_revision(self) -> None:
        transcription = self._purchase_transcription()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        with self.assertRaises(PermissionDenied):
            confirm_transcription(
                actor=self.manager,
                transcription=transcription,
                confirmation_key=uuid.uuid4(),
            )
        returned = return_transcription_to_draft(
            actor=self.owner,
            transcription=transcription,
            reason="Recheck the unit cost.",
        )
        self.assertEqual(returned.status, TranscriptionStatus.DRAFT)
        revision = returned.revisions.last()
        if revision is None:
            self.fail("Expected return revision.")
        self.assertEqual(revision.reason, "Recheck the unit cost.")

    def test_expense_confirmation_locks_draft_and_payment_evidence(self) -> None:
        document = self._capture(DocumentKind.EXPENSE)
        transcription = save_expense_transcription(
            actor=self.manager,
            transcription=document.transcriptions.get(),
            data=ExpenseTranscriptionInput(
                branch=self.branch,
                category=self.category,
                document_date=timezone.localdate(),
                payee="City taxi cooperative",
                description="Transporting new clothing stock",
                amount=Decimal("250.00"),
                payment_method=OperationalPaymentMethod.TELEBIRR,
                telebirr_reference="TX-STAGE-8",
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
        if expense is None:
            self.fail("Expense target was not created.")

        with self.assertRaisesMessage(ValidationError, "owner-confirmed"):
            edit_operating_expense_draft(
                actor=self.owner,
                expense=expense,
                branch=self.branch,
                category=self.category,
                payee="Changed",
                description="Changed",
                amount=Decimal("1.00"),
            )
        with self.assertRaisesMessage(ValidationError, "must match"):
            post_operating_expense(
                actor=self.owner,
                expense=expense,
                method=OperationalPaymentMethod.TELEBIRR,
                telebirr_reference="DIFFERENT",
                idempotency_key=uuid.uuid4(),
            )
        posted = post_operating_expense(
            actor=self.owner,
            expense=expense,
            method=OperationalPaymentMethod.TELEBIRR,
            telebirr_reference="TX-STAGE-8",
            idempotency_key=uuid.uuid4(),
        )
        self.assertEqual(posted.status, ExpenseStatus.POSTED)

    def test_opening_stock_posts_all_lines_atomically_and_replays(self) -> None:
        document = self._capture(DocumentKind.OPENING_STOCK)
        transcription = save_opening_stock_transcription(
            actor=self.manager,
            transcription=document.transcriptions.get(),
            data=OpeningStockTranscriptionInput(
                branch=self.branch,
                lines=[
                    TranscriptionLineInput(
                        variant=self.variant,
                        quantity=Decimal("4.000"),
                        unit_cost=Decimal("400.000000"),
                    ),
                    TranscriptionLineInput(
                        variant=self.second_variant,
                        quantity=Decimal("3.000"),
                        unit_cost=Decimal("450.000000"),
                    ),
                ],
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
        post_confirmed_opening_stock(actor=self.manager, transcription=confirmed)
        post_confirmed_opening_stock(actor=self.manager, transcription=confirmed)

        self.assertEqual(StockOperation.objects.count(), 2)
        self.assertEqual(InventoryMovement.objects.count(), 2)
        self.assertFalse(confirmed.lines.filter(target_stock_operation__isnull=True).exists())

    def test_opening_stock_rolls_back_every_line_when_one_has_prior_movement(self) -> None:
        post_opening_balance(
            actor=self.owner,
            branch=self.branch,
            variant=self.second_variant,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("300.000000"),
            idempotency_key=uuid.uuid4(),
        )
        document = self._capture(DocumentKind.OPENING_STOCK)
        transcription = save_opening_stock_transcription(
            actor=self.manager,
            transcription=document.transcriptions.get(),
            data=OpeningStockTranscriptionInput(
                branch=self.branch,
                lines=[
                    TranscriptionLineInput(
                        variant=self.variant,
                        quantity=Decimal("4.000"),
                        unit_cost=Decimal("400.000000"),
                    ),
                    TranscriptionLineInput(
                        variant=self.second_variant,
                        quantity=Decimal("3.000"),
                        unit_cost=Decimal("450.000000"),
                    ),
                ],
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

        with self.assertRaisesMessage(ValidationError, "before the first movement"):
            post_confirmed_opening_stock(actor=self.owner, transcription=confirmed)

        self.assertEqual(StockOperation.objects.count(), 1)
        self.assertFalse(InventoryMovement.objects.filter(variant=self.variant).exists())
        self.assertFalse(confirmed.lines.filter(target_stock_operation__isnull=False).exists())

    def test_confirmed_revision_is_immutable_and_replacement_preserves_history(self) -> None:
        transcription = self._purchase_transcription()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=uuid.uuid4(),
        )
        revision = confirmed.revisions.last()
        if revision is None:
            self.fail("Expected confirmation revision.")
        revision.reason = "tampered"
        with self.assertRaisesMessage(ValidationError, "immutable"):
            revision.save()
        with self.assertRaisesMessage(ValidationError, "Cancel the source-derived purchase"):
            start_replacement_transcription(
                actor=self.manager,
                transcription=confirmed,
            )
        purchase = confirmed.target_purchase
        if purchase is None:
            self.fail("Expected target purchase.")
        cancel_purchase(actor=self.owner, purchase=purchase)
        replacement = start_replacement_transcription(
            actor=self.manager,
            transcription=confirmed,
        )
        self.assertEqual(replacement.replacement_of_id, confirmed.id)
        self.assertEqual(replacement.lines.count(), confirmed.lines.count())
        self.assertEqual(confirmed.status, TranscriptionStatus.CONFIRMED)

    def test_cross_tenant_master_data_is_rejected(self) -> None:
        other_business = self.business.__class__.objects.create(
            name="Other Shop",
            slug="other-shop-stage-8",
        )
        other_branch = self.branch.__class__.objects.create(
            business=other_business,
            name="Other",
            code="other",
        )
        with self.assertRaises(PermissionDenied):
            capture_document(
                actor=self.owner,
                branch=other_branch,
                kind=DocumentKind.EXPENSE,
                title="Cross tenant",
                uploads=[self.png_upload(suffix=b"other")],
            )

    @override_settings(DOCUMENT_CANCELLED_RETENTION_DAYS=30)
    def test_cancelled_bytes_purge_but_metadata_and_hash_remain(self) -> None:
        document = self._capture()
        source = document.files.get()
        sha256 = source.sha256
        cancel_document(
            actor=self.owner,
            document=document,
            reason="Duplicate source captured during intake.",
        )
        document.refresh_from_db()
        document.cancelled_at = timezone.now() - timedelta(days=31)
        document.save(update_fields=("cancelled_at", "updated_at"))

        self.assertEqual(purge_eligible_document_files(), 1)
        source.refresh_from_db()
        self.assertIsNotNone(source.purged_at)
        self.assertEqual(source.purge_reason, "cancelled_retention_expired")
        self.assertEqual(source.sha256, sha256)
        self.assertFalse(source.source.storage.exists(source.source.name))
        self.assertEqual(DocumentTranscriptionRevision.objects.count(), 2)

    def test_storage_reconciliation_reports_missing_object(self) -> None:
        document = self._capture()
        source = document.files.get()
        self.assertEqual(reconcile_document_storage().missing, ())
        source.source.storage.delete(source.source.name)
        self.assertEqual(
            reconcile_document_storage().missing,
            (source.source.name,),
        )

    def test_storage_reconciliation_detects_and_can_remove_orphan(self) -> None:
        document = self._capture()
        storage = document.files.get().source.storage
        orphan_name = storage.save(
            "documents/orphan.bin",
            ContentFile(b"orphan"),
        )

        result = reconcile_document_storage()
        self.assertEqual(result.orphaned, (orphan_name,))
        with patch.object(
            storage,
            "get_modified_time",
            return_value=timezone.now() - timedelta(hours=25),
        ):
            self.assertEqual(purge_orphaned_document_objects(result), 1)
        self.assertFalse(storage.exists(orphan_name))

    def test_orphan_cleanup_retains_recent_uploads(self) -> None:
        document = self._capture()
        storage = document.files.get().source.storage
        orphan_name = storage.save(
            "documents/recent-orphan.bin",
            ContentFile(b"recent orphan"),
        )
        self.addCleanup(storage.delete, orphan_name)
        result = reconcile_document_storage()

        self.assertEqual(result.orphaned, (orphan_name,))
        self.assertEqual(purge_orphaned_document_objects(result), 0)
        self.assertTrue(storage.exists(orphan_name))

    def test_storage_reconciliation_detects_hash_mismatch(self) -> None:
        document = self._capture()
        source = document.files.get()
        DocumentFile.objects.filter(pk=source.pk).update(sha256="0" * 64)

        self.assertEqual(
            reconcile_document_storage().hash_mismatches,
            (source.source.name,),
        )

    @override_settings(DOCUMENT_QUARANTINE_STALE_HOURS=24)
    @patch("apps.documents.services.configured_scanner", return_value=ErrorScanner())
    def test_storage_reconciliation_detects_stale_quarantine(self, scanner: object) -> None:
        del scanner
        document = self._capture()
        CapturedDocument.objects.filter(pk=document.pk).update(
            updated_at=timezone.now() - timedelta(hours=25)
        )

        self.assertEqual(
            reconcile_document_storage().stale_quarantine,
            (document.id,),
        )

    def test_storage_reconciliation_detects_incomplete_confirmed_target(self) -> None:
        transcription = self._purchase_transcription()
        submit_transcription_for_confirmation(
            actor=self.manager,
            transcription=transcription,
        )
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=uuid.uuid4(),
        )
        confirmed.lines.update(target_purchase_line=None)

        self.assertEqual(
            reconcile_document_storage().incomplete_targets,
            (confirmed.id,),
        )
