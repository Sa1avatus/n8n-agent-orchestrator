"""The migration dry run (data-tables/ops/propose_roles_json.py): the roles_json the 17 legacy
columns give, the existing roles_json winning per key, identical repeated output, no network,
and only roles_json keys printed."""

from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from ahawr_runner import roles_config

SCRIPT = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "propose_roles_json.py"

ROW: dict[str, Any] = {
    "architect_provider": "anthropic", "architect_model": "opus",
    "architect_max_polls": 30, "architect_poll_seconds": 3, "architect_max_retries": 5,
    "worker_provider": "local", "worker_model": "qwen",
    "worker_max_polls": "40", "worker_poll_seconds": 2.0, "worker_max_retries": 3,
    "reviewer_provider": "anthropic", "reviewer_model": "sonnet",
    "reviewer_max_polls": 20, "reviewer_poll_seconds": 3, "reviewer_max_retries": 5,
    "service_provider": None, "service_model": "sonnet",
    "roles_json": "",
    "max_tasks": 9, "api_key": "made-up-secret",
}

LEGACY_PROPOSAL = {
    "architect": {
        "1": "anthropic/opus",
        "options": {"max_polls": 30, "poll_seconds": 3, "max_retries": 5},
    },
    "worker": {
        "1": "local/qwen",
        "options": {"max_polls": 40, "poll_seconds": 2, "max_retries": 3},
    },
    "reviewer": {
        "1": "anthropic/sonnet",
        "options": {"max_polls": 20, "poll_seconds": 3, "max_retries": 5},
    },
}


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("propose_roles_json", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(tmp_path: Path, row: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> str:
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    assert _load_script().main([str(path)]) == 0
    return capsys.readouterr().out


def test_legacy_columns_become_roles_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run(tmp_path, ROW, capsys)
    assert json.loads(out) == LEGACY_PROPOSAL


def test_existing_roles_json_wins_per_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = {
        "worker": {"1": "anthropic/haiku", "options": {"max_polls": 10}},
        "qa": {"1": "anthropic/sonnet"},
    }
    out = json.loads(_run(tmp_path, {**ROW, "roles_json": json.dumps(existing)}, capsys))
    assert out["worker"] == {
        "1": "anthropic/haiku",
        "options": {"max_polls": 10, "poll_seconds": 2, "max_retries": 3},
    }
    assert out["qa"] == {"1": "anthropic/sonnet"}
    assert out["architect"] == LEGACY_PROPOSAL["architect"]


def test_proposal_keeps_the_runtime_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    existing = {"worker": {"1": "anthropic/haiku", "options": {"max_polls": 10}}}
    row = {**ROW, "roles_json": json.dumps(existing)}
    out = json.loads(_run(tmp_path, row, capsys))
    assert roles_config.flatten(out, row) == roles_config.flatten(existing, row)


def test_repeated_runs_print_identical_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _run(tmp_path, ROW, capsys)
    assert _run(tmp_path, ROW, capsys) == first


def test_only_roles_keys_are_printed_and_no_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the dry run opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    out = _run(tmp_path, ROW, capsys)
    assert set(json.loads(out)) <= set(roles_config.KNOWN_ROLES)
    assert "made-up-secret" not in out
    assert "max_tasks" not in out


def test_roles_json_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "row.json"
    path.write_text(json.dumps({**ROW, "roles_json": "[]"}), encoding="utf-8")
    with pytest.raises(ValueError, match="roles_json must be a JSON object"):
        _load_script().main([str(path)])
