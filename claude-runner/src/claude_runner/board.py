"""AHAWR mission board: data model, store interface, in-memory implementation.

The board backs the dashboard's mission tab.  It reads the four n8n data
tables (missions, Live State, Event log, hermes_config) plus the board's own
tables (queue, mission_config, task_overrides, templates, flags, preflight,
changelog) — see ``docs/board-data-model.md`` and ``docs/board-schema.sql``.

Optimistic locking: every write takes an ``expected_version`` (the
``updatedAt`` of the row as read by the caller, ISO-8601).  If the stored
version differs, the write is refused with :class:`ConflictError` (mapped to
HTTP 409, "план изменился, обновите").  Every write first saves the full
previous row to the changelog (``before_json``).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

__all__ = [
    "BoardStore",
    "ChangelogEntry",
    "ConflictError",
    "GuardError",
    "InMemoryBoardStore",
    "LiveState",
    "Mission",
    "PgBoardStore",
    "QueueEntry",
    "TaskStatuses",
    "TaskView",
    "UnavailableBoardStore",
    "get_board_store",
]


def now() -> str:
    # microseconds: successive writes in the same second must not stamp the
    # same version, or a version drift (a write landing after the read) is
    # invisible to the optimistic lock and 409s go missing
    return datetime.now(UTC).isoformat(timespec="microseconds")


class ConflictError(RuntimeError):
    """The row changed since it was read: the expected version does not match.

    Mapped to HTTP 409 with the message "план изменился, обновите"."""

    code = "conflict"
    status_code = 409

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail or "план изменился, обновите")


class GuardError(RuntimeError):
    """The board refuses an action while a mission is running (HTTP 409)."""

    code = "guard"
    status_code = 409


class Mission(BaseModel):
    """One row of the ``missions`` table: the mission plan and its settings."""

    mission_id: str
    state_namespace: str
    name: str = ""
    objective: str = ""
    working_directory: str | None = None
    version: float | None = None
    enabled: bool = True
    rules: list[Any] = []
    acceptance_criteria: list[Any] = []
    retrieval_corpora: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class LiveState(BaseModel):
    """One row of the Live State table: the current execution state of a mission."""

    state_key: str
    worker_session_id: str | None = None
    plan: dict[str, Any] | None = None
    task_index: float | None = None
    task_attempt: float | None = None
    status: str | None = None
    review_feedback: str | None = None
    last_review_score: float | None = None
    last_review_reason: str | None = None
    updated_at: str | None = None
    reviewer_session_id: str | None = None
    reviewer_run_id: str | None = None
    reviewer_status: str | None = None
    worker_run_id: str | None = None
    worker_status: str | None = None
    architect_run_id: str | None = None
    architect_status: str | None = None
    architect_session_id: str | None = None


class TaskView(BaseModel):
    """Per-task status derived from the Live State row and the event log.

    ``status`` is one of: ``waiting``, ``running``, ``review_pending``,
    ``accepted``, ``not_accepted``, ``completed``, ``failed``."""

    task_id: str
    task_index: int
    title: str = ""
    objective: str = ""
    status: str
    attempt: int | None = None
    review_score: float | None = None
    review_reason: str | None = None


class QueueEntry(BaseModel):
    """One row of the board queue: a mission at a position in the run list.

    ``enabled`` = the mission participates in the queue;
    ``attempts_per_task`` caps the attempts for the mission's tasks;
    ``retry_failed`` = failed missions are retried even outside the default
    retry list."""

    position: int
    mission_id: str
    enabled: bool = True
    attempts_per_task: float | None = None
    retry_failed: bool = False
    note: str | None = None


class ChangelogEntry(BaseModel):
    """Audit row of the board changelog.

    ``before_json`` is the full previous row (the row as it was before the
    change; the whole queue list for queue actions); ``after_json`` the row
    after the change.  Both are ``None`` when the action does not carry a
    row value (a flag set, an appended event)."""

    id: int
    at: str
    who: str | None = None
    action: str
    mission_id: str | None = None
    task_key: str | None = None
    before_json: dict[str, Any] | list[Any] | None = None
    after_json: dict[str, Any] | list[Any] | None = None


class BoardStore(ABC):
    """Read/write interface over the board data.

    Every read returns a copy.  Every write takes an ``expected_version``
    (ISO-8601 ``updatedAt`` of the row as read by the caller); ``None``
    disables the check.  A mismatch raises :class:`ConflictError`.  Every
    write saves the full previous row to the changelog first.
    """

    # ---- reads ---------------------------------------------------------------
    @abstractmethod
    def list_missions(self) -> list[Mission]:
        """All missions, ordered by ``mission_id``."""

    @abstractmethod
    def get_mission(self, mission_id: str) -> Mission | None:
        """One mission by its stable id."""

    @abstractmethod
    def list_live_states(self) -> list[LiveState]:
        """All Live State rows, ordered by ``state_key``."""

    @abstractmethod
    def get_live_state(self, state_key: str) -> LiveState | None:
        """One Live State row by its key (the mission's ``state_namespace``)."""

    @abstractmethod
    def events(self, state_key: str) -> list[dict[str, Any]]:
        """Event log rows of one mission (the state key), oldest first."""

    @abstractmethod
    def hermes_config(self) -> dict[str, Any]:
        """The hermes_config row (the ``claude-code`` profile) as a dict."""

    @abstractmethod
    def queue(self) -> list[QueueEntry]:
        """Queue entries, ordered by ``position``."""

    @abstractmethod
    def mission_config(self, mission_id: str) -> dict[str, Any] | None:
        """Per-mission config overrides (JSON object), or ``None``."""

    @abstractmethod
    def task_overrides(self, mission_id: str) -> list[dict[str, Any]]:
        """Per-task overrides of one mission, ordered by ``task_key``."""

    @abstractmethod
    def templates(self) -> list[dict[str, Any]]:
        """Mission templates, ordered by ``name``."""

    @abstractmethod
    def flags(self) -> dict[str, str]:
        """Board flags: key to value."""

    @abstractmethod
    def preflight(self, mission_id: str) -> list[dict[str, Any]]:
        """Preflight records of one mission, newest first."""

    @abstractmethod
    def changelog(self, limit: int = 200) -> list[ChangelogEntry]:
        """Changelog rows, newest first, at most ``limit``."""

    # ---- derived views ------------------------------------------------------
    def running_missions(self) -> list[Mission]:
        """Missions whose Live State ``status`` is ``active``."""
        return [
            mission
            for mission in self.list_missions()
            if (live := self.get_live_state(mission.state_namespace)) is not None
            and live.status == "active"
        ]

    def mission_groups(self) -> dict[str, list[str]]:
        """Mission ids by group: ``running`` / ``queued`` / ``completed`` /
        ``not_completed`` / ``disabled``.

        A disabled mission is ``disabled`` even if its state says otherwise;
        an enabled one is ``running`` (Live State ``active``), ``completed``
        (``completed``), ``not_completed`` (``failed`` or ``stopped``), else
        ``queued`` (never started or no Live State row)."""
        groups: dict[str, list[str]] = {
            "running": [],
            "queued": [],
            "completed": [],
            "not_completed": [],
            "disabled": [],
        }
        for mission in self.list_missions():
            if not mission.enabled:
                groups["disabled"].append(mission.mission_id)
                continue
            live = self.get_live_state(mission.state_namespace)
            status = live.status if live is not None else None
            if status == "active":
                groups["running"].append(mission.mission_id)
            elif status == "completed":
                groups["completed"].append(mission.mission_id)
            elif status in ("failed", "stopped"):
                groups["not_completed"].append(mission.mission_id)
            else:
                groups["queued"].append(mission.mission_id)
        return groups

    def task_views(self, state_key: str) -> list[TaskView]:
        """Per-task statuses derived from the Live State row and the event log
        (the derivation documented in ``docs/board-data-model.md``)."""
        live = self.get_live_state(state_key)
        if live is None or not live.plan:
            return []
        tasks = live.plan.get("tasks") or []
        current_index = int(live.task_index) if live.task_index is not None else 0
        events = self.events(state_key)
        views: list[TaskView] = []
        for index, task in enumerate(tasks):
            task_id = str(task.get("id") or f"T{index + 1:03d}")
            views.append(
                TaskView(
                    task_id=task_id,
                    task_index=index,
                    title=str(task.get("title") or ""),
                    objective=str(task.get("objective") or ""),
                    status=_derive_status(live, index, current_index, events, task_id),
                    attempt=int(live.task_attempt)
                    if live.task_attempt is not None and index == current_index
                    else None,
                    review_score=_latest_review(events, task_id)[0],
                    review_reason=_latest_review(events, task_id)[1],
                )
            )
        return views

    # ---- writes (each takes an expected_version; a mismatch is ConflictError)
    @abstractmethod
    def update_mission(
        self, mission_id: str, expected_version: str | None, **fields: Any
    ) -> Mission:
        """Change fields of a mission; ``mission_id`` and ``state_namespace`` are
        fixed.  Raises :class:`ConflictError` if the stored version differs."""

    @abstractmethod
    def upsert_live_state(
        self, state_key: str, expected_version: str | None, **fields: Any
    ) -> LiveState:
        """Update (or create) the Live State row of a state key.  ``state_key``
        is fixed.  Raises :class:`ConflictError` if the stored version differs."""

    @abstractmethod
    def append_event(self, state_key: str, event: dict[str, Any]) -> None:
        """Append an event log row (no version check: the log is append-only)."""

    @abstractmethod
    def update_hermes_config(
        self, expected_version: str | None, **fields: Any
    ) -> dict[str, Any]:
        """Change fields of the hermes_config row; ``profile_id`` is fixed."""

    @abstractmethod
    def set_queue(
        self, entries: list[QueueEntry], expected_version: str | None
    ) -> list[QueueEntry]:
        """Replace the whole queue.  Positions are renumbered contiguously
        (0..n-1) in the given order; duplicate mission ids are refused."""

    @abstractmethod
    def add_queue(
        self,
        mission_id: str,
        expected_version: str | None,
        position: int | None = None,
        enabled: bool = True,
        attempts_per_task: float | None = None,
        retry_failed: bool = False,
        note: str | None = None,
    ) -> list[QueueEntry]:
        """Add a mission to the queue (at ``position`` or at the end)."""

    @abstractmethod
    def remove_queue(
        self, mission_id: str, expected_version: str | None, confirmed: bool = False
    ) -> list[QueueEntry]:
        """Remove a mission from the queue; positions stay contiguous.

        Removing a mission that is running is refused unless ``confirmed``
        (the API's confirm gate is enforced at the store as well)."""

    @abstractmethod
    def move_queue(
        self, mission_id: str, to_position: int, expected_version: str | None
    ) -> list[QueueEntry]:
        """Move a queued mission to ``to_position``; positions stay contiguous."""

    @abstractmethod
    def create_mission_rows(
        self,
        mission: Mission,
        live: LiveState,
        config: dict[str, Any] | None,
        position: int | None = None,
        note: str | None = None,
        who: str = "",
    ) -> None:
        """Create a mission: the missions row, its Live State row, its queue
        entry (at ``position``) and its config row — one atomic write: all
        rows or none.  The audit keeps the four rows together in one change."""

    @abstractmethod
    def set_mission_config(
        self,
        mission_id: str,
        config: dict[str, Any],
        expected_version: str | None,
        who: str = "",
    ) -> None:
        """Set (or replace) the per-mission config overrides."""

    @abstractmethod
    def set_task_override(
        self,
        mission_id: str,
        task_key: str,
        hint: str | None,
        extra: dict[str, Any] | None,
        expected_version: str | None,
        who: str = "",
    ) -> None:
        """Set (or replace) the per-task override of one mission task."""

    @abstractmethod
    def remove_task_override(
        self, mission_id: str, task_key: str, expected_version: str | None
    ) -> None:
        """Remove the per-task override of one mission task."""

    @abstractmethod
    def set_template(
        self,
        name: str,
        config: dict[str, Any],
        source_mission_id: str | None = None,
        expected_version: str | None = None,
    ) -> None:
        """Add (or replace) a mission template."""

    @abstractmethod
    def remove_template(self, name: str, expected_version: str | None) -> None:
        """Remove a mission template."""

    @abstractmethod
    def set_flag(self, key: str, value: str, expected_version: str | None) -> None:
        """Set (or replace) a board flag."""

    @abstractmethod
    def remove_flag(self, key: str, expected_version: str | None) -> None:
        """Remove a board flag."""

    @abstractmethod
    def record_preflight(self, mission_id: str, items: list[dict[str, Any]]) -> None:
        """Record a preflight result for a mission (newest first)."""

    @abstractmethod
    def get_project_rules(self) -> tuple[list[dict[str, Any]], str]:
        """Project rules ordered by position, and their version ('' when empty)."""

    @abstractmethod
    def put_project_rules(
        self, rules: list[dict[str, Any]], expected_version: str | None, actor: str = "",
    ) -> None:
        """Replace the whole project rule list."""

    @abstractmethod
    def list_i18n(self) -> dict[str, Any]:
        """``{"languages": [...], "strings": {lang: {key: text}}}``."""

    @abstractmethod
    def put_i18n_string(self, lang: str, key: str, text: str, actor: str = "") -> None:
        """Set a UI string; an empty text deletes it."""

    @abstractmethod
    def add_i18n_language(self, code: str, name: str) -> None:
        """Add an enabled UI language."""

    @abstractmethod
    def i18n_is_empty(self) -> bool:
        """True when no UI language and no UI string is stored."""

    @abstractmethod
    def seed_i18n(self, sets: dict[str, Any]) -> None:
        """Insert the seed (same shape as list_i18n) only when the i18n tables are empty."""

    # ---- audit ---------------------------------------------------------------
    def audit_rollback(
        self,
        row_kind: str,
        key: str,
        before: Any,
        after: Any,
        who: str = "",
    ) -> None:
        """Save a changelog row for a rollback (its ``action`` is ``rollback``).

        ``before`` is the state the rollback reverts from (the row the change
        being reverted had left); ``after`` the fields restored.  Written after
        the write that applies the restored value, so it is the newest changelog
        row; the Pg store writes it inside the same transaction."""


def _latest_review(events: list[dict[str, Any]], task_id: str) -> tuple[float | None, str | None]:
    """The latest ``review_parsed`` row of a task: (score, reason)."""
    for event in reversed(events):
        if event.get("task_id") == task_id and event.get("event_type") == "review_parsed":
            score = event.get("review_score")
            return (
                float(score) if isinstance(score, int | float) else None,
                event.get("review_reason"),
            )
    return (None, None)


def _latest_review_status(events: list[dict[str, Any]], task_id: str) -> str | None:
    """The ``review_status`` of the latest ``review_parsed`` row: ``pass`` /
    ``needs_changes``, or ``None`` when the task has no review row."""
    for event in reversed(events):
        if event.get("task_id") == task_id and event.get("event_type") == "review_parsed":
            return event.get("review_status")
    return None


def _derive_status(
    live: LiveState,
    index: int,
    current_index: int,
    events: list[dict[str, Any]],
    task_id: str,
) -> str:
    """Per-task status from the Live State row and the event log (the order is
    the derivation in ``docs/board-data-model.md``)."""
    task_events = [event for event in events if event.get("task_id") == task_id]
    latest = task_events[-1] if task_events else None
    latest_type = latest.get("event_type") if latest is not None else None
    # the verdict is the review_status ("pass" / "needs_changes"); review_reason is the Reviewer's free text
    verdict = _latest_review_status(events, task_id)
    if index > current_index:  # ahead of the current task
        return "waiting"
    if live.status == "completed":  # all tasks done
        return "accepted" if verdict == "pass" else "completed"
    if live.status == "failed" and index == current_index:  # failure on the current task
        return "failed"
    if index < current_index:  # the workflow already advanced past it
        return "accepted" if verdict == "pass" else "not_accepted"
    # the current task, Live State status 'active'
    if verdict == "needs_changes":  # the review rejected the report
        return "not_accepted"
    if latest_type == "worker_terminal":  # the worker run ended in error
        return "running"
    if latest_type == "worker_completed":  # worker done, review pending
        return "review_pending"
    return "running"


class TaskStatuses:
    """Pure derivation of a task's status from its event rows.

    Used by the server-side plan guards (``board_plan.py``): the browser's
    status is never trusted — the status is recomputed from the event log
    the board already holds.  ``derive`` takes the task (its ``id``) and the
    event rows for that task plus the live ``task_index`` / ``task_attempt``
    and the mission status, and returns one of: ``waiting``, ``running``,
    ``review_pending``, ``not_accepted``, ``accepted``, ``failed``."""

    @staticmethod
    def derive(
        task: dict[str, Any],
        live_status: str | None,
        live_task_index: float | None,
        live_task_attempt: float | None,
        task_index: int,
        task_id: str | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> str:
        task_id = task_id or str(task.get("id") or "")
        current_index = int(live_task_index) if live_task_index is not None else 0
        events = events or []
        task_events = [e for e in events if e.get("task_id") == task_id]
        latest = task_events[-1] if task_events else None
        latest_type = latest.get("event_type") if latest is not None else None
        if task_index > current_index:
            return "waiting"
        if live_status == "completed":
            status = _latest_review_status(events, task_id)
            return "accepted" if status == "pass" else "completed"
        if live_status == "failed" and task_index == current_index:
            return "failed"
        if task_index < current_index:
            status = _latest_review_status(events, task_id)
            return "accepted" if status == "pass" else "not_accepted"
        # current task, live status 'active'
        status = _latest_review_status(events, task_id)
        if status == "needs_changes":
            return "not_accepted"
        if latest_type == "worker_terminal":
            return "running"
        if latest_type == "worker_completed":
            return "review_pending"
        return "running"


class InMemoryBoardStore(BoardStore):
    """Thread-safe in-memory store for the board (tests and dry runs).

    Versions are ISO-8601 ``updatedAt`` stamps; every write first saves the
    full previous row to the changelog, then applies the change and stamps the
    new version."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._missions: dict[str, Mission] = {}
        self._live: dict[str, LiveState] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._hermes: dict[str, Any] = {}
        self._hermes_version = ""
        self._queue: dict[str, QueueEntry] = {}  # mission_id -> entry
        self._queue_order: list[str] = []
        self._queue_version = ""
        self._mission_config: dict[str, dict[str, Any]] = {}
        self._mission_config_version: dict[str, str] = {}
        self._task_overrides: dict[tuple[str, str], dict[str, Any]] = {}
        self._task_override_version: dict[tuple[str, str], str] = {}
        self._templates: dict[str, dict[str, Any]] = {}
        self._template_version: dict[str, str] = {}
        self._flags: dict[str, str] = {}
        self._flag_version: dict[str, str] = {}
        self._project_rules: list[dict[str, Any]] = []
        self._project_rules_version = ""
        self._i18n_languages: dict[str, dict[str, Any]] = {}
        self._i18n_strings: dict[tuple[str, str], dict[str, Any]] = {}
        self._preflight: dict[str, list[dict[str, Any]]] = {}
        self._changelog: list[ChangelogEntry] = []
        self._changelog_seq = 0

    # ---- internal helpers ------------------------------------------------------
    def _check_version(self, kind: str, key: str, expected: str | None, stored: str) -> None:
        if expected is not None and expected != stored:
            raise ConflictError(
                f"{kind} {key!r}: expected version {expected}, got {stored}"
            )

    def _audit(
        self,
        action: str,
        before: dict[str, Any] | list[Any] | None,
        after: dict[str, Any] | list[Any] | None,
        mission_id: str | None = None,
        task_key: str | None = None,
        who: str = "",
    ) -> None:
        """Save the full previous row to the changelog before the change lands."""
        self._changelog_seq += 1
        self._changelog.append(
            ChangelogEntry(
                id=self._changelog_seq,
                at=now(),
                who=who or None,
                action=action,
                mission_id=mission_id,
                task_key=task_key,
                before_json=before,
                after_json=after,
            )
        )

    def _guard_running(self, mission_ids: list[str]) -> None:
        """Refuse queue changes that touch a mission that is running."""
        running = {m.mission_id for m in self.running_missions()}
        for mission_id in mission_ids:
            if mission_id in running:
                raise GuardError(f"mission {mission_id!r} is running")

    # ---- reads -------------------------------------------------------------------
    def list_missions(self) -> list[Mission]:
        with self._lock:
            return [self._missions[k] for k in sorted(self._missions)]

    def get_mission(self, mission_id: str) -> Mission | None:
        with self._lock:
            return self._missions.get(mission_id)

    def list_live_states(self) -> list[LiveState]:
        with self._lock:
            return [self._live[k] for k in sorted(self._live)]

    def get_live_state(self, state_key: str) -> LiveState | None:
        with self._lock:
            return self._live.get(state_key)

    def events(self, state_key: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._events.get(state_key, [])]

    def hermes_config(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._hermes)

    def queue(self) -> list[QueueEntry]:
        with self._lock:
            return [self._queue[m] for m in self._queue_order]

    def mission_config(self, mission_id: str) -> dict[str, Any] | None:
        with self._lock:
            if mission_id in self._mission_config:
                return dict(self._mission_config[mission_id]["config_json"])
            return None

    def task_overrides(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(v)
                for (mid, _), v in sorted(self._task_overrides.items())
                if mid == mission_id
            ]

    def templates(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(v) for k, v in sorted(self._templates.items())]

    def flags(self) -> dict[str, str]:
        with self._lock:
            return dict(self._flags)

    def preflight(self, mission_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._preflight.get(mission_id, [])]

    def changelog(self, limit: int = 200) -> list[ChangelogEntry]:
        with self._lock:
            return list(reversed(self._changelog[-limit:]))

    def audit_rollback(
        self,
        row_kind: str,
        key: str,
        before: Any,
        after: Any,
        who: str = "",
    ) -> None:
        """The rollback's own changelog row (before the restored value lands)."""
        with self._lock:
            before_json = before.model_dump() if hasattr(before, "model_dump") else before
            self._audit(
                "rollback",
                before=before_json,
                after=after,
                mission_id=key,
                who=who,
            )

    # ---- writes -------------------------------------------------------------------
    def update_mission(
        self, mission_id: str, expected_version: str | None, **fields: Any
    ) -> Mission:
        with self._lock:
            mission = self._missions.get(mission_id)
            if mission is None:
                raise KeyError(f"mission {mission_id!r} not found")
            self._check_version("mission", mission_id, expected_version, mission.updated_at or "")
            before = mission.model_dump()
            data = mission.model_dump()
            fixed = ("mission_id", "state_namespace")
            for name, value in fields.items():
                if name not in fixed:
                    data[name] = value
            data["updated_at"] = now()
            updated = Mission(**data)
            self._audit(
                "mission update", before=before, after=updated.model_dump(), mission_id=mission_id
            )
            self._missions[mission_id] = updated
            return updated

    def upsert_live_state(
        self, state_key: str, expected_version: str | None, **fields: Any
    ) -> LiveState:
        with self._lock:
            live = self._live.get(state_key)
            if live is not None:
                self._check_version(
                    "live state", state_key, expected_version, live.updated_at or ""
                )
            before = live.model_dump() if live is not None else {"state_key": state_key}
            data = dict(before)
            data["state_key"] = state_key
            for name, value in fields.items():
                if name != "state_key":
                    data[name] = value
            data["updated_at"] = now()
            updated = LiveState(**data)
            self._audit("live state upsert", before=before, after=updated.model_dump())
            self._live[state_key] = updated
            return updated

    def append_event(self, state_key: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._audit(
                "event append", before=None, after=dict(event), mission_id=state_key
            )
            self._events.setdefault(state_key, []).append(dict(event))

    def update_hermes_config(self, expected_version: str | None, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._check_version(
                "hermes_config", "hermes_config", expected_version, self._hermes_version
            )
            before = dict(self._hermes)
            data = dict(self._hermes)
            for name, value in fields.items():
                if name != "profile_id":
                    data[name] = value
            data["updatedAt"] = now()
            self._audit("hermes_config update", before=before, after=dict(data))
            self._hermes = data
            self._hermes_version = data["updatedAt"]
            return dict(data)

    def _renumber(self) -> list[QueueEntry]:
        """Rebuild the queue entries from ``_queue_order`` with contiguous
        positions 0..n-1 (the moved/added mission keeps its fields)."""
        reordered: list[QueueEntry] = []
        for index, mission_id in enumerate(self._queue_order):
            old = self._queue.get(mission_id)
            entry = QueueEntry(
                position=index,
                mission_id=mission_id,
                enabled=old.enabled if old else True,
                attempts_per_task=old.attempts_per_task if old else None,
                retry_failed=old.retry_failed if old else False,
                note=old.note if old else None,
            )
            self._queue[mission_id] = entry
            reordered.append(entry)
        return reordered

    def _queue_audit(self, action: str, before: list[dict[str, Any]]) -> list[QueueEntry]:
        """Save the full previous queue (the snapshot taken before the change)
        to the changelog, then renumber."""
        reordered = self._renumber()
        self._audit(
            action,
            before=before,
            after=[dict(e.model_dump()) for e in reordered],
        )
        self._queue_version = now()
        return reordered

    def create_mission_rows(
        self,
        mission: Mission,
        live: LiveState,
        config: dict[str, Any] | None,
        position: int | None = None,
        note: str | None = None,
        who: str = "",
    ) -> None:
        """One atomic step: the mission row, its Live State row, its queue entry
        (at ``position``) and its config row — all rows or none.

        The lock is held while the rows land; they are not visible to readers
        until the step finishes, so undoing them on failure is a true
        all-or-none.  The audit keeps the four rows together in one change."""
        with self._lock:
            mission_id = mission.mission_id
            state_key = live.state_key
            previous_queue = dict(self._queue)
            previous_order = list(self._queue_order)
            previous_version = self._queue_version
            previous_changelog_len = len(self._changelog)
            try:
                if mission.updated_at is None:
                    mission = mission.model_copy(update={"updated_at": now()})
                if live.updated_at is None:
                    live = live.model_copy(update={"updated_at": now()})
                self._missions[mission_id] = mission
                self._live[state_key] = live
                insert_at = max(0, min(position if position is not None else len(self._queue_order),
                                      len(self._queue_order)))
                self._queue_order.insert(insert_at, mission_id)
                reordered = self._renumber()
                for entry in reordered:
                    if entry.mission_id == mission_id:
                        entry.note = note
                self._queue_version = now()
                if config:
                    previous_config = self._mission_config.get(mission_id)
                    previous_row = (
                        dict(previous_config) if previous_config is not None else None
                    )
                    row = {
                        "mission_id": mission_id,
                        "config_json": dict(config),
                        "updated_at": now(),
                        "updated_by": who or None,
                    }
                    self._audit(
                        "mission_config set",
                        before=previous_row,
                        after=dict(row),
                        mission_id=mission_id,
                        who=who,
                    )
                    self._mission_config[mission_id] = row
                    self._mission_config_version[mission_id] = row["updated_at"]
                self._audit(
                    "mission create",
                    before=None,
                    after={
                        "mission": mission.model_dump(),
                        "live_state": live.model_dump(),
                        "queue": [dict(e.model_dump()) for e in self.queue()],
                    },
                    mission_id=mission_id,
                    who=who or None,
                )
            except Exception:
                # undo every row written inside the failed step so the other
                # missions keep their exact prior state
                self._missions.pop(mission_id, None)
                self._live.pop(state_key, None)
                self._queue = previous_queue
                self._queue_order = previous_order
                self._queue_version = previous_version
                del self._changelog[previous_changelog_len:]
                self._mission_config.pop(mission_id, None)
                self._mission_config_version.pop(mission_id, None)
                raise

    def set_queue(
        self, entries: list[QueueEntry], expected_version: str | None
    ) -> list[QueueEntry]:
        with self._lock:
            self._check_version("queue", "queue", expected_version, self._queue_version)
            ids = [e.mission_id for e in entries]
            if len(ids) != len(set(ids)):
                raise ValueError("queue contains duplicate mission ids")
            self._guard_running(ids)
            before = [dict(entry.model_dump()) for entry in self.queue()]
            self._queue = {
                e.mission_id: QueueEntry(
                    position=e.position,
                    mission_id=e.mission_id,
                    enabled=e.enabled,
                    attempts_per_task=e.attempts_per_task,
                    retry_failed=e.retry_failed,
                    note=e.note,
                )
                for e in entries
            }
            self._queue_order = list(ids)
            return self._queue_audit("queue set", before)

    def add_queue(
        self,
        mission_id: str,
        expected_version: str | None,
        position: int | None = None,
        enabled: bool = True,
        attempts_per_task: float | None = None,
        retry_failed: bool = False,
        note: str | None = None,
    ) -> list[QueueEntry]:
        with self._lock:
            self._check_version("queue", "queue", expected_version, self._queue_version)
            if mission_id in self._queue:
                raise ValueError(f"mission {mission_id!r} is already in the queue")
            self._guard_running([mission_id])
            before = [dict(entry.model_dump()) for entry in self.queue()]
            self._queue[mission_id] = QueueEntry(
                position=len(self._queue_order),
                mission_id=mission_id,
                enabled=enabled,
                attempts_per_task=attempts_per_task,
                retry_failed=retry_failed,
                note=note,
            )
            insert_at = (
                len(self._queue_order)
                if position is None
                else max(0, min(position, len(self._queue_order)))
            )
            self._queue_order.insert(insert_at, mission_id)
            return self._queue_audit("queue add", before)

    def remove_queue(
        self, mission_id: str, expected_version: str | None, confirmed: bool = False
    ) -> list[QueueEntry]:
        with self._lock:
            self._check_version("queue", "queue", expected_version, self._queue_version)
            if mission_id not in self._queue:
                raise KeyError(f"mission {mission_id!r} is not in the queue")
            if not confirmed:
                self._guard_running([mission_id])
            before = [dict(entry.model_dump()) for entry in self.queue()]
            self._queue_order.remove(mission_id)
            del self._queue[mission_id]
            return self._queue_audit("queue remove", before)

    def move_queue(
        self, mission_id: str, to_position: int, expected_version: str | None
    ) -> list[QueueEntry]:
        with self._lock:
            self._check_version("queue", "queue", expected_version, self._queue_version)
            if mission_id not in self._queue:
                raise KeyError(f"mission {mission_id!r} is not in the queue")
            self._guard_running([mission_id])
            before = [dict(entry.model_dump()) for entry in self.queue()]
            self._queue_order.remove(mission_id)
            insert_at = max(0, min(to_position, len(self._queue_order)))
            self._queue_order.insert(insert_at, mission_id)
            return self._queue_audit("queue move", before)

    def set_mission_config(
        self,
        mission_id: str,
        config: dict[str, Any],
        expected_version: str | None,
        who: str = "",
    ) -> None:
        with self._lock:
            previous_row = self._mission_config.get(mission_id)
            previous = dict(previous_row) if previous_row is not None else None
            self._check_version(
                "mission_config",
                mission_id,
                expected_version,
                self._mission_config_version.get(mission_id, ""),
            )
            row = {
                "mission_id": mission_id,
                "config_json": dict(config),
                "updated_at": now(),
                "updated_by": who or None,
            }
            self._audit(
                "mission_config set",
                before=previous,
                after=dict(row),
                mission_id=mission_id,
                who=who,
            )
            self._mission_config[mission_id] = row
            self._mission_config_version[mission_id] = row["updated_at"]

    def set_task_override(
        self,
        mission_id: str,
        task_key: str,
        hint: str | None,
        extra: dict[str, Any] | None,
        expected_version: str | None,
        who: str = "",
    ) -> None:
        with self._lock:
            key = (mission_id, task_key)
            previous = self._task_overrides.get(key)
            self._check_version(
                "task_override", f"{mission_id}/{task_key}",
                expected_version, self._task_override_version.get(key, ""),
            )
            row = {
                "mission_id": mission_id,
                "task_key": task_key,
                "hint": hint,
                "extra_json": extra,
                "updated_at": now(),
                "updated_by": who or None,
            }
            self._audit(
                "task_override set",
                before=previous,
                after=dict(row),
                mission_id=mission_id,
                task_key=task_key,
                who=who,
            )
            self._task_overrides[key] = dict(row)
            self._task_override_version[key] = row["updated_at"]

    def remove_task_override(
        self, mission_id: str, task_key: str, expected_version: str | None
    ) -> None:
        with self._lock:
            key = (mission_id, task_key)
            previous = self._task_overrides.get(key)
            if previous is None:
                raise KeyError(f"task override {mission_id!r}/{task_key!r} not found")
            self._check_version(
                "task_override", f"{mission_id}/{task_key}",
                expected_version, self._task_override_version.get(key, ""),
            )
            self._audit(
                "task_override remove",
                before=previous,
                after=None,
                mission_id=mission_id,
                task_key=task_key,
            )
            del self._task_overrides[key]
            del self._task_override_version[key]

    def set_template(
        self,
        name: str,
        config: dict[str, Any],
        source_mission_id: str | None = None,
        expected_version: str | None = None,
    ) -> None:
        with self._lock:
            previous = self._templates.get(name)
            self._check_version(
                "template", name, expected_version, self._template_version.get(name, "")
            )
            row = {
                "name": name,
                "source_mission_id": source_mission_id,
                "config_json": config,
                "created_at": previous.get("created_at") if previous else now(),
            }
            self._audit("template set", before=previous, after=dict(row))
            self._templates[name] = dict(row)
            self._template_version[name] = now()

    def remove_template(self, name: str, expected_version: str | None) -> None:
        with self._lock:
            previous = self._templates.get(name)
            if previous is None:
                raise KeyError(f"template {name!r} not found")
            self._check_version(
                "template", name, expected_version, self._template_version.get(name, "")
            )
            self._audit("template remove", before=previous, after=None)
            del self._templates[name]
            del self._template_version[name]

    def set_flag(self, key: str, value: str, expected_version: str | None) -> None:
        with self._lock:
            previous = self._flags.get(key)
            self._check_version("flag", key, expected_version, self._flag_version.get(key, ""))
            self._audit(
                "flag set",
                before={"key": key, "value": previous} if previous is not None else None,
                after={"key": key, "value": value},
            )
            self._flags[key] = value
            self._flag_version[key] = now()

    def remove_flag(self, key: str, expected_version: str | None) -> None:
        with self._lock:
            previous = self._flags.get(key)
            if previous is None:
                raise KeyError(f"flag {key!r} not found")
            self._check_version("flag", key, expected_version, self._flag_version.get(key, ""))
            self._audit("flag remove", before={"key": key, "value": previous}, after=None)
            del self._flags[key]
            del self._flag_version[key]

    def record_preflight(self, mission_id: str, items: list[dict[str, Any]]) -> None:
        with self._lock:
            row = {
                "mission_id": mission_id,
                "checked_at": now(),
                "items_json": items,
            }
            self._audit("preflight record", before=None, after=dict(row), mission_id=mission_id)
            self._preflight.setdefault(mission_id, []).insert(0, dict(row))

    def get_project_rules(self) -> tuple[list[dict[str, Any]], str]:
        with self._lock:
            return [dict(r) for r in self._project_rules], self._project_rules_version

    def put_project_rules(
        self, rules: list[dict[str, Any]], expected_version: str | None, actor: str = "",
    ) -> None:
        with self._lock:
            self._check_version(
                "project_rules", "rules", expected_version, self._project_rules_version
            )
            ordered = sorted(
                (
                    {"name": r["name"], "pattern": r["pattern"], "position": int(r["position"])}
                    for r in rules
                ),
                key=lambda r: r["position"],
            )
            self._audit(
                "project rules set",
                before=[dict(r) for r in self._project_rules],
                after=[dict(r) for r in ordered],
                who=actor,
            )
            self._project_rules = ordered
            self._project_rules_version = now() if ordered else ""

    def list_i18n(self) -> dict[str, Any]:
        with self._lock:
            strings: dict[str, dict[str, str]] = {}
            for (lang, key), row in sorted(self._i18n_strings.items()):
                strings.setdefault(lang, {})[key] = row["text"]
            languages = [dict(v) for _, v in sorted(self._i18n_languages.items())]
            return {"languages": languages, "strings": strings}

    def put_i18n_string(self, lang: str, key: str, text: str, actor: str = "") -> None:
        with self._lock:
            previous = self._i18n_strings.get((lang, key))
            if not text:
                if previous is not None:
                    self._audit(
                        "i18n string remove",
                        before={"lang": lang, "key": key, "text": previous["text"]},
                        after=None,
                        who=actor,
                    )
                    del self._i18n_strings[(lang, key)]
                return
            row = {
                "lang": lang,
                "key": key,
                "text": text,
                "updated_at": now(),
                "updated_by": actor or None,
            }
            self._audit(
                "i18n string set",
                before=None if previous is None else {
                    "lang": lang, "key": key, "text": previous["text"],
                },
                after={"lang": lang, "key": key, "text": text},
                who=actor,
            )
            self._i18n_strings[(lang, key)] = row

    def add_i18n_language(self, code: str, name: str) -> None:
        with self._lock:
            if code in self._i18n_languages:
                raise ConflictError(f"i18n language {code!r} already exists")
            after = {"code": code, "name": name, "enabled": True}
            self._audit("i18n language add", before=None, after=after)
            self._i18n_languages[code] = dict(after)

    def i18n_is_empty(self) -> bool:
        with self._lock:
            return not self._i18n_languages and not self._i18n_strings

    def seed_i18n(self, sets: dict[str, Any]) -> None:
        with self._lock:
            if not self.i18n_is_empty():
                return
            for item in sets.get("languages", []):
                self._i18n_languages[item["code"]] = {
                    "code": item["code"], "name": item["name"], "enabled": True,
                }
            for lang, texts in sets.get("strings", {}).items():
                for key, text in texts.items():
                    if text:
                        self._i18n_strings[(lang, key)] = {
                            "lang": lang, "key": key, "text": text,
                            "updated_at": now(), "updated_by": None,
                        }


# --- factory and Pg store (deferred import: keeps ``board`` importable without psycopg)
def get_board_store(dsn: str | None = None) -> BoardStore:
    """Pick the Pg store when the DSN is set and connectable; otherwise return
    an unavailable stub whose ``status()`` explains why (never prints the DSN).

    Deferred import of ``board_pg`` so that importing ``board`` works even when
    ``psycopg`` is missing."""
    from . import board_pg

    return board_pg.get_board_store(dsn)


def __getattr__(name: str) -> Any:
    """Lazy re-export of the Pg store classes from ``board_pg``.

    Keeps ``from claude_runner.board import PgBoardStore`` (and
    ``UnavailableBoardStore``) working without importing psycopg at module
    load."""
    if name in ("PgBoardStore", "UnavailableBoardStore"):
        from . import board_pg

        return getattr(board_pg, name)
    raise AttributeError(name)
