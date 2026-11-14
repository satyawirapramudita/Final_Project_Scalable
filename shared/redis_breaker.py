"""Circuit breaker kecil untuk Redis (dipakai cache poem, wordcloud, kbbi-linker).

Masalah yang diatasi: saat container Redis mati, setiap perintah Redis menunggu
lookup DNS Docker yang gagal (beberapa detik, tidak terkena socket_connect_timeout).
Tanpa breaker, SETIAP request ikut menunggu, padahal cache seharusnya fail-open.

Cara kerja: kegagalan pertama membuka breaker; selama terbuka semua operasi
langsung mengembalikan nilai default (request dilayani dari MySQL). Thread latar
mencoba PING tiap `probe_interval` detik dan menutup breaker begitu Redis pulih,
jadi tidak ada request user yang menjadi "percobaan".

    breaker = RedisBreaker(client, name="poem")
    raw = breaker.run(lambda: client.get(key), default=None)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, TypeVar

log = logging.getLogger("shared.redis_breaker")
T = TypeVar("T")


class RedisBreaker:
    def __init__(self, client, *, name: str, probe_interval: float = 5.0) -> None:
        self._client = client
        self._name = name
        self._interval = probe_interval
        self._open = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return not self._open

    def run(self, fn: Callable[[], T], default: T) -> T:
        """Jalankan operasi Redis; kembalikan `default` bila breaker terbuka atau gagal."""
        if self._open:
            return default
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — cache tidak boleh menjatuhkan layanan
            self.trip(exc)
            return default

    def trip(self, exc: Exception) -> None:
        with self._lock:
            if self._open:
                return
            self._open = True
        log.warning("[%s] Redis tidak tersedia (%s); cache dilewati sampai pulih", self._name, exc)
        threading.Thread(target=self._probe, name=f"redis-probe-{self._name}", daemon=True).start()

    def _probe(self) -> None:
        while True:
            time.sleep(self._interval)
            try:
                self._client.ping()
            except Exception:  # noqa: BLE001
                continue
            self._open = False
            log.info("[%s] Redis pulih; cache aktif kembali", self._name)
            return
