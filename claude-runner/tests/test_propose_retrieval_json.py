"""The migration dry run (data-tables/ops/propose_retrieval_json.py): the retrieval_json the seven
legacy retrieval_* columns give, a round trip to the same flattened values, credentials removed
from retrieval_url, no network, no write, and a refused legacy value stops the run."""

from __future__ import annotations

import ast
import csv
import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from claude_runner import retrieval_config

SCRIPT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "propose_retrieval_json.py"
ALLOWED_IMPORTS = {
    "__future__", "argparse", "collections", "csv", "json", "pathlib", "sys", "typing",
    "urllib", "claude_runner",
}
WRITE_CALLS = {"write_text", "write_bytes", "unlink", "mkdir", "rename", "touch", "rmdir"}

ROW: dict[str, Any] = {
    "retrieval_enabled": True,
    "retrieval_url": "http://retrieval.example:8500",
    "retrieval_corpora_json": ["handbook"],
    "retrieval_timeout_ms": 30000,
    "retrieval_worker_max_tokens": 900,
    "retrieval_reviewer_max_tokens": 800,
    "retrieval_label": "hybrid-v1",
    "max_tasks": 9,
    "api_key": "made-up-secret",
}

PROPOSAL = {
    "enabled": True,
    "url": "http://retrieval.example:8500",
    "corpora": ["handbook"],
    "timeout_ms": 30000,
    "worker_max_tokens": 900,
    "reviewer_max_tokens": 800,
    "label": "hybrid-v1",
}


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("propose_retrieval_json", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_json(tmp_path: Path, row: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> str:
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert _load_script().main([str(path)]) == 0
    return capsys.readouterr().out


def test_legacy_columns_become_retrieval_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run_json(tmp_path, ROW, capsys)
    assert json.loads(out) == PROPOSAL
    assert retrieval_config.validate(json.loads(out)) == []


def test_round_trip_matches_the_legacy_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proposed = json.loads(_run_json(tmp_path, ROW, capsys))
    assert retrieval_config.flatten(proposed, {}) == retrieval_config.flatten(None, ROW)


def test_empty_legacy_columns_print_an_empty_object(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = {column: None for column in retrieval_config.DEFAULTS}
    out = _run_json(tmp_path, empty, capsys)
    assert json.loads(out) == {}
    assert retrieval_config.flatten(json.loads(out), {}) == retrieval_config.flatten(None, empty)


def test_csv_export_gives_the_same_retrieval_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_row = {
        "retrieval_enabled": "true",
        "retrieval_url": "http://retrieval.example:8500",
        "retrieval_corpora_json": '["handbook"]',
        "retrieval_timeout_ms": "30000",
        "retrieval_worker_max_tokens": "900",
        "retrieval_reviewer_max_tokens": "800",
        "retrieval_label": "hybrid-v1",
        "max_tasks": "9",
        "api_key": "made-up-secret",
    }
    path = tmp_path / "row.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(csv_row))
        writer.writeheader()
        writer.writerow(csv_row)
    assert _load_script().main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out) == PROPOSAL


def test_url_credentials_are_printed_redacted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = {**ROW, "retrieval_url": "http://made-up-user:made-up-pass@retrieval.example:8500"}
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert _load_script().main([str(path)]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["url"] == "http://retrieval.example:8500"
    assert "made-up-user" not in captured.out
    assert "made-up-pass" not in captured.out
    assert "credentials removed" in captured.err


def test_no_network_and_no_other_secret_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dry run opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    out = _run_json(tmp_path, ROW, capsys)
    assert set(json.loads(out)) <= {"enabled", "url", "corpora", "timeout_ms", "worker_max_tokens",
                                    "reviewer_max_tokens", "label"}
    assert "made-up-secret" not in out
    assert "max_tasks" not in out


def test_script_has_no_network_database_or_write_calls() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Attribute):
            called.add(node.attr)
    assert imported <= ALLOWED_IMPORTS
    assert called.isdisjoint(WRITE_CALLS)


def test_refused_legacy_value_stops_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "row.json"
    path.write_text(json.dumps({**ROW, "retrieval_corpora_json": "handbook"}), encoding="utf-8")
    assert _load_script().main([str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "corpora must be an array of non-empty strings" in captured.err
