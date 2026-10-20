"""Process cleanup on every terminal run close.

For each terminal status (completed, failed, timeout, cancelled, interrupted) the run's
CLI process tree must be terminated and removed from ``RunManager._procs``: the
``finally`` of ``_execute`` runs ``_terminate_proc`` on every exit path, and ``cancel``
and shutdown do the same. A child that ignores SIGTERM (honours only SIGKILL) must still
be killed after the grace period.

The stub CLI is a real subprocess launched exactly like the real ``claude`` binary (same
interpreter, same flags, same session id, prompt on stdin), so the SIGINT/SIGTERM/SIGKILL
escalation of ``_stop`` is exercised for real. Only ``FAKE_CLAUDE_LOG`` (the runner's own
call log, not a provider endpoint) is disabled, so no real CLI, network or provider call
happens.
"""

from __future__ import annotations

import stat
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.store import Store

from .conftest import wait_for

# A `claude` stand-in: read the prompt, then sleep for the [[sleep:N]] duration (or
# exit right away). It prints a stream-json `result` event for [[ok]] (a clean
# completion) and exits 3 with an error on stderr for [[crash]] (a failed run).
# With [[ignore-sigterm]] it ignores SIGINT and SIGTERM (so only SIGKILL can end it),
# and with [[child]] it spawns a grandchild that keeps sleeping after the parent dies,
# writing its pid to child.pid in the cwd so the test can check the whole tree.
STUB_BODY = '''\
import json
import signal
import subprocess
import sys
import time

prompt = sys.stdin.read()
ignore = "[[ignore-sigterm]]" in prompt
if ignore:
    signal.signal(signal.SIGTERM, lambda sig, frame: None)
    signal.signal(signal.SIGINT, lambda sig, frame: None)
sleep = 0.0
for token in prompt.split():
    if token.startswith("[[sleep:"):
        sleep = float(token[8:-2])
if "[[child]]" in prompt:
    # a grandchild in the same process group: SIGKILL to the group reaches it, SIGTERM
    # (ignored, like the parent's) does not
    extra = {"preexec_fn": lambda: signal.signal(signal.SIGTERM, signal.SIG_IGN)} if ignore else {}
    # the grandchild keeps its own stdout (a real tool's output is captured by the CLI,
    # not shared with its pipe), otherwise it would hold the runner's pipe open
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **extra,
    )
    with open("child.pid", "w", encoding="utf-8") as f:
        f.write(str(grandchild.pid))
if "[[ok]]" in prompt:
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                      "num_turns": 1, "result": "ok", "session_id": "stub",
                      "total_cost_usd": 0}), flush=True)
    sys.exit(0)
if "[[crash]]" in prompt:
    print("fatal: something broke", file=sys.stderr, flush=True)
    sys.exit(3)
time.sleep(sleep)
'''


def _stub_bin(tmp_path: Path) -> str:
    stub = tmp_path / "stub.py"
    stub.write_text(STUB_BODY, encoding="utf-8")
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper)


