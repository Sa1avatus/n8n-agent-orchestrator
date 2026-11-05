"""Thinking profiles (ahawr_runner/think_profiles.py): the ``"profiles"`` catalog of the
``thinking_json`` settings and the lookup a task's profile name resolves to."""

from __future__ import annotations

import json

import pytest

from ahawr_runner.think_policy import parse
from ahawr_runner.think_profiles import parse_profiles, resolve_profile

# inline copy of the values of data-tables/ops/think_policy_plan_pulse.json
PLAN_PULSE = {
    "variants": [
        {
            "name": "plan+pulse",
            "weight": 1,
            "first": [["think", 8], ["off", 25], ["think", 10], ["off", None]],
            "retry": [["think", 8], ["off", 25], ["think", 10], ["off", None]],
        }
    ],
    "from_task": 1,
}

CATALOG = {
    "profiles": {
        "none": {"variants": [{"first": [["off", None]]}], "cap_steps": 1},
        "plan": {
            "variants": [{"first": [["think", 8], ["off", 25], ["think", 10]]}],
            "cap_steps": 12,
        },
        # cap 18 covers the whole 8 + 10 pulse, so nothing of it is cut
        "plan+pulse": {
            "variants": PLAN_PULSE["variants"],
            "from_task": PLAN_PULSE["from_task"],
            "cap_steps": 18,
        },
        "heavy": {"variants": [{"first": [["think", None]]}], "cap_steps": 20},
        "broken": {"variants": [{"first": [["no", 3]]}], "cap_steps": 5},
        "badcap": {"variants": [{"first": [["think", 2]]}], "cap_steps": 0},
    }
}


def test_parse_profiles_reads_none_plan_plan_pulse_and_heavy() -> None:
    catalog = parse_profiles(json.dumps(CATALOG))
    assert sorted(catalog) == ["heavy", "none", "plan", "plan+pulse"]
    assert parse_profiles("") == {}
    assert parse_profiles("{nope") == {}


def test_an_unknown_profile_name_resolves_to_none() -> None:
    catalog = parse_profiles(json.dumps(CATALOG))
    assert resolve_profile("not-a-profile", catalog) is None
    assert resolve_profile("broken", catalog) is None  # skipped entry


def test_a_missing_profile_name_resolves_to_none() -> None:
    catalog = parse_profiles(json.dumps(CATALOG))
    assert resolve_profile(None, catalog) is None
    assert resolve_profile("", catalog) is None


def test_an_invalid_variants_list_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    text = json.dumps(
        {"profiles": {"plan": {"variants": [{"first": [["no", 3]]}], "cap_steps": 5}}}
    )
    assert parse_profiles(text) == {}
    assert "mode must be one of" in caplog.text


def test_a_cap_of_zero_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    text = json.dumps(
        {"profiles": {"plan": {"variants": [{"first": [["think", 2]]}], "cap_steps": 0}}}
    )
    assert parse_profiles(text) == {}
    assert "cap_steps must be a whole number" in caplog.text


def test_a_negative_cap_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    text = json.dumps(
        {"profiles": {"plan": {"variants": [{"first": [["think", 2]]}], "cap_steps": -4}}}
    )
    assert parse_profiles(text) == {}
    assert "cap_steps must be a whole number" in caplog.text


def test_a_string_cap_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    text = json.dumps(
        {"profiles": {"plan": {"variants": [{"first": [["think", 2]]}], "cap_steps": "12"}}}
    )
    assert parse_profiles(text) == {}
    assert "cap_steps must be a whole number" in caplog.text


def test_resolve_applies_the_cap_to_the_thinking_steps() -> None:
    text = json.dumps(
        {
            "profiles": {
                "plan": {
                    "variants": [{"first": [["think", 6], ["off", 3], ["think", 7]]}],
                    "cap_steps": 9,
                }
            }
        }
    )
    policy = resolve_profile("plan", parse_profiles(text))
    assert policy is not None
    variant = policy.variant_for(1)
    modes = [variant.mode_at(n, False)[0] for n in range(200)]
    assert modes.count("think") == 9  # 4 of the 13 think calls of the timeline are cut to off
    assert modes[:6] == ["think"] * 6 and modes[6:9] == ["off"] * 3 and modes[9:12] == ["think"] * 3
    assert modes[12:] == ["off"] * 188


def test_think_until_the_end_is_cut_at_the_cap() -> None:
    text = json.dumps(
        {"profiles": {"heavy": {"variants": [{"first": [["think", None]]}], "cap_steps": 3}}}
    )
    policy = resolve_profile("heavy", parse_profiles(text))
    assert policy is not None
    variant = policy.variant_for(1)
    modes = [variant.mode_at(n, False)[0] for n in range(5)]
    assert modes == ["think", "think", "think", "off", "off"]


def test_a_repeating_capped_timeline_stops_at_the_cap() -> None:
    text = json.dumps(
        {
            "profiles": {
                "plan": {
                    "cap_steps": 12,
                    "variants": [
                        {"name": "p", "repeat": True, "first": [["think", 8], ["off", 25]]}
                    ],
                }
            }
        }
    )
    policy = resolve_profile("plan", parse_profiles(text))
    assert policy is not None
    variant = policy.variant_for(1)
    thinks = sum(1 for n in range(500) if variant.mode_at(n, False)[0] == "think")
    assert thinks <= 12  # without the cap the timeline would cycle: 124 thinks


def test_plan_plus_pulse_resolves_to_the_policy_of_the_stored_json() -> None:
    """The cap 18 covers the 8 + 10 think calls, so the resolved policy is exactly the
    one think_policy.parse reads out of data-tables/ops/think_policy_plan_pulse.json."""
    catalog = parse_profiles(json.dumps(CATALOG))
    stored_policy, errors = parse(json.dumps(PLAN_PULSE))
    assert errors == []
    assert resolve_profile("plan+pulse", catalog) == stored_policy