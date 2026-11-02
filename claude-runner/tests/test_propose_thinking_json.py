"""The migration dry run (data-tables/ops/propose_thinking_json.py): the thinking_json the legacy
worker_think_* columns give, valid for the thinking core and flattening to the same constants,
identical repeated output, no network, and no secret printed."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from claude_runner import thinking_config

SCRIPT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "propose_thinking_json.py"

ROW: dict[str, Any] = {
    "worker_think_policy": "off_first",
    "worker_think_policy_from_task": 1,
    "worker_think_off_after_seconds": 1800,
    "worker_think_off_after_steps": 40,
    "api_key": "made-up-secret",
}


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("propose_thinking_json", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, row: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> str:
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert _load_script().main([str(path)]) == 0
    return capsys.readouterr().out.strip()


def test_legacy_columns_become_valid_thinking_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run(tmp_path, ROW, capsys)
    assert json.loads(out) == {"from_task": 1, "off_after_seconds": 1800, "off_after_steps": 40}
    assert thinking_config.validate(out) == []
    legacy = {name: ROW[name] for name in thinking_config.DEFAULTS}
    assert thinking_config.flatten(out, legacy) == thinking_config.flatten(None, legacy)


def test_empty_columns_give_the_empty_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = {name: None for name in thinking_config.DEFAULTS}
    assert _run(tmp_path, empty, capsys) == "{}"


def test_code_default_values_are_not_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    defaults = {
        "worker_think_policy": "",
        "worker_think_policy_from_task": 0,
        "worker_think_off_after_seconds": -1,
        "worker_think_off_after_steps": -1,
    }
    assert _run(tmp_path, defaults, capsys) == "{}"


def test_zero_off_after_is_kept(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = _run(tmp_path, {**ROW, "worker_think_off_after_seconds": 0}, capsys)
    assert json.loads(out)["off_after_seconds"] == 0


def test_repeated_runs_print_identical_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _run(tmp_path, ROW, capsys)
    assert _run(tmp_path, ROW, capsys) == first


def test_no_network_and_no_secret_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dry run opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    out = _run(tmp_path, ROW, capsys)
    assert set(json.loads(out)) <= set(thinking_config.JSON_KEYS)
    assert "made-up-secret" not in out


def test_invalid_legacy_value_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "row.json"
    path.write_text(json.dumps({**ROW, "worker_think_off_after_steps": "40"}), encoding="utf-8")
    with pytest.raises(ValueError, match="off_after_steps must be an integer"):
        _load_script().main([str(path)])


def test_row_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "row.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        _load_script().main([str(path)])
