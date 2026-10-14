"""Local runs priced as a cloud model by the thinking mode of each step (claude_runner/local_pricing.py)."""
import json

import pytest

from claude_runner.local_pricing import LocalPricing, parse_rates

ENV = {
    "CLAUDE_RUNNER_LOCAL_PRICE_SONNET": "2,10,0.2,4",
    "CLAUDE_RUNNER_LOCAL_PRICE_HAIKU": "1,5,0.1,1.25",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_OFF": "haiku",
    "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON": "sonnet",
}


def step(started, inp=0, out=0, cr=0, cw=0):
    return {"kind": "step", "step": 1, "t": started + 5, "started": started, "input_tokens": inp, "output_tokens": out,
            "cache_read_tokens": cr, "cache_write_tokens": cw}


def write(tmp_path, entries):
    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def test_parse_rates():
    assert parse_rates("2,10,0.2,4") == (2.0, 10.0, 0.2, 4.0)
    assert parse_rates(" 1 5 0.1 1.25 ") == (1.0, 5.0, 0.1, 1.25)
    assert parse_rates("2,10") is None
    assert parse_rates("a,b,c,d") is None
    assert parse_rates("-1,2,3,4") is None


def test_from_env_profiles_and_modes():
    p = LocalPricing.from_env(ENV)
    assert p.enabled
    assert p.profiles["sonnet"] == (2.0, 10.0, 0.2, 4.0)
    assert p.mode_profile == {"off": "haiku", "on": "sonnet"}


def test_disabled_without_modes_or_with_unknown_profile():
    assert not LocalPricing.from_env({}).enabled
    only_profiles = {k: v for k, v in ENV.items() if "THINKING" not in k}
    assert not LocalPricing.from_env(only_profiles).enabled
    assert not LocalPricing.from_env({**ENV, "CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON": "gpt"}).enabled


def test_one_mode_named_prices_both():
    env = {k: v for k, v in ENV.items() if not k.endswith("THINKING_ON")}
    p = LocalPricing.from_env(env)
    assert p.enabled and p.mode_profile == {"off": "haiku", "on": "haiku"}


def test_steps_are_priced_by_the_mode_in_force(tmp_path):
    p = LocalPricing.from_env(ENV)
    path = write(tmp_path, [
        {"kind": "thinking_state", "t": 100, "thinking": "off"},
        step(110, inp=1_000_000, out=100_000),                      # off -> haiku: 1 + 0.5
        {"kind": "thinking_switch", "t": 200, "thinking": "on"},
        step(210, inp=1_000_000, out=100_000, cr=500_000, cw=250_000),  # on -> sonnet: 2 + 1 + 0.1 + 1
    ])
    result = p.cost_from_events(path)
    assert result["cost_usd"] == pytest.approx(1.5 + 4.1)
    assert result["by_mode"]["off"] == {"steps": 1, "usd": pytest.approx(1.5), "profile": "haiku"}
    assert result["by_mode"]["on"]["steps"] == 1
    # the same tokens priced as a whole at one profile, for the Sonnet / Haiku comparison
    assert result["by_profile"]["sonnet"] == pytest.approx(2 + 1 + (2 + 1 + 0.1 + 1))
    assert result["by_profile"]["haiku"] == pytest.approx(1.5 + (1 + 0.5 + 0.05 + 0.3125))


def test_a_step_that_started_before_the_switch_keeps_the_old_mode(tmp_path):
    p = LocalPricing.from_env(ENV)
    path = write(tmp_path, [
        {"kind": "thinking_state", "t": 100, "thinking": "off"},
        {"kind": "thinking_switch", "t": 150, "thinking": "on"},
        step(140, out=1_000_000),  # started at 140, the switch came at 150 while it ran: still off
        step(160, out=1_000_000),
    ])
    result = p.cost_from_events(path)
    assert result["by_mode"]["off"]["steps"] == 1 and result["by_mode"]["on"]["steps"] == 1
    assert result["cost_usd"] == pytest.approx(5 + 10)


def test_no_state_entry_means_thinking_on(tmp_path):
    p = LocalPricing.from_env(ENV)
    result = p.cost_from_events(write(tmp_path, [step(10, out=1_000_000)]))
    assert result["by_mode"]["on"]["steps"] == 1
    assert result["cost_usd"] == pytest.approx(10)


def test_no_steps_missing_file_or_disabled_gives_none(tmp_path):
    p = LocalPricing.from_env(ENV)
    assert p.cost_from_events(write(tmp_path, [{"kind": "text", "t": 1}])) is None
    assert p.cost_from_events(tmp_path / "missing.jsonl") is None
    assert LocalPricing.from_env({}).cost_from_events(write(tmp_path, [step(1, out=5)])) is None
