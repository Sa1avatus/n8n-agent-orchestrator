"""JSON thinking policy (claude_runner/think_policy.py) and its use by ThinkSwitch."""

from __future__ import annotations

import json
from pathlib import Path

from claude_runner import think_policy
from claude_runner.store import Store
from claude_runner.think_switch import ThinkSwitch
from tests.test_think_switch import _Events, _prompt_run

PLAN_PULSE = [["think", 8], ["off", 25], ["think", 10], ["off", None]]
POLICY = {
    "variants": [
        {"name": "plan+pulse", "weight": 2, "first": PLAN_PULSE, "retry": PLAN_PULSE},
        {"name": "control", "first": [["off", None]], "retry": [["off", None]]},
    ],
    "from_task": 1,
}


def test_timeline_modes() -> None:
    policy, errors = think_policy.parse(json.dumps(POLICY))
    assert policy is not None and errors == []
    variant = policy.variants[0]
    modes = [variant.mode_at(n, False)[0] for n in range(0, 60)]
    assert (
        modes[:8] == ["think"] * 8
        and modes[8:33] == ["off"] * 25
        and modes[33:43] == ["think"] * 10
        and modes[43:] == ["off"] * 17
    )


def test_weights_lay_out_the_tasks() -> None:
    policy, _ = think_policy.parse(json.dumps(POLICY))
    assert [policy.variant_for(n).name for n in (1, 2, 3, 4, 5, 6)] == [
        "plan+pulse",
        "plan+pulse",
        "control",
        "plan+pulse",
        "plan+pulse",
        "control",
    ]
    assert think_policy.variant_name(json.dumps(POLICY), 3) == "control"
    assert (
        think_policy.variant_name("pulse:25:10:1|off", 2) == "off"
        and think_policy.variant_name("", 1) == ""
    )


def test_first_and_retry_can_differ_and_missing_side_copies_the_other() -> None:
    text = json.dumps(
        {
            "variants": [
                {"name": "a", "first": [["off", None]], "retry": [["think", 5], ["off", None]]},
                {"name": "b", "first": [["think", 3], ["off", None]]},
            ]
        }
    )
    policy, errors = think_policy.parse(text)
    assert errors == []
    a, b = policy.variants
    assert (
        a.mode_at(0, False)[0] == "off"
        and a.mode_at(0, True)[0] == "think"
        and a.mode_at(5, True)[0] == "off"
    )
    assert b.mode_at(0, True)[0] == "think"  # retry copies first


def test_repeat_cycles_the_timeline() -> None:
    policy, errors = think_policy.parse(
        json.dumps(
            {"variants": [{"name": "r", "repeat": True, "first": [["off", 3], ["think", 2]]}]}
        )
    )
    assert errors == []
    assert [policy.variants[0].mode_at(n, False)[0] for n in range(10)] == ["off"] * 3 + [
        "think"
    ] * 2 + ["off"] * 3 + ["think"] * 2


def test_validation_names_the_problem() -> None:
    assert think_policy.validate("{nope")[0] is False
    assert "variants" in think_policy.validate("{}")[1]
    bad_mode = json.dumps({"variants": [{"first": [["maybe", 3]]}]})
    assert "mode" in think_policy.validate(bad_mode)[1]
    null_in_middle = json.dumps({"variants": [{"first": [["off", None], ["think", 2]]}]})
    assert "last segment" in think_policy.validate(null_in_middle)[1]
    zero = json.dumps({"variants": [{"first": [["off", 0]]}]})
    assert ">= 1" in think_policy.validate(zero)[1]
    repeat_null = json.dumps({"variants": [{"repeat": True, "first": [["off", None]]}]})
    assert "repeat" in think_policy.validate(repeat_null)[1]
    twice = json.dumps(
        {"variants": [{"name": "x", "first": [["off", 1]]}, {"name": "x", "first": [["off", 1]]}]}
    )
    assert "twice" in think_policy.validate(twice)[1]
    ok, message = think_policy.validate(json.dumps(POLICY))
    assert ok and "plan+pulse" in message


def test_switch_follows_a_json_policy(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("off")
    events = _Events(tmp_path / "events")
    policy = {
        "variants": [
            {
                "name": "plan",
                "first": [["think", 2], ["off", None]],
                "retry": [["think", 3], ["off", None]],
            }
        ]
    }
    switch = ThinkSwitch(store, flag, 0, events, default_policy=json.dumps(policy))
    _prompt_run(store, "a", "s1", "running", 60, None, "TASK T001: x")
    assert switch.tick() == "on" and "policy plan" in events.notes[-1][1]["reason"]
    events.steps("a", 2)
    assert switch.tick() == "off"
    store.update_run("a", status="completed", finished_at="2026-01-01T00:00:00+00:00")
    _prompt_run(store, "b", "s1", "running", 5, None, "TASK T001: x (retry)")
    assert switch.tick() == "on"  # a retry: its own timeline, 3 calls of thinking
    events.steps("b", 3)
    assert switch.tick() == "off"


def test_an_invalid_policy_keeps_the_last_valid_one(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("off")
    events = _Events(tmp_path / "events")
    good = json.dumps({"variants": [{"name": "t", "first": [["think", None]]}]})
    switch = ThinkSwitch(store, flag, 0, events, default_policy=good)
    _prompt_run(store, "a", "s1", "running", 60, None, "TASK T001: x")
    assert switch.tick() == "on"
    flag.write_text("off")
    store.update_run("a", think_policy="{broken")
    assert switch.tick() == "on"  # the broken text is ignored, the policy that worked stays


def test_a_json_policy_from_task_waits_for_its_task(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    events = _Events(tmp_path / "events")
    policy = {"from_task": 5, "variants": [{"name": "off", "first": [["off", None]]}]}
    switch = ThinkSwitch(store, flag, 0, events, default_policy=json.dumps(policy))
    _prompt_run(store, "a", "s1", "running", 60, None, "TASK T003: x")
    assert switch.tick() == "" or flag.read_text() == "on"
    _prompt_run(store, "b", "s5", "running", 30, None, "TASK T006: x")
    store.update_run("a", status="completed", finished_at="2026-01-01T00:00:00+00:00")
    assert switch.tick() == "off"
