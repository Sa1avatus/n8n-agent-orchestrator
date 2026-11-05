from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import mcp_search
from ahawr_runner.board_engine import RECOMMENDED_ERROR_RULES
from ahawr_runner.claude_cli import (
    Outcome,
    StreamState,
    build_command,
    classify,
    mcp_search_args,
)
from ahawr_runner.config import Settings

POOLSIDE_400_LINE = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 400,
        "result": (
            "API Error: 400 poolside/laguna-s-2.1:free is no longer available on OpenRouter: "
            "no provider serves this model anymore"
        ),
    }
)


def _classify_line(line: str, error_rules: list[dict[str, Any]] | None) -> Outcome:
    state = StreamState()
    state.feed(line)
    return classify(state, 1, "", error_rules=error_rules)


def test_classify_recommended_rules_fail_over_poolside_400() -> None:
    outcome = _classify_line(POOLSIDE_400_LINE, RECOMMENDED_ERROR_RULES)
    assert outcome.error_code == "api_error" and outcome.http_code is None
    assert outcome.details["error_rule"] == {"id": "model-gone", "action": "failover", "retries": 0}


def test_classify_empty_rule_list_uses_legacy_rules() -> None:
    outcome = _classify_line(POOLSIDE_400_LINE, [])
    assert outcome.error_code == "api_error" and outcome.http_code is None
    assert outcome.details["error_rule"]["id"] == "legacy-transient-http"
    assert outcome.details["error_rule"]["action"] == "retry"


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def settings(config_home: Path) -> Settings:
    return Settings.from_env(
        {
            "CLAUDE_RUNNER_MCP_SEARCH": "1",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-fake",
            "CLAUDE_RUNNER_PROVIDER_OTHER__ANTHROPIC_BASE_URL": "http://other:4000",
            "CLAUDE_RUNNER_PROVIDER_OTHER__ANTHROPIC_AUTH_TOKEN": "sk-fake-other",
        }
    )


def _command(settings: Settings, role: str, provider_name: str) -> list[str]:
    return build_command(
        settings,
        settings.profile(role),
        model="m",
        claude_session_id="id",
        resume=False,
        provider=settings.provider(provider_name),
        role=role,
    )


def test_flag_off_leaves_arguments_unchanged(config_home: Path) -> None:
    settings = Settings.from_env(
        {
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-fake",
        }
    )
    worker = _command(settings, "worker", "local")
    assert "--mcp-config" not in worker
    assert not (config_home / "mcp-search.json").exists()


def test_flag_on_local_worker_writes_mcp_config(settings: Settings, config_home: Path) -> None:
    cmd = _command(settings, "worker", "local")
    path = Path(cmd[cmd.index("--mcp-config") + 1])
    assert path == config_home / "mcp-search.json"
    payload = json.loads(path.read_text("utf-8"))
    assert payload == {"mcpServers": {"ahawr": {"command": "ahawr-mcp-search", "args": []}}}
    server_key = next(iter(payload["mcpServers"]))
    assert f"mcp__{server_key}__{mcp_search.TOOL_NAME}" == "mcp__ahawr__ahawr_search"


def test_flag_on_other_provider_gets_no_mcp_config(settings: Settings, config_home: Path) -> None:
    assert mcp_search_args(settings, "worker", settings.provider("other")) == []
    assert mcp_search_args(settings, "worker", None) == []
    assert not (config_home / "mcp-search.json").exists()


def test_flag_on_reviewer_gets_no_mcp_config(settings: Settings, config_home: Path) -> None:
    assert mcp_search_args(settings, "reviewer", settings.provider("local")) == []
    assert not (config_home / "mcp-search.json").exists()


def test_qa_agent_cli_args_do_not_allow_write_edit_or_bash(config_home: Path) -> None:
    settings = Settings.from_env(
        {
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
            "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-fake",
        }
    )
    cmd = _command(settings, "qa_agent", "local")
    # The tools set is exactly Read, Glob, Grep; nothing else is offered.
    assert cmd[cmd.index("--tools") + 1] == "Read,Glob,Grep"
    # Write, Edit and Bash are not in the tools list, so they are not allowed.
    tools = cmd[cmd.index("--tools") + 1].split(",")
    for forbidden in ("Write", "Edit", "Bash"):
        assert forbidden not in tools
    # No --allowedTools flag (allowed_tools is empty).
    assert "--allowedTools" not in cmd


def test_classify_adds_up_every_result_of_the_run() -> None:
    # background-task notifications resume the process: each result reports its own turns and tokens
    first = {
        "type": "result",
        "subtype": "success",
        "num_turns": 67,
        "duration_api_ms": 100,
        "usage": {
            "input_tokens": 49,
            "output_tokens": 27367,
            "cache_read_input_tokens": 3289663,
            "output_tokens_details": {"thinking_tokens": 0},
        },
    }
    second = {
        "type": "result",
        "subtype": "success",
        "num_turns": 1,
        "duration_api_ms": 130,
        "usage": {"input_tokens": 1, "output_tokens": 226, "cache_read_input_tokens": 90956},
    }
    state = StreamState()
    for event in (first, second):
        state.feed(json.dumps(event))
    outcome = classify(state, 0, "")
    assert outcome.details["num_turns"] == 68
    assert outcome.details["usage"]["output_tokens"] == 27593
    assert outcome.details["usage"]["cache_read_input_tokens"] == 3380619
    assert outcome.details["duration_api_ms"] == 130  # already the session's running total
