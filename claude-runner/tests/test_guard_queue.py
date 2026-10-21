from __future__ import annotations

import json
from typing import Any

import pytest

from claude_runner.guard_queue import (
    HOUR_S,
    MINUTE_S,
    NOTICE_LIMIT_PER_HOUR,
    HourlyLimit,
    IdleGuard,
    MissionNoticeGuard,
    SeenStore,
)
from claude_runner.guards import Notifier


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeQueueLog:
    def __init__(self, lines: list[str]) -> None:
        self.rows = [{"at": f"t{i:04d}", "line": line} for i, line in enumerate(lines)]

    def add(self, line: str) -> None:
        self.rows.insert(0, {"at": f"t{len(self.rows):04d}", "line": line})

    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        return list(self.rows[:limit])


class FakeEvents:
    def events(self, state_key: str) -> list[dict[str, Any]]:
        return [
            {
                "event_type": "review_parsed",
                "task_index": 2,
                "task_attempt": 1,
                "review_score": 40,
                "review_reason": "tests\nmissing",
            }
        ]


def _notifier(sent: list[str]) -> Notifier:
    async def transport(text: str) -> bool:
        sent.append(text)
        return True

    return Notifier(env={}, transport=transport)


def _guard(path: str, log: FakeQueueLog, sent: list[str], limit: HourlyLimit | None = None):
    return MissionNoticeGuard(log, FakeEvents(), SeenStore(path), _notifier(sent), limit=limit)


async def _tick(guard: MissionNoticeGuard) -> None:
    await guard.check()
    await guard.act([])


async def test_first_run_records_history_without_sending(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["MISSION NOT COMPLETED: m1 (failed)", "=== real queue done"])
    sent: list[str] = []
    await _tick(_guard(path, log, sent))
    assert sent == []
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["seen"]


async def test_new_line_is_sent_once_and_survives_restart(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["queue started"])
    sent: list[str] = []
    await _tick(_guard(path, log, sent))

    log.add("MISSION NOT COMPLETED: m1 (failed)")
    await _tick(_guard(path, log, sent))
    assert len(sent) == 1
    assert sent[0].startswith("❌ Миссия m1 не завершена (состояние failed).")
    assert "задача 3, попытка 1, оценка 40." in sent[0]

    await _tick(_guard(path, log, sent))
    await _tick(_guard(str(tmp_path / "state.json"), log, sent))
    assert len(sent) == 1

    log.add("ended m2: completed")
    await _tick(_guard(path, log, sent))
    assert sent[-1] == "✅ Миссия m2 завершена."
    assert len(sent) == 2


async def test_dry_run_check_sends_nothing(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["queue started"])
    sent: list[str] = []
    guard = _guard(path, log, sent)
    await _tick(guard)
    log.add("=== real queue done")
    findings = await guard.check()
    assert len(findings) == 1
    assert sent == []


async def test_dry_run_fresh_state_sends_nothing_and_writes_no_state(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["MISSION NOT COMPLETED: m1 (failed)"])
    sent: list[str] = []
    guard = MissionNoticeGuard(
        log, FakeEvents(), SeenStore(path), _notifier(sent), dry_run=lambda: True
    )
    findings = await guard.check()
    assert len(findings) == 1
    assert await guard.act(findings) is None
    assert sent == []
    assert not (tmp_path / "state.json").exists()


async def test_hourly_limit_drops_extra_notices(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["queue started"])
    sent: list[str] = []
    guard = _guard(path, log, sent, limit=HourlyLimit(2, now=lambda: 0.0))
    await _tick(guard)
    for i in range(3):
        log.add(f"ALERT: problem {i}")
    await _tick(guard)
    assert sent == ["⚠️ problem 0", "⚠️ problem 1"]


async def test_default_limit_is_twenty_per_hour_and_resets_after_an_hour(tmp_path) -> None:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["queue started"])
    sent: list[str] = []
    clock = FakeClock()
    guard = _guard(path, log, sent, limit=HourlyLimit(NOTICE_LIMIT_PER_HOUR, now=clock))
    await _tick(guard)
    for i in range(NOTICE_LIMIT_PER_HOUR + 1):
        log.add(f"ALERT: problem {i}")
    await _tick(guard)
    assert len(sent) == NOTICE_LIMIT_PER_HOUR

    clock.now += HOUR_S
    log.add("ALERT: later")
    await _tick(guard)
    assert sent[-1] == "⚠️ later"
    assert len(sent) == NOTICE_LIMIT_PER_HOUR + 1


async def _send_after(tmp_path, line: str) -> list[str]:
    path = str(tmp_path / "state.json")
    log = FakeQueueLog(["queue started"])
    sent: list[str] = []
    await _tick(_guard(path, log, sent))
    log.add(line)
    await _tick(_guard(path, log, sent))
    return sent


