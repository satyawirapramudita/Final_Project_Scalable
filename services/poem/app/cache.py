"""Cache-aside Redis (tier 2). Gagal Redis tidak boleh menjatuhkan service.

Semua operasi lewat shared.redis_breaker: bila Redis mati, request langsung
dilayani dari MySQL (tanpa menunggu timeout DNS/koneksi di setiap request).
Selama breaker terbuka invalidasi juga dilewati; entri basi paling lama hidup
selama TTL-nya (pendek).
"""

from __future__ import annotations

import json
import logging

from prometheus_client import Counter

try:
    import redis
except Exception:  # noqa: BLE001
    redis = None  # type: ignore[assignment]

from shared.redis_breaker import RedisBreaker

from . import config

log = logging.getLogger("poem.cache")
CACHE_RESULT = Counter("poem_cache_total", "Hasil lookup cache poem", ["result"])
_client = None
_breaker: RedisBreaker | None = None


def _get_client():
    global _client, _breaker
    if _client is None and redis is not None:
        _client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
        _breaker = RedisBreaker(_client, name="poem")
    return _client


def _run(fn, default):
    client = _get_client()
    if client is None or _breaker is None:
        return default
    return _breaker.run(lambda: fn(client), default)


_ERROR = object()


def get(key: str):
    if _get_client() is not None and _breaker is not None and not _breaker.available:
        CACHE_RESULT.labels("bypass").inc()
        return None
    raw = _run(lambda c: c.get(key), _ERROR)
    if raw is _ERROR:
        CACHE_RESULT.labels("error").inc()
        return None
    CACHE_RESULT.labels("hit" if raw else "miss").inc()
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def set(key: str, value, ttl: int) -> None:  # noqa: A001
    payload = json.dumps(value, default=str)
    _run(lambda c: c.set(key, payload, ex=ttl), None)


def delete(*keys: str) -> None:
    if keys:
        _run(lambda c: c.delete(*keys), None)


def delete_pattern(pattern: str) -> None:
    def _scan_delete(c):
        for key in c.scan_iter(match=pattern, count=100):
            c.delete(key)

    _run(_scan_delete, None)
