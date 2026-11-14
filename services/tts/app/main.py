"""tts service — kerangka (F0). Pemilik: Anders.

STATUS: DITUNDA / OPSIONAL.

Endpoint rencana (jika diaktifkan):
    GET /api/tts/{poem_id}   -> status + URL audio

Catatan:
- Konsumsi poem.created/updated (queue tts.q) -> hasilkan audio, simpan ke S3.
- tts.q didefinisikan di shared/events.py tetapi belum di-bind ke exchange sampai fitur aktif
  (antrean tanpa konsumen hanya menumpuk pesan).
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

SERVICE_NAME = os.getenv("SERVICE_NAME", "tts")

app = FastAPI(title="puisi tts service", version="0.1.0")

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


# TODO(F4, opsional): TTS + S3 + endpoint status
