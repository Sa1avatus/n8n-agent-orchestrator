"""Pure statistics for the thinking policy (the board's "thinking" tab).

Reimplements, without importing the data-tables/ops scripts (see
``docs/board-design.md`` section 10), the numbers the old ``think_data`` /
``think_uplift`` / ``think_bandit`` scripts computed:

- :func:`validate_policy` — the problems with a policy JSON
  ``{"variants": [{"name", "weight", "first", "retry"}], "from_task"}``;
- :func:`variant_for_task` — the variant of task n, laid out by weight:
  position ``(n - 1) % sum of weights`` of the variants expanded in order,
  applied from ``from_task`` on;
- :func:`layout_preview` — the variant of each of the first 12 tasks;
- :func:`variant_stats` — per variant: attempts, acceptance share, minutes
  per attempt, thinking tokens, share of runs with / without thinking, and
  the uplift against the control (or "мало данных, нужно ещё N попыток
  на ветку" below the 100-attempt threshold);
- :func:`thompson_best` — the probability that each variant is the best
  (Thompson sampling, reward = accepted attempts per Worker hour, seed 7)
  and the suggested weights.

An "attempt" is the mapping ``Attempt`` below (one row of
``think_data.load_attempts``): the attempt's fields as dicts, steps as dicts
with a ``think`` flag, and token fields.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

MODES = ("think", "off")
MAX_WEIGHT = 20
MIN_ARM = 100  # the threshold below which the uplift is not computed
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 1
MC_SAMPLES = 10_000
MC_SEED = 7
PROPOSED_SLOTS = 8

#: Built-in policy templates.
TEMPLATES: dict[str, dict[str, Any]] = {
    "off_first": {
        "variants": [
            {
                "name": "off_first",
                "weight": 1,
                "first": [["off", None]],
                "retry": [["think", None]],
            }
        ],
        "from_task": 1,
    },
    "plan+pulse": {
        "variants": [
            {
                "name": "plan+pulse",
                "weight": 1,
                "first": [["think", 8], ["off", 25], ["think", 10], ["off", None]],
                "retry": [["think", 8], ["off", 25], ["think", 10], ["off", None]],
            }
        ],
        "from_task": 1,
    },
    "control": {
        "variants": [
            {"name": "control", "weight": 1, "first": [["off", None]], "retry": [["off", None]]}
        ],
        "from_task": 1,
    },
    "always-think": {
        "variants": [
            {"name": "always-think", "weight": 1, "first": [["think", None]], "retry": [["think", None]]}
        ],
        "from_task": 1,
    },
}

TokenField = tuple[str, ...]
#: The token fields of a step (see ``local_pricing.FIELDS``): the thinking
#: tokens of an attempt are the sum over its steps.
TOKEN_FIELDS: TokenField = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")

Attempt = dict[str, Any]


# --------------------------------------------------------------------------- policy


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _segments(
    value: Any, where: str, errors: list[str]
) -> tuple[tuple[str, int | None], ...] | None:
    """One timeline: a non-empty list of ``[mode, calls]`` segments
    (mode ``think``/``off``, calls a whole number >= 1 or ``null`` = until
    the attempt ends; only the last segment may be null)."""
    if not isinstance(value, list) or not value:
        errors.append(f"{where}: a non-empty list of [mode, calls] is required")
        return None
    out: list[tuple[str, int | None]] = []
    for index, item in enumerate(value):
        label = f"{where}[{index}]"
        if not isinstance(item, list) or len(item) != 2:
            errors.append(f"{label}: expected [mode, calls]")
            continue
        mode, calls = item
        if not isinstance(mode, str) or mode not in MODES:
            errors.append(f"{label}: mode must be one of {MODES}, got {mode!r}")
            continue
        if calls is None:
            if index != len(value) - 1:
                errors.append(f"{label}: null (until the end) is only allowed in the last segment")
                continue
        elif _is_int(calls) and calls >= 1:
            pass
        else:
            errors.append(f"{label}: calls must be a whole number >= 1 or null, got {calls!r}")
            continue
        out.append((mode, calls))
    return tuple(out) if len(out) == len(value) else None


def _variant(raw: Any, index: int, names: set[str], errors: list[str]) -> dict[str, Any] | None:
    where = f"variants[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{where}: an object is required")
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"{where}.name: a non-empty string is required")
        return None
    if name in names:
        errors.append(f"{where}: the name {name!r} is used twice")
    names.add(name)
    weight = raw.get("weight", 1)
    if not _is_int(weight) or not 1 <= weight <= MAX_WEIGHT:
        errors.append(f"{where}.weight: a whole number 1..{MAX_WEIGHT} is required")
        return None
    never = (("off", None),)
    first = _segments(raw.get("first"), f"{where}.first", errors) if "first" in raw else None
    retry = _segments(raw.get("retry"), f"{where}.retry", errors) if "retry" in raw else None
    if "first" not in raw and "retry" not in raw:
        first = retry = never
    elif "first" not in raw:
        first = retry
    elif "retry" not in raw:
        retry = first
    if first is None or retry is None:
        return None
    repeat = bool(raw.get("repeat", False))
    if repeat and any(
        any(count is None for _, count in segs) for segs in (first, retry)
    ):
        errors.append(f"{where}: repeat needs a number of calls in every segment (no null)")
    return {"name": name, "weight": weight, "first": first, "retry": retry, "repeat": repeat}


def _parse_policy(policy: dict[str, Any] | str | None) -> tuple[list[dict[str, Any]] | None, list[str]]:
    """The parsed variants (a dict per variant: name, weight 1..20,
    ``first``/``retry`` timelines, ``repeat``) or None when the policy is
    broken, plus the readable problems (empty list = valid)."""
    if policy is None:
        return None, ["a policy object with a non-empty \"variants\" list is required"]
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except ValueError as exc:
            return None, [f"not valid JSON: {exc}"]
    if not isinstance(policy, dict) or not isinstance(policy.get("variants"), list) or not policy["variants"]:
        return None, ['an object with a non-empty "variants" list is required']
    errors: list[str] = []
    names: set[str] = set()
    variants: list[dict[str, Any]] = []
    for index, raw in enumerate(policy["variants"]):
        parsed = _variant(raw, index, names, errors)
        if parsed is not None:
            variants.append(parsed)
    from_task = policy.get("from_task", 0)
    if _is_int(from_task) and from_task < 0:
        errors.append('"from_task": a whole number >= 0 is required')
    if errors or not variants:
        return None, errors or ["no usable variant"]
    return variants, []


def validate_policy(policy: dict[str, Any] | str | None) -> list[str]:
    """The readable problems of a policy (an empty list = valid).

    Checks the JSON shape and every variant: the name (unique, non-empty),
    the weight (a whole number 1..20, so no zero or negative weights), the
    ``first``/``retry`` timelines (a known mode per segment, a whole number
    >= 1 or ``null`` — no negative counts, null only in the last segment)."""
    return _parse_policy(policy)[1]


def _layout(variants: list[dict[str, Any]]) -> list[str]:
    layout: list[str] = []
    for variant in variants:
        layout.extend([variant["name"]] * variant["weight"])
    return layout


def variant_for_task(
    policy: dict[str, Any], task_number: int, from_task: int | None = None
) -> str | None:
    """The variant name of task n (1-based).

    The weights are laid out in order (each variant ``weight`` times) and the
    variant of task n is at position ``(n - 1) % sum of weights``; the policy
    only applies from task ``from_task`` on (``None`` = read ``from_task``
    from the policy, default 0)."""
    variants, problems = _parse_policy(policy)
    if variants is None:
        return None
    if from_task is None:
        from_task = int(policy.get("from_task", 0))
    if task_number < from_task:
        return None
    layout = _layout(variants)
    return layout[(max(task_number, 1) - 1) % len(layout)]


def layout_preview(policy: dict[str, Any], n: int = 12) -> list[str | None]:
    """The variant of each of the first ``n`` tasks (None before the policy
    applies)."""
    return [variant_for_task(policy, number) for number in range(1, n + 1)]


# --------------------------------------------------------------------------- statistics


def _steps_of(attempt: Attempt) -> list[dict[str, Any]]:
    return list(attempt.get("steps") or [])


def _think_share(steps: list[dict[str, Any]]) -> float:
    if not steps:
        return 0.0
    return sum(1 for step in steps if step.get("think")) / len(steps)


def _mode_bucket(share: float) -> str:
    """The mode of a run: "think" / "nothink" / "mixed" from the share of
    thinking steps."""
    return "think" if share >= 0.3 else ("nothink" if share <= 0.05 else "mixed")


def _passed(attempt: Attempt) -> bool:
    return bool(attempt.get("pass"))


def _seconds(attempt: Attempt) -> float:
    value = attempt.get("secs")
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _token_sum(attempt: Attempt, fields: TokenField = TOKEN_FIELDS) -> int:
    total = 0
    for step in _steps_of(attempt):
        for field in fields:
            try:
                total += int(step.get(field) or 0)
            except (TypeError, ValueError):
                pass
    return total


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation of the ``q``-th percentile (``q`` in [0, 1])."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def _bootstrap_interval(
    x_values: list[float], y_values: list[float], rng: random.Random, samples: int
) -> tuple[float, float]:
    """The 90% interval of ``x - y`` over 2000 bootstrap resamples
    (with replacement, one draw per item)."""
    if not x_values or not y_values:
        return (0.0, 0.0)
    values: list[float] = []
    for _ in range(samples):
        xs = [rng.choice(x_values) for _ in range(len(x_values))]
        ys = [rng.choice(y_values) for _ in range(len(y_values))]
        values.append(_mean(xs) - _mean(ys))
    values.sort()
    return (_percentile(values, 0.05), _percentile(values, 0.95))


def _beta(
    alpha: float, beta: float, rng: random.Random
) -> float:
    """A draw from ``Beta(alpha, beta)`` via the Gamma-Beta relation:
    ``Gamma(alpha, 1) / (Gamma(alpha, 1) + Gamma(beta, 1))`` (no scipy needed)."""
    g1 = rng.gammavariate(alpha, 1.0)
    g2 = rng.gammavariate(beta, 1.0)
    return g1 / (g1 + g2)


def _beta_cdf(
    alpha: float, beta: float, x: float, points: int = 101
) -> float:
    """The ``Beta(alpha, beta)`` distribution function at ``x`` (midpoint
    rule over the dense Beta density; the parameters are 1 + counts, so
    they stay below the 100-point grid)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_c = (
        math.lgamma(alpha + beta) - math.lgamma(alpha) - math.lgamma(beta)
    )
    total = 0.0
    for i in range(points):
        t = (i + 0.5) / points
        if t >= x:
            break
        log_pdf = log_c + (alpha - 1) * math.log(t) + (beta - 1) * math.log(1 - t)
        total += math.exp(log_pdf)
    return total / points


