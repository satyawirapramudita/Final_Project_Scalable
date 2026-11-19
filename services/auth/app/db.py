"""Akses database auth_db (MySQL)."""

from __future__ import annotations

import logging
import threading
import time

import pymysql
from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

from shared import backoff
from shared.metrics import DB_POOL_CONNECTIONS

from . import config

log = logging.getLogger("auth.db")

# Pool dibuat lazy saat koneksi pertama dibutuhkan, dengan double-checked
# locking karena diakses dari thread uvicorn yang berbeda-beda.
_POOL: PooledDB | None = None
_POOL_LOCK = threading.Lock()

DDL = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        username    VARCHAR(50)  NOT NULL UNIQUE,
        password    VARCHAR(255) NOT NULL,          -- PBKDF2: salt:digest
        nama        VARCHAR(100) NOT NULL,
        no_id       VARCHAR(30)  NULL,
        created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
    """,
]


def _get_pool() -> PooledDB:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = PooledDB(
                    creator=pymysql,
                    mincached=config.DB_POOL_MIN,
                    maxconnections=config.DB_POOL_MAX,
                    # ping=1: koneksi di-ping saat diambil; koneksi mati
                    # (mis. MySQL restart) otomatis dibuat ulang.
                    ping=1,
                    host=config.DB_HOST,
                    port=config.DB_PORT,
                    user=config.DB_USER,
                    password=config.DB_PASSWORD,
                    database=config.DB_NAME,
                    cursorclass=DictCursor,
                    autocommit=True,
                    charset="utf8mb4",
                )
    return _POOL


def connect() -> pymysql.connections.Connection:
    """Ambil koneksi dari pool. close() mengembalikannya ke pool, bukan menutup."""
    return _get_pool().connection()


def _pool_stats() -> dict:
    """Statistik pool untuk gauge Prometheus (dipanggil dinamis saat scrape)."""
    if _POOL is None:
        return {"idle": 0, "in_use": 0}
    return {"idle": len(_POOL._idle_cache), "in_use": _POOL._connections}


DB_POOL_CONNECTIONS.labels(config.SERVICE_NAME, "idle").set_function(
    lambda: _pool_stats()["idle"]
)
DB_POOL_CONNECTIONS.labels(config.SERVICE_NAME, "in_use").set_function(
    lambda: _pool_stats()["in_use"]
)


def init_schema(retries: int = 30, delay: int = 2) -> None:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    for stmt in DDL:
                        cur.execute(stmt)
            finally:
                conn.close()
            log.info("Skema auth_db siap")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Menunggu database (%s/%s): %s", attempt, retries, exc)
            time.sleep(backoff.equal_jitter(delay))
    raise RuntimeError(f"Tidak dapat menyiapkan skema auth_db: {last}")
