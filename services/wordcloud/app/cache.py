"""Cache-aside Redis untuk hasil word cloud per puisi.

Kunci `wordcloud:{poem_id}` (TTL CACHE_TTL_WORDCLOUD), dihapus setiap kali
event mengubah puisi itu. Redis bersifat fail-open lewat shared.redis_breaker:
bila Redis mati, request dilayani dari MySQL tanpa menunggu timeout.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis
from prometheus_client import Counter

from shared.redis_breaker import RedisBreaker

from . import config

log = logging.getLogger("wordcloud.cache")

CACHE_RESULT = Counter("wordcloud_cache_total", "Hasil cache word cloud", ["result"])


class Cache:
    def __init__(self) -> None:
        self._client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            socket_timeout=0.5,
            socket_connect_timeout=0.5,
        )
        self._breaker = RedisBreaker(self._client, name="wordcloud")

    @staticmethod
    def _key(poem_id: int) -> str:
        return f"wordcloud:{poem_id}"

    def get(self, poem_id: int) -> dict[str, Any] | None:
        if not self._breaker.available:
            CACHE_RESULT.labels("bypass").inc()
            return None
        raw = self._breaker.run(lambda: self._client.get(self._key(poem_id)), default=None)
        CACHE_RESULT.labels("hit" if raw else "miss").inc()
        return json.loads(raw) if raw else None

    def set(self, poem_id: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        self._breaker.run(
            lambda: self._client.set(self._key(poem_id), payload, ex=config.CACHE_TTL), default=None
        )

    def invalidate(self, poem_id: int) -> None:
        # Saat breaker terbuka invalidasi dilewati (consumer tidak boleh tertahan
        # beberapa detik per event); entri basi paling lama hidup selama CACHE_TTL.
        self._breaker.run(lambda: self._client.delete(self._key(poem_id)), default=None)


cache = Cache()
