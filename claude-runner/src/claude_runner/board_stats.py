"""Mission and task statistics with run linkage (the board's mission card).

Links a mission's runs to its tasks through the event log's
``worker_run_id`` / ``reviewer_run_id`` rows and aggregates them with
``mission_stats.stats_for_runs`` (time, cost, speed, cache).  ``mission_card_stats``
adds the mission's status, per-task attempt count against the configured
``max_attempts_per_task`` and the last Reviewer score and reason;
``task_stats`` gives the same figures for one task and its attempts —
including the per-attempt time.  Run ids are collected the way the AHAWR
workflow's "Compute Mission Row" does: every ``_run_id`` field of the Live
State row and of the event log rows.  Nothing in ``mission_stats.py`` is
rewritten: it is only reused.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .board import BoardStore
from .board_order import task_sort_key
from .local_pricing import FIELDS
from .mission_stats import _run_ids_with_compactions, stats_for_runs, token_totals
from .runs import RunManager


def _mission_run_ids(board: BoardStore, state_key: str) -> list[str]:
    """The mission's run ids: the ``_run_id`` fields of the Live State row (a not
    started mission has none) and of every event log row (the id is kept only when
    it is a real runner id)."""
    ids: set[str] = set()
    live = board.get_live_state(state_key)
    rows = [live.model_dump()] if live is not None else []
    for row in rows + board.events(state_key):
        for key, value in row.items():
            if key.endswith("_run_id") and isinstance(value, str) and value.startswith("run_"):
                ids.add(value)
    return sorted(ids)


def _run_seconds(manager: RunManager, run_ids: list[str]) -> float:
    """The run time in seconds (a finished run from start to finish, a running one to now)."""
    from datetime import UTC

    total = 0.0
    moment = datetime.now(UTC)
    for run_id in run_ids:
        run = manager.store.get_run(run_id)
        if not run or not run.get("started_at"):
            continue
        start = datetime.fromisoformat(run["started_at"])
        end = datetime.fromisoformat(run["finished_at"]) if run.get("finished_at") else moment
        total += max(0.0, (end - start).total_seconds())
    return total


def _thinking_entries(manager: RunManager, run_id: str) -> list[dict[str, Any]]:
    """The ``thinking_state`` / ``thinking_switch`` entries of a run, ordered by ``t``
    (a run of an older runner version has none)."""
    entries, _ = manager.events.read(run_id, 0)
    switches = [
        e for e in entries
        if e.get("kind") in ("thinking_state", "thinking_switch")
        and str(e.get("thinking") or "").lower() in ("on", "off")
    ]
    switches.sort(key=lambda e: float(e.get("t") or 0))
    return switches


def _thinking_split(
    manager: RunManager, run_id: str
) -> tuple[int, int, int, int]:
    """Steps split by the thinking mode of each step (the last mode entry before the
    step started): (thinking on steps, thinking off steps, on tokens, off tokens)."""
    switches = _thinking_entries(manager, run_id)
    initial = str(switches[0].get("thinking") or "").lower() if switches else "on"
    on_steps = off_steps = on_tokens = off_tokens = 0
    entries, _ = manager.events.read(run_id, 0)
    for entry in entries:
        if entry.get("kind") != "step":
            continue
        started = float(entry.get("started") or entry.get("t") or 0)
        mode = initial
        for entry_ in switches:
            t = float(entry_.get("t") or 0)
            if t <= started:
                mode = str(entry_.get("thinking") or "").lower()
            else:
                break
        tokens = sum(int(entry.get(field) or 0) for field in FIELDS)
        if mode == "on":
            on_steps += 1
            on_tokens += tokens
        else:
            off_steps += 1
            off_tokens += tokens
    return on_steps, off_steps, on_tokens, off_tokens


def _cost_usd(manager: RunManager, run: dict[str, Any]) -> float:
    """The run's cost: for a local run the runner's own price (``local_pricing``,
    stored as ``details.local_cost``), otherwise the provider's reported cost."""
    if (run.get("provider") or "") == "local":
        details = run.get("details") or {}
        local = details.get("local_cost")
        if local is None and manager.local_pricing.enabled:
            local = manager.local_pricing.cost_from_events(
                manager.events.directory / f"{run['run_id']}.jsonl"
            )
        return float((local or {}).get("cost_usd") or 0.0)
    return float(run.get("cost_usd") or 0.0)


