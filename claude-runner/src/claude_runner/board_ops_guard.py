"""Board ops admission guards: confirmation tokens, rate limit, the
single-running-command check and stale reaping.

Pure functions and small state objects over the store layer
(``board_ops_store.OpsStore``); no HTTP here.  The time source is injectable
(``now_fn``) so tests drive the expiry and staleness deterministically; the
default is ``board.now`` (UTC, microseconds).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from . import board_ops_actions
from .board import now
from .board_ops_store import OpsStore

__all__ = [
    "ConfirmTokens",
    "RateLimiter",
    "check_can_enqueue",
    "expire_stale",
]

#: A confirmation token is valid for 60 s and is single-use.
TOKEN_TTL_S = 60.0


class TokenError(RuntimeError):
    """A confirmation token is expired, unknown or already consumed."""

    code = "token"
    status_code = 400


class RateLimitError(RuntimeError):
    """More than ``n`` command rows were requested in the last 60 s."""

    code = "rate_limit"
    status_code = 429


class EnqueueError(RuntimeError):
    """The command is refused because a row for this action is already
    queued or running (single-execution actions cannot repeat while
    pending)."""

    code = "enqueued"
    status_code = 409


class ConfirmTokens:
    """Issue and consume single-use confirmation tokens.

    ``issue`` returns ``(token, expires_at, consequences)`` where the token is
    bound to the ``action``, ``args`` and ``user`` that asked for it and stays
    pending in memory for 60 s.  ``consume`` removes the pending entry only
    when the token is present, unexpired and bound to the same
    ``action``/``args``/``user``; anything else raises :class:`TokenError`
    ("expired", "unknown" or "does not match").  A token is single-use: the
    pending entry is removed before the caller may write the command row, so a
    replayed token is "unknown" (already used)."""

    def __init__(
        self,
        now_fn: Callable[[], str] | None = None,
        ttl_s: float = TOKEN_TTL_S,
    ) -> None:
        self._lock = threading.Lock()
        self._now_fn = now_fn or now
        self._ttl_s = ttl_s
        # token -> (action, args dict copy, user, issued_at)
        self._pending: dict[str, tuple[str, dict[str, Any], str, str]] = {}

    def _prune(self, now_dt: datetime) -> None:
        expired = [
            token
            for token, (_, _, _, issued_at) in self._pending.items()
            if (now_dt - datetime.fromisoformat(issued_at)).total_seconds() > self._ttl_s
        ]
        for token in expired:
            del self._pending[token]

    def issue(
        self, action: str, args: dict[str, Any] | None, user: str
    ) -> tuple[str, str, str]:
        """Issue a pending confirmation token bound to ``action``/``args``/``user``.

        Returns ``(token, expires_at, consequences)`` where ``expires_at`` is
        ``issued_at + ttl_s`` and ``consequences`` is the action's consequence
        string for the given args.  Unknown actions raise ``ValueError``."""
        args = dict(args) if args is not None else {}
        entry = board_ops_actions.ACTIONS.get(action)
        if entry is None:
            raise ValueError(f"unknown action {action!r}")
        issued_at = self._now_fn()
        token = uuid.uuid4().hex[:32]
        with self._lock:
            now_dt = datetime.fromisoformat(issued_at)
            self._prune(now_dt)
            self._pending[token] = (action, args, user, issued_at)
        expires_at = (
            datetime.fromisoformat(issued_at) + timedelta(seconds=self._ttl_s)
        ).isoformat()
        consequences = entry["consequences"](args)
        return token, expires_at, consequences

    def consume(
        self, token: str, action: str, args: dict[str, Any] | None, user: str
    ) -> None:
        """Consume the token; raises :class:`TokenError` on expired, unknown,
        reused or mismatched tokens."""
        args = dict(args) if args is not None else {}
        with self._lock:
            now_dt = datetime.fromisoformat(self._now_fn())
            # Evaluate the token's own expiry before the side-prune, so an
            # expired-but-present token is reported as "expired" (not dropped
            # into the "unknown" bucket by the prune pass).
            entry = self._pending.get(token)
            if entry is None:
                raise TokenError("token unknown or already used")
            bound_action, bound_args, bound_user, issued_at = entry
            if (now_dt - datetime.fromisoformat(issued_at)).total_seconds() > self._ttl_s:
                del self._pending[token]
                raise TokenError("token expired")
            if bound_action != action or bound_args != args or bound_user != user:
                raise TokenError("token does not match the request")
            del self._pending[token]  # single-use: consumed before the row is inserted
            self._prune(now_dt)  # side cleanup of other lapsed entries


class RateLimiter:
    """At most ``n`` command rows per 60 s, counted by
    ``store.count_recent(60)`` (every status is included)."""

    def __init__(self, n: int) -> None:
        self._n = n

    def check(self, store: OpsStore) -> None:
        """Raise :class:`RateLimitError` with the current count when the limit
        is already reached."""
        count = store.count_recent(60)
        if count >= self._n:
            raise RateLimitError(
                f"rate limit: {count} command rows in the last 60 s "
                f"(limit {self._n})"
            )


def check_can_enqueue(
    store: OpsStore, action: str, args: dict[str, Any] | None
) -> None:
    """Refuse a single-execution action (``repeatable == False``) while a row
    for it is still ``new`` or ``running``; repeatable actions are never
    refused.  Raises :class:`EnqueueError`."""
    entry = board_ops_actions.ACTIONS.get(action)
    if entry is None or entry["repeatable"]:
        return
    for c in store.list_commands(action=action):
        if c.status in ("new", "running"):
            raise EnqueueError(
                f"action {action!r} already has a command {c.status!r} "
                f"(id {c.id}); single-execution actions cannot be enqueued twice"
            )


def expire_stale(store: OpsStore, timeout_s: float) -> list:
    """Mark ``running`` commands older than ``timeout_s`` as ``failed`` with
    the reason 'timeout' and return them (the store's stale reaper)."""
    return store.mark_stale(timeout_s)
