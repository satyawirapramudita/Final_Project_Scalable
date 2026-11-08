"""Cache hasil pencarian di Redis (tier 2). Gagal Redis tidak boleh menjatuhkan service.

Kunci hasil memuat satu versi global (pencarian mencakup semua puisi). Setiap
event yang mengubah indeks menaikkan versinya (bump), sehingga hasil lama
otomatis tidak terpakai: puisi yang baru masuk langsung muncul di pencarian
tanpa menunggu TTL habis. TTL tetap ada sebagai batas memori dan pengaman
bila bump gagal.

Bump digabung (debounce): paling cepat sekali per CACHE_BUMP_INTERVAL, dengan satu
bump susulan agar perubahan terakhir dalam sebuah semburan tidak terlewat. Tanpa
ini, setiap puisi baru membatalkan seluruh cache dan hit ratio runtuh saat
banyak puisi masuk.

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
_bump_lock = threading.Lock()
_last_bump = 0.0
_bump_pending = False


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


VERSION_KEY = "search:ver"


def _result_key(version: str, digest: str) -> str:
    return f"search:r:{version}:{digest}"


def lookup(digest: str) -> tuple[dict | None, str]:
    """Kembalikan (hasil atau None, versi). Versi dipakai lagi saat menyimpan,
    agar hasil yang dihitung sebelum sebuah bump tidak tersimpan di versi baru."""
    client = _get_client()
    if client is None:
        return None, "0"
    try:
        version = client.get(VERSION_KEY) or "0"
        raw = client.get(_result_key(version, digest))
        return (json.loads(raw) if raw else None), version
    except Exception:  # noqa: BLE001
        _mark_down()
        return None, "0"


def store(digest: str, version: str, value: dict) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.set(
            _result_key(version, digest),
            json.dumps(value, default=str),
            ex=config.CACHE_TTL_SEARCH,
        )
    except Exception:  # noqa: BLE001
        _mark_down()


def bump() -> None:
    """Invalidasi semua hasil cache pencarian (digabung, lihat docstring modul)."""
    global _last_bump, _bump_pending
    with _bump_lock:
        wait = config.CACHE_BUMP_INTERVAL - (time.monotonic() - _last_bump)
        if wait <= 0:
            _last_bump = time.monotonic()
        elif _bump_pending:
            return  # bump susulan sudah dijadwalkan; perubahan ini ikut tercakup
        else:
            _bump_pending = True
            timer = threading.Timer(wait, _trailing_bump)
            timer.daemon = True
            timer.start()
            return
    _incr_version()


def _trailing_bump() -> None:
    global _last_bump, _bump_pending
    with _bump_lock:
        _bump_pending = False
        _last_bump = time.monotonic()
    _incr_version()


def _incr_version() -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.incr(VERSION_KEY)
        client.expire(VERSION_KEY, _VERSION_TTL)
    except Exception:  # noqa: BLE001
        log.warning("Gagal invalidasi cache pencarian (TTL jadi pengaman)")
        _mark_down()
