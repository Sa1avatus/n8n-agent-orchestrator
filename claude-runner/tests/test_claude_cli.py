from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_runner import mcp_search
from claude_runner.claude_cli import build_command, mcp_search_args
from claude_runner.config import Settings


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
