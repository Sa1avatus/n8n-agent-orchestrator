from __future__ import annotations

import os
import signal
import time
import urllib.request
from types import SimpleNamespace
from typing import Any

import pytest

from claude_runner import qa_stack
from claude_runner.qa_stack import (
    HEALTH_MAX_POLLS,
    HEALTH_POLL_SECONDS,
    QaStack,
    QaStackError,
    stack_down,
    stack_up,
)

FAKE_PID = 4242
FAKE_PORT = 5555
KILL_REASON = "the kill path uses os.killpg and signal.SIGKILL, which need POSIX"


class FakeProcess:
    def __init__(self) -> None:
        self.pid = FAKE_PID
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


@pytest.fixture
def fake_process(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    process = FakeProcess()
    killed: list[tuple[int, int]] = []

    def fake_killpg(pid: int, sig: int) -> None:
        killed.append((pid, sig))
        process.returncode = -sig

    sleeps: list[float] = []
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(qa_stack, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(time, "sleep", sleeps.append)
    monkeypatch.setattr(qa_stack, "_free_port", lambda: FAKE_PORT)
    monkeypatch.setattr(qa_stack, "_spawn_dashboard", lambda _port, _fixture: process)
    return SimpleNamespace(process=process, killed=killed, sleeps=sleeps)


def _probe_results(monkeypatch: pytest.MonkeyPatch, results: list[Any]) -> list[int]:
    queue = list(results)
    calls: list[int] = []

    def probe(port: int) -> bool:
        calls.append(port)
        result = queue.pop(0)
        if isinstance(result, BaseException):
            raise result
        return bool(result)

    monkeypatch.setattr(qa_stack, "_dashboard_healthy", probe)
    return calls


def test_inprocess_stack_is_ready_after_the_probe_turns_healthy(
    monkeypatch: pytest.MonkeyPatch, fake_process: SimpleNamespace
) -> None:
    calls = _probe_results(monkeypatch, [False, False, False, True])

    stack = stack_up("default", mode="inprocess")

    assert len(calls) == 4
    assert fake_process.sleeps == [HEALTH_POLL_SECONDS] * 3
    assert fake_process.killed == []
    assert stack.process is fake_process.process
    assert stack.dashboard_url == f"http://127.0.0.1:{FAKE_PORT}"


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason=KILL_REASON)
def test_inprocess_stack_timeout_kills_the_child_after_max_polls(
    monkeypatch: pytest.MonkeyPatch, fake_process: SimpleNamespace
) -> None:
    calls = _probe_results(monkeypatch, [False] * HEALTH_MAX_POLLS)

    with pytest.raises(QaStackError, match="not healthy after"):
        stack_up("default", mode="inprocess")

    assert len(calls) == HEALTH_MAX_POLLS
    assert fake_process.killed == [(FAKE_PID, signal.SIGKILL)]


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason=KILL_REASON)
def test_inprocess_startup_failure_still_kills_the_child(
    monkeypatch: pytest.MonkeyPatch, fake_process: SimpleNamespace
) -> None:
    _probe_results(monkeypatch, [RuntimeError("probe broke")])

    with pytest.raises(RuntimeError, match="probe broke"):
        stack_up("default", mode="inprocess")

    assert fake_process.killed == [(FAKE_PID, signal.SIGKILL)]


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason=KILL_REASON)
def test_stack_down_kills_the_process_tree(fake_process: SimpleNamespace) -> None:
    stack = QaStack(run_id="inproc", host="", database="", dsn="", process=fake_process.process)

    stack_down(stack)

    assert fake_process.killed == [(FAKE_PID, signal.SIGKILL)]
    assert fake_process.process.returncode is not None


def test_unknown_mode_raises_before_anything_starts(fake_process: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="mode 'bogus' is not one of"):
        stack_up("default", mode="bogus")

    assert fake_process.killed == []
    assert fake_process.sleeps == []


@pytest.mark.qa_integration
def test_inprocess_dashboard_answers_healthz() -> None:
    try:
        qa_stack._free_port()
    except OSError as error:
        pytest.skip(f"cannot bind a local port: {error}")
    stack = stack_up("default", mode="inprocess")
    try:
        with urllib.request.urlopen(f"{stack.dashboard_url}/healthz", timeout=5) as response:
            assert response.status == 200
    finally:
        stack_down(stack)
