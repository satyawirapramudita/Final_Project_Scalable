"""Fixture uji integrasi live untuk kbbi-linker.

Uji ini menembak MySQL/Redis/RabbitMQ SUNGGUHAN (bukan mock). Tujuannya
memverifikasi hal yang tidak bisa dibuktikan unit test: SQL, semantik
cache, idempotensi event, dan topologi broker.

Diaktifkan hanya bila KBBI_LIVE=1 agar `pytest tests` tetap hijau tanpa
infrastruktur. Lihat tests/live/README.md untuk cara menjalankan.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = SERVICE_ROOT.parents[1]
for path in (str(SERVICE_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("SERVICE_NAME", "kbbi-linker")
os.environ.setdefault("DB_HOST", "127.0.0.1")
os.environ.setdefault("DB_PORT", "13306")
os.environ.setdefault("DB_NAME", "kbbi_db")
os.environ.setdefault("DB_USER", "kbbi_user")
os.environ.setdefault("DB_PASSWORD", "kbbipw")
os.environ.setdefault("DB_POOL_SIZE", "8")
os.environ.setdefault("DB_POOL_TIMEOUT", "2")
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PORT", "16379")
os.environ.setdefault("RABBITMQ_HOST", "127.0.0.1")
os.environ.setdefault("RABBITMQ_PORT", "15673")
os.environ.setdefault("RABBITMQ_USER", "puisi")
os.environ.setdefault("RABBITMQ_PASSWORD", "puisipw")
os.environ.setdefault("JWT_SECRET", "live-test-secret")
os.environ.setdefault("KBBI_DATASET_VERSION", "kbbi-v-live")

requires_live = pytest.mark.skipif(
    os.getenv("KBBI_LIVE") != "1",
    reason="set KBBI_LIVE=1 dan jalankan docker compose -f tests/live/compose.yml",
)


def _wait(predicate, *, timeout: float = 180.0, interval: float = 1.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(interval)
    raise RuntimeError(f"dependensi tidak siap dalam {timeout}s: {last}")


@pytest.fixture(scope="session", autouse=True)
def infrastructure():
    if os.getenv("KBBI_LIVE") != "1":
        yield False
        return

    import pika
    import pymysql
    import redis

    def mysql_ready() -> bool:
        conn = pymysql.connect(
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"],
            connect_timeout=3,
        )
        conn.close()
        return True

    def redis_ready() -> bool:
        redis.Redis(
            host=os.environ["REDIS_HOST"],
            port=int(os.environ["REDIS_PORT"]),
            socket_connect_timeout=2,
        ).ping()
        return True

    def rabbit_ready() -> bool:
        params = pika.ConnectionParameters(
            host=os.environ["RABBITMQ_HOST"],
            port=int(os.environ["RABBITMQ_PORT"]),
            credentials=pika.PlainCredentials(
                os.environ["RABBITMQ_USER"], os.environ["RABBITMQ_PASSWORD"]
            ),
            connection_attempts=1,
        )
        conn = pika.BlockingConnection(params)
        conn.close()
        return True

    _wait(mysql_ready)
    _wait(redis_ready, timeout=60)
    _wait(rabbit_ready)
    yield True


@pytest.fixture
def schema(db_module):
    """Pastikan skema ada, lalu kosongkan tabel supaya hasil deterministik."""
    from app.db import pool

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        for table in (
            "poem_terms",
            "poetic_terms",
            "kbbi_poem_status",
            "kbbi_definitions",
            "kbbi_entries",
        ):
            cur.execute(f"TRUNCATE TABLE {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
    return db_module


@pytest.fixture
def db_module():
    from app import db

    db.init_schema()
    return db


@pytest.fixture
def florets_pool(db_module):
    return db_module


@pytest.fixture
def flush_cache():
    from app.cache import cache

    cache._client.flushdb()
    yield cache
    cache._client.flushdb()
