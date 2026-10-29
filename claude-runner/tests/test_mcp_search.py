from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from claude_runner import mcp_search, search_cli

SRC = Path(__file__).resolve().parent.parent / "src"


def _line(message: dict[str, Any]) -> bytes:
    return (json.dumps(message) + "\n").encode()


def _request(request_id: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return _line(message)


def _serve(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], lines: list[bytes]
) -> tuple[list[dict[str, Any]], str]:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"".join(lines))))
    assert mcp_search.main() == 0
    captured = capsys.readouterr()
    return [json.loads(out_line) for out_line in captured.out.splitlines()], captured.err


def test_initialize_reports_protocol_version_and_tools_capability(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replies, _ = _serve(monkeypatch, capsys, [_request(1, "initialize", {})])

    assert len(replies) == 1
    assert replies[0]["id"] == 1
    result = replies[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "ahawr-search"


def test_initialized_notification_gets_no_reply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    notification = _line({"jsonrpc": "2.0", "method": "notifications/initialized"})

    replies, _ = _serve(monkeypatch, capsys, [notification])

    assert replies == []


def test_tools_list_publishes_schema_with_defaults(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replies, _ = _serve(monkeypatch, capsys, [_request(2, "tools/list")])

    tools = replies[0]["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["ahawr_search"]
    schema = tools[0]["inputSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["query"]
    assert schema["properties"]["query"]["type"] == "string"
    assert schema["properties"]["k"] == {
        "type": "integer",
        "default": mcp_search.DEFAULT_K,
        "description": "number of fragments",
    }
    assert schema["properties"]["budget"]["default"] == search_cli.DEFAULT_BUDGET


def test_tools_call_returns_search_text_as_tool_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, int, int, str]] = []

    def fake_search(query: str, k: int, budget: int, root: str, **_options: Any) -> str:
        calls.append((query, k, budget, root))
        return "src/a.md:1-3\nfragment text"

    monkeypatch.setattr(mcp_search, "search_text", fake_search)
    default_call = _request(
        3, "tools/call", {"name": "ahawr_search", "arguments": {"query": "retry delay"}}
    )
    explicit_call = _request(
        4,
        "tools/call",
        {"name": "ahawr_search", "arguments": {"query": "queue", "k": 2, "budget": 500}},
    )

    replies, _ = _serve(monkeypatch, capsys, [default_call, explicit_call])

    assert calls == [
        ("retry delay", mcp_search.DEFAULT_K, search_cli.DEFAULT_BUDGET, mcp_search.SEARCH_ROOT),
        ("queue", 2, 500, mcp_search.SEARCH_ROOT),
    ]
    assert replies[0]["result"] == {
        "content": [{"type": "text", "text": "src/a.md:1-3\nfragment text"}]
    }
    assert "error" not in replies[1]


def test_retrieval_failure_is_tool_text_not_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    root = os.path.realpath(tmp_path)
    monkeypatch.setattr(search_cli, "ALLOWED_PREFIXES", (root,))
    monkeypatch.setattr(mcp_search, "SEARCH_ROOT", root)

    def unreachable(*_args: Any, **_kwargs: Any) -> None:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(search_cli, "fetch_detailed", unreachable)
    call = _request(5, "tools/call", {"name": "ahawr_search", "arguments": {"query": "anything"}})

    replies, _ = _serve(monkeypatch, capsys, [call])

    assert "error" not in replies[0]
    assert replies[0]["id"] == 5
    assert replies[0]["result"] == {
        "content": [
            {"type": "text", "text": "retrieval unavailable: the service cannot be reached"}
        ]
    }


@pytest.mark.parametrize(
    "params",
    [
        {"name": "other_tool", "arguments": {"query": "x"}},
        {"name": "ahawr_search", "arguments": {}},
        {"name": "ahawr_search", "arguments": {"query": "x", "k": "3"}},
    ],
)
def test_tools_call_with_bad_params_is_invalid_params(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], params: dict[str, Any]
) -> None:
    replies, _ = _serve(monkeypatch, capsys, [_request(6, "tools/call", params)])

    assert replies[0]["id"] == 6
    assert replies[0]["error"]["code"] == -32602


def test_unknown_method_is_method_not_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replies, _ = _serve(monkeypatch, capsys, [_request(7, "resources/list")])

    assert replies[0]["id"] == 7
    assert replies[0]["error"]["code"] == -32601


def test_malformed_json_is_parse_error_and_server_keeps_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [b"{not json\n", _request(8, "initialize", {})]

    replies, err = _serve(monkeypatch, capsys, lines)

    assert len(replies) == 2
    assert replies[0] == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": "parse error"},
    }
    assert replies[1]["id"] == 8
    assert "protocolVersion" in replies[1]["result"]
    assert "malformed JSON" in err


def test_stdout_carries_only_json_lines_in_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = [
        b"{not json\n",
        _request(9, "initialize", {}),
        _line({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        _request(10, "tools/list"),
        _request(11, "bogus"),
    ]

    replies, _ = _serve(monkeypatch, capsys, lines)

    assert [reply["id"] for reply in replies] == [None, 9, 10, 11]
    assert all(reply["jsonrpc"] == "2.0" for reply in replies)


def test_subprocess_stdout_carries_only_json_lines() -> None:
    stdin = b"".join(
        [
            b"{not json\n",
            _request(12, "initialize", {}),
            _request(13, "tools/list"),
        ]
    )
    env = {**os.environ, "PYTHONPATH": str(SRC)}

    completed = subprocess.run(
        [sys.executable, "-m", "claude_runner.mcp_search"],
        input=stdin,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0
    out_lines = completed.stdout.decode().splitlines()
    replies = [json.loads(out_line) for out_line in out_lines]
    assert [reply["id"] for reply in replies] == [None, 12, 13]
    assert b"malformed JSON" in completed.stderr
