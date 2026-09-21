from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.utils import timezone

from apps.documents.models import DocumentKind, DocumentStatus
from apps.documents.services import cancel_document, capture_document
from apps.documents.tests.base import DocumentTestMixin


class DocumentCommandTests(DocumentTestMixin):
    def test_purge_command_previews_and_reports_document_scope(self) -> None:
        document, _ = capture_document(
            actor=self.owner,
            branch=self.branch,
            kind=DocumentKind.PURCHASE,
            title="Cancelled source",
            uploads=[self.png_upload()],
        )
        cancel_document(
            actor=self.owner,
            document=document,
            reason="Duplicate intake.",
        )
        document.refresh_from_db()
        document.cancelled_at = timezone.now() - timedelta(days=31)
        document.save(update_fields=("cancelled_at", "updated_at"))
        source = document.files.get()

        preview = StringIO()
        call_command("purge_document_files", "--dry-run", stdout=preview)

        self.assertTrue(source.source.storage.exists(source.source.name))
        self.assertIn(f"business={self.business.id}", preview.getvalue())
        self.assertIn(f"document={document.id}", preview.getvalue())
        self.assertIn("Would purge 1 document file(s).", preview.getvalue())

        output = StringIO()
        call_command("purge_document_files", stdout=output)
        source.refresh_from_db()

        self.assertEqual(document.status, DocumentStatus.CANCELLED)
        self.assertFalse(source.source.storage.exists(source.source.name))
        self.assertIn(f"business={self.business.id}", output.getvalue())
        self.assertIn(f"document={document.id}", output.getvalue())
        self.assertIn("Purged 1 document file(s).", output.getvalue())
