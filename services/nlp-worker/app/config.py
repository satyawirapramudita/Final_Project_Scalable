"""Konfigurasi nlp-worker dari environment variable."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "nlp-worker")
PORT = _int("PORT", 8100)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
