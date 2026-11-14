"""Akses database wordcloud_db (MySQL via PyMySQL).

Tabel: term_frequency(puisi_id, term, count, updated_at)
Operasi: upsert (idempoten by puisi_id+term) dan delete by puisi_id.
"""

from __future__ import annotations

import logging
from typing import Any

import pymysql
import pymysql.cursors

from . import config

log = logging.getLogger("wordcloud.db")


def _connect() -> pymysql.Connection:
    return pymysql.connect(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def upsert_terms(poem_id: int, terms: list[dict[str, Any]]) -> None:
    """Simpan/perbarui frekuensi istilah untuk poem_id (idempoten)."""
    if not terms:
        return
    sql = """
        INSERT INTO term_frequency (puisi_id, term, count)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE count = VALUES(count)
    """
    rows = [(poem_id, t["term"], int(t["count"])) for t in terms]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    log.info("upsert %d term untuk poem_id=%d", len(rows), poem_id)


def delete_by_poem(poem_id: int) -> None:
    """Hapus semua data word cloud untuk poem_id."""
    sql = "DELETE FROM term_frequency WHERE puisi_id = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (poem_id,))
        conn.commit()
    log.info("hapus term_frequency untuk poem_id=%d", poem_id)


def get_terms(poem_id: int) -> list[dict[str, Any]]:
    """Ambil frekuensi istilah untuk poem_id, diurutkan count DESC."""
    sql = """
        SELECT term, count
        FROM term_frequency
        WHERE puisi_id = %s
        ORDER BY count DESC
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (poem_id,))
            return cur.fetchall()
