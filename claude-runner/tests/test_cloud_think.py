"""Tests for the cloud thinking mapping (claude_runner/cloud_think.py).

Only made-up policy text and values: a JSON policy with the two variants
plan+pulse and control, and the old pipe string of three variants.
"""

from __future__ import annotations

import json

import pytest

from claude_runner import cloud_think

# layout: plan+pulse, plan+pulse, control (weights 2 + 1)
SAMPLE = json.dumps(
    {
        "variants": [
            {
                "name": "plan+pulse",
                "weight": 2,
                "first": [["think", 8], ["off", 25], ["think", 10], ["off", None]],
                "retry": [["off", 25], ["think", 10], ["off", None]],
            },
            {
                "name": "control",
                "weight": 1,
                "first": [["off", None]],
                "retry": [["think", 3], ["off", None]],
            },
        ],
        "from_task": 1,
    }
)

PIPE = "off_first|off|pulse:25:10:1:f5"


def test_json_variants_first_attempt() -> None:
    first = cloud_think.resolve_cloud_think(SAMPLE, 1, False, "anthropic", True)
    assert first is not None
    # plan+pulse thinks on its first attempt: the variable stays unset.
    assert (first.variant, first.mode, first.args, first.env) == ("plan+pulse", "think", [], {})
    control = cloud_think.resolve_cloud_think(SAMPLE, 3, False, "anthropic", True)
    assert control is not None
    # control never thinks: both vars are set to the off values.
    assert (control.variant, control.mode) == ("control", "nothink")
    assert control.env == {"MAX_THINKING_TOKENS": "0", "CLAUDE_CODE_DISABLE_THINKING": "1"}


def test_json_variants_retry() -> None:
    retry = cloud_think.resolve_cloud_think(SAMPLE, 1, True, "anthropic", True)
    assert retry is not None
    # plan+pulse retry starts with the off window: the opposite of its first attempt.
    assert retry.variant == "plan+pulse" and retry.mode == "nothink"
    assert retry.env == {"MAX_THINKING_TOKENS": "0", "CLAUDE_CODE_DISABLE_THINKING": "1"}
    control = cloud_think.resolve_cloud_think(SAMPLE, 3, True, "anthropic", True)
    assert control is not None
    assert control.variant == "control" and control.mode == "think" and control.env == {}


def test_env_keys_are_the_allowed_subset() -> None:
    for task in (1, 2, 3, 4):
        for retry in (False, True):
            setting = cloud_think.resolve_cloud_think(SAMPLE, task, retry, "anthropic", True)
            assert setting is not None
            assert setting.args == []
            assert set(setting.env) <= set(cloud_think.CLOUD_THINK_ENV_VARS)


def test_pipe_variants_first_and_retry() -> None:
    off_first = cloud_think.resolve_cloud_think(PIPE, 1, False, "anthropic", True)
    assert off_first is not None
    # off_first: first attempt off, retry thinks.
    assert off_first.variant == "off_first" and off_first.mode == "nothink"
    off_first_retry = cloud_think.resolve_cloud_think(PIPE, 1, True, "anthropic", True)
    assert off_first_retry is not None and off_first_retry.mode == "think"
    off = cloud_think.resolve_cloud_think(PIPE, 2, False, "anthropic", True)
    assert off is not None and off.variant == "off" and off.mode == "nothink"
    pulse = cloud_think.resolve_cloud_think(PIPE, 3, False, "anthropic", True)
    assert pulse is not None
    # pulse variant: f5 makes the first attempt think; the retry has no r-window
    # and the first pulse cycle call is in the off window.
    assert pulse.variant == "pulse:25:10:1:f5" and pulse.mode == "think"
    pulse_retry = cloud_think.resolve_cloud_think(PIPE, 3, True, "anthropic", True)
    assert pulse_retry is not None and pulse_retry.mode == "nothink"


def test_disabled_and_local_providers_return_none() -> None:
    assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, "anthropic", False) is None
    assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, "local", True) is None
    assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, "Local", True) is None
    assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, "", True) is None


def test_unknown_provider_alias_returns_none() -> None:
    for provider in ("openai", "Grok", "ollama"):
        assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, provider, True) is None
    # "Anthropic" in any case is the known alias.
    assert cloud_think.resolve_cloud_think(SAMPLE, 1, False, "Anthropic", True) is not None


def test_empty_or_invalid_policy_returns_none() -> None:
    for text in ("", "   ", "{", "{nope", "{}", json.dumps({"variants": []})):
        assert cloud_think.resolve_cloud_think(text, 1, False, "anthropic", True) is None
    bad_mode = json.dumps({"variants": [{"name": "x", "first": [["maybe", 3]]}]})
    assert cloud_think.resolve_cloud_think(bad_mode, 1, False, "anthropic", True) is None


def test_same_inputs_give_the_same_result() -> None:
    cases = [
        (SAMPLE, 1, False, "anthropic", True),
        (SAMPLE, 3, True, "anthropic", True),
        (PIPE, 2, True, "anthropic", True),
        ("", 5, False, "anthropic", True),
    ]
    for args in cases:
        assert cloud_think.resolve_cloud_think(*args) == cloud_think.resolve_cloud_think(*args)


def test_build_command_and_child_env_apply_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from claude_runner.claude_cli import build_command, child_env
    from claude_runner.config import Settings

    for key in ("MAX_THINKING_TOKENS", "CLAUDE_CODE_DISABLE_THINKING"):
        monkeypatch.delenv(key, raising=False)

    # No setting (the default): the command and env are unchanged.
    s = Settings.from_env({})
    profile = s.profile("worker")
    cmd = build_command(s, profile, model="m", claude_session_id="id", resume=True)
    env = child_env(None, "m")
    assert "MAX_THINKING_TOKENS" not in env
    assert "CLAUDE_CODE_DISABLE_THINKING" not in env

    # Off mode (variant control, task 3, first attempt): both vars set to off.
    setting = cloud_think.resolve_cloud_think(SAMPLE, 3, False, "anthropic", True)
    assert setting is not None
    cmd = build_command(
        s, profile, model="m", claude_session_id="id", resume=True, cloud_think=setting
    )
    env = child_env(None, "m", cloud_think=setting)
    assert env["MAX_THINKING_TOKENS"] == "0"
    assert env["CLAUDE_CODE_DISABLE_THINKING"] == "1"
    # args stay empty, so the command is unchanged.
    assert cmd == build_command(s, profile, model="m", claude_session_id="id", resume=True)

    # Think mode (variant plan+pulse, task 1, first attempt): no vars set.
    setting = cloud_think.resolve_cloud_think(SAMPLE, 1, False, "anthropic", True)
    assert setting is not None and setting.mode == "think"
    env = child_env(None, "m", cloud_think=setting)
    assert "MAX_THINKING_TOKENS" not in env
    assert "CLAUDE_CODE_DISABLE_THINKING" not in env
