"""The mission board as HTTP routes (the dashboard's board tab, scriptable).

The store (``board.get_board_store``) owns the data and the version checks:
every mutation here takes an ``expected_version`` (the row's ``updatedAt`` as
read by the caller) and the store's optimistic lock raises
:class:`ConflictError` (HTTP 409) on a mismatch; every write is audited to
``board.changelog`` by the store itself.  ``board_plan`` holds the server-side
guards (the task status is re-derived from the event log, never taken from
the browser); ``board_ready`` the readiness checks, the start guard, the
rollback and the atomic mission creation; ``board_stats`` the mission/task
cards (reusing ``mission_stats.stats_for_runs``); ``board_think`` the
thinking-policy statistics.  The runner service supplies the run data the
stats need.

Dangerous actions (deleting a mission, stopping or disabling a running one)
need ``confirm=true``; everything else writes through the store's own audited
writes.  Connection errors never leak the DSN: the store's unavailable stub
reports ``status()`` and its reason carries no host/port.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse

from . import board_plan, board_ready, board_stats, board_think
from .board import (
    BoardStore,
    ConflictError,
    GuardError,
    InMemoryBoardStore,
    QueueEntry,
    get_board_store,
    now,
)
from .board_pg import UnavailableBoardStore
from .board_plan import update_mission_config
from .board_ready import MissionIdError, create_mission, rollback, save_template, start_guard

__all__ = ["router", "add_board_routes"]


def _error(exc: Exception, code: str, status: int) -> JSONResponse:
    """The runner's error envelope for board errors."""
    return JSONResponse(
        status_code=status,
        content={
            "status": "failed",
            "error": {"code": code, "message": str(exc)},
            "code": code,
            "status_code": status,
        },
    )


_STATS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
STATS_TTL_FINISHED = 600.0
STATS_TTL_LIVE = 15.0


def _unavailable(store: BoardStore) -> dict[str, Any] | None:
    """503 with the store's own reason when the backend is unreachable
    (the reason never carries the DSN)."""
    if hasattr(store, "status"):
        status, reason = store.status()
        if status == "unavailable":
            return {"code": "unavailable", "reason": reason}
    return None


def _store_guard(request: Request) -> BoardStore:
    """The board store, with a 503 when it is unavailable (the runner's
    error shape, so a client sees one consistent envelope)."""
    store = request.app.state.board_store
    refuse = _unavailable(store)
    if refuse is not None:
        raise RuntimeError(refuse["reason"])
    return store


def _queue_version(store: BoardStore) -> str | None:
    """The queue's version: the in-memory store keeps one stamp for the whole
    queue; the Pg store has no queue version of its own, so ``None`` (the
    optimistic check is then off)."""
    version = getattr(store, "_queue_version", None)
    if callable(version):  # the Pg store computes it (a method), the in-memory store keeps a stamp
        version = version()
    return version or None


def _state_key_of(store: BoardStore, mission_id: str) -> str:
    mission = store.get_mission(mission_id)
    if mission is None:
        raise KeyError(f"mission {mission_id!r} not found")
    return mission.state_namespace


def _live_plan(store: BoardStore, state_key: str) -> dict[str, Any]:
    live = store.get_live_state(state_key)
    return dict(live.plan or {}) if live is not None else {}


def _plan_guard(store: BoardStore, mission_id: str) -> dict[str, Any]:
    """The board data a plan edit needs: the live row's status/position and
    the event log (the statuses are re-derived server-side)."""
    state_key = _state_key_of(store, mission_id)
    live = store.get_live_state(state_key)
    return {
        "state_key": state_key,
        "live_status": live.status if live is not None else None,
        "live_task_index": live.task_index if live is not None else None,
        "live_task_attempt": live.task_attempt if live is not None else None,
        "events": store.events(state_key),
        "stored_version": live.updated_at if live is not None else None,
    }


def _confirm_needed(running: bool, confirmed: bool, action: str) -> None:
    """Refuse a dangerous action on a running mission without confirm=true."""
    if running and not confirmed:
        raise GuardError(f"{action}: the mission is running; pass confirm=true")


