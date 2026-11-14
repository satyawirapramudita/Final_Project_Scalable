"""Mesin pencarian di balik antarmuka kecil.

Implementasi saat ini: MySQL FULLTEXT (tanpa container tambahan, hemat RAM).
Jika uji beban menunjukkan masalah, buat implementasi lain (mis. Meilisearch)
dari SearchEngine tanpa mengubah consumer maupun endpoint.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from . import db

APPLIED = "applied"
IGNORED = "ignored"


class SearchEngine(ABC):
    """Semua metode tulis mengembalikan (hasil, user_id).

    hasil  : APPLIED atau IGNORED (event basi atau puisi sudah dihapus)
    user_id: pemilik puisi bila diketahui, untuk invalidasi cache; kalau tidak None
    """

    @abstractmethod
    def apply_content(
        self, *, poem_id: int, user_id: int, judul: str, isi: str, kategori: str, at: datetime
    ) -> tuple[str, int | None]:
        """poem.created / poem.updated."""

    @abstractmethod
    def apply_analysis(self, *, poem_id: int, tags: str, at: datetime) -> tuple[str, int | None]:
        """poem.analyzed. `tags` sudah dibersihkan, format 'a,b,c'."""

    @abstractmethod
    def apply_delete(self, *, poem_id: int) -> tuple[str, int | None]:
        """poem.deleted."""

    @abstractmethod
    def search(
        self,
        *,
        match: str | None,
        kategori: str,
        tag: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Kembalikan hingga `limit` baris, diurut relevansi.

        `match` None berarti tanpa kata kunci: hanya filter kategori/tag, urut terbaru."""


class MySQLEngine(SearchEngine):
    # ── Penulisan indeks ─────────────────────────────────────

    @staticmethod
    def _lock_row(cur, poem_id: int) -> dict:
        """Pastikan baris ada lalu kunci. INSERT IGNORE menangani balapan dua
        consumer yang memproses puisi yang sama (event datang tidak berurutan)."""
        cur.execute("INSERT IGNORE INTO search_poem (poem_id) VALUES (%s)", (poem_id,))
        cur.execute(
            "SELECT user_id, deleted, content_at, tags_at "
            "FROM search_poem WHERE poem_id = %s FOR UPDATE",
            (poem_id,),
        )
        return cur.fetchone()

    def apply_content(self, *, poem_id, user_id, judul, isi, kategori, at):
        with db.pool.connection() as conn:
            conn.begin()
            with conn.cursor() as cur:
                row = self._lock_row(cur, poem_id)
                if row["deleted"] or (row["content_at"] is not None and at < row["content_at"]):
                    conn.commit()
                    return IGNORED, row["user_id"]
                cur.execute(
                    "UPDATE search_poem SET user_id = %s, judul = %s, isi = %s, "
                    "kategori = %s, content_at = %s WHERE poem_id = %s",
                    (user_id, judul, isi, kategori, at, poem_id),
                )
            conn.commit()
        return APPLIED, user_id

    def apply_analysis(self, *, poem_id, tags, at):
        with db.pool.connection() as conn:
            conn.begin()
            with conn.cursor() as cur:
                row = self._lock_row(cur, poem_id)
                if row["deleted"] or (row["tags_at"] is not None and at < row["tags_at"]):
                    conn.commit()
                    return IGNORED, row["user_id"]
                cur.execute(
                    "UPDATE search_poem SET tags = %s, tags_at = %s WHERE poem_id = %s",
                    (tags, at, poem_id),
                )
            conn.commit()
        return APPLIED, row["user_id"]

    def apply_delete(self, *, poem_id):
        with db.pool.connection() as conn:
            conn.begin()
            with conn.cursor() as cur:
                row = self._lock_row(cur, poem_id)
                if row["deleted"]:
                    conn.commit()
                    return IGNORED, row["user_id"]
                # Tombstone: teks dikosongkan, baris dipertahankan agar event
                # lama yang terlambat tidak menghidupkan puisi ini lagi.
                cur.execute(
                    "UPDATE search_poem SET deleted = 1, judul = NULL, isi = NULL, "
                    "tags = '' WHERE poem_id = %s",
                    (poem_id,),
                )
            conn.commit()
        return APPLIED, row["user_id"]

    # ── Pencarian ────────────────────────────────────────────

    def search(self, *, match, kategori, tag, limit, offset):
        cols = "SELECT poem_id, user_id, judul, SUBSTRING(isi, 1, 1500) AS isi, kategori, tags, "
        if match:
            sql = (
                cols + "MATCH(judul, isi, tags) AGAINST (%s IN BOOLEAN MODE) AS score "
                "FROM search_poem WHERE deleted = 0 "
                "AND MATCH(judul, isi, tags) AGAINST (%s IN BOOLEAN MODE)"
            )
            params: list[Any] = [match, match]
        else:
            # Hanya filter tag/kategori (klik tag di frontend): urut dari yang terbaru.
            sql = cols + "0 AS score FROM search_poem WHERE deleted = 0 AND judul IS NOT NULL"
            params = []
        if kategori:
            sql += " AND kategori = %s"
            params.append(kategori)
        if tag:
            sql += " AND FIND_IN_SET(%s, tags) > 0"
            params.append(tag)
        sql += " ORDER BY score DESC, poem_id DESC LIMIT %s OFFSET %s"
        params += [limit, offset]

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()


engine: SearchEngine = MySQLEngine()
