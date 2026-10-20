"""Cancel and timeout end the CLI's whole process group.

The stub CLI (the leader) starts a child, and the child starts a grandchild; each one
writes a marker when it receives SIGTERM. The leader exits on SIGINT, as the real CLI
does, so the child and grandchild stay in its group and must get SIGTERM (marker
written) before any SIGKILL. The stub is a real subprocess; no real claude runs.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings

from .conftest import wait_for

STUB_BODY = '''\
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

role, out = sys.argv[1], Path(sys.argv[2])


def leave_on_term(marker):
    def handler(sig, frame):
        marker.write_text("term")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)


if role == "grandchild":
    leave_on_term(out / "grandchild.term")
    time.sleep(60)
elif role == "child":
    leave_on_term(out / "child.term")
    grandchild = subprocess.Popen(
        [sys.executable, __file__, "grandchild", str(out)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (out / "grandchild.pid").write_text(str(grandchild.pid))
    (out / "child.pid").write_text(str(os.getpid()))
    time.sleep(60)
else:
    sys.stdin.read()
    subprocess.Popen(
        [sys.executable, __file__, "child", str(out)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(60)
'''


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _pids(out: Path, timeout: float = 5) -> tuple[int, int]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        child_file, grandchild_file = out / "child.pid", out / "grandchild.pid"
        if child_file.exists() and grandchild_file.exists():
            child, grandchild = child_file.read_text().strip(), grandchild_file.read_text().strip()
            if child and grandchild:
                return int(child), int(grandchild)
        time.sleep(0.02)
    raise AssertionError("the stub's child and grandchild did not start")


def _wait_gone(pids: list[int], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.02)


def _start(client: TestClient) -> str:
    response = client.post("/v1/runs", json={"input": "tree", "model": "sonnet"})
    assert response.status_code == 200, response.text
    return str(response.json()["run_id"])


def _leader(client: TestClient, run_id: str):
    manager = client.app.state.manager
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        proc = manager._procs.get(run_id)
        if proc is not None:
            return proc
        time.sleep(0.02)
    raise AssertionError("the run's CLI process is not tracked")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    out = tmp_path / "tree"
    out.mkdir()
    stub = tmp_path / "stub_cli.py"
    stub.write_text(STUB_BODY)
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{stub}" main "{out}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return out


@pytest.fixture
def settings(tmp_path: Path, tree: Path) -> Settings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
            "CLAUDE_RUNNER_CLAUDE_BIN": str(tmp_path / "claude"),
            "CLAUDE_RUNNER_WORKSPACE": str(workspace),
        }
    )
    object.__setattr__(s, "interrupt_grace_seconds", 2.0)
    return s


def test_cli_runs_in_its_own_process_group(settings: Settings, tree: Path) -> None:
    with TestClient(create_app(settings)) as client:
        run_id = _start(client)
        proc = _leader(client, run_id)
        child, grandchild = _pids(tree)
        assert os.getpgid(proc.pid) == proc.pid
        assert os.getpgid(0) != proc.pid
        assert os.getpgid(child) == proc.pid
        assert os.getpgid(grandchild) == proc.pid
        client.post(f"/v1/runs/{run_id}/cancel")
        wait_for(client, run_id, timeout=10)


def test_cancel_ends_the_child_and_grandchild_with_sigterm_first(
    settings: Settings, tree: Path
) -> None:
    with TestClient(create_app(settings)) as client:
        run_id = _start(client)
        proc = _leader(client, run_id)
        child, grandchild = _pids(tree)
        cancelled = client.post(f"/v1/runs/{run_id}/cancel").json()
        assert cancelled["status"] == "cancelled"
        _wait_gone([proc.pid, child, grandchild])
        assert not any(_alive(pid) for pid in (proc.pid, child, grandchild))
        assert (tree / "child.term").exists(), "the child got no SIGTERM before the kill"
        assert (tree / "grandchild.term").exists(), "the grandchild got no SIGTERM"
        assert run_id not in client.app.state.manager._procs


def test_timeout_ends_the_child_and_grandchild_with_sigterm_first(
    settings: Settings, tree: Path
) -> None:
    object.__setattr__(settings, "max_run_seconds", 1)
    with TestClient(create_app(settings)) as client:
        run_id = _start(client)
        proc = _leader(client, run_id)
        child, grandchild = _pids(tree)
        done = wait_for(client, run_id, timeout=15)
        assert done["status"] == "failed" and done["error"]["code"] == "timeout"
        _wait_gone([proc.pid, child, grandchild])
        assert not any(_alive(pid) for pid in (proc.pid, child, grandchild))
        assert (tree / "child.term").exists(), "the child got no SIGTERM before the kill"
        assert (tree / "grandchild.term").exists(), "the grandchild got no SIGTERM"
        assert run_id not in client.app.state.manager._procs
