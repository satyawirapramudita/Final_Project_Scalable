"""Pembersihan query, tag, dan pembuatan snippet (tanpa dependensi eksternal)."""

from __future__ import annotations

import re

from . import config

# Huruf/angka Unicode saja. Operator boolean MySQL (+ - < > ( ) ~ * " @)
# dan underscore ikut terbuang, sehingga input pengguna tidak bisa mengubah
# struktur ekspresi MATCH ... AGAINST.
_TOKEN_RE = re.compile(r"[^\W_]+")

MAX_TOKEN_LEN = 30
MAX_TAG_LEN = 60
MAX_TAGS_TOTAL = 500  # sesuai kolom search_poem.tags


def tokenize(query: str) -> list[str]:
    """Pecah query menjadi token bersih: huruf kecil, unik, min. 2 karakter."""
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_RE.findall((query or "")[: config.MAX_QUERY_LEN].lower()):
        token = raw[:MAX_TOKEN_LEN]
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= config.MAX_QUERY_TOKENS:
            break
    return tokens


def match_expression(tokens: list[str]) -> str:
    """Ekspresi boolean mode: tiap token berawalan (prefix), hasil diurut relevansi.

    Tanpa operator +/-, dokumen yang cocok dengan salah satu token ikut
    muncul (OR) dan yang cocok dengan lebih banyak token mendapat skor lebih tinggi.
    """
    return " ".join(f"{token}*" for token in tokens)


def clean_tag(raw: str) -> str:
    """Satu tag: huruf kecil, tanpa koma/spasi berlebih."""
    return re.sub(r"\s+", " ", (raw or "").replace(",", " ")).strip().lower()[:MAX_TAG_LEN]


def join_tags(tags: list) -> str:
    """Gabungkan tag hasil analisis menjadi string 'a,b,c' (unik, terbatas panjangnya)."""
    seen: set[str] = set()
    parts: list[str] = []
    total = 0
    for item in tags or []:
        tag = clean_tag(str(item))
        if not tag or tag in seen:
            continue
        extra = len(tag) + (1 if parts else 0)
        if total + extra > MAX_TAGS_TOTAL:
            break
        seen.add(tag)
        parts.append(tag)
        total += extra
    return ",".join(parts)


def split_tags(stored: str | None) -> list[str]:
    return [tag for tag in (stored or "").split(",") if tag]


def make_snippet(text: str | None, tokens: list[str], width: int = 160) -> str:
    """Potongan isi puisi di sekitar kata yang cocok (atau awal teks)."""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= width:
        return flat
    lowered = flat.lower()
    hits = [i for i in (lowered.find(token) for token in tokens) if i >= 0]
    start = max(0, min(hits) - 60) if hits else 0
    end = min(len(flat), start + width)
    start = max(0, end - width)
    snippet = flat[start:end].strip()
    return ("..." if start > 0 else "") + snippet + ("..." if end < len(flat) else "")
