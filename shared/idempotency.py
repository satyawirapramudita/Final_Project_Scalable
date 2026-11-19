"""Dedup request via header Idempotency-Key, disimpan di Redis.

Tujuan: klien yang mengulang submit (timeout, double-click, retry jaringan)
tidak menghasilkan data ganda. Klien mengirim header Idempotency-Key; server
mengingat key → respons selama TTL (default 24 jam).

Alur per key:
1. Klaim pertama (SET NX) → "new": request diproses, respons disimpan saat sukses.
2. Klaim ulang, fingerprint sama, respons sudah tersimpan → "replay":
   respons pertama dikembalikan apa adanya.
3. Fingerprint beda, atau request pertama masih diproses → "conflict" (409).

Sifat penting:
- Key di-scope per user (`idem:{service}:{user}:{key}`) — replay lintas user
  mustahil, dan key tidak bisa dipakai mengintip respons user lain.
- Fail-open: saat Redis bermasalah (breaker terbuka), dedup dilewati dan
  request tetap dilayani — idempotency adalah optimasi, bukan prasyarat.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from prometheus_client import Counter

OUTCOME_NEW = "new"
OUTCOME_REPLAY = "replay"
OUTCOME_CONFLICT = "conflict"

IDEMPOTENCY_TOTAL = Counter(
    "idempotency_total",
    "Hasil lookup Idempotency-Key",
    ["service", "result"],
)

# runner(fn, default) dengan gaya RedisBreaker.run: fn menerima client Redis,
# default dikembalikan saat Redis tidak tersedia (fail-open).
Runner = Callable[[Callable[[Any], Any]], Any]


def fingerprint(payload: dict | None) -> str:
    """SHA-256 payload JSON kanonik — untuk mendeteksi key dipakai ulang dengan body berbeda."""
    canonical = json.dumps(payload if payload is not None else {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyStore:
    def __init__(self, runner: Runner, *, service: str, ttl: int = 86_400) -> None:
        self._runner = runner
        self._service = service
        self._ttl = ttl

    def _key(self, user_id: int, key: str) -> str:
        return f"idem:{self._service}:{user_id}:{key}"

    def claim(self, user_id: int, key: str, fp: str) -> tuple[str, dict | None]:
        """Klaim key untuk request ini. Kembalikan (outcome, respons_tersimpan)."""
        k = self._key(user_id, key)

        def op(client) -> tuple[str, dict | None]:
            created = client.set(k, json.dumps({"fingerprint": fp}), nx=True, ex=self._ttl)
            if created:
                return OUTCOME_NEW, None
            raw = client.get(k)
            if not raw:  # kedaluwarsa di sela SET NX dan GET — dianggap baru
                return OUTCOME_NEW, None
            data = json.loads(raw)
            if data.get("fingerprint") != fp:
                return OUTCOME_CONFLICT, None
            if data.get("status") is not None:
                return OUTCOME_REPLAY, data
            # Request pertama masih diproses (respons belum tersimpan).
            return OUTCOME_CONFLICT, None

        outcome, stored = self._runner(op, (OUTCOME_NEW, None))
        IDEMPOTENCY_TOTAL.labels(self._service, outcome).inc()
        return outcome, stored

    def save(self, user_id: int, key: str, fp: str, status: int, body: dict) -> None:
        """Simpan respons sukses agar klaim ulang mendapat replay."""

        def op(client) -> None:
            client.set(
                self._key(user_id, key),
                json.dumps({"fingerprint": fp, "status": status, "body": body}),
                ex=self._ttl,
            )

        self._runner(op, None)

    def release(self, user_id: int, key: str) -> None:
        """Lepaskan klaim saat request gagal, agar retry klien tidak menabrak konflik."""

        def op(client) -> None:
            client.delete(self._key(user_id, key))

        self._runner(op, None)
