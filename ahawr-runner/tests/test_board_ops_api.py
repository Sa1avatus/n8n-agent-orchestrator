"""Board ops HTTP routes (ahawr_runner/board_ops_api.py): the dangerous
confirmation flow with the client-held token (prepare hands the real 32-hex
token, confirm consumes it and 400s on missing/expired/reused/mismatched
tokens), the stale-agent 503, the journal with sanitized results, repeat
refusals and the live-state flags with the optimistic version check."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta
from typing import Any

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ahawr_runner.board import ConflictError
from ahawr_runner.board_ops_api import (
    _on_conflict,
    _on_missing,
    _on_runtime,
    _on_token,
    _on_value,
    add_ops_routes,
)
from ahawr_runner.board_ops_guard import ConfirmTokens, TokenError
from ahawr_runner.board_ops_livestate import InMemoryLiveStateStore
from ahawr_runner.board_ops_store import InMemoryOpsStore
from ahawr_runner.board_ops_store_pg import PgOpsStore, UnavailableOpsStore

NOW = "2026-10-17T12:00:00+00:00"


def _fresh_store() -> InMemoryOpsStore:
    """A fresh in-memory store with a fresh heartbeat (agent alive)."""
    store = InMemoryOpsStore()
    store.put_heartbeat(None, None)
    return store


def _app_with(store: InMemoryOpsStore | None = None) -> FastAPI:
    """A runner-less app wired to the given (or fresh) in-memory stores;
    add_ops_routes keeps the pre-set store and installs the token registry
    and the exception handlers."""
    if store is None:
        store = _fresh_store()
    app = FastAPI()
    app.state.board_ops_store = store
    app.state.board_ops_livestate = InMemoryLiveStateStore()
    add_ops_routes(app)
    return app


def _stale_app() -> tuple[FastAPI, InMemoryOpsStore]:
    """The app with a heartbeat 40 s old (the stale-agent refusal path)."""
    store = _fresh_store()
    stamp = datetime.fromisoformat(store._heartbeats[-1].heartbeat_at)
    store._heartbeats[-1].heartbeat_at = (stamp - timedelta(seconds=40)).isoformat()
    return _app_with(store), store


# ---------------------------------------------------------------------------
# prepare: the dangerous confirmation flow
# ---------------------------------------------------------------------------

def test_prepare_dangerous_returns_token_and_writes_no_row() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.post("/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"})
    assert r.status_code == 200, r.text
    body = r.json()
    token = body["confirm_token"]
    assert isinstance(token, str) and len(token) == 32 and all(c in "0123456789abcdef" for c in token)
    assert body["expires_at"] and body["consequences"] and body["command_preview"]
    # no row is written until the token is confirmed
    assert app.state.board_ops_store.list_commands(action="restart_queue") == []


def test_prepare_safe_enqueues_directly() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.post("/api/board/ops/commands/prepare", json={"action": "resume", "who": "alice"})
    assert r.status_code == 200, r.text
    command = r.json()["command"]
    assert command["action"] == "resume" and command["status"] == "new"
    # the safe path never issues a token
    assert "confirm_token" not in r.json()


# ---------------------------------------------------------------------------
# confirm: the client-held credential
# ---------------------------------------------------------------------------

def test_confirm_without_token_is_400() -> None:
    app = _app_with()
    client = TestClient(app)
    client.post("/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"})
    r = client.post("/api/board/ops/commands/confirm", json={"action": "restart_queue"})
    assert r.status_code == 400 and r.json()["code"] == "token", r.text


def test_confirm_with_wrong_token_is_400() -> None:
    app = _app_with()
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    # a token that was never issued
    r = client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": "f" * 32, "who": "alice"},
    )
    assert r.status_code == 400 and r.json()["code"] == "token", r.text
    # a valid token bound to a different user
    r = client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "bob"},
    )
    assert r.status_code == 400 and r.json()["code"] == "token", r.text


def test_confirm_with_valid_token_inserts_row() -> None:
    app = _app_with()
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    r = client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    assert r.status_code == 200, r.text
    row = r.json()["command"]
    assert row["action"] == "restart_queue" and row["status"] == "new"
    # the stored token is the audit trail; the wire value is masked
    assert row["confirm_token"] == "***"


def test_token_replay_is_400() -> None:
    app = _app_with()
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    r = client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    assert r.status_code == 400 and r.json()["code"] == "token", r.text


def test_expired_token_is_400() -> None:
    app = _app_with()
    clock = {"now": NOW}
    app.state.board_ops_confirm_tokens = ConfirmTokens(now_fn=lambda: clock["now"])
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    # 120 s later the 60 s token is expired
    clock["now"] = (datetime.fromisoformat(NOW) + timedelta(seconds=120)).isoformat()
    r = client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    assert r.status_code == 400 and r.json()["code"] == "token", r.text
    assert "expired" in r.json()["error"]["message"]


# ---------------------------------------------------------------------------
# the stale agent: 503 on prepare, confirm and repeat
# ---------------------------------------------------------------------------

def test_stale_heartbeat_is_503_on_prepare_confirm_and_repeat() -> None:
    app, store = _stale_app()
    client = TestClient(app)
    r = client.post("/api/board/ops/commands/prepare", json={"action": "resume", "who": "alice"})
    assert r.status_code == 503 and r.json()["code"] == "agent_stale", r.text
    assert "stale" in r.json()["error"]["message"]
    r = client.post("/api/board/ops/commands/confirm", json={"action": "resume", "token": "x" * 32})
    assert r.status_code == 503 and r.json()["code"] == "agent_stale", r.text
    store.insert_command("resume", None, "alice")
    row = store.list_commands(limit=1)[0]
    r = client.post(f"/api/board/ops/commands/{row.id}/repeat")
    assert r.status_code == 503 and r.json()["code"] == "agent_stale", r.text


# ---------------------------------------------------------------------------
# the journal: who, when, what, status and a sanitized result
# ---------------------------------------------------------------------------

def test_journal_shows_who_when_what_status_and_sanitized_result() -> None:
    app = _app_with()
    store = app.state.board_ops_store
    client = TestClient(app)
    client.post("/api/board/ops/commands/prepare", json={"action": "resume", "who": "alice"})
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    # finish the resume command with a result that carries credential-like
    # strings; the journal must answer them scrubbed.
    claimed = store.claim_next()
    store.finish_command(
        claimed.id,
        "done",
        {"note": "sk-" + "a" * 20, "hex": "b" * 32},
    )
    r = client.get("/api/board/ops/commands")
    assert r.status_code == 200, r.text
    entries = r.json()["commands"]
    resume = next(e for e in entries if e["action"] == "resume")
    restart = next(e for e in entries if e["action"] == "restart_queue")
    for entry in (resume, restart):
        for key in ("id", "action", "status", "requested_by", "requested_at", "result_json"):
            assert key in entry, key
    assert resume["requested_by"] == "alice"
    assert resume["status"] == "done"
    assert resume["result_json"] == {"note": "***", "hex": "***"}
    assert restart["status"] == "new"
    # the one-time confirmation token is never in the read route
    assert "confirm_token" not in restart


def test_journal_rows_never_contain_the_confirm_token() -> None:
    app = _app_with()
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    r = client.get("/api/board/ops/commands")
    assert r.status_code == 200, r.text
    for entry in r.json()["commands"]:
        assert "confirm_token" not in entry, entry


def test_stale_command_is_reaped_on_read() -> None:
    app = _app_with()
    store = app.state.board_ops_store
    client = TestClient(app)
    store.insert_command("resume", None, "alice")
    claimed = store.claim_next()
    stamp = datetime.fromisoformat(claimed.started_at)
    store._commands[claimed.id].started_at = (stamp - timedelta(seconds=900)).isoformat()
    r = client.get("/api/board/ops/commands")
    row = next(x for x in r.json()["commands"] if x["id"] == claimed.id)
    assert row["status"] == "failed" and row["result_json"] == {"reason": "timeout"}, row


# ---------------------------------------------------------------------------
# repeat: only repeatable safe actions
# ---------------------------------------------------------------------------

def test_repeat_dangerous_action_is_422() -> None:
    app = _app_with()
    store = app.state.board_ops_store
    client = TestClient(app)
    prep = client.post(
        "/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"}
    ).json()
    client.post(
        "/api/board/ops/commands/confirm",
        json={"action": "restart_queue", "token": prep["confirm_token"], "who": "alice"},
    )
    danger_id = store.list_commands(action="restart_queue")[0].id
    r = client.post(f"/api/board/ops/commands/{danger_id}/repeat")
    assert r.status_code == 422, r.text
    assert "dangerous" in r.json()["error"]["message"]


def test_repeat_repeatable_safe_action_creates_a_new_row() -> None:
    app = _app_with()
    client = TestClient(app)
    first = client.post(
        "/api/board/ops/commands/prepare", json={"action": "send_test_notification", "who": "carol"}
    ).json()["command"]
    r = client.post(f"/api/board/ops/commands/{first['id']}/repeat")
    assert r.status_code == 200, r.text
    second = r.json()["command"]
    assert second["id"] != first["id"] and second["action"] == "send_test_notification"


def test_repeat_unknown_command_id_is_404() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.post("/api/board/ops/commands/9999/repeat")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# the rate limit
# ---------------------------------------------------------------------------

def test_rate_limit_is_429_after_the_limit_is_reached() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.put("/api/board/ops/settings", json={"ops_rate_limit_per_min": 3, "who": "admin"})
    assert r.status_code == 200, r.text
    for _ in range(3):
        r = client.post(
            "/api/board/ops/commands/prepare", json={"action": "send_test_notification", "who": "x"}
        )
        assert r.status_code == 200, r.text
    r = client.post(
        "/api/board/ops/commands/prepare", json={"action": "send_test_notification", "who": "x"}
    )
    assert r.status_code == 429 and r.json()["code"] == "rate_limit", r.text


# ---------------------------------------------------------------------------
# live-state flags
# ---------------------------------------------------------------------------

def test_livestate_get_and_put_flags() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.get("/api/board/ops/livestate/m1/flags")
    assert r.status_code == 200 and r.json() == {"flags": {}, "version": None}, r.text
    r = client.put(
        "/api/board/ops/livestate/m1/flags", json={"changes": {"paused": True}, "who": "carol"}
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["flags"]["paused"] is True and created["version"]
    # a change with the correct version lands
    r = client.put(
        "/api/board/ops/livestate/m1/flags",
        json={"changes": {"paused": False}, "version": created["version"], "who": "carol"},
    )
    assert r.status_code == 200 and r.json()["flags"]["paused"] is False, r.text


def test_livestate_put_with_stale_version_is_409() -> None:
    app = _app_with()
    client = TestClient(app)
    created = client.put(
        "/api/board/ops/livestate/m1/flags", json={"changes": {"paused": True}, "who": "carol"}
    ).json()
    r = client.put(
        "/api/board/ops/livestate/m1/flags",
        json={"changes": {"paused": False}, "version": "stale-version", "who": "carol"},
    )
    assert r.status_code == 409, r.text
    assert created["version"] in r.json()["error"]["message"]


def test_livestate_unknown_flag_is_400() -> None:
    app = _app_with()
    client = TestClient(app)
    r = client.put(
        "/api/board/ops/livestate/m1/flags", json={"changes": {"not_a_flag": True}}
    )
    assert r.status_code == 400, r.text


_STAND_IN_REQUEST: Any = {"type": "http", "method": "GET", "path": "/probe"}


def test_ops_conflict_handler_maps_conflict_error_to_409_conflict() -> None:
    """Pinned: board_ops_api._on_conflict answers 409 with code conflict and the
    exception message as error.message (the handler ignores the request)."""
    response = _on_conflict(_STAND_IN_REQUEST, ConflictError("command already running"))
    assert response.status_code == 409
    assert json.loads(bytes(response.body)) == {
        "status": "failed",
        "error": {"code": "conflict", "message": "command already running"},
        "code": "conflict",
        "status_code": 409,
    }


def test_ops_missing_handler_maps_key_error_to_404_not_found() -> None:
    """Pinned: board_ops_api._on_missing answers 404 with code not_found and the
    key repr as the message (str(KeyError) keeps the quotes)."""
    response = _on_missing(_STAND_IN_REQUEST, KeyError("cmd-7"))
    assert response.status_code == 404
    assert json.loads(bytes(response.body)) == {
        "status": "failed",
        "error": {"code": "not_found", "message": "'cmd-7'"},
        "code": "not_found",
        "status_code": 404,
    }


def test_ops_token_handler_maps_token_error_to_its_own_code_and_status() -> None:
    """Pinned: board_ops_api._on_token answers with TokenError's class-level
    code (token) and status_code (400), not a fixed literal."""
    response = _on_token(_STAND_IN_REQUEST, TokenError("token expired"))
    assert response.status_code == 400
    assert json.loads(bytes(response.body)) == {
        "status": "failed",
        "error": {"code": "token", "message": "token expired"},
        "code": "token",
        "status_code": 400,
    }


def test_ops_value_handler_maps_value_error_to_400_bad_request() -> None:
    """Pinned: board_ops_api._on_value answers 400 with code bad_request and the
    exception message as error.message."""
    response = _on_value(_STAND_IN_REQUEST, ValueError("unknown flag name"))
    assert response.status_code == 400
    assert json.loads(bytes(response.body)) == {
        "status": "failed",
        "error": {"code": "bad_request", "message": "unknown flag name"},
        "code": "bad_request",
        "status_code": 400,
    }


def test_ops_runtime_handler_maps_runtime_error_to_503_unavailable() -> None:
    """Pinned: board_ops_api._on_runtime answers 503 with code unavailable and
    the exception message as error.message."""
    response = _on_runtime(_STAND_IN_REQUEST, RuntimeError("ops agent heartbeat stale"))
    assert response.status_code == 503
    assert json.loads(bytes(response.body)) == {
        "status": "failed",
        "error": {"code": "unavailable", "message": "ops agent heartbeat stale"},
        "code": "unavailable",
        "status_code": 503,
    }


# ---------------------------------------------------------------------------
# S3 characterization: the hardcoded read caps of board_ops_store.py and
# board_ops_store_pg.py (audit ids a010-a025 in reports/ahawr-refactor-4).
# These tests pin the current literals before a later step moves them to
# settings; they do not change the stores.
# ---------------------------------------------------------------------------

class _FakePgCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakePgConnection:
    """Records every (sql, params) PgOpsStore sends; reads answer with no rows."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> _FakePgCursor:
        self.executed.append((sql, params))
        return _FakePgCursor([])


