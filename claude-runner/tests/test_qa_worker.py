from __future__ import annotations

import importlib.util
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from claude_runner import qa_agent_run, qa_worker
from claude_runner.guard_heartbeat import TIME_FORMAT
from claude_runner.qa_requests import QA_UI_FILE_PATTERN
from claude_runner.qa_stack import QaStack, QaStackError

OPS_DIR = Path(__file__).resolve().parents[1].parent / "data-tables" / "ops"
PATCH_FILE = OPS_DIR / "qa_workflow_patch.py"
SCENARIO = "qa"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(folder: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _repo_with_commit(folder: Path, files: dict[str, str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    _git(folder, "init", "-q")
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    _git(folder, "add", "-A")
    _git(folder, "commit", "-q", "-m", "init")


def _patch_heartbeat(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(qa_worker, "_heartbeat_path", lambda: path)


def test_stack_id_is_parsed_from_the_last_stderr_line() -> None:
    stderr = "\n".join(
        ["", "stack id: 1a2b3c4d", "stack id: 5e6f7a8b", "traceback omitted"]
    )
    assert qa_worker._stack_id_from_stderr(stderr) == "5e6f7a8b"


def test_stack_id_is_absent_when_nothing_was_printed() -> None:
    assert qa_worker._stack_id_from_stderr("no id here\n") is None


def test_changed_tracked_ui_file_runs_the_qa(tmp_path: Path) -> None:
    _repo_with_commit(tmp_path, {"board.js": "a", "README.md": "a"})
    (tmp_path / "board.js").write_text("b", encoding="utf-8")
    assert qa_worker.early_verdict(SCENARIO, str(tmp_path)) is None


def test_no_ui_file_changed_skips_the_qa(tmp_path: Path) -> None:
    _repo_with_commit(tmp_path, {"app.py": "a", "README.md": "a"})
    (tmp_path / "README.md").write_text("b", encoding="utf-8")
    verdict: dict[str, Any] | None = qa_worker.early_verdict(SCENARIO, str(tmp_path))
    assert verdict is not None and verdict["verdict"] == "skipped"


def test_untracked_ui_file_runs_the_qa(tmp_path: Path) -> None:
    _repo_with_commit(tmp_path, {"README.md": "a"})
    (tmp_path / "dashboard.html").write_text("<p>new</p>", encoding="utf-8")
    assert qa_worker.early_verdict(SCENARIO, str(tmp_path)) is None


def test_non_git_folder_is_blocked(tmp_path: Path) -> None:
    (tmp_path / "board.js").write_text("a", encoding="utf-8")
    verdict: dict[str, Any] | None = qa_worker.early_verdict(SCENARIO, str(tmp_path))
    assert verdict is not None and verdict["verdict"] == "blocked"


def test_missing_folder_is_blocked(tmp_path: Path) -> None:
    verdict: dict[str, Any] | None = qa_worker.early_verdict(SCENARIO, str(tmp_path / "absent"))
    assert verdict is not None and verdict["verdict"] == "blocked"


def _recording_runners(
    calls: list[str], verdict: str = "pass"
) -> dict[str, qa_worker.LayerRunner]:
    def runner(layer: str) -> qa_worker.LayerRunner:
        def run(_timeout_seconds: float) -> list[dict[str, Any]]:
            calls.append(layer)
            return [{"scenario": layer, "verdict": verdict}]

        return run

    return {layer: runner(layer) for layer in qa_worker.LAYER_TIMEOUT_SECONDS}


def test_layers_run_in_regression_api_ui_order() -> None:
    calls: list[str] = []

    verdicts = qa_worker.run_layers(["ui", "regression", "api"], _recording_runners(calls))

    assert calls == ["regression", "api", "ui"]
    assert [verdict["layer"] for verdict in verdicts] == ["regression", "api", "ui"]


def test_failed_first_layer_does_not_skip_the_others() -> None:
    calls: list[str] = []
    runners = _recording_runners(calls)
    runners["regression"] = lambda _timeout: [{"scenario": "regression", "verdict": "fail"}]

    verdicts = qa_worker.run_layers(["regression", "api", "ui"], runners)

    assert calls == ["api", "ui"]
    assert verdicts[0] == {"scenario": "regression", "verdict": "fail", "layer": "regression"}


def test_layer_over_its_timeout_gets_a_timeout_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(qa_worker.LAYER_TIMEOUT_SECONDS, "api", 0.2)
    release = threading.Event()
    calls: list[str] = []
    runners = _recording_runners(calls)

    def stuck(_timeout_seconds: float) -> list[dict[str, Any]]:
        release.wait(timeout=10)
        return []

    runners["api"] = stuck
    try:
        verdicts = qa_worker.run_layers(["api", "ui"], runners)
    finally:
        release.set()

    assert verdicts[0]["layer"] == "api" and verdicts[0]["verdict"] == "timeout"
    assert verdicts[1] == {"scenario": "ui", "verdict": "pass", "layer": "ui"}
    assert calls == ["ui"]


def test_layer_that_raises_gets_an_error_verdict_with_the_reason() -> None:
    calls: list[str] = []
    runners = _recording_runners(calls)

    def broken(_timeout_seconds: float) -> list[dict[str, Any]]:
        raise RuntimeError("regression tool crashed")

    runners["regression"] = broken

    verdicts = qa_worker.run_layers(["regression", "api"], runners)

    assert verdicts[0]["layer"] == "regression" and verdicts[0]["verdict"] == "error"
    assert verdicts[0]["failed_reason"] == "RuntimeError: regression tool crashed"
    assert calls == ["api"]


def test_outer_bound_gives_timeout_to_layers_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_worker, "RUN_TIMEOUT_SECONDS", 0.2)
    calls: list[str] = []
    runners = _recording_runners(calls)

    def slow(_timeout_seconds: float) -> list[dict[str, Any]]:
        time.sleep(0.3)
        return [{"scenario": "regression", "verdict": "pass"}]

    runners["regression"] = slow

    verdicts = qa_worker.run_layers(["regression", "api"], runners)

    assert [verdict["verdict"] for verdict in verdicts] == ["timeout", "timeout"]
    assert verdicts[1]["failed_reason"] == "request outer bound reached"
    assert calls == []


def test_unknown_layer_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown QA layer"):
        qa_worker.run_layers(["perf"], {})


def test_smoke_is_skipped_when_the_module_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    assert qa_worker.smoke_enabled({"smoke": True}) is False


def test_smoke_needs_the_option_and_the_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    assert qa_worker.smoke_enabled({"smoke": True}) is True
    assert qa_worker.smoke_enabled({"smoke": False}) is False
    assert qa_worker.smoke_enabled({}) is False


def test_host_path_is_mapped_through_config_path_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_RUNNER_PATH_MAP", r"D:\n8n=/mapped")
    mapped = qa_worker.container_path(r"D:\n8n\workspace\board.js")
    assert Path(mapped) == Path("/mapped/workspace/board.js")


def test_worker_uses_the_shared_ui_pattern_and_the_patch_has_none() -> None:
    # the UI file decision moved to the host worker; the workflow patch only asks and waits
    patch = _load("qa_workflow_patch", PATCH_FILE)
    assert qa_worker.UI_FILE_RE.pattern == QA_UI_FILE_PATTERN
    assert not hasattr(patch, "UI_PATTERN_SRC")


POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt" or not Path("/proc").is_dir(),
    reason="needs POSIX process groups (killpg) and /proc to see a process tree",
)

STUCK_QA_BODY = """\
import os
import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
with open("qa_child.pid", "w", encoding="utf-8") as f:
    f.write(str(child.pid))
with open("qa_parent.pid", "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
print("stack id: 0a1b2c3d", file=sys.stderr, flush=True)
time.sleep(60)
"""

FAILING_QA_BODY = """\
import sys

print("stack id: 1b2c3d4e", file=sys.stderr, flush=True)
sys.exit(3)
"""


def _fake_qa_run(monkeypatch: pytest.MonkeyPatch, folder: Path, body: str) -> None:
    script = folder / "fake_qa.py"
    script.write_text(body, encoding="utf-8")
    real_popen = subprocess.Popen

    def popen(_args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        return real_popen([sys.executable, str(script)], cwd=folder, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", popen)


def _alive(pid: int) -> bool:
    # a zombie counts as gone: it has been killed and only waits to be reaped
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def _wait_gone(pid: int, timeout: float = 5) -> bool:
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


@POSIX_ONLY
def test_timed_out_qa_run_kills_the_process_and_its_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(qa_worker, "RUN_TIMEOUT_SECONDS", 2)
    monkeypatch.setattr(qa_worker, "stack_from_id", lambda stack_id: stack_id)
    monkeypatch.setattr(qa_worker, "stack_down", lambda _stack: None)
    _fake_qa_run(monkeypatch, tmp_path, STUCK_QA_BODY)

    verdicts, error = qa_worker._execute({"working_directory": ""})

    assert verdicts == [] and error == "run printed no JSON verdicts"
    parent = int((tmp_path / "qa_parent.pid").read_text(encoding="utf-8"))
    child = int((tmp_path / "qa_child.pid").read_text(encoding="utf-8"))
    assert _wait_gone(parent), "qa process survived the timeout"
    assert _wait_gone(child), "child of the qa process survived the timeout"


def test_failed_qa_run_still_tears_the_stack_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    torn_down: list[str] = []
    monkeypatch.setattr(qa_worker, "stack_from_id", lambda stack_id: stack_id)
    monkeypatch.setattr(qa_worker, "stack_down", torn_down.append)
    _fake_qa_run(monkeypatch, tmp_path, FAILING_QA_BODY)

    verdicts, error = qa_worker._execute({"working_directory": ""})

    assert verdicts == [] and error == "run printed no JSON verdicts"
    assert torn_down == ["1b2c3d4e"]


def test_ui_layer_tears_down_the_stack_from_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    torn_down: list[str] = []
    monkeypatch.setattr(qa_worker, "stack_from_id", lambda stack_id: stack_id)
    monkeypatch.setattr(qa_worker, "stack_down", torn_down.append)
    _fake_qa_run(monkeypatch, tmp_path, FAILING_QA_BODY)

    runner = qa_worker._layer_runners({"working_directory": ""})["ui"]
    runner(30)

    assert torn_down == ["1b2c3d4e"]


def test_ui_layer_skips_and_tears_down_no_stack_when_no_ui_file_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    torn_down: list[str] = []
    monkeypatch.setattr(qa_worker, "stack_from_id", lambda stack_id: stack_id)
    monkeypatch.setattr(qa_worker, "stack_down", torn_down.append)
    _repo_with_commit(tmp_path, {"app.py": "a", "README.md": "a"})
    (tmp_path / "README.md").write_text("b", encoding="utf-8")

    runner = qa_worker._layer_runners({"working_directory": str(tmp_path)})["ui"]
    verdicts = runner(30)

    assert len(verdicts) == 1 and verdicts[0]["verdict"] == "skipped"
    assert torn_down == []


def test_two_concurrent_claims_run_one_at_a_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    started = threading.Event()
    release = threading.Event()
    served: list[str] = []

    def fake_serve(request_row: dict[str, Any]) -> None:
        served.append(str(request_row["id"]))
        started.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(qa_worker, "_serve", fake_serve)
    results: list[bool] = []
    first = threading.Thread(
        target=lambda: results.append(qa_worker._serve_locked({"id": "first"}))
    )
    first.start()
    try:
        assert started.wait(timeout=10)
        assert qa_worker._serve_locked({"id": "second"}) is False
    finally:
        release.set()
        first.join(timeout=10)

    assert results == [True]
    assert served == ["first"]
    assert not qa_worker._lock_path().exists()


def test_heartbeat_file_holds_name_time_and_last_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "heartbeat" / "qa_worker.json"
    _patch_heartbeat(monkeypatch, path)

    qa_worker._write_heartbeat("idle")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"name", "time", "last_action"}
    assert payload["name"] == "qa_worker"
    assert payload["last_action"] == "idle"
    datetime.strptime(payload["time"], TIME_FORMAT)
    assert not path.with_suffix(".json.part").exists()


def test_regression_command_success_returns_output_and_logs_under_runs_dir(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    runs_dir = tmp_path / "qa-runs"
    command = shlex.join([sys.executable, "-c", "print('all good')"])

    outcome = qa_worker.run_command(command, str(folder), timeout_seconds=30, runs_dir=runs_dir)

    assert outcome.output == "all good\n"
    assert outcome.exit_code == 0
    assert not outcome.timed_out and not outcome.missing_command
    assert outcome.log_path is not None and outcome.log_path.parent == runs_dir
    assert outcome.log_path.read_text(encoding="utf-8") == "all good\n"
    assert list(folder.iterdir()) == []


def test_regression_command_failure_returns_exit_code_and_output(tmp_path: Path) -> None:
    code = "import sys; print('1 failed'); sys.exit(3)"
    command = shlex.join([sys.executable, "-c", code])

    outcome = qa_worker.run_command(
        command, str(tmp_path), timeout_seconds=30, runs_dir=tmp_path / "qa-runs"
    )

    assert outcome.exit_code == 3
    assert outcome.output == "1 failed\n"
    assert not outcome.timed_out and not outcome.missing_command


@POSIX_ONLY
def test_regression_command_timeout_kills_the_process_and_its_child(tmp_path: Path) -> None:
    command = shlex.join([sys.executable, "-c", STUCK_QA_BODY])

    outcome = qa_worker.run_command(
        command, str(tmp_path), timeout_seconds=2, runs_dir=tmp_path / "qa-runs"
    )

    assert outcome.timed_out and not outcome.missing_command
    assert outcome.log_path is not None and outcome.log_path.is_file()
    parent = int((tmp_path / "qa_parent.pid").read_text(encoding="utf-8"))
    child = int((tmp_path / "qa_child.pid").read_text(encoding="utf-8"))
    assert _wait_gone(parent), "regression command survived the timeout"
    assert _wait_gone(child), "child of the regression command survived the timeout"


def test_missing_regression_executable_is_reported_not_raised(tmp_path: Path) -> None:
    runs_dir = tmp_path / "qa-runs"

    outcome = qa_worker.run_command(
        "no-such-regression-tool --check", str(tmp_path), timeout_seconds=30, runs_dir=runs_dir
    )

    assert outcome.missing_command and outcome.exit_code is None
    assert outcome.log_path is None and not runs_dir.exists()


class _FakeResponse:
    def __init__(self, payload: dict[str, Any] | bytes = b"{}") -> None:
        if isinstance(payload, bytes):
            self._data = payload
        else:
            self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _fake_runner(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None = None
) -> list[urllib.request.Request]:
    sent: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        sent.append(request)
        if error is not None:
            raise error
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def test_empty_api_key_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(qa_worker.BEARER_ENV, "")
    sent = _fake_runner(monkeypatch)

    qa_worker.claim("req-1")

    assert len(sent) == 1 and sent[0].get_header("Authorization") is None


def test_bearer_appears_in_no_log_line_and_no_posted_verdict(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    bearer = "made-up-bearer-5d9e1c"
    heartbeat = tmp_path / "heartbeat" / "qa_worker.json"
    monkeypatch.setenv(qa_worker.BEARER_ENV, bearer)
    _patch_heartbeat(monkeypatch, heartbeat)
    monkeypatch.setattr(qa_worker, "_execute", lambda _row: (["pass"], ""))
    caplog.set_level(logging.DEBUG)

    sent = _fake_runner(monkeypatch)
    qa_worker._serve_locked({"id": "req-1"})
    claim, result = sent
    assert claim.get_header("Authorization") == f"Bearer {bearer}"
    assert json.loads(cast(bytes, result.data or b"")) == {"verdicts": ["pass"], "error": ""}

    failing = _fake_runner(monkeypatch, error=urllib.error.URLError("connection refused"))
    qa_worker._serve_locked_safely({"id": "req-2"})
    assert failing and "not reported" in caplog.text

    assert bearer not in caplog.text
    assert bearer not in heartbeat.read_text(encoding="utf-8")
    bodies = [cast(bytes, request.data or b"").decode() for request in sent + failing]
    assert all(bearer not in body for body in bodies)


def test_request_without_layers_posts_the_same_verdict_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # records the verdict body the worker posted before the layers wiring; a request
    # without layers must post exactly those bytes
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    monkeypatch.setattr(qa_worker, "_run_child", lambda: (["pass", "skipped"], None, ""))
    sent = _fake_runner(monkeypatch)

    qa_worker._serve_locked({"id": "req-golden"})

    assert sent[1].data == b'{"verdicts": ["pass", "skipped"], "error": ""}'


def test_layers_regression_ui_post_one_combined_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    calls: list[str] = []
    monkeypatch.setattr(qa_worker, "_layer_runners", lambda _row: _recording_runners(calls))
    sent = _fake_runner(monkeypatch)

    qa_worker._serve_locked({"id": "req-layers", "layers": ["regression", "ui"]})

    body = json.loads(cast(bytes, sent[1].data or b""))
    assert body == {
        "verdicts": [
            {"scenario": "regression", "verdict": "pass", "layer": "regression"},
            {"scenario": "ui", "verdict": "pass", "layer": "ui"},
        ],
        "error": "",
    }
    assert calls == ["regression", "ui"]


# --- T013: agent layer integration tests ---

_NO_SLEEP = lambda _seconds: None  # noqa: E731


def _fake_contracts_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "contracts"
    root.mkdir()
    monkeypatch.setattr(qa_worker, "CONTRACTS_ROOT", root)
    return root


def _write_contract(root: Path, mission_id: str, items: list[str]) -> Path:
    contract_dir = root / mission_id
    contract_dir.mkdir(parents=True)
    lines = [f"# Board: {mission_id}", ""]
    lines.append("## Screen 1")
    lines.append("- Open: route /home")
    lines.append("- Viewport: 1280x800")
    lines.append("- Layout: cards")
    lines.append("- Reference image: ref.png")
    for i, text in enumerate(items, start=1):
        lines.append(f"- C{i}: {text}")
    lines.append("")
    (contract_dir / "contract.md").write_text("\n".join(lines), encoding="utf-8")
    (contract_dir / "ref.png").write_text("fake-png", encoding="utf-8")
    return contract_dir


def _fake_capture(monkeypatch: pytest.MonkeyPatch, manifest: list[dict[str, str]]) -> None:
    def fake_capture(contract: Any) -> tuple[list[dict[str, str]], str]:
        return manifest, ""

    monkeypatch.setattr(qa_worker, "_capture_agent_screens", fake_capture)


class _FakeStack:
    dashboard_url = "http://127.0.0.1:1234"


def mock_stack() -> _FakeStack:
    return _FakeStack()


def _combined_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    agent_payloads: list[dict[str, Any]] | None,
) -> list[urllib.request.Request]:
    """A urlopen fake that handles both QA worker API calls and agent run calls.

    Non-run requests (claim, result) get ``{}``; run-related requests consume the
    agent payloads in order.
    """
    sent: list[urllib.request.Request] = []
    run_index = [0]

    def urlopen(request: urllib.request.Request, timeout: float) -> _FakeResponse:
        sent.append(request)
        url = request.full_url
        if "/v1/runs" in url:
            if agent_payloads is None:
                raise urllib.error.URLError("unexpected agent run call")
            index = min(run_index[0], len(agent_payloads) - 1)
            run_index[0] += 1
            return _FakeResponse(json.dumps(agent_payloads[index]).encode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return sent


def _agent_all_pass_payloads() -> list[dict[str, Any]]:
    final_text = json.dumps([
        {"item": "C1", "verdict": "pass", "evidence": "run/Screen.png", "reason": ""},
        {"item": "C2", "verdict": "pass", "evidence": "run/Screen 1.png", "reason": ""},
    ])
    return [
        {"run_id": "run-agent-1"},
        {"status": "completed", "output": final_text},
    ]


def _agent_payloads_with(final_text: str, run_id: str) -> list[dict[str, Any]]:
    return [{"run_id": run_id}, {"status": "completed", "output": final_text}]


def test_flag_off_without_layers_posts_t012_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    monkeypatch.setattr(qa_worker, "_run_child", lambda: (["pass", "skipped"], None, ""))
    sent = _fake_runner(monkeypatch)

    qa_worker._serve_locked({"id": "req-golden"})

    assert sent[1].data == b'{"verdicts": ["pass", "skipped"], "error": ""}'


def test_flag_off_with_scripted_layers_has_no_agent_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    calls: list[str] = []
    monkeypatch.setattr(qa_worker, "_layer_runners", lambda _row: _recording_runners(calls))
    sent = _fake_runner(monkeypatch)

    qa_worker._serve_locked({"id": "req-layers", "layers": ["regression", "ui"]})

    body = json.loads(cast(bytes, sent[1].data or b""))
    assert all("agent" not in str(v.get("layer", "")) for v in body["verdicts"])
    assert body["error"] == ""


def test_flag_off_with_agent_layer_runs_no_capture_no_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the flag off, layer 'agent' is ignored: no capture, no agent run."""
    calls: list[str] = []

    def _record_capture(_contract: Any) -> tuple[list[dict[str, str]], str]:
        calls.append("capture")
        return [], ""

    def _record_run(_prompt: str, _wd: str, **_: Any) -> qa_agent_run.AgentOutcome:
        calls.append("run_agent")
        return qa_agent_run.AgentOutcome("completed", final_text="[]")

    monkeypatch.setattr(qa_worker, "_capture_agent_screens", _record_capture)
    monkeypatch.setattr(qa_agent_run, "run_agent", _record_run)
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    _fake_runner(monkeypatch)

    qa_worker._serve_locked({
        "id": "req-flagoff-agent",
        "layers": ["agent"],
        "mission_id": "mission-x",
        "working_directory": str(tmp_path),
        "options": {},
    })

    assert calls == []


def test_flag_on_all_pass_gateway_passes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _fake_contracts_root(monkeypatch, tmp_path)
    _write_contract(root, "mission-1", ["check C1", "check C2"])
    _fake_capture(monkeypatch, [
        {"screen": "Screen 1", "png": "run/Screen 1.png", "dom": "run/Screen 1.dom.txt", "error": ""},  # noqa: E501  # noqa: E501
    ])
    monkeypatch.setattr(qa_agent_run.time, "sleep", _NO_SLEEP)
    monkeypatch.setattr(qa_worker, "stack_up", lambda mode="docker": mock_stack())
    monkeypatch.setattr(qa_worker, "stack_down", lambda _stack: None)
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    sent = _combined_urlopen(monkeypatch, _agent_all_pass_payloads())

    qa_worker._serve_locked({
        "id": "req-agent",
        "layers": ["agent"],
        "mission_id": "mission-1",
        "working_directory": "/w",
        "options": {"qa_agent": True},
    })

    result_request = [r for r in sent if r.full_url.endswith("/result")][0]
    body = json.loads(cast(bytes, result_request.data or b""))
    agent_verdicts = [v for v in body["verdicts"] if v.get("layer") == "agent"]
    assert len(agent_verdicts) == 2
    assert all(v["verdict"] == "pass" for v in agent_verdicts)
    assert body["error"] == ""


def test_flag_on_one_fail_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _fake_contracts_root(monkeypatch, tmp_path)
    _write_contract(root, "mission-2", ["check C1"])
    _fake_capture(monkeypatch, [
        {"screen": "Screen 1", "png": "run/Screen 1.png", "dom": "run/Screen 1.dom.txt", "error": ""},  # noqa: E501
    ])
    final_text = json.dumps([
        {"item": "C1", "verdict": "fail", "evidence": "run/Screen 1.png", "reason": "button missing"},  # noqa: E501
    ])
    monkeypatch.setattr(qa_agent_run.time, "sleep", _NO_SLEEP)
    monkeypatch.setattr(qa_worker, "stack_up", lambda mode="docker": mock_stack())
    monkeypatch.setattr(qa_worker, "stack_down", lambda _stack: None)
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    sent = _combined_urlopen(monkeypatch, _agent_payloads_with(final_text, "run-agent-2"))

    qa_worker._serve_locked({
        "id": "req-fail",
        "layers": ["agent"],
        "mission_id": "mission-2",
        "working_directory": "/w",
        "options": {"qa_agent": True},
    })

    result_request = [r for r in sent if r.full_url.endswith("/result")][0]
    body = json.loads(cast(bytes, result_request.data or b""))
    agent_verdicts = [v for v in body["verdicts"] if v.get("layer") == "agent"]
    assert agent_verdicts[0]["verdict"] == "fail"
    assert "C1" in body["error"]
    assert "button missing" in body["error"]


def test_flag_on_one_blocked_fails_the_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _fake_contracts_root(monkeypatch, tmp_path)
    _write_contract(root, "mission-3", ["check C1"])
    _fake_capture(monkeypatch, [
        {"screen": "Screen 1", "png": "run/Screen 1.png", "dom": "run/Screen 1.dom.txt", "error": ""},  # noqa: E501
    ])
    final_text = json.dumps([
        {"item": "C1", "verdict": "blocked", "evidence": "", "reason": "route not found"},
    ])
    monkeypatch.setattr(qa_agent_run.time, "sleep", _NO_SLEEP)
    monkeypatch.setattr(qa_worker, "stack_up", lambda mode="docker": mock_stack())
    monkeypatch.setattr(qa_worker, "stack_down", lambda _stack: None)
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    sent = _combined_urlopen(monkeypatch, _agent_payloads_with(final_text, "run-agent-3"))

    qa_worker._serve_locked({
        "id": "req-blocked",
        "layers": ["agent"],
        "mission_id": "mission-3",
        "working_directory": "/w",
        "options": {"qa_agent": True},
    })

    result_request = [r for r in sent if r.full_url.endswith("/result")][0]
    body = json.loads(cast(bytes, result_request.data or b""))
    agent_verdicts = [v for v in body["verdicts"] if v.get("layer") == "agent"]
    assert agent_verdicts[0]["verdict"] == "blocked"
    assert "C1" in body["error"]
    assert "route not found" in body["error"]


def test_no_contract_keeps_scripted_verdicts_and_adds_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_contracts_root(monkeypatch, tmp_path)
    _fake_capture(monkeypatch, [])
    _patch_heartbeat(monkeypatch, tmp_path / "heartbeat" / "qa_worker.json")
    calls: list[str] = []
    monkeypatch.setattr(qa_worker, "_layer_runners", lambda _row: _recording_runners(calls))
    sent = _fake_runner(monkeypatch)

    qa_worker._serve_locked({
        "id": "req-nocontract",
        "layers": ["regression", "ui", "agent"],
        "mission_id": "mission-missing",
        "working_directory": str(tmp_path),
        "options": {"qa_agent": True},
    })

    result_request = [r for r in sent if r.full_url.endswith("/result")][0]
    body = json.loads(cast(bytes, result_request.data or b""))
    scripted = [v for v in body["verdicts"] if v.get("layer") in ("regression", "ui")]
    assert len(scripted) == 2
    assert all(v["verdict"] == "pass" for v in scripted)
    agent = [v for v in body["verdicts"] if v.get("layer") == "agent"]
    assert len(agent) == 1
    assert agent[0]["verdict"] == "skipped"
    assert body["error"] == ""
    assert "no contract" in str(agent[0].get("failed_reason", ""))


def test_agent_capture_uses_the_stack_mode_of_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modes: list[str] = []

    def fake_up(mode: str = "docker") -> QaStack:
        modes.append(mode)
        raise QaStackError("stop here")

    monkeypatch.setenv("QA_STACK_MODE", "postgres")
    monkeypatch.setattr(qa_worker, "stack_up", fake_up)
    contract = cast(Any, object())
    with pytest.raises(QaStackError):
        qa_worker._capture_agent_screens(contract)
    assert modes == ["postgres"]
