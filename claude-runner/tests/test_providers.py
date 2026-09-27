from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings, provider_key

from .conftest import calls, wait_for

LOCAL = {
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-litellm",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_DEFAULT_HAIKU_MODEL": "local-coder",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_DISABLE_THINKING": "1",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__TOOLS": "Bash,Read,Edit",
    "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_TOKENS": "1500",
}


@pytest.fixture
def mixed(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Architect/Reviewer on the Anthropic subscription, Worker on llama.cpp via LiteLLM."""
    for key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-subscription")
    env = {k: str(v) for k, v in LOCAL.items()}
    providers = Settings.from_env(env).providers
    object.__setattr__(settings, "providers", providers)
    object.__setattr__(settings, "compact_min_tokens", 999_999)
    return settings


def run(client: TestClient, **body: object) -> dict[str, object]:
    started = client.post("/v1/runs", json={"input": "task", "model": "m", **body}).json()
    return wait_for(client, started["run_id"])


def test_provider_blocks_and_names() -> None:
    s = Settings.from_env(
        {
            **LOCAL,
            "CLAUDE_RUNNER_PROVIDER_CUSTOM_LLAMA__ANTHROPIC_BASE_URL": "http://x",
            "CLAUDE_RUNNER_PROVIDER_BROKEN": "ignored (no double underscore)",
        }
    )
    local = s.provider("local")
    assert local is not None and local.tools == "Bash,Read,Edit"
    assert local.compact_min_tokens == 1500 and "TOOLS" not in local.env
    assert local.isolates_credentials
    assert s.provider("custom:llama") is not None  # Hermes-style provider names work
    assert s.provider("anthropic") is None and s.provider("") is None
    assert provider_key(" llama-local ") == "LLAMA_LOCAL"


def test_roles_use_their_own_backend_and_credentials(mixed: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(mixed)) as client:
        health = client.get("/health").json()["providers"]
        assert health["LOCAL"]["credential"] == "ANTHROPIC_AUTH_TOKEN"
        assert "sk-litellm" not in str(health)
        assert run(client, role="architect", provider="anthropic", model="opus")["status"] == (
            "completed"
        )
        assert run(client, role="worker", provider="local", model="local-coder")["status"] == (
            "completed"
        )
        assert run(client, role="reviewer", provider="anthropic", model="opus")["status"] == (
            "completed"
        )
    architect, worker, reviewer = calls(tmp_path)
    for claude in (architect, reviewer):
        assert claude["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-subscription"}
        assert "--tools" not in claude["args"]
    # The local Worker talks to LiteLLM and never sees the subscription token.
    assert worker["env"] == {
        "ANTHROPIC_BASE_URL": "http://litellm:4000",
        "ANTHROPIC_AUTH_TOKEN": "sk-litellm",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "local-coder",
        "CLAUDE_CODE_DISABLE_THINKING": "1",
    }
    # secondary model names follow the run's model (hermes_config *_model)
    assert worker["secondary"] == {
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "local-coder",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "local-coder",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "local-coder",
        "CLAUDE_CODE_SUBAGENT_MODEL": "local-coder",
    }
    for claude in (architect, reviewer):
        assert claude["secondary"] == {}
    assert worker["args"][worker["args"].index("--tools") + 1] == "Bash,Read,Edit"


def test_compaction_uses_the_session_provider(mixed: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(mixed)) as client:
        first = run(client, role="worker", provider="local", model="local-coder")
        sid = first["session_id"]
        run(client, role="worker", provider="local", model="local-coder", session_id=sid)
        # 2015 context tokens: above the local provider's 1500, below the global threshold
        done = client.post(f"/v1/sessions/{sid}/compact", json={}).json()
        assert done["status"] == "completed"
    compact = calls(tmp_path)[-1]
    assert compact["prompt"].strip() == "/compact"
    assert compact["env"]["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in compact["env"]
    assert compact["args"][compact["args"].index("--model") + 1] == "local-coder"


def test_unknown_provider_falls_back_to_container_env(mixed: Settings, tmp_path: Path) -> None:
    with TestClient(create_app(mixed)) as client:
        assert run(client, role="worker", provider="custom:openrouter")["status"] == "completed"
    assert calls(tmp_path)[-1]["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-subscription"}


def test_local_model_name_comes_only_from_the_run(mixed: Settings, tmp_path: Path) -> None:
    """No ANTHROPIC_DEFAULT_* in the provider block: every model name is the role's *_model."""
    block = {k: v for k, v in mixed.providers["LOCAL"].env.items() if "MODEL" not in k}
    object.__setattr__(mixed.providers["LOCAL"], "env", block)
    with TestClient(create_app(mixed)) as client:
        run(client, role="worker", provider="local", model="qwen3.8-27b-gsq-rco-iq2-s-mtp")
    secondary = calls(tmp_path)[-1]["secondary"]
    assert set(secondary.values()) == {"qwen3.8-27b-gsq-rco-iq2-s-mtp"}
    assert len(secondary) == 4