def _config_version(store: BoardStore, mission_id: str) -> str | None:
    """The mission-config row's version, from its ``updatedAt`` stamp."""
    if isinstance(store, InMemoryBoardStore):
        return store._mission_config_version.get(mission_id, None)
    config = store.mission_config(mission_id)
    return (config or {}).get("updated_at")


def add_board_routes(app: Any, settings: Any) -> None:
    """Attach the board routes to a runner app (``create_app``).

    The store is created from the DSN (``DASHBOARD_BOARD_PG_DSN``); without it
    the routes answer 503 with the store's own reason (no DSN leaked)."""
    # a store set before lifespan (the in-memory store in tests) is kept
    # instead of the DSN-driven one
    existing = getattr(app.state, "board_store", None)
    if existing is not None and not isinstance(existing, UnavailableBoardStore):
        store = existing
    else:
        store = get_board_store()
    app.state.board_store = store
    app.state.settings = settings

    # exception handlers must live on the FastAPI app (APIRouter has none)
    app.add_exception_handler(ConflictError, _on_conflict)
    app.add_exception_handler(GuardError, _on_guard)
    app.add_exception_handler(MissionIdError, _on_mission_id)
    app.add_exception_handler(KeyError, _on_missing)
    app.add_exception_handler(ValueError, _on_value)
    app.add_exception_handler(RuntimeError, _on_runtime)

    app.include_router(_build_router())


def _on_conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return _error(exc, "conflict", 409)


def _on_guard(_: Request, exc: GuardError) -> JSONResponse:
    return _error(exc, "guard", 403)


def _on_mission_id(_: Request, exc: MissionIdError) -> JSONResponse:
    return _error(exc, "bad_mission_id", 400)


def _on_missing(_: Request, exc: KeyError) -> JSONResponse:
    return _error(exc, "not_found", 404)


def _on_value(_: Request, exc: ValueError) -> JSONResponse:
    return _error(exc, "bad_request", 400)


def _on_runtime(_: Request, exc: RuntimeError) -> JSONResponse:
    return _error(exc, "unavailable", 503)


