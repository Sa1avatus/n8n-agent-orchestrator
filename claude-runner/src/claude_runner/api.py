"""HTTP API. ``/v1/runs`` mirrors the Hermes run API, so the AHAWR Run Manager keeps its
start → poll → resume → retry logic unchanged."""

from __future__ import annotations

import asyncio
import hmac
import logging
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal, TypeVar

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import qa_run_record
from .board_api import add_board_routes
from .board_ops_redact import scrub
from .claude_cli import child_env
from .config import Settings
from .guards import Notifier
from .handoff import session_digest
from .host_actions import (
    TIER_CONFIRM,
    HostActionRequest,
    HostActionStore,
    HostActionStoreFullError,
    HostActionTransitionError,
    WhitelistError,
    host_actions_enabled,
)
from .mem_guard import MemGuard
from .provider_registry import ProviderRegistry, env_provider_row
from .provider_secrets import ProviderSecrets
from .qa_requests import QA_AGENT_LAYER, QaRequestStore, QaTransitionError, qa_agent_enabled
from .report_facts import build_pack, collect_repo_facts, parse_digest
from .roles_config import QA_LAYERS
from .run_views import build_router
from .runs import RunManager, RunnerError, StartRequest
from .search import DEFAULT_LIMIT, MAX_LIMIT
from .search import search as run_search
from .think_switch import ThinkSwitch

logger = logging.getLogger("claude_runner.api")

QaMode = Literal["final", "ui_tasks"]
QaStatus = Literal["queued", "running", "done", "error", "timeout"]
QaVerdict = Literal["pass", "fail", "skipped", "blocked"]
QA_VERDICT_TEXT_MAX_CHARS = 2000
QA_VERDICTS_MAX = 100


QaLayer = Literal["regression", "api", "ui", "agent"]
HostActionState = Literal[
    "pending", "confirmed", "running", "done", "failed", "rejected", "expired"
]
_BodyT = TypeVar("_BodyT", bound=BaseModel)


class QaRequestBody(BaseModel):
    mission_id: str = Field(min_length=1)
    working_directory: str = ""
    mode: QaMode
    nonce: str = Field(min_length=1)
    layers: list[QaLayer] | None = Field(default=None)
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def check_layers(cls, values: dict[str, Any]) -> dict[str, Any]:
        layers = values.get("layers")
        agent_on = qa_agent_enabled(values.get("options") or {})
        allowed = (*QA_LAYERS, QA_AGENT_LAYER) if agent_on else QA_LAYERS
        if layers is not None and not all(name in allowed for name in layers):
            raise ValueError(f"unknown QA layer: {layers}")
        return values


class QaVerdictEntry(BaseModel):
    """A layer verdict: the verdict word plus the layer and the reason the workflow shows."""

    model_config = ConfigDict(extra="allow")

    verdict: QaVerdict
    layer: QaLayer | None = None

    @model_validator(mode="after")
    def check_size(self) -> QaVerdictEntry:
        for key, value in (self.model_extra or {}).items():
            if isinstance(value, str) and len(value) > QA_VERDICT_TEXT_MAX_CHARS:
                raise ValueError(f"verdict field {key!r} exceeds {QA_VERDICT_TEXT_MAX_CHARS}")
        return self


class QaResultBody(BaseModel):
    verdicts: list[QaVerdict | QaVerdictEntry] = Field(
        default_factory=list, max_length=QA_VERDICTS_MAX
    )
    error: str = ""


class HostActionBody(BaseModel):
    action: str = Field(min_length=1)
    service: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class HostActionResultBody(BaseModel):
    ok: bool
    message: str = ""


class HostActionOperatorBody(BaseModel):
    approved: bool


@contextmanager
def _qa_refusals(request_id: str) -> Iterator[None]:
    try:
        yield
    except KeyError:
        raise RunnerError(
            "qa_request_not_found", f"QA request {request_id} not found.", 404
        ) from None
    except QaTransitionError as exc:
        raise RunnerError("qa_transition_refused", str(exc), 409) from None


@contextmanager
def _host_action_refusals(action_id: str) -> Iterator[None]:
    try:
        yield
    except KeyError:
        raise RunnerError(
            "host_action_not_found", f"host action {action_id} not found.", 404
        ) from None
    except HostActionTransitionError as exc:
        raise RunnerError("host_action_transition_refused", str(exc), 409) from None


async def _json_body(request: Request, model: type[_BodyT]) -> _BodyT:
    try:
        return model.model_validate(await request.json())
    except ValueError:
        raise RunnerError(
            "bad_body", f"Body is not valid JSON or misses a field of {model.__name__}.", 400
        ) from None


