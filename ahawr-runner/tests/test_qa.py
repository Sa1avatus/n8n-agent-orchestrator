"""Characterization tests for the ``python -m ahawr_runner.qa`` CLI.

Every T201 print in src/ahawr_runner/qa.py was pinned here before the T201
cleanup. All eleven operator-facing and interface prints stay print: each one
carries a `# stdout-is-interface` comment naming the reader (operator or script).
Only the CLI error in main goes through the module logger (log.error reaches
stderr through logging's lastResort handler).

Tests below pin the printed or logged text with capsys/caplog and the return
values and side effects of each function that contained a print.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import qa, qa_contract, qa_regression, qa_runner, qa_smoke, qa_stack, qa_worker
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.qa_stack import QaStack

STACK_ID = "0a1b2c3d"
STACK = QaStack(
    run_id=STACK_ID,
    host=f"qa-{STACK_ID}",
    database=f"qa_{STACK_ID}",
    dsn="",
    dashboard_url="http://127.0.0.1:49152",
)

CONTRACT_TEXT = (
    "# Title\n\n"
    "## Screen\n"
    "- Open: route /x, viewport 1280x800\n"
    "- Layout: layout\n"
    "- Reference image: x.png\n"
    "- C1: item\n"
)


def _write_contract(tmp_path: Path, mission: str, text: str = CONTRACT_TEXT) -> None:
    mission_dir = tmp_path / mission
    mission_dir.mkdir()
    (mission_dir / "contract.md").write_text(text, encoding="utf-8")
    (mission_dir / "x.png").write_bytes(b"png")


def _handler(argv: list[str]) -> argparse.Namespace:
    """The parsed namespace a CLI handler receives, without running main."""
    return qa.build_parser().parse_args(argv)


def test_capture_missing_contract_exits_nonzero_without_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[str] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    monkeypatch.setattr(qa_contract, "CONTRACTS_ROOT", tmp_path)
    assert qa.main(["capture", "--contract", "ghost"]) == 1
    assert started == []
    assert "no contract" in caplog.text


def test_capture_invalid_contract_exits_nonzero_without_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[str] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    _write_contract(
        tmp_path,
        "bad",
        "## No title\n- Open: route /x, viewport 1280x800\n- Layout: layout\n"
        "- Reference image: x.png\n",
    )
    monkeypatch.setattr(qa_contract, "CONTRACTS_ROOT", tmp_path)
    assert qa.main(["capture", "--contract", "bad"]) == 1
    assert started == []
    assert "missing title" in caplog.text


def test_capture_starts_stack_runs_capture_and_stops_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = tmp_path / "qa-abc12345"
    run_dir.mkdir(parents=True)
    png_path = run_dir / "Screen.png"
    manifest = [{"screen": "Screen", "png": str(png_path), "dom": str(png_path), "error": ""}]

    started: list[str] = []
    modes: list[str] = []
    stopped: list[QaStack] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        modes.append(mode)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    monkeypatch.setattr(qa_stack, "stack_down", stopped.append)
    monkeypatch.setattr(qa, "_capture_screens", lambda contract, base_url: manifest)
    _write_contract(tmp_path, "mission")
    monkeypatch.setattr(qa_contract, "CONTRACTS_ROOT", tmp_path)

    monkeypatch.setenv("QA_STACK_MODE", "postgres")

    assert qa.main(["capture", "--contract", "mission"]) == 0
    assert started == ["default"]
    assert modes == ["postgres"]
    assert stopped == [STACK]
    assert capsys.readouterr().out == f"manifest: {run_dir}\n"


def test_capture_stops_stack_when_capture_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[str] = []
    stopped: list[QaStack] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    monkeypatch.setattr(qa_stack, "stack_down", stopped.append)

    def failing_capture(contract: Any, base_url: str) -> Any:
        raise RuntimeError("capture failed")

    monkeypatch.setattr(qa, "_capture_screens", failing_capture)
    _write_contract(tmp_path, "mission")
    monkeypatch.setattr(qa_contract, "CONTRACTS_ROOT", tmp_path)

    assert qa.main(["capture", "--contract", "mission"]) == 1
    assert started == ["default"]
    assert stopped == [STACK]
    assert "capture failed" in caplog.text

def test_stack_up_prints_dashboard_url_and_stack_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    started: list[str] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    assert qa.main(["stack", "up", "--fixture", "default"]) == 0
    assert started == ["default"]
    out = capsys.readouterr().out
    assert out == "dashboard: http://127.0.0.1:49152\nstack id: 0a1b2c3d\n"


def test_stack_down_prints_removed_and_stops_the_stack(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stopped: list[QaStack] = []
    monkeypatch.setattr(qa_stack, "stack_down", stopped.append)
    monkeypatch.setattr(qa, "stack_from_id", lambda stack_id: STACK)
    assert qa.main(["stack", "down", STACK_ID]) == 0
    assert stopped == [STACK]
    assert capsys.readouterr().out == f"stack {STACK_ID} removed\n"


def test_run_prints_verdict_json_and_returns_zero_when_all_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: [])
    monkeypatch.setattr(
        qa_runner,
        "select_scenarios",
        lambda scenarios, tag, name: list(scenarios),
    )
    monkeypatch.setattr(qa, "_run_all", lambda scenarios, stack_mode: [{"verdict": "pass"}])
    assert qa._run(_handler(["run"])) == 0
    assert capsys.readouterr().out == json.dumps([{"verdict": "pass"}], indent=2) + "\n"


def test_api_prints_verdict_json_and_returns_one_when_a_verdict_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        qa, "_api_verdicts", lambda stack_mode: [{"verdict": "pass"}, {"verdict": "fail"}]
    )
    assert qa._api(_handler(["api"])) == 1
    assert capsys.readouterr().out == json.dumps(
        [{"verdict": "pass"}, {"verdict": "fail"}], indent=2
    ) + "\n"


def test_nightly_writes_verdict_file_and_prints_its_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict_path = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: [])
    monkeypatch.setattr(qa, "_run_all", lambda scenarios, stack_mode: [{"verdict": "pass"}])
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])
    monkeypatch.setattr(qa_worker, "smoke_enabled", lambda options: False)
    monkeypatch.setattr(qa, "_store_for_options", lambda: InMemoryBoardStore())
    monkeypatch.setattr(qa, "_qa_options", lambda store: {})
    assert qa._nightly(_handler(["nightly"])) == 0
    payload = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert payload == {"scenarios": [{"verdict": "pass"}], "layers": []}
    assert capsys.readouterr().out == f"nightly verdict: {verdict_path}\n"


def test_smoke_prints_crawl_verdict_and_returns_zero_when_pass(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    verdict = {"verdict": "pass", "findings": []}
    monkeypatch.setattr(
        qa_smoke,
        "load_seeds",
        lambda: qa_smoke.Seeds(
            start_paths=(),
            mission_paths=qa_smoke.MissionPaths("src", "missions", "id", "missions/{id}.md"),
            products={},
            destructive=(),
        ),
    )
    monkeypatch.setattr(qa, "_smoke_crawl", lambda *args, **kwargs: verdict)
    assert qa._smoke(_handler(["smoke", "--url", "http://127.0.0.1:8000"])) == 0
    assert capsys.readouterr().out == json.dumps(verdict, indent=2) + "\n"


def test_regression_prints_verdict_json_and_exits_one_when_no_profile(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])
    monkeypatch.setattr(qa_regression, "find_profile", lambda profiles, workdir: None)
    assert qa._regression(_handler(["regression", "--workdir", "/tmp/project"])) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["verdict"] == "skipped"
    assert printed["reason"] == "no QA profile"


def test_baseline_prints_failure_counts_and_the_written_baseline_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = qa_regression.Profile(
        name="demo",
        working_directory=str(tmp_path),
        regression=(),
        baseline="baseline.json",
    )
    written: list[tuple[Path, dict[str, list[str]]]] = []
    monkeypatch.setattr(qa, "_named_profile", lambda name: profile)
    monkeypatch.setattr(qa_worker, "container_path", lambda path: str(tmp_path))
    monkeypatch.setattr(
        qa, "_run_commands", lambda profile, working_dir: {"pytest": ["a"], "ruff": []}
    )
    monkeypatch.setattr(
        qa_regression,
        "write_baseline",
        lambda path, found: written.append((path, found)),
    )
    assert qa._baseline(_handler(["baseline", "demo"])) == 0
    assert written == [(tmp_path / "baseline.json", {"pytest": ["a"], "ruff": []})]
    assert capsys.readouterr().out == (
        "pytest: 1 failures\nruff: 0 failures\nbaseline: "
        f"{tmp_path / 'baseline.json'}\n"
    )
