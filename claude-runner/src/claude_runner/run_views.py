"""Read-only run views on claude-runner's API (``GET /v1/runs``, ``/v1/runs/{id}/events``):
the run list and each run's activity log, read by the dashboard."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from .mission_stats import stats_for_runs
from .runs import RunManager, RunnerError
from .titles import title_of


def summary(run: dict[str, Any], manager: RunManager) -> dict[str, Any]:
    details = run.get("details") or {}
    usage = details.get("usage") or {}
    active = run["status"] in ("queued", "running")
    return {
        "run_id": run["run_id"],
        "kind": run.get("kind", "run"),
        "title": title_of(run.get("input") or ""),
        "status": run["status"],
        "role": run["role"],
        "model": details.get("model") or run["model"],
        "requested_model": run["model"],
        "provider": run.get("provider") or "",
        "session_id": run["session_id"],
        "claude_session_id": run["claude_session_id"],
        "cwd": run["cwd"],
        "created_at": run["created_at"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "num_turns": run["num_turns"],
        "cost_usd": run["cost_usd"],
        "context_tokens": run["context_tokens"],
        "tokens": {
            "input": usage.get("input_tokens"),
            "output": usage.get("output_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
        },
        "updated_at": run["started_at"] or run["created_at"],
        "source": "claude-code",
        "error": {"code": run["error_code"], "message": run["error_message"]}
        if run["error_code"] or run["error_message"]
        else None,
        "live": active and manager.events.is_live(run["run_id"]),
    }


class StatsBody(BaseModel):
    run_ids: list[str] = Field(default_factory=list, max_length=2000)


def build_router(get_manager: Callable[[], RunManager]) -> APIRouter:
    router = APIRouter()

    @router.get("/runs")
    async def list_runs(
        limit: int = Query(50, ge=1, le=500),
        session_id: str = "",
        role: str = "",
        status: str = "",
    ) -> dict[str, Any]:
        manager = get_manager()
        runs = manager.store.list_runs(limit, session_id, role, status)
        return {"runs": [summary(run, manager) for run in runs]}

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str, after: int = Query(0, ge=0)) -> dict[str, Any]:
        manager = get_manager()
        run = manager.store.get_run(run_id)
        if not run:
            raise RunnerError("run_not_found", f"Run {run_id} not found.", 404)
        entries, live = manager.events.read(run_id, after)
        return {
            "run": summary(run, manager),
            "events": entries,
            "next": after + len(entries),
            "live": live,
        }

    @router.post("/stats/runs")
    async def run_stats(body: StatsBody) -> dict[str, Any]:
        """Time, cost, speed and cache figures of the given runs and their sessions' compactions."""
        return stats_for_runs(get_manager(), body.run_ids)

    return router
