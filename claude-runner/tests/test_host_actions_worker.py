from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from claude_runner.host_actions import HOST_ACTIONS_FLAG

OPS_DIR = Path(__file__).resolve().parents[1].parent / "data-tables" / "ops"
WORKER_PATH = OPS_DIR / "host_actions_worker.py"
CONFIRMED_ROW = {
    "id": "req-1",
    "action": "restart",
    "service": "ahawr-qa",
    "reason": "test",
    "tier": "AUTO",
    "state": "confirmed",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "requested_by": "run-1",
}


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


worker = _load("host_actions_worker", WORKER_PATH)
_real_queue_paused = worker._queue_paused


@pytest.fixture(autouse=True)
def _queue_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "_queue_paused", lambda: False)


def _fake_queue_check(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    returncode: int = 0,
    platform: str = "win32",
) -> None:
    windows = platform == "win32"
    argv_expected = (
        worker.QUEUE_PROCESS_WINDOWS_ARGV if windows else worker.QUEUE_PROCESS_POSIX_ARGV
    )
    monkeypatch.setattr(worker, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(worker, "QUEUE_PROCESS_ARGV", argv_expected)

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert argv == argv_expected
        return subprocess.CompletedProcess(argv, returncode, stdout, "")

    monkeypatch.setattr(worker.subprocess, "run", fake_run)


def test_queue_paused_on_windows_when_no_queue_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_queue_check(monkeypatch, "0\r\n")
    assert _real_queue_paused() is True


def test_queue_paused_on_posix_when_pgrep_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_queue_check(monkeypatch, "", returncode=1, platform="linux")
    assert _real_queue_paused() is True


def test_queue_not_paused_on_posix_when_pgrep_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_queue_check(monkeypatch, "123\n", returncode=0, platform="linux")
    assert _real_queue_paused() is False


def test_facts_carry_the_queue_state() -> None:
    assert worker._facts_from([], [], True).queue_paused is True
    assert worker._facts_from([], []).queue_paused is False


def test_queue_not_paused_when_a_shell_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_queue_check(monkeypatch, "1\n")
    assert _real_queue_paused() is False


def test_queue_not_paused_when_the_check_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_queue_check(monkeypatch, "", returncode=1)
    assert _real_queue_paused() is False


class FakeRunner:
    def __init__(self, action_rows: list[dict[str, Any]], claimed: dict[str, Any] | None) -> None:
        self.action_rows = action_rows
        self.claimed = claimed
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        self.calls.append((method, path, payload))
        if method == "GET" and path == "/v1/host-actions":
            return self.action_rows
        if method == "GET" and path.startswith("/v1/runs"):
            return {"runs": []}
        if method == "POST" and path == "/v1/host-actions/claim":
            return self.claimed
        return {"ok": True}


def _refuse_real_subprocess(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"real subprocess started: {argv}")


def _fake_docker(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str = "done\n",
    stderr: str = "",
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(worker.subprocess, "run", fake_run)
    monkeypatch.setattr(worker.shutil, "which", lambda _name: "docker")
    return calls


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(worker.DRY_RUN_ENV, raising=False)
    monkeypatch.delenv(HOST_ACTIONS_FLAG, raising=False)
    monkeypatch.setenv(
        worker.HEARTBEAT_HOST_PATH_ENV, str(tmp_path / "heartbeat" / "host_actions_worker.json")
    )
    monkeypatch.setattr(worker.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker.subprocess, "run", _refuse_real_subprocess)


@pytest.mark.parametrize(
    ("action", "service", "expected"),
    [
        ("recreate", "n8n", ["docker", "compose", "up", "-d", "--no-deps", "n8n"]),
        ("restart", "n8n", ["docker", "restart", "n8n-autonomous-agents"]),
        ("restart", "ahawr-qa", ["docker", "restart", "ahawr-qa"]),
    ],
)
def test_dry_run_prints_the_whitelisted_argv(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    action: str,
    service: str,
    expected: list[str],
) -> None:
    monkeypatch.setenv(worker.DRY_RUN_ENV, "1")
    outcome = worker.run_action(action, service)
    assert outcome == worker.Outcome(ok=True, message="dry run")
    assert f"DRY RUN, not executed: {expected}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("action", "service", "expected"),
    [
        ("recreate", "n8n", ["docker", "compose", "up", "-d", "--no-deps", "n8n"]),
        ("restart", "claude-runner", ["docker", "restart", "claude-runner"]),
    ],
)
def test_real_run_passes_the_argv_and_fixed_project_dir(
    monkeypatch: pytest.MonkeyPatch, action: str, service: str, expected: list[str]
) -> None:
    calls = _fake_docker(monkeypatch)
    outcome = worker.run_action(action, service)
    assert outcome == worker.Outcome(ok=True, message="done\n")
    assert len(calls) == 1
    assert calls[0]["argv"] == expected
    assert calls[0]["cwd"] == worker.PROJECT_DIR


@pytest.mark.parametrize(
    ("action", "service"),
    [
        ("restart", "ahawr-postgres"),
        ("restart", "ahawr-retrieval"),
        ("restart", "unknown-service"),
        ("rm", "n8n"),
        ("recreate", "../etc"),
    ],
)
def test_non_whitelisted_service_is_refused(
    monkeypatch: pytest.MonkeyPatch, action: str, service: str
) -> None:
    calls = _fake_docker(monkeypatch)
    outcome = worker.run_action(action, service)
    assert outcome == worker.Outcome(ok=False, message="action or service is not whitelisted")
    assert calls == []


def test_extra_request_fields_never_reach_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_docker(monkeypatch)
    monkeypatch.setattr(worker, "decide", lambda *_args: (True, "may run"))
    claimed = {
        "id": "req-1",
        "action": "restart",
        "service": "ahawr-qa",
        "argv": ["docker", "system", "prune", "-af"],
        "cwd": "C:/",
        "shell": True,
        "container": "n8n-autonomous-agents",
    }
    runner = FakeRunner([CONFIRMED_ROW], claimed)
    monkeypatch.setattr(worker, "_call", runner)
    worker._handle_cycle()
    assert [call["argv"] for call in calls] == [["docker", "restart", "ahawr-qa"]]
    assert calls[0]["cwd"] == worker.PROJECT_DIR
    assert ("POST", "/v1/host-actions/req-1/result", {"ok": True, "message": "done\n"}) in (
        runner.calls
    )


@pytest.mark.parametrize("returncode", [0, 1])
def test_output_is_truncated_to_max_output_chars(
    monkeypatch: pytest.MonkeyPatch, returncode: int
) -> None:
    long_text = "x" * (worker.MAX_OUTPUT_CHARS + 50)
    _fake_docker(monkeypatch, returncode=returncode, stdout=long_text, stderr=long_text)
    outcome = worker.run_action("restart", "n8n")
    assert outcome.ok is (returncode == 0)
    assert outcome.message == "x" * worker.MAX_OUTPUT_CHARS


def test_timeout_gives_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker.shutil, "which", lambda _name: "docker")

    def timed_out(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, worker.COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(worker.subprocess, "run", timed_out)
    assert worker.run_action("restart", "n8n") == worker.Outcome(ok=False, message="timeout")


def test_docker_missing_gives_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker.shutil, "which", lambda _name: None)
    outcome = worker.run_action("restart", "n8n")
    assert outcome == worker.Outcome(ok=False, message=worker.DOCKER_MISSING_REASON)


