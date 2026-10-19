"""Board ops admission guards (board_ops_guard.py): single-use 60 s
confirmation tokens, the per-minute rate limit, the single-execution enqueue
check and the stale reaper with the 'timeout' reason.  The clock is injectable
(``board.now`` replaced by a controllable timestamp) so expiry and staleness
are driven deterministically."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from claude_runner.board_ops_guard import (
    ConfirmTokens,
    EnqueueError,
    RateLimiter,
    RateLimitError,
    TokenError,
    check_can_enqueue,
    expire_stale,
)
from claude_runner.board_ops_store import InMemoryOpsStore


def _store(t: datetime) -> InMemoryOpsStore:
    return InMemoryOpsStore(now_fn=lambda: t.isoformat())


# --------------------------------------------------------------------- ConfirmTokens


def test_token_is_valid_only_once_within_60s_for_the_same_binding():
    base = datetime(2026, 1, 1, 10, 0)
    tokens = ConfirmTokens(now_fn=lambda: base.isoformat())
    token, expires_at, consequences = tokens.issue("stop_mission", None, "alice")

    # expires_at is exactly 60 s after issue
    assert datetime.fromisoformat(expires_at) == base + timedelta(seconds=60)
    # consequences is a non-empty string
    assert consequences and isinstance(consequences, str)

    # valid within 60 s, same action/args/user
    tokens.consume(token, "stop_mission", None, "alice")

    # replay of the same token is refused (single-use)
    with pytest.raises(TokenError):
        tokens.consume(token, "stop_mission", None, "alice")

    # a fresh token for the same binding is independent
    token2, expires_at2, consequences2 = tokens.issue("stop_mission", None, "alice")
    assert expires_at2 == expires_at
    assert consequences2 == consequences
    tokens.consume(token2, "stop_mission", None, "alice")


def test_token_refused_after_60s():
    base = datetime(2026, 1, 1, 10, 0)
    now_fn = lambda: base.isoformat()
    tokens = ConfirmTokens(now_fn=now_fn)
    token, expires_at, _ = tokens.issue("resume", None, "bob")
    # expires_at is exactly 60 s after issue
    assert datetime.fromisoformat(expires_at) == base + timedelta(seconds=60)
    # advance past the 60 s window
    base += timedelta(seconds=61)
    with pytest.raises(TokenError, match="expired"):
        tokens.consume(token, "resume", None, "bob")


def test_token_refused_on_mismatched_action_args_or_user():
    base = datetime(2026, 1, 1, 10, 0)
    tokens = ConfirmTokens(now_fn=lambda: base.isoformat())
    token, _, consequences = tokens.issue("rebuild_service", {"service": "ahawr-dashboard"}, "carol")
    assert consequences  # non-empty consequence string

    # different args
    with pytest.raises(TokenError, match="does not match"):
        tokens.consume(token, "rebuild_service", {"service": "ahawr-retrieval"}, "carol")
    # different user
    token, _, _ = tokens.issue("resume", None, "dave")
    with pytest.raises(TokenError, match="does not match"):
        tokens.consume(token, "resume", None, "eve")
    # different action
    with pytest.raises(TokenError, match="does not match"):
        tokens.consume(token, "pause_at_boundary", None, "dave")


def test_unknown_token_is_refused():
    tokens = ConfirmTokens(now_fn=lambda: datetime(2026, 1, 1, 10, 0).isoformat())
    with pytest.raises(TokenError, match="unknown or already used"):
        tokens.consume("0" * 32, "resume", None, "x")


def test_issue_unknown_action_raises():
    tokens = ConfirmTokens(now_fn=lambda: datetime(2026, 1, 1, 10, 0).isoformat())
    with pytest.raises(ValueError):
        tokens.issue("no_such_action", None, "x")


# ----------------------------------------------------------------------- RateLimiter


def test_rate_limit_exceeding_raises_with_the_current_count():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    # two rows within the window
    store.insert_command("resume", None, "a")
    store.insert_command("send_test_notification", None, "b")
    RateLimiter(3).check(store)  # 2 < 3, ok
    store.insert_command("resume", None, "a")
    # now 3 >= 3
    with pytest.raises(RateLimitError, match="3 command rows in the last 60 s"):
        RateLimiter(3).check(store)


def test_second_concurrent_enqueue_refused_by_the_limit():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    # fill the window to the limit, then a concurrent enqueue is refused
    for i in range(2):
        store.insert_command("resume", None, f"u{i}")
    with pytest.raises(RateLimitError):
        RateLimiter(2).check(store)
    # a later enqueue (same user, one row older) is admitted once the window frees up
    store.insert_command("resume", None, "later")


def test_rate_limit_counts_all_statuses():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    c = store.insert_command("stop_mission", None, "a")
    store.reject_command(c.id, "no mission")  # rejected still counts
    with pytest.raises(RateLimitError):
        RateLimiter(1).check(store)


# ------------------------------------------------------------------ check_can_enqueue


def test_single_execution_action_refused_while_new_or_running():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    store.insert_command("stop_mission", None, "a")  # new
    with pytest.raises(EnqueueError, match="single-execution"):
        check_can_enqueue(store, "stop_mission", None)

    # a running row also refuses
    store = _store(base)
    c = store.insert_command("stop_mission", None, "a")
    store.claim_next()  # now running
    with pytest.raises(EnqueueError):
        check_can_enqueue(store, "stop_mission", None)


def test_repeatable_action_is_never_refused():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    store.insert_command("resume", None, "a")
    store.claim_next()  # running
    check_can_enqueue(store, "resume", None)  # no error


def test_finished_action_allows_reenqueue():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    c = store.insert_command("stop_mission", None, "a")
    store.claim_next()
    store.finish_command(c.id, "done", {"ok": True})
    check_can_enqueue(store, "stop_mission", None)  # done: re-allow


# ----------------------------------------------------------------------- expire_stale


def test_expire_stale_marks_only_commands_older_than_the_timeout():
    # one store per timeline so the injectable clock is a single instant
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    old = store.insert_command("stop_mission", None, "a")
    store.claim_next()  # started at `base`
    # backdate it 700 s (older than the 600 s timeout)
    store._commands[old.id].started_at = (base - timedelta(seconds=700)).isoformat()
    # timeout 600 s marks the 700 s-old row
    stale = expire_stale(store, 600)
    assert len(stale) == 1
    assert stale[0].id == old.id
    assert stale[0].status == "failed"
    assert stale[0].result_json == {"reason": "timeout"}

    # a command started only 100 s ago is not marked at the same timeout
    store2 = _store(base)
    fresh = store2.insert_command("resume", None, "b")
    store2.claim_next()
    store2._commands[fresh.id].started_at = (base - timedelta(seconds=100)).isoformat()
    assert expire_stale(store2, 600) == []
    # and the 100 s-old row is untouched
    assert store2.list_commands(status="running")[0].id == fresh.id


def test_expire_stale_rewrite_of_the_reason():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    c = store.insert_command("stop_mission", None, "a")
    store.claim_next()
    store._commands[c.id].started_at = (base - timedelta(seconds=1000)).isoformat()
    stale = expire_stale(store, 600)
    assert stale[0].result_json == {"reason": "timeout"}


def test_expire_stale_reason_persists_in_store():
    base = datetime(2026, 1, 1, 10, 0)
    store = _store(base)
    c = store.insert_command("stop_mission", None, "a")
    store.claim_next()
    store._commands[c.id].started_at = (base - timedelta(seconds=1000)).isoformat()
    stale = expire_stale(store, 600)
    # re-read through the store, not the returned copy
    reread = store.list_commands(status="failed")
    assert len(reread) == 1
    assert reread[0].id == c.id
    assert reread[0].result_json == {"reason": "timeout"}
