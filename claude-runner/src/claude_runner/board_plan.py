"""Server-side guards for plan editing: the pure functions the board uses.

Everything here is ``plan_json`` plus the Live State row (``live_status`` /
``live_task_index`` / ``live_task_attempt``) — the browser's data is never
trusted: the task status is re-derived server-side with
:class:`claude_runner.board.TaskStatuses`, and every write re-checks the plan
version the caller read (``expected_version``) against the version the
caller read from storage (``stored_version``, the Live State row's
``updatedAt`` — the plan_json itself carries no version field).  Each
function returns the new plan plus a changelog payload; a rejected action
raises :class:`~claude_runner.board.GuardError` (or
:class:`~claude_runner.board.ConflictError`) with a clear message.

Rules (``docs/board-data-model.md``):

- a task is editable only while it is ``waiting`` (its index is past the live
  ``task_index``) and its content fields (title / objective / acceptance
  criteria / verification / scope) only; the Worker hint is stored separately
  in ``task_overrides.hint`` via :func:`set_task_hint`;
- accepted and running tasks (including ``review_pending``) are read-only;
- ``not_accepted`` (the review rejected the current task, retry pending) may
  receive a hint and a retry request, which resets the attempt counter to 1;
- pending tasks can be added, deleted, reordered and duplicated; the keys are
  renumbered predictably (``T001``..``T00n`` in plan order) and every surviving
  task keeps its old key;
- for a running mission only config fields that apply from the next task
  onwards are editable; fields that shape the already-running task (the
  working directory and the state key) are rejected with a reason.
"""

from __future__ import annotations

from typing import Any

from .board import ConflictError, GuardError, TaskStatuses

__all__ = [
    "EDITABLE_TASK_FIELDS",
    "RETRYABLE_TASK_STATUSES",
    "MIDRUN_CONFIG_FIELDS",
    "NON_EDITABLE_CONFIG_FIELDS",
    "compose_task_text",
    "editable_task_fields",
    "task_overrides_hint",
    "update_task",
    "add_task",
    "delete_task",
    "move_task",
    "duplicate_task",
    "set_task_hint",
    "request_retry",
    "update_mission_config",
]

#: Task content fields a ``waiting`` task may be edited in.  The Worker hint
#: is deliberately NOT a content field: it is stored separately in
#: ``task_overrides.hint`` (see :func:`set_task_hint`).
EDITABLE_TASK_FIELDS = (
    "title",
    "objective",
    "acceptance_criteria",
    "verification",
    "scope",
)

#: Task statuses for which editing the content is forbidden.
#: ``waiting`` is the only editable status; ``review_pending`` and
#: ``not_accepted`` are editable only for the hint (and ``not_accepted``
#: additionally allows a retry request).
READ_ONLY_TASK_STATUSES = ("running", "review_pending", "accepted", "completed", "failed")

#: Config fields that a running mission may change: they take effect from the
#: next task onwards (the workflow re-reads the mission config at every run
#: start, so an in-flight run keeps its own values).
MIDRUN_CONFIG_FIELDS = (
    "worker_model",
    "worker_provider",
    "reviewer_model",
    "reviewer_provider",
    "service_model",
    "service_provider",
    "worker_max_polls",
    "reviewer_max_polls",
    "architect_max_polls",
    "worker_poll_seconds",
    "reviewer_poll_seconds",
    "architect_poll_seconds",
    "worker_max_retries",
    "reviewer_max_retries",
    "architect_max_retries",
    "retry_delays",
    "max_attempts_per_task",
    "max_tasks",
    "worker_think_policy",
    "worker_think_policy_from_task",
    "worker_think_off_after_seconds",
    "worker_think_off_after_steps",
)

#: Config fields that are rejected for a running mission, with the reason.
NON_EDITABLE_CONFIG_FIELDS: dict[str, str] = {
    "working_directory": (
        "mission is running: the working directory is bound to the live "
        "worker session and cannot change while a run is in flight"
    ),
    "state_namespace": (
        "mission is running: the state key identifies the live state row and "
        "cannot be changed while the mission is running"
    ),
    "retrieval_corpora": (
        "mission is running: the retrieval corpora are sent with the live "
        "run request and cannot be changed mid-run"
    ),
}

_TASK_KEYS = ("id", "title", "objective", "acceptance_criteria")
_TASK_OPTIONAL = ("verification", "scope", "dependencies")


