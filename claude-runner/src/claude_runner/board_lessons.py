"""Lessons library records and the InMemory store; field limits follow docs/lessons.md."""

from __future__ import annotations

import builtins
import json
import threading
from typing import Any, Literal

from pydantic import BaseModel, Field

from claude_runner.board import ConflictError, GuardError, now

MISSION_MAX_CHARS = 64
TASK_ID_MAX_CHARS = 16
SITUATION_MAX_CHARS = 400
LESSON_MAX_CHARS = 300
EVIDENCE_MAX_CHARS = 1200
TASK_INDEX_MAX = 999
ELLIPSIS = "..."
LESSONS_RETIRE_MIN_SHOWN = 10
LESSONS_RETIRE_MIN_PASS_RATE = 0.5

LessonKind = Literal["fixed-after-retry", "gave-up"]
LessonStatus = Literal["active", "retired"]


def _cut(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - len(ELLIPSIS)] + ELLIPSIS


def _check_version(lesson: Lesson, expected_version: str | None) -> None:
    if expected_version is not None and expected_version != lesson.updated_at:
        raise ConflictError(
            f"lesson {lesson.id}: expected version {expected_version}, got {lesson.updated_at}"
        )


def _check_evidence(evidence: dict[str, Any]) -> None:
    if len(json.dumps(evidence, ensure_ascii=False)) > EVIDENCE_MAX_CHARS:
        raise ValueError(f"evidence is over {EVIDENCE_MAX_CHARS} characters")


def _is_retire_due(lesson: Lesson, min_shown: int, min_pass_rate: float) -> bool:
    return (
        lesson.status == "active"
        and lesson.times_shown >= min_shown
        and lesson.times_first_attempt_passed_after < min_pass_rate * lesson.times_shown
    )


class LessonCandidate(BaseModel):
    """What the writer hands to the store: a lesson before it gets an id and counters."""

    mission: str
    task_id: str
    task_index: int = Field(ge=1, le=TASK_INDEX_MAX)
    kind: LessonKind
    situation: str
    lesson: str
    evidence: dict[str, Any]


class Lesson(LessonCandidate):
    """One row of the lessons library; ``updated_at`` is the optimistic-lock stamp."""

    id: int
    created_at: str
    status: LessonStatus
    times_shown: int
    times_first_attempt_passed_after: int
    times_seen: int
    version: int
    updated_at: str


