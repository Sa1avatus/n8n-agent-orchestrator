"""Numbers for a set of runs (a mission's runs), for the ``mission_results`` table in n8n.

``POST /v1/stats/runs`` takes the run ids the AHAWR history table holds and answers with the
time, cost, speed and cache figures the dashboard would show, so the workflow does not read the
events itself. Compaction runs of the same sessions are counted too (they cost time and money).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .runs import RunManager

ROLES = ("worker", "reviewer", "architect")


def _minutes(run: dict[str, Any]) -> float:
    start, end = run.get("started_at"), run.get("finished_at")
    if not start or not end:
        return 0.0
    try:
        delta = datetime.fromisoformat(end) - datetime.fromisoformat(start)
        return max(0.0, delta.total_seconds() / 60)
    except ValueError:
        return 0.0


def _run_ids_with_compactions(manager: RunManager, run_ids: list[str]) -> list[dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    sessions: set[str] = set()
    for run_id in run_ids:
        run = manager.store.get_run(run_id)
        if run:
            runs[run_id] = run
            if run.get("session_id"):
                sessions.add(run["session_id"])
    for session_id in sessions:
        for run in manager.store.list_runs(500, session_id):
            if run.get("kind") == "compact":
                runs.setdefault(run["run_id"], run)
    return list(runs.values())


def token_totals(manager: RunManager, run_ids: list[str]) -> dict[str, int]:
    """The raw token sums of the runs' steps (``input``, ``output``,
    ``cache_read`` and ``cache_write``), the figures the mission card shows
    alongside the derived speed and cache ratio."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    runs = _run_ids_with_compactions(manager, run_ids)
    for run in runs:
        entries, _ = manager.events.read(run["run_id"], 0)
        for entry in entries:
            if entry.get("kind") != "step":
                continue
            totals["input"] += int(entry.get("input_tokens") or 0)
            totals["output"] += int(entry.get("output_tokens") or 0)
            totals["cache_read"] += int(entry.get("cache_read_tokens") or 0)
            totals["cache_write"] += int(entry.get("cache_write_tokens") or 0)
    return totals


def _tok_per_s(out_tokens: int, gen_ms: int) -> float:
    return round(out_tokens / (gen_ms / 1000), 1) if gen_ms else 0.0


def stats_for_runs(manager: RunManager, run_ids: list[str]) -> dict[str, Any]:
    minutes = dict.fromkeys(ROLES, 0.0)
    cost = cost_real = 0.0
    uncached = cached = compactions = 0
    # output tokens and generation ms of the worker steps
    speed = {"local": [0, 0], "cloud": [0, 0]}
    runs = _run_ids_with_compactions(manager, run_ids)
    for run in runs:
        role = run.get("role") or ""
        if role in minutes:
            minutes[role] += _minutes(run)
        value = float(run.get("cost_usd") or 0.0)
        cost += value
        if (run.get("provider") or "") != "local":
            cost_real += value
        side = "local" if (run.get("provider") or "") == "local" else "cloud"
        entries, _ = manager.events.read(run["run_id"], 0)
        for entry in entries:
            kind = entry.get("kind")
            if kind == "compact":
                compactions += 1
            elif kind == "step":
                uncached += int(entry.get("input_tokens") or 0)
                cached += int(entry.get("cache_read_tokens") or 0)
                if role == "worker" and entry.get(
                    "generation_ms"
                ):  # steps the proxy sent at once have no measured speed
                    speed[side][0] += int(entry.get("output_tokens") or 0)
                    speed[side][1] += int(entry.get("generation_ms") or 0)
    return {
        "runs": len(runs),
        "worker_hours": round(minutes["worker"] / 60, 2),
        "reviewer_minutes": round(minutes["reviewer"], 1),
        "architect_minutes": round(minutes["architect"], 1),
        "cost_estimate_usd": round(cost, 2),
        "cost_real_usd": round(cost_real, 2),
        "tok_per_s": _tok_per_s(
            speed["local"][0] + speed["cloud"][0], speed["local"][1] + speed["cloud"][1]
        ),
        "tok_per_s_local": _tok_per_s(*speed["local"]),
        "tok_per_s_cloud": _tok_per_s(*speed["cloud"]),
        "cache_ratio": round(cached / (cached + uncached), 3) if cached + uncached else 0.0,
        "compactions": compactions,
    }
