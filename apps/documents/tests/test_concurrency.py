import uuid
from datetime import date
from decimal import Decimal
from queue import Queue
from threading import Barrier, Thread

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import User
from apps.businesses.models import Branch, Business, BusinessMembership, MembershipRole
from apps.catalog.models import Product, ProductVariant, StockUnit
from apps.documents.models import (
    CapturedDocument,
    DocumentFile,
    DocumentKind,
    DocumentStatus,
    DocumentTranscription,
    TranscriptionStatus,
)
from apps.documents.services import (
    OpeningStockTranscriptionInput,
    PurchaseTranscriptionInput,
    TranscriptionLineInput,
    cancel_document,
    capture_document,
    confirm_transcription,
    post_confirmed_opening_stock,
    save_opening_stock_transcription,
    save_purchase_transcription,
    submit_transcription_for_confirmation,
)
from apps.inventory.models import StockOperation
from apps.purchasing.models import Supplier


@skipUnlessDBFeature("has_select_for_update")
class DocumentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.business = Business.objects.create(
            name="Concurrent Documents Shop",
            slug="concurrent-documents-shop",
        )
        self.branch = Branch.objects.create(
            business=self.business,
            name="Main",
            code="main",
        )
        user = User.objects.create_user(
            email="concurrent-documents-owner@example.com",
            password="strong-test-password",
        )
        self.owner = BusinessMembership.objects.create(
            business=self.business,
            user=user,
            assigned_branch=self.branch,
            role=MembershipRole.OWNER,
        )
        self.supplier = Supplier.objects.create(
            business=self.business,
            name="Concurrent Supplier",
        )
        product = Product.objects.create(
            business=self.business,
            name="Concurrent Shirt",
        )
        self.variants = [
            ProductVariant.objects.create(
                business=self.business,
                product=product,
                sku=f"CONCURRENT-SHIRT-{index}",
                selling_price=Decimal("900.00"),
                stock_unit=StockUnit.PIECE,
            )
            for index in range(2)
        ]

    def tearDown(self) -> None:
        for document_file in DocumentFile.objects.filter(purged_at__isnull=True):
            document_file.source.storage.delete(document_file.source.name)

    def _upload(self, content: bytes) -> SimpleUploadedFile:
        return SimpleUploadedFile(
            "source.png",
            b"\x89PNG\r\n\x1a\n" + content,
        )

    def _purchase_transcription(self) -> DocumentTranscription:
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.PURCHASE,
            title="Concurrent source",
            uploads=[self._upload(b"concurrent-confirmation")],
        )
        transcription = save_purchase_transcription(
            actor=self.owner,
            transcription=document.transcriptions.get(),
            data=PurchaseTranscriptionInput(
                branch=self.branch,
                supplier=self.supplier,
                purchase_date=date.today(),
                supplier_reference="CONCURRENT-REF",
                expected_date=None,
                settlement_terms="",
                lines=[
                    TranscriptionLineInput(
                        variant=self.variants[0],
                        quantity=Decimal("2.000"),
                        unit_cost=Decimal("500.000000"),
                    )
                ],
            ),
        )
        return submit_transcription_for_confirmation(
            actor=self.owner,
            transcription=transcription,
        )

    def test_concurrent_exact_upload_creates_one_document(self) -> None:
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID] = Queue()
        errors: Queue[BaseException] = Queue()

        def capture() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                barrier.wait()
                document, _ = capture_document(
                    actor=actor,
                    branch=branch,
                    kind=DocumentKind.PURCHASE,
                    title="Concurrent source",
                    uploads=[self._upload(b"concurrent-upload")],
                )
                outcomes.put(document.id)
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        threads = [Thread(target=capture), Thread(target=capture)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(list(errors.queue), [])
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(CapturedDocument.objects.count(), 1)
        self.assertEqual(DocumentFile.objects.count(), 1)

    def test_concurrent_confirmation_with_same_key_creates_one_target(self) -> None:
        transcription = self._purchase_transcription()
        key = uuid.uuid4()
        barrier = Barrier(2)
        outcomes: Queue[uuid.UUID | None] = Queue()
        errors: Queue[BaseException] = Queue()

        def confirm() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current = DocumentTranscription.objects.get(pk=transcription.pk)
                barrier.wait()
                result = confirm_transcription(
                    actor=actor,
                    transcription=current,
                    confirmation_key=key,
                )
                outcomes.put(result.target_purchase_id)
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        threads = [Thread(target=confirm), Thread(target=confirm)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(list(errors.queue), [])
        self.assertEqual(outcomes.get_nowait(), outcomes.get_nowait())
        self.assertEqual(self.business.purchases.count(), 1)

    def test_document_cancellation_cannot_race_confirmed_evidence(self) -> None:
        transcription = self._purchase_transcription()
        barrier = Barrier(2)
        errors: Queue[BaseException] = Queue()

        def confirm() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current = DocumentTranscription.objects.get(pk=transcription.pk)
                barrier.wait()
                confirm_transcription(
                    actor=actor,
                    transcription=current,
                    confirmation_key=uuid.uuid4(),
                )
            except ValidationError:
                pass
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        def cancel() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                document = CapturedDocument.objects.get(pk=transcription.document_id)
                barrier.wait()
                cancel_document(
                    actor=actor,
                    document=document,
                    reason="Concurrent cancellation test",
                )
            except ValidationError:
                pass
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        threads = [Thread(target=confirm), Thread(target=cancel)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(list(errors.queue), [])
        transcription.refresh_from_db()
        transcription.document.refresh_from_db()
        self.assertIn(
            (transcription.status, transcription.document.status),
            {
                (TranscriptionStatus.CONFIRMED, DocumentStatus.AVAILABLE),
                (TranscriptionStatus.CANCELLED, DocumentStatus.CANCELLED),
            },
        )

    def test_concurrent_opening_stock_posting_creates_each_movement_once(self) -> None:
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.OPENING_STOCK,
            title="Concurrent opening stock",
            uploads=[self._upload(b"concurrent-opening")],
        )
        transcription = save_opening_stock_transcription(
            actor=self.owner,
            transcription=document.transcriptions.get(),
            data=OpeningStockTranscriptionInput(
                branch=self.branch,
                lines=[
                    TranscriptionLineInput(
                        variant=variant,
                        quantity=Decimal("2.000"),
                        unit_cost=Decimal("500.000000"),
                    )
                    for variant in self.variants
                ],
            ),
        )
        submit_transcription_for_confirmation(
            actor=self.owner,
            transcription=transcription,
        )
        confirmed = confirm_transcription(
            actor=self.owner,
            transcription=transcription,
            confirmation_key=uuid.uuid4(),
        )
        barrier = Barrier(2)
        errors: Queue[BaseException] = Queue()

        def post() -> None:
            connections.close_all()
            try:
                actor = BusinessMembership.objects.get(pk=self.owner.pk)
                current = DocumentTranscription.objects.get(pk=confirmed.pk)
                barrier.wait()
                post_confirmed_opening_stock(actor=actor, transcription=current)
            except BaseException as error:
                errors.put(error)
            finally:
                connections.close_all()

        threads = [Thread(target=post), Thread(target=post)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(list(errors.queue), [])
        self.assertEqual(StockOperation.objects.count(), 2)
