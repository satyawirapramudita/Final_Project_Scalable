"""<nama> service — kerangka. Ganti SERVICE_NAME dan port sesuai kebutuhan.

Setiap layanan WAJIB menyediakan:
    GET /health   -> status sederhana (dipakai healthcheck Docker)
    GET /metrics  -> metrik Prometheus
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "template")

app = FastAPI(title=f"puisi {SERVICE_NAME}", version="0.1.0")

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
