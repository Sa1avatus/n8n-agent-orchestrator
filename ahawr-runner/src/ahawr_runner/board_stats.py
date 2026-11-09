"""Mission and task statistics with run linkage (the board's mission card).

Links a mission's runs to its tasks through the event log's
``worker_run_id`` / ``reviewer_run_id`` rows and aggregates them with
``mission_stats.stats_for_runs`` (time, cost, speed, cache).  ``mission_card_stats``
adds the mission's status, per-task attempt count against the configured
``max_attempts_per_task`` and the last Reviewer score and reason;
``task_stats`` gives the same figures for one task and its attempts —
including the per-attempt time.  ``_mission_project`` is the project grouping
the Projects view and the project stats route use (the stored rule whose glob
matches the working directory, otherwise its last folder).  Run ids are
collected the way the AHAWR
workflow's "Compute Mission Row" does: every ``_run_id`` field of the Live
State row and of the event log rows.  Nothing in ``mission_stats.py`` is
rewritten: it is only reused.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .board import BoardStore, Mission
from .board_engine import ENGINE_COLUMN, LEGACY_COLUMNS, flatten, parse_engine
from .board_lessons import InMemoryLessonStore, PgLessonStore
from .board_order import task_sort_key
from .board_ready import effective_roles
from .board_think import variant_for_task
from .local_pricing import FIELDS
from .mission_stats import _minutes, _run_ids_with_compactions, stats_for_runs, token_totals
from .roles_config import resolve
from .runs import RunManager

ROLE_LABELS = {"worker": "Worker", "reviewer": "Reviewer", "architect": "Architect", "qa": "QA"}


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


def _mission_project(
    working_directory: str | None, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """The mission's project: the working directory matched against the stored
    project rules (glob with ``*``, ordered by position); the first matching
    rule wins. Otherwise the default rule: the working directory, shown by its
    last folder name."""
    normalized = (working_directory or "").replace("\\", "/").rstrip("/")
    for rule in sorted(rules, key=lambda r: r.get("position", 0)):
        pattern = str(rule.get("pattern", "")).replace("\\", "/").rstrip("/")
        if normalized and pattern and fnmatch.fnmatch(normalized, pattern):
            name = str(rule.get("name", ""))
            return {"key": name, "name": name, "rule": rule["pattern"]}
    last = normalized.rsplit("/", 1)[-1] if normalized else ""
    return {"key": last, "name": last, "rule": None}


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


def configured_value(row: dict[str, Any], key: str) -> Any:
    """The engine core's value for ``key`` (``engine_json`` wins, then the legacy
    column); None when neither sets it, so the core's default is not filled in."""
    engine = row.get(ENGINE_COLUMN)
    accepted, _ = parse_engine(engine)
    if key not in accepted and row.get(LEGACY_COLUMNS[key]) in (None, ""):
        return None
    return flatten(engine, row)[key]


def _max_attempts(board: BoardStore, mission_id: str) -> float | None:
    """The cap of attempts per task: the mission's queue override when set,
    else the hermes config's ``max_attempts_per_task``."""
    for entry in board.queue():
        if entry.mission_id == mission_id and entry.attempts_per_task is not None:
            return float(entry.attempts_per_task)
    try:
        value = configured_value(board.hermes_config(), "max_attempts_per_task")
    except ValueError:
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _role_model(roles: dict[str, Any], role: str, legacy: dict[str, Any]) -> str | None:
    try:
        resolved = resolve(roles, role, 1, legacy)
    except ValueError:
        return None
    return f"{resolved.provider}/{resolved.model}"


