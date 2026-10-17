"""Tests for PgBoardStore: fake psycopg connection, DSN redaction, empty-field guards."""

from __future__ import annotations

import psycopg
import pytest

from claude_runner.board_pg import PgBoardStore, get_board_store


class FakeCursor:
    """Mimics a psycopg cursor with fetchone/fetchall returning row dicts."""

    def __init__(self, rows):
        self._rows = rows
        self._pos = 0

    def fetchone(self):
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self):
        out = self._rows[self._pos:]
        self._pos = len(self._rows)
        return out


class FakeConnection:
    """Records every execute() call; returns canned results per query signature."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self._responses: dict[str, list] = {}

    def set_response(self, marker: str, rows: list) -> None:
        self._responses[marker] = rows

    def execute(self, sql: str, params=None):
        self.executed.append((sql, tuple(params or ())))
        for marker, rows in self._responses.items():
            if marker in sql:
                return FakeCursor(rows)
        return FakeCursor([])

    def transaction(self):
        class _Txn:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *exc):
                return False
        return _Txn()


def _make_store(monkeypatch) -> tuple[PgBoardStore, FakeConnection]:
    """Construct a PgBoardStore with a fake psycopg connection."""
    conn = FakeConnection()
    # Default responses used during __init__:
    conn.set_response("SELECT max(at)", [{"max": None}])
    conn.set_response("updatedAt", [{"updatedAt": None}])
    conn.set_response("SET search_path", [])

    def fake_connect(dsn, row_factory=None):
        return conn

    monkeypatch.setattr(psycopg, "connect", fake_connect)
    store = PgBoardStore("postgresql://u:p@h/db")
    return store, conn


class TestQueueVersion:
    def test_does_not_select_queue_updated_at(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        # The only SQL executed for _queue_version is on the changelog table.
        changelog_calls = [
            sql for sql, _ in conn.executed if "max(at)" in sql
        ]
        assert len(changelog_calls) == 1
        assert "changelog" in changelog_calls[0]
        # No query touches the queue table at all.
        queue_calls = [
            sql for sql, _ in conn.executed
            if "data_table_user" in sql and "max" in sql
        ]
        assert queue_calls == []


class TestEmptyFieldGuards:
    def test_update_mission_empty_fields_raises_valueerror(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        with pytest.raises(ValueError, match="no updatable fields"):
            store.update_mission("T001", None)

    def test_upsert_live_state_update_empty_fields_raises_valueerror(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        # Make get_live_state return a row so we hit the UPDATE branch.
        live_row = {
            "state_key": "T001",
            "worker_session_id": "ws",
            "plan_json": "{}",
            "task_index": 0,
            "task_attempt": 0,
            "status": "idle",
            "review_feedback": None,
            "last_review_score": None,
            "last_review_reason": None,
            "updated_at": "2026-01-01T00:00:00+00:00",
            "reviewer_session_id": None,
            "reviewer_run_id": None,
            "reviewer_status": None,
            "worker_run_id": None,
            "worker_status": None,
            "architect_run_id": None,
            "architect_status": None,
            "architect_session_id": None,
        }
        conn.set_response("SELECT state_key", [live_row])
        conn.set_response(
            "WHERE state_key = %s FOR UPDATE",
            [{"updatedAt": live_row["updated_at"]}],
        )
        with pytest.raises(ValueError, match="no updatable fields"):
            store.upsert_live_state("T001", None)


class TestDsnRedaction:
    def test_get_board_store_failing_connect_does_not_leak_dsn(self, monkeypatch):
        dsn = "postgresql://u:p@h/db"

        def fake_connect(_dsn, row_factory=None):
            raise psycopg.OperationalError("connection failed: " + _dsn)

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        store = get_board_store(dsn=dsn)
        status, reason = store.status()
        assert status == "unavailable"
        assert "u:p" not in reason
        assert "u:p@h" not in reason
        assert "postgresql://" not in reason


def test_empty_plan_column_is_no_plan() -> None:
    """A mission that is planned right now stores plan_json = '' : the board must show it, not fail with a 400."""
    from claude_runner.board_pg import _plan_dict, _read_json

    assert _read_json("") is None and _read_json("   ") is None and _read_json(None) is None
    assert _plan_dict("") is None
    assert _plan_dict('{"tasks": []}') == {"tasks": []}
    assert _plan_dict("[1, 2]") is None
    assert _plan_dict("not json") is None
