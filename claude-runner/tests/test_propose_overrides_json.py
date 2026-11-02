"""The migration dry run (data-tables/ops/propose_overrides_json.py): the overrides_json the legacy
retrieval_* columns give per mission, a round trip to the same resolved and flattened values,
unknown columns dropped with a warning, credentials removed from retrieval_url, no network, no
write, and a refused legacy value stops the run."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from claude_runner import overrides_config

SCRIPT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "propose_overrides_json.py"
ALLOWED_IMPORTS = {
    "__future__", "argparse", "collections", "json", "pathlib", "sys", "typing", "urllib",
    "claude_runner",
}
FORBIDDEN_CALLS = {
    "write_text", "write_bytes", "unlink", "mkdir", "rename", "touch", "rmdir", "connect",
    "execute", "cursor", "environ", "getenv",
}

ROW: dict[str, Any] = {
    "mission_id": "made-mission-1",
    "state_namespace": "made-namespace-1",
    "retrieval_enabled": True,
    "retrieval_url": "http://retrieval.example:8500",
    "retrieval_corpora_json": '["handbook"]',
    "retrieval_timeout_ms": 30000,
    "retrieval_worker_max_tokens": 900,
    "retrieval_reviewer_max_tokens": 800,
    "retrieval_label": "hybrid-v1",
    "max_tasks": 9,
    "api_key": "made-up-secret",
}
EMPTY_ROW: dict[str, Any] = {
    "mission_id": "made-mission-2",
    "retrieval_enabled": None,
    "retrieval_url": None,
    "retrieval_corpora_json": None,
    "retrieval_timeout_ms": None,
    "retrieval_worker_max_tokens": None,
    "retrieval_reviewer_max_tokens": None,
    "retrieval_label": "",
}
PROPOSAL = {
    "retrieval": {
        "enabled": True,
        "url": "http://retrieval.example:8500",
        "corpora": ["handbook"],
        "timeout_ms": 30000,
        "worker_max_tokens": 900,
        "reviewer_max_tokens": 800,
        "label": "hybrid-v1",
    }
}


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("propose_overrides_json", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_rows(
    tmp_path: Path, rows: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> tuple[int, str, str]:
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    code = _load_script().main([str(path)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _overrides_by_mission(out: str) -> dict[str, str]:
    return {entry["mission_id"]: entry["overrides_json"] for entry in json.loads(out)}


def test_legacy_columns_become_overrides_json_per_mission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = _run_rows(tmp_path, [ROW, EMPTY_ROW], capsys)
    assert code == 0
    overrides = _overrides_by_mission(out)
    assert json.loads(overrides["made-mission-1"]) == PROPOSAL
    assert overrides_config.validate(overrides["made-mission-1"]) == []
    assert json.loads(overrides["made-mission-2"]) == {}


def test_round_trip_matches_the_legacy_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = _run_rows(tmp_path, [ROW, EMPTY_ROW], capsys)
    overrides = _overrides_by_mission(out)
    assert overrides_config.resolve(overrides["made-mission-1"], {}) == overrides_config.resolve(
        None, ROW
    )
    assert overrides_config.flatten(overrides["made-mission-1"], {}) == (
        overrides_config.flatten(None, ROW)
    )
    assert overrides_config.flatten(overrides["made-mission-2"], {}) == (
        overrides_config.flatten(None, EMPTY_ROW)
    )


def test_unknown_columns_are_dropped_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run_rows(tmp_path, [ROW], capsys)
    assert code == 0
    assert "made-up-secret" not in out
    assert "api_key" not in out
    assert (
        "made-mission-1: dropped columns not in overrides_json: api_key, max_tasks, state_namespace"
        in err
    )
    assert "made-up-secret" not in err


def test_url_credentials_are_printed_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {**ROW, "retrieval_url": "http://made-up-user:made-up-pass@retrieval.example:8500"}
    code, out, err = _run_rows(tmp_path, [row], capsys)
    assert code == 0
    overrides = _overrides_by_mission(out)["made-mission-1"]
    assert json.loads(overrides)["retrieval"]["url"] == "http://retrieval.example:8500"
    assert "made-up-user" not in out
    assert "made-up-pass" not in out
    assert "retrieval_url credentials removed before printing" in err


@pytest.mark.parametrize(
    ("legacy", "message"),
    [
        (
            {"retrieval_timeout_ms": 500},
            "overrides_json: retrieval.timeout_ms must be an integer >= 1000, got 500",
        ),
        ({"retrieval_corpora_json": "handbook"}, "Expecting value"),
    ],
)
def test_refused_legacy_value_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], legacy: dict[str, Any], message: str
) -> None:
    refused = {**ROW, "mission_id": "made-mission-3", **legacy}
    code, out, err = _run_rows(tmp_path, [ROW, refused], capsys)
    assert code == 1
    assert out == ""
    assert f"made-mission-3: {message}" in err


def test_input_read_from_stdin(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([ROW])))
    assert _load_script().main(["-"]) == 0
    assert _overrides_by_mission(capsys.readouterr().out)["made-mission-1"] == json.dumps(
        PROPOSAL, sort_keys=True
    )


def test_missing_input_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stop:
        _load_script().main([str(tmp_path / "missing.json")])
    assert stop.value.code == 2
    assert capsys.readouterr().out == ""


def test_row_without_mission_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {key: value for key, value in ROW.items() if key != "mission_id"}
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _load_script().main([str(path)])
    assert stop.value.code == 2
    assert "each with a mission_id" in capsys.readouterr().err


def test_no_network_and_no_other_secret_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dry run opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    _, out, _ = _run_rows(tmp_path, [ROW, EMPTY_ROW], capsys)
    assert "made-up-secret" not in out
    assert set(json.loads(out)[0]) == {"mission_id", "overrides_json"}


def test_script_has_no_network_database_or_write_calls() -> None:
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
