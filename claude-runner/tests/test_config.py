from __future__ import annotations

from pathlib import Path

import pytest

from claude_runner.config import ROLES, ProviderProfile, Settings, _providers

PROVIDER = "CLAUDE_RUNNER_PROVIDER_ENGINELOCAL__"


def test_provider_without_engine_keeps_claude_settings() -> None:
    env = {f"{PROVIDER}ANTHROPIC_BASE_URL": "http://litellm:4000", f"{PROVIDER}TOOLS": "Bash Read"}
    assert _providers(env)["ENGINELOCAL"] == ProviderProfile(
        name="ENGINELOCAL",
        env={"ANTHROPIC_BASE_URL": "http://litellm:4000"},
        tools="Bash Read",
    )


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("ENGINE", "gpt", "ENGINE must be one of claude"),
        ("ENGINE", "CLAUDE", "ENGINE must be one of claude"),
    ],
)
def test_provider_invalid_engine_raises(key: str, value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _providers({f"{PROVIDER}{key}": value})


def test_hermes_engine_accepted_with_flag_on() -> None:
    env = {
        f"{PROVIDER}ENGINE": "hermes",
        f"{PROVIDER}HERMES_BASE_URL_VAR": "HERMES_URL_ONE",
        f"{PROVIDER}HERMES_API_KEY_VAR": "HERMES_KEY_ONE",
        f"{PROVIDER}HERMES_START_TIMEOUT_MS": "4500",
        f"{PROVIDER}HERMES_POLL_INTERVAL_MS": "300",
        f"{PROVIDER}HERMES_RUN_TIMEOUT_MS": "6000",
        f"{PROVIDER}HERMES_MAX_POLLS": "9",
    }
    profile = _providers(env, hermes_engine_enabled=True)["ENGINELOCAL"]
    assert profile.engine == "hermes"
    # The field holds the NAME of the environment variable, never its value, and the block
    # keys do not land in the provider env.
    assert profile.hermes_base_url_var == "HERMES_URL_ONE"
    assert profile.hermes_api_key_var == "HERMES_KEY_ONE"
    assert profile.hermes_start_timeout_ms == 4500
    assert profile.hermes_poll_interval_ms == 300
    assert profile.hermes_run_timeout_ms == 6000
    assert profile.hermes_max_polls == 9  # explicit beats the derived value
    assert profile.env == {}


def test_hermes_keys_default_when_unset_and_max_polls_derived() -> None:
    profile = _providers({f"{PROVIDER}ENGINE": "hermes"}, hermes_engine_enabled=True)["ENGINELOCAL"]
    assert profile.hermes_base_url_var == "HERMES_API_URL"
    assert profile.hermes_api_key_var == "API_SERVER_KEY"
    assert profile.hermes_start_timeout_ms == 10000
    assert profile.hermes_poll_interval_ms == 3000
    assert profile.hermes_run_timeout_ms == 3600000
    # Derived from the default timeouts when the block does not name HERMES_MAX_POLLS.
    assert profile.hermes_max_polls == 1200


def test_hermes_derived_max_polls_uses_the_parsed_timeouts() -> None:
    env = {
        f"{PROVIDER}ENGINE": "hermes",
        f"{PROVIDER}HERMES_POLL_INTERVAL_MS": "300",
        f"{PROVIDER}HERMES_RUN_TIMEOUT_MS": "6000",
    }
    assert _providers(env, hermes_engine_enabled=True)["ENGINELOCAL"].hermes_max_polls == 20


def test_hermes_engine_rejected_with_flag_off() -> None:
    # With the flag off, ENGINE=hermes raises exactly as any other unknown value.
    with pytest.raises(ValueError, match="ENGINE must be one of claude"):
        _providers({f"{PROVIDER}ENGINE": "hermes"})
    with pytest.raises(ValueError, match="ENGINE must be one of claude"):
        _providers({f"{PROVIDER}ENGINE": "gpt"})


def test_unknown_engine_rejected_even_with_hermes_flag_on() -> None:
    with pytest.raises(ValueError, match="ENGINE must be one of claude, hermes"):
        _providers({f"{PROVIDER}ENGINE": "gpt"}, hermes_engine_enabled=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("HERMES_START_TIMEOUT_MS", "0"),
        ("HERMES_START_TIMEOUT_MS", "5m"),
        ("HERMES_POLL_INTERVAL_MS", "1.5"),
        ("HERMES_RUN_TIMEOUT_MS", "abc"),
        ("HERMES_MAX_POLLS", "0"),
        ("HERMES_BASE_URL_VAR", "bad-name"),
        ("HERMES_API_KEY_VAR", "bad name"),
    ],
)
def test_hermes_invalid_values_raise(key: str, value: str) -> None:
    env = {f"{PROVIDER}ENGINE": "hermes", f"{PROVIDER}{key}": value}
    with pytest.raises(ValueError, match=f"{key} must be"):
        _providers(env, hermes_engine_enabled=True)