def _task_events(board: BoardStore, state_key: str, task_id: str) -> list[dict[str, Any]]:
    """The event log rows of one task, oldest first."""
    return [e for e in board.events(state_key) if e.get("task_id") == task_id]


def _task_run_ids(board: BoardStore, state_key: str, task_id: str) -> list[str]:
    """The task's run ids from its event rows (a run id may cover several rows)."""
    ids = {
        e[key]
        for e in _task_events(board, state_key, task_id)
        for key in ("worker_run_id", "reviewer_run_id")
        if isinstance(e.get(key), str) and e[key].startswith("run_")
    }
    return sorted(ids)


def _attempt_reference_runs(
    board: BoardStore, state_key: str, task_id: str, attempt: int
) -> list[str]:
    """The run ids one attempt references: its worker run and its reviewer run
    (the reviewer run may be shared with other attempts of the task)."""
    ids = {
        value
        for e in _task_events(board, state_key, task_id)
        if e.get("task_attempt") is not None and int(float(e["task_attempt"])) == attempt
        for value in (e.get("worker_run_id"), e.get("reviewer_run_id"))
        if isinstance(value, str) and value.startswith("run_")
    }
    return sorted(ids)


def _attempt_runs(
    board: BoardStore, state_key: str, task_id: str, attempt: int
) -> list[str]:
    """The attempt's counted runs: the referenced runs minus the runs already
    counted on earlier attempts (a shared reviewer run counts once, on its first
    attempt)."""
    ids = set(_attempt_reference_runs(board, state_key, task_id, attempt))
    for prior in range(0, attempt):
        ids.difference_update(_attempt_runs(board, state_key, task_id, prior))
    return sorted(ids)


def _attempt_times_seconds(
    board: BoardStore, manager: RunManager, state_key: str, task_id: str
) -> list[float]:
    """Seconds of work per attempt of the task: the summed run time of the
    attempt's counted runs (a running run counts to now).  Attempts without any
    counted run are left out."""
    attempts = sorted(
        {
            int(float(e["task_attempt"]))
            for e in _task_events(board, state_key, task_id)
            if e.get("task_attempt") is not None
        }
    )
    return [_run_seconds(manager, _attempt_runs(board, state_key, task_id, a)) for a in attempts]


def _max_attempts(board: BoardStore, mission_id: str) -> float | None:
    """The cap of attempts per task: the mission's queue override when set,
    else the hermes config's ``max_attempts_per_task``."""
    for entry in board.queue():
        if entry.mission_id == mission_id and entry.attempts_per_task is not None:
            return float(entry.attempts_per_task)
    config = board.hermes_config()
    value = config.get("max_attempts_per_task")
    if isinstance(value, int | float):
        return float(value)
    return None


