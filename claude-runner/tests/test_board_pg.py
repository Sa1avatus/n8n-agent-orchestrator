"""Tests for PgBoardStore: fake psycopg connection, DSN redaction, empty-field guards."""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timezone

import pytest

from claude_runner.board import ConflictError
from claude_runner.board_pg import (
    _EVENT_LOG_TABLE,
    _LIVE_STATE_TABLE,
    PgBoardStore,
    get_board_store,
)
from claude_runner.board_stats import mission_timeline

try:
    import psycopg
    from psycopg import rows as psycopg_rows
except ImportError:  # driver not installed: stub, every test replaces connect
    psycopg = types.ModuleType("psycopg")
    psycopg.connect = None
    psycopg.OperationalError = type("OperationalError", (Exception,), {})
    psycopg_rows = types.ModuleType("psycopg.rows")
    psycopg_rows.dict_row = None
    psycopg.rows = psycopg_rows


def _install_driver(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", psycopg_rows)


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

    def fake_connect(dsn, row_factory=None, **kw):
        return conn

    _install_driver(monkeypatch)
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    store = PgBoardStore("postgresql://u:p@h/db")
    return store, conn


class TestConnectionOptions:
    def test_server_side_prepared_statements_are_off(self, monkeypatch):
        """A column added to a data table must not break the cached plan of `select *`."""
        seen: dict = {}
        conn = FakeConnection()
        conn.set_response("SELECT max(at)", [{"max": None}])
        conn.set_response("updatedAt", [{"updatedAt": None}])
        conn.set_response("SET search_path", [])

        def fake_connect(dsn, **kwargs):
            seen.update(kwargs)
            return conn

        _install_driver(monkeypatch)
        monkeypatch.setattr(psycopg, "connect", fake_connect)
        PgBoardStore("postgresql://u:p@h/db")
        assert "prepare_threshold" in seen and seen["prepare_threshold"] is None


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

        def fake_connect(_dsn, row_factory=None, **kw):
            raise psycopg.OperationalError("connection failed: " + _dsn)

        _install_driver(monkeypatch)
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


RULES = [{"name": "a", "pattern": "*.py", "position": 1}]
SEED = {"languages": [{"code": "de", "name": "Deutsch"}], "strings": {"en": {"k": "v"}}}


class TestProjectRulesAndI18n:
    def test_put_project_rules_stale_version_conflicts_without_writing(self, monkeypatch):
        from datetime import datetime, timezone

        store, conn = _make_store(monkeypatch)
        stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        conn.set_response("SELECT name, pattern", [
            {"name": "a", "pattern": "*.py", "position": 1, "updated_at": stamp},
        ])
        with pytest.raises(ConflictError):
            store.put_project_rules(RULES, "stale")
        assert not any("DELETE FROM project_rules" in sql for sql, _ in conn.executed)

    def test_put_i18n_string_empty_text_deletes_the_row(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("SELECT text FROM i18n_strings", [{"text": "old"}])
        store.put_i18n_string("en", "k", "")
        assert any("DELETE FROM i18n_strings" in sql for sql, _ in conn.executed)
        assert not any("INSERT INTO i18n_strings" in sql for sql, _ in conn.executed)

    def test_seed_i18n_does_nothing_when_rows_exist(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("SELECT EXISTS", [{"has_rows": True}])
        store.seed_i18n(SEED)
        assert not any("INSERT INTO i18n_" in sql for sql, _ in conn.executed)


STALE_VERSION = "1999-01-01T00:00:00+00:00"


class TestPlanWriteContract:
    """Plan writes, stale-version refusal, events and timeline on the Pg store."""

    STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_plan_json_update_writes_the_live_row_and_audits(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": self.STAMP}])
        plan = {"tasks": [{"id": "T001", "objective": "one"}]}
        store.upsert_live_state("T001", "2026-01-01T00:00:00+00:00", plan_json=json.dumps(plan))
        updates = [(sql, params) for sql, params in conn.executed if sql.startswith("UPDATE ")]
        assert len(updates) == 1
        sql, params = updates[0]
        assert _LIVE_STATE_TABLE in sql
        assert "plan_json = %s" in sql
        assert json.dumps(plan) in params
        audits = [sql for sql, _ in conn.executed if sql.startswith("INSERT INTO changelog")]
        assert len(audits) == 1

    def test_audit_serializes_timestamps_of_a_config_row(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        store._audit("hermes_config update", before={"updatedAt": self.STAMP}, after=None)
        sql_params = [p for sql, p in conn.executed if sql.startswith("INSERT INTO changelog")]
        assert len(sql_params) == 1
        assert json.loads(sql_params[0][4]) == {"updatedAt": str(self.STAMP)}

    def test_stale_version_conflicts_without_update_or_audit(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": self.STAMP}])
        with pytest.raises(ConflictError):
            store.upsert_live_state("T001", STALE_VERSION, plan_json="{}")
        assert not any(sql.startswith("UPDATE ") for sql, _ in conn.executed)
        assert not any(sql.startswith("INSERT INTO changelog") for sql, _ in conn.executed)

    def test_plan_kwarg_is_written_like_plan_json(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": self.STAMP}])
        store.upsert_live_state("T001", None, plan={"tasks": []})
        updates = [(sql, params) for sql, params in conn.executed if sql.startswith("UPDATE ")]
        assert len(updates) == 1
        sql, params = updates[0]
        assert _LIVE_STATE_TABLE in sql
        assert "plan_json = %s" in sql
        assert json.dumps({"tasks": []}) in params

    def test_reorder_write_path_updates_and_audits(self, monkeypatch):
        """The reorder route's write path: board_order.violations on the fake
        connection, then upsert_live_state(plan=...) like the route does."""
        from claude_runner.board_order import violations

        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": self.STAMP}])
        order = ["T004", "T003", "T002"]
        assert violations(order, {}) == []
        plan = {"tasks": [
            {"id": "T001", "objective": "one"},
            {"id": "T004", "objective": "four"},
            {"id": "T003", "objective": "three"},
            {"id": "T002", "objective": "two"},
        ]}
        store.upsert_live_state("T001", self.STAMP.isoformat(), plan=plan)
        updates = [(sql, params) for sql, params in conn.executed if sql.startswith("UPDATE ")]
        assert len(updates) == 1
        sql, params = updates[0]
        assert _LIVE_STATE_TABLE in sql
        assert "plan_json = %s" in sql
        assert json.dumps(plan) in params
        audits = [sql for sql, _ in conn.executed if sql.startswith("INSERT INTO changelog")]
        assert len(audits) == 1

    def test_events_read_the_quoted_event_table_in_id_order(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("FROM " + _EVENT_LOG_TABLE, [{"task_id": "T001", "event_type": "review_parsed"}])
        rows = store.events("T001")
        assert rows == [{"task_id": "T001", "event_type": "review_parsed"}]
        sql, params = conn.executed[-1]
        assert _EVENT_LOG_TABLE in sql
        assert sql.rstrip().endswith("ORDER BY id")
        assert params == ("T001",)

    def test_timeline_reports_accepted_review(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("FROM " + _EVENT_LOG_TABLE, [
            {"task_id": "T001", "event_type": "review_parsed", "review_status": "pass",
             "created_at": self.STAMP},
        ])
        timeline = mission_timeline(None, store, "T001")
        assert timeline == {
            "accepted": [{"task": "T001", "ts": "2026-01-01T00:00:00+00:00"}],
            "obstacles": [],
        }

    def test_timeline_reports_a_task_rejected_three_times(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        rejected = {"task_id": "T002", "event_type": "review_parsed",
                    "review_status": "needs_changes", "created_at": self.STAMP}
        conn.set_response("FROM " + _EVENT_LOG_TABLE, [rejected, rejected, rejected])
        timeline = mission_timeline(None, store, "T001")
        assert timeline["accepted"] == []
        assert [o["kind"] for o in timeline["obstacles"]] == ["rejected_3x"]
        assert timeline["obstacles"][0]["task"] == "T002"

    def test_timeline_reports_an_interrupted_run(self, monkeypatch):
        stamp = self.STAMP
        events = [
            {"task_id": "T001", "event_type": "worker_finished",
             "worker_run_id": "run_a", "worker_status": "ok",
             "created_at": stamp},
        ]
        store, conn = _make_store(monkeypatch)
        conn.set_response("FROM " + _EVENT_LOG_TABLE, events)

        runs = {
            "run_a": {
                "status": "interrupted",
                "error_code": "run_not_found",
                "error_message": "runner process gone",
                "started_at": stamp,
                "finished_at": stamp,
            },
        }

        class Manager:
            def __init__(self):
                self.store = {"run_a": runs["run_a"]}

            def _get(self):
                return self.store

        # mission_timeline calls manager.store.get_run(run_id);
        # wrap the dict with a get_run shim.
        class _RunStore:
            def __init__(self, d):
                self._d = d
            def get_run(self, run_id):
                return self._d.get(run_id)

        mgr = type("M", (), {
            "store": _RunStore(runs),
        })()
        timeline = mission_timeline(mgr, store, "T001")
        assert timeline["accepted"] == []
        assert len(timeline["obstacles"]) == 1
        assert timeline["obstacles"][0]["kind"] == "interrupted"
        assert timeline["obstacles"][0]["task"] == "T001"
        assert timeline["obstacles"][0]["run_id"] == "run_a"
        assert timeline["obstacles"][0]["ts"] == stamp
        assert timeline["obstacles"][0]["detail"] == "runner process gone"

    def test_timeline_reports_a_timed_out_run(self, monkeypatch):
        stamp = self.STAMP
        events = [
            {"task_id": "T001", "event_type": "worker_finished",
             "worker_run_id": "run_b", "worker_status": "error",
             "created_at": stamp},
        ]
        store, conn = _make_store(monkeypatch)
        conn.set_response("FROM " + _EVENT_LOG_TABLE, events)

        runs = {
            "run_b": {
                "status": "failed",
                "error_code": "timeout",
                "error_message": "timed out",
                "started_at": stamp,
                "finished_at": stamp,
            },
        }

        class _RunStore:
            def __init__(self, d):
                self._d = d
            def get_run(self, run_id):
                return self._d.get(run_id)

        mgr = type("M", (), {
            "store": _RunStore(runs),
        })()
        timeline = mission_timeline(mgr, store, "T001")
        assert timeline["accepted"] == []
        assert len(timeline["obstacles"]) == 1
        assert timeline["obstacles"][0]["kind"] == "timeout"
        assert timeline["obstacles"][0]["task"] == "T001"
        assert timeline["obstacles"][0]["run_id"] == "run_b"
        assert timeline["obstacles"][0]["ts"] == stamp
        assert timeline["obstacles"][0]["detail"] == "timed out"

    def test_timeline_without_events_is_empty(self, monkeypatch):
        store, _ = _make_store(monkeypatch)
        assert mission_timeline(None, store, "T001") == {"accepted": [], "obstacles": []}


class TestHermesRolesJson:
    def test_settings_read_without_roles_json_column(self, monkeypatch):
        from claude_runner import board_api

        store, conn = _make_store(monkeypatch)
        conn.set_response("SELECT * FROM", [{"worker_model": "qwen", "max_attempts_per_task": 5}])
        assert "roles_json" not in store.hermes_config()
        assert board_api._config_defaults(store) == {
            "worker_model": "qwen",
            "max_attempts_per_task": 5,
        }

    def test_update_hermes_config_writes_roles_json(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": datetime(2026, 1, 1, tzinfo=UTC)}])
        store.update_hermes_config(None, roles_json="[]")
        updates = [(sql, params) for sql, params in conn.executed if sql.startswith("UPDATE ")]
        assert len(updates) == 1
        sql, params = updates[0]
        assert "roles_json = %s" in sql
        assert "[]" in params


class TestHermesFeaturesJson:
    def test_settings_read_without_features_json_column(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("SELECT * FROM", [{"worker_model": "qwen"}])
        assert "features_json" not in store.hermes_config()

    def test_update_hermes_config_writes_features_json(self, monkeypatch):
        store, conn = _make_store(monkeypatch)
        conn.set_response("updatedAt", [{"updatedAt": datetime(2026, 1, 1, tzinfo=UTC)}])
        store.update_hermes_config(None, features_json="{}")
        updates = [(sql, params) for sql, params in conn.executed if sql.startswith("UPDATE ")]
        assert len(updates) == 1
        sql, params = updates[0]
        assert "features_json = %s" in sql
        assert "{}" in params