class InMemoryLessonStore:
    """Thread-safe in-memory lessons library (tests and dry runs).

    Text over its limit is cut with ``...``; evidence over its limit is refused,
    because cutting JSON would make it invalid."""

    def __init__(
        self,
        retire_min_shown: int = LESSONS_RETIRE_MIN_SHOWN,
        retire_min_pass_rate: float = LESSONS_RETIRE_MIN_PASS_RATE,
    ) -> None:
        self._lock = threading.RLock()
        self._lessons: dict[int, Lesson] = {}
        self._last_id = 0
        self._retire_min_shown = retire_min_shown
        self._retire_min_pass_rate = retire_min_pass_rate
        self._shown: dict[tuple[str, int], dict[int, bool | None]] = {}

    def _row(self, lesson_id: int) -> Lesson:
        lesson = self._lessons.get(lesson_id)
        if lesson is None:
            raise KeyError(f"lesson {lesson_id} not found")
        return lesson

    def list(self, mission: str, status: LessonStatus | None = None) -> builtins.list[Lesson]:
        with self._lock:
            return [
                lesson.model_copy()
                for lesson in self._lessons.values()
                if lesson.mission == mission and status in (None, lesson.status)
            ]

    def get(self, lesson_id: int) -> Lesson | None:
        with self._lock:
            lesson = self._lessons.get(lesson_id)
            return None if lesson is None else lesson.model_copy()

    def add(self, candidate: LessonCandidate) -> Lesson:
        _check_evidence(candidate.evidence)
        with self._lock:
            self._last_id += 1
            stamp = now()
            lesson = Lesson(
                id=self._last_id,
                mission=_cut(candidate.mission, MISSION_MAX_CHARS),
                task_id=_cut(candidate.task_id, TASK_ID_MAX_CHARS),
                task_index=candidate.task_index,
                kind=candidate.kind,
                situation=_cut(candidate.situation, SITUATION_MAX_CHARS),
                lesson=_cut(candidate.lesson, LESSON_MAX_CHARS),
                evidence=candidate.evidence,
                created_at=stamp,
                status="active",
                times_shown=0,
                times_first_attempt_passed_after=0,
                times_seen=1,
                version=1,
                updated_at=stamp,
            )
            self._lessons[lesson.id] = lesson
            return lesson.model_copy()

    def edit_text(
        self, lesson_id: int, situation: str, lesson: str, expected_version: str | None
    ) -> Lesson:
        with self._lock:
            row = self._row(lesson_id)
            _check_version(row, expected_version)
            edited = row.model_copy(
                update={
                    "situation": _cut(situation, SITUATION_MAX_CHARS),
                    "lesson": _cut(lesson, LESSON_MAX_CHARS),
                    "updated_at": now(),
                }
            )
            self._lessons[lesson_id] = edited
            return edited.model_copy()

    def set_status(
        self, lesson_id: int, status: LessonStatus, expected_version: str | None
    ) -> Lesson:
        with self._lock:
            row = self._row(lesson_id)
            _check_version(row, expected_version)
            changed = row.model_copy(update={"status": status, "updated_at": now()})
            self._lessons[lesson_id] = changed
            return changed.model_copy()

    def delete(self, lesson_id: int, confirm: bool) -> None:
        with self._lock:
            if not confirm:
                raise GuardError(f"delete lesson {lesson_id}: pass confirm=true")
            self._row(lesson_id)
            del self._lessons[lesson_id]
            for links in self._shown.values():
                links.pop(lesson_id, None)

    def bump_duplicate(self, lesson_id: int) -> Lesson:
        with self._lock:
            row = self._row(lesson_id)
            bumped = row.model_copy(update={"times_seen": row.times_seen + 1})
            self._lessons[lesson_id] = bumped
            return bumped.model_copy()

    def record_shown(
        self, mission: str, task_index: int, lesson_ids: builtins.list[int]
    ) -> None:
        with self._lock:
            rows = [self._row(lesson_id) for lesson_id in dict.fromkeys(lesson_ids)]
            links = self._shown.setdefault((mission, task_index), {})
            for row in rows:
                self._lessons[row.id] = row.model_copy(
                    update={"times_shown": row.times_shown + 1}
                )
                links.setdefault(row.id, None)

    def record_outcome(self, mission: str, task_index: int, first_attempt_passed: bool) -> None:
        with self._lock:
            links = self._shown.get((mission, task_index), {})
            pending = [lesson_id for lesson_id, passed in links.items() if passed is None]
            for lesson_id in pending:
                links[lesson_id] = first_attempt_passed
                row = self._lessons[lesson_id]
                counted = row.model_copy(
                    update={
                        "times_first_attempt_passed_after": row.times_first_attempt_passed_after
                        + int(first_attempt_passed)
                    }
                )
                if _is_retire_due(counted, self._retire_min_shown, self._retire_min_pass_rate):
                    counted = counted.model_copy(update={"status": "retired", "updated_at": now()})
                self._lessons[lesson_id] = counted

    def lessons_for_task(self, mission: str, task_index: int) -> builtins.list[Lesson]:
        with self._lock:
            links = self._shown.get((mission, task_index), {})
            return [self._lessons[lesson_id].model_copy() for lesson_id in sorted(links)]


_LESSON_TABLE = "board.lessons"
_SHOWN_TABLE = "board.lesson_shown"
_LESSON_COLUMNS = (
    "id, mission, task_id, task_index, kind, situation, lesson, evidence, created_at, status, "
    "times_shown, times_first_attempt_passed_after, times_seen, version, updated_at"
)


