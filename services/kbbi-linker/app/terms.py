from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


_SPACE_RE = re.compile(r"\s+")
MAX_TERM_LENGTH = 100


def normalize_term(value: str) -> str:
    """Return the stable lookup form used by MySQL and Redis keys."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return _SPACE_RE.sub(" ", normalized).strip().casefold()[:MAX_TERM_LENGTH]


def unique_terms(values: Iterable[Any], *, limit: int) -> list[tuple[str, str]]:
    """Deduplicate terms while retaining the first display spelling."""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            continue
        display = _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", raw)).strip()
        normalized = normalize_term(display)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((display[:MAX_TERM_LENGTH], normalized))
        if len(result) >= limit:
            break
    return result


def assemble_items(
    relations: Iterable[Mapping[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join poem-term relations with dictionary entries without duplicating storage."""
    items: list[dict[str, Any]] = []
    for relation in relations:
        normalized = str(relation["normalized_term"])
        entry = entries.get(normalized)
        items.append(
            {
                "kata_asli": relation["display_term"],
                "normalized_term": normalized,
                "found": entry is not None,
                "bentuk_baku": entry.get("bentuk_baku") if entry else None,
                "kelas_kata": entry.get("kelas_kata") if entry else None,
                "definisi": list(entry.get("definisi") or []) if entry else [],
            }
        )
    return items