def mission_card_stats(manager: RunManager, board: BoardStore, state_key: str) -> dict[str, Any]:
    """The mission card: the mission's runs aggregated (``stats_for_runs``), its
    status, the attempts per task against the configured maximum and the last
    Reviewer score and reason."""
    live = board.get_live_state(state_key)
    mission = next(
        (m for m in board.list_missions() if m.state_namespace == state_key), None
    )
    mission_id = mission.mission_id if mission is not None else state_key
    run_ids = _mission_run_ids(board, state_key)
    stats = stats_for_runs(manager, run_ids)
    tokens = token_totals(manager, run_ids)
    # the same runs stats_for_runs aggregates (the linked runs plus the
    # sessions' compactions, which cost time and money): cost is summed over
    # the compaction-expanded set, not the raw linked ids
    runs = _run_ids_with_compactions(manager, run_ids)
    local = manager.local_pricing.enabled
    on_steps = off_steps = on_tokens = off_tokens = 0
    for run in runs:
        if run is None:
            continue
        s, o, t_on, t_off = _thinking_split(manager, run["run_id"])
        on_steps += s
        off_steps += o
        on_tokens += t_on
        off_tokens += t_off
    cost_usd = round(sum(_cost_usd(manager, run) for run in runs if run is not None), 2)
    total_steps = on_steps + off_steps
    attempts = {
        view.task_id: len(
            {
                int(float(e["task_attempt"]))
                for e in _task_events(board, state_key, view.task_id)
                if e.get("task_attempt") is not None
            }
        )
        for view in sorted(board.task_views(state_key), key=lambda v: task_sort_key(v.task_id))
    }
    return {
        "state_key": state_key,
        "mission_id": mission_id,
        "status": live.status if live is not None else None,
        "runs": stats["runs"],
        "worker_hours": stats["worker_hours"],
        "reviewer_minutes": stats["reviewer_minutes"],
        "architect_minutes": stats["architect_minutes"],
        "input_tokens": tokens["input"],
        "output_tokens": tokens["output"],
        "cache_read_tokens": tokens["cache_read"],
        "cache_write_tokens": tokens["cache_write"],
        "cost_estimate_usd": stats["cost_estimate_usd"],
        "cost_real_usd": stats["cost_real_usd"],
        "cost_usd": cost_usd,
        "local_cost_usd": round(cost_usd, 2) if local else None,
        "tok_per_s": stats["tok_per_s"],
        "cache_ratio": stats["cache_ratio"],
        "compactions": stats["compactions"],
        "thinking_runs": on_steps,
        "thinking_off_runs": off_steps,
        "thinking_tokens": on_tokens,
        "thinking_off_tokens": off_tokens,
        "thinking_steps": total_steps,
        "max_attempts_per_task": _max_attempts(board, mission_id),
        "tasks": {
            view.task_id: {
                "attempts": attempts[view.task_id],
                "last_review_score": view.review_score,
                "last_review_reason": view.review_reason,
            }
            for view in sorted(board.task_views(state_key), key=lambda v: task_sort_key(v.task_id))
        },
    }


_SUMMARY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
SUMMARY_TTL_FINISHED = 600.0
SUMMARY_TTL_LIVE = 15.0


def _mission_figures(manager: RunManager, board: BoardStore, state_key: str) -> dict[str, Any]:
    """The mission's figures (attempts, hours, tokens, cache_ratio, cost, tok_s,
    thinking_share), or nulls when the manager is unreachable or raises."""
    try:
        card = mission_card_stats(manager, board, state_key)
        tasks = card["tasks"]
        thinking_steps = card["thinking_steps"]
        return {
            "attempts": sum(t["attempts"] for t in tasks.values()),
            "hours": card["worker_hours"],
            "tokens": card["input_tokens"] + card["output_tokens"],
            "cache_ratio": card["cache_ratio"],
            "cost": card["cost_usd"],
            "tok_s": card["tok_per_s"],
            "thinking_share": (
                card["thinking_runs"] / thinking_steps if thinking_steps else None
            ),
        }
    except Exception:
        return {
            "attempts": None,
            "hours": None,
            "tokens": None,
            "cache_ratio": None,
            "cost": None,
            "tok_s": None,
            "thinking_share": None,
        }


