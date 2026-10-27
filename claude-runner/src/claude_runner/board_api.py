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

import fnmatch
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import (
    board_order,
    board_plan,
    board_ready,
    board_stats,
    board_think,
    features_config,
    roles_config,
)
from .board import (
    BoardStore,
    ConflictError,
    GuardError,
    InMemoryBoardStore,
    QueueEntry,
    get_board_store,
    now,
)
from .board_ops_redact import scrub
from .board_pg import UnavailableBoardStore
from .board_plan import update_mission_config
from .board_ready import MissionIdError, create_mission, rollback, save_template, start_guard
from .qa import NIGHTLY_VERDICT_PATH
from .qa_runner import RUN_DIR_PREFIX, RUNS_DIR

__all__ = ["router", "add_board_routes"]

logger = logging.getLogger(__name__)
_QA_SCREENSHOT_RE = re.compile(r"^qa-[0-9a-f]+/[A-Za-z0-9_.-]+\.png$")


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


STORE_RETRY_SECONDS = 5.0


def live_store(app: Any) -> BoardStore:
    """The board store of the app; an unavailable one is rebuilt at most once per
    ``STORE_RETRY_SECONDS``, so a database that starts after the dashboard is picked up
    without a restart (the first requests answer 503 meanwhile)."""
    store: BoardStore = app.state.board_store
    if _unavailable(store) is None:
        return store
    now = time.monotonic()
    if now - getattr(app.state, "board_store_tried", float("-inf")) < STORE_RETRY_SECONDS:
        return store
    app.state.board_store_tried = now
    app.state.board_store = get_board_store()
    return app.state.board_store  # type: ignore[no-any-return]


def _store_guard(request: Request) -> BoardStore:
    """The board store, with a 503 when it is unavailable (the runner's
    error shape, so a client sees one consistent envelope)."""
    store = live_store(request.app)
    refuse = _unavailable(store)
    if refuse is not None:
        raise RuntimeError(refuse["reason"])
    return store


_I18N_LANG_RE = re.compile(r"[a-z]{2,8}")


def _require_dashboard_header(request: Request) -> None:
    if request.headers.get("x-ahawr-dashboard") != "1":
        raise GuardError("missing X-AHAWR-Dashboard header")


def _roles_text(request: Request, body: dict[str, Any]) -> str:
    text = body.get("roles_json", "")
    if not isinstance(text, str):
        raise ValueError("roles_json must be a string holding the JSON text")
    if not text.strip():
        return ""
    registry = getattr(request.app.state, "provider_registry", None)
    settings = request.app.state.settings
    providers = registry.profiles() if registry else getattr(settings, "providers", {})
    report = roles_config.validate_roles_json(text, list(providers))
    if report.errors:
        raise ValueError("; ".join(report.errors))
    return text


def _features_text(body: dict[str, Any], key: str) -> str:
    text = body.get(key, "")
    if not isinstance(text, str):
        raise ValueError(f"{key} must be a string holding the JSON text")
    return text


def _i18n_file(lang: str) -> dict[str, str]:
    return json.loads(Path(__file__).with_name(f"board_i18n_{lang}.json").read_text("utf-8"))


def _ensure_i18n_seed(request: Request, store: BoardStore) -> None:
    if getattr(request.app.state, "i18n_seeded", False):
        return
    store.seed_i18n({
        "languages": [{"code": "en", "name": "English"}, {"code": "ru", "name": "Русский"}],
        "strings": {"en": _i18n_file("en"), "ru": _i18n_file("ru")},
    })
    request.app.state.i18n_seeded = True


def _i18n_view(store: BoardStore) -> dict[str, Any]:
    data = store.list_i18n()
    if not any(lang["code"] == "en" for lang in data["languages"]):
        data["languages"].insert(0, {"code": "en", "name": "English", "enabled": True})
    # the shipped texts fill the keys the database does not have yet (a key added in a later release);
    # a text edited in the translation editor is in the database and wins
    shipped = [lang["code"] for lang in data["languages"] if (Path(__file__).with_name(f"board_i18n_{lang['code']}.json")).exists()]
    for code in ["en", *[c for c in shipped if c != "en"]]:
        table = data["strings"].setdefault(code, {})
        for key, text in _i18n_file(code).items():
            table.setdefault(key, text)
    return data


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


