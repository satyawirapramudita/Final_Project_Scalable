"""init_schema search harus aman dijalankan lebih dari satu worker uvicorn."""

from __future__ import annotations

from pathlib import Path

from services.search.app import db


class FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, query: str, params: tuple = ()) -> None:
        self._executed.append(" ".join(query.split()))

    def fetchone(self) -> dict:
        return {"n": 1}  # kolom migrasi sudah ada -> tidak ada ALTER


class FakeConn:
    def __init__(self, executed: list[str]) -> None:
        self._executed = executed

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._executed)


class FakePool:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def connection(self) -> FakeConn:
        return FakeConn(self.executed)


def test_init_schema_runs_ddl_under_lock(monkeypatch, tmp_path: Path) -> None:
    fake = FakePool()
    lock = tmp_path / "schema.lock"
    monkeypatch.setattr(db, "pool", fake)
    monkeypatch.setattr(db, "_SCHEMA_LOCK", str(lock))

    db.init_schema(retries=1, delay=0)

    assert any("CREATE TABLE IF NOT EXISTS search_poem" in q for q in fake.executed)
    assert lock.exists(), "file lock inisialisasi skema harus dibuat"