def _build_router() -> APIRouter:
    """The mission board as an APIRouter under ``/api/board``.

    The module-level ``router`` is this object, importable on its own:
    ``router = _build_router()``.  The store (``app.state.board_store``) owns
    the data and the version checks; every mutation here is audited by the
    store to ``board.changelog``."""
    router = APIRouter(prefix="/api/board", tags=["board"])

    # ------------------------------------------------------------------ status
    @router.get("/status", response_model=None)
    def status(request: Request) -> dict[str, Any] | JSONResponse:
        store = request.app.state.board_store
        refuse = _unavailable(store)
        if refuse is not None:
            return JSONResponse(status_code=503, content=refuse)
        return {"status": "ok"}

    # ------------------------------------------------------------------ missions
    @router.get("/missions")
    def list_missions(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return {
            "groups": store.mission_groups(),
            "missions": [m.model_dump() for m in store.list_missions()],
        }

    @router.get("/missions/{mission_id}")
    def get_mission(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        live = store.get_live_state(mission.state_namespace)
        return {
            "mission": mission.model_dump(),
            "live_state": live.model_dump() if live is not None else None,
            "task_views": [v.model_dump() for v in store.task_views(mission.state_namespace)],
            "config": store.mission_config(mission_id),
            "task_overrides": store.task_overrides(mission_id),
        }

    # ``create`` is registered before the ``{mission_id}`` route, so Starlette
    # matches the literal path instead of treating ``create`` as a mission id
    @router.post("/missions/create")
    def create(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        return create_mission(
            store,
            body.get("mission_id", ""),
            source_mission_id=body.get("source_mission_id"),
            template_name=body.get("template_name"),
            objective=body.get("objective", ""),
            plan=body.get("plan"),
            position=body.get("position"),
            queue_note=body.get("queue_note"),
            who=body.get("who", ""),
        )

    @router.post("/missions/{mission_id}")
    def update_mission(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        expected = body.pop("expected_version", mission.updated_at)
        fields = {k: v for k, v in body.items() if k not in ("who", "mission_id")}
        if not fields:
            raise ValueError("no fields to update")
        updated = store.update_mission(mission_id, expected, who=body.get("who", ""), **fields)
        return {"mission": updated.model_dump()}

    @router.delete("/missions/{mission_id}")
    def delete_mission(request: Request, mission_id: str,
                       confirm: bool = Query(False)) -> dict[str, Any]:
        """Delete a mission's row, its Live State row and its queue entry.

        Deleting a mission that is running needs ``confirm=true``; a
        disabled mission is always deletable.  Only the in-memory store
        supports deletion (the Pg store has no delete API)."""
        store = _store_guard(request)
        if not isinstance(store, InMemoryBoardStore):
            raise ValueError("deleting a mission is only supported by the in-memory store")
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        running = mission.mission_id in {m.mission_id for m in store.running_missions()}
        _confirm_needed(running, confirm, "deleting a running mission")
        with store._lock:
            store._audit(
                "mission delete",
                before=mission.model_dump(),
                after=None,
                mission_id=mission_id,
            )
            store._missions.pop(mission_id, None)
            store._live.pop(mission.state_namespace, None)
            store._queue.pop(mission_id, None)
            if mission_id in store._queue_order:
                store._queue_order.remove(mission_id)
            store._queue_audit("mission delete", [dict(e.model_dump()) for e in store.queue()])
            store._mission_config.pop(mission_id, None)
            store._mission_config_version.pop(mission_id, None)
        return {"deleted": mission_id}

    # ------------------------------------------------------------------ live state
    @router.get("/live-states")
    def list_live_states(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return {"live_states": [s.model_dump() for s in store.list_live_states()]}

    @router.get("/live-states/{state_key}")
    def get_live_state(request: Request, state_key: str) -> dict[str, Any]:
        store = _store_guard(request)
        live = store.get_live_state(state_key)
        if live is None:
            raise KeyError(f"live state {state_key!r} not found")
        return {"live_state": live.model_dump()}

    @router.post("/live-states/{state_key}")
    def upsert_live_state(request: Request, state_key: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        live = store.get_live_state(state_key)
        expected = body.pop("expected_version", live.updated_at if live is not None else None)
        fields = {k: v for k, v in body.items() if k not in ("who", "state_key")}
        if not fields:
            raise ValueError("no fields to update")
        updated = store.upsert_live_state(state_key, expected, who=body.get("who", ""), **fields)
        return {"live_state": updated.model_dump()}

    # ------------------------------------------------------------------ plan / tasks
    @router.get("/missions/{mission_id}/plan")
    def get_plan(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        live = store.get_live_state(data["state_key"])
        return {
            "plan": live.plan if live is not None and live.plan else None,
            "version": data["stored_version"],
            "tasks": [v.model_dump() for v in store.task_views(data["state_key"])],
        }

    @router.post("/missions/{mission_id}/tasks/{task_key}")
    def update_task(request: Request, mission_id: str, task_key: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        fields = {k: v for k, v in body.items() if k not in ("who",)}
        plan, payload = board_plan.update_task(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            task_key,
            data["live_status"],
            data["live_task_index"],
            data["live_task_attempt"],
            events=data["events"],
            **fields,
        )
        store.upsert_live_state(data["state_key"], None, plan=plan, who=body.get("who", ""))
        return {"plan": plan, "changelog": payload}

    @router.post("/missions/{mission_id}/tasks")
    def add_task(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        plan, payload = board_plan.add_task(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            body.get("title", ""),
            body.get("objective", ""),
            body.get("acceptance_criteria"),
            body.get("after_key"),
            data["live_status"],
            data["live_task_index"],
        )
        store.upsert_live_state(data["state_key"], None, plan=plan, who=body.get("who", ""))
        return {"plan": plan, "changelog": payload}

    @router.delete("/missions/{mission_id}/tasks/{task_key}")
    def delete_task(request: Request, mission_id: str, task_key: str,
                    confirm: bool = Query(False)) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        live = store.get_live_state(data["state_key"])
        running = live is not None and live.status == "active"
        _confirm_needed(running, confirm, "deleting a task of a running mission")
        plan, payload = board_plan.delete_task(
            _live_plan(store, data["state_key"]),
            data["stored_version"],
            data["stored_version"],
            task_key,
            data["live_status"],
            data["live_task_index"],
        )
        store.upsert_live_state(data["state_key"], None, plan=plan, who="api")
        return {"plan": plan, "changelog": payload}

    @router.post("/missions/{mission_id}/tasks/{task_key}/move")
    def move_task(request: Request, mission_id: str, task_key: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        plan, payload = board_plan.move_task(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            task_key,
            int(body.get("to_position", -1)),
            data["live_status"],
            data["live_task_index"],
        )
        store.upsert_live_state(data["state_key"], None, plan=plan, who=body.get("who", ""))
        return {"plan": plan, "changelog": payload}

    @router.post("/missions/{mission_id}/tasks/{task_key}/duplicate")
    def duplicate_task(request: Request, mission_id: str, task_key: str,
                       body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        plan, payload = board_plan.duplicate_task(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            task_key,
            body.get("after_key"),
            data["live_status"],
            data["live_task_index"],
        )
        store.upsert_live_state(data["state_key"], None, plan=plan, who=body.get("who", ""))
        return {"plan": plan, "changelog": payload}

    @router.post("/missions/{mission_id}/tasks/{task_key}/hint")
    def set_hint(request: Request, mission_id: str, task_key: str,
                body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        hint = body.get("hint")
        plan, payload = board_plan.set_task_hint(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            task_key,
            hint,
            data["live_status"],
            data["live_task_index"],
            data["live_task_attempt"],
            events=data["events"],
        )
        overrides = store.task_overrides(mission_id)
        row = next((o for o in overrides if o.get("task_key") == task_key), None)
        store.set_task_override(
            mission_id, task_key, hint, body.get("extra"),
            expected_version=row.get("updated_at") if row is not None else None,
            who=body.get("who", ""),
        )
        return {"plan": plan, "override": payload.get("override")}

    @router.post("/missions/{mission_id}/tasks/{task_key}/retry")
    def retry_task(request: Request, mission_id: str, task_key: str,
                   body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        plan, payload = board_plan.request_retry(
            _live_plan(store, data["state_key"]),
            expected,
            data["stored_version"],
            task_key,
            data["live_status"],
            data["live_task_index"],
            data["live_task_attempt"],
            events=data["events"],
        )
        live = store.get_live_state(data["state_key"])
        store.upsert_live_state(
            data["state_key"],
            live.updated_at if live is not None else None,
            task_attempt=payload["live_state"]["task_attempt"],
            who=body.get("who", ""),
        )
        return {"plan": plan, "changelog": payload}

    # ------------------------------------------------------------------ readiness / start / stop
    @router.get("/missions/{mission_id}/readiness")
    async def readiness(request: Request, mission_id: str,
                        host_visible: bool = Query(True),
                        preflight_max_age_seconds: float = Query(3600.0)) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        return await board_ready.collect_readiness(
            store, mission, request.app.state.settings,
            host_visible=host_visible,
            preflight_max_age_seconds=preflight_max_age_seconds,
        )

    @router.post("/missions/{mission_id}/start")
    async def start(request: Request, mission_id: str, confirmed: bool = Query(False)) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        # the start decision uses the mission's full readiness (the same
        # checks the /readiness endpoint reports): any error item blocks
        # the start unless the caller confirms it.
        summary = await board_ready.collect_readiness(store, mission, request.app.state.settings)
        return start_guard(summary, confirmed=confirmed)

    @router.post("/missions/{mission_id}/stop")
    def stop(request: Request, mission_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        live = store.get_live_state(mission.state_namespace)
        if live is None:
            raise KeyError(f"mission {mission_id!r} has no live state")
        running = live.status == "active"
        _confirm_needed(running, confirm, "stopping a running mission")
        store.upsert_live_state(mission.state_namespace, live.updated_at, status="stopped", who="api")
        return {"mission_id": mission_id, "status": "stopped"}

    @router.post("/missions/{mission_id}/disable")
    def disable(request: Request, mission_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        running = mission.mission_id in {m.mission_id for m in store.running_missions()}
        _confirm_needed(running, confirm, "disabling a running mission")
        store.update_mission(mission_id, mission.updated_at, enabled=False)
        return {"mission_id": mission_id, "enabled": False}

    # ------------------------------------------------------------------ rollback
    @router.post("/changelog/{changelog_id}/rollback")
    def rollback_entry(request: Request, changelog_id: int, who: str = Query("")) -> dict[str, Any]:
        store = _store_guard(request)
        return rollback(store, changelog_id, who=who)

    # ------------------------------------------------------------------ templates
    @router.get("/templates")
    def list_templates(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return {"templates": store.templates()}

    @router.post("/templates/{name}")
    def save_template_route(request: Request, name: str,
                           mission_id: str = Body(..., embed=True),
                           who: str = Body("", embed=True)) -> dict[str, Any]:
        store = _store_guard(request)
        template = save_template(store, mission_id, name, who=who)
        return {"template": template}

    @router.delete("/templates/{name}")
    def delete_template(request: Request, name: str) -> dict[str, Any]:
        store = _store_guard(request)
        if not any(t.get("name") == name for t in store.templates()):
            raise KeyError(f"template {name!r} not found")
        store.remove_template(name, expected_version=None)
        return {"deleted": name}

    # ------------------------------------------------------------------ queue
    @router.get("/queue")
    def get_queue(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return {"queue": [e.model_dump() for e in store.queue()], "version": _queue_version(store)}

    @router.post("/queue")
    def set_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        entries = [QueueEntry(**e) for e in body.get("entries", [])]
        updated = store.set_queue(entries, expected)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/add")
    def add_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        updated = store.add_queue(
            body.get("mission_id", ""),
            expected,
            position=body.get("position"),
            enabled=body.get("enabled", True),
            attempts_per_task=body.get("attempts_per_task"),
            retry_failed=body.get("retry_failed", False),
            note=body.get("note"),
        )
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.delete("/queue/{mission_id}")
    def remove_queue(request: Request, mission_id: str, confirm: bool = Query(False)) -> dict[str, Any]:
        store = _store_guard(request)
        expected = _queue_version(store)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        running = mission.mission_id in {m.mission_id for m in store.running_missions()}
        _confirm_needed(running, confirm, "removing a running mission from the queue")
        updated = store.remove_queue(mission_id, expected, confirmed=confirm)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/move")
    def move_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        updated = store.move_queue(body.get("mission_id", ""), int(body.get("to_position", -1)), expected)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/set-enabled")
    def set_queue_enabled(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        mission_id = body.get("mission_id", "")
        if not mission_id:
            raise ValueError("mission_id is required")
        updated = store.set_queue(
            [
                e.model_copy()
                if e.mission_id != mission_id
                else e.model_copy(update={"enabled": bool(body.get("enabled", True))})
                for e in store.queue()
            ],
            expected,
        )
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/reorder")
    def reorder_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        """Replace the queue order by mission id (ids keep their stored
        fields; unknown ids are refused)."""
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        ids = body.get("mission_ids", [])
        if not isinstance(ids, list) or not ids:
            raise ValueError("mission_ids must be a non-empty list")
        by_id = {e.mission_id: e for e in store.queue()}
        if not set(ids) <= set(by_id):
            raise ValueError(f"unknown mission id(s): {sorted(set(ids) - set(by_id))}")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate mission id(s)")
        updated = store.set_queue([by_id[i] for i in ids], expected)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/first")
    def first_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        updated = store.move_queue(body.get("mission_id", ""), 0, expected)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/last")
    def last_queue(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        expected = body.pop("expected_version", _queue_version(store))
        # the store clamps positions to [0, n-1], so "last" is n-1, not -1
        n = len(store.queue())
        updated = store.move_queue(body.get("mission_id", ""), n - 1, expected)
        return {"queue": [e.model_dump() for e in updated], "version": _queue_version(store)}

    @router.post("/queue/retry-not-completed")
    def retry_not_completed(request: Request) -> dict[str, Any]:
        """Re-enable every queued mission whose state is not ``completed``;
        the queue order and version stay untouched."""
        store = _store_guard(request)
        groups = store.mission_groups()
        ids = groups["not_completed"] + groups["queued"]
        if not ids:
            return {"queue": [e.model_dump() for e in store.queue()],
                    "version": _queue_version(store), "changed": []}
        expected = _queue_version(store)
        updated = store.set_queue(
            [
                e.model_copy()
                if e.mission_id not in ids
                else e.model_copy(update={"enabled": True})
                for e in store.queue()
            ],
            expected,
        )
        return {"queue": [e.model_dump() for e in updated],
                "version": _queue_version(store), "changed": sorted(ids)}

    # ------------------------------------------------------------------ config / flags / preflight
    @router.get("/missions/{mission_id}/config")
    def get_config(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        return {
            "config": store.mission_config(mission_id),
            "version": _config_version(store, mission_id),
        }

    @router.post("/missions/{mission_id}/config")
    def update_config(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Edit the mission config (``expected_version`` optimistic lock).

        ``dry_run=true`` previews the diff without writing: the store and the
        changelog stay untouched; a stale version still answers 409."""
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        running = mission.mission_id in {m.mission_id for m in store.running_missions()}
        config = store.mission_config(mission_id) or {}
        expected = body.pop("expected_version", None)
        fields = {k: v for k, v in body.items() if k not in ("who", "mission_id", "dry_run")}
        if not fields:
            raise ValueError("no fields to update")
        dry_run = bool(body.get("dry_run", False))
        new_config, payload = update_mission_config(
            config, expected, _config_version(store, mission_id),
            mission_running=running, **fields,
        )
        diff = {
            k: {"before": config.get(k), "after": v}
            for k, v in fields.items() if config.get(k) != v
        }
        if dry_run:
            return {
                "dry_run": True,
                "changed": bool(diff),
                "diff": diff,
                "config": new_config,
            }
        store.set_mission_config(mission_id, new_config,
                                expected_version=_config_version(store, mission_id),
                                who=body.get("who", ""))
        return {"config": new_config, "changelog": payload}

    @router.get("/flags")
    def get_flags(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        return {"flags": store.flags()}

    @router.post("/flags/{key}")
    def set_flag(request: Request, key: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        value = body.get("value", "")
        expected = body.pop("expected_version", None)
        store.set_flag(key, str(value), expected)
        return {"key": key, "value": str(value)}

    @router.delete("/flags/{key}")
    def remove_flag(request: Request, key: str) -> dict[str, Any]:
        store = _store_guard(request)
        if key not in store.flags():
            raise KeyError(f"flag {key!r} not found")
        store.remove_flag(key, expected_version=None)
        return {"deleted": key}

    @router.get("/missions/{mission_id}/preflight")
    def get_preflight(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        return {"preflight": store.preflight(mission_id)}

    @router.post("/missions/{mission_id}/preflight")
    def record_preflight(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        items = body.get("items", [])
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        store.record_preflight(mission_id, items)
        return {"preflight": store.preflight(mission_id)}

    # ------------------------------------------------------------------ CLAUDE.md
    @router.get("/missions/{mission_id}/claudemd")
    def get_claude_md(request: Request, mission_id: str) -> dict[str, Any]:
        """The mission's CLAUDE.md: the file in the working folder when it is
        mounted, otherwise the copy kept in the config (``source`` marks where
        it lives)."""
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        config = store.mission_config(mission_id) or {}
        if mission.working_directory:
            try:
                text = Path(mission.working_directory).joinpath("CLAUDE.md").read_text(encoding="utf-8")
            except OSError:
                if "claudemd" in config:
                    return {"source": "db", "text": config["claudemd"]}
                # no file here (the folder is not mounted into the dashboard, or it has none yet) and no copy
                # in the config: the editor starts empty and the text is stored in the config
                return {"source": "db", "text": "", "note": "CLAUDE.md не найден в рабочей папке"}
            return {"source": "file", "text": text}
        return {"source": "db", "text": config.get("claudemd", "")}

    @router.put("/missions/{mission_id}/claudemd")
    def set_claude_md(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Write the mission's CLAUDE.md: to the file in the working folder
        when it is mounted, otherwise to the config (``claudemd``), which
        'применяет оператор' (the operator applies it)."""
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        text = body.get("text", "")
        who = body.get("who", "")
        if mission.working_directory:
            try:
                Path(mission.working_directory).joinpath("CLAUDE.md").write_text(text, encoding="utf-8")
            except OSError as exc:
                raise ValueError(f"cannot write CLAUDE.md in {mission.working_directory!r}: {exc}") from exc
            # the file write is not an audited store write, so stamp the
            # config with the write time and let the store log it
            config = store.mission_config(mission_id) or {}
            store.set_mission_config(
                mission_id, {**config, "claudemd_file_written_at": now()},
                expected_version=_config_version(store, mission_id), who=who,
            )
            return {
                "source": "file",
                "path": str(Path(mission.working_directory) / "CLAUDE.md"),
                "config_version": _config_version(store, mission_id),
            }
        config = store.mission_config(mission_id) or {}
        store.set_mission_config(
            mission_id, {**config, "claudemd": text},
            expected_version=_config_version(store, mission_id), who=who,
        )
        return {
            "source": "db",
            "config_version": _config_version(store, mission_id),
            "note": "применяет оператор",
        }

    # ------------------------------------------------------------------ changelog
    @router.get("/changelog")
    def get_changelog(request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        store = _store_guard(request)
        return {"changelog": [e.model_dump() for e in store.changelog(limit)]}

    # ------------------------------------------------------------------ stats
    @router.get("/missions/{mission_id}/stats")
    def mission_stats(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        # a finished mission does not change: its figures (every run's events are read) are kept for 10 minutes,
        # a running one for 15 seconds, so a board with many cards does not re-read the runner for each refresh
        hit = _STATS_CACHE.get(mission_id)
        if hit is not None:
            age = time.monotonic() - hit[0]
            if age < (STATS_TTL_FINISHED if hit[1].get("status") in ("completed", "failed") else STATS_TTL_LIVE):
                return hit[1]
        result = board_stats.mission_card_stats(request.app.state.manager, store, mission.state_namespace)
        _STATS_CACHE[mission_id] = (time.monotonic(), result)
        return result

    @router.get("/missions/{mission_id}/tasks/{task_key}/stats")
    def task_stats(request: Request, mission_id: str, task_key: str) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        views = store.task_views(mission.state_namespace)
        if not any(v.task_id == task_key for v in views):
            raise KeyError(f"task {task_key!r} not found")
        return board_stats.task_stats(request.app.state.manager, store, mission.state_namespace, task_key)

    # ------------------------------------------------------------------ thinking policy
    @router.post("/thinking/policy")
    def policy_check(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        problems = board_think.validate_policy(body.get("policy"))
        return {"valid": not problems, "problems": problems}

    @router.post("/thinking/variant")
    def variant(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        policy = body.get("policy") or {}
        return {"variant": board_think.variant_for_task(policy, int(body.get("task_number", 1)))}

    @router.post("/thinking/layout")
    def layout(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        policy = body.get("policy") or {}
        return {"layout": board_think.layout_preview(policy, int(body.get("n", 12)))}

    @router.post("/thinking/stats")
    def thinking_stats(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        rows = body.get("attempts") or []
        return {"variants": board_think.variant_stats(rows, body.get("control", "off"),
                                                      int(body.get("min_arm", 100)))}

    @router.post("/thinking/thompson")
    def thompson(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        rows = body.get("attempts") or []
        return {"thompson": board_think.thompson_best(
            rows, int(body.get("seed", 7)), int(body.get("samples", 10000)))}

    return router


router = _build_router()