def _fake_pg_store() -> tuple[PgOpsStore, _FakePgConnection]:
    """PgOpsStore built against a recording fake connection instead of a DSN.

    shortcut: the fake never returns rows, so only the recorded LIMIT param is
    checked; upgrade to respond() rows when fetch-side behaviour needs pinning."""
    conn = _FakePgConnection()
    original_connect = psycopg.connect
    psycopg.connect = lambda dsn, **kwargs: conn  # type: ignore[assignment]
    try:
        store = PgOpsStore("postgresql://fake-dsn")
    finally:
        psycopg.connect = original_connect
    return store, conn


def _limit_param(conn: _FakePgConnection, table: str) -> Any:
    """The last %s parameter of the recorded query on `table` (the LIMIT value)."""
    for sql, params in conn.executed:
        if f"FROM {table}" in sql:
            return params[-1]
    raise AssertionError(f"no query on {table!r} was recorded")


def test_ops_store_list_commands_default_page_limit_is_200_rows() -> None:
    """Pinned (a010, a014): list_commands without a limit returns at most 200
    rows, newest first; the rows beyond 200 are dropped."""
    store = InMemoryOpsStore()
    for _ in range(201):
        store.insert_command("watch_logs", None, "alice")
    rows = store.list_commands()
    assert len(rows) == 200
    assert rows[0].id == 201
    assert rows[-1].id == 2