def _lesson_from_row(row: dict[str, Any]) -> Lesson:
    return Lesson(
        **{
            **row,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }
    )


def _require(lesson: Lesson | None, lesson_id: int) -> Lesson:
    if lesson is None:
        raise KeyError(f"lesson {lesson_id} not found")
    return lesson


class PgLessonStore:
    """PostgreSQL lessons library on board.lessons; same methods and errors as InMemory."""

    def __init__(
        self,
        conn: Any,
        retire_min_shown: int = LESSONS_RETIRE_MIN_SHOWN,
        retire_min_pass_rate: float = LESSONS_RETIRE_MIN_PASS_RATE,
    ) -> None:
        self._lock = threading.RLock()
        self._conn = conn
        self._retire_min_shown = retire_min_shown
        self._retire_min_pass_rate = retire_min_pass_rate

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> Lesson | None:
        row = self._conn.execute(sql, params).fetchone()
        return None if row is None else _lesson_from_row(row)

    def _locked(self, lesson_id: int) -> Lesson:
        return _require(
            self._fetch(
                f"SELECT {_LESSON_COLUMNS} FROM {_LESSON_TABLE} WHERE id = %s FOR UPDATE",
                (lesson_id,),
            ),
            lesson_id,
        )

    def _update(self, lesson: Lesson, sets: str, params: tuple[Any, ...]) -> Lesson:
        updated = self._fetch(
            f"UPDATE {_LESSON_TABLE} SET {sets}, updated_at = now() "
            f"WHERE id = %s AND updated_at = %s::timestamptz RETURNING {_LESSON_COLUMNS}",
            (*params, lesson.id, lesson.updated_at),
        )
        if updated is None:
            raise ConflictError(f"lesson {lesson.id}: changed while it was being saved")
        return updated

    def list(self, mission: str, status: LessonStatus | None = None) -> builtins.list[Lesson]:
        sql = f"SELECT {_LESSON_COLUMNS} FROM {_LESSON_TABLE} WHERE mission = %s"
        params: tuple[Any, ...] = (mission,)
        if status is not None:
            sql += " AND status = %s"
            params = (mission, status)
        with self._lock:
            rows = self._conn.execute(f"{sql} ORDER BY id", params).fetchall()
        return [_lesson_from_row(row) for row in rows]

    def get(self, lesson_id: int) -> Lesson | None:
        with self._lock:
            return self._fetch(
                f"SELECT {_LESSON_COLUMNS} FROM {_LESSON_TABLE} WHERE id = %s", (lesson_id,)
            )

    def add(self, candidate: LessonCandidate) -> Lesson:
        _check_evidence(candidate.evidence)
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                f"INSERT INTO {_LESSON_TABLE} "
                "(mission, task_id, task_index, kind, situation, lesson, evidence) "
                f"VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) RETURNING {_LESSON_COLUMNS}",
                (
                    _cut(candidate.mission, MISSION_MAX_CHARS),
                    _cut(candidate.task_id, TASK_ID_MAX_CHARS),
                    candidate.task_index,
                    candidate.kind,
                    _cut(candidate.situation, SITUATION_MAX_CHARS),
                    _cut(candidate.lesson, LESSON_MAX_CHARS),
                    json.dumps(candidate.evidence, ensure_ascii=False),
                ),
            ).fetchone()
        return _lesson_from_row(row)

    def edit_text(
        self, lesson_id: int, situation: str, lesson: str, expected_version: str | None
    ) -> Lesson:
        with self._lock, self._conn.transaction():
            row = self._locked(lesson_id)
            _check_version(row, expected_version)
            return self._update(
                row,
                "situation = %s, lesson = %s",
                (_cut(situation, SITUATION_MAX_CHARS), _cut(lesson, LESSON_MAX_CHARS)),
            )

    def set_status(
        self, lesson_id: int, status: LessonStatus, expected_version: str | None
    ) -> Lesson:
        with self._lock, self._conn.transaction():
            row = self._locked(lesson_id)
            _check_version(row, expected_version)
            return self._update(row, "status = %s", (status,))

    def delete(self, lesson_id: int, confirm: bool) -> None:
        if not confirm:
            raise GuardError(f"delete lesson {lesson_id}: pass confirm=true")
        with self._lock, self._conn.transaction():
            _require(
                self._fetch(
                    f"DELETE FROM {_LESSON_TABLE} WHERE id = %s RETURNING {_LESSON_COLUMNS}",
                    (lesson_id,),
                ),
                lesson_id,
            )

    def bump_duplicate(self, lesson_id: int) -> Lesson:
        with self._lock, self._conn.transaction():
            return _require(
                self._fetch(
                    f"UPDATE {_LESSON_TABLE} SET times_seen = times_seen + 1 "
                    f"WHERE id = %s RETURNING {_LESSON_COLUMNS}",
                    (lesson_id,),
                ),
                lesson_id,
            )

    def record_shown(
        self, mission: str, task_index: int, lesson_ids: builtins.list[int]
    ) -> None:
        ids = list(dict.fromkeys(lesson_ids))
        if not ids:
            return
        with self._lock, self._conn.transaction():
            rows = self._conn.execute(
                f"UPDATE {_LESSON_TABLE} SET times_shown = times_shown + 1 "
                f"WHERE id = ANY(%s) RETURNING {_LESSON_COLUMNS}",
                (ids,),
            ).fetchall()
            found = {row["id"] for row in rows}
            missing = [lesson_id for lesson_id in ids if lesson_id not in found]
            if missing:
                raise KeyError(f"lesson {missing[0]} not found")
            self._conn.execute(
                f"INSERT INTO {_SHOWN_TABLE} (lesson_id, mission, task_index) "
                "SELECT unnest(%s::bigint[]), %s, %s ON CONFLICT DO NOTHING",
                (ids, mission, task_index),
            )

    def record_outcome(self, mission: str, task_index: int, first_attempt_passed: bool) -> None:
        with self._lock, self._conn.transaction():
            linked = self._conn.execute(
                f"UPDATE {_SHOWN_TABLE} SET first_attempt_passed = %s "
                "WHERE mission = %s AND task_index = %s AND first_attempt_passed IS NULL "
                "RETURNING lesson_id",
                (first_attempt_passed, mission, task_index),
            ).fetchall()
            ids = [row["lesson_id"] for row in linked]
            if not ids:
                return
            counted = self._conn.execute(
                f"UPDATE {_LESSON_TABLE} SET "
                "times_first_attempt_passed_after = times_first_attempt_passed_after + %s "
                f"WHERE id = ANY(%s) RETURNING {_LESSON_COLUMNS}",
                (int(first_attempt_passed), ids),
            ).fetchall()
            self._retire_due_rows([_lesson_from_row(row) for row in counted])

    def _retire_due_rows(self, lessons: builtins.list[Lesson]) -> None:
        due = [
            lesson.id
            for lesson in lessons
            if _is_retire_due(lesson, self._retire_min_shown, self._retire_min_pass_rate)
        ]
        if due:
            self._conn.execute(
                f"UPDATE {_LESSON_TABLE} SET status = %s, updated_at = now() WHERE id = ANY(%s)",
                ("retired", due),
            )

    def lessons_for_task(self, mission: str, task_index: int) -> builtins.list[Lesson]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_LESSON_COLUMNS} FROM {_LESSON_TABLE} WHERE id IN "
                f"(SELECT lesson_id FROM {_SHOWN_TABLE} WHERE mission = %s AND task_index = %s) "
                "ORDER BY id",
                (mission, task_index),
            ).fetchall()
        return [_lesson_from_row(row) for row in rows]