def mission_figures(
    manager: RunManager, board: BoardStore, state_key: str, force: bool = False
) -> dict[str, Any]:
    """The mission's figures, cached per mission: a finished mission's figures are
    kept for 600 s, a running one for 15 s (so a board with many cards does not
    re-read the runner for each refresh). When the manager is unreachable or
    raises, the figures are nulls (never an error)."""
    mission = next(
        (m for m in board.list_missions() if m.state_namespace == state_key), None
    )
    mission_id = mission.mission_id if mission is not None else state_key
    live = board.get_live_state(state_key)
    status = live.status if live is not None else None
    ttl = SUMMARY_TTL_FINISHED if status in ("completed", "failed") else SUMMARY_TTL_LIVE
    hit = _SUMMARY_CACHE.get(mission_id)
    if hit is not None and not force:
        age = time.monotonic() - hit[0]
        if age < ttl:
            return hit[1]
    figures = _mission_figures(manager, board, state_key)
    _SUMMARY_CACHE[mission_id] = (time.monotonic(), figures)
    return figures


_NULL_FIGURES: dict[str, Any] = {
    "attempts": None, "hours": None, "tokens": None, "cache_ratio": None,
    "cost": None, "tok_s": None, "thinking_share": None,
}
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()
_REFRESH_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="figures")


def _refresh_figures(manager: RunManager, board: BoardStore, state_key: str) -> None:
    try:
        mission_figures(manager, board, state_key, force=True)
    finally:
        with _REFRESH_LOCK:
            _REFRESHING.discard(state_key)


def cached_mission_figures(
    manager: RunManager, board: BoardStore, state_key: str, mission_id: str | None = None
) -> dict[str, Any]:
    """The figures for a board summary without waiting: a fresh or stale cached value
    at once, nulls when there is none; a stale or missing one is recomputed in the
    background (one at a time), so the first summary of a cold dashboard answers in
    a moment instead of reading the runs of every mission over HTTP."""
    if mission_id is None:
        mission = next(
            (m for m in board.list_missions() if m.state_namespace == state_key), None
        )
        mission_id = mission.mission_id if mission is not None else state_key
    live = board.get_live_state(state_key)
    status = live.status if live is not None else None
    ttl = SUMMARY_TTL_FINISHED if status in ("completed", "failed") else SUMMARY_TTL_LIVE
    hit = _SUMMARY_CACHE.get(mission_id)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    with _REFRESH_LOCK:
        if state_key not in _REFRESHING:
            _REFRESHING.add(state_key)
            _REFRESH_POOL.submit(_refresh_figures, manager, board, state_key)
    return hit[1] if hit is not None else dict(_NULL_FIGURES)


