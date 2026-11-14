"""wordcloud service — implementasi F4. Pemilik: Lindra.

Endpoint:
    GET /api/wordcloud/{poem_id}  → kembalikan frekuensi istilah puisi.

Arsitektur event‑driven (RabbitMQ):
- Konsumsi `poem.analyzed` (queue `wordcloud.q`) → upsert `term_frequency`
  dari payload `terms[{term, count}]` ke `wordcloud_db`.
- Konsumsi `poem.deleted` → hapus seluruh data word‑cloud puisi terkait.
- Idempotent: upsert by `(puisi_id, term)`.

**Catatan visualisasi:** Frontend dapat menampilkan word‑cloud dengan
library JavaScript **wordcloud2.js** yang memanggil endpoint ini
dan merender array `{term, count}` menjadi gambar word‑cloud di-browser.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from shared import events

from . import config, db, messaging

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("wordcloud")

# ── Metrik Prometheus ─────────────────────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)
EVENTS_PROCESSED = Counter(
    "wordcloud_events_processed_total",
    "Total events processed by wordcloud service",
    ["service", "event", "status"],
)


# ── Handler event RabbitMQ ────────────────────────────────────────────────────

def handle_wordcloud_event(data: dict) -> None:
    """Handler tunggal untuk poem.analyzed dan poem.deleted dari wordcloud.q."""
    event_name: str = data.get("event_name", "")
    payload: dict = data.get("payload") or {}

    poem_id = payload.get("poem_id")
    if poem_id is None:
        log.warning("Event '%s' tanpa poem_id, diabaikan.", event_name)
        return
    poem_id = int(poem_id)

    if event_name == events.POEM_ANALYZED:
        terms: list[dict] = payload.get("terms") or []
        db.upsert_terms(poem_id, terms)
        EVENTS_PROCESSED.labels(config.SERVICE_NAME, event_name, "ok").inc()
        log.info("poem.analyzed poem_id=%d → %d term disimpan.", poem_id, len(terms))

    elif event_name == events.POEM_DELETED:
        db.delete_by_poem(poem_id)
        EVENTS_PROCESSED.labels(config.SERVICE_NAME, event_name, "ok").inc()
        log.info("poem.deleted poem_id=%d → term_frequency dihapus.", poem_id)

    else:
        log.debug("Event '%s' diabaikan (bukan urusan wordcloud).", event_name)


# ── Lifespan: jalankan consumer saat startup ──────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    messaging.start_wordcloud_consumer(handle_wordcloud_event)
    log.info("Wordcloud consumer aktif pada queue '%s'.", events.QUEUE_WORDCLOUD)
    yield


# ── Aplikasi FastAPI ──────────────────────────────────────────────────────────

app = FastAPI(
    title="puisi wordcloud service",
    version="0.4.0",
    lifespan=lifespan,
)


# ── Endpoint: baca word cloud ─────────────────────────────────────────────────

@app.get("/api/wordcloud/{poem_id}")
def get_wordcloud(poem_id: int) -> JSONResponse:
    """Kembalikan daftar frekuensi istilah untuk puisi tertentu.

    Response (200):
        {
            "poem_id": 42,
            "terms": [{"term": "hujan", "count": 5}, ...]
        }
    Response (404): jika tidak ada data untuk poem_id tersebut.
    """
    REQUEST_COUNT.labels(config.SERVICE_NAME, "GET", "/api/wordcloud/{poem_id}", 200).inc()
    terms = db.get_terms(poem_id)
    if not terms:
        REQUEST_COUNT.labels(config.SERVICE_NAME, "GET", "/api/wordcloud/{poem_id}", 404).inc()
        raise HTTPException(status_code=404, detail=f"Tidak ada data word cloud untuk poem_id={poem_id}")
    return JSONResponse({"poem_id": poem_id, "terms": terms})


# ── Endpoint: health & metrics ────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
