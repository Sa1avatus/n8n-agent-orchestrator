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


def settings_from(env: dict[str, str]) -> GuardSettings:
    return GuardSettings.from_env(env)


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