class RunBody(BaseModel):
    input: str = ""
    model: str = ""
    provider: str = ""
    session_id: str = ""
    working_directory: str = ""
    role: str = ""
    # thinking policy of the local Worker from hermes_config
    # (empty / 0 / -1 = use the CLAUDE_RUNNER_THINK_* defaults)
    think_policy: str = ""
    think_policy_from_task: int = 0
    think_off_after_seconds: int = -1
    think_off_after_steps: int = -1


class CompactBody(BaseModel):
    mode: str = Field(default="", pattern="^(|auto|always|off)$")
    min_tokens: int | None = Field(default=None, ge=0)
    model: str = ""


class ReportFactsBody(BaseModel):
    digest: dict[str, Any]
    workdir: str = Field(min_length=1)
    since_ref: str = Field(min_length=1)


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


logger = logging.getLogger("claude_runner.api")


async def _send_confirm_notice(notifier: Notifier, created: HostActionRequest) -> None:
    text = (
        f"Host action {created.id} needs confirmation: service {created.service}, "
        f"action {created.action}, reason: {scrub(created.reason)}"
    )
    try:
        delivered = await notifier.send(text)
    except Exception:
        logger.exception("host action %s: confirm notice failed", created.id)
        return
    if not delivered:
        logger.warning("host action %s: confirm notice not delivered", created.id)




