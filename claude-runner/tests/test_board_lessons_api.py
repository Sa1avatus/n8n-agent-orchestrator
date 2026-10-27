"""The operator lessons routes under /api/board: list, get, edit, retire, restore, delete.
The in-memory lessons store only, no database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore, Mission
from claude_runner.board_lessons import InMemoryLessonStore, Lesson, LessonCandidate
from claude_runner.board_lessons_extract import LESSONS_K
from claude_runner.config import Settings

HEADER = {"X-AHAWR-Dashboard": "1"}
STALE = "2000-01-01T00:00:00+00:00"
MISSION_TEXT = "Objective: ship the release notes."
LessonsClient = tuple[AsyncClient, InMemoryLessonStore]


@pytest.fixture
async def lessons_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    app.state.board_store = InMemoryBoardStore()
    app.state.lessons_store = InMemoryLessonStore()
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def lessons_client(lessons_app: FastAPI) -> AsyncIterator[LessonsClient]:
    transport = ASGITransport(app=lessons_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, lessons_app.state.lessons_store


@pytest.fixture
def lessons_on(lessons_app: FastAPI) -> None:
    lessons_app.state.board_store.update_hermes_config(None, lessons_enabled=True)


def _seed(store: InMemoryLessonStore, mission: str = "m1") -> Lesson:
    return store.add(LessonCandidate(
        mission=mission, task_id="T001", task_index=1, kind="fixed-after-retry",
        situation="the parser drops empty lines", lesson="keep empty lines in the parser",
        evidence={"touched_paths": ["src/parser.py"]},
    ))


async def test_list_returns_the_missions_lessons_with_stored_fields(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    first = _seed(store)
    second = _seed(store)
    _seed(store, mission="m2")
    r = await client.get("/api/board/missions/m1/lessons")
    assert r.status_code == 200
    rows: list[dict[str, Any]] = r.json()["lessons"]
    assert [row["id"] for row in rows] == [first.id, second.id]
    assert rows[0]["situation"] == "the parser drops empty lines"
    assert rows[0]["lesson"] == "keep empty lines in the parser"
    assert rows[0]["kind"] == "fixed-after-retry"
    assert rows[0]["status"] == "active"
    assert rows[0]["evidence"] == {"touched_paths": ["src/parser.py"]}
    assert rows[0]["version"] == 1


async def test_get_one_returns_the_stored_lesson(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.get(f"/api/board/lessons/{lesson.id}")
    assert r.status_code == 200
    assert r.json()["lesson"]["id"] == lesson.id
    assert r.json()["lesson"]["mission"] == "m1"
    assert r.json()["lesson"]["updated_at"] == lesson.updated_at


async def test_get_unknown_lesson_is_404(
    lessons_client: LessonsClient,
) -> None:
    client, _ = lessons_client
    r = await client.get("/api/board/lessons/99")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


WRITE_ROUTES = [
    ("PUT", "/api/board/lessons/{id}", {"situation": "s", "lesson": "l"}),
    ("POST", "/api/board/lessons/{id}/retire", {}),
    ("POST", "/api/board/lessons/{id}/restore", {}),
    ("DELETE", "/api/board/lessons/{id}?confirm=true", None),
]


@pytest.mark.parametrize(("method", "path", "body"), WRITE_ROUTES)
async def test_write_routes_without_header_are_403(
    lessons_client: LessonsClient,
    method: str, path: str, body: dict[str, Any] | None,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.request(method, path.format(id=lesson.id), json=body)
    assert r.status_code == 403
    assert store.get(lesson.id) == lesson


STALE_ROUTES = [
    ("PUT", "/api/board/lessons/{id}", {"situation": "s", "lesson": "l"}),
    ("POST", "/api/board/lessons/{id}/retire", {}),
    ("POST", "/api/board/lessons/{id}/restore", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), STALE_ROUTES)
async def test_stale_version_is_409_and_leaves_the_lesson(
    lessons_client: LessonsClient,
    method: str, path: str, body: dict[str, Any],
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.request(
        method, path.format(id=lesson.id), headers=HEADER,
        json={**body, "expected_version": STALE},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert store.get(lesson.id) == lesson


async def test_put_edits_the_text_with_the_current_version(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.put(f"/api/board/lessons/{lesson.id}", headers=HEADER, json={
        "situation": "the parser drops blank lines", "lesson": "keep blank lines",
        "expected_version": lesson.updated_at,
    })
    assert r.status_code == 200
    assert r.json()["lesson"]["situation"] == "the parser drops blank lines"
    stored = store.get(lesson.id)
    assert stored is not None and stored.lesson == "keep blank lines"


async def test_put_rejects_a_non_text_field_with_400(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.put(f"/api/board/lessons/{lesson.id}", headers=HEADER,
                         json={"situation": 5, "lesson": "l"})
    assert r.status_code == 400
    assert store.get(lesson.id) == lesson


async def test_retire_then_restore_changes_the_status(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    retired = await client.post(f"/api/board/lessons/{lesson.id}/retire", headers=HEADER,
                                json={"expected_version": lesson.updated_at})
    assert retired.status_code == 200
    assert retired.json()["lesson"]["status"] == "retired"
    restored = await client.post(f"/api/board/lessons/{lesson.id}/restore", headers=HEADER,
                                 json={"expected_version": retired.json()["lesson"]["updated_at"]})
    assert restored.status_code == 200
    assert restored.json()["lesson"]["status"] == "active"


async def test_delete_without_confirm_is_refused(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.delete(f"/api/board/lessons/{lesson.id}", headers=HEADER)
    assert r.status_code == 403
    assert store.get(lesson.id) == lesson


async def test_delete_with_confirm_removes_the_lesson(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    r = await client.delete(f"/api/board/lessons/{lesson.id}?confirm=true", headers=HEADER)
    assert r.status_code == 200
    assert store.get(lesson.id) is None


def _review(attempt: int, status: str) -> dict[str, Any]:
    return {
        "event_type": "review_parsed", "task_id": "T001", "task_index": 1,
        "task_attempt": attempt, "review_status": status, "review_score": 40,
        "review_reason": "the parser drops empty lines",
        "review_next_task": "keep empty lines in the parser",
        "touched_paths": ["src/parser.py"],
    }


def _write_body(events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mission": "m1", "mission_text": MISSION_TEXT, "max_attempts": 3,
        "events": events,
    }


async def test_write_a_retry_pair_adds_one_lesson(
    lessons_client: LessonsClient, lessons_on: None,
) -> None:
    client, store = lessons_client
    r = await client.post("/api/board/lessons/write", headers=HEADER,
                          json=_write_body([_review(1, "fail"), _review(2, "pass")]))
    assert r.status_code == 200
    assert r.json()["result"] == "add"
    rows = store.list("m1")
    assert len(rows) == 1
    assert rows[0].kind == "fixed-after-retry"
    assert rows[0].lesson == "keep empty lines in the parser"


async def test_write_a_first_attempt_pass_writes_nothing(
    lessons_client: LessonsClient, lessons_on: None,
) -> None:
    client, store = lessons_client
    r = await client.post("/api/board/lessons/write", headers=HEADER,
                          json=_write_body([_review(1, "pass")]))
    assert r.status_code == 200
    assert r.json()["result"] == "none"
    assert store.list("m1") == []


async def test_write_a_duplicate_bumps_the_counter(
    lessons_client: LessonsClient, lessons_on: None,
) -> None:
    client, store = lessons_client
    body = _write_body([_review(1, "fail"), _review(2, "pass")])
    await client.post("/api/board/lessons/write", headers=HEADER, json=body)
    r = await client.post("/api/board/lessons/write", headers=HEADER, json=body)
    assert r.json()["result"] == "bump"
    rows = store.list("m1")
    assert len(rows) == 1
    assert rows[0].times_seen == 2


async def test_select_returns_at_most_k_lessons_and_records_them_as_shown(
    lessons_client: LessonsClient, lessons_on: None,
) -> None:
    client, store = lessons_client
    lessons = [_seed(store) for _ in range(LESSONS_K + 2)]
    r = await client.post("/api/board/lessons/select", headers=HEADER, json={
        "mission": "m1", "task_index": 4, "task_title": "Fix the parser",
        "objective": "keep the parser stable", "scope": [], "files": ["src/parser.py"],
    })
    assert r.status_code == 200
    chosen = r.json()["lesson_ids"]
    assert len(chosen) == LESSONS_K
    assert sum(line.startswith("- [") for line in r.json()["block"].splitlines()) == LESSONS_K
    for lesson in lessons:
        stored = store.get(lesson.id)
        assert stored is not None
        assert stored.times_shown == (1 if lesson.id in chosen else 0)


async def test_select_then_outcome_counts_a_first_attempt_pass(
    lessons_client: LessonsClient, lessons_on: None,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    await client.post("/api/board/lessons/select", headers=HEADER, json={
        "mission": "m1", "task_index": 1, "task_title": "Fix the parser",
        "objective": "keep the parser stable", "scope": [], "files": ["src/parser.py"],
    })
    r = await client.post("/api/board/lessons/outcome", headers=HEADER, json={
        "mission": "m1", "task_index": 1, "first_attempt_passed": True,
    })
    assert r.status_code == 200
    stored = store.get(lesson.id)
    assert stored is not None
    assert stored.times_first_attempt_passed_after == 1


async def test_flag_off_write_select_and_outcome_store_nothing(
    lessons_client: LessonsClient,
) -> None:
    client, store = lessons_client
    lesson = _seed(store)
    written = await client.post("/api/board/lessons/write", headers=HEADER,
                                json=_write_body([_review(1, "fail"), _review(2, "pass")]))
    selected = await client.post("/api/board/lessons/select", headers=HEADER, json={
        "mission": "m1", "task_index": 1, "task_title": "Fix the parser",
        "objective": "keep the parser stable", "scope": [], "files": ["src/parser.py"],
    })
    outcome = await client.post("/api/board/lessons/outcome", headers=HEADER, json={
        "mission": "m1", "task_index": 1, "first_attempt_passed": True,
    })
    assert written.json()["result"] == "off"
    assert selected.json() == {"block": "", "lesson_ids": []}
    assert outcome.json() == {"recorded": False}
    assert store.list("m1") == [lesson]



def _seed_mission(app: FastAPI, tasks: list[str]) -> None:
    store = app.state.board_store
    store._missions["m1"] = Mission(
        mission_id="m1", state_namespace="sk1", name="Source", working_directory="/tmp/x",
        rules=[], acceptance_criteria=[], retrieval_corpora=[],
        updated_at="2026-01-01T00:00:00+00:00",
    )
    store.upsert_live_state("sk1", None, plan={"tasks": [{"id": name} for name in tasks]})


async def test_task_lessons_returns_the_lessons_shown_to_that_task(
    lessons_app: FastAPI, lessons_client: LessonsClient
) -> None:
    client, store = lessons_client
    _seed_mission(lessons_app, ["T001", "T002"])
    lesson = _seed(store)
    store.record_shown("m1", 2, [lesson.id])
    shown = await client.get("/api/board/missions/m1/tasks/T002/lessons")
    other = await client.get("/api/board/missions/m1/tasks/T001/lessons")
    missing = await client.get("/api/board/missions/m1/tasks/T999/lessons")
    assert [row["id"] for row in shown.json()["lessons"]] == [lesson.id]
    assert other.json() == {"lessons": []}
    assert missing.status_code == 404


async def test_lessons_stats_route_returns_the_figures(
    lessons_app: FastAPI, lessons_client: LessonsClient
) -> None:
    client, store = lessons_client
    _seed_mission(lessons_app, ["T001"])
    _seed(store)
    r = await client.get("/api/board/missions/m1/lessons/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["mission_id"] == "m1"
    assert body["lessons"] == {"active": 1, "retired": 0}
    assert set(body) >= {"with_lessons", "without_lessons", "attempts_per_task"}
