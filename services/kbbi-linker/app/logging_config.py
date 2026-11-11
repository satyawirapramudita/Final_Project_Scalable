from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from . import config


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        value = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "service": config.SERVICE_NAME,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "event_id", "correlation_id"):
            field_value = getattr(record, field, None)
            if field_value:
                value[field] = field_value
        if record.exc_info:
            value["exception"] = self.formatException(record.exc_info)
        return json.dumps(value, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