def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    host_actions_on = host_actions_enabled()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        manager = RunManager(settings)
        app.state.manager = manager
        app.state.qa_requests = QaRequestStore(str(settings.data_dir / "qa_requests.json"))
        if host_actions_on:
            app.state.host_actions = HostActionStore(str(settings.data_dir / "host_actions.json"))
        # the board's stats routes read the run data through the manager, so
        # the board routes must be attached only once the manager exists
        add_board_routes(app, settings)
        # a failing store at startup must not stop the app: keep the env blocks
        secrets = ProviderSecrets(
            settings.secrets_file or settings.data_dir / "provider-secrets.json"
        )
        registry = ProviderRegistry(
            settings.providers,
            providers_db=settings.providers_db,
            store=app.state.board_store,
            secrets=secrets,
        )  # type: ignore[arg-type]
        app.state.provider_registry = registry
        manager.extra_provider = registry.db_profile
        app.state.provider_poll_stop = registry.start_polling()
        app.state.interrupted = await manager.startup()
        app.state.cli_version = await _cli_version(settings)
        switch_task = None
        policy = settings.think_policy == "off_first"
        if (
            settings.think_flag_file
        ):  # the workflow may send its own settings per run (hermes_config)
            switch = ThinkSwitch(
                manager.store,
                Path(settings.think_flag_file),
                settings.think_off_after_seconds,
                manager.events,
                policy=policy,
                policy_from_task=settings.think_policy_from_task,
                after_steps=settings.think_off_after_steps,
                default_policy=settings.think_policy,
                loop_repeats=settings.think_loop_repeats,
                loop_hold_steps=settings.think_loop_hold_steps,
                noedit_steps=settings.think_stuck_noedit_steps,
                error_repeats=settings.think_stuck_errors,
            )
            manager.on_run_start = (
                switch.tick
            )  # the policy applies before the first request of the run, not 20 s later
            manager.think_flag = (
                switch.flag
            )  # the run's first thinking mode goes to its event log (the local price depends on it)
            switch_task = asyncio.create_task(switch.run())
        guard_task = None
        if (
            settings.mem_guard_proc_mb > 0
        ):  # kills a Worker process that eats the container's memory (mem_guard.py)
            guard = MemGuard(
                manager.store,
                manager.events,
                proc_limit_mb=settings.mem_guard_proc_mb,
                cgroup_percent=settings.mem_guard_cgroup_percent,
            )
            guard_task = asyncio.create_task(guard.run())
        try:
            yield
        finally:
            if switch_task is not None:
                switch_task.cancel()
            if guard_task is not None:
                guard_task.cancel()
            if app.state.provider_poll_stop is not None:
                app.state.provider_poll_stop.set()
            await manager.shutdown()

    app = FastAPI(title="claude-runner", version="0.11.910", lifespan=lifespan)

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

    def qa_store(request: Request) -> QaRequestStore:
        return request.app.state.qa_requests  # type: ignore[no-any-return]

    def host_store(request: Request) -> HostActionStore:
        return request.app.state.host_actions  # type: ignore[no-any-return]

    def authorize_operator(authorization: str = Header(default="")) -> None:
        # dashboard-only secret: the operator route refuses the shared Worker key
        if not settings.host_actions_operator_key:
            raise RunnerError("unauthorized", "Missing or invalid bearer token.", 401)
        token = authorization.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), settings.host_actions_operator_key.encode()):
            raise RunnerError("unauthorized", "Missing or invalid bearer token.", 401)

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
                think_policy=body.think_policy.strip().lower(),
                think_policy_from_task=body.think_policy_from_task,
                think_off_after_seconds=body.think_off_after_seconds,
                think_off_after_steps=body.think_off_after_steps,
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

    @app.get("/v1/providers", dependencies=[Depends(authorize)])
    async def list_providers(request: Request) -> dict[str, Any]:
        registry: ProviderRegistry = request.app.state.provider_registry
        return {"providers": list(registry.views().values())}

    @app.get("/v1/providers/env-proposals", dependencies=[Depends(authorize)])
    async def env_provider_proposals(request: Request) -> dict[str, Any]:
        """The env provider blocks as import rows (name, base URL, key set or not); no key value."""
        registry: ProviderRegistry = request.app.state.provider_registry
        return {"providers": [env_provider_row(p) for p in registry.env_profiles().values()]}

    @app.post("/v1/providers/reload", dependencies=[Depends(authorize)])
    async def reload_providers(request: Request) -> dict[str, Any]:
        registry: ProviderRegistry = request.app.state.provider_registry
        registry.reload()
        return {"providers": list(registry.views().values())}

    @app.get("/v1/processes", dependencies=[Depends(authorize)])
    async def list_processes(request: Request) -> list[dict[str, Any]]:
        """The CLI processes this runner started, from its own registry (T011).
        Each entry has the pid, age, the run's session id, the run id and the run's
        status: no environment values and no command line."""
        return manager(request).list_processes()

    @app.post("/v1/processes/{pid}/terminate", dependencies=[Depends(authorize)])
    async def terminate_process(pid: int, request: Request) -> dict[str, Any]:
        """Stop one of the runner's own CLI processes by pid (T011). Refused with
        404 ``process_not_found`` (not in the registry, or foreign to this runner)
        and 409 ``run_active`` (the run is queued or running); answered with the
        terminate outcome otherwise."""
        result = await manager(request).terminate_pid(pid)
        outcome = result["outcome"]
        if outcome in ("not_found", "not_owned"):
            raise RunnerError(
                "process_not_found",
                f"Process {pid} is not tracked by this runner.",
                404,
            )
        if outcome == "run_active":
            raise RunnerError("run_active", f"Run {result['run_id']} is {result['status']}.", 409)
        return result

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

    @app.get("/v1/search", dependencies=[Depends(authorize)])
    async def search_endpoint(
        request: Request,
        q: str = Query(""),
        limit: int = Query(DEFAULT_LIMIT, ge=0),
        role: list[str] = Query([]),  # noqa: B008
        model: list[str] = Query([]),  # noqa: B008
        provider: list[str] = Query([]),  # noqa: B008
        cwd: list[str] = Query([]),  # noqa: B008
        status: list[str] = Query([]),  # noqa: B008
        session_id: list[str] = Query([]),  # noqa: B008
    ) -> dict[str, Any]:
        """Search the per-run event logs (case-insensitive substring), the same filters the
        dashboard uses. Runs on a thread so a slow scan never blocks the event loop."""
        runs = manager(request)
        filters = {
            "role": role,
            "model": model,
            "provider": provider,
            "cwd": cwd,
            "status": status,
            "session_id": session_id,
        }
        result = await asyncio.to_thread(
            run_search,
            runs.events.directory,
            q,
            filters,
            store=runs.store,
            limit=min(limit, MAX_LIMIT),
        )
        return result

    @app.post("/v1/sessions/{session_id}/compact", dependencies=[Depends(authorize)])
    async def compact(
        session_id: str, request: Request, body: CompactBody | None = None
    ) -> dict[str, Any]:
        body = body or CompactBody()
        return await manager(request).compact(session_id, body.mode, body.min_tokens, body.model)

    @app.post("/v1/qa/requests", dependencies=[Depends(authorize)])
    def create_qa_request(body: QaRequestBody, request: Request) -> dict[str, Any]:
        layers: list[str] | None = list(body.layers) if body.layers else None
        created = qa_store(request).create(
            body.mission_id,
            body.working_directory,
            body.mode,
            body.nonce,
            layers,
            body.options,
        )
        return qa_store(request).request_dict(created)

    @app.get("/v1/qa/requests", dependencies=[Depends(authorize)])
    def list_qa_requests(request: Request, status: QaStatus | None = None) -> list[dict[str, Any]]:
        store = qa_store(request)
        return [store.request_dict(item) for item in store.list_requests(status)]

    @app.get("/v1/qa/requests/{request_id}", dependencies=[Depends(authorize)])
    def get_qa_request(request_id: str, request: Request) -> dict[str, Any]:
        found = qa_store(request).get(request_id)
        if found is None:
            raise RunnerError("qa_request_not_found", f"QA request {request_id} not found.", 404)
        return qa_store(request).request_dict(found)

    @app.post("/v1/qa/requests/{request_id}/claim", dependencies=[Depends(authorize)])
    def claim_qa_request(request_id: str, request: Request) -> dict[str, Any]:
        with _qa_refusals(request_id):
            claimed = qa_store(request).request_dict(qa_store(request).claim(request_id))
        qa_run_record.start(manager(request), claimed)
        return claimed

    @app.post("/v1/qa/requests/{request_id}/result", dependencies=[Depends(authorize)])
    def finish_qa_request(
        request_id: str, body: QaResultBody, request: Request
    ) -> dict[str, Any]:
        with _qa_refusals(request_id):
            finished = qa_store(request).request_dict(
                qa_store(request).finish(
                    request_id,
                    [
                        v if isinstance(v, str) else v.model_dump(exclude_none=True)
                        for v in body.verdicts
                    ],
                    body.error,
                )
            )
        qa_run_record.finish(manager(request), finished)
        return finished

    @app.post("/v1/report-facts", dependencies=[Depends(authorize)])
    def report_facts(body: ReportFactsBody, request: Request) -> dict[str, str]:
        workdir = manager(request).resolve_cwd(body.workdir)
        try:
            digest = parse_digest(body.digest)
            repo_facts = collect_repo_facts(workdir, body.since_ref)
            markdown = build_pack(digest, repo_facts)
        except ValueError as exc:
            raise RunnerError("invalid_report_facts", str(exc)) from exc
        return {"markdown": markdown}
    if host_actions_on:
        host_notifier = Notifier()

        @app.post("/v1/host-actions", dependencies=[Depends(authorize)])
        async def create_host_action(request: Request) -> dict[str, Any]:
            body = await _json_body(request, HostActionBody)
            try:
                created = host_store(request).create(
                    body.action, body.service, body.reason, "worker"
                )
            except WhitelistError as exc:
                raise RunnerError("host_action_refused", str(exc), 400) from None
            except HostActionStoreFullError as exc:
                raise RunnerError("host_action_store_full", str(exc), 409) from None
            if created.tier == TIER_CONFIRM:
                await _send_confirm_notice(host_notifier, created)
            return asdict(created)

        @app.get("/v1/host-actions", dependencies=[Depends(authorize)])
        def list_host_actions(
            request: Request, status: HostActionState | None = None
        ) -> list[dict[str, Any]]:
            return [asdict(item) for item in host_store(request).list_requests(status)]

        @app.get("/v1/host-actions/{action_id}", dependencies=[Depends(authorize)])
        def get_host_action(action_id: str, request: Request) -> dict[str, Any]:
            found = host_store(request).get(action_id)
            if found is None:
                raise RunnerError(
                    "host_action_not_found", f"host action {action_id} not found.", 404
                )
            return asdict(found)

        @app.post("/v1/host-actions/claim", dependencies=[Depends(authorize)])
        def claim_host_action(request: Request) -> dict[str, Any]:
            claimed = host_store(request).claim()
            if claimed is None:
                raise RunnerError(
                    "host_action_none_ready", "No confirmed host action to claim.", 409
                )
            return asdict(claimed)

        @app.post("/v1/host-actions/{action_id}/result", dependencies=[Depends(authorize)])
        async def finish_host_action(action_id: str, request: Request) -> dict[str, Any]:
            body = await _json_body(request, HostActionResultBody)
            with _host_action_refusals(action_id):
                return asdict(host_store(request).report(action_id, body.ok, body.message))

        @app.post(
            "/v1/host-actions/{action_id}/operator", dependencies=[Depends(authorize_operator)]
        )
        async def operator_host_action(
            action_id: str, request: Request
        ) -> dict[str, Any]:
            body = await _json_body(request, HostActionOperatorBody)
            with _host_action_refusals(action_id):
                if body.approved:
                    return asdict(host_store(request).confirm(action_id))
                return asdict(host_store(request).reject(action_id))

    return app
