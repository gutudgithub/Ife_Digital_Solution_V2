from django.core.management.base import BaseCommand, CommandParser

from apps.documents.services import purge_eligible_document_files


class Command(BaseCommand):
    help = "Purge eligible cancelled private-document bytes while retaining metadata."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List eligible business/document IDs without deleting bytes.",
        )

    def handle(self, *args: object, **options: object) -> None:
        del args
        dry_run = bool(options["dry_run"])
        result = purge_eligible_document_files(dry_run=dry_run)
        action = "would_purge" if dry_run else "purged"
        for summary in result.summaries:
            self.stdout.write(
                f"{action} business={summary.business_id} "
                f"document={summary.document_id} files={summary.file_count}"
            )
        verb = "Would purge" if dry_run else "Purged"
        self.stdout.write(self.style.SUCCESS(f"{verb} {result.file_count} document file(s)."))
