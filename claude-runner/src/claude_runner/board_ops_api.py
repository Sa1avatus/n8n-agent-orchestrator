"""Board ops as HTTP routes, built the same way as the mission board routes.

The store (``board_ops_store`` / ``board_ops_store_pg.get_ops_store``) owns the
data: every read here goes through the store's snapshot and settings reads,
never through a host file or ``.env``.  Every response passes through
``sanitize_result`` (``board_ops_redact``) so no credential-like string —
and in particular no token or chat_id value — can reach the wire, except the
confirmation token the ``prepare`` response hands to the client: it is the
real 32-hex credential the client keeps, so it is set after ``sanitize_result``
has scrubbed the rest of the payload and is never repeated in any other
response or log.

Routes (prefix ``/api/board/ops``):

- ``GET /status`` — agent status, the process panel and the
  ``actions_enabled`` flag (the UI disables actions while the agent is stale).
- ``GET /watchers`` — parsed watchdog, the VM memory series, the
  ``queue_watch`` notifications and the ``tg_relay`` status; the ``tg_relay``
  token and chat_id are reported by :func:`presence` only.
- ``GET /environment`` — models/providers, the verified pairs, the llama
  mode/presets, the memory limits, the ``mem_guard`` events and the display-
  only hardware info.
- ``GET /settings`` — the current settings with their versions.
- ``PUT /settings`` — validation (422) and a version conflict (409); a
  successful write returns the diff and the new versions.
- ``POST /commands/prepare`` — validates an action and its args; a dangerous
  action answers with a pending confirmation object carrying the real
  ``confirm_token`` (the 32-hex credential the client keeps), the expiry,
  consequences and preview (no row written); a safe action is enqueued
  directly after the rate-limit and single-running checks.  A stale agent
  heartbeat (older than 30 s) refuses every prepare with 503.
- ``POST /commands/confirm`` — consumes the client-held ``token`` (400 when
  missing, expired, unknown, reused or mismatched), re-checks the rate limit
  and the single-running invariant, then inserts the command row with the
  consumed token stored for the audit trail.
- ``GET /commands`` — the command journal with optional filters (``action``,
  ``status``, ``requested_by``, ``since``, ``limit``); a read reaps stale
  ``running`` commands first.
- ``POST /commands/{command_id}/repeat`` — re-queues a finished command when
  its action is repeatable and safe; dangerous, non-repeatable and unknown
  actions are refused with 422.
- ``GET /livestate/{mission}/flags`` — the mission's Telegram control flags
  and their version; ``PUT /livestate/{mission}/flags`` — whitelisted flag
  changes with the optimistic version check (409 on drift, 422 on a bad
  flag name or type).

``add_ops_routes`` mirrors ``board_api.add_board_routes``: it keeps a store
set before lifespan (the in-memory store in tests), otherwise creates the
store from the DSN, and registers the exception handlers on the app.  The
module-level ``router`` is the pre-built router, importable on its own.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from . import board_ops_actions, board_ops_parsers, board_ops_settings, board_ops_state
from .board import ConflictError, now
from .board_ops_guard import (
    ConfirmTokens,
    EnqueueError,
    RateLimiter,
    RateLimitError,
    TokenError,
    check_can_enqueue,
    expire_stale,
)
from .board_ops_livestate import InMemoryLiveStateStore, LiveStateStore, PgLiveStateStore
from .board_ops_redact import presence, sanitize_result
from .board_ops_store import Command, OpsStore
from .board_ops_store_pg import UnavailableOpsStore, get_ops_store
from .board_pg import DSN_ENV, _redact_dsn

__all__ = ["router", "add_ops_routes"]


def _error(exc: Exception, code: str, status: int) -> JSONResponse:
    """The runner's error envelope, in the same shape as ``board_api._error``."""
    return JSONResponse(
        status_code=status,
        content={
            "status": "failed",
            "error": {"code": code, "message": str(exc)},
            "code": code,
            "status_code": status,
        },
    )


