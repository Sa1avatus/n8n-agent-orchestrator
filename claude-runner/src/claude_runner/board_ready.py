"""Readiness checks, start guard, rollback and mission creation for the board.

Every check is its own function and returns one readiness item
``{"check", "status", "reason"}`` with ``status`` in ``ok`` / ``warning`` /
``error`` (so each can be tested on its own).  ``collect_readiness`` runs them
all for a mission.  ``start_guard`` refuses the start when any check is
``error`` unless the caller explicitly confirms.  ``rollback`` writes the
changelog entry's ``before_json`` back as a new change — only while the
current row is still what the entry's ``after_json`` says it was; anything
changed after the entry makes the rollback a :class:`ConflictError`
("план изменился, обновите").  ``create_mission`` validates the id (slug,
unique), copies config/rules/criteria/hints from a mission or a template
(objective and plan supplied separately, a copied plan without statuses or
attempts), and writes the missions row, the Live State row and the queue
entry in one atomic step: all three rows or none.  ``save_template`` keeps a
mission as a reusable template without its objective and plan.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from .board import (
    BoardStore,
    ChangelogEntry,
    ConflictError,
    LiveState,
    Mission,
)
from .context_probe import ctx_from_models
from .sources import RunnerSource, _check

HOST_CHECK_NOTE = "проверяется на хосте"

# A mission id is a slug: lower-case letters, digits and separator characters
# (hyphen, underscore, dot); it must not start with a separator.
_MISSION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# Fields a changelog entry may restore, by row kind (the store's column names).
_MISSION_FIELDS = (
    "name", "objective", "working_directory", "version", "enabled",
    "rules", "acceptance_criteria", "retrieval_corpora",
)
_LIVE_FIELDS = (
    "worker_session_id", "plan", "task_index", "task_attempt", "status",
    "review_feedback", "last_review_score", "last_review_reason",
    "reviewer_session_id", "reviewer_run_id", "reviewer_status",
    "worker_run_id", "worker_status",
    "architect_run_id", "architect_status", "architect_session_id",
)


class MissionIdError(ValueError):
    """The mission id is not a valid slug or collides with an existing one."""

    code = "bad_mission_id"
    status_code = 400


# -------------------------------------------------------------------------- items


def _item(check: str, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    item = {"check": check, "status": status, "reason": reason}
    item.update(extra)
    return item


def _seconds_since(stamp: str) -> float:
    """Seconds between the stamp and now (0 when it cannot be parsed)."""
    from datetime import UTC, datetime

    try:
        return max(0.0, (datetime.now(UTC) - datetime.fromisoformat(stamp)).total_seconds())
    except ValueError:
        return 0.0


# -------------------------------------------------------------------------- checks


def check_working_folder(
    working_directory: str | None, host_visible: bool = True
) -> dict[str, Any]:
    """The mission's working folder.

    When the path is visible from the runner (mounted, or a path on the runner
    host) it must exist; when it is a host path the runner cannot see it, the
    check only reports that it is verified on the host (``warning``)."""
    if not working_directory:
        return _item("working_folder", "warning", "working folder is not set")
    if not host_visible:
        return _item("working_folder", "warning", HOST_CHECK_NOTE)
    if os.path.isdir(working_directory):
        return _item("working_folder", "ok", working_directory)
    return _item(
        "working_folder", "error",
        f"working folder {working_directory!r} does not exist",
    )


def check_required_files(
    required_files: list[str] | None, host_visible: bool = True
) -> dict[str, Any]:
    """The files the mission config (``required_files``) expects to exist."""
    files = [f for f in (required_files or []) if f]
    if not files:
        return _item("required_files", "ok", "no required files declared")
    if not host_visible:
        return _item("required_files", "warning", HOST_CHECK_NOTE, files=files)
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        return _item(
            "required_files", "error",
            "missing files: " + ", ".join(missing), files=files,
        )
    return _item("required_files", "ok", ", ".join(files), files=files)


async def check_runner_health(
    runner_url: str, api_key: str = "", timeout: float = 5.0
) -> dict[str, Any]:
    """The claude-runner service: ``GET {url}/health`` must answer ``ok``."""
    if not runner_url:
        return _item("runner", "warning", "runner URL is not set")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            source = RunnerSource(client, runner_url, api_key)
            payload = _check(
                await source.client.get(f"{source.url}/health", headers=source.headers),
                source.name,
            )
    except Exception as exc:
        return _item("runner", "error", f"runner unreachable: {exc}")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        shown = payload if isinstance(payload, dict) else str(payload)
        return _item("runner", "error", f"runner health is not 'ok': {shown}")
    return _item("runner", "ok", f"runner {runner_url} is up")


async def check_litellm_health(
    litellm_url: str, master_key: str = "", timeout: float = 5.0
) -> dict[str, Any]:
    """LiteLLM: ``GET {url}/health/liveliness`` must answer 200."""
    if not litellm_url:
        return _item("litellm", "warning", "litellm URL is not set")
    headers = {"Authorization": f"Bearer {master_key}"} if master_key else {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{litellm_url.rstrip('/')}/health/liveliness", headers=headers
            )
    except Exception as exc:
        return _item("litellm", "error", f"litellm unreachable: {exc}")
    if response.status_code != 200:
        return _item("litellm", "error", f"litellm health: HTTP {response.status_code}")
    return _item("litellm", "ok", f"litellm {litellm_url} is up")


def check_llama_model(
    probe_url: str, api_key: str, worker_model: str, timeout: float = 5.0
) -> dict[str, Any]:
    """The local Worker model: it must be listed by llama-server (``/v1/models``).

    The probe also reads the model's real context window (``--ctx-size``) and
    reports it; ``None`` means the server does not advertise a window for the
    model (a warning: the static settings apply)."""
    if not probe_url:
        return _item("llama_model", "warning", "llama probe URL is not set")
    if not worker_model:
        return _item("llama_model", "warning", "worker_model is not set")
    try:
        import json as _json
        import urllib.request

        req = urllib.request.Request(probe_url.rstrip("/") + "/v1/models")
        if api_key:
            req.add_header("Authorization", "Bearer " + api_key)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = _json.loads(r.read())
    except Exception as exc:
        return _item("llama_model", "error", f"llama-server unreachable: {exc}")
    if not isinstance(payload, dict):
        return _item("llama_model", "error", "llama-server returned no model list")
    found = any(
        item.get("id") == worker_model or worker_model in (item.get("aliases") or [])
        for item in payload.get("data") or []
    )
    if not found:
        return _item(
            "llama_model", "error",
            f"model {worker_model!r} is not in llama-server's /v1/models",
        )
    ctx = ctx_from_models(payload, worker_model)
    if ctx is None:
        return _item(
            "llama_model", "warning",
            f"model {worker_model!r} is up, but its --ctx-size is not advertised",
        )
    return _item(
        "llama_model", "ok", f"model {worker_model!r} is up (context {ctx} tokens)",
    )


def check_plan_present(live: LiveState | None) -> dict[str, Any]:
    """The Live State row must carry the mission plan."""
    if live is None or not live.plan:
        return _item("plan", "error", "the mission has no plan in the Live State row")
    tasks = live.plan.get("tasks") or []
    if not tasks:
        return _item("plan", "error", "the plan has no tasks")
    return _item("plan", "ok", f"plan with {len(tasks)} task(s)")


def check_live_state_present(live: LiveState | None) -> dict[str, Any]:
    """The mission must have a Live State row (it is created together with the
    mission when it is new)."""
    if live is None:
        return _item("live_state", "error", "the mission has no Live State row")
    return _item("live_state", "ok", f"Live State status: {live.status or 'not started'}")


def check_not_running(board: BoardStore, mission_id: str) -> dict[str, Any]:
    """The mission must not be running: its Live State ``status`` must not be
    ``active``."""
    if mission_id in {m.mission_id for m in board.running_missions()}:
        return _item("not_running", "error", f"mission {mission_id!r} is running")
    return _item("not_running", "ok", "mission is not running")


def check_preflight_fresh(
    board: BoardStore, mission_id: str, max_age_seconds: float = 3600.0
) -> dict[str, Any]:
    """The latest recorded preflight of the mission and how old it is.

    No record at all is an error (nothing verified yet); an old record is a
    warning; a fresh one is ok.  ``checked_at`` is reported so the caller can
    see what is being trusted."""
    rows = board.preflight(mission_id)
    if not rows:
        return _item("preflight", "error", "no preflight recorded for the mission")
    latest = rows[0]
    checked_at = str(latest.get("checked_at") or "")
    age = _seconds_since(checked_at)
    if age > max_age_seconds:
        return _item(
            "preflight", "warning",
            f"preflight is {age:.0f}s old (limit {max_age_seconds:.0f}s)",
            checked_at=checked_at,
        )
    return _item("preflight", "ok", f"preflight is {age:.0f}s old", checked_at=checked_at)


# -------------------------------------------------------------------------- collection


def _local_provider(settings: Any) -> Any | None:
    """The local provider profile (the one behind LiteLLM), by env or by name."""
    local = getattr(settings.providers, "LOCAL", None)
    if local is None:
        local = next(
            (p for name, p in settings.providers.items() if name.lower() == "local"),
            None,
        )
    return local


def _service_urls(settings: Any) -> tuple[str, str, str, str, str]:
    """(runner URL, litellm URL, probe URL, probe key, worker model)."""
    local = _local_provider(settings)
    runner_url = ""
    litellm_url = ""
    probe_url = ""
    probe_key = ""
    worker_model = ""
    if local is not None:
        runner_url = (local.env.get("ANTHROPIC_BASE_URL") or "").strip()
        litellm_url = runner_url
        probe_url = local.context_probe_url
        probe_key = local.context_probe_key
    # env fallbacks
    runner_url = runner_url or os.environ.get("CLAUDE_RUNNER_URL", "").strip()
    litellm_url = litellm_url or os.environ.get("LITELLM_URL", "").strip()
    probe_key = probe_key or os.environ.get("LLAMACPP_API_KEY", "").strip()
    probe_url = probe_url or os.environ.get("LLAMACPP_CONTEXT_PROBE_URL", "").strip()
    try:
        config = settings.hermes_config() if hasattr(settings, "hermes_config") else {}
    except Exception:
        config = {}
    worker_model = str(config.get("worker_model") or "")
    return runner_url, litellm_url, probe_url, probe_key, worker_model


async def collect_readiness(
    board: BoardStore,
    mission: Mission,
    settings: Any | None = None,
    host_visible: bool = True,
    preflight_max_age_seconds: float = 3600.0,
) -> dict[str, Any]:
    """All readiness checks of a mission, each its own item.

    ``settings`` (the runner's :class:`Settings`) supplies the service URLs and
    keys; when it is ``None`` the checks that need a URL report ``warning``
    (nothing to check against)."""
    live = board.get_live_state(mission.state_namespace)
    config = board.mission_config(mission.mission_id) or {}
    items: list[dict[str, Any]] = [
        check_working_folder(mission.working_directory, host_visible),
        check_required_files(config.get("required_files"), host_visible),
    ]
    if settings is not None:
        runner_url, litellm_url, probe_url, probe_key, worker_model = _service_urls(settings)
        items.append(await check_runner_health(runner_url))
        items.append(await check_litellm_health(litellm_url))
        items.append(check_llama_model(probe_url, probe_key, worker_model))
    else:
        items.append(_item("runner", "warning", "runner settings are not available"))
        items.append(_item("litellm", "warning", "litellm settings are not available"))
        items.append(_item("llama_model", "warning", "runner settings are not available"))
    items.append(check_plan_present(live))
    items.append(check_live_state_present(live))
    items.append(check_not_running(board, mission.mission_id))
    items.append(check_preflight_fresh(board, mission.mission_id, preflight_max_age_seconds))
    return _summarize(mission.mission_id, items)


def _summarize(mission_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """The mission's readiness: every item, plus an overall ``status`` and the
    error items (the ones the start guard blocks on)."""
    status = "ok"
    for item in items:
        if item["status"] == "error":
            status = "error"
        elif item["status"] == "warning" and status != "error":
            status = "warning"
    errors = [item for item in items if item["status"] == "error"]
    return {
        "mission_id": mission_id,
        "status": status,
        "items": items,
        "errors": errors,
        "confirmed_needed": bool(errors),
    }


def start_guard(summary: dict[str, Any], confirmed: bool = False) -> dict[str, Any]:
    """Refuse the start when any check is ``error`` unless ``confirmed``.

    Returns ``{"allowed", "blocked_by", "confirmed", "reason"}`` (``blocked_by``
    is the list of error items; empty when allowed)."""
    errors = summary.get("errors") or [
        item for item in summary.get("items", []) if item["status"] == "error"
    ]
    if errors and not confirmed:
        return {
            "allowed": False,
            "blocked_by": errors,
            "confirmed": False,
            "reason": "blocked by error checks: "
            + "; ".join(f"{i['check']}: {i['reason']}" for i in errors),
        }
    return {"allowed": True, "blocked_by": [], "confirmed": confirmed, "reason": "start allowed"}


# -------------------------------------------------------------------------- rollback


def _row_fields(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """The row's values restricted to ``fields`` (pydantic rows dump to dicts)."""
    data = row.model_dump() if hasattr(row, "model_dump") else dict(row)
    return {k: data.get(k) for k in fields}


def _state_key_of(entry: ChangelogEntry) -> str:
    """The live-state row's key, from the entry's payload when the entry's own
    ``mission_id`` / ``task_key`` fields carry nothing."""
    payload = entry.after_json if isinstance(entry.after_json, dict) else {}
    return str(payload.get("state_key") or "")


def _same_values(current: Any, stored: Any, fields: tuple[str, ...]) -> bool:
    """The row still matches the stored value on every field the entry
    restores (version stamps are not compared: a newer stamp is drift that the
    version check sees, not a value change)."""
    if stored is None:
        return current is None
    a = _row_fields(current, fields) if current is not None else {}
    b = {k: stored.get(k) for k in fields} if isinstance(stored, dict) else {}
    return a == b


def rollback(store: BoardStore, changelog_id: int, who: str = "") -> dict[str, Any]:
    """Write the entry's ``before_json`` back as a new change.

    The entry is honoured only while the current row still equals what its
    ``after_json`` says it was: anything changed after the entry raises
    :class:`ConflictError` ("план изменился, обновите").  The rollback is its
    own changelog row (``action`` "rollback"), whose backup row is the state
    just before the rollback (the row the entry's change had left).  The
    entry's row kind is determined by its ``mission_id`` / ``task_key``."""
    entry = next((e for e in store.changelog() if e.id == changelog_id), None)
    if entry is None:
        raise KeyError(f"changelog entry {changelog_id} not found")
    if entry.before_json is None or entry.after_json is None:
        raise ValueError(f"changelog entry {changelog_id} carries no row value to restore")

    if entry.action == "live state upsert":
        # a live-state change: the row is addressed by its state key.  The audit
        # carries the key in ``mission_id`` (the in-memory store) or in
        # ``task_key`` (the Pg store); when neither carries it, fall back to the
        # state key inside the payload.
        key = entry.task_key or entry.mission_id or _state_key_of(entry)
        fields = _LIVE_FIELDS
        current = store.get_live_state(key)
    elif entry.action == "mission update":
        # a mission change: the row is addressed by its mission id
        key = entry.mission_id or ""
        fields = _MISSION_FIELDS
        current = store.get_mission(key)
    else:
        raise ValueError(f"changelog entry {changelog_id} is not a mission or live-state change")
    if current is None:
        raise KeyError(f"changelog entry {changelog_id} refers to a row that no longer exists")

    if not _same_values(current, entry.after_json, fields):
        raise ConflictError(
            f"row changed after changelog entry {changelog_id}: rollback refused"
        )
    version = current.updated_at or ""
    restored = {k: entry.before_json[k] for k in fields if k in entry.before_json}
    if not restored:
        raise ValueError(f"changelog entry {changelog_id} restores no fields")
    if isinstance(current, Mission):
        store.update_mission(key, version, **restored)
        store.audit_rollback("mission", key, before=current, after=restored,
                            who=who or "")
    else:
        store.upsert_live_state(key, version, **restored)
        store.audit_rollback("live state", key, before=current, after=restored,
                            who=who or "")
    return {"action": "rollback", "restored": restored, "changelog_id": changelog_id}


# -------------------------------------------------------------------------- create


def validate_mission_id(store: BoardStore, mission_id: str) -> str:
    """The mission id must be a valid slug and unique among the store's missions."""
    mission_id = (mission_id or "").strip()
    if not mission_id:
        raise MissionIdError("mission id must not be empty")
    if not _MISSION_ID_RE.match(mission_id):
        raise MissionIdError(
            f"mission id {mission_id!r} is not a valid slug "
            "(lower-case letters, digits, '-'/'_'/'.'; no leading separator)"
        )
    if store.get_mission(mission_id) is not None:
        raise MissionIdError(f"mission id {mission_id!r} is already used")
    if any(m.state_namespace == mission_id for m in store.list_missions()):
        raise MissionIdError(f"mission id {mission_id!r} is already the state key of a mission")
    if store.get_live_state(mission_id) is not None:
        raise MissionIdError(f"state key {mission_id!r} is already used")
    return mission_id


def _template_content(template: dict[str, Any]) -> dict[str, Any]:
    """A template's content fields.  The store's own ``set_template`` keeps the
    content inside the row's ``config_json`` dict (the row itself is ``name``
    and ``source_mission_id``); a plain template dict carries the fields at
    its top level.  The ``name`` is the mission's display name."""
    cfg = template.get("config_json")
    if isinstance(cfg, dict):
        # the stored template row: the content is inside ``config_json``
        source = cfg
    else:
        # a plain template dict
        source = template
    return {
        "name": source.get("name") or "",
        "rules": list(source.get("rules") or []),
        "acceptance_criteria": list(source.get("acceptance_criteria") or []),
        "retrieval_corpora": list(source.get("retrieval_corpora") or []),
        "working_directory": source.get("working_directory"),
        "config": dict(source.get("config_json") or {}),
    }


def _copy_fields(
    store: BoardStore, mission_id: str, template: dict[str, Any] | None
) -> dict[str, Any]:
    """The mission's starting rows from the template or the source mission:
    name, rules, criteria, working directory — and the config (with the
    hints/CLAUDE.md) and required files from its config_json."""
    if template is not None:
        return _template_content(template)
    source = store.get_mission(mission_id)
    if source is None:
        raise KeyError(f"mission {mission_id!r} not found")
    return {
        "name": source.name,
        "rules": list(source.rules),
        "acceptance_criteria": list(source.acceptance_criteria),
        "retrieval_corpora": list(source.retrieval_corpora or []),
        "working_directory": source.working_directory,
        "config": dict(store.mission_config(mission_id) or {}),
    }


def _clean_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """A copied plan keeps only the task definitions: never a task's status or
    attempt (those belong to a live run, not to a template)."""
    if not plan:
        return None
    tasks = [
        {k: v for k, v in task.items() if k not in ("status", "attempt", "review_score", "review_reason")}
        for task in (plan.get("tasks") or [])
    ]
    if not tasks:
        return None
    return {"summary": plan.get("summary", ""), "tasks": tasks}


def _diff_against_template(
    template: dict[str, Any] | None,
    source_mission_id: str | None,
    store: BoardStore,
    new_rows: dict[str, Any],
) -> dict[str, Any]:
    """Which fields of the new mission differ from its template (or source
    mission): one entry per field, the new value next to the original (``None``
    when the field was absent)."""
    if template is not None:
        old = {
            key: value for key, value in _template_content(template).items()
            if key in ("name", "rules", "acceptance_criteria",
                       "retrieval_corpora", "working_directory")
        }
    else:
        source = store.get_mission(source_mission_id or "")
        if source is None:
            old = {}
        else:
            old = {
                "name": source.name,
                "rules": list(source.rules),
                "acceptance_criteria": list(source.acceptance_criteria),
                "retrieval_corpora": list(source.retrieval_corpora or []),
                "working_directory": source.working_directory,
            }
    return {
        key: {"new": new_rows.get(key), "old": old.get(key)}
        for key in ("name", "rules", "acceptance_criteria", "retrieval_corpora",
                    "working_directory")
        if new_rows.get(key) != old.get(key)
    }


def create_mission(
    store: BoardStore,
    mission_id: str,
    source_mission_id: str | None = None,
    template_name: str | None = None,
    objective: str = "",
    plan: dict[str, Any] | None = None,
    position: int | None = None,
    queue_note: str | None = None,
    who: str = "",
) -> dict[str, Any]:
    """Create a mission: one atomic write of the missions row, its Live State
    row and its queue entry — all three rows or none.

    The id is validated (slug, unique); the content comes from
    ``source_mission_id`` or ``template_name`` (name, rules, criteria,
    working folder, config with the hints/CLAUDE.md) — a running source
    mission is refused.  ``objective`` and ``plan`` are supplied here; a
    copied plan is stripped of statuses and attempts.  Returns
    ``{"mission", "live_state", "queue", "diff"}``."""
    if (source_mission_id or "") and (template_name or ""):
        raise ValueError("pass either source_mission_id or template_name, not both")
    validate_mission_id(store, mission_id)
    state_key = mission_id

    template = None
    if template_name:
        for t in store.templates():
            if t.get("name") == template_name:
                template = t
                break
        if template is None:
            raise KeyError(f"template {template_name!r} not found")
    else:
        source = store.get_mission(source_mission_id or "")
        if source is None:
            raise KeyError(f"mission {source_mission_id!r} not found")
        live = store.get_live_state(source.state_namespace)
        if live is not None and live.status == "active":
            raise ValueError(f"mission {source_mission_id!r} is running; copy it when it stops")

    rows = _copy_fields(store, source_mission_id or "", template)
    config = dict(rows.pop("config") or {})
    clean = _clean_plan(plan)
    # the diff is taken from the mission's final values (including the
    # default name when the source/template does not set one)
    diff = _diff_against_template(template, source_mission_id, store, rows)
    if not rows["name"]:
        rows["name"] = mission_id

    mission = Mission(
        mission_id=mission_id,
        state_namespace=state_key,
        name=rows["name"] or mission_id,
        objective=objective,
        working_directory=rows["working_directory"],
        version=1.0,
        enabled=True,
        rules=rows["rules"],
        acceptance_criteria=rows["acceptance_criteria"],
        retrieval_corpora=rows["retrieval_corpora"] or None,
    )
    live = LiveState(state_key=state_key, plan=clean, status="active" if clean else None)

    # The store's own atomic step lands all rows together: the InMemory store
    # under its lock (rolling the rows back on failure), the Pg store inside
    # one transaction (``with self._conn.transaction()``).
    store.create_mission_rows(
        mission, live, config, position=position, note=queue_note, who=who or ""
    )

    return {
        "mission": mission.model_dump(),
        "live_state": live.model_dump(),
        "queue": [dict(e.model_dump()) for e in store.queue()],
        "diff": diff,
    }


def save_template(
    store: BoardStore,
    mission_id: str,
    name: str,
    who: str = "",
) -> dict[str, Any]:
    """Keep a mission as a reusable template: its name, rules, criteria,
    working folder, config (with the hints/CLAUDE.md) — without its objective
    and plan (those are per-mission).  Returns the template row."""
    mission = store.get_mission(mission_id)
    if mission is None:
        raise KeyError(f"mission {mission_id!r} not found")
    config = store.mission_config(mission_id) or {}
    store.set_template(
        name,
        {
            "name": name,
            "rules": list(mission.rules),
            "acceptance_criteria": list(mission.acceptance_criteria),
            "retrieval_corpora": list(mission.retrieval_corpora or []),
            "working_directory": mission.working_directory,
            "config_json": dict(config),
        },
        source_mission_id=mission_id,
    )
    # the template's own row (what ``set_template`` keeps): the name and the
    # config dict (the content fields live in that dict)
    return next((t for t in store.templates() if t.get("name") == name), None)
