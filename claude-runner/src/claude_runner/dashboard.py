"""Read-only views of runs: the dashboard page and the JSON it polls.

The same views are served twice: under ``/v1`` on the main API (bearer auth like the rest of
it) and on the dashboard port (``CLAUDE_RUNNER_DASHBOARD_PORT``, default 8701), which has no
way to start, cancel or compact runs and answers only to the hosts in
``CLAUDE_RUNNER_DASHBOARD_HOSTS`` — publish it on 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable, Generator
from importlib import resources
from typing import Any

from fastapi import APIRouter, FastAPI, Query
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .runs import RunManager, RunnerError

log = logging.getLogger("claude_runner.dashboard")

_TASK_RE = re.compile(r"^TASK\s+\S+.*$", re.MULTILINE)
_REVIEW_RE = re.compile(r"^CURRENT TASK:\s*\n\s*(\S.*)$", re.MULTILINE)
_MISSION_RE = re.compile(r"^MISSION:\s*(\S.*)$", re.MULTILINE)


def title_of(text: str) -> str:
    """A short label for a run from its AHAWR prompt."""
    if match := _TASK_RE.search(text):
        return match.group(0).strip()[:160]
    if match := _REVIEW_RE.search(text):
        return ("Review: " + match.group(1).strip())[:160]
    if match := _MISSION_RE.search(text):
        return ("Plan: " + match.group(1).strip())[:160]
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:160]


def summary(run: dict[str, Any], manager: RunManager) -> dict[str, Any]:
    details = run.get("details") or {}
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
        "error": {"code": run["error_code"], "message": run["error_message"]}
        if run["error_code"] or run["error_message"]
        else None,
        "live": active and manager.events.is_live(run["run_id"]),
    }


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

    return router


def create_dashboard_app(settings: Settings, get_manager: Callable[[], RunManager]) -> FastAPI:
    app = FastAPI(title="claude-runner dashboard", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.dashboard_hosts))

    def manager() -> RunManager:
        try:
            return get_manager()
        except (AttributeError, KeyError) as exc:
            raise RunnerError("starting", "claude-runner is starting.", 503) from exc

    @app.exception_handler(RunnerError)
    async def runner_error(_: Any, exc: RunnerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": {"code": exc.code, "message": str(exc)}}
        )

    page = resources.files("claude_runner").joinpath("dashboard.html").read_text("utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    app.include_router(build_router(manager), prefix="/api")
    return app


class _EmbeddedServer:
    """The dashboard's uvicorn server inside the main server's event loop (signals stay with
    the main server; a port that cannot be bound only disables the dashboard)."""

    def __init__(self, app: FastAPI, host: str, port: int) -> None:
        import uvicorn

        class Server(uvicorn.Server):
            @contextlib.contextmanager
            def capture_signals(self) -> Generator[None, None, None]:
                yield

        self.server = Server(
            uvicorn.Config(app, host=host, port=port, log_level="warning", lifespan="off")
        )
        self.task: asyncio.Task[None] | None = None

    async def _serve(self) -> None:
        try:
            await self.server.serve()
        except (SystemExit, OSError) as exc:
            log.warning(
                "dashboard disabled: cannot serve on port %s (%r)", self.server.config.port, exc
            )

    def start(self) -> None:
        self.task = asyncio.create_task(self._serve())

    async def stop(self) -> None:
        self.server.should_exit = True
        if self.task is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.task, 10)