def _uplift(
    arm: list[Attempt], control: list[Attempt], control_name: str
) -> dict[str, Any]:
    """The uplift of ``arm`` against ``control``: the pass-rate difference
    in percentage points with a 90% bootstrap interval, plus time to finish
    a task and attempts per task (each with a bootstrap interval, computed
    when both arms have at least 5 finished tasks)."""
    arm_pass = sum(1 for a in arm if _passed(a))
    control_pass = sum(1 for a in control if _passed(a))
    pass_diff = (arm_pass / len(arm) - control_pass / len(control)) * 100 if arm and control else 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    pass_interval = _bootstrap_interval(
        [float(_passed(a)) for a in arm],
        [float(_passed(c)) for c in control],
        rng,
        BOOTSTRAP_SAMPLES,
    )
    result: dict[str, Any] = {"pass_diff_pct": round(pass_diff, 1), "pass_interval": list(pass_interval)}
    arm_tasks = _finished_tasks(arm)
    control_tasks = _finished_tasks(control)
    if len(arm_tasks) >= 5 and len(control_tasks) >= 5:
        arm_min = _mean([minutes for minutes, _ in arm_tasks])
        control_min = _mean([minutes for minutes, _ in control_tasks])
        result["minutes_per_task"] = round(arm_min - control_min, 1)
        result["minutes_interval"] = list(
            _bootstrap_interval(
                [minutes for minutes, _ in arm_tasks],
                [minutes for minutes, _ in control_tasks],
                rng,
                BOOTSTRAP_SAMPLES,
            )
        )
        arm_attempts = _mean([count for _, count in arm_tasks])
        control_attempts = _mean([count for _, count in control_tasks])
        result["attempts_per_task"] = round(arm_attempts - control_attempts, 2)
        result["attempts_interval"] = list(
            _bootstrap_interval(
                [count for _, count in arm_tasks],
                [count for _, count in control_tasks],
                rng,
                BOOTSTRAP_SAMPLES,
            )
        )
    return result

