from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from prometheus_client import Counter

from shared import events
from shared.rabbit import start_consumer

from . import config
from .cache import cache
from .db import repository
from .terms import unique_terms

log = logging.getLogger("kbbi.consumer")

EVENTS = Counter("kbbi_events_total", "Event yang diproses kbbi-linker", ["event", "outcome"])
MYSQL_INT_MAX = 2_147_483_647


def _required_int(payload: dict[str, Any], key: str) -> int:
    raw = payload.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"payload tanpa {key} yang valid")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"payload tanpa {key} yang valid") from None
    if value <= 0 or value > MYSQL_INT_MAX:
        raise ValueError(f"payload tanpa {key} yang valid")
    return value


def _event_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("occurred_at tidak valid") from None
    if parsed.tzinfo is None:
        raise ValueError("occurred_at harus memiliki timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _event_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("event_id tidak valid") from None


def apply_event(
    data: dict[str, Any], *, repository, cache_backend, max_terms: int
) -> str:
    name = str(data.get("event_name") or "")
    if name not in (events.POEM_ANALYZED, events.POEM_DELETED):
        return "ignored"
    payload = data.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload event tidak valid")
    poem_id = _required_int(payload, "poem_id")
    event_id = _event_id(data.get("event_id"))
    occurred_at = _event_time(data.get("occurred_at"))

    if name == events.POEM_ANALYZED:
        raw_terms = payload.get("kata_puitif", [])
        if not isinstance(raw_terms, list):
            raise ValueError("kata_puitif harus berupa array")
        outcome = repository.apply_analysis(
            poem_id=poem_id,
            event_id=event_id,
            occurred_at=occurred_at,
            terms=unique_terms(raw_terms, limit=max_terms),
        )
    else:
        outcome = repository.apply_delete(
            poem_id=poem_id, event_id=event_id, occurred_at=occurred_at
        )
    if outcome == "applied":
        cache_backend.invalidate_poem(poem_id)
    return outcome


def handle_event(data: dict[str, Any]) -> None:
    name = str(data.get("event_name") or "unknown")
    try:
        outcome = apply_event(
            data,
            repository=repository,
            cache_backend=cache,
            max_terms=config.KBBI_MAX_TERMS_PER_POEM,
        )
    except Exception:
        EVENTS.labels(name, "error").inc()
        raise
    EVENTS.labels(name, outcome).inc()
    log.info(
        "event=%s poem=%s outcome=%s",
        name,
        (data.get("payload") or {}).get("poem_id"),
        outcome,
        extra={
            "event_id": data.get("event_id"),
            "correlation_id": data.get("correlation_id"),
        },
    )


def start() -> None:
    start_consumer(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        user=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASSWORD,
        vhost=config.RABBITMQ_VHOST,
        queue=events.QUEUE_KBBI,
        handler=handle_event,
        prefetch=config.CONSUMER_PREFETCH,
    )
