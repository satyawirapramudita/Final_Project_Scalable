"""Helper delay retry dengan jitter.

Retry yang berjalan serentak di banyak proses (consumer RabbitMQ, init
skema saat container start) dengan delay tetap akan "menari" bersama —
thundering herd. Jitter mengacak setiap delay sehingga retry menyebar.

Dua bentuk yang dipakai:
- full_jitter   : backoff eksponensial; delay acak di [0, ceiling].
                  Untuk loop yang semula `2**attempt`.
- equal_jitter  : delay tetap; acak di [delay/2, delay] sehingga rata-rata
                  menunggu tetap sama. Untuk loop yang semula `sleep(delay)`.
"""

from __future__ import annotations

import random
import time
from typing import Callable


def full_jitter(
    attempt: int,
    base: float,
    cap: float,
    *,
    rng: Callable[[], float] = random.random,
) -> float:
    """Delay acak di [0, min(cap, base * 2**(attempt-1))]; attempt mulai dari 1."""
    if attempt < 1:
        raise ValueError("attempt harus mulai dari 1")
    ceiling = min(cap, base * 2 ** (attempt - 1))
    return rng() * ceiling


def equal_jitter(delay: float, *, rng: Callable[[], float] = random.random) -> float:
    """Delay acak di [delay/2, delay]; rata-rata tetap 3/4 dari delay asli."""
    return delay / 2 + rng() * delay / 2


def sleep_backoff(
    attempt: int,
    base: float,
    cap: float,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Tidur sesuai full_jitter — dipakai loop retry eksponensial."""
    sleep(full_jitter(attempt, base, cap))
