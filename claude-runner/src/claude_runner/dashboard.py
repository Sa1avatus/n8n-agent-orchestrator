"""AHAWR dashboard: every agent run in one place.

It merges two sources:

* claude-runner (``DASHBOARD_RUNNER_URL``): Claude Code runs of every role and provider;
* a Hermes Agent API server (``DASHBOARD_HERMES_URL`` + ``DASHBOARD_HERMES_API_KEY``): the
  Hermes sessions of the Hermes variant of AHAWR.

It runs in its own container: the Hermes key must not sit in claude-runner, whose Worker runs
arbitrary commands. Besides reading, it can only stop a claude-runner run (cancelling an n8n
execution leaves the run going). It answers only to ``DASHBOARD_HOSTS`` (publish it on
127.0.0.1 only), and the stop request must carry the ``X-AHAWR-Dashboard`` header, which a
page on another site cannot send without a CORS preflight that the dashboard never allows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from . import board_api, export
from .board_ops_store import InMemoryOpsStore
from .board_ops_store_pg import UnavailableOpsStore, get_ops_store
from .board_pg import DSN_ENV
from .board_providers_api import add_provider_routes
from .board_stats import _mission_run_ids
from .config import _bool
from .guard_heartbeat import HeartbeatSettings, HostHeartbeatGuard
from .guard_queue import IdleGuard, MissionNoticeGuard, SeenStore
from .guard_relay import TelegramRelay
from .guard_restart import QueueRestartGuard
from .guards import (
    Finding,
    Guard,
    GuardSettings,
    GuardSupervisor,
    Notifier,
)
from .guards_history import WINDOWS_S, GuardHistory
from .host_actions import host_actions_enabled
from .provider_secrets import ProviderSecrets
from .qa_fixture import board_store_from_rows, load_fixture
from .remote_manager import RemoteRunManager
from .sources import HERMES_PREFIX, HermesSource, RunnerSource, SourceError

logger = logging.getLogger("claude_runner.dashboard")
QA_FIXTURE_ENV = "DASHBOARD_QA_FIXTURE"


@dataclass(frozen=True)
class DashboardSettings:
    runner_url: str = "http://claude-runner:8700"
    runner_api_key: str = ""
    host_actions_operator_key: str = ""
    hermes_url: str = ""
    hermes_api_key: str = ""
    hermes_session_source: str = ""
    hosts: tuple[str, ...] = ("localhost", "127.0.0.1")
    timeout_seconds: float = 15.0
    providers_db: bool = False
    secrets_file: Path | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> DashboardSettings:
        env = dict(os.environ if env is None else env)
        hosts = [h.strip() for h in env.get("DASHBOARD_HOSTS", "").split(",") if h.strip()]
        return cls(
            runner_url=env.get("DASHBOARD_RUNNER_URL", cls.runner_url).strip(),
            runner_api_key=env.get("DASHBOARD_RUNNER_API_KEY", "").strip(),
            host_actions_operator_key=env.get(
                "CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY", ""
            ).strip(),
            hermes_url=env.get("DASHBOARD_HERMES_URL", "").strip(),
            hermes_api_key=env.get("DASHBOARD_HERMES_API_KEY", "").strip(),
            hermes_session_source=env.get("DASHBOARD_HERMES_SOURCE", "").strip(),
            hosts=tuple(hosts) or cls.hosts,
            providers_db=env.get("CLAUDE_RUNNER_PROVIDERS_DB", "").strip() in ("1", "true"),
            secrets_file=(
                Path(env["CLAUDE_RUNNER_SECRETS_FILE"])
                if env.get("CLAUDE_RUNNER_SECRETS_FILE")
                else None
            ),
        )


_HOST_ACTION_ID = re.compile(r"[0-9a-f]{32}")


async def runner_host_action(
    settings: DashboardSettings,
    method: str,
    path: str,
    key: str,
    *,
    params: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    try:
        async with httpx.AsyncClient(timeout=settings.timeout_seconds) as client:
            response = await client.request(
                method,
                settings.runner_url + path,
                params=params,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
    except httpx.HTTPError as exc:
        return 502, {"error": _describe_error(exc)}
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, {"error": "runner returned no JSON"}


async def _host_action_decision(
    settings: DashboardSettings, request_id: str, approved: bool
) -> JSONResponse:
    code, payload = await runner_host_action(
        settings,
        "POST",
        f"/v1/host-actions/{request_id}/operator",
        settings.host_actions_operator_key,
        body={"approved": approved},
    )
    return JSONResponse(payload, status_code=code)


def create_dashboard_app(
    settings: DashboardSettings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or DashboardSettings.from_env()
    qa_fixture = _qa_fixture_path(dict(os.environ))

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
            # the guard supervisor starts here and stops at shutdown (matching
            # the runner's lifespan): DASHBOARD_GUARDS unset, no tasks run
            guard_settings = GuardSettings.from_env()
            history = (
                GuardHistory(
                    os.path.join(guard_settings.state_dir, "guards_history.json")
                    if guard_settings.state_dir
                    else ""
                )
                if guard_settings.tab
                else None
            )
            app.state.guards = GuardSupervisor(
                guard_settings,
                _build_guards(guard_settings, Notifier()),
                Notifier(),
                history,
            )
            await app.state.guards.start()
            try:
                yield
            finally:
                await app.state.guards.stop()

    app = FastAPI(
        title="AHAWR dashboard", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.hosts))
    page = resources.files("claude_runner").joinpath("dashboard.html").read_text("utf-8")
    if _bool(dict(os.environ), "DASHBOARD_OPS_TAB", False):
        page = re.sub(r"<!--/?ops-tab-->\n", "", page)
    else:
        page = re.sub(r"<!--ops-tab-->\n.*?<!--/ops-tab-->\n", "", page, flags=re.DOTALL)

    # Mutation routes (POST/PUT/DELETE under /api/board and /api/host-actions) require the
    # X-AHAWR-Dashboard header as cancel_run does; read-only routes are open.
    async def board_header_guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if (
            path.startswith(("/api/board/", "/api/host-actions/"))
            and request.method in ("POST", "PUT", "DELETE")
            and request.headers.get("x-ahawr-dashboard") != "1"
        ):
            return JSONResponse({"error": "missing X-AHAWR-Dashboard header"}, status_code=403)
        return await call_next(request)

    app.middleware("http")(board_header_guard)
    if qa_fixture is not None:
        app.state.board_store = board_store_from_rows(load_fixture(qa_fixture))
    board_api.add_board_routes(app, settings)
    if settings.providers_db:
        secrets = ProviderSecrets(settings.secrets_file or Path("provider-secrets.json"))

        def runner_env_providers() -> list[dict[str, Any]]:
            """Ask the runner, which holds the env provider blocks, for the import rows."""
            try:
                response = httpx.get(
                    settings.runner_url + "/v1/providers/env-proposals",
                    headers=(
                        {"Authorization": f"Bearer {settings.runner_api_key}"}
                        if settings.runner_api_key
                        else {}
                    ),
                    timeout=settings.timeout_seconds,
                )
                response.raise_for_status()
                return list(response.json()["providers"])
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                logger.warning("env provider list from the runner failed: %s", _describe_error(exc))
                return []

        add_provider_routes(app, secrets, env_source=runner_env_providers)
    if getattr(app.state, "manager", None) is None:
        # the board statistics read runs through a RunManager-like object; the dashboard has no
        # runner database, so it reads the runner over HTTP
        app.state.manager = RemoteRunManager(settings.runner_url, settings.runner_api_key)

    def sources(request: Request) -> list[Any]:
        return [s for s in (request.app.state.runner, request.app.state.hermes) if s]

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    # Board static assets: board*.js, board*.css and board*.html (created by
    # T017-T020). Anything else is 404; a file that does not exist yet is 404
    # with a short message, so the app still starts.
    _board_static = re.compile(r"^board[a-z0-9_]*[.](js|css|html)$")
    _board_media = {
        "js": "application/javascript",
        "css": "text/css; charset=utf-8",
        "html": "text/html; charset=utf-8",
    }

    @app.get("/static/{name}", response_class=Response)
    async def board_static(name: str) -> Response:
        m = _board_static.fullmatch(name)
        if m is None:
            return Response("unknown static file", status_code=404)
        filename = m.group(0)
        try:
            body = resources.files("claude_runner").joinpath(filename).read_bytes()
        except (FileNotFoundError, LookupError):
            return Response("board static file not found", status_code=404)
        return Response(
            body, media_type=_board_media[m.group(1)], headers={"Cache-Control": "no-store"}
        )

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, Any]:
        return {"status": "ok", "sources": [s.name for s in sources(request)]}

    @app.get("/api/guards")
    async def api_guards(request: Request) -> dict[str, Any]:
        """The guard supervisor's state: whether it is enabled, dry-run mode,
        and each guard's name, status, last run, last result, last action and
        error count. The host heartbeat entries (docker_watchdog and
        gpu_power_governor) appear only when the supervisor is enabled.
        It carries no environment values."""
        state = request.app.state.guards.state()
        body: dict[str, Any] = {
            "enabled": request.app.state.guards.settings.master,
            "dry_run": state["dry_run"],
            "running": state["running"],
            "guards": list(state["guards"].values()),
        }
        if request.app.state.guards.settings.master:
            body["heartbeats"] = _heartbeat_entries(
                request.app.state.guards.guards, request.app.state.guards.settings
            )
        history = request.app.state.guards.history
        if history is not None:
            body["tab"] = True
            body["history_summary"] = history.summary()
        return body

    @app.get("/api/guards/history")
    async def api_guards_history(request: Request, window: str = "24h") -> dict[str, Any]:
        """Checks of every guard inside the window (1h, 24h or 7d), thinned; empty
        unless the Guards tab is on.  It carries no environment values."""
        history = request.app.state.guards.history
        if history is None:
            return {"window": window, "guards": {}}
        if window not in WINDOWS_S:
            raise HTTPException(status_code=422, detail="window must be 1h, 24h or 7d")
        return {"window": window, "guards": history.window(window)}

    host_actions_on = host_actions_enabled()

    @app.get("/api/host-actions")
    async def api_host_actions(status: str = "") -> Any:
        if not host_actions_on:
            return {"enabled": False}
        code, payload = await runner_host_action(
            settings,
            "GET",
            "/v1/host-actions",
            settings.runner_api_key,
            params={"status": status} if status else None,
        )
        if code != 200:
            return JSONResponse(payload, status_code=code)
        return {"enabled": True, "requests": payload}

    @app.post("/api/host-actions/{request_id}/confirm")
    async def api_host_action_confirm(request_id: str) -> Any:
        if not host_actions_on or _HOST_ACTION_ID.fullmatch(request_id) is None:
            return JSONResponse({"error": "host action not found"}, status_code=404)
        code, found = await runner_host_action(
            settings, "GET", f"/v1/host-actions/{request_id}", settings.runner_api_key
        )
        if code != 200:
            return JSONResponse(found, status_code=code)
        if found.get("tier") != "CONFIRM" or found.get("state") != "pending":
            return JSONResponse(
                {"error": "only a pending CONFIRM-tier host action can be confirmed"},
                status_code=409,
            )
        return await _host_action_decision(settings, request_id, approved=True)

    @app.post("/api/host-actions/{request_id}/reject")
    async def api_host_action_reject(request_id: str) -> Any:
        if not host_actions_on or _HOST_ACTION_ID.fullmatch(request_id) is None:
            return JSONResponse({"error": "host action not found"}, status_code=404)
        return await _host_action_decision(settings, request_id, approved=False)

    @app.get("/api/runs")
    async def list_runs(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        session_id: list[str] = Query([]),  # noqa: B008
        role: list[str] = Query([]),  # noqa: B008
        status: list[str] = Query([]),  # noqa: B008
        model: list[str] = Query([]),  # noqa: B008
        provider: list[str] = Query([]),  # noqa: B008
        cwd: list[str] = Query([]),  # noqa: B008
        source: list[str] = Query([]),  # noqa: B008
        mission: str = "",
    ) -> Any:
        # repeated values of one field are matched with OR; fields combine with AND
        mission_runs: set[str] | None = None
        if mission:
            store = board_api.live_store(request.app)
            refuse = board_api._unavailable(store)
            if refuse is not None:
                return JSONResponse(refuse, status_code=503)
            found = store.get_mission(mission)
            mission_runs = set(_mission_run_ids(store, found.state_namespace)) if found else set()
        values: dict[str, set[str]] = {}
        for field, seen in (
            ("model", model),
            ("provider", provider),
            ("cwd", cwd),
            ("role", role),
            ("status", status),
        ):
            values[field] = {v for v in seen if v}
        hermes_sessions = {
            v.removeprefix(HERMES_PREFIX) for v in session_id if v.startswith(HERMES_PREFIX)
        }
        runner_sessions = {v for v in session_id if v and not v.startswith(HERMES_PREFIX)}
        chosen = [s for s in sources(request) if not source or s.name in {v for v in source if v}]
        if hermes_sessions or runner_sessions:  # a session belongs to exactly one source
            chosen = [
                s
                for s in chosen
                if (isinstance(s, HermesSource) and hermes_sessions)
                or (not isinstance(s, HermesSource) and runner_sessions)
            ]
        # role/status/model/provider/cwd are matched after the fetch, so the sources are
        # not narrowed by them (narrowing by role[0] would drop the other role values)
        fetch = 500 if any(values.values()) or mission else limit
        # each source serves the sessions of its kind (the 'hermes:' prefix is
        # stripped before the call); with no session of its kind it is read unfiltered
        calls: list[tuple[Any, Any]] = []
        for s in chosen:
            is_hermes = isinstance(s, HermesSource)
            ids = [
                v.removeprefix(HERMES_PREFIX) if is_hermes else v
                for v in session_id
                if v.startswith(HERMES_PREFIX) == is_hermes
            ] or [""]
            calls.extend((s, s.list_runs(fetch, sid, "", "")) for sid in ids)
        results = await asyncio.gather(*(c[1] for c in calls), return_exceptions=True)
        runs: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        for (src, _), result in zip(calls, results, strict=True):
            if isinstance(result, BaseException):
                states[src.name] = _describe_error(result)
            else:
                states.setdefault(src.name, "ok")
                runs += result
        runs = _runs_match([_with_display_status(r) for r in runs], values)
        if mission_runs is not None:
            runs = [r for r in runs if r.get("run_id") in mission_runs]
        runs.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
        return {"runs": runs[:limit], "sources": states}

    async def fetch_events(request: Request, run_id: str, after: int = 0) -> Any:
        """The run's activity, or a JSONResponse error."""
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

    @app.get("/api/search")
    async def api_search(
        request: Request,
        q: str = Query(""),
        limit: int = Query(50, ge=1, le=200),
        role: list[str] = Query([]),  # noqa: B008
        model: list[str] = Query([]),  # noqa: B008
        provider: list[str] = Query([]),  # noqa: B008
        cwd: list[str] = Query([]),  # noqa: B008
        status: list[str] = Query([]),  # noqa: B008
        session_id: list[str] = Query([]),  # noqa: B008
        source: list[str] = Query([]),  # noqa: B008
    ) -> dict[str, Any]:
        query = q.strip()
        if len(query) < 2:
            return {"runs": [], "incomplete": False, "note": "query too short"}
        chosen = [s for s in sources(request) if not source or s.name in {v for v in source if v}]
        calls = [
            (
                s,
                s.search(query, limit, role, model, provider, cwd, status, session_id),
            )
            for s in chosen
        ]
        results = await asyncio.gather(*(c[1] for c in calls), return_exceptions=True)
        runs: list[dict[str, Any]] = []
        states: dict[str, str] = {}
        incomplete = False
        note: str | None = None
        for (src, _), result in zip(calls, results, strict=True):
            if isinstance(result, BaseException):
                states[src.name] = _describe_error(result)
            else:
                states.setdefault(src.name, "ok")
                runs += result.get("runs", [])
                if result.get("incomplete"):
                    incomplete = True
                if result.get("note") and not note:
                    note = result["note"]
        runs.sort(key=lambda r: str(r.get("started_at") or r.get("created_at") or ""), reverse=True)
        return {"runs": runs[:limit], "incomplete": incomplete, "sources": states, "note": note}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(request: Request, run_id: str, after: int = Query(0, ge=0)) -> Any:
        return await fetch_events(request, run_id, after)

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(request: Request, run_id: str) -> Any:
        if request.headers.get("x-ahawr-dashboard") != "1":
            return JSONResponse({"error": "missing X-AHAWR-Dashboard header"}, status_code=403)
        if run_id.startswith(HERMES_PREFIX):
            return JSONResponse(
                {"error": "Hermes sessions are stopped in Hermes itself"}, status_code=400
            )
        runner = request.app.state.runner
        if runner is None:
            return JSONResponse({"error": "source not configured"}, status_code=404)
        try:
            body = await runner.cancel(run_id)
        except (SourceError, httpx.HTTPError) as exc:
            return JSONResponse({"error": _describe_error(exc)}, status_code=502)
        if body is None:
            return JSONResponse({"error": f"run {run_id} not found"}, status_code=404)
        return body

    @app.get("/api/runs/{run_id}/export")
    async def export_run(request: Request, run_id: str, format: str = "md") -> Any:
        body = await fetch_events(request, run_id)
        if isinstance(body, JSONResponse):
            return body
        return _download(body["run"], [body], format, body["run"].get("session_id", ""))

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(request: Request, session_id: str, format: str = "md") -> Any:
        """Every run of a session, oldest first (a Hermes session is one run)."""
        if session_id.startswith(HERMES_PREFIX):
            ids = [session_id]
        else:
            runner = request.app.state.runner
            if runner is None:
                return JSONResponse({"error": "source not configured"}, status_code=404)
            try:
                runs = await runner.list_runs(500, session_id, "", "")
            except (SourceError, httpx.HTTPError) as exc:
                return JSONResponse({"error": _describe_error(exc)}, status_code=502)
            # claude-runner lists newest first (by creation, then insertion order)
            ids = [r["run_id"] for r in reversed(runs)]
        if not ids:
            return JSONResponse({"error": f"session {session_id} not found"}, status_code=404)
        items = []
        for run_id in ids:
            body = await fetch_events(request, run_id)
            if isinstance(body, JSONResponse):
                return body
            items.append(body)
        return _download(items[-1]["run"], items, format, session_id, whole_session=True)

    return app


