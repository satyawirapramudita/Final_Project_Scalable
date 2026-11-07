"""wordcloud service — kerangka (F0). Pemilik: Lindra.

Endpoint yang akan diimplementasikan pada F4:
    GET /api/wordcloud/{poem_id}

Catatan F4:
- Konsumsi poem.analyzed (queue wordcloud.q) -> simpan frekuensi istilah
  dari payload terms[{term,count}] ke wordcloud_db.
- Konsumsi poem.deleted -> hapus data word cloud puisi terkait.
- Idempotent (upsert by poem_id + term).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "wordcloud")

app = FastAPI(title="puisi wordcloud service", version="0.1.0")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# TODO(F4): konsumsi poem.analyzed/poem.deleted + wordcloud_db + endpoint baca
