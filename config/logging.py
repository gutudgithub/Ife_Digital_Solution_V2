import json
import logging
from datetime import UTC, datetime


class SecurityJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.__dict__.get("security_event", "application_security"),
            "path": record.__dict__.get("request_path", ""),
            "method": record.__dict__.get("request_method", ""),
            "status": record.__dict__.get("response_status"),
            "actor_id": record.__dict__.get("actor_id"),
        }
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