def _task_key(task: dict[str, Any], index: int) -> str:
    return str(task.get("id") or f"T{index + 1:03d}")


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """A copy of the plan with predictable task keys and stable field sets."""
    plan = dict(plan)
    tasks = [dict(t) for t in (plan.get("tasks") or [])]
    for index, task in enumerate(tasks):
        task["id"] = _task_key(task, index)
        for field in _TASK_KEYS:
            task.setdefault(field, "" if field != "acceptance_criteria" else [])
        for field in _TASK_OPTIONAL:
            task.setdefault(field, [])
    plan["tasks"] = tasks
    return plan


def _check_version(expected_version: str | None, stored_version: str | None) -> None:
    """Optimistic lock: the caller must pass the plan version it read.

    Raises :class:`ConflictError` when ``expected_version`` is ``None`` (the
    version is required — the plan_json itself carries no version field, so
    the caller must supply the stored row's version) or when it differs
    from the stored version.
    """
    if expected_version is None:
        raise ConflictError("plan version is required")
    if expected_version != stored_version:
        raise ConflictError(
            f"plan version {expected_version!r} is stale, current version is {stored_version!r}: reload the plan"
        )


def _changelog(action: str, plan: dict[str, Any], **meta: Any) -> dict[str, Any]:
    """The changelog payload for one accepted plan edit."""
    return {"action": action, "plan": plan, **meta}


def _statuses(
    plan: dict[str, Any],
    live_status: str | None,
    live_task_index: float | None,
    live_task_attempt: float | None,
    events: list[dict[str, Any]] | None,
) -> dict[str, str]:
    """Per-task derived statuses: task_key -> status string.

    The status is recomputed server-side from the live row and the event log;
    the browser's status is never trusted."""
    tasks = plan.get("tasks") or []
    result: dict[str, str] = {}
    for index, task in enumerate(tasks):
        key = _task_key(task, index)
        result[key] = TaskStatuses.derive(
            task,
            live_status=live_status,
            live_task_index=live_task_index,
            live_task_attempt=live_task_attempt,
            task_index=index,
            events=events,
        )
    return result


def _validate_editable_fields(
    statuses: dict[str, str], plan: dict[str, Any], task_key: str, fields: dict[str, Any]
) -> str:
    """Which fields may be changed for the task's status; returns the status."""
    tasks = plan.get("tasks") or []
    if not any(_task_key(t, i) == task_key for i, t in enumerate(tasks)):
        raise ValueError(f"task {task_key!r} is not in the plan")
    status = statuses.get(task_key, "waiting")
    if status in READ_ONLY_TASK_STATUSES and status not in RETRYABLE_TASK_STATUSES:
        raise GuardError(f"task {task_key!r} is {status} and read-only")
    if status == "waiting":
        allowed = set(EDITABLE_TASK_FIELDS)
    elif status == "not_accepted" or status == "review_pending":
        allowed = {"hint"}
    else:
        allowed = set()
    for name in fields:
        if name not in allowed:
            raise GuardError(
                f"field {name!r} is not editable for task {task_key!r} (status {status!r}): "
                f"allowed fields for this status: {sorted(allowed) or 'none'}"
            )
    return status


# ---------------------------------------------------------------------------
# Task edits
# ---------------------------------------------------------------------------

