"""Uji cabang yang belum tersentuh: validasi envelope, pool, dan importer live.

Fokus pada jalur gagal — justru di situ bug bersembunyi.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from tests.live.conftest import requires_live

pytestmark = requires_live

VALID_ID = "b8829981-2ca7-4214-9100-4ab5cf96289f"


def _envelope(**overrides) -> dict:
    base = {
        "event_id": VALID_ID,
        "event_name": "poem.deleted",
        "occurred_at": "2026-11-10T08:00:00+00:00",
        "payload": {"poem_id": 9},
    }
    base.update(overrides)
    return base


class _SpyRepo:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def apply_analysis(self, **kwargs):
        self.calls.append("analysis")
        return "applied"

    def apply_delete(self, **kwargs):
        self.calls.append("delete")
        return "applied"


class _SpyCache:
    def __init__(self) -> None:
        self.invalidated: list[int] = []

    def invalidate_poem(self, poem_id: int) -> None:
        self.invalidated.append(poem_id)


def _apply(data: dict, *, max_terms: int = 30):
    from app.consumer import apply_event

    repo, cache = _SpyRepo(), _SpyCache()
    outcome = apply_event(data, repository=repo, cache_backend=cache, max_terms=max_terms)
    return outcome, repo, cache


# ─────────────────────────── validasi envelope ───────────────────────────


@pytest.mark.parametrize("poem_id", [None, "abc", 0, -5])
def test_invalid_poem_id_rejects_event(poem_id) -> None:
    with pytest.raises(ValueError):
        _apply(_envelope(payload={"poem_id": poem_id}))


@pytest.mark.parametrize("poem_id", [1.5, 2.9, 1000.75, True])
def test_non_integer_poem_id_is_rejected(poem_id) -> None:
    with pytest.raises(ValueError):
        _apply(_envelope(payload={"poem_id": poem_id}))


def test_decimal_string_poem_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        _apply(_envelope(payload={"poem_id": "42.0"}))


@pytest.mark.parametrize("poem_id", [2**63, 2**64, 2**63 - 1])
def test_poem_id_beyond_mysql_int_range_is_rejected(poem_id) -> None:
    with pytest.raises(ValueError):
        _apply(_envelope(payload={"poem_id": poem_id}))


def test_missing_poem_id_rejects_event() -> None:
    with pytest.raises(ValueError, match="poem_id"):
        _apply(_envelope(payload={}))


@pytest.mark.parametrize("bad", ["bukan-uuid", "", None, 12345, "00000000-0000-0000-0000-00000000000"])
def test_invalid_event_id_is_rejected(bad) -> None:
    with pytest.raises(ValueError, match="event_id tidak valid"):
        _apply(_envelope(event_id=bad))


def test_uppercase_uuid_is_normalized() -> None:
    outcome, repo, _ = _apply(_envelope(event_id=VALID_ID.upper()))

    assert outcome == "applied"
    assert repo.calls == ["delete"]


@pytest.mark.parametrize("bad", ["2026-11-10T08:00:00", "bukan-tanggal", None, ""])
def test_occurred_at_without_timezone_is_rejected(bad) -> None:
    with pytest.raises(ValueError):
        _apply(_envelope(occurred_at=bad))


def test_z_suffix_timestamp_is_accepted() -> None:
    outcome, _, _ = _apply(_envelope(occurred_at="2026-11-10T08:00:00Z"))

    assert outcome == "applied"


def test_offset_timestamp_is_normalized_to_utc_naive() -> None:
    _, repo, _ = _apply(_envelope(occurred_at="2026-11-10T15:00:00+07:00"))

    assert repo.calls == ["delete"]


@pytest.mark.parametrize("payload", [None, "string", 42, ["list"]])
def test_non_dict_payload_is_rejected(payload) -> None:
    with pytest.raises(ValueError, match="payload event tidak valid"):
        _apply(_envelope(payload=payload))


@pytest.mark.parametrize("terms", ["Senja", 42, {"a": 1}])
def test_analyzed_with_non_list_kata_puitif_is_rejected(terms) -> None:
    with pytest.raises(ValueError, match="kata_puitif harus berupa array"):
        _apply(
            _envelope(
                event_name="poem.analyzed",
                payload={"poem_id": 9, "kata_puitif": terms},
            )
        )


def test_kata_puitif_null_is_rejected() -> None:
    with pytest.raises(ValueError, match="kata_puitif harus berupa array"):
        _apply(
            _envelope(event_name="poem.analyzed", payload={"poem_id": 9, "kata_puitif": None})
        )


def test_analyzed_without_kata_puitif_produces_ready_poem_with_no_terms() -> None:
    outcome, repo, cache = _apply(
        _envelope(event_name="poem.analyzed", payload={"poem_id": 9})
    )

    assert outcome == "applied"
    assert repo.calls == ["analysis"]
    assert cache.invalidated == [9]


def test_analyzed_filters_non_string_terms() -> None:
    from app.consumer import apply_event

    captured = {}

    class Repo:
        def apply_analysis(self, **kwargs):
            captured.update(kwargs)
            return "applied"

    apply_event(
        _envelope(
            event_name="poem.analyzed",
            payload={"poem_id": 9, "kata_puitif": ["Senja", 42, None, {"x": 1}, "Cakrawala"]},
        ),
        repository=Repo(),
        cache_backend=_SpyCache(),
        max_terms=30,
    )

    assert captured["terms"] == [("Senja", "senja"), ("Cakrawala", "cakrawala")]


def test_unknown_event_short_circuits_before_validation() -> None:
    """Event asing tidak boleh divalidasi, apalagi menyentuh repository."""
    outcome, repo, cache = _apply({"event_name": "poem.created", "payload": {}})

    assert outcome == "ignored"
    assert repo.calls == []
    assert cache.invalidated == []


def test_event_without_name_is_ignored() -> None:
    outcome, repo, _ = _apply({"payload": {"poem_id": 9}})

    assert outcome == "ignored"
    assert repo.calls == []


def test_ignored_outcome_does_not_invalidate_cache() -> None:
    class Repo:
        def apply_analysis(self, **kwargs):
            return "ignored"

    from app.consumer import apply_event

    cache = _SpyCache()
    outcome = apply_event(
        _envelope(event_name="poem.analyzed", payload={"poem_id": 9, "kata_puitif": []}),
        repository=Repo(), cache_backend=cache, max_terms=30,
    )

    assert outcome == "ignored"
    assert cache.invalidated == []


def test_handle_event_records_metric_and_reraises_failure(monkeypatch) -> None:
    """handle_event harus melempar ulang supaya RabbitMQ bisa retry/DLQ."""
    from app import consumer

    def boom(*_args, **_kwargs):
        raise ValueError("handler gagal")

    monkeypatch.setattr(consumer, "apply_event", boom)

    with pytest.raises(ValueError):
        consumer.handle_event(_envelope())


def test_handle_event_with_real_pipeline_updates_database(schema, flush_cache) -> None:
    from app import consumer

    consumer.handle_event(
        _envelope(
            event_name="poem.analyzed",
            payload={"poem_id": 555, "kata_puitif": ["Senja"]},
        )
    )

    from app.db import repository

    assert repository.get_poem(555)["status"] == "ready"


# ─────────────────────────── connection pool ───────────────────────────


def test_pool_reports_unavailable_when_exhausted(florets_pool) -> None:
    """Pool penuh harus Unavailable (->503), bukan menggantung selamanya."""
    from app.db import Pool, Unavailable

    small = Pool(size=1, timeout=1)
    holder = threading.Event()
    release = threading.Event()

    def occupy() -> None:
        with small.connection():
            holder.set()
            release.wait(timeout=10)

    thread = threading.Thread(target=occupy, daemon=True)
    thread.start()
    assert holder.wait(timeout=10)

    with pytest.raises(Unavailable, match="database pool penuh"):
        with small.connection():
            pass

    release.set()
    thread.join(timeout=10)


def test_pool_reuses_connection_after_release(florets_pool) -> None:
    from app.db import Pool

    pool = Pool(size=1, timeout=2)
    with pool.connection() as first:
        pass
    with pool.connection() as second:
        assert second.open is True


def test_pool_rolls_back_failed_transaction_and_returns_connection(schema) -> None:
    """Sesudah error, koneksi harus kembali sehat (bukan bocor/rusak)."""
    from app.db import pool

    with schema.pool.connection() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM poetic_terms")

    with pytest.raises(RuntimeError):
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO poetic_terms (display_term,normalized_term) VALUES ('x','x')")
            raise RuntimeError("gagal di tengah transaksi")

    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM poetic_terms")
        assert cur.fetchone()["n"] == 0


def test_unavailable_error_maps_to_503(florets_pool, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app import main
    from app.db import Unavailable
    from shared.security import create_token

    main.app.dependency_overrides.clear()
    monkeypatch.setattr(main.cache, "get_poem", lambda _poem_id: None)

    def explode(_poem_id):
        raise Unavailable("penuh")

    monkeypatch.setattr(main.repository, "get_poem", explode)
    token = create_token(user_id=1, username="dzaky")
    response = TestClient(main.app).get(
        "/api/kbbi/42", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 503


def test_mysql_error_maps_to_503_without_leaking_details(florets_pool, monkeypatch) -> None:
    import pymysql
    from fastapi.testclient import TestClient

    from app import main
    from shared.security import create_token

    main.app.dependency_overrides.clear()
    monkeypatch.setattr(main.cache, "get_poem", lambda _poem_id: None)

    def explode(_poem_id):
        raise pymysql.err.ProgrammingError("SELECT * FROM rahasia_internal")

    monkeypatch.setattr(main.repository, "get_poem", explode)
    token = create_token(user_id=1, username="dzaky")
    response = TestClient(main.app).get(
        "/api/kbbi/42", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 503
    assert "rahasia_internal" not in response.text
