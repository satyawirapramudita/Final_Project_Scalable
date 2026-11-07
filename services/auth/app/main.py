"""auth service — kerangka (F0). Pemilik: Ardi.

Endpoint yang akan diimplementasikan pada F1:
    POST /api/auth/register
    POST /api/auth/login
    POST /api/auth/logout
    GET  /api/auth/me

Catatan F1:
- Pindahkan hash_password/verify_password (PBKDF2) dari Tugas 1.
- Terbitkan JWT HS256 (secret dari env JWT_SECRET) untuk diverifikasi lokal
  oleh semua layanan lain.
- Gunakan database auth_db sendiri.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "auth")

app = FastAPI(title="puisi auth service", version="0.1.0")

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


# TODO(F1): register/login/logout/me + JWT + auth_db
