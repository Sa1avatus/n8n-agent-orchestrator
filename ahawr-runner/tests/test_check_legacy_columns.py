import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "check_legacy_columns.py"
)
FAKE_CHAT_ID = "-100FAKE-4242"
FAKE_API_KEY = "sk-FAKE-9Z"


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    if not SCRIPT_PATH.is_file():
        pytest.skip(f"script not found: {SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location("check_legacy_columns", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(
    roles: dict[str, Any], engine: dict[str, Any] | None = None, **legacy: Any
) -> dict[str, Any]:
    return {
        "profile_id": "claude-code",
        "roles_json": json.dumps(roles),
        "engine_json": json.dumps(engine or {}),
        **legacy,
    }


def _write_row(tmp_path: Path, row: dict[str, Any]) -> Path:
    path = tmp_path / "row.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return path


def test_equal_value_without_readers_is_safe(checker: ModuleType) -> None:
    entry = checker.Legacy("worker_max_polls", "roles_json", "worker.options.max_polls")
    row = _row({"worker": {"options": {"max_polls": 3}}}, worker_max_polls=3)
    line, safe = checker.report_line(row, entry)
    assert safe
    assert "equal=yes | readers=none | verdict=SAFE" in line


def test_differing_value_is_not_safe(checker: ModuleType) -> None:
    entry = checker.Legacy("worker_max_polls", "roles_json", "worker.options.max_polls")
    row = _row({"worker": {"options": {"max_polls": 3}}}, worker_max_polls=2)
    line, safe = checker.report_line(row, entry)
    assert not safe
    assert (
        "worker_max_polls | legacy=2 | roles_json.worker.options.max_polls=3 | equal=no "
        "| readers=none | verdict=NOT SAFE"
    ) in line


def test_missing_json_key_is_not_equal(checker: ModuleType) -> None:
    entry = checker.Legacy("worker_max_polls", "roles_json", "worker.options.max_polls")
    row = _row({"worker": {"options": {}}}, worker_max_polls=3)
    line, safe = checker.report_line(row, entry)
    assert not safe
    assert "roles_json.worker.options.max_polls=NULL | equal=no" in line


def test_already_deleted_column_is_not_equal(checker: ModuleType) -> None:
    entry = checker.Legacy("worker_max_polls", "roles_json", "worker.options.max_polls")
    row = _row({"worker": {"options": {"max_polls": 3}}})
    line, safe = checker.report_line(row, entry)
    assert not safe
    assert (
        "worker_max_polls | legacy=NULL | roles_json.worker.options.max_polls=3 | equal=no" in line
    )


def test_remaining_reader_is_not_safe_even_when_equal(checker: ModuleType) -> None:
    entry = checker.Legacy("worker_max_polls", "roles_json", "worker.options.max_polls", "", 2)
    row = _row({"worker": {"options": {"max_polls": 3}}}, worker_max_polls=3)
    line, safe = checker.report_line(row, entry)
    assert not safe
    assert "equal=yes | readers=workflow=2 | verdict=NOT SAFE" in line


def test_telegram_chat_id_is_masked_in_output(
    checker: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    row = _row({}, engine={"telegram_chat_id": FAKE_CHAT_ID}, telegram_chat_id=FAKE_CHAT_ID)
    assert checker.main(["--row-file", str(_write_row(tmp_path, row))]) == 0
    out = capsys.readouterr().out
    assert FAKE_CHAT_ID not in out
    assert (
        "telegram_chat_id | legacy=*** | engine_json.telegram_chat_id=*** | equal=yes "
        "| readers=workflow=13 | verdict=NOT SAFE"
    ) in out


def test_secret_like_value_is_masked_in_output(
    checker: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_entry = checker.Legacy("worker_api_key", "roles_json", "worker.options.api_key")
    monkeypatch.setattr(checker, "LEGACY", (api_key_entry,))
    row = _row({"worker": {"options": {"api_key": FAKE_API_KEY}}}, worker_api_key=FAKE_API_KEY)
    assert checker.main(["--row-file", str(_write_row(tmp_path, row))]) == 0
    out = capsys.readouterr().out
    assert FAKE_API_KEY not in out
    assert "legacy=*** | roles_json.worker.options.api_key=*** | equal=yes | readers=none" in out
    assert "SAFE: 1, NOT SAFE: 0" in out