def _finished_tasks(items: list[Attempt]) -> list[tuple[float, int]]:
    """Per (mission, task): the minutes and the number of attempts needed to
    finish (up to the first passing attempt, or the last one if none)."""
    groups: dict[tuple[Any, int], list[Attempt]] = {}
    for attempt in items:
        key = (attempt.get("mission"), attempt.get("task"))
        groups.setdefault(key, []).append(attempt)
    out: list[tuple[float, int]] = []
    for group in groups.values():
        group.sort(key=lambda a: int(float(a.get("attempt") or 1)))
        cutoff = next((a for a in group if _passed(a)), group[-1])
        index = group.index(cutoff)
        minutes = sum(_seconds(a) for a in group[: index + 1]) / 60.0
        out.append((minutes, index + 1))
    return out


def variant_stats(
    attempts: list[Attempt],
    control: str = "off",
    min_arm: int = MIN_ARM,
) -> list[dict[str, Any]]:
    """The per-variant statistics of the attempts.

    ``attempts``: rows as ``think_data.load_attempts`` maps them — each row
    has ``variant``, ``pass``, ``secs``, ``steps`` (each with a ``think``
    flag and the token fields) and ``mission`` / ``task``.  Every variant
    gets: attempts, acceptance share, minutes per attempt, thinking tokens
    (the thinking steps' token sum), the share of runs with thinking
    (mode "think") and without (mode "nothink"), and — against the control
    variant — the pass-rate uplift in percentage points with a 90%
    bootstrap interval; below ``min_arm`` attempts it reports how many more
    are missing instead ("мало данных, нужно ещё N попыток на ветку")."""
    by_variant: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_variant.setdefault(str(attempt.get("variant") or ""), []).append(attempt)
    control_attempts = by_variant.get(control, [])
    results: list[dict[str, Any]] = []
    for name, items in by_variant.items():
        passed = sum(1 for a in items if _passed(a))
        minutes = _mean([_seconds(a) / 60.0 for a in items])
        think_runs = sum(1 for a in items if _mode_bucket(_think_share(_steps_of(a))) == "think")
        nothink_runs = sum(1 for a in items if _mode_bucket(_think_share(_steps_of(a))) == "nothink")
        stats: dict[str, Any] = {
            "variant": name,
            "attempts": len(items),
            "passes": passed,
            "acceptance_share": round(passed / len(items), 3) if items else 0.0,
            "minutes_per_attempt": round(minutes, 2),
            "thinking_tokens": sum(_token_sum(a) for a in items),
            "share_with_thinking": round(think_runs / len(items), 3) if items else 0.0,
            "share_without_thinking": round(nothink_runs / len(items), 3) if items else 0.0,
        }
        if name != control:
            if len(items) < min_arm:
                stats["uplift"] = {
                    "text": f"мало данных, нужно ещё {min_arm - len(items)} попыток на ветку"
                }
            else:
                uplift = _uplift(items, control_attempts, control)
                stats["uplift"] = uplift
        results.append(stats)
    return results


