"""Board ops HTTP routes (claude_runner/board_ops_api.py): the dangerous
confirmation flow with the client-held token (prepare hands the real 32-hex
token, confirm consumes it and 400s on missing/expired/reused/mismatched
tokens), the stale-agent 503, the journal with sanitized results, repeat
refusals and the live-state flags with the optimistic version check."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from claude_runner.board_ops_api import add_ops_routes
from claude_runner.board_ops_guard import ConfirmTokens
from claude_runner.board_ops_livestate import InMemoryLiveStateStore
from claude_runner.board_ops_store import InMemoryOpsStore

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

def test_prepare_dangerous_returns_token_and_writes_no_row():
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


def test_prepare_safe_enqueues_directly():
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

def test_confirm_without_token_is_400():
    app = _app_with()
    client = TestClient(app)
    client.post("/api/board/ops/commands/prepare", json={"action": "restart_queue", "who": "alice"})
    r = client.post("/api/board/ops/commands/confirm", json={"action": "restart_queue"})
    assert r.status_code == 400 and r.json()["code"] == "token", r.text


def test_confirm_with_wrong_token_is_400():
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


def test_confirm_with_valid_token_inserts_row():
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


def test_token_replay_is_400():
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


def test_expired_token_is_400():
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

def test_stale_heartbeat_is_503_on_prepare_confirm_and_repeat():
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

def test_journal_shows_who_when_what_status_and_sanitized_result():
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
    assert restart["confirm_token"] == "***"


def test_stale_command_is_reaped_on_read():
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

def test_repeat_dangerous_action_is_422():
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


def test_repeat_repeatable_safe_action_creates_a_new_row():
    app = _app_with()
    store = app.state.board_ops_store
    client = TestClient(app)
    first = client.post(
        "/api/board/ops/commands/prepare", json={"action": "send_test_notification", "who": "carol"}
    ).json()["command"]
    r = client.post(f"/api/board/ops/commands/{first['id']}/repeat")
    assert r.status_code == 200, r.text
    second = r.json()["command"]
    assert second["id"] != first["id"] and second["action"] == "send_test_notification"


def test_repeat_unknown_command_id_is_404():
    app = _app_with()
    client = TestClient(app)
    r = client.post("/api/board/ops/commands/9999/repeat")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# the rate limit
# ---------------------------------------------------------------------------

def test_rate_limit_is_429_after_the_limit_is_reached():
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

def test_livestate_get_and_put_flags():
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


def test_livestate_put_with_stale_version_is_409():
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


def test_livestate_unknown_flag_is_400():
    app = _app_with()
    client = TestClient(app)
    r = client.put(
        "/api/board/ops/livestate/m1/flags", json={"changes": {"not_a_flag": True}}
    )
    assert r.status_code == 400, r.text
