"""Akses database poem_db (MySQL)."""

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

log = logging.getLogger("poem.db")

# Pool dibuat lazy saat koneksi pertama dibutuhkan, dengan double-checked
# locking karena diakses dari thread uvicorn yang berbeda-beda.
_POOL: PooledDB | None = None
_POOL_LOCK = threading.Lock()

DDL = [
    """
    CREATE TABLE IF NOT EXISTS puisi (
        id          INT AUTO_INCREMENT PRIMARY KEY,
        user_id     INT          NOT NULL,
        judul       VARCHAR(150) NOT NULL,
        tgl_submit  DATETIME     NOT NULL,
        isi         TEXT         NOT NULL,
        bait        TEXT         NULL,
        kategori    VARCHAR(50)  NOT NULL,
        keyword     VARCHAR(255) NULL,
        gambar_file VARCHAR(255) NULL,
        status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
        versi       INT          NOT NULL DEFAULT 1,
        updated_at  DATETIME     NULL,
        visibilitas VARCHAR(10)  NOT NULL DEFAULT 'public'
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS puisi_tag (
        puisi_id INT         NOT NULL,
        tag      VARCHAR(60) NOT NULL,
        PRIMARY KEY (puisi_id, tag)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4
    """,
]


# Kolom yang ditambahkan setelah tabel pertama kali dibuat. CREATE TABLE IF NOT
# EXISTS tidak mengubah tabel lama (volume MySQL yang sudah ada), dan MySQL 8
# tidak punya ADD COLUMN IF NOT EXISTS, jadi dicek lewat information_schema.
# Puisi lama otomatis 'public' (perilaku sebelum fitur visibilitas).
MIGRATIONS = [
    ("puisi", "visibilitas", "VARCHAR(10) NOT NULL DEFAULT 'public'"),
]


def _migrate(cur) -> None:
    for table, column, definition in MIGRATIONS:
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (table, column),
        )
        if cur.fetchone()["n"] == 0:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            log.info("Migrasi: kolom %s.%s ditambahkan", table, column)


def _get_pool() -> PooledDB:
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = PooledDB(
                    creator=pymysql,
                    mincached=config.DB_POOL_MIN,
                    maxconnections=config.DB_POOL_MAX,
                    # blocking=True: request ke-(max+1) MENUNGGU koneksi bebas.
                    # Default DBUtils (False) langsung melempar
                    # TooManyConnectionsError -> HTTP 500 saat beban tinggi
                    # (terbukti: 40 submit paralel -> 17x 500).
                    blocking=True,
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
                    # Batas waktu agar koneksi yang macet tidak menahan slot pool
                    # selamanya (blocking=True menunggu tanpa batas).
                    connect_timeout=3,
                    read_timeout=10,
                    write_timeout=10,
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
                    _migrate(cur)
            finally:
                conn.close()
            log.info("Skema poem_db siap")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Menunggu database (%s/%s): %s", attempt, retries, exc)
            time.sleep(backoff.equal_jitter(delay))
    raise RuntimeError(f"Tidak dapat menyiapkan skema poem_db: {last}")
