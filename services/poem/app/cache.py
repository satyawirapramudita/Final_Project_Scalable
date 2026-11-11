"""Cache-aside Redis (tier 2). Gagal Redis tidak boleh menjatuhkan service."""

from __future__ import annotations

import json
import logging

from prometheus_client import Counter

try:
    import redis
except Exception:  # noqa: BLE001
    redis = None  # type: ignore[assignment]

from . import config

log = logging.getLogger("poem.cache")
CACHE_RESULT = Counter("poem_cache_total", "Hasil lookup cache poem", ["result"])
_client = None


def _get_client():
    global _client
    if _client is None and redis is not None:
        _client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
    return _client


def get(key: str):
    try:
        client = _get_client()
        if client is None:
            return None
        raw = client.get(key)
        CACHE_RESULT.labels("hit" if raw else "miss").inc()
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        CACHE_RESULT.labels("error").inc()
        return None


def set(key: str, value, ttl: int) -> None:  # noqa: A001
    try:
        client = _get_client()
        if client is None:
            return
        client.set(key, json.dumps(value, default=str), ex=ttl)
    except Exception:  # noqa: BLE001
        pass


def delete(*keys: str) -> None:
    try:
        client = _get_client()
        if client is None or not keys:
            return
        client.delete(*keys)
    except Exception:  # noqa: BLE001
        pass


def delete_pattern(pattern: str) -> None:
    try:
        client = _get_client()
        if client is None:
            return
        for key in client.scan_iter(match=pattern, count=100):
            client.delete(key)
    except Exception:  # noqa: BLE001
        pass
