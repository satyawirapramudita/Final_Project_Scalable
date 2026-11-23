"""Fixture bersama untuk uji poem service.

Mengikuti pola services/auth/tests: MySQL diganti stub in-memory yang meniru
bentuk query yang dipakai poem, RabbitMQ di-stub, dan Redis diganti stub
falsify, sehingga seluruh endpoint dapat diuji tanpa container.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Akar repo agar "shared" dan "services.poem.app" dapat diimport.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

os.environ["JWT_SECRET"] = "test_secret_panjang_acak_minimal_32_karakter"
os.environ.setdefault("JWT_ALGORITHM", "HS256")


class FakeCursor:
    """Stub cursor yang meniru query yang benar-benar dipakai poem service.

    Query di luar daftar ini gagal uji — sengaja, agar stub tidak melewati
    query yang salah tulis.
    """

    def __init__(self, store) -> None:
        self._store = store
        self._rows: list[dict] = []
        self.lastrowid = 0

    def execute(self, query: str, params: tuple = ()) -> None:
        q = " ".join(query.split())
        if q.startswith("CREATE TABLE") or q.startswith("ALTER TABLE"):
            return
        if q.startswith("SELECT COUNT(*) AS n FROM information_schema.COLUMNS"):
            self._rows = [{"n": 1}]
        elif q.startswith("INSERT INTO puisi"):
            self._store["puisi"].append(
                {
                    "id": len(self._store["puisi"]) + 1,
                    "user_id": params[0],
                    "judul": params[1],
                    "isi": params[2],
                    "bait": params[3],
                    "kategori": params[4],
                    "keyword": params[5],
                    "gambar_file": params[6],
                    "status": "pending",
                    "versi": 1,
                    "visibilitas": params[7],
                    "tgl_submit": None,
                    "updated_at": None,
                }
            )
            self.lastrowid = len(self._store["puisi"])
        elif q.startswith("SELECT * FROM puisi WHERE id = %s"):
            self._rows = [r for r in self._store["puisi"] if r["id"] == params[0]]
        elif q.startswith("SELECT * FROM puisi WHERE user_id = %s"):
            self._rows = [dict(r) for r in self._store["puisi"] if r["user_id"] == params[0]]
        elif q.startswith("SELECT puisi_id, tag FROM puisi_tag"):
            self._rows = [
                {"puisi_id": r["puisi_id"], "tag": r["tag"]} for r in self._store["tag"]
            ]
        elif q.startswith("UPDATE puisi SET"):
            row = next((r for r in self._store["puisi"] if r["id"] == params[-1]), None)
            if row is not None:
                row.update(
                    judul=params[0], isi=params[1], bait=params[2], kategori=params[3],
                    keyword=params[4], gambar_file=params[5], visibilitas=params[6],
                    versi=row["versi"] + 1, status="pending",
                )
        elif q.startswith("DELETE FROM puisi_tag"):
            self._store["tag"] = [t for t in self._store["tag"] if t["puisi_id"] != params[0]]
        elif q.startswith("DELETE FROM puisi"):
            self._store["puisi"] = [r for r in self._store["puisi"] if r["id"] != params[0]]
        elif q.startswith("SET TRANSACTION ISOLATION LEVEL"):
            self._store["isolation"].append(q.rsplit("LEVEL ", 1)[1])
        elif q.startswith("INSERT INTO outbox"):
            self._store["outbox"].append(
                {
                    "id": len(self._store["outbox"]) + 1,
                    "event_id": params[0],
                    "routing_key": params[1],
                    "envelope": params[2],
                    "published_at": None,
                    "attempts": 0,
                    "last_error": None,
                }
            )
        elif q.startswith("SELECT id, event_id, routing_key, envelope FROM outbox"):
            assert "FOR UPDATE SKIP LOCKED" in q
            pending = [r for r in self._store["outbox"] if r["published_at"] is None]
            self._rows = [dict(r) for r in pending[: params[0]]]
        elif q.startswith("UPDATE outbox SET published_at"):
            for r in self._store["outbox"]:
                if r["id"] in params:
                    r["published_at"] = "now"
        elif q.startswith("UPDATE outbox SET attempts"):
            for r in self._store["outbox"]:
                if r["id"] == params[1]:
                    r["attempts"] += 1
                    r["last_error"] = params[0]
        else:
            raise AssertionError(f"Query tidak dikenal stub: {q}")

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return list(self._rows)

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args) -> bool:
        return False


class FakeConnection:
    """Meniru transaksi: begin() memotret tabel, rollback() mengembalikannya."""

    TABLES = ("puisi", "tag", "outbox")

    def __init__(self, store) -> None:
        self._store = store
        self._snapshot: dict | None = None

    def cursor(self, *args, **kwargs) -> FakeCursor:
        return FakeCursor(self._store)

    def begin(self) -> None:
        self._snapshot = {t: copy.deepcopy(self._store[t]) for t in self.TABLES}
        self._store["tx"].append("begin")

    def commit(self) -> None:
        self._snapshot = None
        self._store["tx"].append("commit")

    def rollback(self) -> None:
        if self._snapshot is not None:
            self._store.update(self._snapshot)
            self._snapshot = None
        self._store["tx"].append("rollback")

    def close(self) -> None:
        pass


class FakeRedis:
    """Stub redis client minimal (get/set nx/delete) untuk idempotency & cache."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, *keys: str) -> int:
        n = 0
        for k in keys:
            n += 1 if self.data.pop(k, None) is not None else 0
        return n

    def scan_iter(self, match: str = "", count: int = 100):
        yield from list(self.data)

    def ping(self) -> bool:
        return True


