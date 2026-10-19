"""Board views contract over HTTP: the summary, project rules, i18n and the
changelog.  Every test runs against InMemoryBoardStore and PgBoardStore (a
scripted fake psycopg connection, no network, no real database).  Fixtures
come from tests/fixtures/board_part2 with made-up values."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from claude_runner import board_api, board_stats
from claude_runner.board import ChangelogEntry, InMemoryBoardStore
from claude_runner.board_pg import PgBoardStore

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


FIXTURES = Path(__file__).parent / "fixtures" / "board_part2"
BACKENDS = pytest.mark.parametrize("backend", ["memory", "pg"])
STAMP = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
STAMP_ISO = STAMP.isoformat()
HEADERS = {"X-AHAWR-Dashboard": "1"}


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


CHANGELOG = _fixture("changelog.json")["changelog"]  # newest first


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class ScriptedConnection:
    """Answers each statement with the first handler whose marker is in the SQL.

    A handler's rows may be a callable taking the statement params."""

    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []
        self._handlers: list[tuple[str, object]] = []

    def on(self, marker: str, rows) -> None:
        self._handlers.append((marker, rows))

    def execute(self, sql: str, params=None):
        p = tuple(params or ())
        self.executed.append((sql, p))
        for marker, rows in self._handlers:
            if marker in sql:
                return FakeCursor(rows(p) if callable(rows) else rows)
        return FakeCursor([])

    def sql(self, marker: str) -> list[str]:
        return [s for s, _ in self.executed if marker in s]

    def params(self, marker: str) -> list[tuple]:
        return [p for s, p in self.executed if marker in s]

    def transaction(self):
        class _Txn:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Txn()


def _install_driver(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)
    monkeypatch.setitem(sys.modules, "psycopg.rows", psycopg_rows)


def _pg_store(monkeypatch) -> tuple[PgBoardStore, ScriptedConnection]:
    conn = ScriptedConnection()
    conn.on("SET search_path", [])
    conn.on("SELECT max(at)", [{"max": None}])
    conn.on('SELECT "updatedAt"', [])
    _install_driver(monkeypatch)
    monkeypatch.setattr(psycopg, "connect", lambda dsn, row_factory=None, **kw: conn)
    return PgBoardStore("postgresql://u:p@h/db"), conn


def _build(backend: str, monkeypatch):
    """(store, conn) for the backend; conn is None for InMemory."""
    if backend == "memory":
        return InMemoryBoardStore(), None
    return _pg_store(monkeypatch)


@asynccontextmanager
async def _client(settings, store, *, seeded: bool = True):
    from claude_runner.api import create_app

    app = create_app(settings)
    app.state.board_store = store
    app.state.i18n_seeded = seeded
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as c:
            yield c


def _changelog_rows_pg() -> list[dict]:
    rows = []
    for e in CHANGELOG:
        rows.append({
            **e,
            "before_json": None if e["before_json"] is None else json.dumps(e["before_json"]),
            "after_json": None if e["after_json"] is None else json.dumps(e["after_json"]),
        })
    return rows


def _seed_changelog(backend: str, store, conn) -> None:
    if backend == "memory":
        store._changelog = [ChangelogEntry(**e) for e in sorted(CHANGELOG, key=lambda e: e["id"])]
        store._changelog_seq = 7
        return
    rows = _changelog_rows_pg()
    conn.on("FROM changelog", lambda p: rows[: p[0]])


