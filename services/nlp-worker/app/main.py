"""nlp-worker — pipeline analisis teks asinkron (F3). Pemilik: Satya.

Mengonsumsi poem.created/poem.updated dari nlp.q, menghitung tag,
Kata Puitif, dan frekuensi istilah, lalu mem-publish poem.analyzed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from shared import events

from . import config, messaging, nlp

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("nlp-worker")

JOBS_PROCESSED = Counter(
    "worker_jobs_processed_total",
    "Total jobs processed",
    ["service", "status"],
)


def handle_poem_event(data: dict) -> None:
    payload = data.get("payload") or {}
    poem_id = payload.get("poem_id")
    if poem_id is None:
        log.warning("event tanpa poem_id, diabaikan")
        return
    poem_id = int(poem_id)

    tags, kata_puitif, terms = nlp.analyze(
        payload.get("judul", ""),
        payload.get("isi", ""),
        payload.get("kategori", ""),
        payload.get("keyword", ""),
    )

    messaging.publish(
        events.POEM_ANALYZED,
        events.poem_analyzed_payload(
            poem_id=poem_id, tags=tags, kata_puitif=kata_puitif, terms=terms
        ),
        data.get("correlation_id"),
    )
    JOBS_PROCESSED.labels(config.SERVICE_NAME, "ok").inc()
    log.info("poem %s dianalisis: %s tag, %s kata puitis", poem_id, len(tags), len(kata_puitif))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    messaging.start_created_consumer(handle_poem_event)
    yield


app = FastAPI(title="puisi nlp-worker", version="0.2.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