@pytest.fixture
def store() -> dict:
    return {"puisi": [], "tag": [], "outbox": [], "tx": [], "isolation": []}


@pytest.fixture
def fake_db(monkeypatch, store) -> dict:
    from services.poem.app import db as db_mod

    monkeypatch.setattr(db_mod, "connect", lambda: FakeConnection(store))
    return store


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    """Pasang FakeRedis pada cache poem (client + breaker stub)."""
    from services.poem.app import cache as cache_mod

    client = FakeRedis()
    breaker = SimpleNamespace(
        available=True, run=lambda fn, default: fn()  # gaya RedisBreaker.run
    )
    monkeypatch.setattr(cache_mod, "_client", client)
    monkeypatch.setattr(cache_mod, "_breaker", breaker)
    return client


@pytest.fixture(autouse=True)
def no_relay(monkeypatch):
    """Relay outbox (thread RabbitMQ) tidak boleh berjalan saat uji; event
    cukup diperiksa di tabel outbox stub, relay diuji langsung lewat relay_once."""
    monkeypatch.setattr("services.poem.app.outbox.start_relay", lambda: None)


@pytest.fixture
def auth_header() -> dict:
    """JWT untuk user 1; decode_token diverifikasi lokal oleh poem service."""
    from shared.security import create_token

    token = create_token(user_id=1, username="ardi", nama="Ardi Pratama")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_header_user2() -> dict:
    from shared.security import create_token

    token = create_token(user_id=2, username="budi", nama="Budi")
    return {"Authorization": f"Bearer {token}"}


def _import_main_fresh(module_name: str):
    """Import modul main dengan registry Prometheus yang bersih dari duplikat.

    auth dan poem masing-masing mendefinisikan Counter bernama sama
    (http_requests_total); di produksi keduanya proses terpisah sehingga tidak
    bentrok, tetapi dalam satu proses pytest registrasi kedua ditolak. Bila
    registrasi gagal, collector duplikat dari service lain dilepas lalu modul
    di-import ulang agar metrik milik service ini yang terdaftar.
    """
    import importlib

    from prometheus_client import REGISTRY

    try:
        return importlib.import_module(module_name)
    except ValueError:
        for name in ("http_requests_total", "http_requests_created"):
            collector = REGISTRY._names_to_collectors.get(name)
            if collector is not None:
                REGISTRY.unregister(collector)
        return importlib.import_module(module_name)


@pytest.fixture
def client(fake_db, fake_redis):
    from fastapi.testclient import TestClient

    main = _import_main_fresh("services.poem.app.main")

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def submitted_poem(client, auth_header) -> int:
    """Satu puisi milik user 1, tanpa idempotency key."""
    resp = client.post(
        "/api/poems",
        json={"judul": "Puisi Awal", "isi": "Isi puisi", "kategori": "alam"},
        headers=auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id_puisi"]