# --------------------------------------------------------------------------- Thompson sampling


def _variant_rate_draw(
    attempt_list: list[Attempt], rng: random.Random
) -> float:
    """One Thompson draw of a variant's reward: the pass probability
    ``Beta(1 + passes, 1 + fails)`` divided by the bootstrapped mean time of
    an attempt (hours; at least 5 draws)."""
    passed = sum(1 for a in attempt_list if _passed(a))
    failed = len(attempt_list) - passed
    p = _beta(1 + passed, 1 + failed, rng)
    seconds = [_seconds(a) for a in attempt_list] or [0.0]
    draws = 5
    if seconds:
        draws = max(5, len(seconds))
    hours = _mean([rng.choice(seconds) for _ in range(draws)]) / 3600.0
    if hours <= 0.0:
        hours = 1e-9
    return p / hours


def thompson_best(
    attempts: list[Attempt],
    seed: int = MC_SEED,
    samples: int = MC_SAMPLES,
) -> list[dict[str, Any]]:
    """Thompson sampling over the variants (deterministic for a fixed seed).

    Per draw one ``Beta(1 + passes, 1 + fails)`` pass probability is sampled
    per variant and its bootstrapped time per attempt (at least 5 draws,
    hours); the reward is the passes per Worker hour, and the variant with
    the best rate of the draw wins it.  ``p_best`` is the share of the
    ``samples`` draws each variant won.  The suggested weights give every
    variant ``max(1, round(8 * wins / samples))`` slots, trimmed to at most
    8 total (a tie keeps the earlier variant)."""
    by_variant: dict[str, list[Attempt]] = {}
    for attempt in attempts:
        by_variant.setdefault(str(attempt.get("variant") or ""), []).append(attempt)
    rng = random.Random(seed)
    wins = {name: 0 for name in by_variant}
    for _ in range(samples):
        rates = [(_variant_rate_draw(items, rng), name) for name, items in by_variant.items()]
        rates.sort(reverse=True)
        wins[rates[0][1]] += 1
    results: list[dict[str, Any]] = []
    for name, items in by_variant.items():
        passed = sum(1 for a in items if _passed(a))
        hours = _mean([_seconds(a) for a in items]) / 3600.0
        results.append(
            {
                "variant": name,
                "attempts": len(items),
                "passes": passed,
                "p_best": round(wins[name] / samples, 4),
                "rate_per_hour": round(passed / hours, 3) if hours > 0 else 0.0,
            }
        )
    results.sort(key=lambda r: (-r["p_best"], r["variant"]))
    for entry in results:
        entry["suggested_weight"] = max(1, round(PROPOSED_SLOTS * entry["p_best"]))
    total = sum(entry["suggested_weight"] for entry in results)
    if total > PROPOSED_SLOTS:
        for entry in results:  # the order above keeps the stronger variant
            if total > PROPOSED_SLOTS:
                entry["suggested_weight"] = max(1, entry["suggested_weight"] - 1)
                total -= 1
    return results
