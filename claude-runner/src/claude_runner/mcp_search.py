"""``ahawr-mcp-search``: an MCP stdio server that exposes the ``ahawr_search`` tool.

Speaks newline-delimited JSON-RPC 2.0 on stdin/stdout. Protocol messages go to stdout only;
diagnostics go to stderr only. The tool delegates to ``search_cli.search_text``, so a retrieval
outage comes back as ordinary tool text (``retrieval unavailable: ...``), never as a protocol error.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from claude_runner.search_cli import DEFAULT_BUDGET, DEFAULT_URL, search_text

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "ahawr-search"
try:
    SERVER_VERSION = version("claude-runner")
except PackageNotFoundError:
    SERVER_VERSION = "unknown"
TOOL_NAME = "ahawr_search"
TOOL_DESCRIPTION = "Search the mission corpus; returns path:start-end fragments with their text."
DEFAULT_K = 8
# The search tool runs in the Worker's working directory, the same default as ``ahawr-search``.
SEARCH_ROOT = "."

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

TOOL_SCHEMA: dict[str, Any] = {
    "name": TOOL_NAME,
    "description": TOOL_DESCRIPTION,
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "text to search for"},
            "k": {"type": "integer", "default": DEFAULT_K, "description": "number of fragments"},
            "budget": {
                "type": "integer",
                "default": DEFAULT_BUDGET,
                "description": "token budget",
            },
        },
        "required": ["query"],
    },
}


class InvalidParams(ValueError):
    """A request whose params do not fit the method; answered with -32602."""


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def result_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def integer_argument(arguments: dict[str, Any], name: str, default: int) -> int:
    value: object = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidParams(f"{name} must be an integer")
    return value


def initialize(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    }


def list_tools(params: dict[str, Any]) -> dict[str, Any]:
    return {"tools": [TOOL_SCHEMA]}


def call_tool(params: dict[str, Any]) -> dict[str, Any]:
    if params.get("name") != TOOL_NAME:
        raise InvalidParams(f"unknown tool: {params.get('name')}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise InvalidParams("arguments must be an object")
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise InvalidParams("query must be a non-empty string")
    text = search_text(
        query,
        integer_argument(arguments, "k", DEFAULT_K),
        integer_argument(arguments, "budget", DEFAULT_BUDGET),
        SEARCH_ROOT,
        url=os.environ.get("AHAWR_RETRIEVAL_URL", DEFAULT_URL),
    )
    return {"content": [{"type": "text", "text": text}]}


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "initialize": initialize,
    "tools/list": list_tools,
    "tools/call": call_tool,
}


def dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; ``None`` for notifications, which get no reply."""
    if "id" not in message:
        return None
    request_id = message["id"]
    method = message.get("method")
    if not isinstance(method, str):
        return error_response(request_id, INVALID_REQUEST, "method must be a string")
    handler = HANDLERS.get(method)
    if handler is None:
        return error_response(request_id, METHOD_NOT_FOUND, f"method not found: {method}")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return error_response(request_id, INVALID_PARAMS, "params must be an object")
    try:
        return result_response(request_id, handler(params))
    except InvalidParams as exc:
        return error_response(request_id, INVALID_PARAMS, str(exc))


def read_message(line: bytes) -> dict[str, Any] | None:
    """Parse one stdin line and dispatch it; ``None`` when the line needs no reply."""
    try:
        message = json.loads(line)
    except ValueError as exc:
        print(f"ahawr-mcp-search: malformed JSON: {exc}", file=sys.stderr)
        return error_response(None, PARSE_ERROR, "parse error")
    if not isinstance(message, dict):
        return error_response(None, INVALID_REQUEST, "request must be an object")
    return dispatch(message)


def main() -> int:
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        response = read_message(line)
        if response is not None:
            write_message(response)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
