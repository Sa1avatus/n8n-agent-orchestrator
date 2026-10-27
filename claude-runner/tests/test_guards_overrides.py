"""Run-time changes of a guard (enabled, interval, health window): validation, persistence, the
task being started, stopped and restarted, and the PUT /api/guards/{name} route."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_runner.guards import Finding, Guard, GuardOverrides, GuardSettings, GuardSupervisor
from claude_runner.guards_history import GuardHistory

from .test_dashboard import dashboard

HEADER = {"X-AHAWR-Dashboard": "1"}


class CountingGuard(Guard):
    def __init__(self, name: str = "counting", interval: float = 0.01) -> None:
        super().__init__(name, interval)
        self.checks = 0

    async def check(self) -> list[Finding]:
        self.checks += 1
        return []


def _settings(tmp_path: Path, **env: str) -> GuardSettings:
    return GuardSettings.from_env(
        {"DASHBOARD_GUARDS": "1", "DASHBOARD_GUARDS_STATE_DIR": str(tmp_path), **env}
    )


async def test_an_override_stops_and_starts_the_task(tmp_path: Path) -> None:
    guard = CountingGuard()
    sup = GuardSupervisor(_settings(tmp_path), [guard])
    await sup.start()
    await sup.apply_override("counting", enabled=False)
    seen = guard.checks
    await asyncio.sleep(0.05)
    assert guard.checks == seen
    assert sup.state()["guards"]["counting"]["enabled"] is False
    await sup.apply_override("counting", enabled=True)
    await asyncio.sleep(0.05)
    assert guard.checks > seen
    await sup.stop()


async def test_an_env_disabled_guard_can_be_enabled_by_override(tmp_path: Path) -> None:
    guard = CountingGuard()
    sup = GuardSupervisor(_settings(tmp_path, DASHBOARD_GUARDS_COUNTING_ENABLED="0"), [guard])
    await sup.start()
    assert sup.state()["guards"]["counting"]["enabled"] is False
    await sup.apply_override("counting", enabled=True)
    await asyncio.sleep(0.05)
    assert guard.checks > 0
    await sup.stop()


async def test_the_master_switch_stays_stronger_than_an_override(tmp_path: Path) -> None:
    guard = CountingGuard()
    sup = GuardSupervisor(GuardSettings.from_env({}), [guard])
    await sup.start()
    await sup.apply_override("counting", enabled=True)
    assert sup.effective_enabled("counting") is False
    await sup.stop()


async def test_an_interval_override_takes_effect_at_once(tmp_path: Path) -> None:
    guard = CountingGuard(interval=3600.0)
    sup = GuardSupervisor(_settings(tmp_path), [guard])
    await sup.start()
    await asyncio.sleep(0.03)
    assert guard.checks == 1
    await sup.apply_override("counting", interval=0.0 + 1.0)
    assert sup.state()["guards"]["counting"]["interval_s"] == 1.0
    await asyncio.sleep(0.05)
    assert guard.checks == 2
    await sup.stop()


@pytest.mark.parametrize(
    "fields",
    [
        {"interval": 0.5},
        {"interval": 100000},
        {"interval": "10"},
        {"interval": True},
        {"enabled": "yes"},
        {"health_window": "2h"},
    ],
)
async def test_invalid_values_are_refused_and_nothing_is_saved(
    tmp_path: Path, fields: dict[str, object]
) -> None:
    sup = GuardSupervisor(_settings(tmp_path), [CountingGuard()])
    with pytest.raises(ValueError):
        await sup.apply_override("counting", **fields)  # type: ignore[arg-type]
    assert sup.overrides.get("counting") == {}


async def test_an_unknown_guard_is_refused(tmp_path: Path) -> None:
    sup = GuardSupervisor(_settings(tmp_path), [CountingGuard()])
    with pytest.raises(KeyError):
        await sup.apply_override("nope", enabled=True)


def test_overrides_survive_a_restart_and_a_broken_file_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "guards_overrides.json"
    first = GuardOverrides(str(path))
    first.update("a", {"interval": 5.0})
    assert GuardOverrides(str(path)).get("a") == {"interval": 5.0}
    path.write_text("{not json", encoding="utf-8")
    assert GuardOverrides(str(path)).get("a") == {}


def test_health_counts_checks_and_errors_inside_the_window() -> None:
    history = GuardHistory()
    now = 100000.0
    history.record("g", "ok", None, 1, now=now - 7200)
    history.record("g", "ok", None, 1, now=now - 60)
    history.record("g", "error", None, 1, now=now - 30)
    assert history.health("g", "1h", now=now) == {
        "window": "1h", "checks": 2, "errors": 1, "ok_pct": 50.0,
    }
    assert history.health("g", "24h", now=now)["checks"] == 3
    assert history.health("none", "1h", now=now)["ok_pct"] is None


def test_the_route_needs_the_edit_flag_and_the_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DASHBOARD_GUARDS", "1")
    monkeypatch.setenv("DASHBOARD_GUARDS_STATE_DIR", str(tmp_path))
    with dashboard(client) as dash:
        dash.app.state.guards.guards.append(CountingGuard("counting", interval=3600.0))
        body = {"interval_s": 30}
        assert dash.put("/api/guards/counting", json=body, headers=HEADER).status_code == 403
    monkeypatch.setenv("DASHBOARD_GUARDS_EDIT", "1")
    with dashboard(client) as dash:
        dash.app.state.guards.guards.append(CountingGuard("counting", interval=3600.0))
        assert dash.put("/api/guards/counting", json=body).status_code == 403
        ok = dash.put("/api/guards/counting", json=body, headers=HEADER)
        assert ok.status_code == 200
        assert ok.json()["guard"]["interval_s"] == 30.0
        assert dash.put("/api/guards/nope", json=body, headers=HEADER).status_code == 404
        bad = dash.put("/api/guards/counting", json={"interval_s": 0}, headers=HEADER)
        assert bad.status_code == 422
        listing = dash.get("/api/guards").json()
        assert listing["edit"] is True and listing["windows"] == ["1h", "24h", "7d"]
