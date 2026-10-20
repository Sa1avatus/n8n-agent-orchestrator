"""Fixes for a run marked ``interrupted`` while its ``claude -p`` process lives.

The hypotheses come from the "Runner process lifecycle" notes in
``docs/guards-notes.md``:

1. restart/reload of the runner while the previous instance is still alive;
2. a second ``RunManager``/``Store`` opened on the same data directory;
3. a shutdown that does not wait for the pump;
4. a run cancelled during shutdown marked ``interrupted`` by ``_finish``
   (ruled out: the process is terminated on that path, so it is not a
   process-still-alive hypothesis).

Tests 1 and 2 now prove the fix: a run row carries the pid of the manager that
started it, and ``interrupt_active_runs`` rewrites only rows whose owner pid is
no longer alive, so neither a second app start nor a second ``RunManager`` on
the shared ``runner.sqlite`` touches a live run. Test 3 proves the shutdown
fix: every exit path of ``_execute`` terminates the process tree
(``_terminate_proc``, runs.py) and ``_finish`` closes the row as
``interrupted`` before shutdown completes, so nothing is left alive behind an
``interrupted`` row. Test 4 documents hypothesis 4 as ruled out with evidence.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.runs import RunManager
from claude_runner.store import Store

from .conftest import wait_for


def start_app(app: TestClient, input: str) -> dict[str, object]:
    response = app.post("/v1/runs", json={"input": input, "model": "sonnet"})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _alive(pid: int) -> bool:
    """Running, not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _running_pid(app: TestClient, run_id: str) -> int:
    manager = app.app.state.manager
    proc = manager._procs.get(run_id)
    assert proc is not None, "the run's CLI process is not tracked"
    return proc.pid


def _db_status(settings: Settings, run_id: str) -> dict[str, object] | None:
    store = Store(settings.data_dir / "runner.sqlite")
    try:
        return store.get_run(run_id)
    finally:
        store.close()


# --------------------------------------------------------------------------- 1
def test_restart_while_old_instance_alive_does_not_mark_running_row(
    settings: Settings,
) -> None:
    """Hypothesis 1 (fixed): the new runner's ``startup`` must not mark the
    previous instance's live run as ``interrupted``; the row carries the owner
    pid, which is still alive, so ``interrupt_active_runs`` leaves it alone."""
    with TestClient(create_app(settings)) as app1:
        run = start_app(app1, "slow [[sleep:30]]")
        run_id = str(run["run_id"])
        wait_for(app1, run_id, lambda r: r["status"] == "running", timeout=5)
        pid = _running_pid(app1, run_id)
        with TestClient(create_app(settings)) as app2:
            assert app2.get("/health").json()["interrupted_on_start"] == 0
            # the live run keeps its running row while its CLI process is alive
            assert _db_status(settings, run_id)["status"] == "running"
            assert _alive(pid)


# --------------------------------------------------------------------------- 2
def test_second_manager_on_shared_db_does_not_mark_running_row(
    settings: Settings,
) -> None:
    """Hypothesis 2 (fixed): a second ``RunManager`` (or ``Store``) on the same
    data directory must not interrupt the first instance's live runs: their
    owner pid is still alive, so ``interrupt_active_runs`` leaves them alone."""
    with TestClient(create_app(settings)) as app1:
        run = start_app(app1, "slow [[sleep:30]]")
        run_id = str(run["run_id"])
        wait_for(app1, run_id, lambda r: r["status"] == "running", timeout=5)
        pid = _running_pid(app1, run_id)
        second = RunManager(settings)  # e.g. a dashboard/CLI tool importing the store
        try:
            count = asyncio.run(second.startup())
        finally:
            second.store.close()
        assert count == 0
        # the live run keeps its running row; nothing interrupted it
        row = _db_status(settings, run_id)
        assert row["status"] == "running"
        assert _running_pid(app1, run_id) == pid
        assert _alive(pid)


