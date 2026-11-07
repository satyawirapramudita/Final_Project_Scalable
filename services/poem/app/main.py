"""poem service — CRUD puisi + publisher event + konsumen poem.analyzed.

Pemilik: Satya. Fase F2 (CRUD) + F3 (event).
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from shared import events

from . import cache, config, db, messaging
from .security import current_user

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("poem")

GAMBAR_FILE_RE = re.compile(r"^[\w-][\w/-]*\.[\w]{1,10}$")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)


# ── Model ─────────────────────────────────────────────────────


class PoemCreate(BaseModel):
    judul: str
    isi: str
    kategori: str
    keyword: str | None = None
    bait: str | None = None
    gambar_file: str | None = None


class PoemUpdate(BaseModel):
    judul: str | None = None
    isi: str | None = None
    kategori: str | None = None
    keyword: str | None = None
    bait: str | None = None
    gambar_file: str | None = None


# ── Helper ────────────────────────────────────────────────────


def _clean(value: str | None, max_len: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:max_len] if value else None


def _validate_fields(judul: str, isi: str, kategori: str, gambar_file: str | None) -> None:
    if not (1 <= len(judul) <= 150):
        raise HTTPException(status_code=400, detail="Judul wajib 1-150 karakter")
    if not (1 <= len(isi) <= 10_000):
        raise HTTPException(status_code=400, detail="Isi wajib 1-10000 karakter")
    if not (1 <= len(kategori) <= 50):
        raise HTTPException(status_code=400, detail="Kategori wajib 1-50 karakter")
    if gambar_file and not GAMBAR_FILE_RE.fullmatch(gambar_file):
        raise HTTPException(status_code=400, detail="Format gambar_file tidak valid")


def _get_row(poem_id: int) -> dict:
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM puisi WHERE id = %s", (poem_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail="Puisi tidak ditemukan")
    return row


def _tags_for(poem_ids: list[int]) -> dict[int, list[str]]:
    if not poem_ids:
        return {}
    placeholders = ",".join(["%s"] * len(poem_ids))
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT puisi_id, tag FROM puisi_tag WHERE puisi_id IN ({placeholders}) ORDER BY tag",
                tuple(poem_ids),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row["puisi_id"], []).append(row["tag"])
    return result


def _serialize(row: dict, tags: list[str]) -> dict:
    gambar_file = row.get("gambar_file")
    gambar_url = (
        f"{config.ASSET_BASE_URL}/{gambar_file}"
        if config.ASSET_BASE_URL and gambar_file
        else ""
    )
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "judul": row["judul"],
        "isi": row["isi"],
        "bait": row["bait"],
        "kategori": row["kategori"],
        "keyword": row["keyword"],
        "gambar_file": gambar_file,
        "gambar_url": gambar_url,
        "status": row["status"],
        "versi": row["versi"],
        "tgl_submit": row["tgl_submit"].isoformat() if row.get("tgl_submit") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "tags": tags,
    }


def _invalidate(user_id: int, poem_id: int | None = None) -> None:
    keys = [f"cache:daftar:{user_id}:1"]
    if poem_id is not None:
        keys.append(f"cache:puisi:{poem_id}")
    cache.delete(*keys)


# ── Konsumen poem.analyzed ────────────────────────────────────


def handle_analyzed(data: dict) -> None:
    payload = data.get("payload") or {}
    poem_id = payload.get("poem_id")
    if poem_id is None:
        log.warning("poem.analyzed tanpa poem_id, diabaikan")
        return
    poem_id = int(poem_id)
    tags = payload.get("tags") or []

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM puisi WHERE id = %s", (poem_id,))
            row = cur.fetchone()
            if row is None:
                log.info("poem.analyzed untuk puisi %s yang sudah tidak ada", poem_id)
                return
            cur.execute("DELETE FROM puisi_tag WHERE puisi_id = %s", (poem_id,))
            for tag in tags:
                tag = str(tag).strip()[:60]
                if tag:
                    cur.execute(
                        "INSERT INTO puisi_tag (puisi_id, tag) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE tag = tag",
                        (poem_id, tag),
                    )
            cur.execute("UPDATE puisi SET status = 'done' WHERE id = %s", (poem_id,))
        user_id = row["user_id"]
    finally:
        conn.close()

    _invalidate(user_id, poem_id)
    log.info("poem %s dianalisis: %s tag", poem_id, len(tags))


# ── App ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    messaging.start_analyzed_consumer(handle_analyzed)
    yield


app = FastAPI(title="puisi poem service", version="0.2.0", lifespan=lifespan)


@app.middleware("http")
async def count_requests(request: Request, call_next):
    response = await call_next(request)
    REQUEST_COUNT.labels(config.SERVICE_NAME, request.method, request.url.path, response.status_code).inc()
    return response


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": config.SERVICE_NAME}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/poems", status_code=201)
def create_poem(body: PoemCreate, request: Request, user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    judul = body.judul.strip()
    isi = body.isi.strip()
    kategori = body.kategori.strip()
    keyword = _clean(body.keyword, 255)
    bait = _clean(body.bait, 500)
    gambar_file = _clean(body.gambar_file, 255)
    _validate_fields(judul, isi, kategori, gambar_file)

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO puisi "
                "(user_id, judul, tgl_submit, isi, bait, kategori, keyword, gambar_file, status, versi) "
                "VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, 'pending', 1)",
                (user_id, judul, isi, bait, kategori, keyword, gambar_file),
            )
            poem_id = cur.lastrowid
    finally:
        conn.close()

    messaging.publish(
        events.POEM_CREATED,
        events.poem_created_payload(
            poem_id=poem_id, user_id=user_id, judul=judul, isi=isi, kategori=kategori
        ),
        request.headers.get("X-Request-ID"),
    )
    _invalidate(user_id, poem_id)
    return {"ok": True, "id_puisi": poem_id, "status": "pending"}


@app.get("/api/poems")
def list_poems(user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    key = f"cache:daftar:{user_id}:1"
    cached = cache.get(key)
    if cached is not None:
        return {"ok": True, "puisi": cached, "cached": True}

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM puisi WHERE user_id = %s ORDER BY tgl_submit DESC", (user_id,)
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    tags_map = _tags_for([row["id"] for row in rows])
    items = [_serialize(row, tags_map.get(row["id"], [])) for row in rows]
    cache.set(key, items, config.CACHE_TTL_LIST)
    return {"ok": True, "puisi": items}


@app.get("/api/poems/{poem_id}")
def get_poem(poem_id: int, user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    key = f"cache:puisi:{poem_id}"
    cached = cache.get(key)
    if cached is not None:
        if cached.get("user_id") != user_id:
            raise HTTPException(status_code=403, detail="Bukan pemilik puisi")
        return {"ok": True, "puisi": cached, "cached": True}

    row = _get_row(poem_id)
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Bukan pemilik puisi")
    tags = _tags_for([poem_id]).get(poem_id, [])
    item = _serialize(row, tags)
    cache.set(key, item, config.CACHE_TTL_DETAIL)
    return {"ok": True, "puisi": item}


@app.put("/api/poems/{poem_id}")
def update_poem(
    poem_id: int, body: PoemUpdate, request: Request, user: dict = Depends(current_user)
) -> dict:
    user_id = int(user["sub"])
    row = _get_row(poem_id)
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Bukan pemilik puisi")

    fields = body.model_dump(exclude_unset=True)
    judul = (fields.get("judul") or row["judul"]).strip()
    isi = (fields.get("isi") or row["isi"]).strip()
    kategori = (fields.get("kategori") or row["kategori"]).strip()
    keyword = _clean(fields.get("keyword", row["keyword"]), 255)
    bait = _clean(fields.get("bait", row["bait"]), 500)
    gambar_file = _clean(fields.get("gambar_file", row["gambar_file"]), 255)
    _validate_fields(judul, isi, kategori, gambar_file)

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE puisi SET judul=%s, isi=%s, bait=%s, kategori=%s, keyword=%s, "
                "gambar_file=%s, status='pending', versi=versi+1, updated_at=NOW() WHERE id=%s",
                (judul, isi, bait, kategori, keyword, gambar_file, poem_id),
            )
    finally:
        conn.close()

    messaging.publish(
        events.POEM_UPDATED,
        events.poem_created_payload(
            poem_id=poem_id, user_id=user_id, judul=judul, isi=isi, kategori=kategori
        ),
        request.headers.get("X-Request-ID"),
    )
    _invalidate(user_id, poem_id)
    return {"ok": True, "id_puisi": poem_id, "status": "pending"}


@app.delete("/api/poems/{poem_id}")
def delete_poem(poem_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    row = _get_row(poem_id)
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Bukan pemilik puisi")

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM puisi_tag WHERE puisi_id = %s", (poem_id,))
            cur.execute("DELETE FROM puisi WHERE id = %s", (poem_id,))
    finally:
        conn.close()

    messaging.publish(
        events.POEM_DELETED,
        events.poem_deleted_payload(poem_id=poem_id),
        request.headers.get("X-Request-ID"),
    )
    _invalidate(user_id, poem_id)
    return {"ok": True, "message": "Puisi dihapus"}
