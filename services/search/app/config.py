"""Konfigurasi search service dari environment variable."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "search")
PORT = _int("PORT", 8004)

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "search_db")
DB_USER = os.getenv("DB_USER", "search_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

# Koneksi ke MySQL dibatasi karena server dipakai bersama semua layanan.
# Request yang tidak kebagian koneksi dalam DB_POOL_TIMEOUT detik gagal
# cepat (503) alih-alih menumpuk.
DB_POOL_SIZE = _int("DB_POOL_SIZE", 10)
DB_POOL_TIMEOUT = _int("DB_POOL_TIMEOUT", 2)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
CONSUMER_PREFETCH = _int("CONSUMER_PREFETCH", 10)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _int("REDIS_PORT", 6379)

CACHE_TTL_SEARCH = _int("CACHE_TTL_SEARCH", 45)

DEFAULT_LIMIT = 10
MAX_LIMIT = _int("SEARCH_MAX_LIMIT", 20)
MAX_PAGE = _int("SEARCH_MAX_PAGE", 50)
MAX_QUERY_LEN = 100
MAX_QUERY_TOKENS = 8
