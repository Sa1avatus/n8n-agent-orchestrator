from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_runner.board import InMemoryBoardStore, LiveState, Mission, QueueEntry
from claude_runner.board_pg import (
    _EVENT_LOG_TABLE,
    _HERMES_PROFILE,
    _HERMES_TABLE,
    _I18N_LANGUAGES_TABLE,
    _I18N_STRINGS_TABLE,
    _LIVE_STATE_TABLE,
    _MISSIONS_TABLE,
    _QUEUE_TABLE,
)

FIXTURES = Path(__file__).resolve().parent.parent / "qa" / "fixtures"
MISSIONS_TABLE = _MISSIONS_TABLE.strip('"')
LIVE_STATE_TABLE = _LIVE_STATE_TABLE.strip('"')
EVENT_LOG_TABLE = _EVENT_LOG_TABLE.strip('"')
HERMES_TABLE = _HERMES_TABLE.strip('"')
EXPECTED_GROUPS = {
    "running": ["m01", "m02"],
    "queued": ["m03", "m04", "m10"],
    "completed": ["m05", "m06", "m07"],
    "not_completed": ["m08", "m09"],
    "disabled": ["m11", "m12"],
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _rows(fixture: dict[str, Any], table: str) -> list[dict[str, Any]]:
    return fixture["tables"][table]


def _json_column(row: dict[str, Any], column: str) -> Any:
    return None if row[column] is None else json.loads(row[column])


def _mission(row: dict[str, Any]) -> Mission:
    return Mission(
        mission_id=row["mission_id"],
        state_namespace=row["state_namespace"],
        name=row["name"],
        objective=row["objective"],
        working_directory=row["working_directory"],
        version=row["version"],
        enabled=row["enabled"],
        rules=_json_column(row, "rules_json"),
        acceptance_criteria=_json_column(row, "acceptance_criteria_json"),
        retrieval_corpora=_json_column(row, "retrieval_corpora_json"),
        created_at=row["createdAt"],
        updated_at=row["updatedAt"],
    )


def _live_fields(row: dict[str, Any]) -> dict[str, Any]:
    plan = row.get("plan_json")
    fields = {k: v for k, v in row.items() if k != "plan_json"}
    return {**fields, "plan": None if plan is None else json.loads(plan)}


def test_default_missions_cover_every_group():
    fixture = _load("default")
    rows = _rows(fixture, MISSIONS_TABLE)
    assert len(rows) == 12
    assert len({r["mission_id"] for r in rows}) == 12
    store = InMemoryBoardStore()
    for row in rows:
        store._missions[row["mission_id"]] = _mission(row)
    for live in _rows(fixture, LIVE_STATE_TABLE):
        fields = _live_fields(live)
        store.upsert_live_state(fields.pop("state_key"), None, **fields)
    groups = {k: sorted(v) for k, v in store.mission_groups().items()}
    assert groups == EXPECTED_GROUPS


def test_default_live_state_rows_parse():
    fixture = _load("default")
    for row in _rows(fixture, LIVE_STATE_TABLE):
        LiveState(**_live_fields(row))


def test_default_queue_has_seven_entries():
    fixture = _load("default")
    rows = _rows(fixture, _QUEUE_TABLE)
    mission_ids = {r["mission_id"] for r in _rows(fixture, MISSIONS_TABLE)}
    assert len(rows) == 7
    assert [r["position"] for r in rows] == list(range(7))
    assert len({r["mission_id"] for r in rows}) == 7
    for row in rows:
        QueueEntry(**row)
        assert row["mission_id"] in mission_ids


def test_default_tasks_have_attempts_and_events():
    fixture = _load("default")
    events = _rows(fixture, EVENT_LOG_TABLE)
    state_keys = {r["state_namespace"] for r in _rows(fixture, MISSIONS_TABLE)}
    assert events
    for event in events:
        assert event["state_key"] in state_keys
        assert event["task_id"]
        assert event["task_attempt"] >= 1
        assert event["event_type"]
    attempts = {(e["task_id"], e["task_attempt"]) for e in events}
    assert ("T001", 2) in attempts
    assert any(e["event_type"] == "review_parsed" for e in events)


def test_default_profiles_used_row_is_not_first():
    rows = _rows(_load("default"), HERMES_TABLE)
    assert len(rows) == 2
    first = min(rows, key=lambda r: r["id"])
    used = [r for r in sorted(rows, key=lambda r: r["id"]) if r["profile_id"] == _HERMES_PROFILE]
    assert len(used) == 1
    assert used[0]["id"] != first["id"]


def test_default_table_names_are_mixed_case():
    fixture = _load("default")
    physical = [MISSIONS_TABLE, LIVE_STATE_TABLE, EVENT_LOG_TABLE, HERMES_TABLE]
    for name in physical:
        assert name in fixture["tables"]
        assert name != name.lower()
        assert name != name.upper()


def test_default_has_both_languages():
    fixture = _load("default")
    codes = {r["code"] for r in _rows(fixture, _I18N_LANGUAGES_TABLE)}
    assert codes == {"en", "ru"}
    strings = _rows(fixture, _I18N_STRINGS_TABLE)
    by_key: dict[str, set[str]] = {}
    for row in strings:
        by_key.setdefault(row["key"], set()).add(row["lang"])
    assert by_key
    assert all(langs == {"en", "ru"} for langs in by_key.values())


def test_empty_fixture_has_queue_table_without_rows():
    fixture = _load("empty")
    assert _QUEUE_TABLE in fixture["tables"]
    assert fixture["tables"][_QUEUE_TABLE] == []
    assert all(rows == [] for rows in fixture["tables"].values())
