"""Load a QA fixture (qa/fixtures/*.json) into the rows the board stores are seeded from."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_runner.board import InMemoryBoardStore, LiveState, Mission, QueueEntry
from claude_runner.board_pg import _LIVE_STATE_TABLE, _MISSIONS_TABLE, _QUEUE_TABLE

MISSIONS_TABLE = _MISSIONS_TABLE.strip('"')
LIVE_STATE_TABLE = _LIVE_STATE_TABLE.strip('"')
QUEUE_TABLE = _QUEUE_TABLE.strip('"')
REQUIRED_TABLES = (MISSIONS_TABLE, LIVE_STATE_TABLE, QUEUE_TABLE)


@dataclass(frozen=True)
class FixtureRows:
    missions: list[dict[str, Any]]
    live_state: list[dict[str, Any]]
    queue: list[dict[str, Any]]


def load_fixture(source: Path | dict[str, Any]) -> FixtureRows:
    fixture = _read_json(source) if isinstance(source, Path) else source
    if not isinstance(fixture, dict):
        raise ValueError(f"fixture {source} is not a JSON object")
    tables = fixture.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("fixture has no 'tables' object")
    for table in REQUIRED_TABLES:
        if table not in tables:
            raise ValueError(f"fixture is missing table {table!r}")
        rows = tables[table]
        if not isinstance(rows, list):
            raise ValueError(f"table {table!r} is not a list")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"table {table!r} has a row that is not an object")
    return FixtureRows(
        missions=tables[MISSIONS_TABLE],
        live_state=tables[LIVE_STATE_TABLE],
        queue=tables[QUEUE_TABLE],
    )


def board_store_from_rows(rows: FixtureRows) -> InMemoryBoardStore:
    store = InMemoryBoardStore()
    for row in rows.missions:
        mission = Mission(
            mission_id=row["mission_id"],
            state_namespace=row["state_namespace"],
            name=row.get("name") or "",
            objective=row.get("objective") or "",
            working_directory=row.get("working_directory") or None,
            version=row.get("version"),
            enabled=row.get("enabled", True),
            rules=json.loads(row.get("rules_json") or "[]"),
            acceptance_criteria=json.loads(row.get("acceptance_criteria_json") or "[]"),
            retrieval_corpora=json.loads(row.get("retrieval_corpora_json") or "null"),
            created_at=row.get("createdAt"),
            updated_at=row.get("updatedAt"),
        )
        store._missions[mission.mission_id] = mission
    for row in rows.live_state:
        live = LiveState(
            state_key=row["state_key"],
            worker_session_id=row.get("worker_session_id") or None,
            plan=json.loads(row["plan_json"]) if row.get("plan_json") else None,
            task_index=row.get("task_index"),
            task_attempt=row.get("task_attempt"),
            status=row.get("status") or None,
        )
        store._live[live.state_key] = live
    for position, row in enumerate(sorted(rows.queue, key=lambda r: int(r["position"]))):
        entry = QueueEntry(
            position=position,
            mission_id=row["mission_id"],
            enabled=row.get("enabled", True),
            attempts_per_task=row.get("attempts_per_task"),
            retry_failed=row.get("retry_failed", False),
            note=row.get("note"),
        )
        store._queue[entry.mission_id] = entry
        store._queue_order.append(entry.mission_id)
    return store


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read fixture {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"fixture {path} is not valid JSON: {exc}") from exc
