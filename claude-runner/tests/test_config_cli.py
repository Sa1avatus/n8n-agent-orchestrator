from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_runner.__main__ import main
from claude_runner.claude_cli import StreamState, build_command, child_env, classify
from claude_runner.config import (
    AUTO_COMPACT_WINDOW,
    DEFAULT_RETRY_INPUT_CHARS,
    ProviderProfile,
    Settings,
)


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


def test_retry_fresh_session_settings() -> None:
    # Off by default, with a configurable character budget for the retry input.
    s = Settings.from_env({})
    assert s.retry_fresh_session is False
    assert s.retry_input_chars == DEFAULT_RETRY_INPUT_CHARS
    on = Settings.from_env(
        {
            "CLAUDE_RUNNER_RETRY_FRESH_SESSION": "1",
            "CLAUDE_RUNNER_RETRY_INPUT_CHARS": "18000",
        }
    )
    assert on.retry_fresh_session is True and on.retry_input_chars == 18000
    # Per-provider override: the provider wins over the global value, and an empty
    # value falls back to the global one.
    per = Settings.from_env(
        {
            "CLAUDE_RUNNER_RETRY_FRESH_SESSION": "0",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__RETRY_FRESH_SESSION": "1",
        }
    )
    assert per.providers["LOCAL"].retry_fresh_session is True
    assert per.retry_fresh_session_for(per.providers["LOCAL"]) is True
    assert per.retry_fresh_session_for(None) is False
    empty = Settings.from_env(
        {
            "CLAUDE_RUNNER_RETRY_FRESH_SESSION": "1",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__RETRY_FRESH_SESSION": "",
        }
    )
    assert empty.providers["LOCAL"].retry_fresh_session is None
    assert empty.retry_fresh_session_for(empty.providers["LOCAL"]) is True
    assert empty.retry_fresh_session_for(None) is True
    with pytest.raises(ValueError):
        Settings.from_env({"CLAUDE_RUNNER_RETRY_INPUT_CHARS": "0"})


