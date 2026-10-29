"""Tests for the ``python -m claude_runner.qa capture`` subcommand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claude_runner import qa, qa_contract, qa_stack
from claude_runner.qa_stack import QaStack

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


def test_capture_missing_contract_exits_nonzero_without_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[str] = []

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        started.append(fixture)
        return STACK

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    monkeypatch.setattr(qa_contract, "CONTRACTS_ROOT", tmp_path)
    assert qa.main(["capture", "--contract", "ghost"]) == 1
    assert started == []
    output = capsys.readouterr()
    assert "no contract" in output.err


def test_capture_invalid_contract_exits_nonzero_without_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
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
    output = capsys.readouterr()
    assert "missing title" in output.err


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
    output = capsys.readouterr()
    assert f"manifest: {run_dir}" in output.out


def test_capture_stops_stack_when_capture_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
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
    output = capsys.readouterr()
    assert "capture failed" in output.err
