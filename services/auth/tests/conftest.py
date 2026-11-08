"""Fixture bersama untuk uji auth service.

MySQL diganti stub in-memory yang meniru persis tiga bentuk query yang
dipakai auth (CREATE TABLE no-op, SELECT by username, INSERT users), sehingga
seluruh endpoint dapat diuji tanpa container MySQL. Query di luar daftar itu
gagal uji — sengaja, agar stub tidak melewati query yang salah tulis.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Akar repo agar "shared" dan "services.auth.app" dapat diimport.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# Secret uji. shared.security membaca env saat dipanggil, bukan saat import,
# jadi nilai ini berlaku untuk seluruh sesi pytest.
os.environ["JWT_SECRET"] = "test_secret_panjang_acak_minimal_32_karakter"
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ["JWT_EXPIRE_MINUTES"] = "1440"  # 24 jam, sesuai keputusan F1


class FakeCursor:
    def __init__(self, store, fail_insert: bool = False) -> None:
        self._store = store
        self._fail_insert = fail_insert
        self._rows: list[dict] = []

    def execute(self, query: str, params: tuple = ()) -> None:
        q = " ".join(query.split())
        if q.startswith("CREATE TABLE"):
            return
        if q.startswith("SELECT id FROM users"):
            self._rows = [
                {"id": r["id"]} for r in self._store if r["username"] == params[0]
            ]
        elif q.startswith("SELECT id, username, password, nama FROM users"):
            self._rows = [dict(r) for r in self._store if r["username"] == params[0]]
        elif q.startswith("INSERT INTO users"):
            if self._fail_insert:
                import pymysql

                raise pymysql.err.IntegrityError(1062, "Duplicate entry (simulasi)")
            self._store.append(
                {
                    "id": len(self._store) + 1,
                    "username": params[0],
                    "password": params[1],
                    "nama": params[2],
                }
            )
            self._rows = []
        else:
            raise AssertionError(f"Query tidak dikenal stub: {q}")

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args) -> bool:
        return False


class FakeConnection:
    def __init__(self, store, fail_insert: bool = False) -> None:
        self._store = store
        self._fail_insert = fail_insert

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._store, self._fail_insert)

    def close(self) -> None:
        pass


@pytest.fixture
def store() -> list[dict]:
    """Isi tabel users in-memory; segar untuk setiap test."""
    return []


@pytest.fixture
def fake_db(monkeypatch, store) -> list[dict]:
    """Ganti db.connect milik auth dengan stub in-memory."""
    from services.auth.app import db as db_mod

    monkeypatch.setattr(db_mod, "connect", lambda: FakeConnection(store))
    return store


@pytest.fixture
def fake_db_duplicate_race(monkeypatch, store) -> list[dict]:
    """Stub yang melempar IntegrityError saat INSERT (simulasi balapan unik)."""
    from services.auth.app import db as db_mod

    monkeypatch.setattr(
        db_mod, "connect", lambda: FakeConnection(store, fail_insert=True)
    )
    return store


@pytest.fixture
def client(fake_db):
    """TestClient dengan lifespan (init_schema jalan lewat stub)."""
    from fastapi.testclient import TestClient

    from services.auth.app import main

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def registered_user(client) -> dict:
    """User terdaftar: ardi / rahasia123."""
    resp = client.post(
        "/api/auth/register",
        json={"username": "ardi", "nama": "Ardi Pratama", "password": "rahasia123"},
    )
    assert resp.status_code == 201, resp.text
    return {"username": "ardi", "nama": "Ardi Pratama", "password": "rahasia123"}


@pytest.fixture
def auth_header(client, registered_user) -> dict:
    """Header Authorization dari user yang baru login."""
    resp = client.post(
        "/api/auth/login",
        json={"username": registered_user["username"], "password": registered_user["password"]},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}
