from __future__ import annotations

import json
import logging
from typing import Any

import redis

from . import config

log = logging.getLogger("kbbi.cache")


class Cache:
    def __init__(self) -> None:
        self._client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )

    @staticmethod
    def _poem_key(poem_id: int) -> str:
        return f"kbbi:poem:{poem_id}"

    @staticmethod
    def _entry_key(term: str) -> str:
        return f"kbbi:entry:{config.KBBI_DATASET_VERSION}:{term}"

    @staticmethod
    def _missing_key(term: str) -> str:
        return f"kbbi:missing:{config.KBBI_DATASET_VERSION}:{term}"

    def get_poem(self, poem_id: int) -> dict[str, Any] | None:
        try:
            raw = self._client.get(self._poem_key(poem_id))
            return json.loads(raw) if raw else None
        except (redis.RedisError, ValueError):
            log.warning("Redis tidak tersedia saat membaca cache puisi")
            return None

    def store_poem(self, poem_id: int, value: dict[str, Any]) -> None:
        try:
            self._client.set(
                self._poem_key(poem_id),
                json.dumps(value, default=str, ensure_ascii=False),
                ex=config.KBBI_POEM_CACHE_TTL,
            )
        except redis.RedisError:
            log.warning("Redis tidak tersedia saat menyimpan cache puisi")

    def invalidate_poem(self, poem_id: int) -> None:
        try:
            self._client.delete(self._poem_key(poem_id))
        except redis.RedisError:
            log.warning("Gagal menghapus cache puisi %s", poem_id)

    def get_entries(self, terms: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        if not terms:
            return {}, []
        try:
            pipeline = self._client.pipeline(transaction=False)
            for term in terms:
                pipeline.get(self._entry_key(term))
                pipeline.exists(self._missing_key(term))
            values = pipeline.execute()
        except redis.RedisError:
            return {}, list(terms)

        found: dict[str, dict[str, Any]] = {}
        missing_from_cache: list[str] = []
        for index, term in enumerate(terms):
            raw, known_missing = values[index * 2 : index * 2 + 2]
            if raw:
                try:
                    found[term] = json.loads(raw)
                except ValueError:
                    missing_from_cache.append(term)
            elif not known_missing:
                missing_from_cache.append(term)
        return found, missing_from_cache

    def store_entries(self, requested: list[str], entries: dict[str, dict[str, Any]]) -> None:
        try:
            pipeline = self._client.pipeline(transaction=False)
            for term in requested:
                if term in entries:
                    pipeline.set(
                        self._entry_key(term),
                        json.dumps(entries[term], ensure_ascii=False),
                        ex=config.KBBI_ENTRY_CACHE_TTL,
                    )
                else:
                    pipeline.set(self._missing_key(term), "1", ex=config.KBBI_NEGATIVE_CACHE_TTL)
            pipeline.execute()
        except redis.RedisError:
            log.warning("Redis tidak tersedia saat menyimpan cache entri")


cache = Cache()