def _refusal(status: int, code: str, reason: str, **fields: Any) -> JSONResponse:
    """A refused plan edit: the board error envelope plus the refusal details."""
    return JSONResponse(
        status_code=status,
        content={
            "status": "failed",
            "error": {"code": code, "message": reason},
            "code": code,
            "status_code": status,
            "reason": reason,
            **fields,
        },
    )


def _waiting_graph(plan: dict[str, Any], statuses: dict[str, str]) -> tuple[list[str], dict[str, list[str]]]:
    """The waiting task ids in plan order, and every task's dependency list."""
    waiting = [t["id"] for t in plan["tasks"] if statuses[t["id"]] == "waiting"]
    deps = {t["id"]: list(t.get("dependencies") or []) for t in plan["tasks"]}
    return waiting, deps


def _reordered_plan(
    plan: dict[str, Any], statuses: dict[str, str], order: list[str], deps: dict[str, list[str]]
) -> dict[str, Any]:
    """The plan with the waiting tasks in ``order`` (locked tasks keep their
    slots), renumbered like ``board_plan.move_task`` and with dependencies
    pointing at the new ids."""
    by_id = {t["id"]: t for t in plan["tasks"]}
    slots = iter(order)
    tasks = [by_id[next(slots)] if statuses[t["id"]] == "waiting" else t for t in plan["tasks"]]
    mapping = {t["id"]: f"T{i + 1:03d}" for i, t in enumerate(tasks)}
    return {
        **plan,
        "tasks": [
            {**t, "id": mapping[t["id"]], "dependencies": [mapping.get(d, d) for d in deps[t["id"]]]}
            for t in tasks
        ],
    }


def _stamp(value: Any) -> Any:
    """A timestamp as the store compares it (isoformat), not as the JSON
    encoder writes it (``Z``)."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _config_version(store: BoardStore, mission_id: str) -> str | None:
    """The mission-config row's version, from its ``updatedAt`` stamp."""
    if isinstance(store, InMemoryBoardStore):
        return store._mission_config_version.get(mission_id, None)
    config = store.mission_config(mission_id)
    return (config or {}).get("updated_at")


_UI_CHECK_KEYS = frozenset({"needed", "why", "scenario", "steps"})
_PLAN_QA_KEYS = frozenset({"needed", "reasons", "scenarios"})
_MAX_NEEDED_UI_CHECKS = 3


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_ui_check(label: str, check: Any) -> None:
    if not isinstance(check, dict) or set(check) != _UI_CHECK_KEYS:
        keys = sorted(_UI_CHECK_KEYS)
        raise ValueError(f"task {label}: ui_check must be an object with the keys {keys}")
    if not isinstance(check["needed"], bool):
        raise ValueError(f"task {label}: ui_check.needed must be true or false")
    if not isinstance(check["why"], str) or not isinstance(check["scenario"], str):
        raise ValueError(f"task {label}: ui_check.why and ui_check.scenario must be strings")
    if not _is_str_list(check["steps"]):
        raise ValueError(f"task {label}: ui_check.steps must be a list of strings")


def _check_plan_qa(qa: Any, needed_ui_checks: int) -> None:
    if not isinstance(qa, dict) or set(qa) != _PLAN_QA_KEYS:
        raise ValueError(f"qa must be an object with the keys {sorted(_PLAN_QA_KEYS)}")
    if not isinstance(qa["needed"], bool):
        raise ValueError("qa.needed must be true or false")
    if not _is_str_list(qa["reasons"]) or not _is_str_list(qa["scenarios"]):
        raise ValueError("qa.reasons and qa.scenarios must be lists of strings")
    if qa["needed"] and not needed_ui_checks:
        raise ValueError("qa.needed is true but no task has a needed ui_check")


def _check_plan(plan: Any) -> None:
    if not isinstance(plan, dict):
        return
    tasks = plan.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("plan tasks must be a list")
    needed = 0
    for index, task in enumerate(tasks):
        if isinstance(task, dict) and "ui_check" in task:
            _check_ui_check(str(task.get("id") or index + 1), task["ui_check"])
            if task["ui_check"]["needed"]:
                needed += 1
    if needed > _MAX_NEEDED_UI_CHECKS:
        raise ValueError(f"at most {_MAX_NEEDED_UI_CHECKS} tasks may carry a needed ui_check")
    if "qa" in plan:
        _check_plan_qa(plan["qa"], needed)


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


