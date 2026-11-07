"""Kontrak event bersama (dibekukan di F3).

Pemilik: Satya (poem + nlp-worker).
Semua layanan lain mengimpor definisi ini agar schema tidak menyimpang.
Modul ini SENGAJA tanpa dependensi eksternal agar mudah di-vendor
ke tiap service.

Lihat docs/event-contracts.md untuk tabel lengkap.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# ── Broker ────────────────────────────────────────────────────
EXCHANGE = "poem.events"

# ── Routing keys ──────────────────────────────────────────────
POEM_CREATED = "poem.created"
POEM_UPDATED = "poem.updated"
POEM_DELETED = "poem.deleted"
POEM_ANALYZED = "poem.analyzed"

# ── Queue per konsumen ────────────────────────────────────────
QUEUE_NLP = "nlp.q"
QUEUE_POEM = "poem.q"
QUEUE_WORDCLOUD = "wordcloud.q"
QUEUE_WIKI = "wiki.q"
QUEUE_SEARCH = "search.q"
QUEUE_TTS = "tts.q"

# Bindings: routing key -> daftar queue yang berminat
BINDINGS: dict[str, list[str]] = {
    POEM_CREATED: [QUEUE_NLP, QUEUE_SEARCH, QUEUE_TTS],
    POEM_UPDATED: [QUEUE_NLP, QUEUE_SEARCH, QUEUE_TTS],
    POEM_DELETED: [QUEUE_SEARCH, QUEUE_WORDCLOUD, QUEUE_WIKI],
    POEM_ANALYZED: [QUEUE_POEM, QUEUE_WORDCLOUD, QUEUE_WIKI, QUEUE_SEARCH],
}


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


# ── Payload helper ────────────────────────────────────────────


def poem_created_payload(
    *,
    poem_id: int,
    user_id: int,
    judul: str,
    isi: str,
    kategori: str,
) -> dict[str, Any]:
    return {
        "poem_id": poem_id,
        "user_id": user_id,
        "judul": judul,
        "isi": isi,
        "kategori": kategori,
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
