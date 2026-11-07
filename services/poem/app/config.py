"""Konfigurasi poem service dari environment variable."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "poem")
PORT = _int("PORT", 8002)

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "poem_db")
DB_USER = os.getenv("DB_USER", "poem_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _int("REDIS_PORT", 6379)

ASSET_BASE_URL = os.getenv("ASSET_BASE_URL", "").rstrip("/")

CACHE_TTL_DETAIL = _int("CACHE_TTL_DETAIL", 300)
CACHE_TTL_LIST = _int("CACHE_TTL_LIST", 60)
