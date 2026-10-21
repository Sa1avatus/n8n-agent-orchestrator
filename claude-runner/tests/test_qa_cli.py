from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner import qa, qa_runner, qa_stack
from claude_runner.qa_runner import parse_scenario
from claude_runner.qa_stack import QaStack, QaStackError

STACK_ID = "0a1b2c3d"
PASSWORD = "s3cr3t-Pw_for_test"
DSN = f"postgresql://qa:{PASSWORD}@qa-{STACK_ID}:5432/qa_{STACK_ID}"


def _stack() -> QaStack:
    return QaStack(
        run_id=STACK_ID,
        host=f"qa-{STACK_ID}",
        database=f"qa_{STACK_ID}",
        dsn=DSN,
        dashboard_url="http://127.0.0.1:49152",
    )


def test_stack_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["stack", "--help"])
    assert exit_info.value.code == 0
    assert "up" in capsys.readouterr().out


def test_stack_up_defaults_to_default_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_up(fixture: str = "default") -> QaStack:
        calls.append(fixture)
        return _stack()

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    assert qa.main(["stack", "up"]) == 0
    assert calls == ["default"]


def test_stack_up_passes_empty_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_up(fixture: str = "default") -> QaStack:
        calls.append(fixture)
        return _stack()

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    assert qa.main(["stack", "up", "--fixture", "empty"]) == 0
    assert calls == ["empty"]


def test_stack_up_rejects_unknown_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_stack, "stack_up", _unexpected_call)
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["stack", "up", "--fixture", "nightly"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_stack_up_prints_dashboard_url_and_stack_id_never_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture="default": _stack())
    assert qa.main(["stack", "up"]) == 0
    output = capsys.readouterr()
    assert output.out == f"dashboard: http://127.0.0.1:49152\nstack id: {STACK_ID}\n"
    assert PASSWORD not in output.out + output.err
    assert "postgresql://" not in output.out + output.err


def test_stack_up_error_is_reported_without_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_up(fixture: str = "default") -> QaStack:
        raise QaStackError("docker build failed with exit code 1: no space left")

    monkeypatch.setattr(qa_stack, "stack_up", failing_up)
    assert qa.main(["stack", "up"]) == 1
    output = capsys.readouterr()
    assert "no space left" in output.err
    assert PASSWORD not in output.out + output.err
    assert "postgresql://" not in output.out + output.err


def test_stack_down_removes_objects_named_from_stack_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    removed: list[QaStack] = []
    monkeypatch.setattr(qa_stack, "stack_down", removed.append)
    assert qa.main(["stack", "down", STACK_ID]) == 0
    assert len(removed) == 1
    stack = removed[0]
    assert stack.run_id == STACK_ID
    assert stack.containers == [f"qa-{STACK_ID}", f"qa-{STACK_ID}-dashboard"]
    assert stack.volumes == [f"qa-{STACK_ID}-data"]
    assert stack.networks == [f"qa-{STACK_ID}-net"]
    assert stack.images == [f"qa-dashboard:{STACK_ID}"]
    assert "removed" in capsys.readouterr().out


@pytest.mark.parametrize("bad_id", ["../x", "qa-0a1b2c3d", "0A1B2C3D", "0a1b2c3"])
def test_stack_down_rejects_malformed_stack_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    monkeypatch.setattr(qa_stack, "stack_down", _unexpected_call)
    assert qa.main(["stack", "down", bad_id]) == 1
    assert "not 8 lowercase hex digits" in capsys.readouterr().err


def test_stack_down_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_down(stack: QaStack) -> None:
        raise QaStackError(f"teardown of QA stack {stack.run_id} incomplete: rm failed")

    monkeypatch.setattr(qa_stack, "stack_down", failing_down)
    assert qa.main(["stack", "down", STACK_ID]) == 1
    assert "incomplete" in capsys.readouterr().err


def test_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        qa.main([])
    assert exit_info.value.code == 2
    assert "required" in capsys.readouterr().err


def _scenario_text(name: str, tag: str) -> str:
    return (
        f"name: {name}\ntags: [{tag}]\npreconditions: {{fixture: default}}\nsteps: [{{open: /}}]\n"
    )


def _verdict(name: str, value: str) -> dict[str, Any]:
    return {"scenario": name, "verdict": value}


def test_run_exits_zero_when_every_verdict_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(
        qa_runner, "run_browser_scenario", lambda scenario, max_minutes: _verdict("board", "pass")
    )

    assert qa.main(["run"]) == 0
    assert json.loads(capsys.readouterr().out) == [_verdict("board", "pass")]


def test_run_exits_one_on_blocked_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    blocked = _verdict("board", "blocked")
    monkeypatch.setattr(qa_runner, "run_browser_scenario", lambda scenario, max_minutes: blocked)

    assert qa.main(["run"]) == 1
    assert json.loads(capsys.readouterr().out) == [blocked]


def test_run_filters_by_tag_and_scenario_name(monkeypatch: pytest.MonkeyPatch) -> None:
    scenarios = [
        parse_scenario(_scenario_text("board", "ui")),
        parse_scenario(_scenario_text("nightly-sweep", "nightly")),
    ]
    ran: list[str] = []

    def record(scenario: Any, max_minutes: float) -> dict[str, Any]:
        ran.append(scenario.name)
        return _verdict(scenario.name, "pass")

    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(qa_runner, "run_browser_scenario", record)

    assert qa.main(["run", "--tag", "nightly"]) == 0
    assert qa.main(["run", "--scenario", "board"]) == 0
    assert ran == ["nightly-sweep", "board"]


def test_run_rejects_unknown_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_runner, "run_browser_scenario", _unexpected_call)
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["run", "--tag", "billing"])
    assert exit_info.value.code == 2


def test_run_without_scenario_files_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_files() -> list[Any]:
        raise ValueError("no scenario files (*.yaml) in qa/scenarios/")

    monkeypatch.setattr(qa_runner, "load_scenarios", no_files)
    assert qa.main(["run"]) == 1
    assert "no scenario files" in capsys.readouterr().err


def test_nightly_runs_every_scenario_and_writes_verdict_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenarios = [
        parse_scenario(_scenario_text("board", "ui")),
        parse_scenario(_scenario_text("nightly-sweep", "nightly")),
    ]
    ran: list[str] = []

    def record(scenario: Any, max_minutes: float) -> dict[str, Any]:
        ran.append(scenario.name)
        return _verdict(scenario.name, "pass")

    verdict_path = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(qa_runner, "run_browser_scenario", record)

    assert qa.main(["nightly"]) == 0
    assert ran == ["board", "nightly-sweep"]
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written == [_verdict("board", "pass"), _verdict("nightly-sweep", "pass")]


def test_nightly_writes_verdict_file_and_exits_one_on_blocked_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict_path = tmp_path / "nightly-verdict.json"
    blocked = _verdict("board", "blocked")
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(
        qa_runner, "load_scenarios", lambda: [parse_scenario(_scenario_text("board", "ui"))]
    )
    monkeypatch.setattr(qa_runner, "run_browser_scenario", lambda scenario, max_minutes: blocked)

    assert qa.main(["nightly"]) == 1
    assert json.loads(verdict_path.read_text(encoding="utf-8")) == [blocked]


def _unexpected_call(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the QA stack must not be touched by a rejected command")
