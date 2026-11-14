"""wordcloud service — implementasi F4. Pemilik: Lindra.

Endpoint:
    GET /api/wordcloud/{poem_id}  -> frekuensi kata puisi (butuh JWT, seperti layanan lain)
        200 {ok, poem_id, terms:[{term, count}], cached}
        404 belum dianalisis / puisi sudah dihapus
        503 MySQL tidak tersedia

Event-driven (RabbitMQ, queue `wordcloud.q`):
- `poem.analyzed` -> ganti seluruh kata puisi dengan `terms[{term, count}]` terbaru
  (kata versi lama dihapus, jadi word cloud ikut berubah saat puisi diedit).
- `poem.deleted`  -> hapus kata + tombstone.
- Event dobel / tidak berurutan diabaikan berdasarkan `occurred_at` (lihat db.py).

Cache: Redis cache-aside `wordcloud:{poem_id}`, diinvalidasi oleh event.
Frontend merender hasilnya dengan wordcloud2.js.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Path, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from shared import events
from shared.metrics import REQUEST_SECONDS, route_path

from . import config, db, messaging
from .cache import cache
from .security import current_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("wordcloud")

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


def _event_time(raw) -> datetime:
    """occurred_at amplop -> datetime UTC naif (format kolom DATETIME MySQL)."""
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def handle_wordcloud_event(data: dict) -> None:
    """Handler tunggal untuk poem.analyzed dan poem.deleted dari wordcloud.q."""
    event_name: str = data.get("event_name", "")
    payload: dict = data.get("payload") or {}
    if event_name not in (events.POEM_ANALYZED, events.POEM_DELETED):
        return
    try:
        poem_id = int(payload.get("poem_id"))
    except (TypeError, ValueError):
        log.warning("event %s tanpa poem_id valid, diabaikan", event_name)
        EVENTS_PROCESSED.labels(config.SERVICE_NAME, event_name, "invalid").inc()
        return
    occurred_at = _event_time(data.get("occurred_at"))

    if event_name == events.POEM_ANALYZED:
        outcome = db.apply_analysis(poem_id, occurred_at, payload.get("terms") or [])
    else:
        outcome = db.apply_delete(poem_id, occurred_at)
    if outcome == "applied":
        cache.invalidate(poem_id)
    EVENTS_PROCESSED.labels(config.SERVICE_NAME, event_name, outcome).inc()
    log.info("%s poem_id=%d -> %s", event_name, poem_id, outcome)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    messaging.start_wordcloud_consumer(handle_wordcloud_event)
    log.info("consumer wordcloud aktif pada queue '%s'", events.QUEUE_WORDCLOUD)
    yield


app = FastAPI(title="puisi wordcloud service", version="0.5.0", lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    path = route_path(request)
    REQUEST_COUNT.labels(config.SERVICE_NAME, request.method, path, response.status_code).inc()
    REQUEST_SECONDS.labels(config.SERVICE_NAME, request.method, path).observe(
        time.perf_counter() - started
    )
    return response


@app.get("/api/wordcloud/{poem_id}")
def get_wordcloud(poem_id: int = Path(gt=0), _user: dict = Depends(current_user)) -> dict:
    cached = cache.get(poem_id)
    if cached is not None:
        result, from_cache = cached, True
    else:
        try:
            result = db.get_wordcloud(poem_id, config.MAX_TERMS)
        except db.Unavailable:
            raise HTTPException(status_code=503, detail="Layanan word cloud sedang tidak tersedia")
        from_cache = False
        if result is not None:
            cache.set(poem_id, result)

    if result is None:
        raise HTTPException(status_code=404, detail="Word cloud belum tersedia (puisi belum dianalisis)")
    if result.get("deleted"):
        raise HTTPException(status_code=404, detail="Puisi sudah dihapus")
    return {"ok": True, "poem_id": poem_id, "terms": result["terms"], "cached": from_cache}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
