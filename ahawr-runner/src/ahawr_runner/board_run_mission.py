"""Eligibility of a mission for a new run, and the "run next" queue move.

Only stored rows are read: ``enabled`` of the mission and of its queue row,
``retry_failed`` of the queue row, the running marker of the Live State row,
the Live State ``status``, and the ``review_status`` verdict of the current
task.  The free-text ``review_reason`` is never parsed (CLAUDE.md: statuses
come from the stored data).  ``run_next`` refuses an ineligible mission and
otherwise delegates the move to ``store.move_queue``, so this module holds no
SQL and does no position arithmetic of its own.

``run_now`` first performs ``run_next``, then wakes the queue with the
``resume`` ops action when the process panel says nothing is running; while a
mission is running it stops nothing and offers the boundary stop instead.  The
enqueued command row is executed by the host agent: this module never runs
docker, powershell or a shell command of its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import board_ops_actions, board_ops_state
from .board import (
    BoardStore,
    GuardError,
    LiveState,
    QueueEntry,
    _latest_review_status,
    _waits_for_retry,
    is_stale_active,
    never_ran,
)
from .board_ops_guard import check_can_enqueue
from .board_ops_store import OpsStore

__all__ = [
    "BOUNDARY_STOP_ACTION",
    "BLOCKED_REASONS",
    "RESUME_ACTION",
    "REASON_COMPLETED",
    "REASON_DISABLED",
    "REASON_FAILED",
    "REASON_RUNNING",
    "RUN_NEXT_POSITION",
    "RUN_NOW_ACTOR",
    "STATUS_AFTER_RUNNING",
    "STATUS_RESUME_QUEUED",
    "mission_run_eligibility",
    "run_next",
    "run_now",
]

# The only reason codes a refused run can carry; the UI shows them, not text.
REASON_DISABLED = "disabled"
REASON_FAILED = "failed_without_retry"
REASON_RUNNING = "running"
REASON_COMPLETED = "completed"
BLOCKED_REASONS: frozenset[str] = frozenset(
    {REASON_DISABLED, REASON_FAILED, REASON_RUNNING, REASON_COMPLETED}
)

# "Run next" means the front of the queue, position 0 of ``board.queue``.
RUN_NEXT_POSITION = 0

# Action ids taken from the ``board_ops_actions.ACTIONS`` catalogue: the queue is
# woken by ``resume`` when nothing runs, and the boundary stop is only offered.
RESUME_ACTION = "resume"
BOUNDARY_STOP_ACTION = "pause_at_boundary"

# The two structured answers of ``run_now`` (the UI branches on the code, never
# on a reason text): the resume row was queued, or the mission waits behind the
# one running now.
STATUS_RESUME_QUEUED = "resume_queued"
STATUS_AFTER_RUNNING = "after_running"

# The name the enqueued ``resume`` row carries as its author.
RUN_NOW_ACTOR = "run now"


def _queued_entry(store: BoardStore, mission_id: str) -> QueueEntry | None:
    """The queue row of ``mission_id``, or None when the mission is not queued."""
    return next(
        (entry for entry in store.queue() if entry.mission_id == mission_id),
        None,
    )


def _current_task_id(live: LiveState) -> str | None:
    """The task id at the live ``task_index``, or None when the plan has no task there."""
    tasks = (live.plan or {}).get("tasks") or []
    index = int(live.task_index) if live.task_index is not None else 0
    if not 0 <= index < len(tasks):
        return None
    return str(tasks[index].get("id") or f"T{index + 1:03d}")


def mission_run_eligibility(store: BoardStore, mission_id: str) -> str | None:
    """Return None when ``mission_id`` may be run, else the blocked reason code.

    ``KeyError`` names a mission that is not on the board.  A mission is
    blocked when it is disabled (the mission row or its queue row says so),
    when its Live State row is running (``active`` and written recently), when
    it is ``completed``, or when it is ``failed``/``stopped`` and the queue row
    carries no ``retry_failed`` flag.
    """
    mission = store.get_mission(mission_id)
    if mission is None:
        raise KeyError(f"mission {mission_id!r} is not on the board")
    entry = _queued_entry(store, mission_id)
    if not mission.enabled or (entry is not None and not entry.enabled):
        return REASON_DISABLED

    live = store.get_live_state(mission.state_namespace)
    if live is None:
        return None  # never started: nothing stored says it cannot run
    if live.status == "active" and not is_stale_active(live) and not never_ran(live):
        return REASON_RUNNING
    if live.status == "completed":
        return REASON_COMPLETED

    will_retry = _waits_for_retry(entry)
    if live.status in ("failed", "stopped"):
        return None if will_retry else REASON_FAILED

    # shortcut: when the Live State status was never written, the stored
    # review_status of the current task is the only verdict left; upgrade when
    # every finish writes the status column.
    task_id = _current_task_id(live)
    if task_id is None:
        return None
    verdict = _latest_review_status(store.events(live.state_key), task_id)
    if verdict == "pass":
        return REASON_COMPLETED
    if verdict == "needs_changes" and not will_retry:
        return REASON_FAILED
    return None


def run_next(
    store: BoardStore,
    mission_id: str,
    expected_version: str | None = None,
) -> list[QueueEntry]:
    """Send ``mission_id`` to position 0 of ``board.queue``.

    Raises :class:`GuardError` (the board API's 409) with the reason code when
    the mission is ineligible, and the store's ``KeyError`` when it has no
    queue row (the UI adds that row with ``add_queue`` before the move).  The
    move itself runs through ``store.move_queue``, which keeps the positions
    contiguous; the queue after the move is returned.
    """
    reason = mission_run_eligibility(store, mission_id)
    if reason is not None:
        raise GuardError(f"run next refused for mission {mission_id!r}: {reason}")
    return store.move_queue(mission_id, RUN_NEXT_POSITION, expected_version)


def _queue_is_running(ops_store: OpsStore) -> bool:
    """Whether a mission is running now: ``queue.alive`` of the process panel
    built from the newest published host snapshot (``board_ops_state``)."""
    rows = ops_store.get_snapshot("host_snapshot", limit=1)
    snapshot = rows[0].get("data_json") if rows else None
    panel = board_ops_state.process_panel(
        snapshot if isinstance(snapshot, dict) else None, None
    )
    queue = panel.get("queue")
    return bool(queue.get("alive")) if isinstance(queue, dict) else False


def _enqueue_action(ops_store: OpsStore, action: str, args: dict[str, Any]) -> dict[str, Any]:
    """Enqueue one command row for the host agent through the ops machinery.

    The same checks the UI's prepare/confirm routes take: the catalogue
    validation of ``board_ops_actions`` and the single-execution admission guard;
    the agent claims and executes the row, never this module.
    """
    errors = board_ops_actions.validate_action(action, args)
    if errors:
        raise GuardError("; ".join(errors))
    check_can_enqueue(ops_store, action, args)
    command = ops_store.insert_command(action, args, RUN_NOW_ACTOR)
    return command.model_dump()


def run_now(
    store: BoardStore,
    mission_id: str,
    ops_store: OpsStore,
    expected_version: str | None = None,
    executor: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Send ``mission_id`` to the front of the queue, then wake the queue.

    ``run_next`` runs first, so its ``GuardError`` (the reason code) and the
    store's ``KeyError`` propagate unchanged.  The "is a mission running" fact
    comes from the process panel (``queue.alive`` of the host snapshot, the
    source the ops control uses).  When nothing is running, the existing
    ``resume`` action is enqueued exactly once — ``executor`` replaces the store
    enqueue in tests, and the host agent runs the row; no shell command is
    issued here.  When a mission is running, nothing is stopped and ``resume``
    is not called: the answer is ``STATUS_AFTER_RUNNING`` with
    ``BOUNDARY_STOP_ACTION`` as the offer the operator can choose instead.
    """
    run_next(store, mission_id, expected_version)
    if _queue_is_running(ops_store):
        return {
            "status": STATUS_AFTER_RUNNING,
            "mission_id": mission_id,
            "offer_action": BOUNDARY_STOP_ACTION,
        }
    command = (
        executor(RESUME_ACTION, {}) if executor is not None
        else _enqueue_action(ops_store, RESUME_ACTION, {})
    )
    return {
        "status": STATUS_RESUME_QUEUED,
        "mission_id": mission_id,
        "action": RESUME_ACTION,
        "command": command,
    }