_TASK_ID_RE = re.compile(r"^T[0-9]+[A-Z]*$")

#: The mission card's task status mapped to the summary's vocabulary.
_TASK_STATUS_MAP = {
    "waiting": "waiting",
    "running": "running",
    "review_pending": "running",
    "accepted": "accepted",
    "completed": "accepted",
    "not_accepted": "rejected",
    "failed": "rejected",
}


def _mission_project(
    working_directory: str | None, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    """The mission's project: the working directory matched against the stored
    project rules (glob with ``*``, ordered by position); the first matching
    rule wins. Otherwise the default rule: the working directory, shown by its
    last folder name."""
    normalized = (working_directory or "").replace("\\", "/").rstrip("/")
    for rule in sorted(rules, key=lambda r: r.get("position", 0)):
        pattern = str(rule.get("pattern", "")).replace("\\", "/").rstrip("/")
        if normalized and pattern and fnmatch.fnmatch(normalized, pattern):
            name = str(rule.get("name", ""))
            return {"key": name, "name": name, "rule": rule["pattern"]}
    last = normalized.rsplit("/", 1)[-1] if normalized else ""
    return {"key": last, "name": last, "rule": None}


def _task_attempts(store: BoardStore, state_key: str) -> dict[str, int]:
    """The per-task attempt counts (the distinct ``task_attempt`` values of each
    task's event rows)."""
    attempts: dict[str, int] = {}
    for view in store.task_views(state_key):
        attempts[view.task_id] = len(
            {
                int(float(e["task_attempt"]))
                for e in store.events(state_key)
                if e.get("task_id") == view.task_id
                and e.get("task_attempt") is not None
            }
        )
    return attempts


# The hermes_config row (profile claude-code) holds the values a mission uses when its own config is empty;
# only these keys are shown on the board (the row also carries chat ids and URLs).
_DEFAULT_KEYS = (
    "architect_provider", "architect_model", "worker_provider", "worker_model", "reviewer_provider",
    "reviewer_model", "max_tasks", "max_attempts_per_task", "worker_think_policy", "worker_think_policy_from_task",
)


def _config_defaults(store: BoardStore) -> dict[str, Any]:
    try:
        row = store.hermes_config() or {}
    except Exception:
        return {}
    out: dict[str, Any] = {}
    for key in _DEFAULT_KEYS:
        value = row.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, float) and value == int(value):
            value = int(value)
        elif isinstance(value, str) and value.endswith(".0") and value[:-2].lstrip("-").isdigit():
            value = value[:-2]
        out[key] = value
    return out


def _mission_task_list(
    store: BoardStore, state_key: str
) -> list[dict[str, Any]]:
    """The mission's tasks: id (parsed as T[0-9]+[A-Z]*), title, status
    (the mission card's status mapped to the summary's vocabulary, a retried
    accepted task is ``accepted_retry``), attempts and dependencies."""
    views = store.task_views(state_key)
    live = store.get_live_state(state_key)
    plan = live.plan if live is not None else None
    tasks = plan.get("tasks") if isinstance(plan, dict) else []
    tasks_by_id = {str(t.get("id") or ""): t for t in tasks or []}
    attempts_by_id = _task_attempts(store, state_key)
    result: list[dict[str, Any]] = []
    for view in views:
        task_id = view.task_id
        if not _TASK_ID_RE.match(task_id):
            continue
        deps = [d for d in tasks_by_id.get(task_id, {}).get("dependencies") or []]
        attempts = attempts_by_id.get(task_id, 0)
        status = _TASK_STATUS_MAP.get(view.status, view.status)
        if status == "accepted" and attempts > 1:
            status = "accepted_retry"
        result.append(
            {"id": task_id, "title": view.title, "status": status,
             "attempts": attempts, "deps": deps}
        )
    return result


_TASKLIST_CACHE: dict[str, tuple[float, list[dict[str, Any]], Any]] = {}


