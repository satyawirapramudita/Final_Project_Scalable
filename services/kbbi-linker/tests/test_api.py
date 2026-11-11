from __future__ import annotations

from fastapi.testclient import TestClient

from app import main
from app.security import current_user
import pytest


class FakeCache:
    def __init__(self, poem=None, entries=None) -> None:
        self.poem = poem
        self.entries = entries or {}

    def get_poem(self, _poem_id):
        return self.poem

    def store_poem(self, _poem_id, poem):
        self.poem = poem

    def get_entries(self, terms):
        return ({term: self.entries[term] for term in terms if term in self.entries}, [])

    def store_entries(self, _requested, _entries):
        pass


class FakeRepository:
    def __init__(self, poem=None, entries=None) -> None:
        self.poem = poem
        self.entries = entries or {}

    def get_poem(self, _poem_id):
        return self.poem

    def get_entries(self, terms):
        return {term: self.entries[term] for term in terms if term in self.entries}


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    main.app.dependency_overrides.clear()
    yield
    main.app.dependency_overrides.clear()


def _client(monkeypatch, *, poem=None, entries=None):
    monkeypatch.setattr(main, "cache", FakeCache(poem, entries))
    monkeypatch.setattr(main, "repository", FakeRepository(poem, entries))
    main.app.dependency_overrides[current_user] = lambda: {"sub": "1"}
    return TestClient(main.app)


def test_unknown_poem_returns_pending_instead_of_false_404(monkeypatch) -> None:
    response = _client(monkeypatch).get("/api/kbbi/42")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"


def test_ready_poem_returns_definitions_for_hover(monkeypatch) -> None:
    poem = {
        "status": "ready",
        "deleted": False,
        "terms": [{"display_term": "Senja", "normalized_term": "senja"}],
    }
    entries = {
        "senja": {
            "bentuk_baku": "senja",
            "kelas_kata": "nomina",
            "definisi": ["waktu setelah matahari terbenam"],
        }
    }

    response = _client(monkeypatch, poem=poem, entries=entries).get("/api/kbbi/42")

    assert response.status_code == 200
    assert response.json()["items"][0]["definisi"] == ["waktu setelah matahari terbenam"]


def test_deleted_poem_returns_404(monkeypatch) -> None:
    response = _client(
        monkeypatch, poem={"status": "deleted", "deleted": True, "terms": []}
    ).get("/api/kbbi/42")

    assert response.status_code == 404


def test_endpoint_requires_jwt_without_dependency_override() -> None:
    main.app.dependency_overrides.clear()
    response = TestClient(main.app).get("/api/kbbi/42")

    assert response.status_code == 401