def test_compact_instructions_delivered_via_precompact_hook(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    # A provider block with its own endpoint: the local provider the instructions target.
    local = ProviderProfile(
        name="LOCAL",
        env={"ANTHROPIC_BASE_URL": "http://litellm:4000", "ANTHROPIC_AUTH_TOKEN": "sk-litellm"},
    )
    s = Settings.from_env({})
    s.providers["LOCAL"] = local
    assert "## Task and acceptance criteria" in s.compact_instructions
    assert "1500-2000" in s.compact_instructions
    assert "Do not copy tool outputs" in s.compact_instructions
    assert "## Findings (file:line)" in s.compact_instructions
    assert "## Changed files" in s.compact_instructions
    assert "## Checks done / not done" in s.compact_instructions
    assert "## Next step" in s.compact_instructions

    # Only the local (credential-isolating) provider gets the settings file; the
    # PreCompact hook only fires on compaction, so normal runs are unaffected.
    assert local.isolates_credentials
    cmd = build_command(
        s, s.profile("worker"), model="m", claude_session_id="id", resume=True, provider=local
    )
    assert "--settings" in cmd
    assert cmd[cmd.index("--settings") + 1] == str(tmp_path) + "/compact-settings.json"
    payload = json.loads((tmp_path / "compact-settings.json").read_text("utf-8"))
    hook = payload["hooks"]["PreCompact"][0]["hooks"][0]
    assert hook["type"] == "command"
    assert "## Task and acceptance criteria" in hook["command"]

    # A compact run gets it too, with the usual compact flags still absent.
    compact = build_command(
        s,
        s.profile("worker"),
        model="",
        claude_session_id="id",
        resume=True,
        compact=True,
        provider=local,
    )
    assert "--permission-mode" not in compact and "--settings" in compact

    # Overriding the instruction text via env works.
    custom = Settings.from_env({"CLAUDE_RUNNER_COMPACT_INSTRUCTIONS": "кратко"})
    assert custom.compact_instructions == "кратко"
    build_command(
        custom,
        s.profile("worker"),
        model="m",
        claude_session_id="id",
        resume=True,
        provider=local,
    )
    custom_hook = json.loads((tmp_path / "compact-settings.json").read_text("utf-8"))["hooks"][
        "PreCompact"
    ][0]["hooks"][0]
    assert "кратко" in custom_hook["command"]

    # Empty instructions add no settings file at all.
    bare = Settings.from_env({"CLAUDE_RUNNER_COMPACT_INSTRUCTIONS": ""})
    empty = build_command(
        bare,
        s.profile("worker"),
        model="m",
        claude_session_id="id",
        resume=True,
        provider=local,
    )
    assert "--settings" not in empty

    # The flag is off: no settings file even for the local provider, including a compact run.
    off = Settings.from_env({"CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS": "0"})
    assert off.local_compact_instructions is False
    gated = build_command(
        off,
        s.profile("worker"),
        model="m",
        claude_session_id="id",
        resume=True,
        provider=local,
    )
    assert "--settings" not in gated
    gated_compact = build_command(
        off,
        s.profile("worker"),
        model="",
        claude_session_id="id",
        resume=True,
        compact=True,
        provider=local,
    )
    assert "--settings" not in gated_compact

    # No local provider (default container env): no settings file.
    none = build_command(
        s, s.profile("worker"), model="m", claude_session_id="id", resume=True, provider=None
    )
    assert "--settings" not in none


def test_autocompact_threshold_from_pct_and_absolute_window() -> None:
    # 56.98% of the 65536-token window: 0.5698 * 65536 = 37342.4 → 37342.
    s = Settings.from_env(
        {"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "56.98"}
    )
    assert s.provider("local").autocompact_threshold == 37342
    # An absolute window: the trigger is window - min(max_output, 20000) - 13000.
    # 65536 - 8192 - 13000 = 44344 (the default effective trigger, ~44K).
    s = Settings.from_env(
        {"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW": "65536"}
    )
    assert s.provider("local").autocompact_threshold == 44344


def test_autocompact_reserve_keeps_runs_inside_the_65536_window() -> None:
    # The full window: trigger = 65536 - 8192 - 13000 = 44344, headroom = 21192.
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW": "65536",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192",
        }
    )
    local = s.provider("local")
    assert local is not None
    assert local.autocompact_threshold == 44344
    assert local.max_output_tokens == 8192
    # Headroom after the trigger: 65536 - 44344 = 21192 >= 20000 tool reserve.
    assert local.autocompact_threshold + 8192 + 13000 == AUTO_COMPACT_WINDOW
    # A window larger than the 65536-token window produces a threshold above the
    # window and is rejected.
    with pytest.raises(ValueError):
        Settings.from_env(
            {"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW": "200000"}
        )
    # A PCT that pushes the trigger close to the full window leaves less than the
    # 20000-token tool reserve: 0.99 * 65536 = 64880, headroom = 656 < 20000.
    with pytest.raises(ValueError):
        Settings.from_env({"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "99"})
    # The 65536 window still gives the default 44344 threshold, headroom 21192.
    s = Settings.from_env(
        {"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW": "65536"}
    )
    local = s.provider("local")
    assert local is not None
    assert local.autocompact_threshold == 44344
    assert AUTO_COMPACT_WINDOW - local.autocompact_threshold == 21192
    # With no threshold set there is nothing to validate.
    s = Settings.from_env({"CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192"})
    assert s.provider("local").autocompact_threshold is None


def test_child_env_carries_the_local_provider_output_limits() -> None:
    # The output-limit variables (and the autocompact window) reach the Claude Code
    # process env through the provider block: they are what actually reduce how often
    # a run hits the compaction threshold.
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-litellm",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__BASH_MAX_OUTPUT_LENGTH": "12000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS": "10000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS": "8192",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW": "65536",
        }
    )
    local = s.provider("local")
    env = child_env(local)
    assert env["BASH_MAX_OUTPUT_LENGTH"] == "12000"
    assert env["CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS"] == "10000"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "8192"
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "65536"
    # The runner's own settings still do not leak into the child process.
    assert not any(key.startswith("CLAUDE_RUNNER_") for key in env)


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


def _reply(message_id: str, text: str, stop: str | None = None, tool: bool = False) -> str:
    content: list[dict[str, object]] = [{"type": "text", "text": text}] if text else []
    if tool:
        content.append({"type": "tool_use", "id": "t", "name": "Read", "input": {}})
    message = {"id": message_id, "content": content, "stop_reason": stop}
    return json.dumps({"type": "assistant", "parent_tool_use_id": None, "message": message})


def test_report_cut_at_output_limit_keeps_both_parts() -> None:
    state = StreamState()
    state.feed(_reply("m0", "Reading the code.", "max_tokens"))  # an early cut: work, not report
    for i in range(1, 5):
        state.feed(_reply(f"m{i}", "", "tool_use", tool=True))
    state.feed(_reply("m5", "## FIX APPLIED\npart one", "max_tokens"))
    state.feed(_reply("m6", "Let me see where it stopped.", "tool_use", tool=True))
    state.feed(_reply("m7", "part two (continuing)", "end_turn"))
    state.feed(
        json.dumps({"type": "result", "subtype": "success", "result": "part two (continuing)"})
    )
    out = classify(state, 0, "")
    assert out.status == "completed"
    assert out.output == "## FIX APPLIED\npart one\n\npart two (continuing)"


