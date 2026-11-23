"""Transactional outbox untuk event poem.

Masalah yang diatasi: sebelumnya poem melakukan INSERT/UPDATE/DELETE (commit)
lalu publish ke RabbitMQ secara terpisah. Bila RabbitMQ mati di antara keduanya,
event hilang diam-diam: puisi tersimpan tetapi tidak pernah dianalisis,
diindeks search, atau dibuatkan wordcloud/KBBI.

Cara kerja:
1. Endpoint menulis perubahan puisi DAN satu baris `outbox` dalam SATU
   transaksi (`add`). Keduanya commit bersama atau tidak sama sekali.
2. Relay (thread latar di setiap replika poem) mengambil baris yang belum
   terkirim dengan `SELECT ... FOR UPDATE SKIP LOCKED`, publish dengan
   publisher confirms, lalu menandai `published_at`. SKIP LOCKED membuat
   beberapa replika poem berbagi pekerjaan tanpa mengirim baris yang sama.
3. Saat RabbitMQ mati, request tetap sukses; baris menumpuk di outbox dan
   dikirim otomatis begitu RabbitMQ pulih (backoff full jitter).

Jaminan: at-least-once. Bila relay mati setelah publish tetapi sebelum commit,
baris dikirim ulang; konsumen sudah idempoten (event_id/occurred_at, upsert).
"""

from __future__ import annotations

import json
import logging
import threading
import time

from prometheus_client import Counter, Gauge

from shared import backoff, events
from shared.rabbit import Publisher

from . import config, db

log = logging.getLogger("poem.outbox")

PUBLISHED = Counter(
    "poem_outbox_published_total", "Event outbox yang berhasil dikirim ke RabbitMQ"
)
RELAY_ERRORS = Counter(
    "poem_outbox_relay_errors_total", "Putaran relay outbox yang gagal (RabbitMQ/DB)"
)
PENDING = Gauge("poem_outbox_pending", "Event outbox yang belum terkirim")
OLDEST_AGE = Gauge(
    "poem_outbox_oldest_pending_seconds", "Umur event outbox tertua yang belum terkirim"
)

_wake = threading.Event()
_stop = threading.Event()


# ── Sisi penulis (dipanggil di dalam db.transaction()) ────────


def add(cur, event_name: str, payload: dict, correlation_id: str | None = None) -> str:
    """Tulis event ke outbox memakai cursor transaksi yang sedang berjalan.

    Amplop (event_id, occurred_at) dibuat SEKARANG, saat perubahan terjadi,
    bukan saat relay mengirimnya, agar urutan occurred_at di konsumen benar.
    """
    envelope = events.EventEnvelope(
        event_name=event_name, payload=payload, correlation_id=correlation_id
    )
    cur.execute(
        "INSERT INTO outbox (event_id, routing_key, envelope) VALUES (%s, %s, %s)",
        (envelope.event_id, event_name, json.dumps(envelope.to_dict(), ensure_ascii=False)),
    )
    return envelope.event_id


def notify() -> None:
    """Bangunkan relay segera setelah commit (tanpa menunggu interval polling)."""
    _wake.set()


# ── Relay ─────────────────────────────────────────────────────


def relay_once(publisher: Publisher, batch: int | None = None) -> int:
    """Kirim satu batch event tertunda. Kembalikan jumlah yang terkirim.

    READ COMMITTED: FOR UPDATE hanya mengunci baris yang terbaca (tanpa gap
    lock), jadi INSERT outbox baru dari request lain tidak ikut tertahan.
    Bila publish gagal di tengah batch, baris yang sudah terkirim tetap
    ditandai lalu exception diteruskan agar relay mundur (backoff).
    """
    batch = batch or config.OUTBOX_BATCH
    sent: list[int] = []
    error: Exception | None = None
    with db.transaction(isolation="READ COMMITTED") as cur:
        cur.execute(
            "SELECT id, event_id, routing_key, envelope FROM outbox "
            "WHERE published_at IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED",
            (batch,),
        )
        rows = cur.fetchall()
        for row in rows:
            try:
                publisher.publish_raw(
                    row["routing_key"], row["envelope"].encode("utf-8"), row["event_id"]
                )
            except Exception as exc:  # noqa: BLE001
                error = exc
                cur.execute(
                    "UPDATE outbox SET attempts = attempts + 1, last_error = %s WHERE id = %s",
                    (str(exc)[:255] or type(exc).__name__, row["id"]),
                )
                break
            sent.append(row["id"])
        if sent:
            placeholders = ",".join(["%s"] * len(sent))
            cur.execute(
                f"UPDATE outbox SET published_at = NOW(3) WHERE id IN ({placeholders})",
                tuple(sent),
            )
    PUBLISHED.inc(len(sent))
    if error is not None:
        raise error
    return len(sent)


def refresh_stats() -> None:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n, "
                "TIMESTAMPDIFF(MICROSECOND, MIN(created_at), NOW(3)) AS age_us "
                "FROM outbox WHERE published_at IS NULL"
            )
            row = cur.fetchone() or {}
    finally:
        conn.close()
    PENDING.set(row.get("n") or 0)
    OLDEST_AGE.set((row.get("age_us") or 0) / 1_000_000)


def cleanup() -> int:
    """Hapus baris yang sudah terkirim lebih lama dari masa simpan (bertahap)."""
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM outbox WHERE published_at IS NOT NULL "
                "AND published_at < NOW(3) - INTERVAL %s HOUR LIMIT 1000",
                (config.OUTBOX_RETENTION_HOURS,),
            )
            return cur.rowcount
    finally:
        conn.close()


def _safe(fn, what: str) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001
        log.warning("%s outbox gagal", what, exc_info=True)


def _run(publisher: Publisher) -> None:
    failures = 0
    last_stats = last_cleanup = 0.0
    log.info("Relay outbox aktif (batch=%s)", config.OUTBOX_BATCH)
    while not _stop.is_set():
        _wake.clear()
        try:
            publisher.ensure()
            sent = relay_once(publisher)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            RELAY_ERRORS.inc()
            delay = backoff.full_jitter(failures, base=1, cap=15)
            log.warning("Relay outbox gagal (percobaan %s), coba lagi %.1f dtk: %s",
                        failures, delay, exc)
            _safe(refresh_stats, "Statistik")
            _stop.wait(delay)
            continue
        if failures:
            log.info("Relay outbox pulih setelah %s kegagalan", failures)
            failures = 0

        now = time.monotonic()
        if now - last_stats >= 5:
            _safe(refresh_stats, "Statistik")
            last_stats = now
        if now - last_cleanup >= 600:
            _safe(cleanup, "Pembersihan")
            last_cleanup = now

        # Batch penuh = masih ada antrean, langsung lanjut. Selain itu tunggu
        # notify() dari endpoint atau interval polling (fallback antar-replika).
        if sent < config.OUTBOX_BATCH:
            _wake.wait(config.OUTBOX_POLL_SECONDS)
            publisher.heartbeat()


def start_relay() -> threading.Thread | None:
    if not config.OUTBOX_RELAY_ENABLED:
        log.info("Relay outbox dinonaktifkan (OUTBOX_RELAY_ENABLED=0)")
        return None
    publisher = Publisher(
        config.RABBITMQ_HOST,
        config.RABBITMQ_PORT,
        config.RABBITMQ_USER,
        config.RABBITMQ_PASSWORD,
        config.RABBITMQ_VHOST,
        connect_retries=1,
    )
    thread = threading.Thread(target=_run, args=(publisher,), name="outbox-relay", daemon=True)
    thread.start()
    return thread
