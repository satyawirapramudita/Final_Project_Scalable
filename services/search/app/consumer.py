"""Konsumen search.q: memelihara indeks pencarian dari event poem.*.

Handler idempoten: pengiriman ganda atau tidak berurutan tidak merusak
indeks (lihat aturan cap waktu dan tombstone di engine.py).
Kegagalan dilempar ke shared.rabbit.start_consumer, yang mengulang dengan
backoff lalu memindahkan pesan ke search.q.dlq.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pymysql
from prometheus_client import Counter

from shared import events
from shared.rabbit import start_consumer

from . import cache, config, db
from .engine import APPLIED, IGNORED, engine
from .textutil import join_tags

log = logging.getLogger("search.consumer")

DB_OUTAGE_WAIT_SECONDS = 45

EVENTS = Counter(
    "search_events_total",
    "Event poem.* yang diproses indeks",
    ["event", "outcome"],  # outcome: applied | ignored | error
)


def _parse_time(value) -> datetime:
    """ISO 8601 -> UTC tanpa tzinfo (sesuai kolom DATETIME). Kosong/rusak -> sekarang."""
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).replace(tzinfo=None)


def _required_int(payload: dict, key: str) -> int:
    try:
        return int(payload[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"payload tanpa {key} yang valid") from None


def _required_text(payload: dict, key: str, max_len: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload tanpa {key} yang valid")
    return value.strip()[:max_len]


def _apply(name: str, payload: dict, at: datetime) -> tuple[str, int | None]:
    if name in (events.POEM_CREATED, events.POEM_UPDATED):
        return engine.apply_content(
            poem_id=_required_int(payload, "poem_id"),
            user_id=_required_int(payload, "user_id"),
            judul=_required_text(payload, "judul", 150),
            isi=_required_text(payload, "isi", 10_000),
            kategori=_required_text(payload, "kategori", 50),
            at=at,
        )
    if name == events.POEM_ANALYZED:
        return engine.apply_analysis(
            poem_id=_required_int(payload, "poem_id"),
            tags=join_tags(payload.get("tags") or []),
            at=at,
        )
    if name == events.POEM_DELETED:
        return engine.apply_delete(poem_id=_required_int(payload, "poem_id"))
    return IGNORED, None


def _apply_waiting_for_db(name: str, payload: dict, at: datetime) -> tuple[str, int | None]:
    """Saat database tidak tersedia, tahan pesan (belum di-ack) dan coba lagi,
    alih-alih menghabiskan retry dan membuangnya ke DLQ: pemadaman singkat
    MySQL tidak boleh membuat indeks kehilangan event.

    Batasnya di bawah dua kali heartbeat RabbitMQ (60 dtk). Bila database tetap
    mati, pesan jatuh ke retry/DLQ biasa dan indeks bisa dibangun ulang dengan
    scripts/reindex_search.py."""
    deadline = time.monotonic() + DB_OUTAGE_WAIT_SECONDS
    while True:
        try:
            return _apply(name, payload, at)
        except (db.Unavailable, pymysql.err.OperationalError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.warning("database belum tersedia, event %s ditahan: %s", name, type(exc).__name__)
            time.sleep(2)


def handle_event(data: dict) -> None:
    name = str(data.get("event_name") or "unknown")
    payload = data.get("payload") or {}
    at = _parse_time(data.get("occurred_at"))
    try:
        outcome, _user_id = _apply_waiting_for_db(name, payload, at)
    except Exception:
        EVENTS.labels(name, "error").inc()
        raise
    EVENTS.labels(name, outcome).inc()
    if outcome == APPLIED:
        cache.bump()
    log.info("%s poem=%s -> %s", name, payload.get("poem_id"), outcome)


def start() -> None:
    start_consumer(
        host=config.RABBITMQ_HOST,
        port=config.RABBITMQ_PORT,
        user=config.RABBITMQ_USER,
        password=config.RABBITMQ_PASSWORD,
        vhost=config.RABBITMQ_VHOST,
        queue=events.QUEUE_SEARCH,
        handler=handle_event,
        prefetch=config.CONSUMER_PREFETCH,
    )