def _cached_task_list(store: BoardStore, state_key: str) -> list[dict[str, Any]]:
    """_mission_task_list for the summary, kept 600 s for a finished mission and 5 s
    for the others: reading the tasks of ~90 missions took 8 s on every refresh."""
    live = store.get_live_state(state_key)
    if live is None or live.status in ("completed", "failed"):
        ttl = 600.0 if live is not None else 30.0
    else:
        ttl = 5.0
    hit = _TASKLIST_CACHE.get(state_key)
    if hit is not None and hit[2] is store and time.monotonic() - hit[0] < ttl:
        return hit[1]
    tasks = _mission_task_list(store, state_key)
    _TASKLIST_CACHE[state_key] = (time.monotonic(), tasks, store)
    return tasks


def _summary_projects(
    rules: list[dict[str, Any]], missions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The projects: the stored rules (key, name, rule) plus the default
    project for missions with no matching rule; each project's ``mission_ids``
    are the missions whose project is it."""
    by_key: dict[str, dict[str, Any]] = {}
    for mission in missions:
        project = mission["project"]
        entry = by_key.setdefault(
            project["key"],
            {"key": project["key"], "name": project["name"],
             "rule": project["rule"], "mission_ids": []},
        )
        entry["mission_ids"].append(mission["mission_id"])
    # a stored rule's name is the project key; keep its rule and name
    for rule in rules:
        name = str(rule.get("name", ""))
        if name in by_key:
            by_key[name]["name"] = name
            by_key[name]["rule"] = rule.get("pattern")
    return [by_key[k] for k in sorted(by_key)]


def _read_qa_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("qa file %s unreadable: %s", path.name, type(exc).__name__)
        return None


def _qa_matches(scenarios: set[str]) -> list[tuple[float, dict[str, Any]]]:
    matches: list[tuple[float, dict[str, Any]]] = []
    if RUNS_DIR.is_dir():
        for path in RUNS_DIR.glob(f"{RUN_DIR_PREFIX}*/verdict.json"):
            verdict = _read_qa_json(path)
            if isinstance(verdict, dict) and verdict.get("scenario") in scenarios:
                matches.append((path.stat().st_mtime, verdict))
    if NIGHTLY_VERDICT_PATH.is_file():
        nightly = _read_qa_json(NIGHTLY_VERDICT_PATH)
        items: list[dict[str, Any]] = []
        if isinstance(nightly, dict):
            items = nightly.get("scenarios", [])
            if not isinstance(items, list):
                items = []
        elif isinstance(nightly, list):
            items = [item for item in nightly if isinstance(item, dict)]
        if items:
            mtime = NIGHTLY_VERDICT_PATH.stat().st_mtime
            matches.extend(
                (mtime, item) for item in items if item.get("scenario") in scenarios
            )
    return matches


def _qa_view(verdict: dict[str, Any]) -> dict[str, Any]:
    """The verdict with its text scrubbed again and its screenshots as links;
    only names matching the qa output layout become links."""
    failed_step = verdict.get("failed_step")
    shots = verdict.get("screenshots") or []
    links = [
        {"name": shot, "url": f"/api/board/qa/screenshots/{shot}"}
        for shot in shots
        if isinstance(shot, str) and _QA_SCREENSHOT_RE.match(shot)
    ]
    return {
        "scenario": scrub(str(verdict.get("scenario", ""))),
        "verdict": str(verdict.get("verdict", "")),
        "layer": str(verdict.get("layer", "")) if verdict.get("layer") else None,
        "blocked_reason": _scrubbed_or_none(verdict.get("blocked_reason")),
        "failed_step": failed_step if isinstance(failed_step, int) else None,
        "failed_reason": scrub(str(verdict.get("failed_reason") or "")),
        "reason": _scrubbed_or_none(verdict.get("reason")),
        "console_errors": _scrubbed_list(verdict.get("console_errors")),
        "network_errors": _scrubbed_list(verdict.get("network_errors")),
        "screenshots": links,
    }


def _scrubbed_or_none(value: object) -> str | None:
    return None if value is None else scrub(str(value))


def _scrubbed_list(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    return [scrub(str(item)) for item in items]


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
        store = live_store(request.app)
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
            "defaults": _config_defaults(store),
            "task_overrides": store.task_overrides(mission_id),
        }

    @router.get("/missions/{mission_id}/qa")
    def mission_qa(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        plan_qa = _live_plan(store, _state_key_of(store, mission_id)).get("qa")
        scenarios = set(plan_qa.get("scenarios") or []) if isinstance(plan_qa, dict) else set()
        matches = _qa_matches(scenarios) if scenarios else []
        if not matches:
            return {"verdict": None, "verdicts": []}
        latest = max(matches, key=lambda match: match[0])[1]
        verdicts = [_qa_view(item) for _, item in sorted(matches, key=lambda match: match[0])]
        return {"verdict": _qa_view(latest), "verdicts": verdicts}

    @router.get("/qa/screenshots/{run_name}/{file_name}")
    def qa_screenshot(run_name: str, file_name: str) -> FileResponse:
        if not _QA_SCREENSHOT_RE.match(f"{run_name}/{file_name}"):
            raise KeyError(f"screenshot {file_name!r} not found")
        path = (RUNS_DIR / run_name / file_name).resolve()
        if not path.is_relative_to(RUNS_DIR.resolve()) or not path.is_file():
            raise KeyError(f"screenshot {file_name!r} not found")
        return FileResponse(path, media_type="image/png")

    # ``create`` is registered before the ``{mission_id}`` route, so Starlette
    # matches the literal path instead of treating ``create`` as a mission id
    @router.post("/missions/create")
    def create(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        _check_plan(body.get("plan"))
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
        _check_plan(fields.get("plan"))
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

    @router.post("/missions/{mission_id}/tasks/reorder")
    def reorder_tasks(request: Request, mission_id: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        try:
            board_plan._check_version(expected, data["stored_version"])
        except ConflictError as exc:
            return _refusal(409, "conflict", str(exc))
        plan = board_plan._normalize_plan(_live_plan(store, data["state_key"]))
        statuses = board_plan._statuses(
            plan, data["live_status"], data["live_task_index"], data["live_task_attempt"], data["events"]
        )
        waiting, deps = _waiting_graph(plan, statuses)
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(i, str) for i in order):
            raise ValueError("order must be a list of task ids")
        unknown = [i for i in order if i not in deps]
        if unknown:
            raise ValueError(f"unknown task id(s): {unknown}")
        locked = [i for i in order if i not in waiting]
        if locked:
            return _refusal(403, "guard", "a locked task would move", locked=locked)
        if sorted(order) != sorted(waiting):
            raise ValueError("order must list every waiting task exactly once")
        broken = board_order.violations(order, deps)
        if broken:
            return _refusal(403, "guard", "the order breaks a dependency", broken=broken)
        new_plan = _reordered_plan(plan, statuses, order, deps)
        try:
            store.upsert_live_state(data["state_key"], expected, plan=new_plan, who=body.get("who", ""))
        except ConflictError as exc:
            return _refusal(409, "conflict", str(exc))
        return {
            "plan": new_plan,
            "changelog": board_plan._changelog("task reorder", new_plan, order=order),
            "running_warning": data["live_status"] == "active",
        }

    @router.post("/missions/{mission_id}/tasks/{task_key}/dependencies")
    def task_dependencies(request: Request, mission_id: str, task_key: str,
                          body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        try:
            board_plan._check_version(expected, data["stored_version"])
        except ConflictError as exc:
            return _refusal(409, "conflict", str(exc))
        plan = board_plan._normalize_plan(_live_plan(store, data["state_key"]))
        statuses = board_plan._statuses(
            plan, data["live_status"], data["live_task_index"], data["live_task_attempt"], data["events"]
        )
        waiting, deps = _waiting_graph(plan, statuses)
        action = body.get("action")
        dependency = body.get("dependency")
        if not isinstance(dependency, str) or task_key not in deps or dependency not in deps:
            raise ValueError(f"unknown task {task_key!r} or dependency {dependency!r}")
        if task_key not in waiting:
            return _refusal(403, "guard", "a locked task cannot change its dependencies", locked=[task_key])
        if action == "add":
            closing = board_order.would_cycle(deps, task_key, dependency)
            if closing:
                return _refusal(422, "cycle", "the dependency closes a cycle", closing_pair=closing)
            if dependency not in deps[task_key]:
                deps[task_key].append(dependency)
        elif action == "remove":
            deps[task_key] = [d for d in deps[task_key] if d != dependency]
        else:
            raise ValueError("action must be add or remove")
        order = board_order.repair_order(waiting, deps)
        new_plan = _reordered_plan(plan, statuses, order, deps)
        try:
            store.upsert_live_state(data["state_key"], expected, plan=new_plan, who=body.get("who", ""))
        except ConflictError as exc:
            return _refusal(409, "conflict", str(exc))
        return {
            "plan": new_plan,
            "changelog": board_plan._changelog(
                f"task dependency {action}", new_plan, task_key=task_key, dependency=dependency
            ),
            "running_warning": data["live_status"] == "active",
        }

    @router.post("/missions/{mission_id}/tasks/{task_key}")
    def update_task(request: Request, mission_id: str, task_key: str, body: dict[str, Any]) -> dict[str, Any]:
        store = _store_guard(request)
        data = _plan_guard(store, mission_id)
        expected = body.pop("expected_version", data["stored_version"])
        fields = {k: v for k, v in body.items() if k not in ("who",)}
        # normalize the requested key the same way the renumbering does: a key
        # like "T1" names the task at that plan position (the stored plan's
        # ids are the renumbered keys)
        plan = _live_plan(store, data["state_key"])
        tasks = plan.get("tasks") or []
        try:
            index = int(task_key[1:]) - 1
        except ValueError:
            index = None
        if index is not None and 0 <= index < len(tasks):
            task_key = tasks[index].get("id") or f"T{index + 1:03d}"
        plan, payload = board_plan.update_task(
            plan,
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
                return {"source": "db", "text": "", "note": "CLAUDE.md not found in the working folder"}
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
            "note": "applied by the operator",
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

    @router.get("/missions/{mission_id}/timeline")
    def mission_timeline(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        mission = store.get_mission(mission_id)
        if mission is None:
            raise KeyError(f"mission {mission_id!r} not found")
        return board_stats.mission_timeline(
            getattr(request.app.state, "manager", None), store, mission.state_namespace
        )

    # ------------------------------------------------------------------ summary
    @router.get("/summary")
    def summary(request: Request) -> dict[str, Any]:
        """One response for the whole board: every mission (state group,
        position, project, its tasks, and its cached figures) and the projects
        (the stored rules plus the default project).  A failing or absent
        runner gives null figures (HTTP 200, never an error)."""
        store = _store_guard(request)
        manager = request.app.state.manager
        rules, _ = store.get_project_rules()
        groups = store.mission_groups()
        group_of: dict[str, str] = {}
        for group, ids in groups.items():
            for mission_id in ids:
                group_of[mission_id] = group
        queue_of = {e.mission_id: e.position for e in store.queue()}
        times = store.mission_times()
        numbers = store.mission_numbers()
        missions: list[dict[str, Any]] = []
        for mission in store.list_missions():
            state_key = mission.state_namespace
            figures = board_stats.cached_mission_figures(
                manager, store, state_key, mission.mission_id
            )
            missions.append(
                {
                    "mission_id": mission.mission_id,
                    "number": numbers.get(mission.mission_id),
                    "name": mission.name,
                    "state_group": group_of.get(mission.mission_id),
                    "position": queue_of.get(mission.mission_id),
                    "created_at": mission.created_at,
                    "started_at": (times.get(state_key) or {}).get("first"),
                    "last_at": (times.get(state_key) or {}).get("last"),
                    "project": _mission_project(mission.working_directory, rules),
                    "tasks": _cached_task_list(store, state_key),
                    "attempts": figures["attempts"],
                    "hours": figures["hours"],
                    "tokens": figures["tokens"],
                    "cache_ratio": figures["cache_ratio"],
                    "cost": figures["cost"],
                    "tok_s": figures["tok_s"],
                    "thinking_share": figures["thinking_share"],
                }
            )
        return {"missions": missions, "projects": _summary_projects(rules, missions)}

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

    @router.get("/project-rules")
    def get_project_rules(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        rules, version = store.get_project_rules()
        return {"rules": rules, "version": version}

    @router.put("/project-rules")
    def put_project_rules(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        rules = body.get("rules")
        if not isinstance(rules, list) or not all(
            isinstance(r, dict) and isinstance(r.get("name"), str)
            and isinstance(r.get("pattern"), str) and isinstance(r.get("position"), int)
            for r in rules
        ):
            raise ValueError("rules must be a list of {name, pattern, position} objects")
        store.put_project_rules(rules, body.get("expected_version"), actor=body.get("who", ""))
        rules, version = store.get_project_rules()
        return {"rules": rules, "version": version}

    @router.get("/roles")
    def get_roles(request: Request) -> dict[str, Any]:
        row = _store_guard(request).hermes_config() or {}
        return {"roles_json": row.get("roles_json") or "", "version": _stamp(row.get("updatedAt"))}

    @router.put("/roles")
    def put_roles(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        text = _roles_text(request, body)
        store.update_hermes_config(body.get("expected_version"), roles_json=text)
        return {"roles_json": text, "version": _stamp(
            (store.hermes_config() or {}).get("updatedAt"))}

    @router.get("/missions/{mission_id}/roles")
    def get_mission_roles(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        if store.get_mission(mission_id) is None:
            raise KeyError(f"mission {mission_id!r} not found")
        config = store.mission_config(mission_id) or {}
        return {
            "roles_json": config.get("roles_json") or "",
            "version": _config_version(store, mission_id),
        }

    @router.put("/missions/{mission_id}/roles")
    def put_mission_roles(
        request: Request, mission_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        if store.get_mission(mission_id) is None:
            raise KeyError(f"mission {mission_id!r} not found")
        text = _roles_text(request, body)
        config = store.mission_config(mission_id) or {}
        store.set_mission_config(
            mission_id, {**config, "roles_json": text},
            expected_version=body.get("expected_version"), who=body.get("who", ""),
        )
        return {"roles_json": text, "version": _config_version(store, mission_id)}

    @router.get("/features")
    def get_features(request: Request) -> dict[str, Any]:
        row = _store_guard(request).hermes_config() or {}
        return {"features_json": row.get("features_json") or "", "version": _stamp(
            row.get("updatedAt"))}

    @router.put("/features")
    def put_features(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        text = _features_text(body, "features_json")
        errors = features_config.validate_features_json(text, None)
        if errors:
            raise ValueError("; ".join(errors))
        store.update_hermes_config(body.get("expected_version"), features_json=text)
        return {"features_json": text, "version": _stamp(
            (store.hermes_config() or {}).get("updatedAt"))}

    @router.get("/missions/{mission_id}/features")
    def get_mission_features(request: Request, mission_id: str) -> dict[str, Any]:
        store = _store_guard(request)
        if store.get_mission(mission_id) is None:
            raise KeyError(f"mission {mission_id!r} not found")
        config = store.mission_config(mission_id) or {}
        return {
            "mission_features_json": config.get("mission_features_json") or "",
            "version": _config_version(store, mission_id),
        }

    @router.put("/missions/{mission_id}/features")
    def put_mission_features(
        request: Request, mission_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        if store.get_mission(mission_id) is None:
            raise KeyError(f"mission {mission_id!r} not found")
        text = _features_text(body, "mission_features_json")
        global_text = (store.hermes_config() or {}).get("features_json") or ""
        errors = features_config.validate_features_json(global_text, text)
        if errors:
            raise ValueError("; ".join(errors))
        config = store.mission_config(mission_id) or {}
        store.set_mission_config(
            mission_id, {**config, "mission_features_json": text},
            expected_version=body.get("expected_version"), who=body.get("who", ""),
        )
        return {"mission_features_json": text, "version": _config_version(store, mission_id)}

    @router.get("/i18n")
    def get_i18n(request: Request) -> dict[str, Any]:
        store = _store_guard(request)
        _ensure_i18n_seed(request, store)
        return _i18n_view(store)

    @router.post("/i18n/languages")
    def add_i18n_language(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        _ensure_i18n_seed(request, store)
        code, name = body.get("code"), body.get("name")
        if not isinstance(code, str) or not _I18N_LANG_RE.fullmatch(code):
            raise ValueError("code must match ^[a-z]{2,8}$")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name must be a non-empty string")
        store.add_i18n_language(code, name.strip())
        return _i18n_view(store)

    @router.put("/i18n/{lang}/{key}")
    def put_i18n_string(request: Request, lang: str, key: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store = _store_guard(request)
        _ensure_i18n_seed(request, store)
        text = body.get("text", "")
        if not isinstance(text, str):
            raise ValueError("text must be a string")
        known = {lang_row["code"] for lang_row in store.list_i18n()["languages"]} | {"en"}
        if lang not in known:
            raise KeyError(lang)
        store.put_i18n_string(lang, key, text, actor=body.get("who", ""))
        return {"lang": lang, "key": key, "text": text}

    return router


router = _build_router()