def _unavailable(store: OpsStore) -> dict[str, Any] | None:
    """The 503 body when the ops store has no backend (the reason never
    carries the DSN)."""
    if hasattr(store, "status"):
        status, reason = store.status()
        if status == "unavailable":
            return {"code": "unavailable", "reason": reason}
    return None


def _store_guard(request: Request) -> OpsStore:
    """The ops store, raising ``RuntimeError`` (mapped to 503) when it is
    unavailable, like ``board_api._store_guard``."""
    store = request.app.state.board_ops_store
    refuse = _unavailable(store)
    if refuse is not None:
        raise RuntimeError(refuse["reason"])
    return store


def _latest_snapshot(store: OpsStore) -> dict[str, Any] | None:
    """The newest ``board.host_snapshot`` row's ``data_json`` (``None`` when
    the agent never published one)."""
    rows = store.get_snapshot("host_snapshot", limit=1)
    if not rows:
        return None
    data = rows[0].get("data_json")
    return data if isinstance(data, dict) else None


def _agent_heartbeat(store: OpsStore) -> str | None:
    """The newest agent heartbeat stamp (the ``alive``/``stale`` decision)."""
    agent = store.get_agent()
    return agent.heartbeat_at if agent is not None else None


def _panel_now(store: OpsStore, snapshot: dict[str, Any] | None) -> str:
    """The clock the ages are computed against: the snapshot's own ``at`` when
    present, else the store clock (``now()``; the Pg store's ``board.now`` is
    the connection's ``now()``)."""
    if isinstance(snapshot, dict) and snapshot.get("at"):
        return snapshot["at"]
    return now()


def _settings_map(store: OpsStore) -> dict[str, dict[str, Any]]:
    """The registered settings as ``{key: {value, version}}``; keys absent
    from the store fall back to their default."""
    result: dict[str, dict[str, Any]] = {}
    for key in board_ops_settings.REGISTRY:
        row = store.get_setting(key)
        if row is None:
            result[key] = {"value": board_ops_settings.defaults()[key], "version": None}
        else:
            result[key] = {"value": row.value_json, "version": row.updated_at}
    return result


def _worker_runs(request: Request) -> list[dict[str, Any]]:
    """The worker run summaries the process panel needs, when the runner is
    reachable (otherwise ``[]``)."""
    manager = getattr(request.app.state, "manager", None)
    if manager is None:
        return []
    try:
        from .run_views import summary

        runs = manager.store.list_runs(200)
        return [summary(run, manager) for run in runs]
    except Exception:
        return []


def _section(snapshot: dict[str, Any] | None, name: str) -> dict[str, Any]:
    """The ``snapshot[name]`` section as a dict (``{}`` when absent)."""
    value = (snapshot or {}).get(name)
    return value if isinstance(value, dict) else {}


def _raw(value: Any) -> str:
    """The raw-text field of a section (``""`` when missing)."""
    section = value if isinstance(value, dict) else {}
    text = section.get("raw_text")
    return text if isinstance(text, str) else ""


# ---------------------------------------------------------------------------
# routers
# ---------------------------------------------------------------------------