def _download(
    run: dict[str, Any],
    items: list[dict[str, Any]],
    fmt: str,
    session_id: str,
    whole_session: bool = False,
) -> Response:
    """Markdown or JSON export as a file download."""
    prefix = "session-" if whole_session else ""
    runs = [{"run": item["run"], "events": item["events"]} for item in items]
    if fmt == "json":
        payload: dict[str, Any] = (
            {"session_id": session_id, "runs": runs} if whole_session else runs[0]
        )
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        name, media = export.filename(run, "json", prefix), "application/json"
    else:
        content = (
            export.session_markdown(session_id, runs)
            if whole_session
            else export.run_markdown(runs[0]["run"], runs[0]["events"])
        )
        name, media = export.filename(run, "md", prefix), "text/markdown; charset=utf-8"
    return Response(
        content,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _with_display_status(run: dict[str, Any]) -> dict[str, Any]:
    """Queued and running rows keep their status; a superseded queued run shows as cancelled."""
    error = run.get("error")
    if isinstance(error, dict) and error.get("code") == "superseded":
        return {**run, "status": "cancelled"}
    return run


def _runs_match(runs: list[dict[str, Any]], values: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Filter runs after the sources have been read.

    Each field keeps its active values in ``values``; a run matches a field when its
    value (a missing provider/cwd counts as ``""``) is one of them (OR within a field,
    AND across fields).
    """
    active = {field: allowed for field, allowed in values.items() if allowed}
    if not active:
        return runs
    out: list[dict[str, Any]] = []
    for run in runs:
        for field, allowed in active.items():
            if str(run.get(field) or "") not in allowed:
                break
        else:
            out.append(run)
    return out


def _describe_error(exc: BaseException) -> str:
    if isinstance(exc, httpx.ConnectError):
        return f"unreachable ({exc.request.url.host}:{exc.request.url.port})"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    return str(exc) or type(exc).__name__


def _build_guards(settings: GuardSettings, notifier: Notifier) -> list[Guard]:
    """The container guards of the supervisor.

    With ``settings.master`` off the list is empty: no guard is registered and
    no per-guard task is started.  Each guard is registered regardless of
    whether its source is configured: a missing source keeps the guard inert
    (unknown state, no action) instead of failing its check.  Paths and the
    DSN come from environment variable names only; their values are never
    logged or returned."""
    if not settings.master:
        return []
    env = dict(os.environ)
    guards: list[Guard] = []
    ops_store = _ops_store(env)
    guards.append(
        IdleGuard(
            lambda: _queue_alive(ops_store), lambda: _queue_running(ops_store),
            notifier,
            interval=settings.interval("idle"),
            dry_run=lambda: settings.dry_run_for("idle"),
        )
    )
    seen_path = env.get("DASHBOARD_GUARDS_QUEUE_STATE_PATH", "").strip()
    seen = SeenStore(seen_path) if seen_path else _EmptySeenStore()
    guards.append(
        MissionNoticeGuard(
            ops_store, _mission_events(ops_store), seen, notifier,
            interval=settings.interval("mission_notice"),
            dry_run=lambda: settings.dry_run_for("mission_notice"),
        )
    )
    request_dir = env.get("DASHBOARD_GUARDS_QUEUE_REQUEST_DIR", "").strip()
    request_path = f"{request_dir}/queue_restart.req" if request_dir else ""
    guards.append(
        QueueRestartGuard(
            _queue_state(ops_store), request_path,
            interval=settings.interval("queue_restart"),
            dry_run=lambda: settings.dry_run_for("queue_restart"),
        )
    )
    guards.append(
        HostHeartbeatGuard(
            HeartbeatSettings.from_env(env), notifier,
            interval=settings.interval("host_heartbeat"),
            dry_run=lambda: settings.dry_run_for("host_heartbeat"),
        )
    )
    guards.append(_RelayGuard(settings, env))
    return guards


def _heartbeat_entries(
    guards: list[Guard], settings: GuardSettings
) -> list[dict[str, object]]:
    """The two host heartbeat entries (docker_watchdog, gpu_power_governor)."""
    for guard in guards:
        if isinstance(guard, HostHeartbeatGuard):
            entries = guard.status_entries()
            dry_run = settings.dry_run_for("host_heartbeat")
            last_result = guard.state.last_result
            out: list[dict[str, object]] = []
            for entry in entries:
                out.append(
                    {
                        "name": entry["name"],
                        "status": entry["status"],
                        "time": entry["time"],
                        "last_action": entry["last_action"],
                        "last_result": str(entry["detail"]) or last_result,
                        "dry_run": dry_run,
                    }
                )
            return out
    return []


def _qa_fixture_path(env: dict[str, str]) -> Path | None:
    value = env.get(QA_FIXTURE_ENV, "").strip()
    if not value:
        return None
    if env.get(DSN_ENV, "").strip():
        raise RuntimeError(
            f"{QA_FIXTURE_ENV} and {DSN_ENV} cannot both be set: "
            "a QA fixture must never run against a database"
        )
    return Path(value)


def _ops_store(env: dict[str, str]) -> Any:
    """The ops store for the guard sources: the Pg ops store when the DSN is
    set and connectable, ``None`` otherwise (the guards stay registered but
    inert).  A driver import failure or a refused connection both leave the
    store unset: the guards read unknown state and never fail their check."""
    if env.get(QA_FIXTURE_ENV, "").strip():
        return InMemoryOpsStore()
    dsn = env.get("DASHBOARD_BOARD_PG_DSN", "").strip()
    if not dsn:
        return None
    store = get_ops_store(dsn)
    if isinstance(store, UnavailableOpsStore):
        return None
    return store


def _queue_state(ops_store: Any) -> Any:
    """Queue liveness and run count from the published snapshot rows; an inert
    state (unknown, never an error) when the store or the data is unknown, so
    the restart guard stays inert."""
    if ops_store is None:
        return _InertQueueState()
    try:
        return _LiveQueueState(_queue_alive(ops_store), _queue_running(ops_store))
    except Exception as exc:
        logger.exception("queue state read failed: %s", exc)
        return _InertQueueState()


def _snapshot_data(ops_store: Any) -> dict[str, Any]:
    """The newest host_snapshot row's ``data_json`` (``{}`` when there is none
    or it is not an object)."""
    if ops_store is None:
        return {}
    for row in ops_store.get_snapshot("host_snapshot", limit=1):
        data = row.get("data_json")
        if isinstance(data, dict):
            return data
    return {}


def _queue_alive(ops_store: Any) -> bool | None:
    """Whether the queue process is alive, from the published snapshot rows.
    Returns ``None`` when no snapshot is present (unknown, never an error)."""
    data = _snapshot_data(ops_store)
    queue = data.get("queue")
    alive = queue.get("alive") if isinstance(queue, dict) else None
    if isinstance(alive, bool):
        return alive
    value = data.get("alive")
    if isinstance(value, bool):
        return value
    return None


def _queue_running(ops_store: Any) -> int | None:
    """The queue's running run count (executions plus runner runs, the queue
    watcher's own ``running`` total); ``None`` when unknown."""
    data = _snapshot_data(ops_store)
    value = data.get("running")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class _LiveQueueState:
    def __init__(self, alive: bool | None, running: int | None) -> None:
        self._alive = alive
        self._running = running

    def is_alive(self) -> bool | None:
        return self._alive

    def running_count(self) -> int | None:
        return self._running


class _InertQueueState:
    """A queue state that is always unknown: the restart guard never acts."""

    def is_alive(self) -> bool | None:
        return None

    def running_count(self) -> int | None:
        return None


class _EmptySeenStore(SeenStore):
    """A seen store with no keys: every line is unseen, so notices would fire
    but dry-run or the inert sources keep them from being sent."""

    def __init__(self) -> None:
        self.fresh = True

    def has(self, key: str) -> bool:
        return False

    def add(self, key: str) -> None:
        pass

    def add_all(self, keys: list[str]) -> None:
        pass

    def save(self) -> None:
        pass


class _EmptyEventSource:
    """An event source that returns no events: the mission notice guard finds
    no review data, which is inert when the store is missing."""

    def events(self, state_key: str) -> list[dict[str, Any]]:
        return []


def _mission_events(ops_store: Any) -> Any:
    """The event source for mission reviews: an empty source when the store is
    missing, so the notice guard stays inert without failing its check."""
    if ops_store is None:
        return _EmptyEventSource()
    return ops_store


class _RelayGuard(Guard):
    """The Telegram relay as a registered guard: its check reports the relay's
    state (config, dry-run, running) and its act is inert — the relay itself
    runs as a task of the supervisor, started when the guard starts and
    stopped when it stops."""

    def __init__(self, settings: GuardSettings, env: dict[str, str]) -> None:
        super().__init__("tg_relay", settings.interval("tg_relay"))
        self._settings = settings
        self._env = env
        self._relay: TelegramRelay | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._configured = False
        self._dry_run = False
        self._last_state: str | None = None

    def _configure(self) -> None:
        from .guard_relay import (
            TOKEN_VAR,
            build_telegram_client,
            build_webhook_client,
            load_relay_settings,
        )

        try:
            relay_settings = load_relay_settings(self._env)
            token = self._env.get(TOKEN_VAR, "").strip()
            if not token:
                return
            telegram = build_telegram_client(self._env)
            webhook = build_webhook_client()
            self._relay = TelegramRelay(relay_settings, telegram, webhook)
            self._configured = True
            self._dry_run = relay_settings.dry_run
        except (ValueError, KeyError):
            self._configured = False

    async def check(self) -> list[Finding]:
        self._configure()
        if self._task is None and self._configured:
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._run())
        state = "dry-run" if (self._configured and self._dry_run) else (
            "running" if self._configured else "unconfigured"
        )
        if state != self._last_state:
            self._last_state = state
            return [Finding(detail=f"relay {state}")]
        return []

    async def act(self, findings: list[Finding]) -> str | None:
        return None

    async def _run(self) -> None:
        if self._relay is None or self._stop_event is None:
            return
        await self._relay.run(self._stop_event)

    def stop_task(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self._stop_event is not None:
            self._stop_event.set()
        self._task = None
        self._stop_event = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahawr-dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8701)
    args = parser.parse_args(argv)

    import uvicorn

    uvicorn.run(create_dashboard_app(), host=args.host, port=args.port, log_level="warning")
    return 0
