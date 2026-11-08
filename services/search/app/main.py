"""search service — indeks dan pencarian puisi. Pemilik: Anders. Fase F4.

    GET /api/search?q=&kategori=&tag=&page=&limit=

Indeks dipelihara dari event poem.* (queue search.q), bukan dari database
poem. Pencarian mencakup SEMUA puisi (bukan hanya milik pengguna); JWT tetap
wajib. Hasilnya di-cache di Redis dengan satu versi global.
"""

from __future__ import annotations

import hashlib
import logging
import time
from contextlib import asynccontextmanager

import pymysql
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from . import cache, config, consumer, db
from .engine import engine
from .security import current_user
from .textutil import clean_tag, make_snippet, match_expression, split_tags, tokenize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("search")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)
REQUEST_SECONDS = Histogram(
    "http_request_duration_seconds",
    "Latensi request HTTP",
    ["service", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
CACHE_RESULT = Counter("search_cache_total", "Hasil lookup cache pencarian", ["result"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    consumer.start()
    yield


app = FastAPI(title="puisi search service", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    # Pakai template route (bukan URL mentah) agar label metrik tidak meledak.
    route = request.scope.get("route")
    path = getattr(route, "path", "unmatched")
    REQUEST_COUNT.labels(config.SERVICE_NAME, request.method, path, response.status_code).inc()
    REQUEST_SECONDS.labels(config.SERVICE_NAME, request.method, path).observe(
        time.perf_counter() - started
    )
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/search")
def search(
    q: str = Query(""),
    kategori: str = Query(""),
    tag: str = Query(""),
    page: int = Query(1),
    limit: int = Query(config.DEFAULT_LIMIT),
    _user: dict = Depends(current_user),
) -> dict:
    # Login wajib, tetapi hasil mencakup semua puisi (bukan hanya milik pengguna).
    tokens = tokenize(q)
    if not tokens:
        raise HTTPException(status_code=400, detail="Kata kunci pencarian minimal 2 karakter")
    kategori = kategori.strip()[:50]
    tag = clean_tag(tag)
    # Batas atas melindungi database: satu request tidak boleh menarik terlalu banyak.
    limit = min(max(limit, 1), config.MAX_LIMIT)
    page = min(max(page, 1), config.MAX_PAGE)

    digest = hashlib.sha1(
        "|".join([" ".join(tokens), kategori.lower(), tag, str(page), str(limit)]).encode("utf-8")
    ).hexdigest()

    cached, version = cache.lookup(digest)
    if cached is not None:
        CACHE_RESULT.labels("hit").inc()
        return {**cached, "cached": True}
    CACHE_RESULT.labels("miss").inc()

    try:
        # limit + 1 baris cukup untuk tahu ada halaman berikutnya tanpa COUNT(*) kedua.
        rows = engine.search(
            match=match_expression(tokens),
            kategori=kategori,
            tag=tag,
            limit=limit + 1,
            offset=(page - 1) * limit,
        )
    except db.Unavailable:
        # kolam koneksi penuh, atau database tidak terjangkau: gagal cepat
        raise HTTPException(status_code=503, detail="Pencarian sedang sibuk, coba lagi sebentar")
    except pymysql.MySQLError:
        log.exception("Query pencarian gagal")
        raise HTTPException(status_code=503, detail="Pencarian tidak tersedia")

    has_more = len(rows) > limit
    result = {
        "ok": True,
        "q": " ".join(tokens),
        "page": page,
        "limit": limit,
        "has_more": has_more,
        "items": [
            {
                "id": row["poem_id"],
                "user_id": row["user_id"],
                "judul": row["judul"],
                "snippet": make_snippet(row["isi"], tokens),
                "kategori": row["kategori"],
                "tags": split_tags(row["tags"]),
                "score": round(float(row["score"]), 4),
            }
            for row in rows[:limit]
        ],
    }
    cache.store(digest, version, result)
    return {**result, "cached": False}