def _role_rows(
    manager: RunManager, board: BoardStore, mission_id: str, runs: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Runs, minutes, cost and the configured model per role.  An absent or invalid
    roles_json counts as no roles, so each row falls back to the defaults."""
    try:
        roles = effective_roles(board, mission_id)
    except ValueError:
        roles = {}
    legacy = board.hermes_config()
    rows: dict[str, dict[str, Any]] = {}
    for role, label in ROLE_LABELS.items():
        role_runs = [run for run in runs if run.get("role") == role]
        rows[role] = {
            "label": label,
            "runs": len(role_runs),
            "minutes": round(sum(_minutes(run) for run in role_runs), 2),
            "cost_usd": round(sum(_cost_usd(manager, run) for run in role_runs), 2),
            "model": _role_model(roles, role, legacy),
        }
    return rows


def mission_card_stats(
    manager: RunManager, board: BoardStore, state_key: str, mission_id: str | None = None
) -> dict[str, Any]:
    """The mission card: the mission's runs aggregated (``stats_for_runs``), its
    status, the attempts per task against the configured maximum and the last
    Reviewer score and reason."""
    live = board.get_live_state(state_key)
    if mission_id is None:
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
        "tok_per_s_local": stats["tok_per_s_local"],
        "tok_per_s_cloud": stats["tok_per_s_cloud"],
        "cache_ratio": stats["cache_ratio"],
        "compactions": stats["compactions"],
        "thinking_runs": on_steps,
        "thinking_off_runs": off_steps,
        "thinking_tokens": on_tokens,
        "thinking_off_tokens": off_tokens,
        "thinking_steps": total_steps,
        "max_attempts_per_task": _max_attempts(board, mission_id),
        "roles": _role_rows(manager, board, mission_id, runs),
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
SUMMARY_TTL_FINISHED = 21600.0  # 6 h: a finished mission's runs do not change
SUMMARY_TTL_LIVE = 15.0
RETRY_FAILED_SECONDS = 30.0
FIGURES_WORKERS = 3  # more workers fill a cold cache sooner; the runner reads runs in parallel


def _mission_figures(
    manager: RunManager, board: BoardStore, state_key: str, mission_id: str | None = None
) -> dict[str, Any]:
    """The mission's figures (attempts, hours, tokens, cache_ratio, cost, tok_s,
    thinking_share), or nulls when the manager is unreachable or raises."""
    try:
        card = mission_card_stats(manager, board, state_key, mission_id)
        tasks = card["tasks"]
        thinking_steps = card["thinking_steps"]
        return {
            "attempts": sum(t["attempts"] for t in tasks.values()),
            "hours": card["worker_hours"],
            "tokens": card["input_tokens"] + card["output_tokens"],
            "cache_ratio": card["cache_ratio"],
            "cost": card["cost_usd"],
            "tok_s": card["tok_per_s"],
            "tok_s_local": card["tok_per_s_local"],
            "tok_s_cloud": card["tok_per_s_cloud"],
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
            "tok_s_local": None,
            "tok_s_cloud": None,
            "thinking_share": None,
        }


def mission_figures(
    manager: RunManager,
    board: BoardStore,
    state_key: str,
    force: bool = False,
    mission_id: str | None = None,
) -> dict[str, Any]:
    """The mission's figures, cached per mission: a finished mission's figures are
    kept for 6 h, a running one for 15 s (so a board with many cards does not
    re-read the runner for each refresh). When the manager is unreachable or
    raises, the figures are nulls (never an error)."""
    if mission_id is None:
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
    figures = _mission_figures(manager, board, state_key, mission_id)
    stored_at = time.monotonic()
    if figures["hours"] is None:
        # a failed read (runner busy) keeps the last good figures, not 0 h, and is retried soon
        if hit is not None and hit[1]["hours"] is not None:
            figures = hit[1]
        stored_at -= ttl - RETRY_FAILED_SECONDS
    _SUMMARY_CACHE[mission_id] = (stored_at, figures)
    return figures


_NULL_FIGURES: dict[str, Any] = {
    "attempts": None, "hours": None, "tokens": None, "cache_ratio": None,
    "cost": None, "tok_s": None, "thinking_share": None,
}
_REFRESHING: set[str] = set()
_REFRESH_LOCK = threading.Lock()
_REFRESH_POOL = ThreadPoolExecutor(max_workers=FIGURES_WORKERS, thread_name_prefix="figures")


def _refresh_figures(
    manager: RunManager, board: BoardStore, state_key: str, mission_id: str
) -> None:
    try:
        mission_figures(manager, board, state_key, force=True, mission_id=mission_id)
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
            _REFRESH_POOL.submit(_refresh_figures, manager, board, state_key, mission_id)
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
        "tok_per_s_local": stats["tok_per_s_local"],
        "tok_per_s_cloud": stats["tok_per_s_cloud"],
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


LESSON_VARIANT = "lessons"
CONTROL_VARIANT = "off"


def lesson_block_shown(config: dict[str, Any], task_index: int) -> bool:
    """Whether task ``task_index`` (1-based) gets the lessons block.  With
    ``lessons_holdout_enabled`` on, ``variant_for_task`` splits the tasks: odd ones
    get the block, even ones the ``off`` control.  Off, every task gets it."""
    if config.get("lessons_holdout_enabled") is not True:
        return True
    # shortcut: fixed period 2, lessons_holdout_every unread; add it when a period is needed
    policy = {
        "variants": [
            {"name": LESSON_VARIANT, "weight": 1},
            {"name": CONTROL_VARIANT, "weight": 1},
        ]
    }
    return variant_for_task(policy, task_index) == LESSON_VARIANT


def _attempts_and_verdicts(
    board: BoardStore, state_key: str
) -> tuple[dict[int, set[int]], dict[int, Any], set[int]]:
    """Per task index: the attempt numbers seen, the ``review_status`` of the first
    attempt (its lowest ``task_attempt``) when a review was parsed, and the set of
    tasks with a parsed ``needs_changes`` review on any attempt."""
    rows = [
        e for e in board.events(state_key)
        if e.get("task_index") is not None and e.get("task_attempt") is not None
    ]
    attempts: dict[int, set[int]] = {}
    for e in rows:
        attempts.setdefault(int(float(e["task_index"])), set()).add(int(float(e["task_attempt"])))
    verdicts: dict[int, Any] = {}
    flagged: set[int] = set()
    for e in rows:
        if e.get("event_type") != "review_parsed":
            continue
        index = int(float(e["task_index"]))
        if e.get("review_status") == "needs_changes":
            flagged.add(index)
        if int(float(e["task_attempt"])) == min(attempts[index]):
            verdicts[index] = e.get("review_status")
    return attempts, verdicts, flagged


@dataclass(frozen=True)
class _TaskFigure:
    attempts: int
    first_passed: bool | None  # None: no review was parsed for the first attempt
    needs_changes: bool
    with_lesson: bool
    before_first_lesson: bool


def _mission_tasks(
    board: BoardStore,
    lessons: InMemoryLessonStore | PgLessonStore,
    state_key: str,
    mission_id: str,
) -> list[_TaskFigure]:
    attempts, verdicts, flagged = _attempts_and_verdicts(board, state_key)
    with_block = lessons.task_indexes_with_lessons(mission_id)
    shown = [index for index in attempts if index in with_block]
    first_shown = min(shown, default=None)
    return [
        _TaskFigure(
            attempts=len(attempts[index]),
            first_passed=verdicts[index] == "pass" if index in verdicts else None,
            needs_changes=index in flagged,
            with_lesson=index in with_block,
            before_first_lesson=first_shown is None or index < first_shown,
        )
        for index in sorted(attempts)
    ]


def _lesson_figures(tasks: list[_TaskFigure]) -> dict[str, Any]:
    with_lesson = [t for t in tasks if t.with_lesson]
    without_lesson = [t for t in tasks if not t.with_lesson]
    before = [t for t in tasks if t.before_first_lesson]
    after = [t for t in tasks if not t.before_first_lesson]

    def pass_rate(group: list[_TaskFigure]) -> float | None:
        judged = [t.first_passed for t in group if t.first_passed is not None]
        return sum(judged) / len(judged) if judged else None

    def mean_attempts(group: list[_TaskFigure]) -> float | None:
        return sum(t.attempts for t in group) / len(group) if group else None

    return {
        "needs_changes_share": (
            sum(t.needs_changes for t in tasks) / len(tasks) if tasks else None
        ),
        "mean_attempts_per_task": mean_attempts(tasks),
        "with_lessons": {
            "tasks": len(with_lesson), "first_attempt_pass_rate": pass_rate(with_lesson),
        },
        "without_lessons": {
            "tasks": len(without_lesson), "first_attempt_pass_rate": pass_rate(without_lesson),
        },
        "attempts_per_task": {
            "before_first_lesson": mean_attempts(before),
            "after_first_lesson": mean_attempts(after),
        },
    }


def lessons_stats(
    board: BoardStore,
    lessons: InMemoryLessonStore | PgLessonStore,
    state_key: str,
    mission_id: str,
) -> dict[str, Any]:
    """The mission's lesson figures (see ``_lesson_figures``) and its lesson counts by
    status.  A task with no block is one that no lesson was shown to."""
    by_status = Counter(lesson.status for lesson in lessons.list(mission_id))
    return {
        "state_key": state_key,
        "mission_id": mission_id,
        "lessons": {"active": by_status["active"], "retired": by_status["retired"]},
        **_lesson_figures(_mission_tasks(board, lessons, state_key, mission_id)),
    }


def lessons_stats_overall(
    board: BoardStore,
    lessons: InMemoryLessonStore | PgLessonStore,
    missions: list[Mission],
) -> dict[str, Any]:
    """The lesson figures pooled over the tasks of ``missions``; zeros and nulls for none."""
    tasks = [
        task
        for mission in missions
        for task in _mission_tasks(board, lessons, mission.state_namespace, mission.mission_id)
    ]
    return {"missions": len(missions), **_lesson_figures(tasks)}


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
