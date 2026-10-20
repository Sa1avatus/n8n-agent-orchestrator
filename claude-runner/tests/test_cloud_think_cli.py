"""Command, environment and whitelist tests of the cloud thinking CLI wiring.

Everything is built from pure ``build_command`` / ``child_env`` calls with a stub
CLI (``tests/fake_claude.py``): no network, no real Claude Code run.  The stub is
launched exactly the way the runner launches the CLI, so its call log records the
child process's own ``os.environ`` — the env the runner passed, observed from inside
the process, not a copy of the parent's.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

from claude_runner import cloud_think
from claude_runner.claude_cli import CLOUD_THINK_ENV_VARS, build_command, child_env
from claude_runner.config import Settings

from .conftest import calls

# Made-up policy text: task 1 -> plan+pulse (thinks on the first attempt, off on a
# retry); task 3 -> control (off on the first attempt, thinks on a retry).
SAMPLE = (
    '{"variants":[{"name":"plan+pulse","weight":2,'
    '"first":[["think", 8], ["off", 25], ["think", 10], ["off", null]],'
    '"retry":[["off", 25], ["think", 10], ["off", null]]},'
    '{"name":"control","weight":1,"first":[["off", null]],"retry":[["think", 3], ["off", null]]}],'
    '"from_task":1}'
)


def _settings() -> Settings:
    return Settings.from_env({})


def _cmd(
    s: Settings,
    model: str,
    session: str,
    setting: cloud_think.CloudThinkSetting | None,
) -> list[str]:
    return build_command(
        s,
        s.profile("worker"),
        model=model,
        claude_session_id=session,
        resume=False,
        cloud_think=setting,
    )


def test_anthropic_on_sets_env_and_keeps_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CLOUD_THINK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    s = _settings()
    # First attempt (task 1, plan+pulse): think mode leaves both vars unset.
    first = cloud_think.resolve_cloud_think(SAMPLE, 1, False, "anthropic", True)
    assert first is not None and first.mode == "think" and first.env == {}
    base = _cmd(s, "m", "id", None)
    cmd = _cmd(s, "m", "id", first)
    env = child_env(None, "m", cloud_think=first)
    # No CLI flag switches thinking, so the command is unchanged for every attempt.
    assert cmd == base
    assert "MAX_THINKING_TOKENS" not in env
    assert "CLAUDE_CODE_DISABLE_THINKING" not in env

    # Retry (task 1, plan+pulse): the first window of a retry is off.
    retry = cloud_think.resolve_cloud_think(SAMPLE, 1, True, "anthropic", True)
    assert retry is not None and retry.mode == "nothink"
    cmd = _cmd(s, "m", "id", retry)
    env = child_env(None, "m", cloud_think=retry)
    assert cmd == base
    assert env["MAX_THINKING_TOKENS"] == "0"
    assert env["CLAUDE_CODE_DISABLE_THINKING"] == "1"


def test_setting_off_gives_the_previous_command_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CLOUD_THINK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    s = _settings()
    base = _cmd(s, "m", "id", None)
    env = child_env(None, "m")
    # Off (the setting disabled): the mapping is None and both command and env are
    # the same as before the setting existed.
    off = cloud_think.resolve_cloud_think(SAMPLE, 3, False, "anthropic", False)
    assert off is None
    assert _cmd(s, "m", "id", off) == base
    env2 = child_env(None, "m", cloud_think=off)
    assert env2 == env
    assert "MAX_THINKING_TOKENS" not in env2
    assert "CLAUDE_CODE_DISABLE_THINKING" not in env2


def test_local_provider_gives_no_change(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in CLOUD_THINK_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    # The local provider is never remapped: the mapping stays None.
    local = cloud_think.resolve_cloud_think(SAMPLE, 1, False, "local", True)
    assert local is None
    s = _settings()
    base = _cmd(s, "m", "id", None)
    assert _cmd(s, "m", "id", local) == base
    env = child_env(None, "m", cloud_think=local)
    assert env == child_env(None, "m")
    assert "MAX_THINKING_TOKENS" not in env
    assert "CLAUDE_CODE_DISABLE_THINKING" not in env


def test_whitelist_only_whitelisted_names_reach_the_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_bin: str
) -> None:
    # The parent env carries made-up extras (secrets and runner settings) that a
    # leak would show up in the child process.
    monkeypatch.setenv("FAKE_SECRET", "x")
    monkeypatch.setenv("FAKE_TOKEN", "y")
    monkeypatch.setenv("FAKE_SECONDARY", "w")
    monkeypatch.setenv("CLAUDE_RUNNER_API_KEY", "z")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "parent-model")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "parent-subagent")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "parent-think")
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_THINKING", "parent-disable")
    s = _settings()
    # Off-mode setting (task 3, control): the two allowed vars take the run's values.
    setting = cloud_think.resolve_cloud_think(SAMPLE, 3, False, "anthropic", True)
    assert setting is not None and setting.mode == "nothink"
    # --session-id with a fresh UUID, like the runner for a new run.
    session = str(uuid.uuid4())
    cmd = _cmd(s, "m", session, setting)
    env = child_env(None, "m", cloud_think=setting)

    # The whitelist is the container env minus the runner's own ``CLAUDE_RUNNER_*``
    # settings, with the run's thinking-mode vars on top.  Nothing else is added or
    # inherited beyond that, so nothing else may reach the child env.
    expected = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_RUNNER_")}
    for key in CLOUD_THINK_ENV_VARS:
        if key in setting.env:
            expected[key] = setting.env[key]
    expected.setdefault("DISABLE_AUTOUPDATER", "1")
    expected.setdefault("CLAUDE_CODE_RESUME_INTERRUPTED_TURN", "1")
    assert env == expected
    # The runner's own settings never leak into the child env.
    assert not any(key.startswith("CLAUDE_RUNNER_") for key in env)
    assert "CLAUDE_RUNNER_API_KEY" not in env
    # The made-up non-runner extras are part of the container env, so they are
    # whitelisted and do reach the child (they are not "leaked" by the runner).
    assert env["FAKE_SECRET"] == "x"
    assert env["FAKE_TOKEN"] == "y"
    assert env["FAKE_SECONDARY"] == "w"
    # The whitelisted credential is present with the container value; the secondary
    # model names are inherited as-is (provider None does not remap them).
    assert env["ANTHROPIC_API_KEY"] == "k"
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "parent-model"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL"] == "parent-subagent"
    # The thinking-mode vars take the run's values, not the parent's.
    assert env["MAX_THINKING_TOKENS"] == "0"
    assert env["CLAUDE_CODE_DISABLE_THINKING"] == "1"
    assert env["DISABLE_AUTOUPDATER"] == "1"

    # The stub CLI records the child process's own env (not the parent's) in the
    # call log; launching it the same way the runner does makes the recorded env the
    # child env the runner passed.
    result = subprocess.run(
        [fake_bin, *cmd[1:]],
        cwd=tmp_path,
        env=env,
        input="go",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    records = calls(tmp_path)
    assert len(records) == 1
    seen = records[0]["env"]
    # The whitelisted names the stub observes carry the run's values, and no runner
    # setting is among them.
    assert seen["ANTHROPIC_API_KEY"] == "k"
    assert seen["CLAUDE_CODE_DISABLE_THINKING"] == "1"
    assert not any(key.startswith("CLAUDE_RUNNER_") for key in seen)
    secondary = records[0]["secondary"]
    assert secondary["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "parent-model"
    assert secondary["CLAUDE_CODE_SUBAGENT_MODEL"] == "parent-subagent"
