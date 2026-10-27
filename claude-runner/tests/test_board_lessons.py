"""InMemoryLessonStore and PgLessonStore (over a fake connection): add, list, get, edit with
the current and a stale version, retire and restore, delete with and without confirm,
bump_duplicate, and the field limits of docs/lessons.md (text cut with '...', evidence
refused, task_index and kind validated)."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from claude_runner.board import ConflictError, GuardError
from claude_runner.board_lessons import (
    EVIDENCE_MAX_CHARS,
    LESSON_MAX_CHARS,
    LESSONS_RETIRE_MIN_SHOWN,
    MISSION_MAX_CHARS,
    SITUATION_MAX_CHARS,
    TASK_ID_MAX_CHARS,
    InMemoryLessonStore,
    Lesson,
    LessonCandidate,
    PgLessonStore,
)


def _candidate(**overrides: Any) -> LessonCandidate:
    fields: dict[str, Any] = {
        "mission": "ahawr-demo",
        "task_id": "T004",
        "task_index": 4,
        "kind": "fixed-after-retry",
        "situation": "The first attempt edited the parser without running its test.",
        "lesson": "Run the parser test before reporting.",
        "evidence": {"failed_attempt": 1, "accepted_attempt": 2, "touched_paths": ["src/p.py"]},
    }
    fields.update(overrides)
    return LessonCandidate(**fields)


def _stored(store: InMemoryLessonStore, lesson_id: int) -> Lesson:
    row = store.get(lesson_id)
    assert row is not None
    return row


def test_add_returns_active_row_with_zero_counters() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    assert row.id == 1
    assert row.status == "active"
    assert (row.times_shown, row.times_first_attempt_passed_after) == (0, 0)
    assert (row.times_seen, row.version) == (1, 1)
    assert store.get(row.id) == row


def test_get_unknown_id_is_none() -> None:
    assert InMemoryLessonStore().get(99) is None


def test_list_filters_by_mission_and_status() -> None:
    store = InMemoryLessonStore()
    first = store.add(_candidate())
    store.add(_candidate(mission="ahawr-other"))
    store.set_status(first.id, "retired", None)
    assert [row.id for row in store.list("ahawr-demo")] == [first.id]
    assert store.list("ahawr-demo", status="active") == []
    assert [row.id for row in store.list("ahawr-demo", status="retired")] == [first.id]


def test_edit_with_current_version_changes_text() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    edited = store.edit_text(row.id, "New situation.", "New lesson.", row.updated_at)
    assert (edited.situation, edited.lesson) == ("New situation.", "New lesson.")
    assert _stored(store, row.id) == edited


def test_edit_with_stale_version_is_a_conflict() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.edit_text(row.id, "First edit.", "First lesson.", row.updated_at)
    with pytest.raises(ConflictError):
        store.edit_text(row.id, "Stale edit.", "Stale lesson.", row.updated_at)
    assert _stored(store, row.id).situation == "First edit."


def test_retire_then_restore_with_current_versions() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    retired = store.set_status(row.id, "retired", row.updated_at)
    assert retired.status == "retired"
    restored = store.set_status(row.id, "active", retired.updated_at)
    assert restored.status == "active"


def test_retire_with_stale_version_is_a_conflict() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.set_status(row.id, "retired", row.updated_at)
    with pytest.raises(ConflictError):
        store.set_status(row.id, "active", row.updated_at)
    assert _stored(store, row.id).status == "retired"


def test_delete_without_confirm_is_refused_and_keeps_row() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    with pytest.raises(GuardError, match="confirm=true"):
        store.delete(row.id, confirm=False)
    assert store.get(row.id) is not None


def test_delete_with_confirm_removes_row() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.delete(row.id, confirm=True)
    assert store.get(row.id) is None


def test_bump_duplicate_raises_times_seen() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    assert store.bump_duplicate(row.id).times_seen == 2
    assert _stored(store, row.id).times_seen == 2


def test_text_over_limit_is_cut_and_marked_with_ellipsis() -> None:
    store = InMemoryLessonStore()
    row = store.add(
        _candidate(
            mission="m" * (MISSION_MAX_CHARS + 1),
            task_id="T" * (TASK_ID_MAX_CHARS + 1),
            situation="s" * (SITUATION_MAX_CHARS + 1),
            lesson="l" * (LESSON_MAX_CHARS + 1),
        )
    )
    assert len(row.mission) == MISSION_MAX_CHARS
    assert len(row.task_id) == TASK_ID_MAX_CHARS
    assert len(row.situation) == SITUATION_MAX_CHARS
    assert len(row.lesson) == LESSON_MAX_CHARS
    assert row.lesson.endswith("...")


def test_text_at_limit_is_kept_whole() -> None:
    row = InMemoryLessonStore().add(_candidate(situation="s" * SITUATION_MAX_CHARS))
    assert row.situation == "s" * SITUATION_MAX_CHARS


def test_edit_cuts_text_over_limit() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    edited = store.edit_text(row.id, "s", "l" * (LESSON_MAX_CHARS + 50), row.updated_at)
    assert len(edited.lesson) == LESSON_MAX_CHARS


def test_evidence_over_limit_is_refused() -> None:
    long_path = "x" * EVIDENCE_MAX_CHARS
    with pytest.raises(ValueError, match="evidence"):
        InMemoryLessonStore().add(_candidate(evidence={"touched_paths": [long_path]}))


def test_record_shown_raises_times_shown_for_each_prompt() -> None:
    store = InMemoryLessonStore()
    first = store.add(_candidate())
    second = store.add(_candidate(task_index=5))
    store.record_shown("ahawr-demo", 4, [first.id, second.id])
    store.record_shown("ahawr-demo", 4, [first.id])
    assert (_stored(store, first.id).times_shown, _stored(store, second.id).times_shown) == (2, 1)


def test_record_outcome_pass_counts_once_per_task() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.record_shown("ahawr-demo", 4, [row.id])
    store.record_outcome("ahawr-demo", 4, first_attempt_passed=True)
    store.record_outcome("ahawr-demo", 4, first_attempt_passed=True)
    stored = _stored(store, row.id)
    assert (stored.times_shown, stored.times_first_attempt_passed_after) == (1, 1)


def test_record_outcome_fail_or_other_task_leaves_passed_count() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.record_shown("ahawr-demo", 4, [row.id])
    store.record_outcome("ahawr-demo", 5, first_attempt_passed=True)
    store.record_outcome("ahawr-demo", 4, first_attempt_passed=False)
    assert _stored(store, row.id).times_first_attempt_passed_after == 0


def test_auto_retire_fires_at_default_min_shown_not_before() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    for task_index in range(1, LESSONS_RETIRE_MIN_SHOWN):
        store.record_shown("ahawr-demo", task_index, [row.id])
        store.record_outcome("ahawr-demo", task_index, first_attempt_passed=False)
    assert _stored(store, row.id).status == "active"
    store.record_shown("ahawr-demo", LESSONS_RETIRE_MIN_SHOWN, [row.id])
    store.record_outcome("ahawr-demo", LESSONS_RETIRE_MIN_SHOWN, first_attempt_passed=False)
    assert _stored(store, row.id).status == "retired"


@pytest.mark.parametrize(("passes", "status"), [(5, "active"), (4, "retired")])
def test_auto_retire_pass_rate_is_inclusive_at_default_threshold(passes: int, status: str) -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    for task_index in range(1, LESSONS_RETIRE_MIN_SHOWN + 1):
        store.record_shown("ahawr-demo", task_index, [row.id])
        store.record_outcome("ahawr-demo", task_index, first_attempt_passed=task_index <= passes)
    assert _stored(store, row.id).status == status


def test_lessons_for_task_returns_the_lessons_shown_to_that_task() -> None:
    store = InMemoryLessonStore()
    first = store.add(_candidate())
    store.add(_candidate(situation="A second situation."))
    third = store.add(_candidate(situation="A third situation."))
    store.record_shown("ahawr-demo", 4, [third.id, first.id])
    assert [row.id for row in store.lessons_for_task("ahawr-demo", 4)] == [first.id, third.id]
    assert store.lessons_for_task("ahawr-demo", 5) == []
    assert store.lessons_for_task("ahawr-other", 4) == []


def test_delete_removes_lesson_from_task_view() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    store.record_shown("ahawr-demo", 4, [row.id])
    store.delete(row.id, confirm=True)
    assert store.lessons_for_task("ahawr-demo", 4) == []


def test_record_shown_unknown_lesson_is_key_error_without_counting() -> None:
    store = InMemoryLessonStore()
    row = store.add(_candidate())
    with pytest.raises(KeyError, match="lesson 7 not found"):
        store.record_shown("ahawr-demo", 4, [row.id, 7])
    assert _stored(store, row.id).times_shown == 0


def test_task_index_outside_1_to_999_is_refused() -> None:
    with pytest.raises(ValidationError):
        _candidate(task_index=0)
    with pytest.raises(ValidationError):
        _candidate(task_index=1000)


def test_unknown_kind_is_refused() -> None:
    with pytest.raises(ValidationError):
        _candidate(kind="escalated")


STAMP = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 27, 13, 0, tzinfo=UTC)
STAMP_TEXT = STAMP.isoformat()
LATER_TEXT = LATER.isoformat()


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeConnection:
    """Records every (sql, params); answers with the rows of the first marker found in the SQL."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._responses: dict[str, list[dict[str, Any]]] = {}

    def respond(self, marker: str, rows: list[dict[str, Any]]) -> None:
        self._responses[marker] = rows

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _FakeCursor:
        self.executed.append((sql, params))
        for marker, rows in self._responses.items():
            if marker in sql:
                return _FakeCursor(rows)
        return _FakeCursor([])

    def transaction(self) -> nullcontext[None]:
        return nullcontext()


