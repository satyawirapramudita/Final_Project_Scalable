"""Kontrak event bersama (dibekukan di F3).

Pemilik: Satya (poem + nlp-worker).
Semua layanan lain mengimpor definisi ini agar schema tidak menyimpang.
Modul ini SENGAJA tanpa dependensi eksternal agar mudah di-vendor
ke tiap service.

Lihat docs/event-contracts.md untuk tabel lengkap.

Catatan: setiap service punya salinan modul ini di image-nya (di-COPY saat
build), jadi perubahan di sini butuh `docker compose up -d --build`.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Broker ────────────────────────────────────────────────────
EXCHANGE = "poem.events"
DLX = "poem.events.dlx"  # dead-letter exchange

# ── Routing keys ──────────────────────────────────────────────
POEM_CREATED = "poem.created"
POEM_UPDATED = "poem.updated"
POEM_DELETED = "poem.deleted"
POEM_ANALYZED = "poem.analyzed"

# ── Queue per konsumen ────────────────────────────────────────
QUEUE_NLP = "nlp.q"
QUEUE_POEM = "poem.q"
QUEUE_WORDCLOUD = "wordcloud.q"
QUEUE_KBBI = "kbbi.q"
QUEUE_SEARCH = "search.q"

# Bindings: routing key -> daftar queue yang berminat.
BINDINGS: dict[str, list[str]] = {
    POEM_CREATED: [QUEUE_NLP, QUEUE_SEARCH],
    POEM_UPDATED: [QUEUE_NLP, QUEUE_SEARCH],
    POEM_DELETED: [QUEUE_SEARCH, QUEUE_WORDCLOUD, QUEUE_KBBI],
    POEM_ANALYZED: [QUEUE_POEM, QUEUE_WORDCLOUD, QUEUE_KBBI, QUEUE_SEARCH],
}


def queue_bindings() -> dict[str, list[str]]:
    """Kembalikan peta: queue -> daftar routing key yang di-bind ke queue itu."""
    result: dict[str, list[str]] = {}
    for routing_key, queues in BINDINGS.items():
        for queue in queues:
            result.setdefault(queue, []).append(routing_key)
    return result


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventEnvelope:
    """Amplop standar untuk semua event."""

    event_name: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None
    occurred_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Visibilitas puisi ─────────────────────────────────────────
# Ditambahkan secara additive: event lama tanpa field ini berarti publik
# (semua puisi sebelum fitur ini memang publik).
VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
VISIBILITIES = (VISIBILITY_PUBLIC, VISIBILITY_PRIVATE)


def parse_visibility(value: Any) -> str:
    """Nilai visibilitas dari payload. Kosong -> publik (event lama);
    nilai tidak dikenal -> privat (gagal tertutup, jangan sampai bocor)."""
    if value is None or value == "":
        return VISIBILITY_PUBLIC
    value = str(value).strip().lower()
    return value if value in VISIBILITIES else VISIBILITY_PRIVATE


# ── Payload helper ────────────────────────────────────────────


def poem_created_payload(
    *,
    poem_id: int,
    user_id: int,
    judul: str,
    isi: str,
    kategori: str,
    visibilitas: str = VISIBILITY_PUBLIC,
) -> dict[str, Any]:
    return {
        "poem_id": poem_id,
        "user_id": user_id,
        "judul": judul,
        "isi": isi,
        "kategori": kategori,
        "visibilitas": visibilitas,
    }


def poem_deleted_payload(*, poem_id: int) -> dict[str, Any]:
    return {"poem_id": poem_id}


def poem_analyzed_payload(
    *,
    poem_id: int,
    tags: list[str],
    kata_puitif: list[str],
    terms: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "poem_id": poem_id,
        "tags": tags,
        "kata_puitif": kata_puitif,
        "terms": terms,  # [{"term": str, "count": int}, ...]
    }
