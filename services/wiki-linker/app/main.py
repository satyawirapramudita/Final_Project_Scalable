"""wiki-linker service — kerangka (F0). Pemilik: Dzaky.

Endpoint yang akan diimplementasikan pada F4:
    GET /api/wiki/{poem_id}

Catatan F4:
- Konsumsi poem.analyzed (queue wiki.q) -> cocokkan setiap Kata Puitif
  ke halaman Wikipedia; simpan ke wiki_db.
- Konsumsi poem.deleted -> hapus tautan puisi terkait.
- Cache hasil lookup Wikipedia di Redis (TTL jam-hari) karena API eksternal
  rentan rate limit.
- Panggilan keluar (Wikipedia) wajib punya timeout + retry + DLQ.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "wiki-linker")

app = FastAPI(title="puisi wiki-linker service", version="0.1.0")

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


# TODO(F4): konsumsi poem.analyzed/poem.deleted + lookup Wikipedia + wiki_db
