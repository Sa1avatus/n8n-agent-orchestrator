import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import mem_guard
from ahawr_runner.mem_guard import MemGuard, Proc, decide


def proc(pid, name, mb, cmd=""):
    return Proc(pid, name, mb, cmd or name)


def test_kills_the_biggest_over_the_per_process_limit():
    procs = [
        proc(1, "ahawr-runner", 60),
        proc(80, "claude", 400, "claude -p"),
        proc(200, "python3", 5000),
        proc(201, "bash", 3),
    ]
    kill = decide(
        procs,
        own_pid=1,
        proc_limit_mb=3072,
        cgroup_anon_mb=5500,
        cgroup_limit_mb=6144,
        cgroup_percent=85,
    )
    assert kill is not None and kill.proc.pid == 200 and "5000" in kill.reason


def test_never_kills_the_runner_or_the_claude_cli():
    procs = [proc(1, "ahawr-runner", 9000), proc(80, "claude", 9000, "claude -p --model x")]
    assert decide(procs, 1, 3072, 9000, 6144, 85) is None


def test_container_near_its_limit_kills_the_biggest_even_under_the_process_limit():
    procs = [proc(200, "python3", 2900), proc(201, "pytest", 2600)]
    kill = decide(procs, 1, 3072, 5600, 6144, 85)
    assert kill is not None and kill.proc.pid == 200 and "container" in kill.reason


def test_small_processes_are_left_alone_when_the_container_is_full_of_cache_like_memory():
    assert decide([proc(200, "python3", 10)], 1, 3072, 5600, 6144, 85) is None


def test_off_values_disable_each_trigger():
    procs = [proc(200, "python3", 9000)]
    assert decide(procs, 1, 0, 100, 0, 0) is None


def test_read_processes_parses_a_fake_proc(tmp_path):
    d = tmp_path / "4242"
    d.mkdir()
    (d / "status").write_text(
        "Name:\tpython3\nRssAnon:\t  2097152 kB\nRssFile:\t 10 kB\n", encoding="utf-8"
    )
    (d / "cmdline").write_bytes(b"python3\0-c\0print(1)\0")
    (tmp_path / "self").mkdir()
    found = mem_guard.read_processes(tmp_path)
    assert found == [Proc(4242, "python3", 2048, "python3 -c print(1)")]


def test_cgroup_numbers(tmp_path):
    (tmp_path / "memory.max").write_text("6442450944\n", encoding="utf-8")
    (tmp_path / "memory.stat").write_text("anon 1073741824\nfile 5\n", encoding="utf-8")
    assert mem_guard.cgroup_anon_and_limit_mb(tmp_path) == (1024, 6144)
    (tmp_path / "memory.max").write_text("max\n", encoding="utf-8")
    assert mem_guard.cgroup_anon_and_limit_mb(tmp_path) == (0, 0)


class Events:
    def __init__(self):
        self.added = []

    def add(self, run_id, entry):
        self.added.append((run_id, entry))


class Store:
    def running_local_workers(self):
        return [{"run_id": "run_1"}]


def test_scan_kills_a_real_process_and_reports_it(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        fake = tmp_path / str(child.pid)
        fake.mkdir()
        (fake / "status").write_text("Name:\tpython3\nRssAnon:\t 8388608 kB\n", encoding="utf-8")
        (fake / "cmdline").write_bytes(b"python3\0big.py\0")
        events = Events()
        guard = MemGuard(
            Store(), events, proc_limit_mb=3072, proc_root=tmp_path, cgroup_root=tmp_path / "none"
        )
        kill = guard.scan()
        assert kill is not None and kill.proc.pid == child.pid
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.1)
        assert child.returncode is not None
        assert events.added[0][0] == "run_1"
        assert events.added[0][1]["kind"] == "mem_guard" and events.added[0][1]["memory_mb"] == 8192
    finally:
        if child.poll() is None:
            child.kill()


def _loop_guard(tmp_path: Path, scan: Any) -> MemGuard:
    """A guard whose loop can run: proc_root exists, scan replaced, sleep faked by the test."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    guard = MemGuard(None, None, interval=1, proc_root=proc_root)
    guard.scan = scan
    return guard


def test_mem_guard_loop_exits_when_proc_root_is_not_a_directory(tmp_path, monkeypatch):
    scans, sleeps = [], []

    async def fake_sleep(_interval):
        sleeps.append(1)

    monkeypatch.setattr(MemGuard, "scan", lambda self: scans.append(1))
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    asyncio.run(MemGuard(None, None, proc_root=tmp_path / "missing").run())
    assert not scans and not sleeps


def test_mem_guard_loop_exits_on_keyboardinterrupt(tmp_path, monkeypatch):
    scans, sleeps = [], []

    async def fake_sleep(_interval):
        sleeps.append(1)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    guard = _loop_guard(tmp_path, lambda: scans.append(1))
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(guard.run())
    assert len(scans) == 2 and len(sleeps) == 2


def test_mem_guard_loop_exits_on_cancellederror(tmp_path, monkeypatch):
    scans, sleeps = [], []

    async def fake_sleep(_interval):
        sleeps.append(1)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    guard = _loop_guard(tmp_path, lambda: scans.append(1))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(guard.run())
    assert len(scans) == 2 and len(sleeps) == 2


def test_mem_guard_loop_exits_when_sleep_raises(tmp_path, monkeypatch):
    scans, sleeps = [], []

    async def fake_sleep(_interval):
        sleeps.append(1)
        if len(sleeps) == 3:
            raise RuntimeError("sleep failed")

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    guard = _loop_guard(tmp_path, lambda: scans.append(1))
    with pytest.raises(RuntimeError):
        asyncio.run(guard.run())
    # sleep sits outside the try: the third sleep failure ends run() after three scans.
    assert len(scans) == 3 and len(sleeps) == 3


def test_mem_guard_loop_keeps_running_after_scan_raises(tmp_path, monkeypatch):
    scans, sleeps = [], []

    def failing_scan():
        scans.append(1)
        raise RuntimeError("scan failed")

    async def fake_sleep(_interval):
        sleeps.append(1)
        if len(sleeps) == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    guard = _loop_guard(tmp_path, failing_scan)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(guard.run())
    # except Exception swallows the scan error: the loop ran all three iterations.
    assert len(scans) == 3 and len(sleeps) == 3


def test_mem_guard_loop_stops_at_max_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scans: list[int] = []
    sleeps: list[int] = []

    async def fake_sleep(_interval: float) -> None:
        sleeps.append(1)
        # guard: without the bound the loop never ends; this raises and fails the run
        if len(sleeps) == 4:
            raise RuntimeError("loop did not stop at the bound")

    monkeypatch.setattr(mem_guard, "MAX_GUARD_SCANS", 3)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    guard = _loop_guard(tmp_path, lambda: scans.append(1))
    asyncio.run(guard.run())
    assert len(scans) == 3 and len(sleeps) == 3
