"""Akses database wordcloud_db (MySQL via PyMySQL).

Tabel:
- term_frequency(puisi_id, term, count, updated_at): frekuensi kata per puisi.
- wordcloud_poem_state(poem_id, analyzed_at, deleted): waktu event terakhir
  yang diterapkan + tombstone hapus. Dipakai agar event yang datang dobel atau
  tidak berurutan tidak menimpa data yang lebih baru, dan agar analisis yang
  terlambat tiba setelah puisi dihapus tidak menghidupkan word cloud lagi.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pymysql
import pymysql.cursors

from . import config

log = logging.getLogger("wordcloud.db")

DDL = [
    """CREATE TABLE IF NOT EXISTS term_frequency (
        puisi_id    INT          NOT NULL,
        term        VARCHAR(100) NOT NULL,
        count       INT          NOT NULL,
        updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (puisi_id, term)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS wordcloud_poem_state (
        poem_id      INT         NOT NULL PRIMARY KEY,
        analyzed_at  DATETIME(6) NULL,
        deleted      TINYINT(1)  NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


class Unavailable(Exception):
    """MySQL mati atau pool penuh; endpoint menjawab 503."""


class Pool:
    """Pool koneksi kecil dan terbatas (tidak membuka koneksi baru per request)."""

    def __init__(self, size: int, timeout: int) -> None:
        self._slots = threading.BoundedSemaphore(size)
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._timeout = timeout

    @staticmethod
    def _new() -> pymysql.Connection:
        return pymysql.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
        )

    @contextmanager
    def connection(self):
        if not self._slots.acquire(timeout=self._timeout):
            raise Unavailable("pool database penuh")
        conn = None
        try:
            try:
                conn = self._idle.get_nowait()
                conn.ping(reconnect=True)
            except queue.Empty:
                conn = self._new()
            yield conn
        except (pymysql.err.OperationalError, OSError) as exc:
            if conn is not None:
                conn.close()
                conn = None
            raise Unavailable("database tidak tersedia") from exc
        except Exception:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn is not None:
                self._idle.put(conn)
            self._slots.release()


pool = Pool(config.DB_POOL_SIZE, config.DB_POOL_TIMEOUT)


def init_schema(retries: int = 30, delay: int = 2) -> None:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with pool.connection() as conn, conn.cursor() as cur:
                for statement in DDL:
                    cur.execute(statement)
                conn.commit()
            log.info("skema wordcloud_db siap")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("menunggu wordcloud_db (%s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"tidak dapat menyiapkan wordcloud_db: {last}")


def _clean_terms(terms: list[dict[str, Any]]) -> list[tuple[str, int]]:
    merged: dict[str, int] = {}
    for t in terms or []:
        term = str(t.get("term") or "").strip()[:100]
        try:
            count = int(t.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if term and count > 0:
            merged[term] = merged.get(term, 0) + count
    return list(merged.items())


def apply_analysis(poem_id: int, occurred_at: datetime, terms: list[dict[str, Any]]) -> str:
    """Ganti SELURUH kata puisi dengan hasil analisis terbaru.

    Kata dari versi lama dihapus dulu, jadi setelah puisi diedit word cloud
    hanya berisi kata versi baru. Mengembalikan 'applied' atau 'ignored'.
    """
    rows = _clean_terms(terms)
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO wordcloud_poem_state (poem_id) VALUES (%s)", (poem_id,))
            cur.execute(
                "SELECT analyzed_at, deleted FROM wordcloud_poem_state WHERE poem_id=%s FOR UPDATE",
                (poem_id,),
            )
            state = cur.fetchone()
            if state["deleted"] or (
                state["analyzed_at"] is not None and occurred_at <= state["analyzed_at"]
            ):
                conn.commit()
                return "ignored"
            cur.execute("DELETE FROM term_frequency WHERE puisi_id=%s", (poem_id,))
            if rows:
                cur.executemany(
                    "INSERT INTO term_frequency (puisi_id, term, count) VALUES (%s, %s, %s)",
                    [(poem_id, term, count) for term, count in rows],
                )
            cur.execute(
                "UPDATE wordcloud_poem_state SET analyzed_at=%s WHERE poem_id=%s",
                (occurred_at, poem_id),
            )
        conn.commit()
    return "applied"


def apply_delete(poem_id: int, occurred_at: datetime) -> str:
    """Hapus kata puisi dan pasang tombstone (id puisi tidak pernah dipakai ulang)."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM term_frequency WHERE puisi_id=%s", (poem_id,))
            cur.execute(
                "INSERT INTO wordcloud_poem_state (poem_id, analyzed_at, deleted) VALUES (%s, %s, 1) "
                "ON DUPLICATE KEY UPDATE deleted=1",
                (poem_id, occurred_at),
            )
        conn.commit()
    return "applied"


def get_wordcloud(poem_id: int, limit: int) -> dict[str, Any] | None:
    """None bila puisi belum pernah dianalisis; {'deleted': True} bila sudah dihapus."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT analyzed_at, deleted FROM wordcloud_poem_state WHERE poem_id=%s",
            (poem_id,),
        )
        state = cur.fetchone()
        cur.execute(
            "SELECT term, count FROM term_frequency WHERE puisi_id=%s "
            "ORDER BY count DESC, term ASC LIMIT %s",
            (poem_id, limit),
        )
        terms = cur.fetchall()
        conn.commit()
    if state and state["deleted"]:
        return {"deleted": True, "terms": []}
    if state is None and not terms:
        return None
    return {"deleted": False, "terms": terms}
