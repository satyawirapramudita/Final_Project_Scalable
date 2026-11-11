from __future__ import annotations

from app.consumer import apply_event
import pytest


class FakeRepository:
    def __init__(self) -> None:
        self.analysis = None
        self.deleted = None

    def apply_analysis(self, **kwargs):
        self.analysis = kwargs
        return "applied"

    def apply_delete(self, **kwargs):
        self.deleted = kwargs
        return "applied"


class FakeCache:
    def __init__(self) -> None:
        self.invalidated = []

    def invalidate_poem(self, poem_id: int) -> None:
        self.invalidated.append(poem_id)


def test_poem_analyzed_deduplicates_terms_and_invalidates_poem_cache() -> None:
    repository = FakeRepository()
    cache = FakeCache()

    outcome = apply_event(
        {
            "event_id": "b8829981-2ca7-4214-9100-4ab5cf96289f",
            "event_name": "poem.analyzed",
            "occurred_at": "2026-11-10T08:00:00+00:00",
            "payload": {
                "poem_id": 42,
                "kata_puitif": ["Senja", " senja ", "Cakrawala"],
            },
        },
        repository=repository,
        cache_backend=cache,
        max_terms=30,
    )

    assert outcome == "applied"
    assert repository.analysis["poem_id"] == 42
    assert repository.analysis["terms"] == [
        ("Senja", "senja"),
        ("Cakrawala", "cakrawala"),
    ]
    assert cache.invalidated == [42]


def test_poem_deleted_removes_only_poem_relations_and_invalidates_cache() -> None:
    repository = FakeRepository()
    cache = FakeCache()

    outcome = apply_event(
        {
            "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
            "event_name": "poem.deleted",
            "occurred_at": "2026-11-10T08:00:00+00:00",
            "payload": {"poem_id": 9},
        },
        repository=repository,
        cache_backend=cache,
        max_terms=30,
    )

    assert outcome == "applied"
    assert repository.deleted["poem_id"] == 9
    assert cache.invalidated == [9]


def test_unknown_event_is_ignored() -> None:
    assert apply_event(
        {"event_name": "other.event", "payload": {}},
        repository=FakeRepository(),
        cache_backend=FakeCache(),
        max_terms=30,
    ) == "ignored"


def test_known_event_rejects_non_uuid_event_id() -> None:
    with pytest.raises(ValueError, match="event_id tidak valid"):
        apply_event(
            {
                "event_id": "not-a-uuid",
                "event_name": "poem.deleted",
                "occurred_at": "2026-11-10T08:00:00+00:00",
                "payload": {"poem_id": 9},
            },
            repository=FakeRepository(),
            cache_backend=FakeCache(),
            max_terms=30,
        )


def test_known_event_requires_timezone_in_occurred_at() -> None:
    with pytest.raises(ValueError, match="occurred_at harus memiliki timezone"):
        apply_event(
            {
                "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
                "event_name": "poem.deleted",
                "occurred_at": "2026-11-10T08:00:00",
                "payload": {"poem_id": 9},
            },
            repository=FakeRepository(),
            cache_backend=FakeCache(),
            max_terms=30,
        )


@pytest.mark.parametrize("poem_id", [1.5, 2.9, True])
def test_known_event_rejects_non_integer_poem_id(poem_id) -> None:
    with pytest.raises(ValueError, match="poem_id yang valid"):
        apply_event(
            {
                "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
                "event_name": "poem.deleted",
                "occurred_at": "2026-11-10T08:00:00+00:00",
                "payload": {"poem_id": poem_id},
            },
            repository=FakeRepository(),
            cache_backend=FakeCache(),
            max_terms=30,
        )


def test_known_event_accepts_integer_poem_id_string() -> None:
    repository = FakeRepository()

    apply_event(
        {
            "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
            "event_name": "poem.deleted",
            "occurred_at": "2026-11-10T08:00:00+00:00",
            "payload": {"poem_id": "9"},
        },
        repository=repository,
        cache_backend=FakeCache(),
        max_terms=30,
    )

    assert repository.deleted["poem_id"] == 9


def test_known_event_rejects_poem_id_beyond_mysql_int_range() -> None:
    with pytest.raises(ValueError, match="poem_id yang valid"):
        apply_event(
            {
                "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
                "event_name": "poem.deleted",
                "occurred_at": "2026-11-10T08:00:00+00:00",
                "payload": {"poem_id": 2_147_483_648},
            },
            repository=FakeRepository(),
            cache_backend=FakeCache(),
            max_terms=30,
        )


def test_poem_analyzed_rejects_null_kata_puitif() -> None:
    with pytest.raises(ValueError, match="kata_puitif harus berupa array"):
        apply_event(
            {
                "event_id": "8ba04991-c388-4b0e-b0e0-2fa9e225fac4",
                "event_name": "poem.analyzed",
                "occurred_at": "2026-11-10T08:00:00+00:00",
                "payload": {"poem_id": 9, "kata_puitif": None},
            },
            repository=FakeRepository(),
            cache_backend=FakeCache(),
            max_terms=30,
        )
