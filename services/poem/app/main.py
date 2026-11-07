"""poem service — kerangka (F0). Pemilik: Satya.

Endpoint yang akan diimplementasikan pada F2:
    GET    /api/poems
    POST   /api/poems
    GET    /api/poems/{id}
    PUT    /api/poems/{id}
    DELETE /api/poems/{id}

Catatan F2/F3:
- Verifikasi JWT lokal (tanpa memanggil auth service).
- Database poem_db sendiri (tabel puisi + puisi_tag).
- Setelah commit, publish poem.created/updated/deleted ke RabbitMQ
  (lihat shared/events.py), lalu konsumsi poem.analyzed untuk mengisi tag
  dan mengubah status menjadi 'done'.
- Jalur baca dilindungi cache Redis (cache-aside).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "poem")

app = FastAPI(title="puisi poem service", version="0.1.0")

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


# TODO(F2): CRUD puisi + verifikasi JWT lokal + poem_db
# TODO(F3): publish poem.created/updated/deleted + konsumsi poem.analyzed
