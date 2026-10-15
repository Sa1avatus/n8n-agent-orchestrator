"""Memory guard for the processes a Worker starts inside the runner container.

A Worker script that reads a 9 GB model file into RAM (``bytes(data[a:b])`` with a misparsed
length, ``read()`` of a huge file, a runaway list) used to run the container into its memory
limit: the kernel then killed whatever it chose, sometimes the ``claude`` CLI itself, which
interrupts the run, and with no limit at all it took the whole Docker VM down. The guard looks
at the processes every second and kills the offender first, with a clear event in the run's log
(the Worker sees exit code 137 / "Killed" and the rule from its system prompt tells it what
that means).

Two triggers, both on *anonymous* memory (``RssAnon``; a memory-mapped file is page cache and is
reclaimable, so reading a big file through ``mmap`` is fine):

- one process holds more than ``proc_limit_mb``;
- the container's anonymous memory passes ``cgroup_percent`` of its limit: the biggest process
  goes, even if it is under ``proc_limit_mb``.

The runner itself (pid 1), the guard's own process and the ``claude`` CLI are never killed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("claude_runner.mem_guard")

PROTECTED_NAMES = ("claude", "claude-runner")


@dataclass(frozen=True)
class Proc:
    pid: int
    name: str
    anon_mb: int
    cmd: str


@dataclass(frozen=True)
class Kill:
    proc: Proc
    reason: str


def _status_kb(text: str, key: str) -> int:
    for line in text.splitlines():
        if line.startswith(key + ":"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return 0


def read_processes(proc_root: Path = Path("/proc")) -> list[Proc]:
    """Every process with its anonymous memory in MB; unreadable or vanished ones are skipped."""
    out: list[Proc] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
            cmd = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
                .strip()
            )
        except OSError:
            continue
        name = next(
            (
                line.split(":", 1)[1].strip()
                for line in status.splitlines()
                if line.startswith("Name:")
            ),
            "",
        )
        out.append(Proc(int(entry.name), name, _status_kb(status, "RssAnon") // 1024, cmd))
    return out


def cgroup_anon_and_limit_mb(cgroup_root: Path = Path("/sys/fs/cgroup")) -> tuple[int, int]:
    """(anonymous MB in use, limit MB) of the container's cgroup v2; (0, 0) without a limit."""
    try:
        limit_raw = (cgroup_root / "memory.max").read_text(encoding="utf-8").strip()
        stat = (cgroup_root / "memory.stat").read_text(encoding="utf-8")
    except OSError:
        return 0, 0
    if not limit_raw.isdigit():
        return 0, 0
    anon = next((int(line.split()[1]) for line in stat.splitlines() if line.startswith("anon ")), 0)
    return anon // (1024 * 1024), int(limit_raw) // (1024 * 1024)


def decide(
    procs: list[Proc],
    own_pid: int,
    proc_limit_mb: int,
    cgroup_anon_mb: int,
    cgroup_limit_mb: int,
    cgroup_percent: int,
) -> Kill | None:
    """The process to kill now, or None. One kill per scan: the next scan sees the new state."""
    candidates = [
        p
        for p in procs
        if p.pid not in (1, own_pid)
        and p.name not in PROTECTED_NAMES
        and not p.cmd.startswith("claude")
    ]
    if not candidates:
        return None
    biggest = max(candidates, key=lambda p: p.anon_mb)
    if proc_limit_mb > 0 and biggest.anon_mb > proc_limit_mb:
        return Kill(
            biggest, f"holds {biggest.anon_mb} MB of memory (limit {proc_limit_mb} MB per process)"
        )
    over = (
        cgroup_limit_mb > 0
        and cgroup_percent > 0
        and cgroup_anon_mb * 100 > cgroup_limit_mb * cgroup_percent
    )
    if over and biggest.anon_mb >= 64:
        return Kill(
            biggest,
            f"the container uses {cgroup_anon_mb} of {cgroup_limit_mb} MB; "
            f"this is the biggest process ({biggest.anon_mb} MB)",
        )
    return None


class MemGuard:
    def __init__(
        self,
        store: Any,
        events: Any | None,
        proc_limit_mb: int = 3072,
        cgroup_percent: int = 85,
        interval: float = 1.0,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
    ) -> None:
        self.store = store
        self.events = events
        self.proc_limit_mb = proc_limit_mb
        self.cgroup_percent = cgroup_percent
        self.interval = interval
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.kills = 0

    def scan(self) -> Kill | None:
        anon, limit = cgroup_anon_and_limit_mb(self.cgroup_root)
        kill = decide(
            read_processes(self.proc_root),
            os.getpid(),
            self.proc_limit_mb,
            anon,
            limit,
            self.cgroup_percent,
        )
        if kill is None:
            return None
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(kill.proc.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        self.kills += 1
        log.warning(
            "memory guard killed pid %s (%s): %s", kill.proc.pid, kill.proc.name, kill.reason
        )
        self._report(kill)
        return kill

    def _report(self, kill: Kill) -> None:
        if self.events is None:
            return
        try:
            runs = self.store.running_local_workers()
        except Exception:  # the guard must never die on a bookkeeping error
            return
        entry = {
            "kind": "mem_guard",
            "pid": kill.proc.pid,
            "process": kill.proc.name,
            "memory_mb": kill.proc.anon_mb,
            "reason": kill.reason,
            "command": kill.proc.cmd[:300],
        }
        for run in runs:
            with contextlib.suppress(Exception):
                self.events.add(run["run_id"], dict(entry))

    async def run(self) -> None:
        if not self.proc_root.is_dir():  # not Linux: nothing to watch
            return
        while True:
            try:
                await asyncio.to_thread(self.scan)
            except Exception:
                log.exception("memory guard scan failed")
            await asyncio.sleep(self.interval)
