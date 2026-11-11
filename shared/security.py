"""Helper JWT bersama (HS256).

Semua layanan memverifikasi token secara lokal memakai secret yang sama
(env `JWT_SECRET`) tanpa memanggil auth service.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import jwt


def _secret() -> str:
    return os.getenv("JWT_SECRET", "")


def _algorithm() -> str:
    return os.getenv("JWT_ALGORITHM", "HS256")


def _expire_minutes() -> int:
    try:
        return int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    except ValueError:
        return 60


def create_token(
    *,
    user_id: int,
    username: str,
    nama: str | None = None,
    expires_minutes: int | None = None,
) -> str:
    """Terbitkan access token. Dipakai auth service; berguna juga untuk uji."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=expires_minutes or _expire_minutes())
    payload = {
        "sub": str(user_id),
        "username": username,
        "nama": nama or username,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(payload, _secret(), algorithm=_algorithm())


def decode_token(token: str) -> dict:
    """Verifikasi & dekode token. Melempar exception bila tidak valid/kedaluwarsa."""
    return jwt.decode(token, _secret(), algorithms=[_algorithm()])
