import subprocess
import sys
import time

from claude_runner import mem_guard
from claude_runner.mem_guard import MemGuard, Proc, decide


def proc(pid, name, mb, cmd=""):
    return Proc(pid, name, mb, cmd or name)


def test_kills_the_biggest_over_the_per_process_limit():
    procs = [
        proc(1, "claude-runner", 60),
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
    procs = [proc(1, "claude-runner", 9000), proc(80, "claude", 9000, "claude -p --model x")]
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
