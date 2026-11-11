from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


SERVICE_NAME = os.getenv("SERVICE_NAME", "kbbi-linker")
PORT = _int("PORT", 8005)

DB_HOST = os.getenv("DB_HOST", "mysql")
DB_PORT = _int("DB_PORT", 3306)
DB_NAME = os.getenv("DB_NAME", "kbbi_db")
DB_USER = os.getenv("DB_USER", "kbbi_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_POOL_SIZE = _int("DB_POOL_SIZE", 8)
DB_POOL_TIMEOUT = _int("DB_POOL_TIMEOUT", 2)

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBITMQ_PORT = _int("RABBITMQ_PORT", 5672)
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "puisi")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "")
RABBITMQ_VHOST = os.getenv("RABBITMQ_VHOST", "/")
CONSUMER_PREFETCH = _int("CONSUMER_PREFETCH", 1)

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = _int("REDIS_PORT", 6379)
KBBI_ENTRY_CACHE_TTL = _int("KBBI_ENTRY_CACHE_TTL_SECONDS", 2_592_000)
KBBI_POEM_CACHE_TTL = _int("KBBI_POEM_CACHE_TTL_SECONDS", 86_400)
KBBI_NEGATIVE_CACHE_TTL = _int("KBBI_NEGATIVE_CACHE_TTL_SECONDS", 86_400)
KBBI_DATASET_VERSION = os.getenv("KBBI_DATASET_VERSION", "kbbi-v-local")
KBBI_MAX_TERMS_PER_POEM = _int("KBBI_MAX_TERMS_PER_POEM", 30)