def _db_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 1,
        "mission": "ahawr-demo",
        "task_id": "T004",
        "task_index": 4,
        "kind": "fixed-after-retry",
        "situation": "The first attempt edited the parser without running its test.",
        "lesson": "Run the parser test before reporting.",
        "evidence": {"failed_attempt": 1, "accepted_attempt": 2, "touched_paths": ["src/p.py"]},
        "created_at": STAMP,
        "status": "active",
        "times_shown": 0,
        "times_first_attempt_passed_after": 0,
        "times_seen": 1,
        "version": 1,
        "updated_at": STAMP,
    }
    row.update(overrides)
    return row


def test_pg_get_maps_row_and_timestamps_to_text() -> None:
    conn = _FakeConnection()
    conn.respond("SELECT", [_db_row()])
    lesson = PgLessonStore(conn).get(1)
    assert lesson is not None
    assert (lesson.id, lesson.created_at, lesson.updated_at) == (1, STAMP_TEXT, STAMP_TEXT)
    assert conn.executed[0][1] == (1,)


def test_pg_get_unknown_id_is_none() -> None:
    assert PgLessonStore(_FakeConnection()).get(99) is None


def test_pg_list_passes_mission_and_status_as_parameters() -> None:
    conn = _FakeConnection()
    conn.respond("FROM board.lessons", [_db_row()])
    rows = PgLessonStore(conn).list("ahawr-demo", status="active")
    sql, params = conn.executed[0]
    assert "WHERE mission = %s AND status = %s" in sql
    assert params == ("ahawr-demo", "active")
    assert [row.id for row in rows] == [1]


