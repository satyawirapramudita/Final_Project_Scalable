from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import pymysql
from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

from shared.metrics import REQUEST_SECONDS, route_path

from . import config, consumer, db
from .cache import cache
from .db import repository
from .logging_config import configure_logging
from .security import current_user
from .terms import assemble_items

configure_logging()
log = logging.getLogger("kbbi")

REQUEST_COUNT = Counter(
    "http_requests_total", "Total HTTP requests", ["service", "method", "path", "status"]
)
CACHE_RESULT = Counter("kbbi_cache_total", "Hasil cache kbbi", ["layer", "result"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    consumer.start()
    yield


app = FastAPI(title="puisi kbbi-linker service", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        REQUEST_COUNT.labels(config.SERVICE_NAME, request.method, route_path(request), 500).inc()
        raise
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
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/kbbi/{poem_id}")
def get_poem_definitions(
    poem_id: int = Path(gt=0), _user: dict = Depends(current_user)
) -> dict:
    poem = cache.get_poem(poem_id)
    poem_cached = poem is not None
    CACHE_RESULT.labels("poem", "hit" if poem_cached else "miss").inc()
    try:
        if poem is None:
            poem = repository.get_poem(poem_id)
            if poem is not None:
                cache.store_poem(poem_id, poem)
        if poem is None:
            return {
                "ok": True,
                "poem_id": poem_id,
                "status": "pending",
                "cached": False,
                "dataset_version": config.KBBI_DATASET_VERSION,
                "items": [],
            }
        if poem.get("deleted"):
            raise HTTPException(status_code=404, detail="Puisi tidak ditemukan")

        relations = list(poem.get("terms") or [])
        normalized = [str(item["normalized_term"]) for item in relations]
        entries, unresolved = cache.get_entries(normalized)
        if unresolved:
            from_db = repository.get_entries(unresolved)
            cache.store_entries(unresolved, from_db)
            entries.update(from_db)
        return {
            "ok": True,
            "poem_id": poem_id,
            "status": poem.get("status", "ready"),
            "cached": poem_cached and not unresolved,
            "dataset_version": config.KBBI_DATASET_VERSION,
            "items": assemble_items(relations, entries),
        }
    except db.Unavailable:
        raise HTTPException(status_code=503, detail="Layanan KBBI sedang sibuk") from None
    except pymysql.MySQLError:
        log.exception("Query KBBI gagal")
        raise HTTPException(status_code=503, detail="Layanan KBBI tidak tersedia") from None
