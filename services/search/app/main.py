"""search service — kerangka (F0). Pemilik: Anders.

Endpoint yang akan diimplementasikan pada F4:
    GET /api/search?q=

Catatan F4:
- Konsumsi poem.created/updated/deleted/analyzed (queue search.q)
  untuk memelihara indeks pencarian sendiri.
- Keputusan terbuka: MySQL FULLTEXT atau Meilisearch.
- Cache hasil pencarian populer di Redis (TTL 30-60 detik).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "search")

app = FastAPI(title="puisi search service", version="0.1.0")

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


# TODO(F4): indeks pencarian + konsumsi event + endpoint /api/search
