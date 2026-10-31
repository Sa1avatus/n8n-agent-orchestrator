from __future__ import annotations

import pytest

from claude_runner.dashboard_auth_limit import (
    BASE_LOCKOUT_S,
    FORGET_WINDOW_S,
    FREE_ATTEMPTS,
    MAX_ADDRESSES,
    MAX_LOCKOUT_S,
    LoginLimiter,
)


class FakeClock:
    """Injectible clock returning a mutable ``now`` (seconds, float)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_unknown_address_is_allowed() -> None:
    limiter = LoginLimiter(now=FakeClock())
    assert limiter.retry_after_seconds("10.0.0.1") == 0.0


def test_free_attempts_allowed_then_lock() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.2"
    for _ in range(FREE_ATTEMPTS):
        limiter.record_failure(addr)
        assert limiter.retry_after_seconds(addr) == 0.0
    # the next failure crosses the free-attempt threshold
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(BASE_LOCKOUT_S)


def test_each_failure_doubles_up_to_max() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.3"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(BASE_LOCKOUT_S)
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(2 * BASE_LOCKOUT_S)
    # enough further failures to hit the ceiling
    for _ in range(30):
        limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(MAX_LOCKOUT_S)
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(MAX_LOCKOUT_S)


def test_other_addresses_unaffected() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    a = "10.0.0.4"
    b = "10.0.0.5"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(a)
    assert limiter.retry_after_seconds(a) > 0
    assert limiter.retry_after_seconds(b) == 0.0
    limiter.record_failure(b)
    assert limiter.retry_after_seconds(b) == 0.0


def test_success_resets_the_address() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.6"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) > 0
    limiter.record_success(addr)
    assert limiter.retry_after_seconds(addr) == 0.0
    # free attempts are available again after the reset
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == 0.0


def test_block_clears_after_the_delay() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.7"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(addr)
    delay = limiter.retry_after_seconds(addr)
    assert delay == pytest.approx(BASE_LOCKOUT_S)
    clock.now += delay
    assert limiter.retry_after_seconds(addr) == 0.0


def test_failure_during_lockout_keeps_increasing() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.8"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(BASE_LOCKOUT_S)
    # still inside the first lockout; another failure doubles it
    clock.now += 1.0
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(2 * BASE_LOCKOUT_S)


def test_address_cap_evicts_least_recently_used() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    first = "addr-0"
    # lock the first address (touched first, evicted first)
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(first)
    assert limiter.retry_after_seconds(first) > 0
    # fill the remaining slots with fresh, unlocked addresses
    for i in range(1, MAX_ADDRESSES):
        limiter.record_failure(f"addr-{i}")
    # inserting one more past the cap evicts the least-recently-active address
    limiter.record_failure(f"addr-{MAX_ADDRESSES}")
    assert limiter.retry_after_seconds(first) == 0.0


def test_forget_window_forgives_idle_address() -> None:
    clock = FakeClock()
    limiter = LoginLimiter(now=clock)
    addr = "10.0.0.9"
    for _ in range(FREE_ATTEMPTS + 1):
        limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) > 0
    # the lockout itself expires first
    clock.now += BASE_LOCKOUT_S
    assert limiter.retry_after_seconds(addr) == 0.0
    # then the address sits idle past the forget window
    clock.now += FORGET_WINDOW_S
    limiter.retry_after_seconds(addr)
    # the failure counter was reset: free attempts are available again
    for _ in range(FREE_ATTEMPTS):
        limiter.record_failure(addr)
        assert limiter.retry_after_seconds(addr) == 0.0
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) == pytest.approx(BASE_LOCKOUT_S)


def test_default_clock_is_monotonic() -> None:
    limiter = LoginLimiter()
    addr = "127.0.0.1"
    assert limiter.retry_after_seconds(addr) == 0.0
    for _ in range(FREE_ATTEMPTS):
        limiter.record_failure(addr)
    limiter.record_failure(addr)
    assert limiter.retry_after_seconds(addr) > 0.0
