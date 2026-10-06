"""HTTP API. ``/v1/runs`` mirrors the Hermes run API, so the AHAWR Run Manager keeps its
start → poll → resume → retry logic unchanged."""

from __future__ import annotations

import asyncio
import hmac
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .claude_cli import child_env
from .config import Settings
from .handoff import session_digest
from .run_views import build_router
from .runs import RunManager, RunnerError, StartRequest


class RunBody(BaseModel):
    input: str = ""
    model: str = ""
    provider: str = ""
    session_id: str = ""
    working_directory: str = ""
    role: str = ""


class CompactBody(BaseModel):
    mode: str = Field(default="", pattern="^(|auto|always|off)$")
    min_tokens: int | None = Field(default=None, ge=0)
    model: str = ""


async def _cli_version(settings: Settings) -> str:
    binary = shutil.which(settings.claude_bin) or settings.claude_bin
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "--version",
            env=child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        return out.decode("utf-8", "replace").strip() or f"exit {proc.returncode}"
    except (OSError, TimeoutError) as exc:
        return f"unavailable: {exc}"


def _auth_mode() -> str:
    env = child_env()
    for name, label in (
        ("CLAUDE_CODE_USE_BEDROCK", "bedrock"),
        ("CLAUDE_CODE_USE_VERTEX", "vertex"),
        ("CLAUDE_CODE_USE_FOUNDRY", "foundry"),
        ("ANTHROPIC_AUTH_TOKEN", "auth_token"),
        ("ANTHROPIC_API_KEY", "api_key"),
        ("CLAUDE_CODE_OAUTH_TOKEN", "oauth_token"),
    ):
        if env.get(name, "").strip():
            return label
    return "stored_login_or_none"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager = RunManager(settings)
        app.state.manager = manager
        app.state.interrupted = await manager.startup()
        app.state.cli_version = await _cli_version(settings)
        try:
            yield
        finally:
            await manager.shutdown()

    app = FastAPI(title="claude-runner", version="0.2.0", lifespan=lifespan)

    def authorize(authorization: str = Header(default="")) -> None:
        if not settings.api_key:
            return
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), settings.api_key.encode()):
            raise RunnerError("unauthorized", "Missing or invalid bearer token.", 401)

    def manager(request: Request) -> RunManager:
        return request.app.state.manager  # type: ignore[no-any-return]

    def manager_of(application: FastAPI) -> RunManager:
        return application.state.manager  # type: ignore[no-any-return]

    @app.exception_handler(RunnerError)
    async def runner_error(_: Request, exc: RunnerError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "status": "failed",
                "error": {"code": exc.code, "message": str(exc)},
                "code": exc.code,
                "status_code": exc.status_code,
            },
        )

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        runs = manager(request)
        return {
            "status": "ok",
            "claude_code": request.app.state.cli_version,
            "auth": _auth_mode(),
            "workspace": settings.workspace,
            "max_concurrent": settings.max_concurrent,
            "compact_mode": settings.compact_mode,
            "providers": {
                name: {
                    "base_url": provider.env.get("ANTHROPIC_BASE_URL", ""),
                    "credential": next(
                        (
                            k
                            for k in (
                                "ANTHROPIC_AUTH_TOKEN",
                                "ANTHROPIC_API_KEY",
                                "CLAUDE_CODE_OAUTH_TOKEN",
                            )
                            if provider.env.get(k)
                        ),
                        "inherited",
                    ),
                    "tools": provider.tools,
                }
                for name, provider in settings.providers.items()
            },
            "runs": runs.store.counts(),
            "interrupted_on_start": request.app.state.interrupted,
        }

    @app.post("/v1/runs", dependencies=[Depends(authorize)])
    async def start_run(
        body: RunBody,
        request: Request,
        x_hermes_session_id: str = Header(default=""),
        x_hermes_working_directory: str = Header(default=""),
        x_ahawr_role: str = Header(default=""),
    ) -> dict[str, Any]:
        return await manager(request).start(
            StartRequest(
                input=body.input,
                model=body.model,
                provider=body.provider,
                session_id=body.session_id or x_hermes_session_id,
                working_directory=body.working_directory or x_hermes_working_directory,
                role=body.role or x_ahawr_role,
            )
        )

    # Read-only run list and activity (the dashboard's data), e.g. for scripts or n8n.
    app.include_router(
        build_router(lambda: manager_of(app)), prefix="/v1", dependencies=[Depends(authorize)]
    )

    @app.get("/v1/runs/{run_id}", dependencies=[Depends(authorize)])
    async def get_run(run_id: str, request: Request) -> dict[str, Any]:
        return manager(request).get(run_id)

    @app.post("/v1/runs/{run_id}/cancel", dependencies=[Depends(authorize)])
    async def cancel_run(run_id: str, request: Request) -> dict[str, Any]:
        return await manager(request).cancel(run_id)

    @app.get("/v1/sessions/{session_id}", dependencies=[Depends(authorize)])
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        runs = manager(request)
        session = runs.store.get_session(session_id)
        if not session:
            raise RunnerError("session_not_found", f"Session {session_id} not found.", 404)
        active = runs.store.active_run(session_id)
        return {**session, "active_run_id": active["run_id"] if active else ""}

    @app.get("/v1/sessions/{session_id}/digest", dependencies=[Depends(authorize)])
    async def digest(
        session_id: str, request: Request, max_chars: int = Query(12_000, ge=500, le=200_000)
    ) -> dict[str, Any]:
        """What the session already did, for continuing it in a fresh session."""
        runs = manager(request)
        if not runs.store.get_session(session_id):
            raise RunnerError("session_not_found", f"Session {session_id} not found.", 404)
        return session_digest(runs, session_id, max_chars)

    @app.post("/v1/sessions/{session_id}/compact", dependencies=[Depends(authorize)])
    async def compact(
        session_id: str, request: Request, body: CompactBody | None = None
    ) -> dict[str, Any]:
        body = body or CompactBody()
        return await manager(request).compact(session_id, body.mode, body.min_tokens, body.model)

    return app
