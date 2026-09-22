import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext as _


@dataclass(frozen=True)
class RecoveryAttestation:
    approved_by: str
    evidence_url: str
    database_restore_tested_at: datetime
    object_restore_tested_at: datetime
    measured_rpo_hours: Decimal
    measured_rto_hours: Decimal


def load_recovery_attestation() -> RecoveryAttestation:
    path_value = str(settings.STAGE12_RECOVERY_ATTESTATION_PATH).strip()
    if not path_value:
        raise ValidationError(_("Recovery attestation path is not configured."))
    path = Path(path_value)
    try:
        payload = cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
        raise ValidationError(_("Recovery attestation is unavailable or invalid.")) from error

    approved_by = str(payload.get("approved_by", "")).strip()
    evidence_url = str(payload.get("evidence_url", "")).strip()
    evidence_parts = urlsplit(evidence_url)
    database_restore = parse_datetime(str(payload.get("database_restore_tested_at", "")))
    object_restore = parse_datetime(str(payload.get("object_restore_tested_at", "")))
    try:
        rpo = Decimal(str(payload["measured_rpo_hours"]))
        rto = Decimal(str(payload["measured_rto_hours"]))
    except (InvalidOperation, KeyError) as error:
        raise ValidationError(_("Recovery objectives must be valid hour values.")) from error
    if (
        not approved_by
        or evidence_parts.scheme != "https"
        or not evidence_parts.netloc
        or evidence_parts.username is not None
        or evidence_parts.password is not None
        or database_restore is None
        or object_restore is None
        or timezone.is_naive(database_restore)
        or timezone.is_naive(object_restore)
        or rpo <= 0
        or rto <= 0
    ):
        raise ValidationError(_("Recovery attestation fields are incomplete or unsafe."))
    return RecoveryAttestation(
        approved_by=approved_by,
        evidence_url=evidence_url,
        database_restore_tested_at=database_restore,
        object_restore_tested_at=object_restore,
        measured_rpo_hours=rpo,
        measured_rto_hours=rto,
    )