def task_stats(
    manager: RunManager, board: BoardStore, state_key: str, task_id: str
) -> dict[str, Any]:
    """One task: its runs aggregated, its attempts (against the mission's
    ``max_attempts_per_task``) and the time of each attempt, plus the last Reviewer
    score and reason."""
    board.get_live_state(state_key)
    mission = next(
        (m for m in board.list_missions() if m.state_namespace == state_key), None
    )
    mission_id = mission.mission_id if mission is not None else state_key
    run_ids = _task_run_ids(board, state_key, task_id)
    stats = stats_for_runs(manager, run_ids)
    tokens = token_totals(manager, run_ids)
    # the same runs stats_for_runs aggregates (compactions included)
    runs = _run_ids_with_compactions(manager, run_ids)
    on_steps = off_steps = on_tokens = off_tokens = 0
    for run in runs:
        if run is None:
            continue
        s, o, t_on, t_off = _thinking_split(manager, run["run_id"])
        on_steps += s
        off_steps += o
        on_tokens += t_on
        off_tokens += t_off
    cost_usd = round(sum(_cost_usd(manager, run) for run in runs if run is not None), 2)
    attempts = sorted(
        {
            int(float(e["task_attempt"]))
            for e in _task_events(board, state_key, task_id)
            if e.get("task_attempt") is not None
        }
    )
    view = next(
        (v for v in board.task_views(state_key) if v.task_id == task_id), None
    )
    return {
        "state_key": state_key,
        "mission_id": mission_id,
        "task_id": task_id,
        "status": view.status if view is not None else None,
        "runs": stats["runs"],
        "worker_hours": stats["worker_hours"],
        "reviewer_minutes": stats["reviewer_minutes"],
        "architect_minutes": stats["architect_minutes"],
        "input_tokens": tokens["input"],
        "output_tokens": tokens["output"],
        "cache_read_tokens": tokens["cache_read"],
        "cache_write_tokens": tokens["cache_write"],
        "cost_estimate_usd": stats["cost_estimate_usd"],
        "cost_real_usd": stats["cost_real_usd"],
        "cost_usd": cost_usd,
        "tok_per_s": stats["tok_per_s"],
        "cache_ratio": stats["cache_ratio"],
        "compactions": stats["compactions"],
        "thinking_runs": on_steps,
        "thinking_off_runs": off_steps,
        "thinking_tokens": on_tokens,
        "thinking_off_tokens": off_tokens,
        "thinking_steps": on_steps + off_steps,
        "max_attempts_per_task": _max_attempts(board, mission_id),
        "attempts": len(attempts),
        "attempt_seconds": [round(seconds, 1) for seconds in _attempt_times_seconds(
            board, manager, state_key, task_id
        )],
        "attempts_detail": [
            {
                "attempt": attempt,
                "seconds": round(
                    _run_seconds(manager, _attempt_runs(board, state_key, task_id, attempt)), 1
                ),
                "runs": _attempt_reference_runs(board, state_key, task_id, attempt),
            }
            for attempt in attempts
        ],
        "last_review_score": view.review_score if view is not None else None,
        "last_review_reason": view.review_reason if view is not None else None,
    }


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def mission_timeline(
    manager: RunManager | None, board: BoardStore, state_key: str
) -> dict[str, list[dict[str, Any]]]:
    """The mission's accepted reviews (``review_parsed`` rows with ``pass``) and its
    obstacles: a task rejected three times or more, a run that timed out or was
    interrupted (read through the manager).  Empty lists when data or manager is missing."""
    try:
        events = board.events(state_key)
        accepted: list[dict[str, Any]] = []
        rejections: dict[str, list[dict[str, Any]]] = {}
        run_task: dict[str, str | None] = {}
        for e in events:
            task = e.get("task_id")
            if e.get("event_type") == "review_parsed":
                # some rows carry only review_reason; either column carries the verdict
                verdict = e.get("review_status") or e.get("review_reason")
                if verdict == "pass":
                    accepted.append({"task": task, "ts": _iso(e.get("created_at"))})
                elif verdict == "needs_changes":
                    rejections.setdefault(task, []).append(e)
            for key in ("worker_run_id", "reviewer_run_id"):
                run_id = e.get(key)
                if isinstance(run_id, str) and run_id.startswith("run_"):
                    run_task.setdefault(run_id, task)
        obstacles: list[dict[str, Any]] = []
        for task, rows in rejections.items():
            if len(rows) >= 3:
                last = rows[-1]
                obstacles.append(
                    {
                        "kind": "rejected_3x",
                        "task": task,
                        "ts": _iso(last.get("created_at")),
                        "detail": f"rejected {len(rows)} times, last reason: "
                        + (last.get("review_reason") or ""),
                    }
                )
        for run_id, task in run_task.items():
            run = manager.store.get_run(run_id)
            if not run:
                continue
            if run.get("status") == "interrupted":
                kind = "interrupted"
            elif run.get("error_code") == "timeout":
                kind = "timeout"
            else:
                continue
            obstacles.append({
                "kind": kind,
                "task": task,
                "run_id": run_id,
                "ts": run.get("finished_at") or run.get("started_at"),
                "detail": run.get("error_message") or "",
            })
        return {"accepted": accepted, "obstacles": obstacles}
    except Exception:
        return {"accepted": [], "obstacles": []}
