from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.documents.services import (
    purge_orphaned_document_objects,
    reconcile_document_storage,
)


class Command(BaseCommand):
    help = "Reconcile private-document database records, hashes, targets, and stored objects."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--delete-orphans",
            action="store_true",
            help=(
                "Delete orphan objects older than the configured safety window; "
                "recent objects are retained."
            ),
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        result = reconcile_document_storage()
        deleted = 0
        if options["delete_orphans"]:
            deleted = purge_orphaned_document_objects(result)
            result = reconcile_document_storage()
        summary = (
            f"Checked {result.checked} file(s); missing {len(result.missing)}, "
            f"orphaned {len(result.orphaned)}, hash mismatch "
            f"{len(result.hash_mismatches)}, stale quarantine "
            f"{len(result.stale_quarantine)}, incomplete targets "
            f"{len(result.incomplete_targets)}, deleted orphans {deleted}."
        )
        if result.issue_count:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))
