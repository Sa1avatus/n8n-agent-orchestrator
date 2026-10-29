"""``ahawr-mcp-board``: an MCP stdio server over the board store.

Same wire format as ``ahawr-mcp-search``: newline-delimited JSON-RPC 2.0 on stdin/stdout,
diagnostics on stderr. A store failure comes back as tool text with ``isError`` set, so the server
keeps running. Write tools reuse the plan guard and store functions of the board UI; dangerous
actions (stop, pause, rebuild, docker update) are refused with text pointing at the dashboard.
The store is injected into ``dispatch``; ``main`` reads it from ``get_board_store``,
which takes the DSN from the environment and never prints it.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from claude_runner import board_plan
from claude_runner.board import BoardStore, ConflictError, GuardError, Mission, get_board_store
from claude_runner.board_api import _plan_guard
from claude_runner.mcp_search import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SERVER_VERSION,
    InvalidParams,
    error_response,
    result_response,
    write_message,
)

SERVER_NAME = "ahawr-board"

_NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {}}
_MISSION_ARGUMENTS: dict[str, Any] = {
    "type": "object",
    "properties": {"mission_id": {"type": "string", "description": "mission id"}},
    "required": ["mission_id"],
}
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "queue_list",
        "description": "List the board queue in run order.",
        "inputSchema": _NO_ARGUMENTS,
    },
    {
        "name": "mission_get",
        "description": "Return one mission row by mission_id.",
        "inputSchema": _MISSION_ARGUMENTS,
    },
    {
        "name": "task_list",
        "description": "Return the per-task status of one mission by mission_id.",
        "inputSchema": _MISSION_ARGUMENTS,
    },
    {
        "name": "settings_get",
        "description": "Return the claude-code settings row.",
        "inputSchema": _NO_ARGUMENTS,
    },
    {
        "name": "health",
        "description": "Check that the board store answers.",
        "inputSchema": _NO_ARGUMENTS,
    },
    {
        "name": "task_edit",
        "description": "Edit one pending task's content (title, objective, "
        "acceptance_criteria, verification, scope).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {"type": "string", "description": "mission id"},
                "task_key": {"type": "string", "description": "task key (T001...)"},
                "expected_version": {
                    "type": "string", "description": "the plan version read before the edit",
                },
                "fields": {"type": "object", "description": "task content fields to change"},
            },
            "required": ["mission_id", "task_key", "expected_version"],
        },
    },
    {
        "name": "add_hint",
        "description": "Set or clear the Worker hint of one task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {"type": "string", "description": "mission id"},
                "task_key": {"type": "string", "description": "task key (T001...)"},
                "expected_version": {
                    "type": "string", "description": "the plan version read before the edit",
                },
                "hint": {
                    "type": ["string", "null"], "description": "the hint text; null clears it",
                },
            },
            "required": ["mission_id", "task_key", "expected_version"],
        },
    },
    {
        "name": "queue_move",
        "description": "Move a mission to another position in the queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {"type": "string", "description": "mission id"},
                "to_position": {"type": "integer", "description": "the new 0-based queue position"},
                "expected_version": {
                    "type": "string", "description": "the queue version read before the move",
                },
            },
            "required": ["mission_id", "to_position", "expected_version"],
        },
    },
    {
        "name": "enable_mission",
        "description": "Enable or disable a mission.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {"type": "string", "description": "mission id"},
                "enabled": {"type": "boolean", "description": "the new enabled flag"},
                "expected_version": {
                    "type": "string", "description": "the mission version read before the change",
                },
            },
            "required": ["mission_id", "enabled", "expected_version"],
        },
    },
    {
        "name": "pause_after_current",
        "description": "Explain how to pause the queue after the current run.",
        "inputSchema": _NO_ARGUMENTS,
    },
    {
        "name": "dangerous_action_help",
        "description": "Explain how to run stop, pause, rebuild or docker update.",
        "inputSchema": _NO_ARGUMENTS,
    },
]


DANGEROUS_ACTIONS_TEXT = (
    "stop, pause (ops action pause_at_boundary), rebuild and docker update are "
    "dangerous; this server does not run them, create them or confirm them; "
    "ask a human to request and confirm them in the dashboard ops panel"
)


def store_text(text: str) -> str:
    # shortcut: no redaction yet, wire provider_secrets.redact here once T019 exists
    return text


def mission_id_argument(arguments: dict[str, Any]) -> str:
    value = arguments.get("mission_id")
    if not isinstance(value, str) or not value.strip():
        raise InvalidParams("mission_id must be a non-empty string")
    return value


def existing_mission(store: BoardStore, arguments: dict[str, Any]) -> Mission:
    mission_id = mission_id_argument(arguments)
    mission = store.get_mission(mission_id)
    if mission is None:
        raise LookupError(f"mission not found: {mission_id}")
    return mission


def queue_list(store: BoardStore, arguments: dict[str, Any]) -> Any:
    return [entry.model_dump() for entry in store.queue()]


def mission_get(store: BoardStore, arguments: dict[str, Any]) -> Any:
    return existing_mission(store, arguments).model_dump()


def task_list(store: BoardStore, arguments: dict[str, Any]) -> Any:
    mission = existing_mission(store, arguments)
    return [view.model_dump() for view in store.task_views(mission.state_namespace)]


def settings_get(store: BoardStore, arguments: dict[str, Any]) -> Any:
    return store.hermes_config()


def health(store: BoardStore, arguments: dict[str, Any]) -> Any:
    store.queue()
    return {"ok": True, "store": type(store).__name__}


def expected_version_argument(arguments: dict[str, Any]) -> str:
    value = arguments.get("expected_version")
    if not isinstance(value, str) or not value.strip():
        raise InvalidParams("expected_version must be a non-empty string")
    return value


def task_key_argument(arguments: dict[str, Any]) -> str:
    value = arguments.get("task_key")
    if not isinstance(value, str) or not value.strip():
        raise InvalidParams("task_key must be a non-empty string")
    return value


def task_edit(store: BoardStore, arguments: dict[str, Any]) -> Any:
    mission_id = mission_id_argument(arguments)
    task_key = task_key_argument(arguments)
    expected = expected_version_argument(arguments)
    data = _plan_guard(store, mission_id)
    live = store.get_live_state(data["state_key"])
    plan = dict(live.plan or {}) if live is not None else {}
    payload = {
        "action": "task update",
        "task_key": task_key,
        "fields": arguments.get("fields") or {},
        "previous_status": None,
    }
    plan, _ = board_plan.update_task(
        plan, expected, data["stored_version"], task_key,
        data["live_status"], data["live_task_index"], data["live_task_attempt"],
        events=data["events"], **(arguments.get("fields") or {}),
    )
    store.upsert_live_state(data["state_key"], None, plan=plan, who="mcp")
    return {"plan": plan, "changelog": payload}


def add_hint(store: BoardStore, arguments: dict[str, Any]) -> Any:
    mission_id = mission_id_argument(arguments)
    task_key = task_key_argument(arguments)
    expected = expected_version_argument(arguments)
    hint = arguments.get("hint")
    if not isinstance(hint, (str, type(None))):
        raise InvalidParams("hint must be a string or null")
    data = _plan_guard(store, mission_id)
    live = store.get_live_state(data["state_key"])
    plan = dict(live.plan or {}) if live is not None else {}
    payload = {
        "action": "task hint set",
        "override": {"task_key": task_key, "hint": hint},
        "previous_status": None,
    }
    board_plan.set_task_hint(
        plan, expected, data["stored_version"], task_key, hint,
        data["live_status"], data["live_task_index"], data["live_task_attempt"],
        events=data["events"],
    )
    row = next((o for o in store.task_overrides(mission_id) if o.get("task_key") == task_key), None)
    store.set_task_override(
        mission_id, task_key, hint, None,
        expected_version=row.get("updated_at") if row is not None else None,
        who="mcp",
    )
    return {"override": payload["override"]}


def queue_move(store: BoardStore, arguments: dict[str, Any]) -> Any:
    mission_id = mission_id_argument(arguments)
    expected = expected_version_argument(arguments)
    to_position = arguments.get("to_position")
    if not isinstance(to_position, int):
        raise InvalidParams("to_position must be an integer")
    updated = store.move_queue(mission_id, to_position, expected)
    return {"queue": [e.model_dump() for e in updated]}


def enable_mission(store: BoardStore, arguments: dict[str, Any]) -> Any:
    mission_id = mission_id_argument(arguments)
    expected = expected_version_argument(arguments)
    enabled = arguments.get("enabled")
    if not isinstance(enabled, bool):
        raise InvalidParams("enabled must be a boolean")
    updated = store.update_mission(mission_id, expected, enabled=enabled, who="mcp")
    return {"mission": updated.model_dump()}


def pause_after_current(store: BoardStore, arguments: dict[str, Any]) -> Any:
    return DANGEROUS_ACTIONS_TEXT


def dangerous_action_help(store: BoardStore, arguments: dict[str, Any]) -> Any:
    return DANGEROUS_ACTIONS_TEXT


TOOL_HANDLERS: dict[str, Callable[[BoardStore, dict[str, Any]], Any]] = {
    "queue_list": queue_list,
    "mission_get": mission_get,
    "task_list": task_list,
    "settings_get": settings_get,
    "health": health,
    "task_edit": task_edit,
    "add_hint": add_hint,
    "queue_move": queue_move,
    "enable_mission": enable_mission,
    "pause_after_current": pause_after_current,
    "dangerous_action_help": dangerous_action_help,
}


def tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": store_text(text)}]}
    if is_error:
        result["isError"] = True
    return result


def initialize(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def list_tools(params: dict[str, Any]) -> dict[str, Any]:
    return {"tools": TOOL_SCHEMAS}


def call_tool(store: BoardStore, params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    handler = TOOL_HANDLERS.get(name) if isinstance(name, str) else None
    if handler is None:
        raise InvalidParams(f"unknown tool: {name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise InvalidParams("arguments must be an object")
    try:
        payload = handler(store, arguments)
    except InvalidParams:
        raise
    except ConflictError as exc:
        return tool_result(f"version conflict: reload the plan and retry ({exc})", is_error=True)
    except GuardError as exc:
        return tool_result(f"task already ran or is running and is read-only: {exc}", is_error=True)
    except Exception as exc:  # a store failure is reported as tool text; the server keeps running
        return tool_result(f"error: {type(exc).__name__}: {exc}", is_error=True)
    return tool_result(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def dispatch(message: dict[str, Any], store: BoardStore) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; ``None`` for notifications, which get no reply."""
    if "id" not in message:
        return None
    request_id = message["id"]
    method = message.get("method")
    if not isinstance(method, str):
        return error_response(request_id, INVALID_REQUEST, "method must be a string")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return error_response(request_id, INVALID_PARAMS, "params must be an object")
    try:
        if method == "tools/call":
            return result_response(request_id, call_tool(store, params))
        if method == "tools/list":
            return result_response(request_id, list_tools(params))
        if method == "initialize":
            return result_response(request_id, initialize(params))
    except InvalidParams as exc:
        return error_response(request_id, INVALID_PARAMS, str(exc))
    return error_response(request_id, METHOD_NOT_FOUND, f"method not found: {method}")


def read_message(line: bytes, store: BoardStore) -> dict[str, Any] | None:
    try:
        message = json.loads(line)
    except ValueError as exc:
        print(f"ahawr-mcp-board: malformed JSON: {exc}", file=sys.stderr)
        return error_response(None, PARSE_ERROR, "parse error")
    if not isinstance(message, dict):
        return error_response(None, INVALID_REQUEST, "request must be an object")
    return dispatch(message, store)


def main() -> int:
    store = get_board_store()
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        response = read_message(line, store)
        if response is not None:
            write_message(response)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