def _build_router() -> APIRouter:
    router = APIRouter(prefix="/api/board/ops", tags=["board ops"])

    # ------------------------------------------------------------------ status
    @router.get("/status")
    def status(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        heartbeat = _agent_heartbeat(store)
        snapshot = _latest_snapshot(store)
        panel_now = _panel_now(store, snapshot)
        panel = board_ops_state.process_panel(snapshot, _worker_runs(request))
        alive = board_ops_state.agent_status(heartbeat, panel_now)
        tg_relay = panel.get("tg_relay") or {}
        # The panel carries the tg_relay section raw (token and chat_id); the
        # response must expose them by presence only.
        tg_relay_view = {
            k: v
            for k, v in (tg_relay if isinstance(tg_relay, dict) else {}).items()
            if k not in ("token", "chat_id")
        }
        tg_relay_view["token"] = presence(
            tg_relay.get("token") if isinstance(tg_relay, dict) else None
        )
        tg_relay_view["chat_id"] = presence(
            tg_relay.get("chat_id") if isinstance(tg_relay, dict) else None
        )
        panel["tg_relay"] = tg_relay_view
        return sanitize_result(
            {
                "agent": {
                    "status": alive,
                    "heartbeat_at": heartbeat,
                    "actions_enabled": alive == "alive",
                },
                "process_panel": panel,
                "actions_enabled": alive == "alive",
            }
        )

    # ------------------------------------------------------------------ watchers
    @router.get("/watchers")
    def watchers(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        snapshot = _latest_snapshot(store)
        panel = board_ops_state.process_panel(snapshot, _worker_runs(request))

        watchdog = _section(snapshot, "docker_watchdog")
        vm_points = _section(snapshot, "docker_vm")
        queue_watch = panel.get("queue_watch") or {}
        tg_relay = panel.get("tg_relay") or {}

        return sanitize_result(
            {
                "watchdog": board_ops_parsers.parse_watchdog_csv(_raw(watchdog)),
                "vm_memory": board_ops_parsers.parse_vm_csv(_raw(vm_points)),
                "queue_watch": queue_watch,
                "notices_per_hour": queue_watch.get("notices_per_hour", 0)
                if isinstance(queue_watch, dict)
                else 0,
                "tg_relay": {
                    "alive": tg_relay.get("alive") if isinstance(tg_relay, dict) else None,
                    "last_update_id": tg_relay.get("last_update_id")
                    if isinstance(tg_relay, dict)
                    else None,
                    "token": presence(
                        tg_relay.get("token") if isinstance(tg_relay, dict) else None
                    ),
                    "chat_id": presence(
                        tg_relay.get("chat_id") if isinstance(tg_relay, dict) else None
                    ),
                },
                "queue_log_tail": store.get_queue_log(limit=200),
            }
        )

    # ------------------------------------------------------------------ environment
    @router.get("/environment")
    def environment(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        snapshot = _latest_snapshot(store)

        hermes = _section(snapshot, "hermes_config")
        models = _section(snapshot, "models")
        memory = _section(snapshot, "memory_limits")
        hardware = _section(snapshot, "hardware")
        mem_guard = _section(snapshot, "mem_guard_events")

        settings_map = _settings_map(store)
        verified_pairs = settings_map["verified_model_pairs"]["value"]

        return sanitize_result(
            {
                "models_providers": models,
                "hermes_config": hermes,
                "verified_model_pairs": verified_pairs,
                "llama": {
                    "mode": models.get("llama_mode"),
                    "presets": models.get("llama_presets") or [],
                },
                "memory_limits": memory,
                "mem_guard_events": board_ops_parsers.parse_mem_guard_events(
                    _raw(mem_guard)
                ),
                "hardware": hardware,
            }
        )

    # ------------------------------------------------------------------ settings
    @router.get("/settings")
    def get_settings(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return sanitize_result(
            {
                "settings": _settings_map(store),
                "known_keys": sorted(board_ops_settings.REGISTRY),
            }
        )

    @router.put("/settings")
    def put_settings(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        who = body.pop("who", "")
        versions_in = body.pop("versions", None)
        changes = {k: v for k, v in body.items() if k not in ("who", "versions")}
        if not changes:
            return _error(ValueError("no settings keys to update"), "validation", 422)

        # The caller's expected versions (per key), read at the moment the
        # caller last saw the settings (GET /settings); a missing entry
        # disables the check for that key.  Keys outside the registry are
        # a validation error.
        versions: dict[str, str | None] = {}
        for key in changes:
            if key not in board_ops_settings.REGISTRY:
                return _error(
                    ValueError(
                        "unknown setting key "
                        f"{key!r} (known keys: {', '.join(sorted(board_ops_settings.REGISTRY))})"
                    ),
                    "validation",
                    422,
                )
            versions[key] = versions_in.get(key) if isinstance(versions_in, dict) else None

        # The value the caller will see changing.  A key not yet in the store
        # shows its default (the value GET /settings reported for it), so the
        # diff's "old" matches what the caller saw.
        old = {
            key: (store.get_setting(key).value_json if store.get_setting(key)
                  else board_ops_settings.defaults()[key])
            for key in changes
        }

        # Validate before any write: 422 on a bad value.
        errors: list[str] = []
        for key, value in changes.items():
            errors.extend(board_ops_settings.validate(key, value))
        if errors:
            return _error(ValueError("; ".join(errors)), "validation", 422)

        # ConflictError is raised by ``apply`` and mapped to 409 below.
        written = board_ops_settings.apply(store, store.changelog(), who, changes, versions)

        new_settings = _settings_map(store)
        diff = {
            key: {"old": old[key], "new": changes[key]}
            for key in changes
            if old[key] != changes[key]
        }
        return sanitize_result(
            {
                "settings": new_settings,
                "diff": diff,
                "written": [e.model_dump() for e in written],
            }
        )

    # ------------------------------------------------------------ commands: prepare
    @router.post("/commands/prepare")
    def prepare_command(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        _expire_stale(store)
        stale = _stale_agent_error(store)
        if stale is not None:
            return stale

        action = body.get("action")
        args = body.get("args")
        if not isinstance(args, dict):
            args = {}
        who = body.get("who", "")
        if not isinstance(who, str):
            who = ""
        pairs = _verified_pairs(store)
        errors = board_ops_actions.validate_action(action, args, pairs)
        if errors:
            return _error(ValueError("; ".join(errors)), "validation", 422)

        state = _boundary_state(store, request)
        boundary_errors = board_ops_state.check_boundary(action, state)
        if boundary_errors:
            return _error(ValueError("; ".join(boundary_errors)), "validation", 422)

        entry = board_ops_actions.ACTIONS[action]
        if entry["dangerous"]:
            # Pending confirmation: no row is written until the token is
            # confirmed (POST /commands/confirm).  The token is a real
            # 32-hex credential, so it is handed to the client in
            # ``confirm_token`` and must not appear anywhere else: the rest
            # of the payload is scrubbed by ``sanitize_result`` first, and
            # the token is set afterwards (scrub would mask it otherwise).
            tokens = request.app.state.board_ops_confirm_tokens
            token, expires_at, consequences = tokens.issue(action, args, who)
            check_can_enqueue(store, action, args)
            response = sanitize_result(
                {
                    "action": action,
                    "args": args,
                    "expires_at": expires_at,
                    "consequences": consequences,
                    "command_preview": entry["command_preview"](args),
                    "interrupted_preview": board_ops_state.interrupted_preview(state),
                }
            )
            response["confirm_token"] = token
            return response

        _check_rate_limit(store)
        check_can_enqueue(store, action, args)
        command = store.insert_command(action, args, who or None)
        return sanitize_result({"command": _command_dict(command)})

    # ------------------------------------------------------------ commands: confirm
    @router.post("/commands/confirm")
    def confirm_command(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        _expire_stale(store)
        stale = _stale_agent_error(store)
        if stale is not None:
            return stale

        action = body.get("action")
        args = body.get("args")
        if not isinstance(args, dict):
            args = {}
        who = body.get("who", "")
        if not isinstance(who, str):
            who = ""

        # Validate before consuming: a bad request must not spend the token.
        pairs = _verified_pairs(store)
        errors = board_ops_actions.validate_action(action, args, pairs)
        if errors:
            return _error(ValueError("; ".join(errors)), "validation", 422)
        state = _boundary_state(store, request)
        boundary_errors = board_ops_state.check_boundary(action, state)
        if boundary_errors:
            return _error(ValueError("; ".join(boundary_errors)), "validation", 422)

        # The client-held credential from the prepare response.  Single-use:
        # consumed before any rate-limit or single-running check, and the row
        # is written only after all the checks pass.  ``consume`` reports
        # expired, unknown, reused or mismatched tokens as a 400.
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise TokenError("token missing or not a string")

        tokens = request.app.state.board_ops_confirm_tokens
        tokens.consume(token, action, args, who)

        _check_rate_limit(store)
        check_can_enqueue(store, action, args)
        command = store.insert_command(action, args, who or None, confirm_token=token)
        return sanitize_result({"command": _command_dict(command)})

    # ------------------------------------------------------------ commands: journal
    @router.get("/commands")
    def list_commands(
        request: Request,
        action: str | None = Query(None),
        status: str | None = Query(None),
        requested_by: str | None = Query(None),
        since: str | None = Query(None),
        limit: int = Query(200, ge=0),
    ) -> dict[str, Any]:
        store = _store_guard(request)
        _expire_stale(store)
        rows = store.list_commands(
            action=action,
            status=status,
            requested_by=requested_by,
            since=since,
            limit=limit,
        )
        return sanitize_result(
            {"commands": [_command_dict(c) for c in rows], "count": len(rows)}
        )

    # ------------------------------------------------------------ commands: repeat
    @router.post("/commands/{command_id}/repeat")
    def repeat_command(
        request: Request,
        command_id: int,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        store = _store_guard(request)
        _expire_stale(store)
        stale = _stale_agent_error(store)
        if stale is not None:
            return stale

        command = next(
            (c for c in store.list_commands(limit=1000) if c.id == command_id),
            None,
        )
        if command is None:
            raise KeyError(f"command {command_id!r} not found")

        entry = board_ops_actions.ACTIONS.get(command.action)
        if entry is None:
            reason = f"unknown action {command.action!r}"
        elif entry["dangerous"]:
            reason = f"action {command.action!r} is dangerous and cannot be repeated"
        elif not entry["repeatable"]:
            reason = f"action {command.action!r} is not repeatable"
        else:
            reason = None
        if reason is not None:
            return _error(ValueError(reason), "validation", 422)

        args = command.args_json or {}
        pairs = _verified_pairs(store)
        errors = board_ops_actions.validate_action(command.action, args, pairs)
        if errors:
            return _error(ValueError("; ".join(errors)), "validation", 422)

        if isinstance(body, dict):
            who = body.get("who")
        else:
            who = None
        if not isinstance(who, str):
            who = command.requested_by
        if not isinstance(who, str):
            who = None

        _check_rate_limit(store)
        check_can_enqueue(store, command.action, args)
        new_command = store.insert_command(command.action, args, who)
        return sanitize_result({"command": _command_dict(new_command)})

    # ------------------------------------------------------------ live state flags
    @router.get("/livestate/{mission}/flags")
    def get_live_flags(request: Request, mission: str) -> dict[str, Any]:
        live = _live_state_store(request)
        return sanitize_result(live.get_flags(mission))

    @router.put("/livestate/{mission}/flags")
    def put_live_flags(
        request: Request, mission: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        live = _live_state_store(request)
        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("no flags to update")
        version = body.get("version")
        if version is not None and not isinstance(version, str):
            raise ValueError("version must be a string")
        who = body.get("who", "")
        if not isinstance(who, str):
            raise ValueError("who must be a string")
        result = live.set_flags(mission, changes, version, who)
        return sanitize_result(result)

    return router


router = _build_router()


def _live_state_store(request: Request) -> LiveStateStore:
    """The live-state flag store on the app: pre-set state is kept (tests set
    an in-memory store), otherwise the Pg store is built from the DSN (never
    printed in an error) or the in-memory store when no DSN is set."""
    live = getattr(request.app.state, "board_ops_livestate", None)
    if live is None:
        dsn = os.environ.get(DSN_ENV, "")
        if dsn:
            try:
                live = PgLiveStateStore(dsn)
            except Exception as exc:
                # psycopg's error text may embed the DSN — redact it before
                # reporting; the store falls back to in-memory.
                message = _redact_dsn(str(exc), dsn)
                live = InMemoryLiveStateStore()
                request.app.state.board_ops_livestate_error = message
        else:
            live = InMemoryLiveStateStore()
        request.app.state.board_ops_livestate = live
    return live


def _verified_pairs(store: OpsStore) -> list[dict[str, Any]] | None:
    """The current ``verified_model_pairs`` setting value (``None`` when unset,
    so validation falls back to the registry default)."""
    row = store.get_setting("verified_model_pairs")
    if row is None:
        return None
    value = row.value_json
    return value if isinstance(value, list) else None


def _command_dict(command: Command) -> dict[str, Any]:
    """The command row as a dict (who, when, what, status, sanitized result)."""
    return command.model_dump()


def _check_rate_limit(store: OpsStore) -> None:
    """Rate-limit the command rows: ``ops_rate_limit_per_min`` rows in the
    last 60 s; ``RateLimitError`` (429) when already reached."""
    setting = store.get_setting("ops_rate_limit_per_min")
    n = setting.value_json if setting is not None else board_ops_settings.defaults()["ops_rate_limit_per_min"]
    RateLimiter(n).check(store)


def _expire_stale(store: OpsStore) -> None:
    """Reap ``running`` commands older than the ``command_timeout_s`` setting
    (the store's stale reaper; the agent reaps its own rows while alive, so
    every read runs this too)."""
    setting = store.get_setting("command_timeout_s")
    timeout = (
        setting.value_json
        if setting is not None
        else board_ops_settings.defaults()["command_timeout_s"]
    )
    expire_stale(store, float(timeout))


def _stale_agent_error(store: OpsStore) -> JSONResponse | None:
    """The 503 refusal when the agent heartbeat is stale (older than 30 s):
    no command may be prepared, confirmed or repeated while the agent is
    stale, and a dead agent can never claim a command anyway."""
    snapshot = _latest_snapshot(store)
    heartbeat = _agent_heartbeat(store)
    status = board_ops_state.agent_status(heartbeat, _panel_now(store, snapshot))
    if status == "alive":
        return None
    return _error(
        RuntimeError(
            f"agent heartbeat is stale (status {status!r}); "
            "board ops commands are refused until the agent heartbeats again"
        ),
        "agent_stale",
        503,
    )


def _boundary_state(store: OpsStore, request: Request) -> dict[str, Any]:
    """The process-panel state for the task-boundary check: the published
    snapshot plus the queue-log tail the boundary rule reads."""
    snapshot = _latest_snapshot(store)
    panel = board_ops_state.process_panel(snapshot, _worker_runs(request))
    return {**panel, "queue_log": store.get_queue_log()}


def add_ops_routes(app: Any, settings: Any = None) -> None:
    """Attach the ops routes to a runner app, like ``board_api.add_board_routes``.

    A store set before lifespan (the in-memory store in tests) is kept instead
    of the DSN-driven one; otherwise the store is created from the DSN.  The
    confirmation tokens live on the app (in memory, single-use).  The
    exception handlers live on the FastAPI app (``APIRouter`` has none).
    """
    existing = getattr(app.state, "board_ops_store", None)
    if existing is not None and not isinstance(existing, UnavailableOpsStore):
        store = existing
    else:
        store = get_ops_store()
    app.state.board_ops_store = store

    app.state.board_ops_confirm_tokens = ConfirmTokens()

    # Specific guard errors before the generic RuntimeError handler (FastAPI
    # resolves the handler by the raised class, so 400/429/409 stay their own
    # codes instead of mapping to 503).
    app.add_exception_handler(ConflictError, _on_conflict)
    app.add_exception_handler(KeyError, _on_missing)
    app.add_exception_handler(TokenError, _on_token)
    app.add_exception_handler(RateLimitError, _on_rate_limit)
    app.add_exception_handler(EnqueueError, _on_enqueue)
    app.add_exception_handler(ValueError, _on_value)
    app.add_exception_handler(RuntimeError, _on_runtime)

    app.include_router(_build_router())


def _on_conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return _error(exc, "conflict", 409)


def _on_missing(_: Request, exc: KeyError) -> JSONResponse:
    return _error(exc, "not_found", 404)


def _on_token(_: Request, exc: TokenError) -> JSONResponse:
    return _error(exc, exc.code, exc.status_code)


def _on_rate_limit(_: Request, exc: RateLimitError) -> JSONResponse:
    return _error(exc, exc.code, exc.status_code)


def _on_enqueue(_: Request, exc: EnqueueError) -> JSONResponse:
    return _error(exc, exc.code, exc.status_code)


def _on_value(_: Request, exc: ValueError) -> JSONResponse:
    return _error(exc, "bad_request", 400)


def _on_runtime(_: Request, exc: RuntimeError) -> JSONResponse:
    return _error(exc, "unavailable", 503)
