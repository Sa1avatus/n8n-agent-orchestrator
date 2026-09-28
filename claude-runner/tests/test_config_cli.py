from __future__ import annotations

import json

import pytest

from claude_runner.__main__ import main
from claude_runner.claude_cli import StreamState, build_command, child_env, classify
from claude_runner.config import Settings


def test_settings_from_env_profiles_and_validation(tmp_path: object) -> None:
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_REVIEWER_PERMISSION_MODE": "plan",
            "CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS": "Read, Grep",
            "CLAUDE_RUNNER_WORKER_DISALLOWED_TOOLS": "",
            "CLAUDE_RUNNER_WORKER_MAX_TURNS": "40",
            "CLAUDE_RUNNER_EXTRA_ARGS": "--effort high",
            "CLAUDE_RUNNER_BARE": "true",
            "CLAUDE_RUNNER_MAX_BUDGET_USD": "2.5",
            "CLAUDE_RUNNER_PATH_MAP": "D:\\a=/w,D:\\a\\b=/x,broken",
            "CLAUDE_RUNNER_TOOLS": "Bash,Read,Edit",
            "CLAUDE_RUNNER_REVIEWER_TOOLS": "Read,Grep,Glob",
        }
    )
    assert s.profile("reviewer").permission_mode == "plan"
    assert s.profile("reviewer").allowed_tools == ["Read", "Grep"]
    assert s.profile("worker").disallowed_tools == []
    assert s.profile("nobody") == s.profile("generic")
    assert s.path_map[0] == ("D:\\a\\b", "/x")
    cmd = build_command(s, s.profile("worker"), model="opus", claude_session_id="id", resume=True)
    assert cmd[-2:] == ["--effort", "high"]
    assert "--bare" in cmd and cmd[cmd.index("--max-turns") + 1] == "40"
    assert cmd[cmd.index("--max-budget-usd") + 1] == "2.5"
    assert cmd[cmd.index("--tools") + 1] == "Bash,Read,Edit"
    reviewer = build_command(
        s, s.profile("reviewer"), model="m", claude_session_id="id", resume=False
    )
    assert reviewer[reviewer.index("--tools") + 1] == "Read,Grep,Glob"
    compact = build_command(
        s, s.profile("worker"), model="", claude_session_id="id", resume=True, compact=True
    )
    assert "--permission-mode" not in compact and "--model" not in compact
    with pytest.raises(ValueError):
        Settings.from_env({"CLAUDE_RUNNER_WORKER_PERMISSION_MODE": "yolo"})
    with pytest.raises(ValueError):
        Settings.from_env({"CLAUDE_RUNNER_COMPACT_MODE": "sometimes"})
    with pytest.raises(ValueError):
        Settings.from_env({"CLAUDE_RUNNER_MAX_CONCURRENT": "0"})


def test_child_env_hides_runner_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_RUNNER_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    env = child_env()
    assert "CLAUDE_RUNNER_API_KEY" not in env and env["ANTHROPIC_API_KEY"] == "k"
    assert env["DISABLE_AUTOUPDATER"] == "1"


def test_classify_edge_cases() -> None:
    state = StreamState()
    for line in [
        "",
        "[]",
        "{bad",
        '{"type":"assistant","parent_tool_use_id":"t1",'
        '"message":{"content":[{"type":"text","text":"sub"}]}}',
    ]:
        state.feed(line)
    assert state.bad_lines == 1 and state.last_text == ""
    missing = classify(state, 1, "No conversation found with session ID: abc")
    assert missing.error_code == "session_not_found"
    cancelled = classify(state, -2, "", cancelled=True)
    assert cancelled.status == "cancelled"
    state.feed('{"type":"system","subtype":"api_retry","error":"server_error","error_status":502}')
    state.feed('{"type":"result","subtype":"success","is_error":true,"result":"boom"}')
    server = classify(state, 1, "")
    assert server.error_code == "server_error" and server.http_code == 502
    upstream = StreamState()
    upstream.feed(
        '{"type":"result","subtype":"success","is_error":true,"result":"x","api_error_status":503}'
    )
    unavailable = classify(upstream, 1, "")
    assert unavailable.error_code == "server_error" and unavailable.http_code == 503
    budget = StreamState()
    budget.feed('{"type":"result","subtype":"error_max_budget_usd","is_error":true}')
    assert classify(budget, 1, "").error_code == "max_budget"
    other = StreamState()
    other.feed('{"type":"result","subtype":"error_during_execution","is_error":true}')
    assert classify(other, 1, "").error_code == "error_during_execution"
    assert classify(StreamState(), 2, "").error_message == "Claude Code exited with code 2."


def test_main_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("CLAUDE_RUNNER_DATA_DIR", "/tmp/claude-runner-test")
    assert main(["--port", "8799"]) == 0
    assert seen["port"] == 8799


def test_context_size_fallback_for_gateways_without_streamed_usage() -> None:
    state = StreamState()
    state.feed(
        '{"type":"assistant","parent_tool_use_id":null,"message":{"content":[],'
        '"usage":{"input_tokens":0,"output_tokens":0}}}'
    )
    assert state.final_context_tokens() is None
    state.feed(
        '{"type":"result","subtype":"success","is_error":false,"num_turns":2,'
        '"usage":{"input_tokens":2400,"cache_read_input_tokens":0}}'
    )
    assert state.final_context_tokens() == 1200
    direct = StreamState()
    direct.feed(
        '{"type":"assistant","parent_tool_use_id":null,"message":{"content":[],'
        '"usage":{"input_tokens":5,"cache_read_input_tokens":900}}}'
    )
    direct.feed('{"type":"result","subtype":"success","num_turns":3,"usage":{"input_tokens":1}}')
    assert direct.final_context_tokens() == 905


def test_context_size_from_stream_events_of_a_run_that_timed_out() -> None:
    def stream(inner: dict[str, object], agent: str | None = None) -> str:
        return json.dumps({"type": "stream_event", "parent_tool_use_id": agent, "event": inner})

    state = StreamState()
    state.feed(stream({"type": "message_start", "message": {"usage": {"input_tokens": 41000}}}))
    state.feed(stream({"type": "message_delta", "usage": {"input_tokens": 63824}}))
    # a subagent's request is not the session's size
    state.feed(stream({"type": "message_start", "message": {"usage": {"input_tokens": 9}}}, "t1"))
    state.feed(stream({"type": "message_delta", "usage": {"output_tokens": 5}}))
    assert state.final_context_tokens() == 63824  # no result event: the run was cut off