def test_hermes_engine_flag_defaults_off_and_leaves_the_config_unchanged() -> None:
    # Off by default (docs/naming.md: a flag reads as a statement, `enabled`). Read the
    # same way as the other flags: "1" turns it on.
    assert Settings.from_env({}).hermes_engine_enabled is False
    on = Settings.from_env({"CLAUDE_RUNNER_HERMES_ENGINE_ENABLED": "1"})
    assert on.hermes_engine_enabled is True
    # With the flag absent, the existing provider fixture parses exactly as before.
    parsed = Settings.from_env({f"{PROVIDER}ENGINE": "claude"})
    assert parsed.hermes_engine_enabled is False
    assert parsed.provider("enginelocal") == ProviderProfile(name="ENGINELOCAL", engine="claude")
    # The accepted ENGINE values and the error message stay as today while the flag is off.
    with pytest.raises(ValueError, match="ENGINE must be one of claude"):
        Settings.from_env({f"{PROVIDER}ENGINE": "hermes"})


def test_qa_complexity_keys_default_off_with_named_thresholds() -> None:
    # Off by default, like the other gate flags; the thresholds carry their units.
    s = Settings.from_env({})
    assert s.qa_complexity_enabled is False
    assert s.qa_complexity_max_complexity == 10
    assert s.qa_complexity_max_length_lines == 40
    assert s.qa_complexity_max_nesting_depth == 3
    assert s.qa_complexity_baseline_path is None


def test_qa_complexity_env_sets_flag_thresholds_and_baseline_path() -> None:
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_QA_COMPLEXITY_ENABLED": "1",
            "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_COMPLEXITY": "7",
            "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_LENGTH_LINES": "50",
            "CLAUDE_RUNNER_QA_COMPLEXITY_MAX_NESTING_DEPTH": "2",
            "CLAUDE_RUNNER_QA_COMPLEXITY_BASELINE_PATH": "/data/qa_baseline.json",
        }
    )
    assert s.qa_complexity_enabled is True
    assert s.qa_complexity_max_complexity == 7
    assert s.qa_complexity_max_length_lines == 50
    assert s.qa_complexity_max_nesting_depth == 2
    assert s.qa_complexity_baseline_path == Path("/data/qa_baseline.json")


def test_qa_agent_profile_has_read_only_tools() -> None:
    s = Settings.from_env({})
    # qa_agent is a runner role with its own profile.
    assert "qa_agent" in ROLES
    prof = s.profile("qa_agent")
    assert prof.permission_mode == "dontAsk"
    # The tool set is restricted to Read, Glob and Grep; nothing else is offered.
    assert prof.tools == "Read,Glob,Grep"
    # No write tools are allowed; they are denied explicitly.
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert tool in prof.disallowed_tools
    # No allowed_tools (the --allowedTools flag is not set); the tools set does the work.
    assert prof.allowed_tools == []
    # The captures live in /qa-runs, outside the working directory: the agent may read there.
    assert prof.add_dirs == ["/qa-runs"]
    assert s.profile("worker").add_dirs == []
    # The Worker and Reviewer profiles are unchanged (pinned).
    worker = s.profile("worker")
    assert worker.permission_mode == "bypassPermissions"
    assert worker.allowed_tools == []
    assert worker.disallowed_tools == Settings.from_env({}).profile("worker").disallowed_tools
    assert "Read(./.env)" in worker.disallowed_tools
    reviewer = s.profile("reviewer")
    assert reviewer.permission_mode == "dontAsk"
    assert "Bash(ahawr-search *)" in reviewer.allowed_tools
    assert "Edit" in reviewer.disallowed_tools
    # the Reviewer reads a line range itself; an in-place sed edit is not on its list
    assert "Bash(sed -n *)" in reviewer.allowed_tools
    assert "Bash(sed *)" not in reviewer.allowed_tools
