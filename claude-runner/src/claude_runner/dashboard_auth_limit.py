"""Per-address login attempt limiter with exponential backoff for the dashboard.

After ``FREE_ATTEMPTS`` failures from one address, further attempts are locked
out.  Each subsequent failure doubles the lockout duration (capped at
``MAX_LOCKOUT_S``).  A successful login resets the address, the block clears on
its own once the delay elapses, addresses idle longer than ``FORGET_WINDOW_S``
are forgotten, and at most ``MAX_ADDRESSES`` addresses are tracked (the
least-recently-active are evicted).  Standard library only.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

# Free attempts before the first lockout.
FREE_ATTEMPTS = 5
# First lockout in seconds after the free attempts are used up; doubles per
# further failure until ``MAX_LOCKOUT_S``.
BASE_LOCKOUT_S = 10.0
# Hard ceiling on the lockout length.
MAX_LOCKOUT_S = 3600.0
# Bounded memory: at most this many addresses tracked at once.
MAX_ADDRESSES = 1000
# Forget an idle address (no failures/successes) after this many seconds.
FORGET_WINDOW_S = 3600.0


@dataclass
class _Entry:
    failures: int
    last_seen: float
    lockout_until: float


class LoginLimiter:
    """Track login failures per client address with exponential backoff."""

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._entries: OrderedDict[str, _Entry] = OrderedDict()

    def retry_after_seconds(self, addr: str) -> float:
        """Seconds the address must wait before a fresh attempt; 0 if allowed."""
        now = self._now()
        self._prune(now)
        entry = self._entries.get(addr)
        if entry is None:
            return 0.0
        return max(entry.lockout_until - now, 0.0)

    def record_failure(self, addr: str) -> None:
        """Record a failed login attempt for *addr*, locking it if escalated."""
        now = self._now()
        self._prune(now)
        entry = self._touch(addr, now)
        entry.failures += 1
        delay = self._lockout_delay(entry.failures)
        if delay > 0.0:
            entry.lockout_until = now + delay

    def record_success(self, addr: str) -> None:
        """Clear a successful login: reset the address's failure history."""
        now = self._now()
        self._prune(now)
        self._entries.pop(addr, None)

    @staticmethod
    def _lockout_delay(failures: int) -> float:
        # post_free is 1-indexed: the first post-free failure yields the base
        # delay, the next doubles it, and so on, capped at the maximum.
        if failures <= FREE_ATTEMPTS:
            return 0.0
        post_free = failures - FREE_ATTEMPTS
        return min(BASE_LOCKOUT_S * (2.0 ** (post_free - 1)), MAX_LOCKOUT_S)

    def _touch(self, addr: str, now: float) -> _Entry:
        # Refresh recency for an existing address (LRU ordering); insert a new
        # entry, evicting the least-recently-active address past the cap.
        if addr in self._entries:
            entry = self._entries.pop(addr)
            entry.last_seen = now
            self._entries[addr] = entry
            return entry
        while len(self._entries) >= MAX_ADDRESSES:
            self._entries.popitem(last=False)
        entry = _Entry(failures=0, last_seen=now, lockout_until=0.0)
        self._entries[addr] = entry
        return entry

    def _prune(self, now: float) -> None:
        # Forget idle, non-locked addresses past the forget window.  A locked
        # entry is never pruned here: its lockout must run its full course.
        stale = [
            addr
            for addr, entry in self._entries.items()
            if entry.lockout_until <= now and now - entry.last_seen > FORGET_WINDOW_S
        ]
        for addr in stale:
            del self._entries[addr]
