from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

LAUNCHER_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "queue_launcher.py"


def _load_launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location("queue_launcher", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


class FakeStarter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return "0"


@pytest.mark.parametrize(
    ("age_s", "handled", "executions", "starts", "action"),
    [
        pytest.param(10, False, 0, 0, "start", id="valid-request"),
        pytest.param(10, False, 0, launcher.MAX_PER_HOUR - 1, "start", id="third-start-allowed"),
        pytest.param(10, True, 0, 0, "drop", id="already-handled"),
        pytest.param(launcher.MAX_AGE_S + 1, False, 0, 0, "drop", id="too-old"),
        pytest.param(10, False, 0, launcher.MAX_PER_HOUR, "wait", id="four-per-hour-reached"),
        pytest.param(10, False, 1, 0, "wait", id="execution-running"),
        pytest.param(10, False, -1, 0, "wait", id="unknown-state"),
    ],
)
def test_decide(age_s: float, handled: bool, executions: int, starts: int, action: str) -> None:
    assert launcher.decide(age_s, handled, executions, starts).action == action


def test_no_request_file_starts_nothing(tmp_path, monkeypatch, capsys) -> None:
    starter = FakeStarter()
    monkeypatch.setattr(launcher, "start_queue", starter)
    monkeypatch.setattr(launcher, "RESULTS", str(tmp_path / "results.jsonl"))
    monkeypatch.setattr(sys, "argv", ["queue_launcher.py", "--request-dir", str(tmp_path)])
    assert launcher.main() == 0
    assert starter.calls == 0
    assert capsys.readouterr().out == ""


def test_dry_run_starts_nothing_and_keeps_request(tmp_path, monkeypatch, capsys) -> None:
    request = {
        "request_id": "11111111-2222-4333-8444-555555555555",
        "requested_at": datetime.now(UTC).isoformat(),
        "reason": "queue_not_alive",
    }
    request_file = tmp_path / launcher.REQUEST_NAME
    request_file.write_text(json.dumps(request), encoding="utf-8")
    results = tmp_path / "results.jsonl"
    starter = FakeStarter()
    monkeypatch.setattr(launcher, "start_queue", starter)
    monkeypatch.setattr(launcher, "executions_running", lambda: 0)
    monkeypatch.setattr(launcher, "RESULTS", str(results))
    monkeypatch.setattr(
        sys, "argv", ["queue_launcher.py", "--request-dir", str(tmp_path), "--dry-run"]
    )
    assert launcher.main() == 0
    assert starter.calls == 0
    assert request_file.exists()
    assert not results.exists()
    assert capsys.readouterr().out.startswith("start: ")
