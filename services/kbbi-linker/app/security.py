from __future__ import annotations

from fastapi import Header, HTTPException

from shared.security import decode_token


def current_user(authorization: str = Header(default="")) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Token tidak ada atau format salah")
    token = authorization.split(" ", 1)[1].strip()
    try:
        return decode_token(token)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Token tidak valid atau kedaluwarsa") from exc