def test_ops_store_changelog_default_rows_is_200_newest_first() -> None:
    """Pinned (a011, a015): changelog() with no limit keeps the 200 newest rows."""
    store = InMemoryOpsStore()
    for index in range(201):
        store.put_setting(f"ops_key_{index}", {"value": index}, None, "op")
    entries = store.changelog()
    assert len(entries) == 200
    assert entries[0].id == 201
    assert entries[-1].id == 2  # the oldest audit row falls off the default tail


def test_ops_store_queue_log_default_tail_is_1000_rows() -> None:
    """Pinned (a012, a016): get_queue_log() without a limit tails 1000 published
    rows, newest first.  shortcut: published rows arrive from n8n, not through
    a store method; the test seeds the internal tail for the cap check."""
    store = InMemoryOpsStore()
    store._queue_log = [{"at": NOW, "line": f"line {index}"} for index in range(1001)]
    tail = store.get_queue_log()
    assert len(tail) == 1000
    assert tail[0]["line"] == "line 1000"
    assert tail[-1]["line"] == "line 1"


def test_ops_store_snapshot_default_tail_is_100_rows() -> None:
    """Pinned (a013, a017): get_snapshot() without a limit tails 100 rows,
    newest first (a012's seeding note applies the same way)."""
    store = InMemoryOpsStore()
    store._snapshots = [{"at": NOW, "data_json": {"seq": index}} for index in range(101)]
    rows = store.get_snapshot("host_snapshot")
    assert len(rows) == 100
    assert rows[0]["data_json"] == {"seq": 100}
    assert rows[-1]["data_json"] == {"seq": 1}