class TestChangelog:
    @BACKENDS
    async def test_get_matches_fixture_newest_first(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_changelog(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/changelog")
        assert r.status_code == 200
        assert r.json()["changelog"] == CHANGELOG

    @BACKENDS
    async def test_limit_is_honored(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_changelog(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/changelog", params={"limit": 1})
        assert r.status_code == 200
        assert r.json()["changelog"] == [CHANGELOG[0]]
        assert CHANGELOG[0]["id"] == 7

    @BACKENDS
    @pytest.mark.parametrize("limit", [0, 1001])
    async def test_limit_out_of_bounds_is_422(self, settings, backend, monkeypatch, limit):
        store, conn = _build(backend, monkeypatch)
        _seed_changelog(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/changelog", params={"limit": limit})
        assert r.status_code == 422

    async def test_pg_changelog_sends_limit_param(self, settings, monkeypatch):
        store, conn = _build("pg", monkeypatch)
        _seed_changelog("pg", store, conn)
        async with _client(settings, store) as c:
            await c.get("/api/board/changelog", params={"limit": 5})
        assert conn.params("FROM changelog ORDER BY") == [(5,)]


RULES = [
    {"name": "Run", "pattern": "/d/example/mission/running", "position": 1},
    {"name": "Alpha", "pattern": "/d/example/*", "position": 2},
]


def _seed_rules(backend: str, store, conn) -> None:
    if backend == "memory":
        store.put_project_rules(RULES, None)
        return
    rows = [{**r, "updated_at": STAMP} for r in RULES]
    conn.on("SELECT name, pattern", rows)


class TestProjectRules:
    @BACKENDS
    async def test_get_returns_rules_and_version(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_rules(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/project-rules")
        assert r.status_code == 200
        body = r.json()
        assert [(x["name"], x["pattern"], x["position"]) for x in body["rules"]] == [
            ("Run", "/d/example/mission/running", 1),
            ("Alpha", "/d/example/*", 2),
        ]
        if backend == "pg":
            assert body["version"] == STAMP_ISO
        else:
            assert body["version"] == store.get_project_rules()[1] != ""

    @BACKENDS
    async def test_put_without_dashboard_header_is_403(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_rules(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.put("/api/board/project-rules", json={"rules": RULES, "expected_version": None})
        assert r.status_code == 403
        if conn is not None:
            assert not conn.sql("DELETE FROM project_rules")
        else:
            assert len(store.get_project_rules()[0]) == 2

    @BACKENDS
    @pytest.mark.parametrize("body", [
        {"rules": "nope"},
        {"rules": [1]},
    ])
    async def test_put_malformed_body_is_400(self, settings, backend, monkeypatch, body):
        store, conn = _build(backend, monkeypatch)
        _seed_rules(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.put("/api/board/project-rules", json=body, headers=HEADERS)
        assert r.status_code == 400

    @BACKENDS
    async def test_put_stale_version_is_409_and_keeps_rules(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_rules(backend, store, conn)
        stale = "stale-version" if backend == "memory" else "stale"
        async with _client(settings, store) as c:
            r = await c.put(
                "/api/board/project-rules",
                json={"rules": [RULES[0]], "expected_version": stale},
                headers=HEADERS,
            )
        assert r.status_code == 409
        if backend == "pg":
            assert not conn.sql("DELETE FROM project_rules")
        else:
            assert len(store.get_project_rules()[0]) == 2

    async def test_memory_round_trip_sorts_by_position(self, settings, monkeypatch):
        store, _ = _build("memory", monkeypatch)
        _seed_rules("memory", store, None)
        async with _client(settings, store) as c:
            version = (await c.get("/api/board/project-rules")).json()["version"]
            reordered = [dict(RULES[1], position=1), dict(RULES[0], position=2)]
            r = await c.put(
                "/api/board/project-rules",
                json={"rules": list(reversed(reordered)), "expected_version": version},
                headers=HEADERS,
            )
            assert r.status_code == 200
            got = (await c.get("/api/board/project-rules")).json()
        assert [x["name"] for x in got["rules"]] == ["Alpha", "Run"]
        assert got["version"] != version

    async def test_pg_put_deletes_inserts_and_audits_under_lock(self, settings, monkeypatch):
        store, conn = _build("pg", monkeypatch)
        _seed_rules("pg", store, conn)
        async with _client(settings, store) as c:
            r = await c.put(
                "/api/board/project-rules",
                json={"rules": RULES, "expected_version": STAMP_ISO},
                headers=HEADERS,
            )
        assert r.status_code == 200
        assert conn.sql("pg_advisory_xact_lock")
        assert conn.sql("DELETE FROM project_rules")
        assert conn.params("INSERT INTO project_rules") == [
            ("Run", "/d/example/mission/running", 1),
            ("Alpha", "/d/example/*", 2),
        ]
        assert any("project rules set" in str(p) for p in conn.params("INSERT INTO changelog"))


I18N_SEED = {
    "languages": [{"code": "de", "name": "Deutsch"}],
    "strings": {"de": {"k": "Hallo"}},
}


def _seed_i18n(backend: str, store, conn, *, edited: bool = False) -> None:
    if backend == "memory":
        store.seed_i18n(I18N_SEED)
        return
    conn.on("SELECT code, name, enabled FROM i18n_languages", [
        {"code": "de", "name": "Deutsch", "enabled": True},
    ])
    conn.on("SELECT lang, key, text FROM i18n_strings", [
        {"lang": "de", "key": "k", "text": "Edited" if edited else "Hallo"},
    ])


def _language_codes(body: dict) -> list[str]:
    return [x["code"] for x in body["languages"]]


class TestI18n:
    @BACKENDS
    async def test_get_puts_english_first_and_fills_from_file(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/i18n")
        assert r.status_code == 200
        body = r.json()
        assert _language_codes(body) == ["en", "de"]
        assert body["strings"]["en"]["app.title"] == "Missions"
        assert body["strings"]["de"]["k"] == "Hallo"

    @BACKENDS
    async def test_put_without_dashboard_header_is_403(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.put("/api/board/i18n/de/k", json={"text": "Neu"})
        assert r.status_code == 403
        if conn is not None:
            assert not conn.sql("INSERT INTO i18n_strings")

    @BACKENDS
    async def test_put_unknown_language_is_404(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.put("/api/board/i18n/xx/k", json={"text": "Neu"}, headers=HEADERS)
        assert r.status_code == 404

    @BACKENDS
    async def test_put_text_is_stored_and_served(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.put("/api/board/i18n/de/k", json={"text": "Neu"}, headers=HEADERS)
            assert r.status_code == 200
            assert r.json() == {"lang": "de", "key": "k", "text": "Neu"}
            if backend == "memory":
                got = (await c.get("/api/board/i18n")).json()
                assert got["strings"]["de"]["k"] == "Neu"
        if conn is not None:
            assert any("Neu" in p for p in conn.params("INSERT INTO i18n_strings"))

    @BACKENDS
    async def test_put_empty_text_deletes_and_english_falls_back_to_file(
        self, settings, backend, monkeypatch
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        if backend == "memory":
            store.put_i18n_string("en", "app.title", "Custom")
        else:
            conn.on("SELECT text FROM i18n_strings", [{"text": "Custom"}])
        async with _client(settings, store) as c:
            r = await c.put("/api/board/i18n/en/app.title", json={"text": ""}, headers=HEADERS)
            assert r.status_code == 200
            got = (await c.get("/api/board/i18n")).json()
        assert got["strings"]["en"]["app.title"] == "Missions"
        if conn is not None:
            assert conn.sql("DELETE FROM i18n_strings")
            assert not conn.sql("INSERT INTO i18n_strings")

    @BACKENDS
    async def test_english_language_cannot_be_removed(self, settings, backend, monkeypatch):
        """No route removes or disables a language, so English can only be
        removed through an edit; deleting its last stored string must leave
        it in the list (the file still backs it) and no INSERT may follow."""
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        if backend == "memory":
            store.put_i18n_string("en", "app.title", "Custom")
        else:
            conn.on("SELECT text FROM i18n_strings", [{"text": "Custom"}])
        async with _client(settings, store) as c:
            r = await c.put("/api/board/i18n/en/app.title", json={"text": ""}, headers=HEADERS)
            assert r.status_code == 200
            body = (await c.get("/api/board/i18n")).json()
        assert _language_codes(body) == ["en", "de"]
        if conn is not None:
            assert not conn.sql("INSERT INTO i18n_strings")

    @BACKENDS
    @pytest.mark.parametrize("payload", [
        {"code": "D", "name": "Deutsch"},
        {"code": "DE", "name": "Deutsch"},
        {"code": "de", "name": ""},
        {"code": "de"},
    ])
    async def test_add_language_rejects_bad_input_with_400(
        self, settings, backend, monkeypatch, payload
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.post("/api/board/i18n/languages", json=payload, headers=HEADERS)
        assert r.status_code == 400

    @BACKENDS
    async def test_add_language_duplicate_is_409(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        if conn is not None:
            conn.on("SELECT 1 FROM i18n_languages WHERE code", lambda p: [{"x": 1}] if p[0] == "de" else [])
        async with _client(settings, store) as c:
            r = await c.post("/api/board/i18n/languages", json={"code": "de", "name": "Deutsch"}, headers=HEADERS)
        assert r.status_code == 409

    @BACKENDS
    async def test_add_language_success(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        _seed_i18n(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.post("/api/board/i18n/languages", json={"code": "fr", "name": "Français"}, headers=HEADERS)
            assert r.status_code == 200
            if backend == "memory":
                assert "fr" in _language_codes((await c.get("/api/board/i18n")).json())
        if conn is not None:
            assert any(p[:2] == ("fr", "Français") for p in conn.params("INSERT INTO i18n_languages"))

    @BACKENDS
    async def test_seed_fills_an_empty_store(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        if conn is not None:
            conn.on("SELECT EXISTS", [{"has_rows": False}])
        async with _client(settings, store, seeded=False) as c:
            r = await c.get("/api/board/i18n")
        assert r.status_code == 200
        assert "en" in _language_codes(r.json())
        if conn is not None:
            assert conn.sql("INSERT INTO i18n_languages")
        else:
            assert "ru" in _language_codes(r.json())

    @BACKENDS
    async def test_seed_leaves_an_edited_store_alone(self, settings, backend, monkeypatch):
        store, conn = _build(backend, monkeypatch)
        # Edit a non-English row (English strings fall back to the file, so an
        # edited English row is not observable in the response either way).
        if backend == "memory":
            store.seed_i18n(I18N_SEED)
            store.put_i18n_string("de", "k", "Edited")
        else:
            _seed_i18n(backend, store, conn, edited=True)
            conn.on("SELECT EXISTS", [{"has_rows": True}])
        async with _client(settings, store, seeded=False) as c:
            r = await c.get("/api/board/i18n")
        assert r.status_code == 200
        body = r.json()
        assert body["strings"]["de"]["k"] == "Edited"
        assert "ru" not in _language_codes(body)
        if conn is not None:
            assert not conn.sql("INSERT INTO i18n_")
            assert not conn.sql("DELETE FROM i18n_")


MISSIONS = _fixture("missions.json")["missions"]
MISSION = {m["mission_id"]: m for m in MISSIONS}
WORKING_DIRECTORY = {
    "m_running": "/d/example/mission/running",
    "m_done": "/d/example/mission/done",
    "m_queued": "/d/other/queued-folder",
    "m_failed": "/d/other/failed-run",
}
MISSION_GROUPS = {"m_running": "running", "m_done": "completed", "m_failed": "not_completed", "m_queued": "queued"}
PLAN = _fixture("plan.json")["plan"]
PLAN_WITH_SCRATCH = {"tasks": PLAN["tasks"] + [
    {"id": "scratch", "title": "Scratch", "objective": "", "dependencies": []},
]}
CARD = {
    "tasks": {"T001": {"attempts": 2}, "T002": {"attempts": 1}},
    "thinking_steps": 4,
    "thinking_runs": 1,
    "worker_hours": 1.5,
    "input_tokens": 100,
    "output_tokens": 50,
    "cache_ratio": 0.4,
    "cost_usd": 0.02,
    "tok_per_s": 12.5,
}
FIGURE_KEYS = ["attempts", "hours", "tokens", "cache_ratio", "cost", "tok_s", "thinking_share"]
EVENT_COLUMNS = [
    "state_key", "task_id", "task_index", "task_attempt", "event_type",
    "worker_run_id", "worker_status", "worker_output", "worker_error",
    "reviewer_run_id", "reviewer_status", "reviewer_output", "reviewer_error",
    "review_status", "review_score", "review_reason", "review_next_task", "created_at",
]


def _ev(task_id, index, attempt, event_type, **extra):
    return {"state_key": "sk_running", "task_id": task_id, "task_index": index,
            "task_attempt": attempt, "event_type": event_type, **extra}


EVENTS = [
    _ev("T001", 0, 1, "worker_completed"),
    _ev("T001", 0, 1, "review_parsed", review_status="needs_changes",
        review_reason="needs_changes", review_score=0.4),
    _ev("T001", 0, 2, "review_parsed", review_status="pass", review_reason="pass", review_score=0.9),
    _ev("T002", 1, 1, "worker_terminal", worker_status="error"),
]


def _event_row(e: dict) -> dict:
    return {c: e.get(c) for c in EVENT_COLUMNS}


def _mission_row(m: dict) -> dict:
    return {
        "mission_id": m["mission_id"],
        "state_namespace": m["state_namespace"],
        "name": m["name"],
        "objective": m["objective"],
        "working_directory": m["working_directory"],
        "version": m["version"],
        "enabled": m["enabled"],
        "rules_json": json.dumps(m["rules"]),
        "acceptance_criteria_json": json.dumps(m["acceptance_criteria"]),
        "retrieval_corpora_json": json.dumps(m["retrieval_corpora"]),
        "createdAt": m["created_at"],
        "updatedAt": m["updated_at"],
    }


def _live_row(state_key: str, status: str, plan=None, task_index=None, task_attempt=None) -> dict:
    return {
        "state_key": state_key, "worker_session_id": None,
        "plan_json": None if plan is None else json.dumps(plan),
        "task_index": task_index, "task_attempt": task_attempt, "status": status,
        "review_feedback": None, "last_review_score": None, "last_review_reason": None,
        "updated_at": None, "reviewer_session_id": None, "reviewer_run_id": None,
        "reviewer_status": None, "worker_run_id": None, "worker_status": None,
        "architect_run_id": None, "architect_status": None, "architect_session_id": None,
    }


def _mission_list(missions: list[dict]) -> list[dict]:
    return [{**m, "working_directory": WORKING_DIRECTORY.get(m["mission_id"], m["working_directory"])}
            for m in missions]


@pytest.fixture
def card_stub(monkeypatch):
    board_stats._SUMMARY_CACHE.clear()

    def fake_card(manager, board, state_key):
        if state_key == "sk_failed":
            raise RuntimeError("runner down")
        return CARD

    monkeypatch.setattr(board_stats, "mission_card_stats", fake_card)
    yield
    board_stats._SUMMARY_CACHE.clear()


def _seed_summary(backend: str, store, conn) -> None:
    from claude_runner.board import Mission, QueueEntry
    from claude_runner.board_pg import _EVENT_LOG_TABLE, _LIVE_STATE_TABLE, _MISSIONS_TABLE

    missions = _mission_list(MISSIONS)
    _seed_rules(backend, store, conn)
    if backend == "memory":
        for m in missions:
            store._missions[m["mission_id"]] = Mission(**m)
        store._queue["m_running"] = QueueEntry(position=0, mission_id="m_running", enabled=True)
        store._queue["m_queued"] = QueueEntry(position=1, mission_id="m_queued", enabled=True)
        store._queue_order.extend(["m_running", "m_queued"])
        store.upsert_live_state("sk_running", None, status="active", task_index=1,
                                task_attempt=2, plan=PLAN_WITH_SCRATCH)
        store.upsert_live_state("sk_queued", None, status="queued")
        store.upsert_live_state("sk_done", None, status="completed")
        store.upsert_live_state("sk_failed", None, status="failed")
        for e in EVENTS:
            store.append_event("sk_running", e)
        return
    live = {
        "sk_running": _live_row("sk_running", "active", PLAN_WITH_SCRATCH, 1, 2),
        "sk_queued": _live_row("sk_queued", "queued"),
        "sk_done": _live_row("sk_done", "completed"),
        "sk_failed": _live_row("sk_failed", "failed"),
    }
    conn.on(f"FROM {_MISSIONS_TABLE} ORDER BY mission_id", [_mission_row(m) for m in missions])
    conn.on("FROM queue ORDER BY position", [
        {"position": 0, "mission_id": "m_running", "enabled": True,
         "attempts_per_task": None, "retry_failed": None, "note": None},
        {"position": 1, "mission_id": "m_queued", "enabled": True,
         "attempts_per_task": None, "retry_failed": None, "note": None},
    ])
    conn.on(f"FROM {_LIVE_STATE_TABLE} WHERE state_key", lambda p: [live[p[0]]] if p[0] in live else [])
    conn.on(f"FROM {_EVENT_LOG_TABLE} WHERE state_key",
            lambda p: [_event_row(e) for e in EVENTS if e["state_key"] == p[0]])


class TestSummary:
    @BACKENDS
    async def test_cards_carry_group_position_project_and_figures(
        self, settings, backend, monkeypatch, card_stub
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_summary(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/summary")
            first = {m["mission_id"]: m for m in r.json()["missions"]}
            # a cold summary answers at once with null figures and fills them in the background
            assert first["m_running"]["attempts"] in (None, 3)
            for _ in range(100):
                r = await c.get("/api/board/summary")
                if r.json()["missions"] and all(
                    m["attempts"] is not None for m in r.json()["missions"]
                ):
                    break
                await asyncio.sleep(0.05)
        assert r.status_code == 200
        by_id = {m["mission_id"]: m for m in r.json()["missions"]}
        assert set(by_id) == set(MISSION_GROUPS)
        for mid, group in MISSION_GROUPS.items():
            assert by_id[mid]["state_group"] == group
        assert by_id["m_running"]["position"] == 0
        assert by_id["m_queued"]["position"] == 1
        assert by_id["m_done"]["position"] is None
        assert by_id["m_running"]["name"] == MISSION["m_running"]["name"]
        assert by_id["m_running"]["project"] == {
            "key": "Run", "name": "Run", "rule": "/d/example/mission/running",
        }
        assert by_id["m_queued"]["project"] == {
            "key": "queued-folder", "name": "queued-folder", "rule": None,
        }
        running = by_id["m_running"]
        assert {k: running[k] for k in FIGURE_KEYS} == {
            "attempts": 3, "hours": 1.5, "tokens": 150, "cache_ratio": 0.4,
            "cost": 0.02, "tok_s": 12.5, "thinking_share": 0.25,
        }

    @BACKENDS
    async def test_projects_match_rules_by_position_and_default_folder(
        self, settings, backend, monkeypatch, card_stub
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_summary(backend, store, conn)
        async with _client(settings, store) as c:
            projects = (await c.get("/api/board/summary")).json()["projects"]
        assert projects == [
            {"key": "Alpha", "name": "Alpha", "rule": "/d/example/*", "mission_ids": ["m_done"]},
            {"key": "Run", "name": "Run", "rule": "/d/example/mission/running", "mission_ids": ["m_running"]},
            {"key": "failed-run", "name": "failed-run", "rule": None, "mission_ids": ["m_failed"]},
            {"key": "queued-folder", "name": "queued-folder", "rule": None, "mission_ids": ["m_queued"]},
        ]

    @BACKENDS
    async def test_tasks_skip_non_task_ids_and_map_statuses(
        self, settings, backend, monkeypatch, card_stub
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_summary(backend, store, conn)
        async with _client(settings, store) as c:
            by_id = {m["mission_id"]: m for m in (await c.get("/api/board/summary")).json()["missions"]}
        assert by_id["m_running"]["tasks"] == [
            {"id": "T001", "title": "Prepare the working folder", "status": "accepted_retry",
             "attempts": 2, "deps": []},
            {"id": "T002", "title": "Extract the source tables", "status": "running",
             "attempts": 1, "deps": ["T001"]},
            {"id": "T003", "title": "Write the report", "status": "waiting",
             "attempts": 0, "deps": ["T002"]},
            {"id": "T013A", "title": "Append the appendix", "status": "waiting",
             "attempts": 0, "deps": ["T003"]},
        ]

    @BACKENDS
    async def test_failing_runner_gives_null_figures_with_200(
        self, settings, backend, monkeypatch, card_stub
    ):
        store, conn = _build(backend, monkeypatch)
        _seed_summary(backend, store, conn)
        async with _client(settings, store) as c:
            r = await c.get("/api/board/summary")
        assert r.status_code == 200
        failed = {m["mission_id"]: m for m in r.json()["missions"]}["m_failed"]
        assert {k: failed[k] for k in FIGURE_KEYS} == {k: None for k in FIGURE_KEYS}

    @BACKENDS
    async def test_mission_without_live_row_is_summarised(
        self, settings, backend, monkeypatch, card_stub
    ):
        from claude_runner.board import Mission
        from claude_runner.board_pg import _MISSIONS_TABLE

        orphan = {**MISSIONS[0], "mission_id": "m_orphan", "state_namespace": "sk_orphan",
                  "working_directory": "/d/x/orphan"}
        store, conn = _build(backend, monkeypatch)
        if backend == "memory":
            store._missions["m_orphan"] = Mission(**orphan)
        else:
            conn.on(f"FROM {_MISSIONS_TABLE} ORDER BY mission_id", [_mission_row(orphan)])
        async with _client(settings, store) as c:
            r = await c.get("/api/board/summary")
        assert r.status_code == 200
        assert [m["mission_id"] for m in r.json()["missions"]] == ["m_orphan"]
