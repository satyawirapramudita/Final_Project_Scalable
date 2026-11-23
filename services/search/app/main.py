"""search service — indeks dan pencarian puisi. Pemilik: Anders. Fase F4.

    GET /api/search?q=&kategori=&tag=&page=&limit=

Indeks dipelihara dari event poem.* (queue search.q), bukan dari database
poem. Pencarian mencakup SEMUA puisi (bukan hanya milik pengguna); JWT tetap
wajib. Hasilnya di-cache di Redis dengan satu versi global.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from contextlib import asynccontextmanager

import pymysql
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    generate_latest,
    multiprocess,
)

from shared.metrics import REQUEST_SECONDS, route_path

from . import cache, config, consumer, db
from .breaker import BreakerOpen, CircuitBreaker
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
CACHE_RESULT = Counter("search_cache_total", "Hasil lookup cache pencarian", ["result"])


def _mysql_is_sick(exc: BaseException) -> bool:
    """Hanya kegagalan yang menandakan MySQL sakit yang membuka breaker.
    PoolTimeout (replika ini sendiri sedang ramai) dan bug SQL (ProgrammingError)
    tidak dihitung, agar breaker tidak menolak request saat MySQL sebenarnya sehat."""
    if isinstance(exc, db.PoolTimeout):
        return False
    return isinstance(
        exc, (db.DBDown, pymysql.err.OperationalError, pymysql.err.InterfaceError, OSError)
    )


search_breaker = CircuitBreaker(
    "search_mysql",
    failure_threshold=config.BREAKER_FAILURES,
    reset_timeout=config.BREAKER_RESET_SECONDS,
    counts_as_failure=_mysql_is_sick,
)


def _stale_or_503(digest: str, detail: str) -> dict:
    """Degradasi halus: saat MySQL tidak bisa dipakai, sajikan salinan cadangan
    (mungkin usang) bila ada; kalau tidak, 503."""
    stale = cache.lookup_stale(digest)
    if stale is not None:
        CACHE_RESULT.labels("stale").inc()
        return {**stale, "cached": True, "stale": True}
    raise HTTPException(status_code=503, detail=detail)


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
    path = route_path(request)
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
    """Metrik Prometheus.

    Bila container dijalankan dengan lebih dari satu worker uvicorn
    (UVICORN_WORKERS>1), setiap worker punya registry sendiri. MultiProcessCollector
    menggabungkan nilai dari semua worker lewat direktori PROMETHEUS_MULTIPROC_DIR,
    jadi angka yang di-scrape tidak bergantung pada worker mana yang menjawab.
    """
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/search")
def search(
    q: str = Query(""),
    kategori: str = Query(""),
    tag: str = Query(""),
    page: int = Query(1),
    limit: int = Query(config.DEFAULT_LIMIT),
    user: dict = Depends(current_user),
) -> dict:
    # Login wajib. Hasil: semua puisi publik + puisi privat milik pengguna ini.
    try:
        viewer_id = int(user["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Token tidak valid")
    tokens = tokenize(q)
    kategori = kategori.strip()[:50]
    tag = clean_tag(tag)
    # Tanpa kata kunci tetap boleh bila ada filter tag/kategori (klik tag di frontend).
    if not tokens and not (kategori or tag):
        raise HTTPException(status_code=400, detail="Kata kunci pencarian minimal 2 karakter")
    # Batas atas melindungi database: satu request tidak boleh menarik terlalu banyak.
    limit = min(max(limit, 1), config.MAX_LIMIT)
    page = min(max(page, 1), config.MAX_PAGE)

    # viewer_id ikut kunci cache: hasil berbeda per pengguna karena puisi privat
    # milik sendiri ikut tampil. Tanpa ini, hasil berisi puisi privat seseorang
    # bisa tersaji dari cache ke pengguna lain.
    digest = hashlib.sha1(
        "|".join(
            [str(viewer_id), " ".join(tokens), kategori.lower(), tag, str(page), str(limit)]
        ).encode("utf-8")
    ).hexdigest()

    cached, version = cache.lookup(digest)
    if cached is not None:
        CACHE_RESULT.labels("hit").inc()
        return {**cached, "cached": True}
    CACHE_RESULT.labels("miss").inc()

    try:
        # limit + 1 baris cukup untuk tahu ada halaman berikutnya tanpa COUNT(*) kedua.
        rows = search_breaker.call(
            lambda: engine.search(
                match=match_expression(tokens) if tokens else None,
                kategori=kategori,
                tag=tag,
                viewer_id=viewer_id,
                limit=limit + 1,
                offset=(page - 1) * limit,
            )
        )
    except BreakerOpen:
        # MySQL dianggap sakit: tidak dicoba sama sekali, jawab seketika
        return _stale_or_503(digest, "Pencarian sementara tidak tersedia, coba lagi sebentar")
    except db.Unavailable:
        # kolam koneksi penuh, atau database tidak terjangkau: gagal cepat
        return _stale_or_503(digest, "Pencarian sedang sibuk, coba lagi sebentar")
    except pymysql.MySQLError:
        log.exception("Query pencarian gagal")
        return _stale_or_503(digest, "Pencarian tidak tersedia")

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
                "visibilitas": row["visibilitas"],
                "score": round(float(row["score"]), 4),
            }
            for row in rows[:limit]
        ],
    }
    cache.store(digest, version, result)
    return {**result, "cached": False}
