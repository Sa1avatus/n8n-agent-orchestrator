"""Tests for board_think.py (the pure thinking-policy statistics): the policy
validation (JSON shape, variant fields, timelines), the weighted variant
layout over the first tasks with ``from_task``, the "null = until the end"
timeline rule, the built-in templates, the per-variant statistics, the
uplift against the control, and the deterministic Thompson sampling."""

from __future__ import annotations

from claude_runner.board_think import (
    TEMPLATES,
    layout_preview,
    thompson_best,
    validate_policy,
    variant_for_task,
    variant_stats,
)

#: Two variants, weights 2 and 1, the policy applies from task 5 on.
POLICY = {
    "variants": [
        {"name": "plan+pulse", "weight": 2},
        {"name": "control", "weight": 1},
    ],
    "from_task": 5,
}


# --------------------------------------------------------------------------- validation


def test_validate_policy_reports_the_missing_or_broken_shape() -> None:
    assert validate_policy(None) == [
        'a policy object with a non-empty "variants" list is required'
    ]
    assert validate_policy("{nope")[0].startswith("not valid JSON")
    assert validate_policy({}) == [
        'an object with a non-empty "variants" list is required'
    ]
    assert validate_policy({"variants": []}) == [
        'an object with a non-empty "variants" list is required'
    ]
    assert validate_policy(
        {"variants": [{"name": "a"}], "from_task": -1}
    ) == ['"from_task": a whole number >= 0 is required']


def test_validate_policy_names_each_variant_problem() -> None:
    assert validate_policy({"variants": [{"name": "a"}]}) == []  # never thinks
    assert validate_policy({"variants": [{"name": "a", "retry": [["off", None]]}]}) == []
    assert validate_policy({"variants": ["a"]})[0] == "variants[0]: an object is required"
    assert validate_policy({"variants": [{"name": ""}]})[0] == (
        "variants[0].name: a non-empty string is required"
    )
    assert validate_policy({"variants": [{"name": "x"}, {"name": "x"}]})[0] == (
        "variants[1]: the name 'x' is used twice"
    )
    for weight in (0, 21, "2", True):
        assert validate_policy({"variants": [{"name": "a", "weight": weight}]})[0] == (
            "variants[0].weight: a whole number 1..20 is required"
        )
    assert validate_policy(
        {"variants": [{"name": "a", "first": [["maybe", 3]]}]}
    )[0] == "variants[0].first[0]: mode must be one of ('think', 'off'), got 'maybe'"
    assert validate_policy(
        {"variants": [{"name": "a", "first": [["off"]]}]}
    )[0] == "variants[0].first[0]: expected [mode, calls]"
    for calls in (0, -1, "2", 1.5):
        assert validate_policy(
            {"variants": [{"name": "a", "first": [["off", calls]]}]}
        )[0] == f"variants[0].first[0]: calls must be a whole number >= 1 or null, got {calls!r}"
    assert validate_policy(
        {"variants": [{"name": "a", "repeat": True, "first": [["off", None]]}]}
    )[0] == "variants[0]: repeat needs a number of calls in every segment (no null)"


def test_a_fully_broken_variant_is_reported_and_no_variant_is_used() -> None:
    problems = validate_policy({"variants": [{"name": ""}]})
    assert problems == ["variants[0].name: a non-empty string is required"]
    assert variant_for_task({"variants": [{"name": ""}]}, 1) is None


def test_the_timeline_may_run_until_the_end_only_in_its_last_segment() -> None:
    until_end = {"variants": [{"name": "a", "first": [["think", 8], ["off", 25], ["think", 10], ["off", None]]}]}
    assert validate_policy(until_end) == []
    assert validate_policy(
        {"variants": [{"name": "a", "first": [["off", None], ["think", 2]]}]}
    )[0] == "variants[0].first[0]: null (until the end) is only allowed in the last segment"


# --------------------------------------------------------------------------- layout


def test_layout_preview_lays_out_the_first_twelve_tasks() -> None:
    # weights 2 + 1: task n is at position (n - 1) % 3 of the layout
    # [plan+pulse, plan+pulse, control]; before task 5 the policy does not
    # apply (None)
    assert layout_preview(POLICY) == [
        None, None, None, None,
        "plan+pulse", "control", "plan+pulse",
        "plan+pulse", "control", "plan+pulse",
        "plan+pulse", "control",
    ]
    assert layout_preview(POLICY, 6) == [None, None, None, None, "plan+pulse", "control"]


def test_variant_for_task_honours_the_from_task_override() -> None:
    assert variant_for_task(POLICY, 3, from_task=5) is None
    assert variant_for_task(POLICY, 5, from_task=5) == "plan+pulse"
    assert variant_for_task(POLICY, 6, from_task=5) == "control"
    assert variant_for_task(POLICY, 7, from_task=5) == "plan+pulse"
    # from_task=1 applies from the start: task 10 is (10 - 1) % 3 = 0
    assert variant_for_task(POLICY, 10, from_task=1) == "plan+pulse"
    # the default from_task comes from the policy (5)
    assert variant_for_task(POLICY, 10) == "plan+pulse"


def test_variant_for_task_is_none_for_a_broken_policy() -> None:
    assert variant_for_task({"variants": []}, 1) is None


# --------------------------------------------------------------------------- templates


