"""Circuit breaker untuk query pencarian ke MySQL (Bab 11.2).

Pool di db.py hanya gagal cepat bila koneksi BARU tidak bisa dibuka. Bila MySQL
masih menerima koneksi tetapi macet atau sangat lambat, setiap request menunggu
read_timeout (5 dtk) sambil memegang slot pool, dan pool cepat habis. Breaker
ini menghitung kegagalan query itu sendiri:

    CLOSED     normal. `failure_threshold` kegagalan berturut-turut -> OPEN.
    OPEN       semua request ditolak seketika (BreakerOpen) selama
               `reset_timeout` detik, tanpa menyentuh MySQL.
    HALF_OPEN  setelah jeda itu, SATU request percobaan diizinkan. Berhasil ->
               CLOSED; gagal -> OPEN lagi. Request lain tetap ditolak selama
               percobaan berjalan.

Status diekspor ke Prometheus: search_breaker_state (0 closed, 1 open, 2 half-open).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from prometheus_client import Counter, Gauge

log = logging.getLogger("search.breaker")

T = TypeVar("T")

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"
_STATE_VALUE = {CLOSED: 0, OPEN: 1, HALF_OPEN: 2}

BREAKER_STATE = Gauge(
    "search_breaker_state",
    "Status circuit breaker (0 closed, 1 open, 2 half-open)",
    ["name"],
)
BREAKER_TRANSITIONS = Counter(
    "search_breaker_transitions_total",
    "Perpindahan status circuit breaker",
    ["name", "to"],
)
BREAKER_REJECTED = Counter(
    "search_breaker_rejected_total",
    "Request yang ditolak breaker tanpa menyentuh dependensi",
    ["name"],
)


class BreakerOpen(Exception):
    """Breaker terbuka: dependensi dianggap sakit, request tidak dicoba."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_timeout: float = 10.0,
        counts_as_failure: Callable[[BaseException], bool] = lambda _exc: True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._threshold = max(1, failure_threshold)
        self._reset_timeout = reset_timeout
        self._counts = counts_as_failure
        self._clock = clock
        self._lock = threading.Lock()
        self._state = CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._trial_running = False
        BREAKER_STATE.labels(name).set(0)

    @property
    def state(self) -> str:
        with self._lock:
            # OPEN yang jedanya sudah lewat dilaporkan sebagai HALF_OPEN
            if self._state == OPEN and self._clock() - self._opened_at >= self._reset_timeout:
                return HALF_OPEN
            return self._state

    def call(self, fn: Callable[[], T]) -> T:
        """Jalankan `fn` lewat breaker. Melempar BreakerOpen bila ditolak."""
        trial = self._before()
        try:
            result = fn()
        except BaseException as exc:
            self._after_failure(trial, exc)
            raise
        self._after_success(trial)
        return result

    # ── internal ────────────────────────────────────────────

    def _before(self) -> bool:
        """Kembalikan True bila request ini adalah percobaan half-open."""
        with self._lock:
            if self._state == CLOSED:
                return False
            if self._state == OPEN:
                if self._clock() - self._opened_at < self._reset_timeout:
                    BREAKER_REJECTED.labels(self.name).inc()
                    raise BreakerOpen()
                self._set(HALF_OPEN)
            # HALF_OPEN: hanya satu percobaan pada satu waktu
            if self._trial_running:
                BREAKER_REJECTED.labels(self.name).inc()
                raise BreakerOpen()
            self._trial_running = True
            return True

    def _after_success(self, trial: bool) -> None:
        with self._lock:
            if trial:
                self._trial_running = False
                log.info("Breaker %s: percobaan berhasil, kembali normal", self.name)
            self._failures = 0
            if self._state != CLOSED:
                self._set(CLOSED)

    def _after_failure(self, trial: bool, exc: BaseException) -> None:
        with self._lock:
            if trial:
                self._trial_running = False
            if not self._counts(exc):
                # mis. pool penuh atau bug SQL: bukan tanda MySQL sakit.
                # Percobaan half-open yang gagal karena ini tidak memutuskan apa-apa.
                return
            if trial:
                log.warning("Breaker %s: percobaan gagal (%s), terbuka lagi", self.name, exc)
                self._open()
                return
            self._failures += 1
            if self._state == CLOSED and self._failures >= self._threshold:
                log.warning(
                    "Breaker %s: %s kegagalan berturut-turut (%s), terbuka %.0f dtk",
                    self.name, self._failures, exc, self._reset_timeout,
                )
                self._open()

    def _open(self) -> None:
        self._opened_at = self._clock()
        self._set(OPEN)

    def _set(self, state: str) -> None:
        if state != self._state:
            self._state = state
            BREAKER_STATE.labels(self.name).set(_STATE_VALUE[state])
            BREAKER_TRANSITIONS.labels(self.name, state).inc()
