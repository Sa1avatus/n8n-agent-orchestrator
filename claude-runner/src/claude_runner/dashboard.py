"""AHAWR dashboard: every agent run in one place, read-only.

It merges two sources:

* claude-runner (``DASHBOARD_RUNNER_URL``): Claude Code runs of every role and provider;
* a Hermes Agent API server (``DASHBOARD_HERMES_URL`` + ``DASHBOARD_HERMES_API_KEY``): the
  Hermes sessions of the Hermes variant of AHAWR.

It runs in its own container: the Hermes key must not sit in claude-runner, whose Worker runs
arbitrary commands. It can only read, and it answers only to ``DASHBOARD_HOSTS`` (publish it
on 127.0.0.1 only).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from typing import Any

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .sources import HERMES_PREFIX, HermesSource, RunnerSource, SourceError


@dataclass(frozen=True)
class DashboardSettings:
    runner_url: str = "http://claude-runner:8700"
    runner_api_key: str = ""
    hermes_url: str = ""
    hermes_api_key: str = ""
    hermes_session_source: str = ""
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DashboardSettings:
        env = dict(os.environ if env is None else env)
        hosts = [h.strip() for h in env.get("DASHBOARD_HOSTS", "").split(",") if h.strip()]
        return cls(
            runner_url=env.get("DASHBOARD_RUNNER_URL", cls.runner_url).strip(),
            runner_api_key=env.get("DASHBOARD_RUNNER_API_KEY", "").strip(),
            hermes_url=env.get("DASHBOARD_HERMES_URL", "").strip(),
            hermes_api_key=env.get("DASHBOARD_HERMES_API_KEY", "").strip(),
            hermes_session_source=env.get("DASHBOARD_HERMES_SOURCE", "").strip(),
            hosts=tuple(hosts) or cls.hosts,
        )


def create_dashboard_app(
    settings: DashboardSettings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or DashboardSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            timeout=settings.timeout_seconds, transport=transport
        ) as client:
            app.state.runner = (
                RunnerSource(client, settings.runner_url, settings.runner_api_key)
                if settings.runner_url
                else None
            )
            app.state.hermes = (
                HermesSource(
                    client,
                    settings.hermes_url,
                    settings.hermes_api_key,
                    settings.hermes_session_source,
                )
                if settings.hermes_url
                else None
            )
            yield

    app = FastAPI(
        title="AHAWR dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts))
    page = resources.files("claude_runner").joinpath("dashboard.html").read_text("utf-8")

    def sources(request: Request) -> list[Any]:
        return [s for s in (request.app.state.runner, request.app.state.hermes) if s]

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {"status": "ok", "sources": [s.name for s in sources(request)]}

    @app.get("/api/runs")
    async def list_runs(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        session_id: str = "",
        role: str = "",
        status: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        chosen = [s for s in sources(request) if not source or s.name == source]
        if session_id:  # a session belongs to exactly one source
            hermes = session_id.startswith(HERMES_PREFIX)
            session_id = session_id.removeprefix(HERMES_PREFIX)
            chosen = [s for s in chosen if isinstance(s, HermesSource) == hermes]
        results = await asyncio.gather(
            *(s.list_runs(limit, session_id, role, status) for s in chosen), return_exceptions=True
        )
        runs: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        for src, result in zip(chosen, results, strict=True):
            if isinstance(result, BaseException):
                states[src.name] = _describe_error(result)
            else:
                states[src.name] = "ok"
                runs += result
        runs.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
        return {"runs": runs[:limit], "sources": states}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str, after: int = Query(0, ge=0)) -> Any:
        src = (
            request.app.state.hermes
            if run_id.startswith(HERMES_PREFIX)
            else request.app.state.runner
        )
        if src is None:
            return JSONResponse({"error": "source not configured"}, status_code=404)
        try:
            body = await src.events(run_id, after)
        except (SourceError, httpx.HTTPError) as exc:
            return JSONResponse({"error": _describe_error(exc)}, status_code=502)
        if body is None:
            return JSONResponse({"error": f"run {run_id} not found"}, status_code=404)
        return body

    return app


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"unreachable ({exc.request.url.host}:{exc.request.url.port})"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    return str(exc) or type(exc).__name__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahawr-dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8701)
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_dashboard_app(), host=args.host, port=args.port, log_level="warning")
    return 0