def test_builtin_templates_pass_validation() -> None:
    for name, policy in TEMPLATES.items():
        assert validate_policy(policy) == [], name
    assert layout_preview(TEMPLATES["control"]) == ["control"] * 12


# --------------------------------------------------------------------------- statistics


def _attempt(variant, mission, task, pass_, secs, steps) -> dict:
    """A synthetic ``think_data`` attempt row: the variant/mission/task keys
    plus the pass flag, the duration and the steps with the token fields."""
    return {
        "variant": variant,
        "mission": mission,
        "task": task,
        "pass": pass_,
        "secs": secs,
        "steps": steps,
    }


#: Control: 4 attempts, 2 passed, 120 s each.
#: Arm: 4 attempts, 3 passed, 90 s each.
#: Both arms have 4 finished tasks (>= 5 is required for the
#: minutes/attempts-per-task interval, so only the pass-rate interval is
#: reported).
STAT_ATTEMPTS = [
    _attempt("control", "m1", 1, True, 120, [{"think": True, "input_tokens": 100, "output_tokens": 50}]),
    _attempt("control", "m1", 1, False, 120, [{"think": False, "input_tokens": 5}]),
    _attempt("control", "m1", 1, True, 120, [{"think": False, "input_tokens": 10, "output_tokens": 20}]),
    _attempt("control", "m1", 1, False, 120, [{"think": False, "output_tokens": 5}]),
    _attempt("arm", "m1", 1, True, 90, [{"think": True, "input_tokens": 10, "output_tokens": 5}]),
    _attempt("arm", "m1", 1, True, 90, [{"think": True, "output_tokens": 5}]),
    _attempt("arm", "m1", 1, False, 90, [{"think": True, "input_tokens": 1}]),
    _attempt("arm", "m1", 1, True, 90, []),
]


#: 100 attempts per arm: 25 missions x 4 tasks, each mission finished in one
#: pass. Arm passes every even index (50/100), control every 4th (25/100);
#: arm 90 s per attempt (1.5 min), control 120 s (2 min).
BIG_ATTEMPTS: list[dict] = []
for _m in range(25):
    for _t in range(4):
        _idx = _m * 4 + _t
        BIG_ATTEMPTS.append(_attempt("arm", f"m{_m}", _t, _idx % 2 == 0, 90, []))
        BIG_ATTEMPTS.append(_attempt("control", f"m{_m}", _t, _idx % 4 == 0, 120, []))


def test_variant_stats_reports_per_variant_numbers() -> None:
    stats = variant_stats(STAT_ATTEMPTS, control="control")
    by = {s["variant"]: s for s in stats}
    control = by["control"]
    arm = by["arm"]
    assert control["attempts"] == 4
    assert control["passes"] == 2
    assert control["acceptance_share"] == 0.5
    assert control["minutes_per_attempt"] == 2.0
    assert control["thinking_tokens"] == 190
    assert control["share_with_thinking"] == 0.25
    assert control["share_without_thinking"] == 0.75
    assert arm["attempts"] == 4
    assert arm["passes"] == 3
    assert arm["acceptance_share"] == 0.75
    assert arm["minutes_per_attempt"] == 1.5
    assert arm["thinking_tokens"] == 21
    assert arm["share_with_thinking"] == 0.75
    assert arm["share_without_thinking"] == 0.25
    # the control variant has no uplift against itself
    assert "uplift" not in control


def test_variant_stats_reports_the_uplift_against_the_control() -> None:
    uplift = variant_stats(BIG_ATTEMPTS, control="control")[0]["uplift"]
    assert uplift["pass_diff_pct"] == 25.0
    lo, hi = uplift["pass_interval"]
    assert 0.14 < lo < 0.16 and 0.35 < hi < 0.37 and lo < hi
    assert uplift["minutes_per_task"] == -0.5
    assert uplift["minutes_interval"] == [-0.5, -0.5]
    assert uplift["attempts_per_task"] == 0.0
    assert uplift["attempts_interval"] == [0.0, 0.0]


def test_variant_stats_says_how_many_attempts_are_missing() -> None:
    # the arm has exactly the 100-attempt threshold -> the uplift is computed
    assert variant_stats(BIG_ATTEMPTS, control="control")[0]["uplift"]["pass_diff_pct"] == 25.0
    # below the threshold the uplift is the missing-attempts text
    arm = [a for a in BIG_ATTEMPTS if a["variant"] == "arm"]
    ctrl = [a for a in BIG_ATTEMPTS if a["variant"] == "control"]
    stats = variant_stats(arm[:80] + ctrl, control="control")
    by = {s["variant"]: s for s in stats}
    assert by["arm"]["uplift"] == {
        "text": "мало данных, нужно ещё 20 попыток на ветку"
    }


def test_thompson_best_is_deterministic_for_a_fixed_seed() -> None:
    first = thompson_best(BIG_ATTEMPTS, seed=7)
    second = thompson_best(BIG_ATTEMPTS, seed=7)
    assert first == second
    # the arm (50/100 passes, 90 s per attempt) is the best variant:
    # it wins the draws and the control keeps only the minimum weight
    assert first[0]["variant"] == "arm"
    assert first[0]["p_best"] > 0.99
    assert first[0]["suggested_weight"] >= 7
    assert first[1]["suggested_weight"] == 1
    assert abs(sum(e["p_best"] for e in first) - 1.0) < 0.01
    assert first[0]["rate_per_hour"] == 2000.0
    assert first[1]["rate_per_hour"] == 750.0
