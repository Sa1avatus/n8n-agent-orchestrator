"""The migration dry run (data-tables/ops/propose_engine_json.py): the engine_json the legacy
loop and infrastructure columns give, only the keys that differ from the defaults, a round trip
to the same flattened values, telegram_chat_id never printed, no network, no write, and a
refused legacy value stops the run."""

from __future__ import annotations

import ast
import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ahawr_runner import board_engine

SCRIPT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "propose_engine_json.py"
ALLOWED_IMPORTS = {"__future__", "argparse", "collections", "json", "pathlib", "sys", "typing",
                   "ahawr_runner"}
FORBIDDEN_CALLS = {"write_text", "write_bytes", "unlink", "mkdir", "rename", "touch", "rmdir",
                   "environ", "getenv"}

ROW: dict[str, Any] = {
    "max_attempts_per_task": 3,
    "retry_delays_json": "[10, 20]",
    "runner_url": "http://made-runner:9000/",
    "run_manager_workflow_id": "made-workflow-1",
    "telegram_chat_id": "made-chat-9",
}

PROPOSAL = {
    "max_attempts_per_task": 3,
    "retry_delays": [10, 20],
    "run_manager_workflow_id": "made-workflow-1",
    "runner_url": "http://made-runner:9000",
}


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("propose_engine_json", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_row(tmp_path: Path, row: Any) -> Path:
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def _run(
    tmp_path: Path, row: Any, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    code = _load_script().main([str(_write_row(tmp_path, row))])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_legacy_columns_become_engine_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run(tmp_path, ROW, capsys)
    assert code == 0
    assert json.loads(out) == PROPOSAL
    assert board_engine.validate_engine_json(out) == ()


def test_round_trip_matches_the_legacy_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run(tmp_path, ROW, capsys)
    back = board_engine.flatten(out, {})
    legacy = board_engine.flatten(None, ROW)
    assert {k: v for k, v in back.items() if k != "telegram_chat_id"} == {
        k: v for k, v in legacy.items() if k != "telegram_chat_id"
    }


def test_telegram_chat_id_is_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, err = _run(tmp_path, ROW, capsys)
    assert "made-chat-9" not in out
    assert "telegram_chat_id" not in out
    assert "telegram_chat_id: not proposed" in err
    assert "made-chat-9" not in err


def test_defaults_are_left_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    defaults = {
        "max_attempts_per_task": 5,
        "retry_delays_json": "[15, 30, 60, 120, 300]",
        "runner_url": "http://ahawr-runner:8700/",
        "run_manager_workflow_id": "",
        "telegram_chat_id": None,
    }
    code, out, _ = _run(tmp_path, defaults, capsys)
    assert code == 0
    assert json.loads(out) == {}


def test_refused_legacy_value_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(tmp_path, {**ROW, "max_attempts_per_task": 0}, capsys)
    assert code == 1
    assert out == ""
    assert "max_attempts_per_task must be a number of at least 1" in err


def test_row_file_that_is_not_an_object_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        _load_script().main([str(_write_row(tmp_path, [ROW]))])
    assert stop.value.code == 2
    assert "must hold one object" in capsys.readouterr().err


def test_missing_row_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        _load_script().main([str(tmp_path / "missing.json")])
    assert stop.value.code == 2
    assert capsys.readouterr().out == ""


def test_no_network_and_no_other_secret_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dry run opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    _, out, _ = _run(tmp_path, {**ROW, "api_key": "made-up-secret"}, capsys)
    assert "made-up-secret" not in out
    assert set(json.loads(out)) <= set(board_engine.ENGINE_DEFAULTS)


def test_script_has_no_network_environment_or_write_calls() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
    assert imported <= ALLOWED_IMPORTS
    assert used.isdisjoint(FORBIDDEN_CALLS)