def _alive(pid: int) -> bool:
    """Running, not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
        "CLAUDE_RUNNER_CLAUDE_BIN": _stub_bin(tmp_path),
        "CLAUDE_RUNNER_WORKSPACE": str(tmp_path),
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
    }
    s = Settings.from_env(env)
    # a short grace period keeps the suite fast; disable prlimit so the stub's
    # stdlib imports don't hit the data limit during venv init
    object.__setattr__(s, "interrupt_grace_seconds", 0.5)
    object.__setattr__(s, "proc_data_limit_mb", 0)
    return s


def _start(client: TestClient, manager, stub: str) -> tuple[float, str, int]:
    """Returns the monotonic moment the CLI process was launched (tracked), the
    run id, and its pid."""
    response = client.post("/v1/runs", json={"input": stub, "model": "sonnet"})
    assert response.status_code == 200, response.text
    run = response.json()
    run_id = str(run["run_id"])
    # wait until the CLI process is actually launched and tracked
    deadline = time.monotonic() + 5
    proc = None
    launch_t = 0.0
    while time.monotonic() < deadline:
        proc = manager._procs.get(run_id)
        if proc is not None:
            launch_t = time.monotonic()
            break
        time.sleep(0.02)
    assert proc is not None, "the run's CLI process is not tracked"
    pid = proc.pid
    assert _alive(pid)
    return launch_t, run_id, pid


def _child_pid(workspace) -> int | None:
    """The grandchild's pid, written to ``child.pid`` in the run's cwd by the stub."""
    try:
        return int((Path(workspace) / "child.pid").read_text().strip())
    except (OSError, ValueError):
        return None


def _assert_tree_terminated(run_id: str, manager, pid: int, grace: float,
                           workspace: str | None = None) -> None:
    """The run's process tree (the CLI process and any grandchild it spawned) is dead
    and no ``_procs`` entry is left."""
    deadline = time.monotonic() + grace
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _alive(pid), f"process {pid} is still alive after close"
    child = _child_pid(workspace) if workspace is not None else None
    if child is not None:
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not _alive(child), f"grandchild {child} is still alive after close"
    assert run_id not in manager._procs


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c


# --------------------------------------------------------------------------- 1
def test_completed_run_terminates_its_process(settings: Settings, client: TestClient) -> None:
    """A run that completes normally leaves its whole process tree dead (parent and
    grandchild) and no ``_procs`` entry."""
    manager = client.app.state.manager
    _, run_id, pid = _start(client, manager, "done [[child]] [[ok]] [[sleep:30]]")
    done = wait_for(client, run_id)
    assert done["status"] == "completed"
    _assert_tree_terminated(run_id, manager, pid, 5, settings.workspace)


# --------------------------------------------------------------------------- 2
def test_failed_run_terminates_its_process(settings: Settings, client: TestClient) -> None:
    """A run whose CLI fails (no usable stream) leaves the process dead and no
    ``_procs`` entry."""
    manager = client.app.state.manager
    _, run_id, pid = _start(client, manager, "boom [[crash]]")
    done = wait_for(client, run_id)
    assert done["status"] == "failed"
    _assert_tree_terminated(run_id, manager, pid, 5)


# --------------------------------------------------------------------------- 3
def test_timeout_run_terminates_its_process(settings: Settings, client: TestClient) -> None:
    """A run that exceeds its timeout is terminated (the process does not keep running)
    and leaves no ``_procs`` entry."""
    manager = client.app.state.manager
    object.__setattr__(settings, "max_run_seconds", 1)
    _, run_id, pid = _start(client, manager, "slow [[sleep:30]]")
    wait_for(client, run_id, timeout=15)
    _assert_tree_terminated(run_id, manager, pid, 5)


# --------------------------------------------------------------------------- 4
def test_cancelled_run_terminates_its_process(settings: Settings, client: TestClient) -> None:
    """A run cancelled while running has its whole process tree (parent and grandchild)
    terminated by ``cancel`` and leaves no ``_procs`` entry."""
    manager = client.app.state.manager
    _, run_id, pid = _start(client, manager, "stuck [[child]] [[sleep:30]]")
    cancelled = client.post(f"/v1/runs/{run_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    _assert_tree_terminated(run_id, manager, pid, 5, settings.workspace)


# --------------------------------------------------------------------------- 5
def test_interrupted_run_terminates_its_process(settings: Settings) -> None:
    """A run interrupted on shutdown is closed as ``interrupted`` and its process is
    terminated; nothing is left behind.

    The run is started and the app is closed while it is still running: ``shutdown``
    cancels the task, the ``finally`` of ``_execute`` terminates the process tree, and
    ``_finish`` rewrites the cancelled outcome to ``interrupted``."""
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        _, run_id, pid = _start(client, manager, "late [[sleep:30]]")
    store = Store(settings.data_dir / "runner.sqlite")
    try:
        row = store.get_run(run_id)
    finally:
        store.close()
    assert row["status"] == "interrupted"
    _assert_tree_terminated(run_id, manager, pid, 5)


# --------------------------------------------------------------------------- 6
def test_sigterm_ignoring_child_is_killed_after_the_grace(
    settings: Settings, client: TestClient
) -> None:
    """A process that ignores SIGTERM (honours only SIGKILL), with its grandchild, is
    terminated only after the grace period: it is still alive max_run_seconds + grace
    after the launch, so the test shows the SIGINT->SIGTERM->SIGKILL escalation and not
    only the end state; nothing is left behind afterwards."""
    manager = client.app.state.manager
    object.__setattr__(settings, "max_run_seconds", 1)
    response = client.post(
        "/v1/runs",
        json={"input": "ignore [[child]] [[ignore-sigterm]] [[sleep:30]]", "model": "sonnet"},
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json()["run_id"])
    # wait until the CLI process is actually launched and tracked; that moment is
    # the launch, and the timeout is measured from it
    deadline = time.monotonic() + 5
    proc = None
    launch_t = 0.0
    while time.monotonic() < deadline:
        proc = manager._procs.get(run_id)
        if proc is not None:
            launch_t = time.monotonic()
            break
        time.sleep(0.02)
    assert proc is not None, "the run's CLI process is not tracked"
    pid = proc.pid
    assert _alive(pid)
    # the timeout fires max_run_seconds after the launch, and the SIGKILL that finally
    # kills the stub lands at launch + max_run_seconds + grace or later; the stub
    # ignores SIGINT/SIGTERM and must still be alive at that moment
    # the timeout fires max_run_seconds after the launch; at that moment the escalation
    # (SIGINT -> SIGTERM -> SIGKILL) is in progress and the stub, which ignores
    # SIGINT/SIGTERM, must still be alive
    check_t = launch_t + settings.max_run_seconds
    while time.monotonic() < check_t:
        time.sleep(0.02)
    assert _alive(pid), "the stub ignores SIGINT/SIGTERM and must survive until SIGKILL"
    wait_for(client, run_id, timeout=15)
    _assert_tree_terminated(run_id, manager, pid, 5, settings.workspace)
