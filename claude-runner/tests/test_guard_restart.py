from __future__ import annotations

import json
import os

from claude_runner.guard_restart import HOUR_S, REASON, RESTART_LIMIT_PER_HOUR, QueueRestartGuard


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


class FakeStore:
    def __init__(self) -> None:
        self.alive: bool | None = False
        self.running: int | None = 0

    def is_alive(self) -> bool | None:
        return self.alive

    def running_count(self) -> int | None:
        return self.running


def _guard(
    store: FakeStore, path: str, clock: FakeClock, dry_run: bool = False
) -> QueueRestartGuard:
    return QueueRestartGuard(store, path, dry_run=lambda: dry_run, now=clock)


async def _tick(guard: QueueRestartGuard) -> None:
    await guard.act(await guard.check())


async def test_request_written_with_id_time_reason_and_limits(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    clock = FakeClock()
    await _tick(_guard(FakeStore(), path, clock))
    with open(path, encoding="utf-8") as f:
        request = json.load(f)
    assert set(request) == {"request_id", "requested_at", "reason", "limits"}
    assert len(request["request_id"]) == 36
    assert request["requested_at"] == "2027-01-15T08:00:00+00:00"
    assert request["reason"] == REASON
    assert request["limits"] == {"per_hour": RESTART_LIMIT_PER_HOUR, "window_s": HOUR_S}
    assert not os.path.exists(path + ".tmp")


async def test_blocked_while_an_execution_runs(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    store = FakeStore()
    store.running = 1
    await _tick(_guard(store, path, FakeClock()))
    assert not os.path.exists(path)


async def test_blocked_while_queue_is_alive(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    store = FakeStore()
    store.alive = True
    await _tick(_guard(store, path, FakeClock()))
    assert not os.path.exists(path)


async def test_blocked_on_unknown_state(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    clock = FakeClock()
    for alive, running in [(None, 0), (False, None), (None, None)]:
        store = FakeStore()
        store.alive, store.running = alive, running
        await _tick(_guard(store, path, clock))
        assert not os.path.exists(path), (alive, running)


async def test_blocked_after_four_requests_in_an_hour(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    clock = FakeClock()
    guard = _guard(FakeStore(), path, clock)
    for _ in range(RESTART_LIMIT_PER_HOUR):
        await _tick(guard)
        assert os.path.exists(path)
        os.remove(path)
    await _tick(guard)
    assert not os.path.exists(path)
    clock.now += HOUR_S
    await _tick(guard)
    assert os.path.exists(path)


async def test_pending_request_is_not_overwritten(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    guard = _guard(FakeStore(), path, FakeClock())
    await _tick(guard)
    with open(path, encoding="utf-8") as f:
        first = f.read()
    await _tick(guard)
    with open(path, encoding="utf-8") as f:
        assert f.read() == first


async def test_dry_run_writes_nothing_and_uses_no_quota(tmp_path) -> None:
    path = str(tmp_path / "queue_restart.req")
    guard = _guard(FakeStore(), path, FakeClock(), dry_run=True)
    findings = await guard.check()
    assert len(findings) == 1
    assert await guard.act(findings) is None
    assert not os.path.exists(path)
    assert not os.path.exists(path + ".tmp")
    assert len(guard._written) == 0
