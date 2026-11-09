"""Run-based bench figures (pure): every run-derived number of the bench table for any
scope -- a mission, a task, an attempt, a project.

The formulas are the ones of ``data-tables/ops/bench_series_table.py``'s ``mission_row``:
closed tasks out of the planned ones, the average final and first-attempt Reviewer
scores, the extra attempts, Worker hours and minutes per task, steps and steps per
task, output tokens and tokens per step, tok/s, the cache share, the generation share
of Worker time, compactions, Reviewer minutes and the cost estimate at the Sonnet
price. Time, speed, cache, compaction and Reviewer figures come from
``mission_stats.stats_for_runs``; the run ids come from the ``board_stats`` linkage. A
ratio that cannot be computed is ``None``, with its reason in the ``reasons`` key;
sums over an empty scope are a real 0.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .board import BoardStore
from .board_stats import (
    _attempt_runs,
    _mission_project,
    _mission_run_ids,
    _task_events,
    _task_run_ids,
)
from .mission_stats import _run_ids_with_compactions, stats_for_runs, token_totals
from .runs import RunManager

# input, output, cache read, cache write rates in USD per 1M tokens, used only when the
# runner has no Sonnet profile in its local pricing
SONNET_PRICE_USD_PER_MILLION_TOKENS = (2.0, 10.0, 0.2, 4.0)
SECONDS_PER_HOUR = 3600
# a project breakdown reads every mission of the project (about 2-3 s each for a big one): the
# per-mission figures are kept this long, so a second open of the popup is not another minute
MISSION_FIGURES_TTL_SECONDS = 600.0
MAX_CACHED_MISSIONS = 500
_FIGURES_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_FIGURES_LOCK = threading.Lock()


def sonnet_rates_usd(manager: RunManager) -> tuple[float, float, float, float]:
    """The Sonnet rates of the runner's local pricing, else the named constant."""
    # a remote manager (the dashboard container) has a stub pricing without profiles
    profiles = getattr(manager.local_pricing, "profiles", {})
    return profiles.get("sonnet") or SONNET_PRICE_USD_PER_MILLION_TOKENS


def mission_reviews(board: BoardStore, state_key: str) -> list[dict[str, Any]]:
    """The Reviewer rows of the mission: every ``review_parsed`` event of the state."""
    return [row for row in board.events(state_key) if row.get("event_type") == "review_parsed"]


