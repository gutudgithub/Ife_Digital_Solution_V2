import json
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone
from django.utils.dateparse import parse_datetime


class Command(BaseCommand):
    help = "Record human-approved PostgreSQL and object-storage restore evidence."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--approved-by", required=True)
        parser.add_argument("--evidence-url", required=True)
        parser.add_argument("--database-restore-tested-at", required=True)
        parser.add_argument("--object-restore-tested-at", required=True)
        parser.add_argument("--measured-rpo-hours", required=True, type=float)
        parser.add_argument("--measured-rto-hours", required=True, type=float)
        parser.add_argument("--output", required=True)

    def handle(self, *args: object, **options: object) -> None:
        del args
        approved_by = str(options["approved_by"]).strip()
        evidence_url = str(options["evidence_url"]).strip()
        database_restore = str(options["database_restore_tested_at"]).strip()
        object_restore = str(options["object_restore_tested_at"]).strip()
        if not approved_by:
            raise CommandError("approved-by is required.")
        evidence_parts = urlsplit(evidence_url)
        if (
            evidence_parts.scheme != "https"
            or not evidence_parts.netloc
            or evidence_parts.username is not None
            or evidence_parts.password is not None
        ):
            raise CommandError("evidence-url must be HTTPS.")
        database_restore_value = parse_datetime(database_restore)
        object_restore_value = parse_datetime(object_restore)
        if (
            database_restore_value is None
            or object_restore_value is None
            or timezone.is_naive(database_restore_value)
            or timezone.is_naive(object_restore_value)
        ):
            raise CommandError("Restore timestamps must be timezone-aware ISO-8601 datetimes.")
        rpo = cast(float, options["measured_rpo_hours"])
        rto = cast(float, options["measured_rto_hours"])
        if rpo <= 0 or rto <= 0:
            raise CommandError("Recovery objective measurements must be positive.")
        output = Path(str(options["output"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "approved_by": approved_by,
                    "evidence_url": evidence_url,
                    "database_restore_tested_at": database_restore,
                    "object_restore_tested_at": object_restore,
                    "measured_rpo_hours": rpo,
                    "measured_rto_hours": rto,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"Recovery attestation written to {output}."))
