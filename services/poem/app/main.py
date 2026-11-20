"""poem service — CRUD puisi + publisher event + konsumen poem.analyzed.

Pemilik: Satya. Fase F2 (CRUD) + F3 (event).
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from shared import events, idempotency
from shared.metrics import REQUEST_SECONDS, route_path

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


Visibility = Literal["public", "private"]


class PoemCreate(BaseModel):
    judul: str
    isi: str
    kategori: str
    keyword: str | None = None
    bait: str | None = None
    gambar_file: str | None = None
    # Default publik: klien lama yang tidak mengirim field ini tetap berperilaku sama.
    visibilitas: Visibility = "public"


class PoemUpdate(BaseModel):
    judul: str | None = None
    isi: str | None = None
    kategori: str | None = None
    keyword: str | None = None
    bait: str | None = None
    gambar_file: str | None = None
    visibilitas: Visibility | None = None


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
        "visibilitas": events.parse_visibility(row.get("visibilitas")),
        "tags": tags,
    }


def _can_read(item: dict, user_id: int) -> bool:
    """Puisi publik boleh dibaca semua pengguna yang login; puisi privat hanya pemiliknya.
    Entri cache lama tanpa field visibilitas berasal dari sebelum fitur ini (= publik)."""
    if events.parse_visibility(item.get("visibilitas")) == events.VISIBILITY_PUBLIC:
        return True
    return int(item["user_id"]) == user_id


# ── Idempotency ───────────────────────────────────────────────
# Klien mengirim header Idempotency-Key pada POST/PUT/DELETE. Retry dengan
# key + payload yang sama mendapat respons pertama kembali (bukan duplikat);
# key dengan payload berbeda ditolak 409. Tanpa header, perilaku tidak berubah.


def _idem_begin(request: Request, user_id: int, payload: dict | None):
    """Klaim key. Kembalikan (key, fp) untuk _idem_finish, atau Response bila
    replay, atau raise 409 bila konflik. None = tanpa header, jalur biasa."""
    key = request.headers.get("Idempotency-Key")
    if not key:
        return None
    # Method + path ikut sidik jari: key yang sama untuk operasi lain (mis.
    # DELETE puisi B setelah DELETE puisi A) harus 409, bukan replay respons
    # operasi pertama yang membuat klien mengira puisi B ikut terhapus.
    fp = idempotency.fingerprint(
        {"method": request.method, "path": request.url.path, "body": payload}
    )
    outcome, stored = cache.idempotency().claim(user_id, key, fp)
    if outcome == idempotency.OUTCOME_REPLAY:
        return JSONResponse(
            status_code=int(stored["status"]),
            content=stored["body"],
            headers={"Idempotent-Replay": "true"},
        )
    if outcome == idempotency.OUTCOME_CONFLICT:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key sudah dipakai untuk request yang berbeda",
        )
    return (key, fp)


def _idem_finish(token, user_id: int, status: int, body: dict) -> None:
    """Simpan respons sukses; dipanggil setelah operasi DB benar-benar commit."""
    if token is not None:
        cache.idempotency().save(user_id, token[0], token[1], status, body)


def _idem_release(token, user_id: int) -> None:
    """Lepaskan klaim saat request gagal agar retry klien tidak menabrak konflik."""
    if token is not None:
        cache.idempotency().release(user_id, token[0])


def _load_item(poem_id: int) -> tuple[dict, bool]:
    """Detail puisi lewat cache-aside. Kembalikan (item, dari_cache).

    Cache menyimpan puisi apa adanya (termasuk yang privat); pemeriksaan akses
    selalu dilakukan SETELAH lookup, jadi isi cache tidak pernah bocor."""
    key = f"cache:puisi:{poem_id}"
    cached = cache.get(key)
    if cached is not None:
        return cached, True
    row = _get_row(poem_id)
    tags = _tags_for([poem_id]).get(poem_id, [])
    item = _serialize(row, tags)
    cache.set(key, item, config.CACHE_TTL_DETAIL)
    return item, False


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
    started = time.perf_counter()
    response = await call_next(request)
    # Template route (bukan URL mentah) agar label metrik tidak bertambah per id puisi.
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


@app.post("/api/poems", status_code=201)
def create_poem(body: PoemCreate, request: Request, user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    judul = body.judul.strip()
    isi = body.isi.strip()
    kategori = body.kategori.strip()
    keyword = _clean(body.keyword, 255)
    bait = _clean(body.bait, 500)
    gambar_file = _clean(body.gambar_file, 255)
    visibilitas = body.visibilitas
    _validate_fields(judul, isi, kategori, gambar_file)

    token = _idem_begin(request, user_id, body.model_dump())
    if isinstance(token, Response):
        return token

    try:
        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO puisi "
                    "(user_id, judul, tgl_submit, isi, bait, kategori, keyword, gambar_file, "
                    "status, versi, visibilitas) "
                    "VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, 'pending', 1, %s)",
                    (user_id, judul, isi, bait, kategori, keyword, gambar_file, visibilitas),
                )
                poem_id = cur.lastrowid
        finally:
            conn.close()
    except Exception:
        _idem_release(token, user_id)
        raise

    result = {"ok": True, "id_puisi": poem_id, "status": "pending", "visibilitas": visibilitas}
    # Respons disimpan sebelum publish/invalidasi: INSERT sudah commit (autocommit),
    # jadi retry harus mendapat replay, bukan duplikat.
    _idem_finish(token, user_id, 201, result)

    messaging.publish(
        events.POEM_CREATED,
        events.poem_created_payload(
            poem_id=poem_id,
            user_id=user_id,
            judul=judul,
            isi=isi,
            kategori=kategori,
            visibilitas=visibilitas,
        ),
        request.headers.get("X-Request-ID"),
    )
    _invalidate(user_id, poem_id)
    return result


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
    # Puisi publik dapat dibaca semua pengguna yang login; puisi privat hanya
    # pemiliknya. Pengguna lain mendapat 404 (bukan 403) agar keberadaan puisi
    # privat tidak terungkap. Mengubah dan menghapus tetap hanya untuk pemilik.
    item, from_cache = _load_item(poem_id)
    if not _can_read(item, int(user["sub"])):
        raise HTTPException(status_code=404, detail="Puisi tidak ditemukan")
    if from_cache:
        return {"ok": True, "puisi": item, "cached": True}
    return {"ok": True, "puisi": item}


@app.get("/internal/poems/{poem_id}/access", status_code=204)
def check_access(poem_id: int, user: dict = Depends(current_user)) -> Response:
    """Dipakai gateway (auth_request) sebelum meneruskan /api/wordcloud/{id} dan
    /api/kbbi/{id}, sehingga data turunan puisi privat ikut terlindungi tanpa
    mengubah layanan wordcloud/kbbi-linker.

    204 = boleh. 403 = tidak boleh atau puisi tidak ada (gateway mengubahnya
    menjadi 404). auth_request hanya mengenali 2xx/401/403; kode lain dianggap error.
    Path /internal/ diblokir gateway untuk klien luar."""
    try:
        item, _ = _load_item(poem_id)
    except HTTPException:
        return Response(status_code=403)
    if not _can_read(item, int(user["sub"])):
        return Response(status_code=403)
    return Response(status_code=204)


@app.put("/api/poems/{poem_id}")
def update_poem(
    poem_id: int, body: PoemUpdate, request: Request, user: dict = Depends(current_user)
) -> dict:
    user_id = int(user["sub"])
    # Klaim sebelum pemeriksaan row: retry replay harus mencapai cek key
    # walau puisi sudah berubah/hapus; kegagalan (404/403/400) melepas key.
    token = _idem_begin(request, user_id, body.model_dump())
    if isinstance(token, Response):
        return token

    try:
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
        visibilitas = fields.get("visibilitas") or events.parse_visibility(row.get("visibilitas"))
        _validate_fields(judul, isi, kategori, gambar_file)

        conn = db.connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE puisi SET judul=%s, isi=%s, bait=%s, kategori=%s, keyword=%s, "
                    "gambar_file=%s, visibilitas=%s, status='pending', versi=versi+1, "
                    "updated_at=NOW() WHERE id=%s",
                    (judul, isi, bait, kategori, keyword, gambar_file, visibilitas, poem_id),
                )
        finally:
            conn.close()
    except Exception:
        _idem_release(token, user_id)
        raise

    result = {"ok": True, "id_puisi": poem_id, "status": "pending", "visibilitas": visibilitas}
    _idem_finish(token, user_id, 200, result)

    # Hapus cache SEBELUM publish: begitu puisi dijadikan privat, gateway
    # (pemeriksaan akses wordcloud/kbbi) tidak boleh lagi membaca versi publik dari cache.
    _invalidate(user_id, poem_id)
    messaging.publish(
        events.POEM_UPDATED,
        events.poem_created_payload(
            poem_id=poem_id,
            user_id=user_id,
            judul=judul,
            isi=isi,
            kategori=kategori,
            visibilitas=visibilitas,
        ),
        request.headers.get("X-Request-ID"),
    )
    return {"ok": True, "id_puisi": poem_id, "status": "pending", "visibilitas": visibilitas}


@app.delete("/api/poems/{poem_id}")
def delete_poem(poem_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    user_id = int(user["sub"])
    # Klaim sebelum _get_row: retry setelah puisi terhapus harus tetap
    # mendapat replay 200, bukan 404. Pemeriksaan gagal melepas key.
    token = _idem_begin(request, user_id, None)
    if isinstance(token, Response):
        return token

    try:
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
    except Exception:
        _idem_release(token, user_id)
        raise

    result = {"ok": True, "message": "Puisi dihapus"}
    _idem_finish(token, user_id, 200, result)

    messaging.publish(
        events.POEM_DELETED,
        events.poem_deleted_payload(poem_id=poem_id),
        request.headers.get("X-Request-ID"),
    )
    _invalidate(user_id, poem_id)
    return result
