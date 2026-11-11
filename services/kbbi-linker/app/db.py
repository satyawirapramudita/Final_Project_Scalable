from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from . import config

log = logging.getLogger("kbbi.db")

DDL = [
    """CREATE TABLE IF NOT EXISTS kbbi_entries (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        kata VARCHAR(100) NOT NULL,
        normalized_kata VARCHAR(100) NOT NULL,
        bentuk_baku VARCHAR(100) NULL,
        kelas_kata VARCHAR(80) NULL,
        dataset_version VARCHAR(50) NOT NULL,
        UNIQUE KEY uq_kbbi_normalized_version (normalized_kata, dataset_version),
        KEY idx_kbbi_bentuk_baku (bentuk_baku)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS kbbi_definitions (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        entry_id BIGINT NOT NULL,
        urutan SMALLINT NOT NULL,
        definisi TEXT NOT NULL,
        UNIQUE KEY uq_kbbi_definition_order (entry_id, urutan),
        CONSTRAINT fk_kbbi_definition_entry FOREIGN KEY (entry_id)
            REFERENCES kbbi_entries(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS poetic_terms (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        display_term VARCHAR(100) NOT NULL,
        normalized_term VARCHAR(100) NOT NULL UNIQUE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS kbbi_poem_status (
        poem_id INT PRIMARY KEY,
        status VARCHAR(20) NOT NULL,
        deleted TINYINT(1) NOT NULL DEFAULT 0,
        analysis_at DATETIME(6) NULL,
        source_event_id CHAR(36) NULL,
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS poem_terms (
        poem_id INT NOT NULL,
        term_id BIGINT NOT NULL,
        analysis_at DATETIME(6) NOT NULL,
        PRIMARY KEY (poem_id, term_id),
        KEY idx_poem_terms_term (term_id),
        CONSTRAINT fk_poem_terms_term FOREIGN KEY (term_id)
            REFERENCES poetic_terms(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
]


class Unavailable(Exception):
    pass


class Pool:
    def __init__(self, size: int, timeout: int) -> None:
        self._slots = threading.BoundedSemaphore(size)
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._timeout = timeout

    @staticmethod
    def _new():
        return pymysql.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            cursorclass=DictCursor,
            autocommit=False,
            charset="utf8mb4",
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
        )

    @contextmanager
    def connection(self):
        if not self._slots.acquire(timeout=self._timeout):
            raise Unavailable("database pool penuh")
        conn = None
        try:
            try:
                conn, _last_used = self._idle.get_nowait()
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
                self._idle.put((conn, time.monotonic()))
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
            log.info("Skema kbbi_db siap")
            return
        except Exception as exc:
            last = exc
            log.warning("Menunggu kbbi_db (%s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"Tidak dapat menyiapkan kbbi_db: {last}")


class Repository:
    def apply_analysis(
        self, *, poem_id: int, event_id: str, occurred_at: datetime, terms: list[tuple[str, str]]
    ) -> str:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT IGNORE INTO kbbi_poem_status (poem_id,status) VALUES (%s,'pending')",
                    (poem_id,),
                )
                cur.execute(
                    "SELECT deleted,analysis_at,source_event_id FROM kbbi_poem_status "
                    "WHERE poem_id=%s FOR UPDATE",
                    (poem_id,),
                )
                row = cur.fetchone()
                if row["deleted"] or row["source_event_id"] == event_id or (
                    row["analysis_at"] is not None and occurred_at < row["analysis_at"]
                ):
                    conn.commit()
                    return "ignored"

                cur.execute("DELETE FROM poem_terms WHERE poem_id=%s", (poem_id,))
                if terms:
                    cur.executemany(
                        "INSERT INTO poetic_terms (display_term,normalized_term) VALUES (%s,%s) "
                        "ON DUPLICATE KEY UPDATE display_term=VALUES(display_term)",
                        terms,
                    )
                    normalized_terms = [normalized for _display, normalized in terms]
                    placeholders = ",".join(["%s"] * len(normalized_terms))
                    cur.execute(
                        f"SELECT id FROM poetic_terms WHERE normalized_term IN ({placeholders})",
                        normalized_terms,
                    )
                    cur.executemany(
                        "INSERT INTO poem_terms (poem_id,term_id,analysis_at) VALUES (%s,%s,%s)",
                        [(poem_id, row["id"], occurred_at) for row in cur.fetchall()],
                    )
                cur.execute(
                    "UPDATE kbbi_poem_status SET status='ready',deleted=0,analysis_at=%s,"
                    "source_event_id=%s WHERE poem_id=%s",
                    (occurred_at, event_id, poem_id),
                )
            conn.commit()
        return "applied"

    def apply_delete(self, *, poem_id: int, event_id: str, occurred_at: datetime) -> str:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM poem_terms WHERE poem_id=%s", (poem_id,))
                cur.execute(
                    "INSERT INTO kbbi_poem_status "
                    "(poem_id,status,deleted,analysis_at,source_event_id) VALUES (%s,'deleted',1,%s,%s) "
                    "ON DUPLICATE KEY UPDATE status='deleted',deleted=1,analysis_at=VALUES(analysis_at),"
                    "source_event_id=VALUES(source_event_id)",
                    (poem_id, occurred_at, event_id),
                )
            conn.commit()
        return "applied"

    def get_poem(self, poem_id: int) -> dict[str, Any] | None:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status,deleted,analysis_at FROM kbbi_poem_status WHERE poem_id=%s",
                (poem_id,),
            )
            status = cur.fetchone()
            if status is None:
                return None
            cur.execute(
                "SELECT pt.display_term,pt.normalized_term FROM poem_terms pm "
                "JOIN poetic_terms pt ON pt.id=pm.term_id WHERE pm.poem_id=%s ORDER BY pt.id",
                (poem_id,),
            )
            return {**status, "terms": cur.fetchall()}

    def get_entries(self, normalized_terms: list[str]) -> dict[str, dict[str, Any]]:
        if not normalized_terms:
            return {}
        placeholders = ",".join(["%s"] * len(normalized_terms))
        params: list[Any] = [config.KBBI_DATASET_VERSION, *normalized_terms]
        sql = (
            "SELECT e.id,e.normalized_kata,e.bentuk_baku,e.kelas_kata,d.urutan,d.definisi "
            "FROM kbbi_entries e LEFT JOIN kbbi_definitions d ON d.entry_id=e.id "
            f"WHERE e.dataset_version=%s AND e.normalized_kata IN ({placeholders}) "
            "ORDER BY e.id,d.urutan"
        )
        result: dict[str, dict[str, Any]] = {}
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            for row in cur.fetchall():
                entry = result.setdefault(
                    row["normalized_kata"],
                    {
                        "bentuk_baku": row["bentuk_baku"],
                        "kelas_kata": row["kelas_kata"],
                        "definisi": [],
                    },
                )
                if row["definisi"]:
                    entry["definisi"].append(row["definisi"])
        return result


repository = Repository()
