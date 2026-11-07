"""Pipeline NLP ringan untuk bahasa Indonesia (tanpa dependensi eksternal).

Fungsi: tokenisasi, stopword removal, ekstraksi keyword/tag, dan
identifikasi Kata Puitif. Keputusan pustaka NLP masih terbuka; implementasi
ini sengaja self-contained agar mudah diganti nanti.
"""

from __future__ import annotations

import re
from collections import Counter

WORD_RE = re.compile(r"[A-Za-z]+")

# Stopword umum bahasa Indonesia (ringkas, dapat diperluas).
STOPWORDS = {
    "yang", "dan", "di", "ke", "dari", "ini", "itu", "dengan", "untuk", "pada",
    "adalah", "akan", "atau", "juga", "saya", "kamu", "dia", "kami", "kita",
    "mereka", "tidak", "tak", "bukan", "sudah", "telah", "sedang", "masih",
    "bisa", "dapat", "harus", "mau", "ingin", "ada", "tidak", "ya", "kah",
    "lah", "pun", "se", "ter", "ber", "meng", "men", "mem", "di", "ke", "se",
    "dalam", "atas", "bawah", "sini", "sana", "situ", "mana", "apa", "siapa",
    "kapan", "dimana", "bagaimana", "mengapa", "karena", "sebab", "agar",
    "supaya", "jika", "kalau", "bila", "ketika", "saat", "lalu", "kemudian",
    "namun", "tetapi", "tapi", "melainkan", "sedangkan", "sementara", "oleh",
    "olehku", "olehmu", "padaku", "padamu", "kepadaku", "kepadamu", "akan",
    "ku", "kau", "nya", "mu", "para", "sang", "si", "suatu", "sebuah", "satu",
    "dua", "tiga", "semua", "setiap", "tiap", "banyak", "sedikit", "lagi",
    "hanya", "saja", "sangat", "amat", "paling", "lebih", "kurang", "begitu",
    "demikian", "maka", "hanyalah", "ialah", "yakni", "yaitu", "adapun",
}

# Leksikon kecil kata puitis (dapat diperluas / diganti pustaka NLP).
POETIC_WORDS = {
    "senja", "cakrawala", "rindu", "sunyi", "embun", "angin", "samudra",
    "gerimis", "gema", "pelangi", "petir", "kelam", "gulita", "fajar",
    "diksi", "bait", "larik", "pantun", "syahdu", "bening", "kalbu", "nestapa",
    "asmaraloka", "dermaga", "oase", "titik", "rona", "wangi", "luruh",
    "puspa", "swastamita", "mayapada", "bianglala", "nirwana", "elegi",
    "semerbak", "kelana", "perahu", "nelayan", "badai", "lembayung", "kabut",
}


def tokenize(text: str) -> list[str]:
    return [word.lower() for word in WORD_RE.findall(text or "")]


def analyze(judul: str, isi: str, kategori: str = "", keyword: str = "") -> tuple[list[str], list[str], list[dict]]:
    """Kembalikan (tags, kata_puitif, terms)."""
    tokens = tokenize(" ".join(filter(None, [judul, isi])))
    content = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    freq = Counter(content)

    terms = [{"term": term, "count": count} for term, count in freq.most_common(20)]

    # Tag: kategori + keyword dari user + kata teratas dari isi.
    tags: list[str] = []
    if kategori:
        tags.append(kategori.lower())
    if keyword:
        tags.extend(k.strip().lower() for k in re.split(r"[,\n;]+", keyword) if k.strip())
    tags.extend(term for term, _ in freq.most_common(5))
    tags = list(dict.fromkeys(t for t in tags if t))[:8]

    # Kata puitis: cocokkan leksikon; fallback kata panjang yang jarang.
    kata_puitif = [t for t in dict.fromkeys(content) if t in POETIC_WORDS]
    if len(kata_puitif) < 3:
        rare = [
            t for t, c in freq.items() if c == 1 and len(t) >= 7 and t not in kata_puitif
        ]
        kata_puitif.extend(rare[: 5 - len(kata_puitif)])
    kata_puitif = kata_puitif[:10]

    return tags, kata_puitif, terms
