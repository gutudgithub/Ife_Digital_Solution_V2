from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.catalog.services import (
    purge_orphaned_catalog_media,
    reconcile_catalog_media,
)


class Command(BaseCommand):
    help = "Check product and Telebirr media objects against database evidence."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--purge-orphans",
            action="store_true",
            help="Delete objects that are not referenced by a current database record.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply requested deletions; without this flag the command is a dry run.",
        )

    def handle(self, *args: object, **options: object) -> None:
        report = reconcile_catalog_media()
        self.stdout.write(f"Checked referenced objects: {report.checked}")
        for label, names in (
            ("missing", report.missing),
            ("hash-mismatch", report.hash_mismatches),
            ("orphan", report.orphaned),
        ):
            for name in names:
                self.stdout.write(f"{label}: {name}")
        if report.listing_failed:
            raise CommandError("Storage listing is unavailable; orphan detection did not run.")
        if options["purge_orphans"]:
            removed = purge_orphaned_catalog_media(
                names=report.orphaned,
                dry_run=not bool(options["apply"]),
            )
            mode = "deleted" if options["apply"] else "would-delete"
            for name in removed:
                self.stdout.write(f"{mode}: {name}")
        if report.missing or report.hash_mismatches:
            raise CommandError("Catalog media reconciliation found referenced-object failures.")
