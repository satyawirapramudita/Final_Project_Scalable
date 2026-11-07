"""nlp-worker — kerangka (F0). Pemilik: Satya.

STATUS: worker asinkron (tanpa API publik). Membuka /health dan /metrics saja.

Catatan F3:
- Konsumsi poem.created/updated (queue nlp.q).
- Pipeline: tokenisasi, stopword, ekstraksi keyword/tag, dan Kata Puitif.
- Publish poem.analyzed (queue poem.q, wordcloud.q, wiki.q, search.q).
- Idempotent, prefetch kecil, DLQ.
- Keputusan terbuka: pustaka NLP bahasa Indonesia.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "nlp-worker")

app = FastAPI(title="puisi nlp-worker", version="0.1.0")

JOBS_PROCESSED = Counter(
    "worker_jobs_processed_total",
    "Total jobs processed",
    ["service", "status"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# TODO(F3): consumer loop RabbitMQ (nlp.q) -> publish poem.analyzed
