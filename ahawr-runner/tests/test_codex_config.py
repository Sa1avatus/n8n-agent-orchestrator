from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner.codex_config import (
    CODEX_HOME_DIR_NAME,
    DENY_ENV_FILES,
    GLOB_SCAN_MAX_DEPTH,
    PERMISSIONS_PROFILE,
    REQUEST_MAX_RETRIES,
    TOOL_OUTPUT_TOKEN_LIMIT,
    render_config_toml,
    write_codex_home,
)
from ahawr_runner.config import ProviderProfile
from ahawr_runner.context_probe import autocompact_trigger

WINDOW = 65536
WORKSPACE = 'D:\\OpenAIProjects\\mission "one"'


def _openai() -> ProviderProfile:
    return ProviderProfile(
        name="openai",
        codex_base_url="https://api.openai.com/v1",
        codex_api_key_var="OPENAI_API_KEY",
        codex_stream_idle_timeout_ms=300_000,
    )


def _local() -> ProviderProfile:
    return ProviderProfile(
        name="llamacpp",
        codex_base_url="http://host.docker.internal:8033/v1",
        codex_api_key_var="",
        codex_stream_idle_timeout_ms=300_000,
    )


def _parse(provider: ProviderProfile, role: str = "worker", window: int | None = None) -> Any:
    text = render_config_toml(provider, role, "gpt-test", WORKSPACE, window)
    return tomllib.loads(text)


def test_openai_provider_keys() -> None:
    cfg = _parse(_openai())

    assert cfg["model"] == "gpt-test"
    assert cfg["model_provider"] == "openai"
    assert cfg["approval_policy"] == "never"
    assert cfg["tool_output_token_limit"] == TOOL_OUTPUT_TOKEN_LIMIT
    assert cfg["compact_prompt"].startswith("Summarize the session")
    assert cfg["default_permissions"] == PERMISSIONS_PROFILE
    assert "model_context_window" not in cfg
    assert "model_auto_compact_token_limit" not in cfg
    assert cfg["model_providers"]["openai"] == {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "wire_api": "responses",
        "env_key": "OPENAI_API_KEY",
        "request_max_retries": REQUEST_MAX_RETRIES,
        "stream_idle_timeout_ms": 300_000,
    }


def test_local_llama_server_keys() -> None:
    provider = _local()
    cfg = _parse(provider, window=WINDOW)

    assert cfg["model_provider"] == "llamacpp"
    assert cfg["model_context_window"] == WINDOW
    assert cfg["model_auto_compact_token_limit"] == autocompact_trigger(WINDOW, provider)
    table = cfg["model_providers"]["llamacpp"]
    assert table["base_url"] == "http://host.docker.internal:8033/v1"
    assert table["wire_api"] == "responses"
    assert "env_key" not in table


@pytest.mark.parametrize(
    ("role", "sandbox"),
    [("worker", "workspace-write"), ("reviewer", "read-only"), ("architect", "read-only")],
)
def test_sandbox_per_role(role: str, sandbox: str) -> None:
    cfg = _parse(_openai(), role=role)

    assert cfg["sandbox_mode"] == sandbox
    if role == "worker":
        roots = cfg["sandbox_workspace_write"]["writable_roots"]
        assert roots == [WORKSPACE]
        assert cfg["sandbox_workspace_write"]["network_access"] is False
    else:
        assert "sandbox_workspace_write" not in cfg


@pytest.mark.parametrize(("role", "project"), [("worker", "write"), ("reviewer", "read")])
def test_env_files_denied_under_the_workspace(role: str, project: str) -> None:
    fs = _parse(_openai(), role=role)["permissions"][PERMISSIONS_PROFILE]["filesystem"]

    assert fs[":root"] == "read"
    assert fs["glob_scan_max_depth"] == GLOB_SCAN_MAX_DEPTH
    assert fs[":project_roots"] == project
    for pattern in DENY_ENV_FILES:
        assert fs[f"{WORKSPACE}/{pattern}"] == "none"
    assert not any(key.endswith(".env.example") for key in fs)


def test_shell_environment_excludes_secrets() -> None:
    policy = _parse(_openai())["shell_environment_policy"]

    assert policy["inherit"] == "core"
    assert {"*KEY*", "*TOKEN*", "*SECRET*"} <= set(policy["exclude"])


def test_no_secret_value_in_output() -> None:
    provider = ProviderProfile(
        name="openai",
        env={"OPENAI_API_KEY": "sk-test-secret-value"},
        codex_api_key_var="OPENAI_API_KEY",
    )
    text = render_config_toml(provider, "worker", "gpt-test", WORKSPACE, None)

    assert "sk-test-secret-value" not in text
    assert 'env_key = "OPENAI_API_KEY"' in text


def test_write_codex_home_creates_directory(tmp_path: Path) -> None:
    text = render_config_toml(_openai(), "reviewer", "gpt-test", WORKSPACE, None)

    home = write_codex_home(tmp_path / "run-1", text)

    assert home == tmp_path / "run-1" / CODEX_HOME_DIR_NAME
    assert (home / "config.toml").read_text(encoding="utf-8") == text


def test_trim_tools_disables_web_search_and_unused_features() -> None:
    text = render_config_toml(_openai(), "worker", "m", "/w", None, trim_tools=True)
    assert text.index('web_search = "disabled"') < text.index("[permissions.")
    assert "[features]\nmulti_agent = false" in text
    tomllib.loads(text)
    assert "[features]" not in render_config_toml(_openai(), "worker", "m", "/w", None)