# --------------------------------------------------------------------------- 3
def test_shutdown_closes_run_before_process_is_terminated(
    settings: Settings,
) -> None:
    """Hypothesis 3 (fixed): a run cancelled during shutdown is closed as
    ``interrupted`` by ``_finish`` *before* shutdown completes, and its CLI
    process is terminated (the ``finally`` of ``_execute`` runs
    ``_terminate_proc`` on every exit path, so a task cancelled mid-pump can
    no longer die before ``_finish``). Nothing is left alive behind an
    ``interrupted`` row, and the next startup has no orphan to rewrite."""
    object.__setattr__(settings, "max_run_seconds", 1)
    object.__setattr__(settings, "interrupt_grace_seconds", 1.0)
    with TestClient(create_app(settings)) as app1:
        run = start_app(app1, "sleep [[sleep:180]]")
        run_id = str(run["run_id"])
        wait_for(app1, run_id, lambda r: r["status"] == "running", timeout=10)
        pid = _running_pid(app1, run_id)
    row = _db_status(settings, run_id)
    assert row["status"] == "interrupted"
    # the CLI process was terminated during shutdown; nothing is left alive
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


# --------------------------------------------------------------------------- 4
def test_shutdown_cancelled_run_is_interrupted_but_process_is_terminated(
    settings: Settings,
) -> None:
    """Hypothesis 4: a run cancelled during shutdown is marked ``interrupted`` by
    ``_finish`` (runs.py:451-456), not by ``interrupt_active_runs``; the process
    IS terminated via ``cancel`` -> ``_stop`` (runs.py:542), so this is an
    ``interrupted`` path but not a process-still-alive hypothesis. Ruled out."""
    with TestClient(create_app(settings)) as app1:
        run = start_app(app1, "slow [[sleep:30]]")
        run_id = str(run["run_id"])
        wait_for(app1, run_id, lambda r: r["status"] == "running", timeout=5)
        pid = _running_pid(app1, run_id)
    row = _db_status(settings, run_id)
    assert row["status"] == "interrupted"
    assert "shutdown" in str(row["error_message"])
    # the CLI process was terminated by the shutdown path; nothing is left alive
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


# --------------------------------------------------------------------------- 5
def test_old_schema_gets_owner_pid_column(tmp_path: Path) -> None:
    """A database created before the owner check (no ``owner_pid`` column) is
    migrated: ``Store`` adds the column, and ``create_run`` accepts an owner
    token without error."""
    path = tmp_path / "old_runner.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(
            """CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL DEFAULT 'run',
                session_id TEXT NOT NULL,
                claude_session_id TEXT NOT NULL,
                session_created INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL,
                input TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                output TEXT NOT NULL DEFAULT '',
                error_code TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                http_code INTEGER,
                cost_usd REAL,
                num_turns INTEGER,
                context_tokens INTEGER,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )"""
        )
        db.execute("CREATE INDEX runs_by_session ON runs(session_id, status)")
    store = Store(path)
    try:
        store.create_run(
            run_id="run_old",
            session_id="s1",
            claude_session_id="cs1",
            cwd="/tmp",
            owner_pid="1:boot",
        )
        row = store.get_run("run_old")
        assert row["owner_pid"] == "1:boot"
    finally:
        store.close()


# --------------------------------------------------------------------------- 6
def test_row_with_old_token_format_is_interrupted(tmp_path: Path) -> None:
    """A row carrying a bare pid (the old token format, no boot id) is not a
    live owner: it belongs to a manager instance whose token is no longer in
    the registry, so ``interrupt_active_runs`` rewrites it even when the pid
    matches the current process. Count is 1 and the row is ``interrupted``."""
    path = tmp_path / "runner.sqlite"
    store = Store(path)
    try:
        store.create_run(
            run_id="run_old",
            session_id="s1",
            claude_session_id="cs1",
            cwd="/tmp",
            owner_pid=str(os.getpid()),
        )
        count = store.interrupt_active_runs("reason")
        assert count == 1
        assert store.get_run("run_old")["status"] == "interrupted"
    finally:
        store.close()
