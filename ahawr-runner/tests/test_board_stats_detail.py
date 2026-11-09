"""The stats routes of ``board_api`` and the new detail routes (T008): the
byte-for-byte shape of the existing mission and task stats responses with both
feature flags off, then the project route, the mission detail and the task
detail behind ``stats_popup``, and the engine block behind
``stats_engine_sampler``.  The fixtures mirror ``test_board_stats.py`` (an
in-memory board store and a ``RunManager`` with explicit local pricing), and the
client is opened the way ``test_board_api.py`` opens it: the lifespan runs first,
because that is where the board routes and their error handlers are attached."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from ahawr_runner import board_api
from ahawr_runner.api import create_app
from ahawr_runner.board import InMemoryBoardStore
from ahawr_runner.board_api import _PROJECT_STATS_CACHE, _STATS_CACHE
from ahawr_runner.board_pg import UnavailableBoardStore
from ahawr_runner.config import Settings
from ahawr_runner.dashboard import DashboardSettings, create_dashboard_app
from ahawr_runner.engine_samples import insert_sample
from ahawr_runner.features_config import parse_features
from ahawr_runner.runs import RunManager
from ahawr_runner.store import Store
from tests.test_board_stats import PRICING_ENV, _add_run, _event, _seed_board, _write_run_events
from tests.test_engine_samples import FakeBoardConn

#: the exact bytes the mission card route answers with both flags off
MISSION_STATS_BYTES = (
    '{"state_key":"sk1","mission_id":"m1","status":"queued","runs":3,"worker_hours":0.25,'
    '"reviewer_minutes":5.0,"architect_minutes":0.0,"input_tokens":200000,'
    '"output_tokens":20000,"cache_read_tokens":20000,"cache_write_tokens":0,'
    '"cost_estimate_usd":3.5,"cost_real_usd":0.5,"cost_usd":1.1,"local_cost_usd":1.1,'
    '"tok_per_s":10000.0,"tok_per_s_local":10000.0,"tok_per_s_cloud":0.0,"cache_ratio":0.091,'
    '"compactions":0,"thinking_runs":2,"thinking_off_runs":0,"thinking_tokens":240000,'
    '"thinking_off_tokens":0,"thinking_steps":2,"max_attempts_per_task":null,'
    '"roles":{"worker":{"label":"Worker","runs":2,"minutes":15.0,"cost_usd":0.6,'
    '"model":null},"reviewer":{"label":"Reviewer","runs":1,"minutes":5.0,"cost_usd":0.5,'
    '"model":null},"architect":{"label":"Architect","runs":0,"minutes":0,"cost_usd":0,'
    '"model":null},"qa":{"label":"QA","runs":0,"minutes":0,"cost_usd":0,'
    '"model":"anthropic/sonnet"}},"tasks":{"T001":{"attempts":2,"last_review_score":90.0,'
    '"last_review_reason":"ok"},"T002":{"attempts":0,"last_review_score":null,'
    '"last_review_reason":null}}}'
)

#: the exact bytes the per-task route answers with both flags off
TASK_STATS_BYTES = (
    '{"state_key":"sk1","mission_id":"m1","task_id":"T001","status":"running","runs":3,'
    '"worker_hours":0.25,"reviewer_minutes":5.0,"architect_minutes":0.0,'
    '"input_tokens":200000,"output_tokens":20000,"cache_read_tokens":20000,'
    '"cache_write_tokens":0,"cost_estimate_usd":3.5,"cost_real_usd":0.5,"cost_usd":1.1,'
    '"tok_per_s":10000.0,"tok_per_s_local":10000.0,"tok_per_s_cloud":0.0,"cache_ratio":0.091,'
    '"compactions":0,"thinking_runs":2,"thinking_off_runs":0,"thinking_tokens":240000,'
    '"thinking_off_tokens":0,"thinking_steps":2,"max_attempts_per_task":null,"attempts":2,'
    '"attempt_seconds":[900.0,300.0],"attempts_detail":[{"attempt":0,"seconds":900.0,'
    '"runs":["run_r1","run_w1"]},{"attempt":1,"seconds":300.0,"runs":["run_r1","run_w2"]}],'
    '"last_review_score":90.0,"last_review_reason":"ok"}'
)


def _run_manager(settings: Settings) -> RunManager:
    """A RunManager over its own sqlite store, with the bench pricing pinned."""
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(settings.data_dir),
        "CLAUDE_RUNNER_CLAUDE_BIN": settings.claude_bin,
        "CLAUDE_RUNNER_WORKSPACE": str(settings.workspace),
        "CLAUDE_RUNNER_PATH_MAP": ";".join(
            f"{src_path}={dst_path}" for src_path, dst_path in settings.path_map
        ),
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        "CLAUDE_RUNNER_COMPACT_MIN_TOKENS": "2500",
        **PRICING_ENV,
    }
    return RunManager(Settings.from_env(env), Store(settings.data_dir / "detail_runs.sqlite"))


def _seed(settings: Settings) -> tuple[InMemoryBoardStore, RunManager]:
    """The mission, its plan, three runs with step events and two attempts of T001."""
    board = InMemoryBoardStore()
    _seed_board(board)
    manager = _run_manager(settings)
    base = datetime(2026, 1, 1, 10, 0)
    _add_run(manager.store, "run_w1", "s1", "worker", base,
             base + timedelta(minutes=10), 2.0, provider="local")
    _add_run(manager.store, "run_w2", "s1", "worker",
            base + timedelta(minutes=20), base + timedelta(minutes=25), 1.0, provider="local")
    _add_run(manager.store, "run_r1", "s2", "reviewer",
            base + timedelta(minutes=30), base + timedelta(minutes=35), 0.5)
    _write_run_events(manager.events, "run_w1", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
         "output_tokens": 10_000, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "generation_ms": 1000},
    ])
    _write_run_events(manager.events, "run_w2", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
         "output_tokens": 10_000, "cache_read_tokens": 20_000,
         "cache_write_tokens": 0, "generation_ms": 1000},
    ])
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=0,
        event_type="worker_completed", worker_run_id="run_w1", worker_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=0,
        event_type="review_parsed", worker_run_id="run_w1",
        review_status="needs_changes", review_score=55.0, review_reason="not enough",
        reviewer_run_id="run_r1", reviewer_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=1,
        event_type="worker_completed", worker_run_id="run_w2", worker_status="completed",
    ))
    board.append_event("sk1", _event(
        "sk1", task_id="T001", task_index=0, task_attempt=1,
        event_type="review_parsed", worker_run_id="run_w2",
        review_status="pass", review_score=90.0, review_reason="ok",
        reviewer_run_id="run_r1", reviewer_status="completed",
    ))
    return board, manager


@pytest.fixture
async def stats_client(settings: Settings) -> AsyncIterator[tuple[AsyncClient, InMemoryBoardStore]]:
    """A runner app with the in-memory board store and the seeded runs in place.

    The lifespan installs its own RunManager, so the seeded one is swapped in
    before the client opens."""
    board, manager = _seed(settings)
    _STATS_CACHE.clear()
    _PROJECT_STATS_CACHE.clear()
    app = create_app(settings)
    app.state.board_store = board
    async with app.router.lifespan_context(app):
        app.state.manager = manager
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, board


def test_stats_popup_bundle_is_served_through_the_static_route() -> None:
    """T011: board.html loads the popup as /static/board_stats_popup.js, so the
    dashboard's fixed static route must serve that exact file (a name the regex
    rejects 404s the button's bundle, as board_i18n.js once did)."""
    app = create_dashboard_app(
        DashboardSettings(runner_url=""),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    with TestClient(app, base_url="http://localhost") as dash:
        response = dash.get("/static/board_stats_popup.js")
        assert response.status_code == 200
        assert "BoardStatsPopup" in response.text
        assert "attach" in response.text


def test_flags_default_off() -> None:
    """The characterization baseline is valid only with both flags off."""
    features, errors = parse_features(None, None)
    assert errors == []
    assert features is not None
    assert features["stats_popup"]["enabled"] is False
    assert features["stats_engine_sampler"]["enabled"] is False


async def test_mission_stats_route_bytes_with_flags_off(stats_client: Any) -> None:
    client, _ = stats_client
    response = await client.get("/api/board/missions/m1/stats")
    assert response.status_code == 200
    assert response.content == MISSION_STATS_BYTES.encode("utf-8")


async def test_task_stats_route_bytes_with_flags_off(stats_client: Any) -> None:
    client, _ = stats_client
    response = await client.get("/api/board/missions/m1/tasks/T001/stats")
    assert response.status_code == 200
    assert response.content == TASK_STATS_BYTES.encode("utf-8")


def _set_flags(
    board: InMemoryBoardStore,
    *,
    popup: bool,
    sampler: bool,
    mission_id: str | None = None,
) -> None:
    """Turn the stats flags on for the board (no mission) or for one mission."""
    text = json.dumps(
        {"stats_popup": {"enabled": popup}, "stats_engine_sampler": {"enabled": sampler}}
    )
    if mission_id is None:
        board.update_hermes_config(None, features_json=text)
        return
    board.set_mission_config(
        mission_id, {**(board.mission_config(mission_id) or {}), "mission_features_json": text},
        None,
    )


def _error_code_and_message(response: Any) -> tuple[str, str]:
    payload = response.json()
    return payload["error"]["code"], payload["error"]["message"]


async def test_mission_detail_refused_when_popup_off(stats_client: Any) -> None:
    client, _ = stats_client
    response = await client.get("/api/board/missions/m1/stats/detail")
    assert response.status_code == 404
    code, message = _error_code_and_message(response)
    assert code == "not_found"
    assert "stats_popup" in message


async def test_task_detail_refused_when_popup_off(stats_client: Any) -> None:
    client, _ = stats_client
    response = await client.get("/api/board/missions/m1/tasks/T001/stats/detail")
    assert response.status_code == 404
    code, message = _error_code_and_message(response)
    assert code == "not_found"
    assert "stats_popup" in message


async def test_project_stats_refused_when_popup_off(stats_client: Any) -> None:
    client, _ = stats_client
    response = await client.get("/api/board/projects/model-bench/stats")
    assert response.status_code == 404
    code, message = _error_code_and_message(response)
    assert code == "not_found"
    assert "stats_popup" in message


async def test_detail_routes_refuse_when_the_store_is_unavailable(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new routes answer the store's own 503 before any flag is read.

    get_board_store is patched, the way test_board_api_store_retry.py patches it:
    with a real DSN in the environment the lifespan would rebuild a working store
    on the first request instead of keeping the unavailable one."""

    def build() -> UnavailableBoardStore:
        return UnavailableBoardStore("DSN is not set")

    monkeypatch.setattr(board_api, "get_board_store", build)
    app = create_app(settings)
    app.state.board_store = UnavailableBoardStore("DSN is not set")
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            "/api/board/missions/m1/stats/detail",
            "/api/board/missions/m1/tasks/T001/stats/detail",
            "/api/board/projects/model-bench/stats",
        ):
            response = await client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "unavailable"


async def test_unknown_mission_or_task_stats_detail_is_404(stats_client: Any) -> None:
    client, board = stats_client
    _set_flags(board, popup=True, sampler=False, mission_id="m1")
    board.put_project_rules(
        [{"name": "model-bench", "pattern": "D:/ClaudeProjects/model-bench/*", "position": 1}],
        None,
    )
    for path in (
        "/api/board/missions/nope/stats/detail",
        "/api/board/missions/m1/tasks/T999/stats/detail",
        "/api/board/projects/ghost-project/stats",
    ):
        response = await client.get(path)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"


async def test_invalid_path_values_are_rejected(stats_client: Any) -> None:
    client, board = stats_client
    _set_flags(board, popup=True, sampler=False, mission_id="m1")
    bad_task = await client.get("/api/board/missions/m1/tasks/T-1/stats/detail")
    assert bad_task.status_code == 400
    assert bad_task.json()["error"]["code"] == "bad_request"
    # %00 decodes to a control character, which _PROJECT_KEY_RE rejects
    bad_project = await client.get("/api/board/projects/%00/stats")
    assert bad_project.status_code == 400
    assert bad_project.json()["error"]["code"] == "bad_request"


async def test_mission_detail_figures(stats_client: Any) -> None:
    client, board = stats_client
    _set_flags(board, popup=True, sampler=False, mission_id="m1")
    figures = (await client.get("/api/board/missions/m1/stats/detail")).json()
    assert figures["closed_tasks"] == 1
    assert figures["planned_tasks"] == 2
    assert figures["extra_attempts"] == 1
    assert figures["avg_final_review_score"] == 90.0
    assert figures["avg_first_review_score"] == 55.0
    assert figures["steps"] == 2
    assert figures["output_tokens"] == 20000
    assert figures["cost_usd"] == 0.6
    assert figures["tasks"] == [
        {
            "task_id": "T001",
            "attempts": 2,
            "first_review_score": 55.0,
            "final_review_score": 90.0,
            "passed": True,
        }
    ]
    assert "engine" not in figures


async def test_task_detail_attempt_rows(stats_client: Any) -> None:
    client, board = stats_client
    _set_flags(board, popup=True, sampler=False, mission_id="m1")
    figures = (await client.get("/api/board/missions/m1/tasks/T001/stats/detail")).json()
    attempts = figures["attempts"]
    assert [row["attempt"] for row in attempts] == [0, 1]
    # the shared reviewer run counts on the first attempt only (board_stats
    # _attempt_runs: "a shared reviewer run counts once, on its first attempt")
    assert set(attempts[0]["runs"]) == {"run_w1", "run_r1"}
    assert set(attempts[1]["runs"]) == {"run_w2"}
    assert attempts[0]["figures"]["closed_tasks"] == 0
    assert attempts[1]["figures"]["closed_tasks"] == 1
    # stats_for_runs rounds hours to 2 decimals, so 5 Worker minutes report as 0.08 h
    assert attempts[1]["figures"]["worker_hours"] == pytest.approx(0.08)
    assert "engine" not in figures


async def test_project_stats_figures_and_breakdown(stats_client: Any) -> None:
    client, board = stats_client
    board.put_project_rules(
        [{"name": "model-bench", "pattern": "D:/ClaudeProjects/model-bench/*", "position": 1}],
        None,
    )
    board._missions["m1"] = board._missions["m1"].model_copy(
        update={"working_directory": "D:/ClaudeProjects/model-bench/rag-platform"}
    )
    _set_flags(board, popup=True, sampler=False)
    figures = (await client.get("/api/board/projects/model-bench/stats")).json()
    assert [row["mission_id"] for row in figures["missions"]] == ["m1"]
    assert figures["closed_tasks"] == 1
    assert figures["worker_hours"] == pytest.approx(0.25)
    assert figures["reasons"] == {}
    assert "engine" not in figures


async def test_project_stats_of_a_rule_with_no_missions(stats_client: Any) -> None:
    client, board = stats_client
    board.put_project_rules(
        [{"name": "idle-bench", "pattern": "D:/ClaudeProjects/idle-bench/*", "position": 1}],
        None,
    )
    _set_flags(board, popup=True, sampler=False)
    figures = (await client.get("/api/board/projects/idle-bench/stats")).json()
    assert figures["missions"] == []
    assert figures["closed_tasks"] == 0
    assert figures["planned_tasks"] is None
    assert figures["reasons"]["planned_tasks"] == "no plan and no review rows"


async def test_existing_routes_keep_their_bytes_with_the_flags_on(stats_client: Any) -> None:
    """The new flag changes nothing on the routes that existed before T008."""
    client, board = stats_client
    _set_flags(board, popup=True, sampler=True, mission_id="m1")
    mission = await client.get("/api/board/missions/m1/stats")
    task = await client.get("/api/board/missions/m1/tasks/T001/stats")
    assert mission.status_code == 200
    assert mission.content == MISSION_STATS_BYTES.encode("utf-8")
    assert task.status_code == 200
    assert task.content == TASK_STATS_BYTES.encode("utf-8")


async def test_engine_block_is_null_with_no_samples(stats_client: Any) -> None:
    """The in-memory store has no connection, so it can hold no engine samples."""
    client, board = stats_client
    _set_flags(board, popup=True, sampler=True, mission_id="m1")
    figures = (await client.get("/api/board/missions/m1/stats/detail")).json()
    assert figures["engine"] is None
    assert figures["engine_reason"] == "no samples"


async def test_engine_block_of_the_window_of_the_mission_runs(stats_client: Any) -> None:
    """Samples inside the run window (10:00-10:35) are reported, outside are not."""
    client, board = stats_client
    conn = FakeBoardConn()
    for sample_key, sampled_at, tok_per_s, generated, source, vram, low in (
        ("in1", "2026-01-01T10:05:00", 12.0, 60, "sampled", 800, False),
        ("in2", "2026-01-01T10:25:00", 20.0, 80, "backfill-segment", 400, True),
        ("in3", "2026-01-01T10:26:00", 100.0, 40, "sampled", 900, False),
        ("before", "2026-01-01T09:59:59", 5.0, 60, "sampled", 100, True),
        ("at_end", "2026-01-01T10:35:00", 9.0, 60, "sampled", 100, True),
    ):
        insert_sample(
            conn,
            sampled_at=sampled_at,
            engine_run=1,
            generated_tokens=generated,
            tok_per_s=tok_per_s,
            drafts_accepted=30 if sample_key == "in1" else 10,
            drafts_total=100 if sample_key in ("in1", "in2") else 0,
            expert_slots=4 if sample_key == "in1" else 7,
            vram_free_mib=vram,
            low_vram=low,
            source=source,
            sample_key=sample_key,
        )
    board._conn = conn
    _set_flags(board, popup=True, sampler=True, mission_id="m1")
    figures = (await client.get("/api/board/missions/m1/stats/detail")).json()
    assert figures["engine"]["median_tok_s"] == 16.0  # the 100 tok/s reply is below MIN tokens
    assert figures["engine"]["samples_by_source"] == {"sampled": 2, "backfill-segment": 1}
    assert figures["engine"]["min_free_vram_mib"] == 400
    assert figures["engine"]["low_vram_count"] == 1
    assert figures["engine"]["expert_slots"] == 4
    assert "engine_reason" not in figures


async def test_project_engine_block_of_the_project_runs(stats_client: Any) -> None:
    """The project window is the runs of every mission under its rule: an
    in-memory store holds no samples, then the fake connection fills the window."""
    client, board = stats_client
    board.put_project_rules(
        [{"name": "model-bench", "pattern": "D:/ClaudeProjects/model-bench/*", "position": 1}],
        None,
    )
    board._missions["m1"] = board._missions["m1"].model_copy(
        update={"working_directory": "D:/ClaudeProjects/model-bench/rag-platform"}
    )
    _set_flags(board, popup=True, sampler=True)
    figures = (await client.get("/api/board/projects/model-bench/stats")).json()
    assert figures["engine"] is None
    assert figures["engine_reason"] == "no samples"

    conn = FakeBoardConn()
    insert_sample(
        conn,
        sampled_at="2026-01-01T10:12:00",
        engine_run=2,
        generated_tokens=60,
        tok_per_s=14.0,
        drafts_accepted=20,
        drafts_total=50,
        expert_slots=6,
        vram_free_mib=700,
        low_vram=False,
        source="sampled",
        sample_key="project-in",
    )
    insert_sample(
        conn,
        # one minute past the last run of the project (10:35) stays out of the window
        sampled_at="2026-01-01T10:36:00",
        engine_run=2,
        generated_tokens=60,
        tok_per_s=5.0,
        drafts_accepted=0,
        drafts_total=0,
        expert_slots=6,
        vram_free_mib=100,
        low_vram=True,
        source="sampled",
        sample_key="project-out",
    )
    board._conn = conn
    _PROJECT_STATS_CACHE.clear()  # new samples need a refresh
    figures = (await client.get("/api/board/projects/model-bench/stats")).json()
    assert figures["engine"]["median_tok_s"] == 14.0
    assert figures["engine"]["samples_by_source"] == {"sampled": 1}
    assert figures["engine"]["min_free_vram_mib"] == 700
    assert figures["engine"]["low_vram_count"] == 0


def test_project_stats_are_computed_once_while_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """The second request inside the max age reads the stored figures: the 240 s
    computation of the n8n project must not run on every popup open."""
    _PROJECT_STATS_CACHE.clear()
    calls: list[str] = []

    def fake_payload(store: Any, manager: Any, project: str) -> dict[str, Any]:
        calls.append(project)
        return {"project": project, "n": len(calls)}

    monkeypatch.setattr(board_api, "_project_stats_payload", fake_payload)
    first = board_api._cached_project_stats(None, None, "p")  # type: ignore[arg-type]
    second = board_api._cached_project_stats(None, None, "p")  # type: ignore[arg-type]
    assert first == second == {"project": "p", "n": 1}
    monkeypatch.setattr(board_api, "PROJECT_STATS_MAX_AGE_SECONDS", 0.0)
    third = board_api._cached_project_stats(None, None, "p")  # type: ignore[arg-type]
    assert third["n"] == 2
    _PROJECT_STATS_CACHE.clear()
