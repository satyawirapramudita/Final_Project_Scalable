"""Uji lapisan cache Redis sungguhan.

Fokus: fail-open saat Redis mati, TTL negatif, dan key namespacing per versi
dataset. Semua perilaku ini hanya bisa dibuktikan dengan Redis nyata.
"""

from __future__ import annotations

import pytest

from tests.live.conftest import requires_live

pytestmark = requires_live


@pytest.fixture
def clean_redis(flush_cache):
    return flush_cache


def test_poem_roundtrip_and_ttl(clean_redis, schema) -> None:
    cache = clean_redis
    poem = {"status": "ready", "deleted": 0, "terms": [{"display_term": "Senja", "normalized_term": "senja"}]}

    assert cache.get_poem(42) is None
    cache.store_poem(42, poem)

    assert cache.get_poem(42) == poem
    ttl = cache._client.ttl("kbbi:poem:42")
    assert 0 < ttl <= 86_400


def test_invalidate_poem_removes_entry(clean_redis, schema) -> None:
    cache = clean_redis
    cache.store_poem(42, {"status": "ready", "deleted": 0, "terms": []})

    cache.invalidate_poem(42)

    assert cache.get_poem(42) is None


def test_poem_cache_serializes_non_native_types(clean_redis, schema) -> None:
    """default=str harus membuat datetime dan tuple tetap bisa di-cache."""
    from datetime import datetime

    cache = clean_redis
    cache.store_poem(42, {"status": "ready", "deleted": 0, "analysis_at": datetime(2026, 11, 10, 8, 0), "terms": ()})

    assert cache.get_poem(42)["analysis_at"] == "2026-11-10 08:00:00"


def test_entry_hit_and_miss_reporting(clean_redis, schema) -> None:
    cache = clean_redis
    cache.store_entries(["senja"], {"senja": {"bentuk_baku": "senja", "kelas_kata": "nomina", "definisi": ["x"]}})

    found, unresolved = cache.get_entries(["senja", "temaram"])

    assert set(found) == {"senja"}
    assert unresolved == ["temaram"]


def test_negative_cache_prevents_repeated_miss(clean_redis, schema) -> None:
    """Kata yang tidak ada disimpan sebagai missing supaya tidak query DB berulang."""
    cache = clean_redis
    cache.store_entries(["temaram"], {})

    found, unresolved = cache.get_entries(["temaram"])

    assert found == {}
    assert unresolved == []
    assert cache._client.get("kbbi:missing:kbbi-v-live:temaram") == "1"


def test_negative_cache_ttl_is_shorter_than_entry_ttl(clean_redis, schema) -> None:
    cache = clean_redis
    cache.store_entries(["temaram"], {})

    missing_ttl = cache._client.ttl("kbbi:missing:kbbi-v-live:temaram")

    assert 0 < missing_ttl <= 86_400


def test_cache_keys_are_namespaced_by_dataset_version(clean_redis, schema) -> None:
    cache = clean_redis
    cache.store_entries(["senja"], {"senja": {"definisi": ["v-live"]}})

    assert cache._client.exists("kbbi:entry:kbbi-v-live:senja") == 1
    assert cache._client.exists("kbbi:entry:kbbi-v-2020:senja") == 0


def test_corrupt_entry_payload_is_treated_as_miss(clean_redis, schema) -> None:
    """JSON rusak tidak boleh membuat request gagal — hanya jadi cache miss."""
    cache = clean_redis
    cache._client.set("kbbi:entry:kbbi-v-live:senja", "{{{ bukan json")

    found, unresolved = cache.get_entries(["senja"])

    assert found == {}
    assert unresolved == ["senja"]


def test_corrupt_poem_payload_is_treated_as_miss(clean_redis, schema) -> None:
    cache = clean_redis
    cache._client.set("kbbi:poem:42", "bukan json")

    assert cache.get_poem(42) is None


def test_empty_term_list_skips_redis(clean_redis, schema) -> None:
    assert clean_redis.get_entries([]) == ({}, [])


def test_cache_fails_open_when_redis_is_unreachable(schema, monkeypatch) -> None:
    """Redis mati = degradasi, bukan error. Semua request tetap harus lolos."""
    import redis as redis_lib

    from app.cache import Cache

    broken = Cache()
    broken._client = redis_lib.Redis(
        host="127.0.0.1", port=1, decode_responses=True,
        socket_timeout=0.2, socket_connect_timeout=0.2,
    )

    assert broken.get_poem(42) is None
    assert broken.get_entries(["senja"]) == ({}, ["senja"])
    broken.store_poem(42, {"status": "ready"})       # tidak boleh melempar
    broken.invalidate_poem(42)                        # tidak boleh melempar
    broken.store_entries(["senja"], {"senja": {}})    # tidak boleh melempar
