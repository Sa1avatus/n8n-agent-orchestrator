"""Tests for the guard framework core (guards.py)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from claude_runner.guards import (
    Finding,
    Guard,
    GuardSettings,
    GuardSupervisor,
    Notifier,
)


class FailingGuard(Guard):
    """A guard whose check raises every time."""

    def __init__(self, name: str = "bad") -> None:
        super().__init__(name)

    async def check(self) -> list[Finding]:
        raise RuntimeError("check failed")


class OkGuard(Guard):
    """A guard whose check succeeds and reports one finding."""

    def __init__(self, name: str = "good", findings: int = 1) -> None:
        super().__init__(name, interval=0.01)
        self._findings = findings
        self.act_calls = 0
        self.check_count = 0

    async def check(self) -> list[Finding]:
        self.check_count += 1
        return [Finding(f"f{i}") for i in range(self._findings)]

    async def act(self, findings: list[Finding]) -> str | None:
        self.act_calls += 1
        return "acted"


class FlakyGuard(Guard):
    """A guard whose check fails once and then succeeds."""

    def __init__(self, name: str = "flaky") -> None:
        super().__init__(name, interval=0.01)
        self.check_count = 0

    async def check(self) -> list[Finding]:
        self.check_count += 1
        if self.check_count == 1:
            raise RuntimeError("check failed once")
        return []


class ScriptedGuard(Guard):
    """A guard whose checks raise for each ``True`` outcome, then succeed."""

    def __init__(self, name: str, errors: list[bool]) -> None:
        super().__init__(name, interval=0.01)
        self._errors = list(errors)

    async def check(self) -> list[Finding]:
        if self._errors and self._errors.pop(0):
            raise RuntimeError("check failed")
        return []


def settings_from(env: dict[str, str]) -> GuardSettings:
    return GuardSettings.from_env(env)


def capturing_notifier() -> tuple[Notifier, list[str]]:
    sent: list[str] = []

    async def transport(text: str) -> bool:
        sent.append(text)
        return True

    return Notifier(env={}, transport=transport), sent


async def test_no_tasks_when_master_off() -> None:
    sup = GuardSupervisor(settings_from({}), [OkGuard(), FailingGuard()])
    await sup.start()
    assert not sup._tasks
    await sup.stop()


async def test_error_isolated_with_backoff() -> None:
    settings = settings_from({"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_DRY_RUN": "1"})
    sup = GuardSupervisor(settings, [OkGuard("good"), FailingGuard("bad")])
    await sup.start()
    await asyncio.sleep(0.1)
    await sup.stop()
    snap = sup.state()
    assert snap["guards"]["bad"]["error_count"] == 1
    assert snap["guards"]["bad"]["status"] == "error"
    # the other guard is unaffected: no errors, a check ran
    assert snap["guards"]["good"]["error_count"] == 0
    assert snap["guards"]["good"]["last_result"] is not None
    # dry-run records the intended action instead of calling act()
    assert "dry-run" in str(snap["guards"]["good"]["last_action"])


async def test_dry_run_records_without_acting() -> None:
    settings = settings_from({"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_DRY_RUN": "1"})
    good = OkGuard("good")
    sup = GuardSupervisor(settings, [good])
    await sup.start()
    await asyncio.sleep(0.05)
    await sup.stop()
    assert good.act_calls == 0
    assert str(good.state.last_action or "").startswith("dry-run:")


async def test_notifier_never_leaks_token_or_chat_id() -> None:
    token = "1234567890-AbCdEfGhIjK"
    chat_id = "424242"
    captured: list[str] = []

    async def transport(text: str) -> bool:
        captured.append(text)
        return True

    notifier = Notifier(
        env={
            "DASHBOARD_TELEGRAM_BOT_TOKEN": token,
            "DASHBOARD_TELEGRAM_CHAT_ID": chat_id,
        },
        transport=transport,
    )
    assert notifier.telegram_configured()
    ok = await notifier.send("guard bad: acted")
    assert ok
    assert captured == ["guard bad: acted"]
    for blob in captured:
        assert token not in blob
        assert chat_id not in blob
    # nothing secret is exposed in the configured flag
    assert notifier.telegram_configured() is True
    assert token not in repr(notifier.telegram_configured())
    assert chat_id not in repr(notifier.telegram_configured())


async def test_notifier_falls_back_to_log_when_vars_missing(
    caplog: pytest.LogCaptureFixture
) -> None:
    notifier = Notifier(env={})
    with caplog.at_level(logging.INFO, logger="claude_runner.guards"):
        ok = await notifier.send("guard bad: acted")
    assert ok
    assert any("telegram variables missing" in rec.message for rec in caplog.records)
    assert not any("1234567890" in rec.message for rec in caplog.records)


async def test_per_guard_enabled_and_interval() -> None:
    settings = settings_from(
        {
            "DASHBOARD_GUARDS": "1",
            "DASHBOARD_GUARDS_ORPHAN_ENABLED": "0",
            "DASHBOARD_GUARDS_QUEUE_INTERVAL": "25",
        }
    )
    assert settings.enabled("orphan") is False
    assert settings.interval("orphan") == 60.0
    assert settings.enabled("queue") is True
    assert settings.interval("queue") == 25.0
    assert settings.enabled("unset") is True
    assert settings.interval("unset") == 60.0


async def test_failing_guard_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.01)
    settings = settings_from({"DASHBOARD_GUARDS": "1"})
    flaky = FlakyGuard("flaky")
    sup = GuardSupervisor(settings, [flaky])
    await sup.start()
    await asyncio.sleep(0.2)
    await sup.stop()
    snap = sup.state()
    assert snap["guards"]["flaky"]["status"] == "ok"
    assert snap["guards"]["flaky"]["error_count"] == 1
    # failure, backoff sleep, then several successful checks
    assert flaky.check_count > 2


def test_dry_run_is_off_by_default() -> None:
    settings = settings_from({"DASHBOARD_GUARDS": "1"})
    assert settings.dry_run is False
    assert settings.dry_run_for("good") is False


def test_dry_run_can_be_set_per_guard() -> None:
    settings = settings_from({"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_GOOD_DRY_RUN": "1"})
    assert settings.dry_run_for("good") is True
    assert settings.dry_run_for("other") is False
    assert settings.dry_run is False


def test_state_exposes_enabled_and_dry_run_per_guard() -> None:
    settings = settings_from({"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_GOOD_DRY_RUN": "1"})
    sup = GuardSupervisor(settings, [OkGuard("good")])
    guard_state = sup.state()["guards"]["good"]
    assert guard_state["dry_run"] is True
    assert guard_state["enabled"] is True
    assert "error_count" in guard_state
    assert "last_run" in guard_state
    assert "last_result" in guard_state


def test_build_guards_registers_all_five_when_master_on() -> None:
    from claude_runner.dashboard import _build_guards

    notifier = Notifier(env={})
    off = settings_from({})
    assert _build_guards(off, notifier) == []
    on = settings_from({"DASHBOARD_GUARDS": "1"})
    names = [g.name for g in _build_guards(on, notifier)]
    assert names == ["idle", "mission_notice", "queue_restart",
                     "host_heartbeat", "tg_relay"]


async def test_dry_run_logs_notice_without_sending(caplog: pytest.LogCaptureFixture) -> None:
    token = "1234567890-AbCdEfGhIjK"
    chat_id = "424242"
    sent: list[str] = []

    async def transport(text: str) -> bool:
        sent.append(text)
        return True

    notifier = Notifier(
        env={"DASHBOARD_TELEGRAM_BOT_TOKEN": token, "DASHBOARD_TELEGRAM_CHAT_ID": chat_id},
        transport=transport,
    )
    settings = settings_from({"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_DRY_RUN": "1"})
    sup = GuardSupervisor(settings, [OkGuard("good")], notifier)
    with caplog.at_level(logging.INFO, logger="claude_runner.guards"):
        await sup.start()
        await asyncio.sleep(0.05)
        await sup.stop()
    assert sent == []
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("dry-run" in m and "guard good" in m for m in messages)
    assert not any(token in m or chat_id in m for m in messages)


async def test_three_consecutive_errors_disable_guard_with_one_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.001)
    notifier, sent = capturing_notifier()
    bad = FailingGuard("bad")
    sup = GuardSupervisor(settings_from({"DASHBOARD_GUARDS": "1"}), [bad], notifier)
    await sup.start()
    for _ in range(300):
        if not bad.state.enabled:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    await sup.stop()
    assert bad.state.error_count == 3
    assert bad.state.enabled is False
    assert sup.state()["guards"]["bad"]["enabled"] is False
    assert len(sent) == 1
    assert sent[0] == "guard bad: disabled after 3 errors in a row"


async def test_success_resets_the_error_streak(monkeypatch: pytest.MonkeyPatch) -> None:
    import claude_runner.guards as guards_module

    monkeypatch.setattr(guards_module, "BACKOFF_BASE_S", 0.001)
    notifier, sent = capturing_notifier()
    flaky = ScriptedGuard("flaky", [True, True, False, True, True, False])
    sup = GuardSupervisor(settings_from({"DASHBOARD_GUARDS": "1"}), [flaky], notifier)
    await sup.start()
    await asyncio.sleep(0.3)
    await sup.stop()
    assert flaky.state.error_count == 4
    assert flaky.state.consecutive_errors == 0
    assert flaky.state.enabled is True
    assert sent == []