def test_pg_add_cuts_text_and_writes_evidence_as_jsonb() -> None:
    conn = _FakeConnection()
    conn.respond("INSERT INTO board.lessons", [_db_row()])
    PgLessonStore(conn).add(_candidate(situation="s" * (SITUATION_MAX_CHARS + 1)))
    sql, params = conn.executed[0]
    assert sql.startswith("INSERT INTO board.lessons (mission, task_id, task_index")
    assert "%s::jsonb" in sql
    assert len(params[4]) == SITUATION_MAX_CHARS
    assert params[4].endswith("...")


def test_pg_add_refuses_evidence_over_limit_without_sql() -> None:
    conn = _FakeConnection()
    with pytest.raises(ValueError, match="evidence"):
        PgLessonStore(conn).add(_candidate(evidence={"touched_paths": ["x" * EVIDENCE_MAX_CHARS]}))
    assert conn.executed == []


def test_pg_edit_with_current_version_updates_where_stamp_matches() -> None:
    conn = _FakeConnection()
    conn.respond("FOR UPDATE", [_db_row()])
    conn.respond(
        "UPDATE board.lessons SET situation",
        [_db_row(situation="New situation.", lesson="New lesson.", updated_at=LATER)],
    )
    edited = PgLessonStore(conn).edit_text(1, "New situation.", "New lesson.", STAMP_TEXT)
    assert (edited.situation, edited.lesson, edited.updated_at) == (
        "New situation.",
        "New lesson.",
        LATER_TEXT,
    )
    assert conn.executed[0][0].endswith("WHERE id = %s FOR UPDATE")
    sql, params = conn.executed[-1]
    assert sql.startswith("UPDATE board.lessons SET situation = %s, lesson = %s, updated_at")
    assert "WHERE id = %s AND updated_at = %s::timestamptz" in sql
    assert params == ("New situation.", "New lesson.", 1, STAMP_TEXT)


