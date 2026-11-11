from __future__ import annotations

import json
import logging

from app.logging_config import JsonFormatter


def test_json_log_includes_event_correlation_and_service() -> None:
    record = logging.LogRecord(
        "kbbi.consumer",
        logging.INFO,
        __file__,
        1,
        "event applied",
        (),
        None,
    )
    record.event_id = "8ba04991-c388-4b0e-b0e0-2fa9e225fac4"
    record.correlation_id = "request-42"

    value = json.loads(JsonFormatter().format(record))

    assert value["service"] == "kbbi-linker"
    assert value["event_id"] == "8ba04991-c388-4b0e-b0e0-2fa9e225fac4"
    assert value["correlation_id"] == "request-42"
