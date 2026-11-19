"""Uji shared.idempotency — dedup Idempotency-Key via Redis (fail-open).

Runner di-stub agar logika klaim/simpan/lepas dapat diuji tanpa Redis asli.
"""

from __future__ import annotations

from shared.idempotency import (
    OUTCOME_CONFLICT,
    OUTCOME_NEW,
    OUTCOME_REPLAY,
    IdempotencyStore,
    fingerprint,
)


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


def make_store(client: FakeRedis | None = None) -> tuple[IdempotencyStore, FakeRedis]:
    client = client or FakeRedis()
    store = IdempotencyStore(lambda fn, default: fn(client), service="poem", ttl=86400)
    return store, client


class TestFingerprint:
    def test_payload_sama_fingerprint_sama(self) -> None:
        assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})

    def test_payload_beda_fingerprint_beda(self) -> None:
        assert fingerprint({"a": 1}) != fingerprint({"a": 2})

    def test_payload_none_setara_kosong(self) -> None:
        assert fingerprint(None) == fingerprint({})


class TestClaim:
    def test_klaim_pertama_new(self) -> None:
        store, _ = make_store()
        assert store.claim(1, "k-1", fingerprint({"a": 1})) == (OUTCOME_NEW, None)

    def test_replay_setelah_disimpan(self) -> None:
        store, _ = make_store()
        fp = fingerprint({"a": 1})
        store.claim(1, "k-1", fp)
        store.save(1, "k-1", fp, 201, {"ok": True})
        outcome, stored = store.claim(1, "k-1", fp)
        assert outcome == OUTCOME_REPLAY
        assert stored["status"] == 201
        assert stored["body"] == {"ok": True}

    def test_request_bersamaan_tanpa_response_conflict(self) -> None:
        # Klaim kedua dengan fingerprint sama tapi respons pertama belum selesai
        # → conflict agar klien mencoba lagi setelah request pertama selesai.
        store, _ = make_store()
        fp = fingerprint({"a": 1})
        store.claim(1, "k-1", fp)
        assert store.claim(1, "k-1", fp) == (OUTCOME_CONFLICT, None)

    def test_fingerprint_beda_conflict(self) -> None:
        store, _ = make_store()
        store.claim(1, "k-1", fingerprint({"a": 1}))
        outcome, _ = store.claim(1, "k-1", fingerprint({"a": 2}))
        assert outcome == OUTCOME_CONFLICT

    def test_key_di_scope_per_user(self) -> None:
        # Key yang sama milik user berbeda tidak saling bertabrakan —
        # melindungi dari replay lintas user saat pentest.
        store, _ = make_store()
        fp = fingerprint({"a": 1})
        assert store.claim(1, "k-1", fp)[0] == OUTCOME_NEW
        assert store.claim(2, "k-1", fp)[0] == OUTCOME_NEW

    def test_redis_down_fail_open_sebagai_new(self) -> None:
        # Runner yang selalu gagal (breaker terbuka) → dianggap request baru,
        # dedup dilewati daripada menjatuhkan service.
        store = IdempotencyStore(lambda fn, default: default, service="poem", ttl=1)
        assert store.claim(1, "k-1", fingerprint({"a": 1})) == (OUTCOME_NEW, None)

    def test_release_mengembalikan_key_ke_new(self) -> None:
        store, _ = make_store()
        store.claim(1, "k-1", fingerprint({"a": 1}))
        store.release(1, "k-1")
        assert store.claim(1, "k-1", fingerprint({"a": 1})) == (OUTCOME_NEW, None)

    def test_save_tanpa_claim_sebelumnya_tidak_error(self) -> None:
        store, _ = make_store()
        store.save(1, "k-x", fingerprint({}), 200, {})
