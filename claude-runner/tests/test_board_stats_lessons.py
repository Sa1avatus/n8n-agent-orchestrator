"""The lesson figures of board_stats.py: the first-attempt pass rates and the attempts
per task with and without a lesson block, the lesson counts by status, and the
alternation switch (``lesson_block_shown``) that the select route honours.  Made-up
event rows and the in-memory stores; no database."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from claude_runner.api import create_app
from claude_runner.board import InMemoryBoardStore
from claude_runner.board_lessons import InMemoryLessonStore, LessonCandidate
from claude_runner.board_stats import lesson_block_shown, lessons_stats
from claude_runner.config import Settings

HEADER = {"X-AHAWR-Dashboard": "1"}


def _review(task_index: int, attempt: int, status: str) -> dict:
    return {
        "state_key": "sk1", "task_index": task_index, "task_attempt": attempt,
        "task_id": f"T{task_index:03d}", "event_type": "review_parsed",
        "review_status": status,
    }


def _worker(task_index: int, attempt: int) -> dict:
    return {
        "state_key": "sk1", "task_index": task_index, "task_attempt": attempt,
        "task_id": f"T{task_index:03d}", "event_type": "worker_completed",
    }


def _candidate(mission: str = "m1") -> LessonCandidate:
    return LessonCandidate(
        mission=mission, task_id="T001", task_index=1, kind="fixed-after-retry",
        situation="the parser drops empty lines", lesson="keep empty lines in the parser",
        evidence={"touched_paths": ["src/parser.py"]},
    )


def test_lessons_stats_of_made_up_events() -> None:
    board = InMemoryBoardStore()
    # task 1: passes first time; task 2: fails once, then passes;
    # task 3: passes first time and is the first task with a block;
    # task 4: fails twice, then passes
    for row in [
        _worker(1, 0), _review(1, 0, "pass"),
        _worker(2, 0), _review(2, 0, "needs_changes"), _worker(2, 1), _review(2, 1, "pass"),
        _worker(3, 0), _review(3, 0, "pass"),
        _worker(4, 0), _review(4, 0, "needs_changes"),
        _worker(4, 1), _review(4, 1, "needs_changes"),
        _worker(4, 2), _review(4, 2, "pass"),
    ]:
        board.append_event("sk1", row)
    lessons = InMemoryLessonStore()
    active = lessons.add(_candidate())
    retired = lessons.add(_candidate())
    lessons.set_status(retired.id, "retired", retired.updated_at)
    lessons.record_shown("m1", 3, [active.id])

    stats = lessons_stats(board, lessons, "sk1", "m1")

    assert stats["lessons"] == {"active": 1, "retired": 1}
    assert stats["with_lessons"]["tasks"] == 1
    assert stats["with_lessons"]["first_attempt_pass_rate"] == pytest.approx(1.0)
    assert stats["without_lessons"]["tasks"] == 3
    assert stats["without_lessons"]["first_attempt_pass_rate"] == pytest.approx(1 / 3)
    assert stats["attempts_per_task"]["before_first_lesson"] == pytest.approx(1.5)
    assert stats["attempts_per_task"]["after_first_lesson"] == pytest.approx(2.0)


def test_lessons_stats_without_any_block_has_no_after_figure() -> None:
    board = InMemoryBoardStore()
    board.append_event("sk1", _worker(1, 0))
    board.append_event("sk1", _review(1, 0, "pass"))
    stats = lessons_stats(board, InMemoryLessonStore(), "sk1", "m1")
    assert stats["with_lessons"] == {"tasks": 0, "first_attempt_pass_rate": None}
    assert stats["without_lessons"]["first_attempt_pass_rate"] == pytest.approx(1.0)
    assert stats["attempts_per_task"] == {
        "before_first_lesson": pytest.approx(1.0), "after_first_lesson": None,
    }


def test_alternation_off_gives_every_task_the_block() -> None:
    assert all(lesson_block_shown({}, n) for n in range(1, 9))
    assert all(
        lesson_block_shown({"lessons_holdout_enabled": False}, n) for n in range(1, 9)
    )


def test_alternation_on_gives_odd_tasks_the_block_and_even_tasks_the_control() -> None:
    config = {"lessons_holdout_enabled": True}
    assert [lesson_block_shown(config, n) for n in range(1, 7)] == [
        True, False, True, False, True, False,
    ]


@pytest.fixture
async def lessons_app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    app.state.board_store = InMemoryBoardStore()
    app.state.lessons_store = InMemoryLessonStore()
    async with app.router.lifespan_context(app):
        yield app


async def _select_for_each_task(app: FastAPI, holdout: bool) -> list[bool]:
    app.state.board_store.update_hermes_config(
        None, lessons_enabled=True, lessons_holdout_enabled=holdout,
    )
    app.state.lessons_store.add(_candidate())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        shown = []
        for task_index in range(1, 5):
            r = await client.post("/api/board/lessons/select", headers=HEADER, json={
                "mission": "m1", "task_index": task_index, "task_title": "Fix the parser",
                "objective": "keep the parser stable", "scope": [],
                "files": ["src/parser.py"],
            })
            assert r.status_code == 200
            shown.append(bool(r.json()["lesson_ids"]))
    return shown


async def test_select_route_withholds_the_block_from_control_tasks(
    lessons_app: FastAPI,
) -> None:
    assert await _select_for_each_task(lessons_app, holdout=True) == [True, False, True, False]


async def test_select_route_gives_every_task_the_block_when_alternation_is_off(
    lessons_app: FastAPI,
) -> None:
    assert await _select_for_each_task(lessons_app, holdout=False) == [True, True, True, True]
