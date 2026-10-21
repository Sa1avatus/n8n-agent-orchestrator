from __future__ import annotations

import json
import time
from pathlib import Path

from claude_runner.guard_heartbeat import (
    HEARTBEAT_NAMES,
    TIME_FORMAT,
    HeartbeatSettings,
    HostHeartbeatGuard,
)
from claude_runner.guards import Notifier

STALE_S = 300.0
T0 = 1_800_000_000.0
DOCKER, GOVERNOR = HEARTBEAT_NAMES


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


def _write(directory: Path, name: str, epoch: float, action: str = "idle") -> None:
    stamp = time.strftime(TIME_FORMAT, time.gmtime(epoch))
    (directory / f"{name}.json").write_text(
        json.dumps({"name": name, "time": stamp, "last_action": action}), encoding="utf-8"
    )


def _guard(
    directory: Path, clock: FakeClock, sent: list[str], dry_run: bool = False
) -> HostHeartbeatGuard:
    async def transport(text: str) -> bool:
        sent.append(text)
        return True

    settings = HeartbeatSettings(heartbeat_dir=str(directory), stale_seconds=STALE_S)
    return HostHeartbeatGuard(
        settings,
        Notifier(env={}, transport=transport),
        dry_run=lambda: dry_run,
        now=clock,
    )


async def _tick(guard: HostHeartbeatGuard) -> None:
    await guard.act(await guard.check())


async def test_fresh_files_are_reported_without_notice(tmp_path: Path) -> None:
    clock = FakeClock()
    sent: list[str] = []
    _write(tmp_path, DOCKER, T0 - 10, "engine ok")
    _write(tmp_path, GOVERNOR, T0 - 5, "boosting")
    guard = _guard(tmp_path, clock, sent)
    assert await guard.check() == []
    entries = {str(e["name"]): e for e in guard.status_entries()}
    assert entries[DOCKER]["status"] == "fresh"
    assert entries[DOCKER]["last_action"] == "engine ok"
    assert entries[GOVERNOR]["time"] == time.strftime(TIME_FORMAT, time.gmtime(T0 - 5))
    assert sent == []


async def test_missing_files_are_unknown_and_never_notify(tmp_path: Path) -> None:
    clock = FakeClock()
    sent: list[str] = []
    guard = _guard(tmp_path, clock, sent)
    assert await guard.check() == []
    assert [e["status"] for e in guard.status_entries()] == ["unknown", "unknown"]
    await _tick(guard)
    assert sent == []


def test_empty_heartbeat_dir_is_unknown() -> None:
    guard = HostHeartbeatGuard(
        HeartbeatSettings(heartbeat_dir="", stale_seconds=STALE_S),
        Notifier(env={}),
        now=FakeClock(),
    )
    assert [e["status"] for e in guard.status_entries()] == ["unknown", "unknown"]


async def test_one_notice_per_stale_episode_and_one_on_recovery(tmp_path: Path) -> None:
    clock = FakeClock()
    sent: list[str] = []
    _write(tmp_path, DOCKER, T0)
    guard = _guard(tmp_path, clock, sent)
    await _tick(guard)
    assert sent == []

    clock.now = T0 + STALE_S + 1
    for _ in range(3):
        await _tick(guard)
    assert len(sent) == 1
    assert f"heartbeat {DOCKER} stale" in sent[0]
    assert [e["status"] for e in guard.status_entries()][0] == "stale"

    _write(tmp_path, DOCKER, clock.now)
    await _tick(guard)
    assert len(sent) == 2
    assert f"heartbeat {DOCKER} recovered" in sent[1]
    await _tick(guard)
    assert len(sent) == 2

    clock.now += STALE_S + 1
    await _tick(guard)
    assert len(sent) == 3
    assert f"heartbeat {DOCKER} stale" in sent[2]


async def test_malformed_json_is_an_error_without_crash(tmp_path: Path) -> None:
    clock = FakeClock()
    sent: list[str] = []
    (tmp_path / f"{GOVERNOR}.json").write_text("{not json", encoding="utf-8")
    guard = _guard(tmp_path, clock, sent)
    findings = await guard.check()
    assert len(findings) == 1
    assert f"heartbeat {GOVERNOR} error" in findings[0].detail
    entries = {str(e["name"]): e for e in guard.status_entries()}
    assert entries[GOVERNOR]["status"] == "error"
    assert await guard.check() == []
    await guard.act(findings)
    assert len(sent) == 1


def test_bad_time_stamp_is_an_error(tmp_path: Path) -> None:
    (tmp_path / f"{DOCKER}.json").write_text(
        json.dumps({"name": DOCKER, "time": "yesterday", "last_action": "x"}), encoding="utf-8"
    )
    guard = _guard(tmp_path, FakeClock(), [])
    assert guard.status_entries()[0]["status"] == "error"


async def test_dry_run_sends_nothing(tmp_path: Path) -> None:
    clock = FakeClock()
    sent: list[str] = []
    _write(tmp_path, DOCKER, T0 - STALE_S - 1)
    guard = _guard(tmp_path, clock, sent, dry_run=True)
    findings = await guard.check()
    assert len(findings) == 1
    assert await guard.act(findings) is None
    assert sent == []


def test_settings_read_from_env() -> None:
    settings = HeartbeatSettings.from_env(
        {
            "DASHBOARD_GUARDS_HEARTBEAT_DIR": " /mnt/ops/heartbeat ",
            "DASHBOARD_GUARDS_HEARTBEAT_STALE_S": "90",
        }
    )
    assert settings == HeartbeatSettings(heartbeat_dir="/mnt/ops/heartbeat", stale_seconds=90.0)
    fallback = HeartbeatSettings.from_env({"DASHBOARD_GUARDS_HEARTBEAT_STALE_S": "soon"})
    assert fallback == HeartbeatSettings(heartbeat_dir="", stale_seconds=300.0)
