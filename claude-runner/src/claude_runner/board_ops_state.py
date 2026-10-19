"""Process-state aggregation and task-boundary checks for board ops.

Pure functions over the dicts the agent publishes: the ``board.agent``
heartbeat row, the ``board.host_snapshot`` payload and the worker runs
(``run_views.summary`` rows).  No I/O.  Time stamps are ISO-8601 strings
(``board.now`` / Postgres ``timestamptz``), so every age computation goes
through :func:`_age`.

``agent_status(heartbeat, now)`` reports ``alive`` / ``stale`` — stale
means the heartbeat is older than :data:`STALE_AGENT_S` (30 s; the same
threshold the UI uses to disable the action buttons, see
``docs/board-ops.md``, "board.agent").

``process_panel(snapshot, runs)`` aggregates the process panel: the queue
state (``real_queue.sh`` alive, current mission and task, age of the last
queue event), ``queue_watch``, ``docker_watchdog``, ``tg_relay``, the n8n
container and the running Worker run (the snapshot keys follow the
``data_json`` list in ``docs/board-ops.md``, "board.host_snapshot").

``at_task_boundary(state)`` is true when no mission is running, or when the
newest line of the queue log that matches ``ended <mission>:`` or
``started <mission>`` is an ``ended`` line — the current task has
finished and the next one has not started yet (the rule of
``docs/board-ops-notes.md``, "Process control": the boundary is the point
where the Worker's task id changes; between the ``ended <mission>:`` line
and the next ``started`` line the task is finished and the next one is
not running).  ``check_boundary(action, state)`` refuses an action that
has ``requires_boundary`` (``board_ops_actions.ACTIONS``) unless the
state is at a boundary.

``interrupted_preview(state)`` describes what a stop would interrupt.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from . import board_ops_actions

__all__ = [
    "STALE_AGENT_S",
    "agent_status",
    "at_task_boundary",
    "check_boundary",
    "interrupted_preview",
    "process_panel",
]

#: A heartbeat older than this many seconds means the agent is stale
#: (docs/board-ops.md: the UI disables actions when the heartbeat is
#: older than 30 s).
STALE_AGENT_S = 30.0

#: The n8n container name of the agent stack.
_N8N_CONTAINER = "n8n-autonomous-agents"

#: The queue log line that closes a mission / task (the boundary marker).
_END_PATTERN = re.compile(r"\bended\s+([^\s:]+):")
#: The queue log line that starts a mission / task.
_START_PATTERN = re.compile(r"\bstarted\s+([^\s:]+)(?:\s+\(run\s+\d+)?")


def _age(value: str | None, now: str | None) -> float | None:
    """Seconds between the ISO-8601 stamp ``value`` and ``now``.

    ``None`` when either side is missing or not a parseable ISO-8601 stamp."""
    if not value or not now:
        return None
    try:
        age = (datetime.fromisoformat(now) - datetime.fromisoformat(value)).total_seconds()
    except ValueError:
        return None
    return age if age >= 0 else 0.0


def _snapshot_key(snapshot: dict[str, Any], name: str) -> Any:
    """The section under ``snapshot[name]`` (``None`` when absent)."""
    section = snapshot.get(name)
    return section if isinstance(section, dict) else None


def agent_status(heartbeat: str | None, now: str | None) -> str:
    """``alive`` when ``heartbeat`` is no more than :data:`STALE_AGENT_S`
    seconds old at ``now``; ``stale`` otherwise (missing or unparseable
    ``heartbeat`` is stale)."""
    age = _age(heartbeat, now)
    return "alive" if age is not None and age <= STALE_AGENT_S else "stale"


def _current_worker_run(runs: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The first ``role == 'worker'`` run that is ``queued`` or ``running``.

    ``status`` values are the ``run_views.summary`` ones (``queued``,
    ``running``, ``done``, ``failed``)."""
    for run in runs or []:
        if not isinstance(run, dict):
            continue
        if run.get("role") == "worker" and run.get("status") in ("queued", "running"):
            return run
    return None


