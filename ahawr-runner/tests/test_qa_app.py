"""Real-process tests of the QA app launcher.

Every case starts a real child process through app_up on a free 127.0.0.1 port: the fake
command is a plain Python HTTP server script written into a temp directory. Nothing is
mocked in the launcher itself, so readiness, the scrubbed environment, the poll bound and
the kill-and-remove cleanup all go through the real path.

The last case starts the real mi-find-tracker project app from qa/projects.yaml: it is
opt-in and skipped unless AHAWR_QA_LIVE_PROJECT_APP=1 is set.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import qa_app, qa_regression, qa_runner
from ahawr_runner.qa_app import app_down, app_up
from ahawr_runner.qa_regression import Profile, RegressionCommand, UiConfig
from ahawr_runner.qa_stack import HEALTH_POLL_SECONDS, QaStackError, QaTargetError

SENTINEL_ENV = "AHAWR_QA_APP_SENTINEL"
PORT_ENV = "QA_APP_PORT"
HEALTH_PATH = "/healthz"
READY_TIMEOUT_SECONDS = 15
NEVER_READY_TIMEOUT_SECONDS = 2
POSIX_KILL_REASON = "the process-alive probe os.kill(pid, 0) is POSIX-only"
LIVE_PROJECT_APP_ENV = "AHAWR_QA_LIVE_PROJECT_APP"
LIVE_PROJECT_NAME = "mi-find-tracker"
PROJECTS_FILE = Path(__file__).resolve().parents[1] / "qa" / "projects.yaml"
STATUS_PATHS = ("/health", "/api/status")
STATUS_REQUEST_SECONDS = 5

FAKE_SERVER = """\
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(os.environ[sys.argv[1]])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
with open("child_pid.txt", "w", encoding="utf-8") as handle:
    handle.write(str(child.pid))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = json.dumps({"env": dict(os.environ)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _profile(working_directory: Path, start_command: list[str], ready_timeout: int) -> Profile:
    return Profile(
        name="fake-app",
        working_directory=str(working_directory),
        regression=(RegressionCommand(name="none", command="true"),),
        baseline="qa/baselines/fake-app.json",
        ui=UiConfig(
            start_command=tuple(start_command),
            env={
                "QA_APP_DATA_DIR": "{data_dir}",
                "QA_APP_EXPECTED_PORT": "{port}",
                "QA_APP_MODE": "stub",
            },
            port_env=PORT_ENV,
            health_path=HEALTH_PATH,
            ready_timeout_seconds=ready_timeout,
            scenarios_dir="fake-app",
        ),
    )


@pytest.fixture
def fake_server(tmp_path: Path) -> Path:
    script = tmp_path / "fake_app.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return script


def _read_json_env(url: str) -> dict[str, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    env: dict[str, str] = payload["env"]
    return env


def _process_alive(pid: int) -> bool:
    """True only for a running pid: a killed process that init has not reaped yet is a zombie.

    shortcut: /proc/<pid>/status is Linux-only, upgrade to `ps -o stat= -p` for a macOS runner.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return True
    status_text = status_path.read_text(encoding="utf-8")
    state_line = next((line for line in status_text.splitlines() if line.startswith("State:")), "")
    if not state_line:
        return True
    return not state_line.split(":", 1)[1].strip().startswith("Z")


def test_app_up_answers_and_app_down_kills_the_tree(tmp_path: Path, fake_server: Path) -> None:
    start_command = [sys.executable, str(fake_server), PORT_ENV]
    profile = _profile(tmp_path, start_command, READY_TIMEOUT_SECONDS)

    handle = app_up(profile)
    try:
        assert handle.base_url == f"http://127.0.0.1:{handle.port}"
        with urllib.request.urlopen(f"{handle.base_url}{HEALTH_PATH}", timeout=5) as response:
            assert response.status == 200
        assert handle.data_dir.is_dir()
        # the fake server writes its child pid into its working directory, not the data dir
        child_pid = int((tmp_path / "child_pid.txt").read_text(encoding="utf-8"))
        assert _process_alive(child_pid)
    finally:
        app_down(handle)

    assert handle.process.returncode is not None
    assert not handle.data_dir.exists()
    if sys.platform == "win32":
        pytest.skip(POSIX_KILL_REASON)
    child_pid = int((tmp_path / "child_pid.txt").read_text(encoding="utf-8"))
    assert not _process_alive(child_pid)


def test_app_up_never_healthy_is_blocked_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    command = [
        sys.executable,
        "-c",
        "import os, time; open('pid.txt', 'w').write(str(os.getpid())); time.sleep(600)",
    ]
    profile = _profile(tmp_path, command, NEVER_READY_TIMEOUT_SECONDS)

    with pytest.raises(QaStackError, match="not healthy after"):
        app_up(profile)

    pid = int((tmp_path / "pid.txt").read_text(encoding="utf-8"))
    created = [path for path in tmp_path.iterdir() if path.name.startswith("qa-app-")]
    assert not created, f"leftover data dirs: {[path.name for path in created]}"
    if sys.platform == "win32":
        pytest.skip(POSIX_KILL_REASON)
    assert not _process_alive(pid)


def test_missing_binary_is_blocked_and_leaves_no_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    profile = _profile(tmp_path, ["no-such-app-binary-xyz"], READY_TIMEOUT_SECONDS)

    with pytest.raises(QaStackError, match="no-such-app-binary-xyz"):
        app_up(profile)

    leftovers = [path for path in tmp_path.iterdir() if path.name.startswith("qa-app-")]
    assert not leftovers, f"leftover data dirs: {[path.name for path in leftovers]}"


def test_app_up_child_environment_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_server: Path
) -> None:
    monkeypatch.setenv(SENTINEL_ENV, "must-not-leak")
    start_command = [sys.executable, str(fake_server), PORT_ENV]
    profile = _profile(tmp_path, start_command, READY_TIMEOUT_SECONDS)

    handle = app_up(profile)
    try:
        env = _read_json_env(f"{handle.base_url}{HEALTH_PATH}")
        assert SENTINEL_ENV not in env
        assert env[PORT_ENV] == str(handle.port)
        assert env["QA_APP_EXPECTED_PORT"] == str(handle.port)
        assert env["QA_APP_DATA_DIR"] == str(handle.data_dir)
        assert env["QA_APP_MODE"] == "stub"
        assert "PATH" in env
    finally:
        app_down(handle)


def test_app_up_rejects_a_non_local_host(tmp_path: Path, fake_server: Path) -> None:
    start_command = [sys.executable, str(fake_server), PORT_ENV]
    profile = _profile(tmp_path, start_command, READY_TIMEOUT_SECONDS)

    with pytest.raises(QaTargetError, match="127.0.0.1"):
        app_up(profile, host="10.0.0.5")

    with pytest.raises(QaTargetError, match="127.0.0.1"):
        app_up(profile, host="app.internal.example.com")

    handle = app_up(profile, host="localhost")
    assert handle.base_url.startswith("http://localhost:")
    app_down(handle)


needs_live_project_app = pytest.mark.skipif(
    os.environ.get(LIVE_PROJECT_APP_ENV) != "1",
    reason=f"the live project app run is opt-in: set {LIVE_PROJECT_APP_ENV}=1 to run it",
)


@needs_live_project_app
def test_mi_find_tracker_project_app_answers_health_and_status(tmp_path: Path) -> None:
    """Real start of the mi-find-tracker app of qa/projects.yaml on a loopback port."""
    pytest.importorskip("yaml", reason="the qa extra (PyYAML) is needed to read projects.yaml")
    profiles = qa_regression.load_profiles(PROJECTS_FILE)
    profile = next((p for p in profiles if p.name == LIVE_PROJECT_NAME), None)
    if profile is None or profile.ui is None:
        pytest.skip(f"profile {LIVE_PROJECT_NAME!r} has no ui block in {PROJECTS_FILE}")
    if not Path(profile.working_directory).is_dir():
        pytest.skip(f"project folder {profile.working_directory} is not present")

    handle = app_up(profile)
    try:
        assert handle.base_url == f"http://127.0.0.1:{handle.port}"
        for path in STATUS_PATHS:
            with urllib.request.urlopen(
                handle.base_url + path, timeout=STATUS_REQUEST_SECONDS
            ) as response:
                assert response.status == 200, f"{path} answered {response.status}"
        scenarios = qa_runner.load_project_scenarios(profile.ui.scenarios_dir)
        assert scenarios, f"no scenarios under {profile.ui.scenarios_dir}"
        verdicts: list[dict[str, Any]] = [
            qa_runner.run_browser_scenario(scenario, runs_dir=tmp_path, app=handle)
            for scenario in scenarios
        ]
        for verdict in verdicts:
            if verdict["verdict"] == "blocked":
                # a blocked scenario in the live run means Playwright (the qa extra) is missing
                assert "Playwright" in (verdict["blocked_reason"] or ""), verdict
    finally:
        app_down(handle)

    assert handle.process.returncode is not None
    assert not handle.data_dir.exists()


def test_ready_polls_are_derived_from_ready_timeout() -> None:
    assert qa_app._ready_polls(NEVER_READY_TIMEOUT_SECONDS) == 1
    assert qa_app._ready_polls(READY_TIMEOUT_SECONDS) == READY_TIMEOUT_SECONDS // 2
    assert qa_app._ready_polls(1) >= 1
    assert qa_app._ready_polls(READY_TIMEOUT_SECONDS) * HEALTH_POLL_SECONDS <= READY_TIMEOUT_SECONDS