def test_pg_edit_with_stale_version_is_a_conflict_without_update() -> None:
    conn = _FakeConnection()
    conn.respond("FOR UPDATE", [_db_row(updated_at=LATER)])
    with pytest.raises(ConflictError):
        PgLessonStore(conn).edit_text(1, "Stale edit.", "Stale lesson.", STAMP_TEXT)
    assert not any(sql.startswith("UPDATE") for sql, _ in conn.executed)


def test_pg_edit_when_update_matches_no_row_is_a_conflict() -> None:
    conn = _FakeConnection()
    conn.respond("FOR UPDATE", [_db_row()])
    with pytest.raises(ConflictError, match="changed while it was being saved"):
        PgLessonStore(conn).edit_text(1, "s", "l", STAMP_TEXT)


def test_pg_edit_unknown_id_is_key_error() -> None:
    with pytest.raises(KeyError, match="lesson 7 not found"):
        PgLessonStore(_FakeConnection()).edit_text(7, "s", "l", None)


def test_pg_retire_writes_status_and_stamp_in_one_update() -> None:
    conn = _FakeConnection()
    conn.respond("FOR UPDATE", [_db_row()])
    conn.respond(
        "UPDATE board.lessons SET status", [_db_row(status="retired", updated_at=LATER)]
    )
    retired = PgLessonStore(conn).set_status(1, "retired", STAMP_TEXT)
    assert retired.status == "retired"
    sql, params = conn.executed[-1]
    assert sql.startswith("UPDATE board.lessons SET status = %s, updated_at = now()")
    assert "WHERE id = %s AND updated_at = %s::timestamptz" in sql
    assert params == ("retired", 1, STAMP_TEXT)


def test_pg_retire_with_stale_version_is_a_conflict_without_update() -> None:
    conn = _FakeConnection()
    conn.respond("FOR UPDATE", [_db_row(updated_at=LATER)])
    with pytest.raises(ConflictError):
        PgLessonStore(conn).set_status(1, "retired", STAMP_TEXT)
    assert not any(sql.startswith("UPDATE") for sql, _ in conn.executed)


def test_pg_delete_without_confirm_is_refused_without_sql() -> None:
    conn = _FakeConnection()
    with pytest.raises(GuardError, match="confirm=true"):
        PgLessonStore(conn).delete(1, confirm=False)
    assert conn.executed == []