def _task_markers(queue_log: list[dict[str, Any]] | None) -> tuple[int | None, int | None]:
    """The positions in the newest-first queue log of the newest
    ``ended <mission>:`` line and the newest ``started <mission>`` line
    (``None`` when neither kind of line is present)."""
    ended_idx: int | None = None
    started_idx: int | None = None
    for index, row in enumerate(queue_log or []):
        line = row.get("line") if isinstance(row, dict) else None
        if not isinstance(line, str):
            continue
        if ended_idx is None and _END_PATTERN.search(line):
            ended_idx = index
        if started_idx is None and _START_PATTERN.search(line):
            started_idx = index
        if ended_idx is not None and started_idx is not None:
            break
    return ended_idx, started_idx


def process_panel(
    snapshot: dict[str, Any] | None,
    runs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """The process panel over one published snapshot and the worker runs.

    Keys: ``queue`` (``alive``, ``mission``, ``task``, ``last_event_age_s``),
    ``queue_watch``, ``docker_watchdog``, ``tg_relay``, ``n8n`` and
    ``worker_run``.  Each section mirrors the ``data_json`` contents of
    ``docs/board-ops.md`` ("board.host_snapshot"); a missing section is
    ``None`` and a missing age is ``None``."""
    snapshot = snapshot or {}
    queue_section = _snapshot_key(snapshot, "queue") or {}
    queue_watch = _snapshot_key(snapshot, "queue_watch")
    watchdog = _snapshot_key(snapshot, "docker_watchdog")
    relay = _snapshot_key(snapshot, "tg_relay")
    n8n = _snapshot_key(snapshot, "n8n")

    runs = [run for run in (runs or []) if isinstance(run, dict)]

    queue = {
        "alive": queue_section.get("alive"),
        "mission": queue_section.get("mission"),
        "task": queue_section.get("task"),
        "last_event_age_s": _age(queue_section.get("last_event_at"), snapshot.get("at")),
    }
    return {
        "queue": queue,
        "queue_watch": queue_watch,
        "docker_watchdog": watchdog,
        "tg_relay": relay,
        "n8n": n8n,
        "worker_run": _current_worker_run(runs),
    }


def at_task_boundary(state: dict[str, Any] | None) -> bool:
    """Whether the state is at a task boundary.

    True when no mission is running, or when the newest line of the queue
    log that matches ``ended <mission>:`` or ``started <mission>`` is an
    ``ended`` line (the current task has finished and the next one has not
    started yet; see the module docstring).  While the queue is alive, a
    newest ``started`` line — or no matching line at all — means mid-run
    (conservatively ``False``)."""
    state = state or {}
    queue = state.get("queue")
    queue = queue if isinstance(queue, dict) else {}
    if not queue.get("alive"):
        return True
    queue_log = state.get("queue_log")
    if not isinstance(queue_log, list):
        return False
    ended_idx, started_idx = _task_markers(queue_log)
    return ended_idx is not None and (started_idx is None or ended_idx < started_idx)


def check_boundary(action: str, state: dict[str, Any] | None) -> list[str]:
    """The rejection reasons for an action at the current state.

    Empty for actions without a ``requires_boundary`` flag.  For an action
    that requires a task boundary, refuses when the state is mid-run: a
    mission is running and the current task has not finished (no
    ``ended <mission>:`` line after the last ``started`` line of the queue
    log)."""
    entry = board_ops_actions.ACTIONS.get(action)
    if entry is None:
        known = ", ".join(sorted(board_ops_actions.ACTIONS))
        return [f"unknown action {action!r} (known actions: {known})"]
    if not entry.get("requires_boundary"):
        return []
    if at_task_boundary(state):
        return []
    return [f"action {action!r} requires a task boundary, but a task is mid-run"]


def interrupted_preview(state: dict[str, Any] | None) -> str | None:
    """What a stop would interrupt, or ``None`` when nothing is running."""
    state = state or {}
    queue = state.get("queue")
    queue = queue if isinstance(queue, dict) else {}
    if not queue.get("alive"):
        return None
    mission = queue.get("mission")
    task = queue.get("task")
    if mission and task:
        return f"mission {mission} — task {task}"
    if mission:
        return f"mission {mission}"
    return "the queue"