def test_report_cut_found_from_stream_event_and_not_doubled() -> None:
    state = StreamState()
    state.feed(_reply("m1", "whole report, cut", None))
    delta = {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {}}
    state.feed(json.dumps({"type": "stream_event", "parent_tool_use_id": None, "event": delta}))
    assert state.cut == ["m1"]
    # no continuation: the cut reply is the result itself and is not repeated
    state.feed(json.dumps({"type": "result", "subtype": "success", "result": "whole report, cut"}))
    assert classify(state, 0, "").output == "whole report, cut"
    plain = StreamState()
    plain.feed(_reply("a", "done", "end_turn"))
    plain.feed(json.dumps({"type": "result", "subtype": "success", "result": "done"}))
    assert classify(plain, 0, "").output == "done"


def test_secret_deny_keeps_env_example_readable() -> None:
    worker = Settings.from_env({}).profiles["worker"].disallowed_tools
    assert "Read(./.env)" in worker and "Read(**/.env.production)" in worker
    assert "Read(./.env.*.local)" in worker
    assert not any(".env.example" in rule or rule.endswith(".env.*)") for rule in worker)


def test_role_add_dirs_become_add_dir_flags() -> None:
    s = Settings.from_env({"CLAUDE_RUNNER_REVIEWER_ADD_DIRS": "/tmp/work, /d/rag-tmp"})
    reviewer = build_command(
        s, s.profile("reviewer"), model="opus", claude_session_id="id", resume=False
    )
    i = reviewer.index("--add-dir")
    assert reviewer[i : i + 4] == ["--add-dir", "/tmp/work", "--add-dir", "/d/rag-tmp"]
    worker = build_command(
        s, s.profile("worker"), model="opus", claude_session_id="id", resume=False
    )
    assert "--add-dir" not in worker
    compact = build_command(
        s, s.profile("reviewer"), model="opus", claude_session_id="id", resume=True, compact=True
    )
    assert "--add-dir" not in compact


def test_reviewer_default_profile_allows_read_only_bash_and_ahawr_search():
    prof = Settings.from_env({}).profile("reviewer")
    assert prof.permission_mode == "dontAsk"
    for rule in (
        "Bash(ahawr-search *)",
        "Bash(grep *)",
        "Bash(git diff *)",
        "Bash(git -C * log *)",
        "Bash(bash -n *)",
    ):
        assert rule in prof.allowed_tools
    # nothing that writes, builds or changes git state
    joined = " ".join(prof.allowed_tools)
    for word in (
        "commit",
        "push",
        "checkout",
        "reset",
        "apply",
        "cmake",
        "make",
        "rm ",
        "sed",
        "tee",
    ):
        assert word not in joined
    assert "Bash(* >*)" in prof.disallowed_tools and "Edit" in prof.disallowed_tools
    # the env override still replaces the list, and an empty value switches Bash off again
    assert (
        Settings.from_env({"CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS": ""})
        .profile("reviewer")
        .allowed_tools
        == []
    )
    # the Worker and Architect are unchanged
    assert Settings.from_env({}).profile("architect").allowed_tools == []
    assert Settings.from_env({}).profile("worker").allowed_tools == []


def test_result_placement_rule_in_prompts_keeps_reviewer_read_only(tmp_path: Path) -> None:
    """T011: the shipped default rows of agent_prompts.example.csv do NOT carry the
    result placement rule (opt-in, off by default), and the Reviewer profile stays
    read-only regardless."""
    import csv
    from pathlib import Path

    rows = list(
        csv.reader(
            (Path(__file__).resolve().parents[2] / "agent_prompts.example.csv").open(newline="")
        )
    )
    assert [r[0] for r in rows[1:]] == ["architect", "worker", "reviewer"]
    architect, worker, reviewer = (r[3] for r in rows[1:])
    # the rule is NOT in the shipped defaults (it is opt-in text per §5.3)
    assert "RESULT PLACEMENT RULE" not in architect
    assert "RESULT PLACEMENT RULE" not in worker
    assert "RESULT PLACEMENT RULE" not in reviewer
    # the Reviewer stays read-only: no write tools, no shell commands that write/build
    prof = Settings.from_env({}).profile("reviewer")
    for word in ("Edit", "Write", "NotebookEdit"):
        assert word in prof.disallowed_tools
    assert "Bash(* >*)" in prof.disallowed_tools
    joined = " ".join(prof.allowed_tools)
    for word in ("commit", "push", "rm ", "sed", "tee", "make"):
        assert word not in joined
