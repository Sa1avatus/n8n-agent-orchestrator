"""Tests for the guard framework (guards.py): supervision, dry-run, rate
limiting, state snapshot, and the no-secret log/state guarantees.

No network is used: Telegram is replaced by an in-memory fake transport and
the notifier's real HTTP path is never exercised.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from claude_runner.guards import (
    Finding,
    Guard,
    GuardSettings,
    GuardSupervisor,
    NoticeLimiter,
    Notifier,
    SupervisorSnapshot,
)

TOKEN = "7777777777-AbCdEfGhIjK"
CHAT_ID = "999999"


class FiringGuard(Guard):
    """A guard that succeeds, reports one finding, and performs one action."""

    def __init__(self, name: str = "firing", interval: float = 0.01) -> None:
        super().__init__(name, interval)
        self.act_calls = 0

    async def check(self) -> list[Finding]:
        return [Finding("one finding")]

    async def act(self, findings: list[Finding]) -> str | None:
        self.act_calls += 1
        return f"acted on {len(findings)}"


class FailingGuard(Guard):
    """A guard whose check raises every time."""

    def __init__(self, name: str = "bad") -> None:
        super().__init__(name)

    async def check(self) -> list[Finding]:
        raise RuntimeError("check failed")


class FlakyGuard(Guard):
    """A guard that fails once and then succeeds."""

    def __init__(self, name: str = "flaky", interval: float = 0.01) -> None:
        super().__init__(name, interval)
        self.check_count = 0

    async def check(self) -> list[Finding]:
        self.check_count += 1
        if self.check_count == 1:
            raise RuntimeError("check failed once")
        return []


class QuietGuard(Guard):
    """A guard with no findings and no action, so it never notifies."""

    def __init__(self, name: str = "quiet", interval: float = 0.01) -> None:
        super().__init__(name, interval)

    async def check(self) -> list[Finding]:
        return []


async def _fake_transport(captured: list[str]) -> bool:
    async def transport(text: str) -> bool:
        captured.append(text)
        return True

    return transport


def _supervisor(
    settings: GuardSettings,
    guards: list[Guard],
    notifier: Notifier | None = None,
) -> GuardSupervisor:
    return GuardSupervisor(settings, guards, notifier)


# --- off by default ---

async def test_off_by_default_starts_no_tasks() -> None:
    settings = GuardSettings.from_env({})
    sup = _supervisor(settings, [FiringGuard(), FailingGuard()])
    await sup.start()
    # with DASHBOARD_GUARDS unset no per-guard task is created
    assert not sup._tasks
    assert sup.state()["guards"]["firing"]["last_run"] == 0.0
    await sup.stop()


async def test_off_by_default_never_checks() -> None:
    settings = GuardSettings.from_env({})
    sup = _supervisor(settings, [FiringGuard(), FailingGuard()])
    await sup.start()
    await asyncio.sleep(0.05)
    await sup.stop()
    snap = sup.state()
    for name in ("firing", "bad"):
        assert snap["guards"][name]["error_count"] == 0
        assert snap["guards"][name]["last_run"] == 0.0
        assert snap["guards"][name]["status"] == "ok"


# --- exception isolation and backoff ---

async def test_exception_isolated_and_backed_off(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.01)
    settings = GuardSettings.from_env({"DASHBOARD_GUARDS": "1"})
    good = FiringGuard("good")
    bad = FailingGuard("bad")
    sup = _supervisor(settings, [good, bad])
    await sup.start()
    with caplog.at_level(logging.INFO, logger="claude_runner.guards"):
        await asyncio.sleep(0.1)
        await sup.stop()
    snap = sup.state()
    # the failing guard is in error status with at least one recorded error
    assert snap["guards"]["bad"]["status"] == "error"
    assert snap["guards"]["bad"]["error_count"] >= 1
    # the other guard is unaffected by the failure
    assert snap["guards"]["good"]["error_count"] == 0
    assert snap["guards"]["good"]["status"] == "ok"
    assert snap["guards"]["good"]["last_result"] == "findings=1"
    # the failure was logged with its backoff, without any secret
    assert any("check failed" in r.message for r in caplog.records)
    for rec in caplog.records:
        assert TOKEN not in rec.message
        assert CHAT_ID not in rec.message


async def test_failing_guard_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.01)
    settings = GuardSettings.from_env({"DASHBOARD_GUARDS": "1"})
    flaky = FlakyGuard("flaky")
    sup = _supervisor(settings, [flaky])
    await sup.start()
    await asyncio.sleep(0.2)
    await sup.stop()
    snap = sup.state()
    assert snap["guards"]["flaky"]["status"] == "ok"
    assert snap["guards"]["flaky"]["error_count"] == 1
    # after the single failure it keeps checking and recovers
    assert flaky.check_count > 2


# --- dry-run ---

async def test_dry_run_records_without_acting() -> None:
    settings = GuardSettings.from_env(
        {"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_DRY_RUN": "1"}
    )
    good = FiringGuard("good")
    sup = _supervisor(settings, [good])
    await sup.start()
    await asyncio.sleep(0.05)
    await sup.stop()
    assert good.act_calls == 0
    action = str(good.state.last_action or "")
    assert action.startswith("dry-run:")
    assert "would act on 1 finding(s)" in action


async def test_dry_run_is_carried_in_the_snapshot() -> None:
    settings = GuardSettings.from_env(
        {"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_DRY_RUN": "1"}
    )
    sup = _supervisor(settings, [FiringGuard()])
    await sup.start()
    assert sup.state()["dry_run"] is True
    await sup.stop()


# --- notice rate limit ---

async def test_notice_rate_limit_caps_one_per_minute() -> None:
    settings = GuardSettings.from_env({"DASHBOARD_GUARDS": "1"})
    captured: list[str] = []
    notifier = Notifier(
        env={},
        transport=await _fake_transport(captured),
    )
    firing = FiringGuard("firing")
    quiet = QuietGuard("quiet")
    sup = _supervisor(settings, [firing, quiet], notifier)
    sup.limiter._window = 1000.0
    await sup.start()
    await asyncio.sleep(0.2)
    await sup.stop()
    # the quiet guard never notifies, the firing guard is capped to one notice
    assert captured == ["guard firing: acted on 1"]


async def test_notice_limiter_allows_again_after_window() -> None:
    limiter = NoticeLimiter(1.0)
    base = time.monotonic()
    # pin the window so the offsets below are deterministic
    limiter._window = base
    assert limiter.allow(now=base) is True
    # a second notice within the same minute is denied
    assert limiter.allow(now=base + 10.0) is False
    # a full minute later the window resets and notices are allowed again
    assert limiter.allow(now=base + 70.0) is True


# --- state snapshot fields ---

async def test_state_snapshot_has_expected_fields() -> None:
    settings = GuardSettings.from_env({})
    sup = _supervisor(settings, [FiringGuard("firing"), FailingGuard("bad")])
    await sup.start()
    snap = sup.state()
    assert isinstance(snap, SupervisorSnapshot)
    expected = {"last_run", "last_result", "last_action", "error_count", "status"}
    for guard_state in snap["guards"].values():
        assert set(guard_state) == expected
    # the supervisor-level fields are present too
    assert set(snap) == {"dry_run", "running", "guards"}
    await sup.stop()


async def test_snapshot_fields_after_activity() -> None:
    settings = GuardSettings.from_env({"DASHBOARD_GUARDS": "1"})
    good = FiringGuard("good")
    bad = FailingGuard("bad")
    sup = _supervisor(settings, [good, bad])
    await sup.start()
    await asyncio.sleep(0.1)
    await sup.stop()
    snap = sup.state()
    good_state = snap["guards"]["good"]
    assert good_state["last_run"] > 0
    assert good_state["last_result"] == "findings=1"
    assert good_state["last_action"] == "acted on 1"
    assert good_state["error_count"] == 0
    assert good_state["status"] == "ok"
    bad_state = snap["guards"]["bad"]
    assert bad_state["error_count"] == 1
    assert bad_state["status"] == "error"
    assert bad_state["last_result"] is None


# --- notifier falls back to the log when the variables are missing ---

async def test_notifier_falls_back_to_log_when_vars_missing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notifier = Notifier(env={})
    with caplog.at_level(logging.INFO, logger="claude_runner.guards"):
        ok = await notifier.send("guard bad: acted")
    assert ok
    assert any("telegram variables missing" in r.message for r in caplog.records)
    assert any("guard bad: acted" in r.message for r in caplog.records)


async def test_notifier_uses_injected_transport_and_never_exposes_secrets() -> None:
    captured: list[str] = []
    transport = await _fake_transport(captured)
    notifier = Notifier(
        env={
            "DASHBOARD_TELEGRAM_BOT_TOKEN": TOKEN,
            "DASHBOARD_TELEGRAM_CHAT_ID": CHAT_ID,
        },
        transport=transport,
    )
    assert notifier.telegram_configured() is True
    ok = await notifier.send("guard good: acted on 1")
    assert ok
    # the transport only ever receives the text, never the token or chat id
    assert captured == ["guard good: acted on 1"]
    for blob in captured:
        assert TOKEN not in blob
        assert CHAT_ID not in blob


# --- no made-up secret appears in logs or state ---

async def test_no_secret_in_logs_or_state(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.01)
    settings = GuardSettings.from_env(
        {
            "DASHBOARD_GUARDS": "1",
            "DASHBOARD_TELEGRAM_BOT_TOKEN": TOKEN,
            "DASHBOARD_TELEGRAM_CHAT_ID": CHAT_ID,
        }
    )
    captured: list[str] = []
    notifier = Notifier(
        env={
            "DASHBOARD_TELEGRAM_BOT_TOKEN": TOKEN,
            "DASHBOARD_TELEGRAM_CHAT_ID": CHAT_ID,
        },
        transport=await _fake_transport(captured),
    )
    good = FiringGuard("good")
    bad = FailingGuard("bad")
    sup = _supervisor(settings, [good, bad], notifier)
    sup.limiter._window = 1000.0
    await sup.start()
    with caplog.at_level(logging.INFO, logger="claude_runner.guards"):
        await asyncio.sleep(0.15)
        await sup.stop()
    snap = sup.state()
    # no secret in any log record, transport payload, or state field
    for rec in caplog.records:
        assert TOKEN not in rec.message
        assert CHAT_ID not in rec.message
    for blob in captured:
        assert TOKEN not in blob
        assert CHAT_ID not in blob
    for guard_state in snap["guards"].values():
        for value in guard_state.values():
            assert TOKEN not in str(value)
            assert CHAT_ID not in str(value)
