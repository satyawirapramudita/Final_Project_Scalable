"""Hashing password PBKDF2 — dipindah apa adanya dari Tugas 1 (server.py L42-61).

Format tersimpan: `salt:digest` (keduanya hex). Verifikasi memakai
hmac.compare_digest agar tahan timing attack.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return f"{salt}:{digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(":", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS
    ).hex()
    return hmac.compare_digest(candidate, digest)