def _reviews_by_task(
    reviews: list[dict[str, Any]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Review rows grouped by (state key, task), oldest attempt first. A row without
    ``task_id`` is grouped under its task index. The state key is part of the key so the
    missions of one project, whose task ids (T001) repeat, keep their tasks apart."""
    by_task: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in reviews:
        task_id = str(row.get("task_id") if row.get("task_id") else row.get("task_index") or "")
        by_task.setdefault((str(row.get("state_key") or ""), task_id), []).append(row)
    for rows in by_task.values():
        rows.sort(key=lambda row: int(float(row.get("task_attempt") or 0)))
    return by_task


def _worker_step_totals(manager: RunManager, run_ids: list[str]) -> tuple[int, int, int]:
    """Worker steps, their output tokens and their generation time in ms, read from the
    run activity log (the same ``kind == "step"`` entries the run views show)."""
    steps = output_tokens = generation_ms = 0
    for run in _run_ids_with_compactions(manager, run_ids):
        if (run.get("role") or "") != "worker":
            continue
        entries, _ = manager.events.read(run["run_id"], 0)
        for entry in entries:
            if entry.get("kind") != "step":
                continue
            steps += 1
            output_tokens += int(entry.get("output_tokens") or 0)
            generation_ms += int(entry.get("generation_ms") or 0)
    return steps, output_tokens, generation_ms


def run_figures(
    manager: RunManager,
    run_ids: list[str],
    reviews: list[dict[str, Any]],
    planned_tasks: int | None = None,
) -> dict[str, Any]:
    """Every run-based bench metric for the given runs and Reviewer rows."""
    stats = stats_for_runs(manager, run_ids)
    tokens = token_totals(manager, run_ids)
    steps, output_tokens, generation_ms = _worker_step_totals(manager, run_ids)
    generation_seconds = generation_ms / 1000
    by_task = _reviews_by_task(reviews)
    task_count = len(by_task)
    reasons: dict[str, str] = {}

    def none_with(name: str, reason: str) -> float | None:
        reasons[name] = reason
        return None

    def ratio(name: str, numerator: float, denominator: float, reason: str) -> float | None:
        if denominator <= 0:
            return none_with(name, reason)
        return round(numerator / denominator, 1)

    def average(name: str, scores: list[float]) -> float | None:
        if not scores:
            return none_with(name, "no review scores")
        return round(sum(scores) / len(scores), 1)

    final_scores = [
        float(row["review_score"])
        for row in (rows[-1] for rows in by_task.values())
        if row.get("review_score") is not None
    ]
    first_scores = [
        float(row["review_score"])
        for row in (rows[0] for rows in by_task.values())
        if row.get("review_score") is not None
    ]
    closed_tasks = sum(1 for rows in by_task.values() if rows[-1].get("review_status") == "pass")
    planned = planned_tasks if planned_tasks is not None else task_count or none_with(
        "planned_tasks", "no plan and no review rows"
    )
    input_rate, output_rate, cache_read_rate, cache_write_rate = sonnet_rates_usd(manager)

    return {
        "closed_tasks": closed_tasks,
        "planned_tasks": planned,
        "avg_final_review_score": average("avg_final_review_score", final_scores),
        "avg_first_review_score": average("avg_first_review_score", first_scores),
        "extra_attempts": (
            sum(len(rows) for rows in by_task.values()) - task_count
            if task_count
            else none_with("extra_attempts", "no reviewed tasks")
        ),
        "worker_hours": stats["worker_hours"],
        "minutes_per_task": ratio(
            "minutes_per_task", stats["worker_hours"] * 60, task_count, "no reviewed tasks"
        ),
        "steps": steps,
        "steps_per_task": ratio("steps_per_task", steps, task_count, "no reviewed tasks"),
        "output_tokens": output_tokens,
        "tokens_per_step": ratio("tokens_per_step", output_tokens, steps, "no Worker steps"),
        "tok_per_s": (
            stats["tok_per_s"]
            if generation_ms
            else none_with("tok_per_s", "no Worker steps with measured generation time")
        ),
        "cache_share_percent": (
            round(100 * stats["cache_ratio"], 1)
            if tokens["input"] + tokens["cache_read"]
            else none_with("cache_share_percent", "no step input tokens")
        ),
        "generation_share_percent": ratio(
            "generation_share_percent",
            100 * generation_seconds,
            stats["worker_hours"] * SECONDS_PER_HOUR,
            "no Worker time",
        ),
        "compactions": stats["compactions"],
        "reviewer_minutes": stats["reviewer_minutes"],
        # shortcut: every run of the scope is priced at the Sonnet profile, as the bench
        # table does; upgrade to per-run profiles (board_stats._cost_usd) when a scope
        # mixes priced roles
        "cost_usd": round(
            (
                tokens["input"] * input_rate
                + tokens["output"] * output_rate
                + tokens["cache_read"] * cache_read_rate
                + tokens["cache_write"] * cache_write_rate
            )
            / 1_000_000,
            2,
        ),
        "reasons": reasons,
    }


def _task_rows(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-task bench rows: attempts, the first and the final Reviewer score, passed."""
    return [
        {
            "task_id": task_id,
            "attempts": len(rows),
            "first_review_score": rows[0].get("review_score"),
            "final_review_score": rows[-1].get("review_score"),
            "passed": rows[-1].get("review_status") == "pass",
        }
        for (_, task_id), rows in _reviews_by_task(reviews).items()
    ]


def mission_figures(manager: RunManager, board: BoardStore, state_key: str) -> dict[str, Any]:
    """The bench figures of one mission, with the per-task rows of its reviewed tasks."""
    reviews = mission_reviews(board, state_key)
    live = board.get_live_state(state_key)
    planned_tasks = len(live.plan["tasks"]) if live is not None and live.plan else None
    figures = run_figures(manager, _mission_run_ids(board, state_key), reviews, planned_tasks)
    figures["tasks"] = _task_rows(reviews)
    return figures


def _cached_mission_figures(
    manager: RunManager, board: BoardStore, state_key: str
) -> dict[str, Any]:
    """``mission_figures`` kept for ``MISSION_FIGURES_TTL_SECONDS``; the cache holds at most
    ``MAX_CACHED_MISSIONS`` missions (the oldest entry goes first)."""
    now = time.monotonic()
    with _FIGURES_LOCK:
        hit = _FIGURES_CACHE.get(state_key)
        if hit is not None and now - hit[0] < MISSION_FIGURES_TTL_SECONDS:
            return hit[1]
    figures = mission_figures(manager, board, state_key)
    with _FIGURES_LOCK:
        if len(_FIGURES_CACHE) >= MAX_CACHED_MISSIONS:
            _FIGURES_CACHE.pop(min(_FIGURES_CACHE, key=lambda key: _FIGURES_CACHE[key][0]))
        _FIGURES_CACHE[state_key] = (time.monotonic(), figures)
    return figures


def project_figures(
    manager: RunManager, board: BoardStore, project_key: str
) -> dict[str, Any]:
    """The bench figures of a project: the same metrics as ``run_figures`` over every
    run of every mission of the project, plus the per-mission breakdown.

    The grouping is the rule the Projects view and the store already use, not a new
    one: ``_mission_project`` (board_stats.py:57-70) matches the mission's working
    directory against the stored rules (glob with ``*``, ordered by ``position``, the
    first match wins; otherwise the last folder of the working directory), and
    ``groupByProject`` (board_view_projects.js:52-61) groups the missions by the key
    that rule returns. A project key with no missions is a valid empty scope.
    """
    rules, _ = board.get_project_rules()
    missions = [
        (mission.mission_id, mission.state_namespace)
        for mission in board.list_missions()
        if _mission_project(mission.working_directory, rules)["key"] == project_key
    ]
    breakdown: list[dict[str, Any]] = [
        {
            "mission_id": mission_id,
            "state_key": state_key,
            "figures": _cached_mission_figures(manager, board, state_key),
        }
        for mission_id, state_key in missions
    ]
    run_ids = sorted(
        {
            run_id
            for _, state_key in missions
            for run_id in _mission_run_ids(board, state_key)
        }
    )
    # the state key on every row is what keeps the tasks of two missions of the same
    # project apart: both missions have a T001 review row
    reviews = [
        {**row, "state_key": state_key}
        for _, state_key in missions
        for row in mission_reviews(board, state_key)
    ]
    planned = [row["figures"]["planned_tasks"] for row in breakdown]
    planned_tasks = (
        None if all(value is None for value in planned) else sum(value or 0 for value in planned)
    )
    # Aggregate from the project's runs, never by averaging the mission figures. The
    # bench reference does exactly this inside one mission: bench_series_table.py:53-54
    # "data = X.in_runner("_pub_helper.py", {m: X.run_ids(m)})[m]" and
    # "s, w = data["stats"], data["by_role"].get("worker", {})", then line 64
    # "hours=s["worker_hours"], tok_s=s["tok_per_s"], steps=w.get("steps", 0),
    # cost=s["cost_estimate_usd"]" -- every figure off the mission's own runs. The
    # reference has no project rows, so there is nothing there to average.
    figures = run_figures(manager, run_ids, reviews, planned_tasks)
    figures["missions"] = breakdown
    return figures


def task_figures(
    manager: RunManager, board: BoardStore, state_key: str, task_id: str
) -> dict[str, Any]:
    """The bench figures of one task, with one row per attempt of that task."""
    reviews = [row for row in mission_reviews(board, state_key) if row.get("task_id") == task_id]
    figures = run_figures(manager, _task_run_ids(board, state_key, task_id), reviews, 1)
    attempts = sorted(
        {
            int(float(row["task_attempt"]))
            for row in _task_events(board, state_key, task_id)
            if row.get("task_attempt") is not None
        }
    )
    figures["attempts"] = [
        {
            "attempt": attempt,
            "runs": attempt_run_ids,
            "figures": run_figures(
                manager,
                attempt_run_ids,
                [row for row in reviews if int(float(row.get("task_attempt") or 0)) == attempt],
                1,
            ),
        }
        for attempt, attempt_run_ids in (
            (attempt, _attempt_runs(board, state_key, task_id, attempt)) for attempt in attempts
        )
    ]
    return figures