from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from ahawr_runner import config
from ahawr_runner.config import ROLES, ProviderProfile, Settings, _providers

PROVIDER = "CLAUDE_RUNNER_PROVIDER_CODEXLOCAL__"


def test_provider_without_engine_keeps_claude_settings() -> None:
    env = {f"{PROVIDER}ANTHROPIC_BASE_URL": "http://litellm:4000", f"{PROVIDER}TOOLS": "Bash Read"}
    assert _providers(env)["CODEXLOCAL"] == ProviderProfile(
        name="CODEXLOCAL",
        env={"ANTHROPIC_BASE_URL": "http://litellm:4000"},
        tools="Bash Read",
    )


def test_provider_engine_codex_parses_codex_fields() -> None:
    env = {
        f"{PROVIDER}ENGINE": "codex",
        f"{PROVIDER}CODEX_BASE_URL": "http://host.docker.internal:8033/v1",
        f"{PROVIDER}CODEX_API_KEY_VAR": "LLAMA_API_KEY",
        f"{PROVIDER}CODEX_STREAM_IDLE_TIMEOUT_MS": "120000",
        f"{PROVIDER}PRICE_USD_PER_MILLION_TOKENS": "2,10,0.2,4",
        f"{PROVIDER}OPENAI_API_KEY": "sk-fake",
    }
    profile = _providers(env)["CODEXLOCAL"]
    assert profile.engine == "codex"
    assert profile.codex_base_url == "http://host.docker.internal:8033/v1"
    assert profile.codex_api_key_var == "LLAMA_API_KEY"
    assert profile.codex_stream_idle_timeout_ms == 120000
    assert profile.price_usd_per_million_tokens == (2.0, 10.0, 0.2, 4.0)
    assert profile.env == {"OPENAI_API_KEY": "sk-fake"}


def test_provider_codex_fields_default_when_unset() -> None:
    profile = _providers({f"{PROVIDER}ENGINE": "codex"})["CODEXLOCAL"]
    assert profile.codex_base_url == "https://api.openai.com/v1"
    assert profile.codex_api_key_var == "OPENAI_API_KEY"
    assert profile.codex_stream_idle_timeout_ms == 300_000
    assert profile.price_usd_per_million_tokens is None


def test_provider_codex_empty_api_key_var_omits_env_key() -> None:
    env = {f"{PROVIDER}ENGINE": "codex", f"{PROVIDER}CODEX_API_KEY_VAR": ""}
    assert _providers(env)["CODEXLOCAL"].codex_api_key_var == ""


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("ENGINE", "gpt", "ENGINE must be one of claude, codex"),
        ("ENGINE", "Codex", "ENGINE must be one of claude, codex"),
        ("CODEX_BASE_URL", "localhost:8033/v1", "CODEX_BASE_URL must be an http"),
        ("CODEX_API_KEY_VAR", "bad-name", "CODEX_API_KEY_VAR must be an environment variable"),
        ("CODEX_STREAM_IDLE_TIMEOUT_MS", "0", "CODEX_STREAM_IDLE_TIMEOUT_MS must be a positive"),
        ("CODEX_STREAM_IDLE_TIMEOUT_MS", "5m", "CODEX_STREAM_IDLE_TIMEOUT_MS must be a positive"),
        ("PRICE_USD_PER_MILLION_TOKENS", "2,10,0.2", "PRICE_USD_PER_MILLION_TOKENS must be four"),
        ("PRICE_USD_PER_MILLION_TOKENS", "2,-1,0.2,4", "PRICE_USD_PER_MILLION_TOKENS must be four"),
    ],
)
def test_provider_invalid_engine_or_codex_field_raises(key: str, value: str, message: str) -> None:
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
    profile = _providers(env, hermes_engine_enabled=True)["CODEXLOCAL"]
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
    profile = _providers({f"{PROVIDER}ENGINE": "hermes"}, hermes_engine_enabled=True)["CODEXLOCAL"]
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
    assert _providers(env, hermes_engine_enabled=True)["CODEXLOCAL"].hermes_max_polls == 20


def test_hermes_engine_rejected_with_flag_off() -> None:
    # With the flag off, ENGINE=hermes raises exactly as any other unknown value.
    with pytest.raises(ValueError, match="ENGINE must be one of claude, codex"):
        _providers({f"{PROVIDER}ENGINE": "hermes"})
    with pytest.raises(ValueError, match="ENGINE must be one of claude, codex"):
        _providers({f"{PROVIDER}ENGINE": "gpt"})


