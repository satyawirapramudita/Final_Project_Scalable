"""Akses search_db (MySQL): pool koneksi terbatas + skema."""

from __future__ import annotations

import logging
import queue
import threading
import time
from contextlib import contextmanager

import pymysql
from prometheus_client import Counter
from pymysql.cursors import DictCursor

from . import config

log = logging.getLogger("search.db")

POOL_TIMEOUTS = Counter(
    "search_db_pool_timeouts_total",
    "Jumlah request yang gagal karena tidak kebagian koneksi database",
)

# Satu baris per puisi. Kolom content_* dan tags_* punya cap waktu event
# masing-masing supaya event yang datang terlambat tidak menimpa data yang
# lebih baru. `deleted` adalah tombstone: event created/analyzed yang
# terlambat tidak menghidupkan kembali puisi yang sudah dihapus.
DDL = [
    """
    CREATE TABLE IF NOT EXISTS search_poem (
        poem_id    INT          NOT NULL PRIMARY KEY,
        user_id    INT          NULL,
        judul      VARCHAR(150) NULL,
        isi        TEXT         NULL,
        kategori   VARCHAR(50)  NULL,
        tags       VARCHAR(500) NOT NULL DEFAULT '',
        deleted    TINYINT(1)   NOT NULL DEFAULT 0,
        content_at DATETIME(6)  NULL,
        tags_at    DATETIME(6)  NULL,
        KEY idx_user (user_id, deleted),
        FULLTEXT KEY ft_search (judul, isi, tags)
    ) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci
    """,
]

_IDLE_PING_AFTER = 30  # detik; koneksi yang lama menganggur dicek dulu
_RETRY_COOLDOWN = 5  # detik; jeda antar percobaan menyambung saat database mati


class Unavailable(Exception):
    """Database tidak bisa dipakai saat ini (penuh atau tidak terjangkau)."""


class PoolTimeout(Unavailable):
    """Tidak ada koneksi bebas dalam batas waktu."""


class DBDown(Unavailable):
    """Database tidak terjangkau; request gagal cepat tanpa mencoba menyambung."""


class Pool:
    """Pool koneksi sederhana dengan batas atas.

    - Batas atas melindungi MySQL (dipakai bersama semua layanan): request yang
      menunggu terlalu lama gagal cepat dengan PoolTimeout.
    - Saat MySQL tidak terjangkau, menyambung bisa memakan waktu lama (mis. ~8 dtk
      untuk resolusi DNS container yang berhenti). Maka hanya SATU request yang
      boleh mencoba menyambung lagi; yang lain gagal seketika dengan DBDown.
    """

    def __init__(self, size: int, acquire_timeout: float) -> None:
        self._slots = threading.BoundedSemaphore(size)
        self._idle: queue.LifoQueue = queue.LifoQueue()
        self._timeout = acquire_timeout
        self._failing = False
        self._retry_after = 0.0
        self._probe = threading.Lock()

    @staticmethod
    def _new() -> pymysql.connections.Connection:
        return pymysql.connect(
            host=config.DB_HOST,
            port=config.DB_PORT,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME,
            cursorclass=DictCursor,
            autocommit=True,
            charset="utf8mb4",
            connect_timeout=3,
            read_timeout=5,
            write_timeout=5,
        )

    def _take(self) -> pymysql.connections.Connection:
        while True:
            try:
                conn, last_used = self._idle.get_nowait()
            except queue.Empty:
                break
            if time.monotonic() - last_used > _IDLE_PING_AFTER:
                try:
                    conn.ping(reconnect=False)
                except Exception:  # noqa: BLE001
                    _close(conn)
                    continue
            return conn
        try:
            return self._new()
        except (pymysql.err.OperationalError, OSError) as exc:
            self._failing = True
            self._retry_after = time.monotonic() + _RETRY_COOLDOWN
            raise DBDown() from exc

    @contextmanager
    def connection(self):
        probing = False
        if self._failing:
            # Jeda sejenak setelah percobaan gagal, lalu satu request saja yang mencoba.
            if time.monotonic() < self._retry_after or not self._probe.acquire(blocking=False):
                raise DBDown()
            probing = True
        try:
            if not self._slots.acquire(timeout=self._timeout):
                POOL_TIMEOUTS.inc()
                raise PoolTimeout()
            conn = None
            try:
                conn = self._take()
                self._failing = False
                yield conn
            except Exception:
                # Koneksi bisa dalam keadaan tidak jelas (transaksi terbuka, putus):
                # buang saja, tutup membatalkan transaksi yang belum di-commit.
                if conn is not None:
                    _close(conn)
                    conn = None
                raise
            finally:
                if conn is not None:
                    self._idle.put((conn, time.monotonic()))
                self._slots.release()
        finally:
            if probing:
                self._probe.release()


def _close(conn: pymysql.connections.Connection) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


pool = Pool(config.DB_POOL_SIZE, config.DB_POOL_TIMEOUT)


def init_schema(retries: int = 30, delay: int = 2) -> None:
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    for stmt in DDL:
                        cur.execute(stmt)
            log.info("Skema search_db siap")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            log.warning("Menunggu database (%s/%s): %s", attempt, retries, exc)
            time.sleep(delay)
    raise RuntimeError(f"Tidak dapat menyiapkan skema search_db: {last}")
