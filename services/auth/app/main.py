"""auth service — registrasi, login, dan penerbitan JWT (F1).

Pemilik: Ardi. Satu-satunya pemilik kredensial; semua layanan lain
memverifikasi token secara lokal via shared.security.decode_token.

Endpoint (kontrak respons mempertahankan gaya monolit {ok, message}):
    POST /api/auth/register -> 201 {ok, message} | 400 | 409
    POST /api/auth/login    -> 200 {ok, message, nama, username, token} | 401
    POST /api/auth/logout   -> 200 {ok, message} (stateless: token dibuang klien)
    GET  /api/auth/me       -> 200 {ok, username, nama} | 200 {ok:false, message}
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import pymysql
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from shared import security
from shared.metrics import REQUEST_SECONDS, route_path

from . import config, db
from .security import USERNAME_PATTERN, hash_password, verify_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("auth")

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)


# ── Model ─────────────────────────────────────────────────────


class RegisterBody(BaseModel):
    username: str
    nama: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


# ── Helper ────────────────────────────────────────────────────

# Batas atas agar request raksasa tidak membebani hashing PBKDF2.
MAX_PASSWORD_LENGTH = 128


def _validate_register(username: str, nama: str, password: str) -> None:
    if not USERNAME_PATTERN.fullmatch(username):
        raise HTTPException(
            status_code=400,
            detail="Username 3-50 karakter, hanya huruf, angka, _ . -",
        )
    if not (1 <= len(nama) <= 100):
        raise HTTPException(status_code=400, detail="Nama lengkap wajib diisi (max 100)")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password minimal 6 karakter")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password maksimal {MAX_PASSWORD_LENGTH} karakter"
        )


# ── App ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_schema()
    yield


app = FastAPI(title="puisi auth service", version="0.2.0", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def http_exception_to_monolith_format(request: Request, exc: HTTPException):
    """Kontrak respons tetap {ok, message} gaya monolit agar frontend tidak berubah.

    HTTPException FastAPI aslinya menghasilkan {"detail": ...}; handler ini
    membungkusnya menjadi {"ok": false, "message": ...} dengan status sama.
    """
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_to_monolith_format(request: Request, exc: RequestValidationError):
    """Body tidak lengkap/salah tipe -> 400 {ok:false, message} (bukan 422 {detail:[...]})."""
    from fastapi.responses import JSONResponse

    fields = sorted({str(err["loc"][-1]) for err in exc.errors() if err.get("loc")})
    message = "Data tidak lengkap atau tidak valid"
    if fields:
        message += ": " + ", ".join(fields)
    return JSONResponse(status_code=400, content={"ok": False, "message": message})


@app.middleware("http")
async def count_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    # Template route (bukan URL mentah) agar label metrik tidak bertambah per id.
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


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterBody) -> dict:
    username = body.username.strip()
    nama = body.nama.strip()
    _validate_register(username, nama, body.password)

    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="Username sudah terdaftar")
            cur.execute(
                "INSERT INTO users (username, password, nama) VALUES (%s, %s, %s)",
                (username, hash_password(body.password), nama),
            )
    except pymysql.err.IntegrityError:
        # Balapan antar request: unique constraint menang setelah cek SELECT.
        raise HTTPException(status_code=409, detail="Username sudah terdaftar")
    finally:
        conn.close()

    log.info("user %s terdaftar", username)
    return {"ok": True, "message": f"Akun {username} berhasil didaftarkan"}


@app.post("/api/auth/login")
def login(body: LoginBody) -> dict:
    username = body.username.strip()
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password, nama FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if row is None or not verify_password(body.password, row["password"]):
        raise HTTPException(status_code=401, detail="Username atau password salah")

    token = security.create_token(
        user_id=row["id"], username=row["username"], nama=row["nama"]
    )
    log.info("user %s login", username)
    return {
        "ok": True,
        "message": "Login berhasil",
        "nama": row["nama"],
        "username": row["username"],
        "token": token,
    }


@app.post("/api/auth/logout")
def logout() -> dict:
    # Stateless: tidak ada sesi di server yang perlu dihancurkan.
    # Klien membuang token; endpoint ini hanya formalitas kontrak lama.
    return {"ok": True, "message": "Logout berhasil"}


@app.get("/api/auth/me")
def me(request: Request) -> dict:
    authorization = request.headers.get("Authorization", "")
    if not authorization.lower().startswith("bearer "):
        return {"ok": False, "message": "Belum login"}
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = security.decode_token(token)
    except Exception:  # noqa: BLE001 — token rusak/kedaluwarsa bukan error HTTP
        return {"ok": False, "message": "Sesi tidak valid atau kedaluwarsa"}
    return {"ok": True, "username": claims["username"], "nama": claims.get("nama", "")}