def test_unknown_engine_rejected_even_with_hermes_flag_on() -> None:
    with pytest.raises(ValueError, match="ENGINE must be one of claude, codex, hermes"):
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
    parsed = Settings.from_env({f"{PROVIDER}ENGINE": "codex"})
    assert parsed.hermes_engine_enabled is False
    assert parsed.provider("codexlocal") == ProviderProfile(name="CODEXLOCAL", engine="codex")
    # The accepted ENGINE values and the error message stay as today while the flag is off.
    with pytest.raises(ValueError, match="ENGINE must be one of claude, codex"):
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


def test_flat_price_profiles_parse_pairs_and_default_to_none() -> None:
    assert Settings.from_env({}).flat_price_profiles == {}
    env = {"CLAUDE_RUNNER_FLAT_PRICE_PROFILES": "Strata=Sonnet, other=haiku;bad;=x"}
    assert Settings.from_env(env).flat_price_profiles == {"strata": "sonnet", "other": "haiku"}


# S2 characterization pins: the literals inline in config.py (report S2 worklist, ids
# a042-a048). Each runs with an empty env (no JSON settings file). The a040, a041 entries
# are the ENGINE=codex part of config.py: excluded, no test.


def test_compact_timeout_seconds_defaults_to_six_hundred() -> None:
    # a043: the inline default 600 of _int(..., "CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS", 600, 1).
    assert Settings.from_env({}).compact_timeout_seconds == 600


def test_compact_timeout_seconds_below_one_is_rejected() -> None:
    # a044: the `1` floor argument is a validation bound, not a settable value.
    with pytest.raises(ValueError, match="CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS must be >= 1"):
        Settings.from_env({"CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS": "0"})


def test_watchdog_interval_seconds_defaults_to_sixty() -> None:
    # a046: the inline default 60 of _int(..., "CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS", 60, 1).
    assert Settings.from_env({}).watchdog_interval_seconds == 60


def test_watchdog_interval_seconds_below_one_is_rejected() -> None:
    # a047: the `1` floor argument of the watchdog interval bound.
    with pytest.raises(ValueError, match="CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS must be >= 1"):
        Settings.from_env({"CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS": "0"})


def test_think_proxy_url_defaults_to_loopback_8700() -> None:
    # a045: the inline default "http://127.0.0.1:8700" when the env var is absent.
    assert Settings.from_env({}).think_proxy_url == "http://127.0.0.1:8700"


def test_hermes_derived_max_polls_floors_at_one() -> None:
    # a042: max(1, run_timeout_ms // poll_interval_ms) in _hermes_max_polls: the derived
    # poll count never drops below 1 when the run timeout is shorter than one poll.
    env = {
        f"{PROVIDER}ENGINE": "hermes",
        f"{PROVIDER}HERMES_RUN_TIMEOUT_MS": "1000",
        f"{PROVIDER}HERMES_POLL_INTERVAL_MS": "3000",
    }
    assert _providers(env, hermes_engine_enabled=True)["CODEXLOCAL"].hermes_max_polls == 1


def test_generic_profile_is_the_fallback_for_unknown_roles() -> None:
    # a048: the vocabulary token "generic" in ROLES and the profile() fallback key.
    assert "generic" in ROLES
    s = Settings.from_env({})
    assert s.profile("no-such-role") == s.profiles["generic"]


def test_ops_store_row_caps_default_to_the_rows_they_used_to_be() -> None:
    # a010-a025: the store defaults before the constants existed, kept byte-for-byte.
    assert config.OPS_COMMANDS_DEFAULT_ROWS == 200
    assert config.OPS_CHANGELOG_DEFAULT_ROWS == 200
    assert config.OPS_QUEUE_LOG_MAX_ROWS == 1000
    assert config.OPS_SNAPSHOT_MAX_ROWS == 100


def test_ops_store_row_caps_env_override_is_read_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_RUNNER_OPS_QUEUE_LOG_MAX_ROWS", "2500")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.OPS_QUEUE_LOG_MAX_ROWS == 2500
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_dashboard_defaults_match_the_literals_they_used_to_be() -> None:
    # a049-a055: the runs-list, search and dashboard CLI defaults before the settings existed,
    # kept byte-for-byte.
    assert config.DASHBOARD_PORT == 8701
    assert config.DASHBOARD_BIND_HOST == "0.0.0.0"
    assert config.RUNS_LIST_DEFAULT_ROWS == 100
    assert config.RUNS_LIST_MAX_ROWS == 500
    assert config.SEARCH_DEFAULT_ROWS == 50
    assert config.SEARCH_MAX_ROWS == 200


def test_dashboard_row_caps_env_override_is_read_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_RUNNER_SEARCH_MAX_ROWS", "300")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.SEARCH_MAX_ROWS == 300
    finally:
        monkeypatch.undo()
        importlib.reload(config)
