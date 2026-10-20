"""RunManager process listing and safe terminate (T011).

1. The listing contains only pids from the runner's own registry, not a host
   process list.
2. terminate_pid refuses unknown pids and pids whose run is running or queued.
3. No environment and no unmasked command line in any returned field.
"""

from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

from claude_runner.config import Settings
from claude_runner.runs import RunManager, StartRequest


async def _scenario(settings: Settings) -> None:
    manager = RunManager(settings)
    try:
        run = await manager.start(StartRequest(input="slow [[sleep:30]]"))
        run_id = run["run_id"]
        deadline = time.monotonic() + 10
        while run_id not in manager._procs and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert run_id in manager._procs

        # 1) The listing is exactly the manager's own registry entries.
        listed = manager.list_processes()
        assert len(listed) == len(manager._procs) == 1
        entry = listed[0]
        assert entry["pid"] == manager._procs[run_id].pid
        assert entry["run_id"] == run_id
        assert entry["session_id"] == run["session_id"]
        assert entry["status"] == "running"
        assert entry["age_seconds"] >= 0
        # 3) No environment or raw command line in any returned field.
        for value in entry.values():
            text = str(value)
            assert "ANTHROPIC" not in text
            assert "CLAUDE_RUNNER_" not in text
            assert "claude" not in text
            assert "prlimit" not in text

        # 2) A pid whose run is running is refused; the process is left in place.
        busy = await manager.terminate_pid(entry["pid"])
        assert busy["outcome"] == "run_active"
        assert busy["run_id"] == run_id
        assert manager._procs[run_id].returncode is None

        # 2) A pid that is not in the runner's own registry is refused.
        foreign = await manager.terminate_pid(os.getpid())
        assert foreign["outcome"] == "not_found"
        assert foreign["pid"] == os.getpid()

        # After the run ends its pid leaves the registry: the listing is empty and
        # terminating it again is refused, never touching a foreign process.
        await manager.cancel(run_id)
        gone = await manager.terminate_pid(entry["pid"])
        assert gone["outcome"] == "not_found"
        assert manager.list_processes() == []

        # A registry process whose run does not belong to this manager's sessions
        # is refused with not_owned (no signal is sent to it).
        manager.store.create_run(
            run_id="run_foreign",
            session_id="no-such-session",
            claude_session_id="x",
            cwd="/",
            input="x",
        )
        foreign_proc = SimpleNamespace(pid=999_999, returncode=None)
        manager._procs["run_foreign"] = foreign_proc  # type: ignore[assignment]
        refused = await manager.terminate_pid(999_999)
        assert refused["outcome"] == "not_owned"
        assert refused["run_id"] == "run_foreign"
        manager._procs.pop("run_foreign")
    finally:
        await manager.shutdown()


def test_list_processes_and_safe_terminate(settings: Settings) -> None:
    asyncio.run(_scenario(settings))
