"""Uji status circuit breaker pencarian dengan jam palsu (tanpa MySQL)."""

import pytest

from services.search.app.breaker import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    BreakerOpen,
    CircuitBreaker,
)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Sick(Exception):
    pass


class Busy(Exception):
    pass


def boom(exc=Sick):
    def fn():
        raise exc()
    return fn


@pytest.fixture
def setup():
    clock = Clock()
    br = CircuitBreaker(
        "test",
        failure_threshold=3,
        reset_timeout=10,
        counts_as_failure=lambda e: isinstance(e, Sick),
        clock=clock,
    )
    return br, clock


def fail(br, n, exc=Sick):
    for _ in range(n):
        with pytest.raises(exc):
            br.call(boom(exc))


def test_opens_after_threshold_and_rejects_without_calling(setup):
    br, _ = setup
    fail(br, 2)
    assert br.state == CLOSED
    fail(br, 1)
    assert br.state == OPEN
    called = []
    with pytest.raises(BreakerOpen):
        br.call(lambda: called.append(1))
    assert called == []  # dependensi tidak disentuh


def test_success_resets_consecutive_count(setup):
    br, _ = setup
    fail(br, 2)
    assert br.call(lambda: "ok") == "ok"
    fail(br, 2)
    assert br.state == CLOSED


def test_ignored_errors_do_not_open(setup):
    br, _ = setup
    fail(br, 10, Busy)
    assert br.state == CLOSED


def test_half_open_trial_success_closes(setup):
    br, clock = setup
    fail(br, 3)
    clock.t = 10
    assert br.state == HALF_OPEN
    assert br.call(lambda: 42) == 42
    assert br.state == CLOSED


def test_half_open_trial_failure_reopens_for_full_timeout(setup):
    br, clock = setup
    fail(br, 3)
    clock.t = 10
    fail(br, 1)  # satu kegagalan percobaan langsung membuka lagi
    assert br.state == OPEN
    clock.t = 19
    with pytest.raises(BreakerOpen):
        br.call(lambda: 1)
    clock.t = 20
    assert br.call(lambda: 1) == 1


def test_only_one_trial_at_a_time(setup):
    br, clock = setup
    fail(br, 3)
    clock.t = 10
    inner = []

    def trial():
        # request kedua yang datang saat percobaan berjalan harus ditolak
        with pytest.raises(BreakerOpen):
            br.call(lambda: inner.append("second"))
        return "first"

    assert br.call(trial) == "first"
    assert inner == []
    assert br.state == CLOSED


def test_ignored_error_during_trial_releases_trial_slot(setup):
    br, clock = setup
    fail(br, 3)
    clock.t = 10
    fail(br, 1, Busy)  # pool penuh saat percobaan: tidak memutuskan apa-apa
    assert br.call(lambda: "ok") == "ok"
    assert br.state == CLOSED
