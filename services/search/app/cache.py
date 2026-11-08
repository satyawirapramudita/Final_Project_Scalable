"""Cache hasil pencarian di Redis (tier 2). Gagal Redis tidak boleh menjatuhkan service.

Kunci hasil memuat versi per pengguna. Setiap event yang mengubah indeks
milik seorang pengguna menaikkan versinya (bump), sehingga hasil lama
otomatis tidak terpakai: puisi yang baru masuk langsung muncul di pencarian
tanpa menunggu TTL habis. TTL tetap ada sebagai batas memori dan pengaman
bila bump gagal.

Circuit breaker: begitu Redis gagal, request berhenti memakainya dan sebuah
thread latar memeriksa pemulihan. Ini penting karena menyambung ke Redis yang
berhenti bisa memblokir ~8 detik (resolusi DNS container yang mati; timeout
socket tidak berlaku untuk tahap itu), dan tanpa breaker setiap request
menanggung penundaan tersebut.
"""

from __future__ import annotations

import json
import logging
import threading
import time

try:
    import redis
except Exception:  # noqa: BLE001
    redis = None  # type: ignore[assignment]

from . import config

log = logging.getLogger("search.cache")
_client = None
_available = True
_prober_lock = threading.Lock()
_VERSION_TTL = 86400
_PROBE_INTERVAL = 3


def _get_client():
    global _client
    if not _available:
        return None
    if _client is None and redis is not None:
        _client = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            decode_responses=True,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
    return _client


def _probe_until_back() -> None:
    global _available
    try:
        while True:
            time.sleep(_PROBE_INTERVAL)
            try:
                _client.ping()
                _available = True
                log.info("Redis pulih, cache aktif lagi")
                return
            except Exception:  # noqa: BLE001
                continue
    finally:
        _prober_lock.release()


def _mark_down() -> None:
    """Hentikan pemakaian Redis dan mulai satu thread pemeriksa pemulihan."""
    global _available
    if _available:
        log.warning("Redis tidak terjangkau, cache dimatikan sementara")
    _available = False
    if _prober_lock.acquire(blocking=False):
        threading.Thread(target=_probe_until_back, name="redis-prober", daemon=True).start()


def _version_key(user_id: int) -> str:
    return f"search:ver:{user_id}"


def _result_key(user_id: int, version: str, digest: str) -> str:
    return f"search:r:{user_id}:{version}:{digest}"


def lookup(user_id: int, digest: str) -> tuple[dict | None, str]:
    """Kembalikan (hasil atau None, versi). Versi dipakai lagi saat menyimpan,
    agar hasil yang dihitung sebelum sebuah bump tidak tersimpan di versi baru."""
    client = _get_client()
    if client is None:
        return None, "0"
    try:
        version = client.get(_version_key(user_id)) or "0"
        raw = client.get(_result_key(user_id, version, digest))
        return (json.loads(raw) if raw else None), version
    except Exception:  # noqa: BLE001
        _mark_down()
        return None, "0"


def store(user_id: int, digest: str, version: str, value: dict) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.set(
            _result_key(user_id, version, digest),
            json.dumps(value, default=str),
            ex=config.CACHE_TTL_SEARCH,
        )
    except Exception:  # noqa: BLE001
        _mark_down()


def bump(user_id: int) -> None:
    """Invalidasi semua hasil cache milik pengguna ini."""
    client = _get_client()
    if client is None:
        return
    try:
        key = _version_key(user_id)
        client.incr(key)
        client.expire(key, _VERSION_TTL)
    except Exception:  # noqa: BLE001
        log.warning("Gagal invalidasi cache pengguna %s (TTL jadi pengaman)", user_id)
        _mark_down()