def update_task(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    live_status: str | None,
    live_task_index: float | None,
    live_task_attempt: float | None,
    events: list[dict[str, Any]] | None = None,
    **fields: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Edit the content of one task (title / objective / acceptance
    criteria / verification / scope).

    Editable only while the task is ``waiting``; the Worker hint is not a
    content field and is stored separately in ``task_overrides.hint``
    (see :func:`set_task_hint`).  Returns the new plan plus a changelog
    payload; raises :class:`GuardError` for a read-only task and
    :class:`ConflictError` for a stale plan version.
    """
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    statuses = _statuses(plan, live_status, live_task_index, live_task_attempt, events)
    _validate_editable_fields(statuses, plan, task_key, fields)
    tasks = plan["tasks"]
    index = next(i for i, t in enumerate(tasks) if _task_key(t, i) == task_key)
    task = tasks[index]
    for name, value in fields.items():
        task[name] = value
    return plan, _changelog(
        "task update",
        plan,
        mission_id=None,
        task_key=task_key,
        previous_status=statuses[task_key],
    )


def add_task(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    title: str,
    objective: str = "",
    acceptance_criteria: list[Any] | None = None,
    after_key: str | None = None,
    live_status: str | None = None,
    live_task_index: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add a pending task (inserted after ``after_key`` or appended).

    All keys are renumbered ``T001``..``T00n`` in plan order; a task may not be
    inserted at or before the live ``task_index`` (that task is already in
    flight and cannot be changed).
    """
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    tasks = plan.get("tasks") or []
    if not title:
        raise ValueError("title is required")
    if live_task_index is not None:
        current_index = int(live_task_index)
    elif live_status == "completed":
        current_index = len(tasks)
    else:
        current_index = -1
    if after_key is None:
        insert_at = len(tasks)
    else:
        insert_at = next(i for i, t in enumerate(tasks) if _task_key(t, i) == after_key) + 1
    if insert_at <= current_index:
        raise GuardError(
            f"cannot insert a task at position {insert_at}: it would land at or before the live "
            f"task_index {current_index}; a pending task must be inserted after it"
        )
    new_index = insert_at
    task = {
        "id": "",
        "title": title,
        "objective": objective,
        "acceptance_criteria": list(acceptance_criteria or []),
        "verification": [],
        "scope": [],
        "dependencies": [],
    }
    tasks.insert(insert_at, task)
    # Renumber: keys follow plan order, so the new task gets T00n for its new
    # position and every key after it shifts.
    for i, t in enumerate(tasks):
        t["id"] = f"T{i + 1:03d}"
    plan["tasks"] = tasks
    new_key = _task_key(tasks[new_index], new_index)
    return plan, _changelog("task add", plan, mission_id=None, task_key=new_key)


def delete_task(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    live_status: str | None,
    live_task_index: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Delete a pending task; keys are renumbered predictably."""
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    tasks = plan.get("tasks") or []
    _statuses(plan, live_status, live_task_index, None, None)
    index = next((i for i, t in enumerate(tasks) if _task_key(t, i) == task_key), None)
    if index is None:
        raise ValueError(f"task {task_key!r} is not in the plan")
    current_index = int(live_task_index) if live_task_index is not None else -1
    if index <= current_index:
        raise GuardError(
            f"task {task_key!r} is at index {index}, which is not past the live "
            f"task_index {current_index}: only pending (waiting) tasks can be deleted"
        )
    tasks.pop(index)
    for i, t in enumerate(tasks):
        t["id"] = f"T{i + 1:03d}"
    plan["tasks"] = tasks
    return plan, _changelog("task delete", plan, mission_id=None, task_key=task_key)


def move_task(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    to_position: int,
    live_status: str | None,
    live_task_index: float | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reorder pending tasks; ``to_position`` is the new 0-based position."""
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    tasks = plan.get("tasks") or []
    _statuses(plan, live_status, live_task_index, None, None)
    index = next((i for i, t in enumerate(tasks) if _task_key(t, i) == task_key), None)
    if index is None:
        raise ValueError(f"task {task_key!r} is not in the plan")
    current_index = int(live_task_index) if live_task_index is not None else -1
    if index <= current_index:
        raise GuardError(
            f"task {task_key!r} is not pending (index {index} vs live task_index {current_index})"
        )
    if to_position <= current_index:
        raise GuardError(
            f"cannot move task {task_key!r} to position {to_position}: pending tasks may only "
            f"occupy positions past the live task_index {current_index}"
        )
    task = tasks.pop(index)
    to_position = max(0, min(to_position, len(tasks)))
    tasks.insert(to_position, task)
    for i, t in enumerate(tasks):
        t["id"] = f"T{i + 1:03d}"
    plan["tasks"] = tasks
    return plan, _changelog(
        "task move", plan, mission_id=None, task_key=task_key, from_index=index, to_position=to_position
    )


def duplicate_task(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    after_key: str | None = None,
    live_status: str | None = None,
    live_task_index: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Duplicate a task as a new pending task; keys are renumbered predictably."""
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    tasks = plan.get("tasks") or []
    index = next((i for i, t in enumerate(tasks) if _task_key(t, i) == task_key), None)
    if index is None:
        raise ValueError(f"task {task_key!r} is not in the plan")
    current_index = int(live_task_index) if live_task_index is not None else -1
    if index <= current_index:
        raise GuardError(
            f"task {task_key!r} is not pending (index {index} vs live task_index {current_index})"
        )
    insert_at = index + 1
    if after_key is not None:
        after_idx = next(i for i, t in enumerate(tasks) if _task_key(t, i) == after_key)
        insert_at = after_idx + 1
    if insert_at <= current_index:
        raise GuardError(
            f"cannot duplicate task {task_key!r} at position {insert_at}: it would land at or "
            f"before the live task_index {current_index}"
        )
    task = dict(tasks[index])
    tasks.insert(insert_at, task)
    for i, t in enumerate(tasks):
        t["id"] = f"T{i + 1:03d}"
    plan["tasks"] = tasks
    new_key = _task_key(tasks[insert_at], insert_at)
    return plan, _changelog("task duplicate", plan, mission_id=None, task_key=new_key)


def set_task_hint(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    hint: str | None,
    live_status: str | None,
    live_task_index: float | None,
    live_task_attempt: float | None,
    events: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Store a Worker hint separately in ``task_overrides.hint``.

    Allowed for ``waiting`` and ``review_pending`` tasks; for ``not_accepted``
    the hint is the only editable field.  The plan itself is unchanged (the
    hint lives in the task override), so the changelog payload carries the
    override row, not a plan diff.
    """
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    statuses = _statuses(plan, live_status, live_task_index, live_task_attempt, events)
    if task_key not in statuses:
        raise ValueError(f"task {task_key!r} is not in the plan")
    status = statuses[task_key]
    if status in READ_ONLY_TASK_STATUSES and status not in ("not_accepted", "review_pending"):
        raise GuardError(f"task {task_key!r} is {status} and read-only")
    # The hint is stored in the task override, not in the plan itself.
    return plan, {
        "action": "task hint set",
        "override": {"task_key": task_key, "hint": hint},
        "previous_status": status,
    }


RETRYABLE_TASK_STATUSES = ("not_accepted",)


def request_retry(
    plan: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    task_key: str,
    live_status: str | None,
    live_task_index: float | None,
    live_task_attempt: float | None,
    events: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Request a retry of a ``not_accepted`` task: resets the attempt counter.

    Only valid while the task is ``not_accepted`` (the review rejected the
    current task and a retry is pending).  The returned plan is unchanged; the
    live-state update (``task_attempt`` reset to 1) is carried in the
    changelog payload for the caller to write to the Live State row.
    """
    _check_version(expected_version, stored_version)
    plan = _normalize_plan(plan)
    statuses = _statuses(plan, live_status, live_task_index, live_task_attempt, events)
    if task_key not in statuses:
        raise ValueError(f"task {task_key!r} is not in the plan")
    status = statuses[task_key]
    if status not in RETRYABLE_TASK_STATUSES:
        raise GuardError(
            f"task {task_key!r} has status {status!r}; only a not_accepted task may be retried"
        )
    return plan, {
        "action": "task retry requested",
        "live_state": {"task_attempt": 1},
        "previous_attempt": live_task_attempt,
    }


# ---------------------------------------------------------------------------
# Mission-level config
# ---------------------------------------------------------------------------

def update_mission_config(
    config: dict[str, Any],
    expected_version: str,
    stored_version: str | None,
    mission_running: bool,
    **fields: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Edit mission-level config.

    For a running mission only the fields that apply from the next task
    onwards are allowed; anything else is rejected with a reason.  For a
    non-running mission every field is editable.  Returns the new config plus
    a changelog payload; raises :class:`GuardError` for rejected fields and
    :class:`ConflictError` when the version is missing or stale.
    """
    _check_version(expected_version, stored_version)
    for name, value in fields.items():
        if mission_running and name not in MIDRUN_CONFIG_FIELDS:
            reason = NON_EDITABLE_CONFIG_FIELDS.get(
                name,
                f"field {name!r} is not editable while the mission is running: only fields that "
                f"apply from the next task onwards may be changed",
            )
            raise GuardError(f"mission is running: {reason}")
    config = dict(config)
    for name, value in fields.items():
        config[name] = value
    return config, _changelog("mission config update", config, mission_id=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compose_task_text(
    task: dict[str, Any], hint: str | None = None
) -> str:
    """Compose the text sent to the Worker for launch.

    ``task text`` = title + objective + acceptance criteria (the fields the
    workflow injects into the Worker prompt); the Worker hint (stored in
    ``task_overrides.hint``) is appended last, separated by a blank line.
    """
    parts = [
        str(task.get("title") or "").strip(),
        str(task.get("objective") or "").strip(),
    ]
    criteria = task.get("acceptance_criteria") or []
    if criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(str(c) for c in criteria))
    text = "\n\n".join(p for p in parts if p)
    if hint is not None and str(hint).strip():
        text = text + "\n\nWorker hint:\n" + str(hint).strip()
    return text


def task_overrides_hint(
    overrides: list[dict[str, Any]], task_key: str
) -> str | None:
    """The stored hint for a task from its ``task_overrides`` rows."""
    for row in overrides:
        if row.get("task_key") == task_key:
            return row.get("hint")
    return None


def editable_task_fields(status: str) -> tuple[str, ...]:
    """The fields editable for a task of the given status."""
    if status == "waiting":
        return EDITABLE_TASK_FIELDS
    if status in ("not_accepted", "review_pending"):
        return ("hint",)
    return ()
