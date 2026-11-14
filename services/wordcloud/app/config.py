"""Konfigurasi wordcloud service dari environment variable."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "wordcloud")
PORT = _int("PORT", 8003)

# MySQL (nama variabel sama dengan layanan lain: DB_*)
DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "wordcloud_db")
DB_USER = os.getenv("DB_USER", "wordcloud_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_POOL_SIZE = _int("DB_POOL_SIZE", 10)
DB_POOL_TIMEOUT = _int("DB_POOL_TIMEOUT", 2)

# Redis (cache-aside hasil word cloud per puisi)
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _int("REDIS_PORT", 6379)
CACHE_TTL = _int("CACHE_TTL_WORDCLOUD", 600)

# Batas jumlah kata yang dikirim ke frontend (wordcloud2.js lambat bila ribuan kata)
MAX_TERMS = _int("WORDCLOUD_MAX_TERMS", 100)

# RabbitMQ
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