def test_pg_record_shown_raises_counter_and_links_the_task() -> None:
    conn = _FakeConnection()
    conn.respond("SET times_shown = times_shown + 1", [_db_row(id=1), _db_row(id=2)])
    PgLessonStore(conn).record_shown("ahawr-demo", 4, [2, 1])
    counter_sql, counter_params = conn.executed[0]
    assert "SET times_shown = times_shown + 1 WHERE id = ANY(%s)" in counter_sql
    assert counter_params == ([2, 1],)
    link_sql, link_params = conn.executed[1]
    assert link_sql.startswith("INSERT INTO board.lesson_shown (lesson_id, mission, task_index)")
    assert link_params == ([2, 1], "ahawr-demo", 4)


def test_pg_record_shown_unknown_lesson_is_key_error() -> None:
    conn = _FakeConnection()
    conn.respond("SET times_shown = times_shown + 1", [_db_row(id=1)])
    with pytest.raises(KeyError, match="lesson 2 not found"):
        PgLessonStore(conn).record_shown("ahawr-demo", 4, [1, 2])
    assert not any(sql.startswith("INSERT") for sql, _ in conn.executed)


def test_pg_record_outcome_pass_increments_only_the_linked_lessons() -> None:
    conn = _FakeConnection()
    conn.respond("SET first_attempt_passed", [{"lesson_id": 1}])
    conn.respond(
        "times_first_attempt_passed_after + %s",
        [_db_row(times_shown=1, times_first_attempt_passed_after=1)],
    )
    PgLessonStore(conn).record_outcome("ahawr-demo", 4, first_attempt_passed=True)
    assert conn.executed[0][1] == (True, "ahawr-demo", 4)
    sql, params = conn.executed[1]
    assert "times_first_attempt_passed_after + %s WHERE id = ANY(%s)" in sql
    assert params == (1, [1])
    assert not any("SET status" in sql for sql, _ in conn.executed)


def test_pg_record_outcome_retires_a_lesson_that_is_due() -> None:
    conn = _FakeConnection()
    conn.respond("SET first_attempt_passed", [{"lesson_id": 1}])
    conn.respond("times_first_attempt_passed_after + %s", [_db_row(times_shown=10)])
    PgLessonStore(conn).record_outcome("ahawr-demo", 4, first_attempt_passed=False)
    sql, params = conn.executed[-1]
    assert sql.startswith("UPDATE board.lessons SET status = %s, updated_at = now()")
    assert params == ("retired", [1])


def test_pg_record_outcome_without_links_writes_nothing_else() -> None:
    conn = _FakeConnection()
    PgLessonStore(conn).record_outcome("ahawr-demo", 4, first_attempt_passed=True)
    assert len(conn.executed) == 1


def test_pg_lessons_for_task_selects_through_the_link_table() -> None:
    conn = _FakeConnection()
    conn.respond("FROM board.lessons", [_db_row()])
    rows = PgLessonStore(conn).lessons_for_task("ahawr-demo", 4)
    sql, params = conn.executed[0]
    assert "FROM board.lesson_shown WHERE mission = %s AND task_index = %s" in sql
    assert params == ("ahawr-demo", 4)
    assert [row.id for row in rows] == [1]


def test_pg_delete_with_confirm_deletes_by_id() -> None:
    conn = _FakeConnection()
    conn.respond("DELETE FROM board.lessons", [_db_row()])
    PgLessonStore(conn).delete(1, confirm=True)
    sql, params = conn.executed[0]
    assert sql.startswith("DELETE FROM board.lessons WHERE id = %s")
    assert params == (1,)


def test_pg_delete_unknown_id_is_key_error() -> None:
    with pytest.raises(KeyError, match="lesson 7 not found"):
        PgLessonStore(_FakeConnection()).delete(7, confirm=True)