@pytest.mark.parametrize("flag_value", [None, "0", "off"])
def test_flag_off_exits_without_polling(
    monkeypatch: pytest.MonkeyPatch, flag_value: str | None
) -> None:
    if flag_value is not None:
        monkeypatch.setenv(HOST_ACTIONS_FLAG, flag_value)

    def must_not_poll() -> int:
        raise AssertionError("polled with the flag off")

    monkeypatch.setattr(worker, "_handle_cycle", must_not_poll)
    assert worker.main([]) == 0


def test_heartbeat_file_is_written_in_tmp_path(tmp_path: Path) -> None:
    path = tmp_path / "heartbeat" / "host_actions_worker.json"
    worker._write_heartbeat("req-1")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"name", "time", "last_action"}
    assert payload["name"] == "host_actions_worker"
    assert payload["last_action"] == "req-1"
    datetime.strptime(payload["time"], "%Y-%m-%dT%H:%M:%SZ")
    assert not path.with_suffix(".json.part").exists()


def test_max_polls_ends_the_loop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(HOST_ACTIONS_FLAG, "1")
    monkeypatch.setattr(worker, "MAX_POLL_CYCLES", 3)
    cycles: list[int] = []

    def idle_cycle() -> int:
        cycles.append(1)
        return 0

    monkeypatch.setattr(worker, "_handle_cycle", idle_cycle)
    caplog.set_level("INFO", logger="host_actions_worker")
    assert worker.main([]) == 0
    assert len(cycles) == 3
    assert "poll cycle bound reached" in caplog.text