def test_unavailable_ops_store_read_defaults_keep_the_shared_caps() -> None:
    """Pinned (a018, a019, a020, a021): the stub refuses reads before any query,
    so the effective caps are only the signature defaults."""
    defaults = {
        name: inspect.signature(getattr(UnavailableOpsStore, name)).parameters["limit"].default
        for name in ("list_commands", "changelog", "get_queue_log", "get_snapshot")
    }
    assert defaults == {
        "list_commands": 200,
        "changelog": 200,
        "get_queue_log": 1000,
        "get_snapshot": 100,
    }


def test_pg_ops_list_commands_default_limit_is_200_rows() -> None:
    """Pinned (a022): PgOpsStore sends the default 200 as the LIMIT parameter."""
    store, conn = _fake_pg_store()
    store.list_commands()
    assert _limit_param(conn, "commands") == 200


def test_pg_ops_changelog_default_limit_is_200_rows() -> None:
    """Pinned (a023): the changelog query LIMITs the default 200 rows."""
    store, conn = _fake_pg_store()
    store.changelog()
    assert _limit_param(conn, "changelog") == 200


def test_pg_ops_queue_log_default_limit_is_1000_rows() -> None:
    """Pinned (a024): the queue-log tail query LIMITs the default 1000 rows."""
    store, conn = _fake_pg_store()
    store.get_queue_log()
    assert _limit_param(conn, "queue_log") == 1000


def test_pg_ops_snapshot_default_limit_is_100_rows() -> None:
    """Pinned (a025): the host_snapshot query LIMITs the default 100 rows."""
    store, conn = _fake_pg_store()
    store.get_snapshot("host_snapshot")
    assert _limit_param(conn, "host_snapshot") == 100
