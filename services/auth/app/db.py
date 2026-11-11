"""Akses database auth_db (MySQL)."""

from __future__ import annotations

import logging
import time

import pymysql
from pymysql.cursors import DictCursor

from . import config

log = logging.getLogger("auth.db")

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


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        cursorclass=DictCursor,
        autocommit=True,
        charset="utf8mb4",
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
            time.sleep(delay)
    raise RuntimeError(f"Tidak dapat menyiapkan skema auth_db: {last}")