async def test_completed_mission_sends_notice(tmp_path) -> None:
    assert await _send_after(tmp_path, "ended m2: completed") == ["✅ Миссия m2 завершена."]


async def test_not_completed_mission_names_last_review(tmp_path) -> None:
    sent = await _send_after(tmp_path, "MISSION NOT COMPLETED: m1 (failed)")
    assert len(sent) == 1
    assert "Последнее ревью: задача 3, попытка 1, оценка 40." in sent[0]
    assert "tests missing" in sent[0]


async def test_queue_stopped_sends_notice(tmp_path) -> None:
    sent = await _send_after(tmp_path, "REAL QUEUE STOPPED: n8n busy")
    assert len(sent) == 1
    assert sent[0].startswith("⛔ Очередь миссий остановилась: n8n busy.")


async def test_queue_done_sends_notice(tmp_path) -> None:
    sent = await _send_after(tmp_path, "=== real queue done")
    assert sent == ["🏁 Очередь миссий закончилась: все миссии пройдены или пропущены."]


async def test_alert_line_sends_notice(tmp_path) -> None:
    assert await _send_after(tmp_path, "ALERT: disk almost full") == ["⚠️ disk almost full"]


@pytest.mark.parametrize(
    "line",
    [
        "ALERT: DISABLE_THINKING is set",
        "ALERT: new missions were not inserted",
        "ALERT: REAL QUEUE STOPPED",
    ],
)
async def test_ignored_alert_lines_send_nothing(tmp_path, line: str) -> None:
    assert await _send_after(tmp_path, line) == []


@pytest.mark.parametrize(
    "line",
    ["ended m1: failed", "queue heartbeat", "MISSION NOT COMPLETED: m1"],
)
async def test_unknown_line_sends_nothing(tmp_path, line: str) -> None:
    assert await _send_after(tmp_path, line) == []


class FakeQueue:
    def __init__(self) -> None:
        self.alive: bool | None = True
        self.running: int | None = 0

    def is_alive(self) -> bool | None:
        return self.alive

    def running_count(self) -> int | None:
        return self.running


def _idle_guard(
    queue: FakeQueue, clock: FakeClock, sent: list[str], dry_run: bool = False
) -> IdleGuard:
    return IdleGuard(
        queue.is_alive,
        queue.running_count,
        _notifier(sent),
        dry_run=lambda: dry_run,
        now=clock,
    )


async def _idle_tick(guard: IdleGuard) -> None:
    await guard.act(await guard.check())


async def test_idle_notice_after_twenty_minutes_once(tmp_path) -> None:
    queue = FakeQueue()
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 20 * MINUTE_S - 1
    await _idle_tick(guard)
    assert sent == []
    clock.now = 20 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 1
    assert sent[0].startswith("💤 Ничего не выполняется уже 20 мин")
    clock.now = 60 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 1


async def test_idle_notice_rearmed_only_after_activity() -> None:
    queue = FakeQueue()
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 20 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 1

    queue.running = 1
    clock.now = 25 * MINUTE_S
    await _idle_tick(guard)
    queue.running = 0
    clock.now = 26 * MINUTE_S
    await _idle_tick(guard)
    clock.now = 45 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 1
    clock.now = 46 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 2


async def test_idle_clock_restarts_after_activity() -> None:
    queue = FakeQueue()
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 15 * MINUTE_S
    queue.running = 2
    await _idle_tick(guard)
    queue.running = 0
    clock.now = 30 * MINUTE_S
    await _idle_tick(guard)
    assert sent == []
    clock.now = 50 * MINUTE_S
    await _idle_tick(guard)
    assert len(sent) == 1


async def test_idle_unknown_liveness_sends_nothing() -> None:
    queue = FakeQueue()
    queue.alive = None
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 60 * MINUTE_S
    await _idle_tick(guard)
    assert sent == []


async def test_idle_unknown_run_count_sends_nothing() -> None:
    queue = FakeQueue()
    queue.running = None
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 60 * MINUTE_S
    await _idle_tick(guard)
    assert sent == []


async def test_idle_dead_queue_sends_nothing() -> None:
    queue = FakeQueue()
    queue.alive = False
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent)
    await _idle_tick(guard)
    clock.now = 60 * MINUTE_S
    await _idle_tick(guard)
    assert sent == []


async def test_idle_dry_run_sends_nothing() -> None:
    queue = FakeQueue()
    clock = FakeClock()
    sent: list[str] = []
    guard = _idle_guard(queue, clock, sent, dry_run=True)
    await _idle_tick(guard)
    clock.now = 20 * MINUTE_S
    findings = await guard.check()
    assert len(findings) == 1
    assert await guard.act(findings) is None
    assert sent == []
