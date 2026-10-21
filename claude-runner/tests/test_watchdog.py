"""Watchdog over the run's CLI processes.

Every interval the runner looks at each CLI process it started: one whose parent is no
longer the runner is an ``orphan``; one that used more than the CPU limit since the run's
last event is ``child_hung``. Both are killed (process group) and the run fails with that
error code. ``watchdog_verdict`` is pure, so the decision is tested on plain values; one
stub process that spins on the CPU checks the whole path for real.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import time
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from claude_runner import runs
from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.runs import ProcSample, RunManager, group_sample, read_sample, watchdog_verdict

from .conftest import wait_for

RUNNER = 4242


def test_healthy_when_parent_is_runner_and_cpu_is_under_limit() -> None:
    sample = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=10.0)
    assert watchdog_verdict(sample, cpu_at_last_event=5.0, cpu_limit=900, runner_pid=RUNNER) is None


def test_qa_runs_are_not_stopped_for_cpu_but_orphans_still_are() -> None:
    busy = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=1000.0)
    orphan = ProcSample(pid=100, ppid=999, cpu_seconds=1000.0)
    assert watchdog_verdict(busy, 50.0, 900, RUNNER, cpu_watch=False) is None
    assert watchdog_verdict(orphan, 50.0, 900, RUNNER, cpu_watch=False) == "orphan"


def test_hung_when_cpu_since_last_event_exceeds_limit() -> None:
    sample = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=1000.0)
    assert (
        watchdog_verdict(sample, cpu_at_last_event=50.0, cpu_limit=900, runner_pid=RUNNER)
        == "child_hung"
    )


def test_qa_time_limit_defaults_to_20_minutes_and_comes_from_env() -> None:
    assert Settings.from_env({}).qa_max_minutes == 20
    assert Settings.from_env({"CLAUDE_RUNNER_QA_MAX_MINUTES": "45"}).qa_max_minutes == 45


def test_cpu_limit_is_strict() -> None:
    sample = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=950.0)
    assert watchdog_verdict(sample, 50.0, 900, RUNNER) is None
    sample = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=950.5)
    assert watchdog_verdict(sample, 50.0, 900, RUNNER) == "child_hung"


def test_an_event_resets_the_cpu_budget() -> None:
    # 1000 s of CPU in total, but the last event came at 990 s: only 10 s since then
    sample = ProcSample(pid=100, ppid=RUNNER, cpu_seconds=1000.0)
    verdict = watchdog_verdict(sample, cpu_at_last_event=990.0, cpu_limit=900, runner_pid=RUNNER)
    assert verdict is None


def test_orphan_when_parent_is_not_runner() -> None:
    sample = ProcSample(pid=100, ppid=1, cpu_seconds=0.0)
    assert watchdog_verdict(sample, 0.0, 900, RUNNER) == "orphan"


def test_orphan_takes_precedence_over_hung() -> None:
    sample = ProcSample(pid=100, ppid=1, cpu_seconds=5000.0)
    assert watchdog_verdict(sample, 0.0, 900, RUNNER) == "orphan"


def test_read_sample_parses_stat_with_spaces_in_comm(tmp_path: Path) -> None:
    pid_dir = tmp_path / "77"
    pid_dir.mkdir()
    # comm "my (odd) name": the fields follow the last ')'
    fields = "S 4242 0 0 0 -1 0 0 0 0 0 300 100 0 0 20 0 1 0 1 0 0"
    (pid_dir / "stat").write_text(f"77 (my (odd) name) {fields}\n")
    sample = read_sample(77, proc_root=tmp_path)
    assert sample is not None
    assert sample.ppid == 4242
    assert sample.cpu_seconds == pytest.approx(400 / os.sysconf("SC_CLK_TCK"))


def test_read_sample_none_for_missing_process(tmp_path: Path) -> None:
    assert read_sample(99999, proc_root=tmp_path) is None


def test_defaults_are_60_seconds_and_15_minutes() -> None:
    s = Settings.from_env({})
    assert s.watchdog_interval_seconds == 60
    assert s.watchdog_cpu_seconds == 900


def test_interval_and_cpu_limit_come_from_env() -> None:
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS": "5",
            "CLAUDE_RUNNER_WATCHDOG_CPU_SECONDS": "120",
        }
    )
    assert s.watchdog_interval_seconds == 5
    assert s.watchdog_cpu_seconds == 120


# A `claude` stand-in that reads the prompt and then spins on the CPU without printing any
# event. It writes its own pid to stub.pid in the run's cwd so the test can check it is gone.
SPIN_BODY = '''\
import os
import sys

sys.stdin.read()
with open("stub.pid", "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
while True:
    sum(range(10000))
'''


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def test_busy_child_without_events_is_stopped_as_child_hung(tmp_path: Path) -> None:
    if not Path("/proc").is_dir():
        pytest.skip("needs /proc")
    stub = tmp_path / "spin.py"
    stub.write_text(SPIN_BODY, encoding="utf-8")
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
            "CLAUDE_RUNNER_CLAUDE_BIN": str(wrapper),
            "CLAUDE_RUNNER_WORKSPACE": str(tmp_path),
            "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        }
    )
    object.__setattr__(s, "interrupt_grace_seconds", 0.5)
    object.__setattr__(s, "proc_data_limit_mb", 0)
    object.__setattr__(s, "watchdog_interval_seconds", 1)
    object.__setattr__(s, "watchdog_cpu_seconds", 1)

    with TestClient(create_app(s)) as client:
        manager = client.app.state.manager
        response = client.post("/v1/runs", json={"input": "spin", "model": "sonnet"})
        assert response.status_code == 200, response.text
        run_id = str(response.json()["run_id"])

        run = wait_for(client, run_id, timeout=30)
        assert run["status"] == "failed", run
        assert run["error"]["code"] == "child_hung", run

        pid = int((tmp_path / "stub.pid").read_text())
        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(pid)
        assert run_id not in manager._procs


def test_group_sample_sums_child_cpu_into_the_sample(tmp_path: Path) -> None:
    # leader 10 (pgrp 10, cpu 300/ticks) and its child 11 (pgrp 10, cpu 100/ticks):
    # the group sample must carry the leader's ppid and the summed CPU of both.
    leader_dir = tmp_path / "10"
    leader_dir.mkdir()
    (leader_dir / "stat").write_text(
        "10 (lead) S 4242 10 10 0 -1 0 0 0 0 0 300 0 0 0 20 0 1 0 1 0 0\n",
        encoding="utf-8",
    )
    child_dir = tmp_path / "11"
    child_dir.mkdir()
    (child_dir / "stat").write_text(
        "11 (child) S 10 10 10 0 -1 0 0 0 0 0 100 0 0 0 20 0 1 0 1 0 0\n",
        encoding="utf-8",
    )
    sample = group_sample(10, proc_root=tmp_path)
    assert sample is not None
    assert sample.ppid == RUNNER
    assert sample.cpu_seconds == pytest.approx(400 / os.sysconf("SC_CLK_TCK"))


# A `claude` stand-in that stays idle (sleep) but spawns a child that spins on the CPU.
# The child is in the same session as the leader, so the group's summed CPU is what trips
# the watchdog, not the idle leader alone.
SLEEP_SPIN_BODY = '''\
import os
import subprocess
import sys
import time

sys.stdin.read()
with open("stub.pid", "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
child = subprocess.Popen([sys.executable, "-c", "while True: sum(range(100000))"])
with open("child.pid", "w", encoding="utf-8") as f:
    f.write(str(child.pid))
time.sleep(300)
'''


def test_idle_parent_with_spinning_child_is_stopped_as_child_hung(tmp_path: Path) -> None:
    if not Path("/proc").is_dir():
        pytest.skip("needs /proc")
    stub = tmp_path / "sleep_spin.py"
    stub.write_text(SLEEP_SPIN_BODY, encoding="utf-8")
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
            "CLAUDE_RUNNER_CLAUDE_BIN": str(wrapper),
            "CLAUDE_RUNNER_WORKSPACE": str(tmp_path),
            "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        }
    )
    object.__setattr__(s, "interrupt_grace_seconds", 0.5)
    object.__setattr__(s, "proc_data_limit_mb", 0)
    object.__setattr__(s, "watchdog_interval_seconds", 1)
    object.__setattr__(s, "watchdog_cpu_seconds", 1)

    with TestClient(create_app(s)) as client:
        manager = client.app.state.manager
        response = client.post("/v1/runs", json={"input": "spin", "model": "sonnet"})
        assert response.status_code == 200, response.text
        run_id = str(response.json()["run_id"])

        run = wait_for(client, run_id, timeout=30)
        assert run["status"] == "failed", run
        assert run["error"]["code"] == "child_hung", run

        parent = int((tmp_path / "stub.pid").read_text())
        child = int((tmp_path / "child.pid").read_text())
        deadline = time.monotonic() + 5
        while (_alive(parent) or _alive(child)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(parent)
        assert not _alive(child)
        assert run_id not in manager._procs


def _scanned_manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cpu: dict[int, float]
) -> tuple[RunManager, list[int]]:
    killed: list[int] = []

    def fake_sample(pid: int) -> ProcSample:
        return ProcSample(pid=pid, ppid=os.getpid(), cpu_seconds=cpu[pid])

    monkeypatch.setattr(runs, "group_sample", fake_sample)
    monkeypatch.setattr(os, "killpg", lambda pid, _sig: killed.append(pid))
    return RunManager(Settings.from_env({"CLAUDE_RUNNER_DATA_DIR": str(tmp_path)})), killed


class _FakeProc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def _register_proc(
    manager: RunManager, run_id: str, pid: int, *, qa: bool, started_ago: float = 0.0
) -> None:
    proc = cast("asyncio.subprocess.Process", _FakeProc(pid))
    manager._procs[run_id] = proc
    manager._proc_started_at[proc] = time.monotonic() - started_ago
    if qa:
        manager._qa_runs.add(run_id)


def test_qa_run_past_its_time_limit_is_stopped_and_other_runs_are_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, killed = _scanned_manager(tmp_path, monkeypatch, {101: 0.0, 102: 0.0})
    over_limit = 21 * 60
    _register_proc(manager, "qa-run", 101, qa=True, started_ago=over_limit)
    _register_proc(manager, "worker-run", 102, qa=False, started_ago=over_limit)
    manager.watchdog_scan()
    assert manager._watchdog_codes == {"qa-run": "qa_timeout"}
    assert killed == [101]


def test_watchdog_scan_stops_only_the_non_qa_run_over_cpu_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cpu = {201: 0.0, 202: 0.0}
    manager, killed = _scanned_manager(tmp_path, monkeypatch, cpu)
    _register_proc(manager, "qa-run", 201, qa=True)
    _register_proc(manager, "worker-run", 202, qa=False)
    manager.watchdog_scan()
    cpu[201] = cpu[202] = 1000.0
    manager.watchdog_scan()
    assert manager._watchdog_codes == {"worker-run": "child_hung"}
    assert killed == [202]
