"""Akses database poem_db (MySQL)."""

from __future__ import annotations

import logging
import time

import pymysql
from pymysql.cursors import DictCursor

from . import config

log = logging.getLogger("poem.db")

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
        updated_at  DATETIME     NULL
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
            log.info("Skema poem_db siap")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Menunggu database (%s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"Tidak dapat menyiapkan skema poem_db: {last}")
