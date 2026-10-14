"""JSON thinking policy for the local Worker (``hermes_config.worker_think_policy`` / ``CLAUDE_RUNNER_THINK_POLICY``).

A policy is a JSON object; every variant is a timeline of segments counted in model calls of one attempt::

    {
      "variants": [
        {"name": "plan+pulse", "weight": 2,
         "first": [["think", 8], ["off", 25], ["think", 10], ["off", null]],
         "retry": [["think", 8], ["off", 25], ["think", 10], ["off", null]]},
        {"name": "control", "weight": 1, "first": [["off", null]], "retry": [["off", null]]}
      ],
      "from_task": 1
    }

- a segment is ``[mode, calls]``: mode ``think`` or ``off``; ``null`` calls = until the attempt ends (only the last segment may have it);
- ``first`` is the first attempt at a task, ``retry`` an attempt after a review (missing = the other one; both missing = never thinks);
- ``repeat: true`` repeats the timeline from the start when it is over (every segment then needs a number of calls);
- ``weight`` (1..20, default 1) is the share of tasks: task n uses the variant at position (n - 1) mod (sum of weights) of the weights laid out in order,
  so one mission compares the variants;
- ``from_task``: the policy applies from this task number on (default 0 = the workflow's setting).

The text is lower-cased on its way from the workflow, so names and modes are lower-case. ``validate`` tells what is wrong before the value is stored.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MODES = ("think", "off")
MAX_WEIGHT_SUM = 24


@dataclass(frozen=True)
class Variant:
    name: str
    weight: int
    first: tuple[tuple[str, int | None], ...]
    retry: tuple[tuple[str, int | None], ...]
    repeat: bool = False

    def mode_at(self, calls_made: int, retry: bool) -> tuple[str, str]:
        """('think' | 'off', label of the segment) for the model call number ``calls_made`` + 1 of an attempt."""
        segments = self.retry if retry else self.first
        position = calls_made
        total = sum(count for _, count in segments if count is not None)
        if self.repeat and total > 0 and all(count is not None for _, count in segments):
            position %= total
        start = 0
        for index, (mode, count) in enumerate(segments):
            if count is None or position < start + count:
                return mode, f"segment {index + 1} {mode}"
            start += count
        return "off", "timeline over"


@dataclass(frozen=True)
class Policy:
    variants: tuple[Variant, ...]
    from_task: int = 0

    def variant_for(self, task_number: int) -> Variant:
        layout = [variant for variant in self.variants for _ in range(variant.weight)]
        return layout[(max(task_number, 1) - 1) % len(layout)]


def _segments(value: Any, where: str, errors: list[str]) -> tuple[tuple[str, int | None], ...] | None:
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
        if mode not in MODES:
            errors.append(f"{label}: mode must be one of {MODES}, got {mode!r}")
            continue
        if calls is None:
            if index != len(value) - 1:
                errors.append(f"{label}: null (until the end) is only allowed in the last segment")
                continue
        elif isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
            errors.append(f"{label}: calls must be a whole number >= 1 or null, got {calls!r}")
            continue
        out.append((mode, calls))
    return tuple(out) if len(out) == len(value) else None


def parse(text: str) -> tuple[Policy | None, list[str]]:
    """The policy and the list of problems (an empty list = valid)."""
    errors: list[str] = []
    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, [f"not valid JSON: {exc}"]
    if not isinstance(data, dict) or not isinstance(data.get("variants"), list) or not data["variants"]:
        return None, ['an object with a non-empty "variants" list is required']
    variants: list[Variant] = []
    names: set[str] = set()
    for index, raw in enumerate(data["variants"]):
        where = f"variants[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where}: an object is required")
            continue
        name = str(raw.get("name") or f"v{index + 1}")
        if name in names:
            errors.append(f"{where}: the name {name!r} is used twice")
        names.add(name)
        weight = raw.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, int) or not 1 <= weight <= 20:
            errors.append(f"{where}.weight: a whole number 1..20 is required")
            weight = 1
        never = (("off", None),)
        first = _segments(raw["first"], f"{where}.first", errors) if "first" in raw else None
        retry = _segments(raw["retry"], f"{where}.retry", errors) if "retry" in raw else None
        if "first" not in raw and "retry" not in raw:
            first = retry = never
        elif "first" not in raw:
            first = retry
        elif "retry" not in raw:
            retry = first
        repeat = bool(raw.get("repeat", False))
        if repeat and any(segs and any(count is None for _, count in segs) for segs in (first, retry)):
            errors.append(f"{where}: repeat needs a number of calls in every segment (no null)")
        if first is None or retry is None:
            continue
        variants.append(Variant(name=name, weight=weight, first=first, retry=retry, repeat=repeat))
    from_task = data.get("from_task", 0)
    if isinstance(from_task, bool) or not isinstance(from_task, int) or from_task < 0:
        errors.append('"from_task": a whole number >= 0 is required')
        from_task = 0
    if sum(variant.weight for variant in variants) > MAX_WEIGHT_SUM:
        errors.append(f"the weights add up to more than {MAX_WEIGHT_SUM}")
    if errors or not variants:
        return None, errors or ["no usable variant"]
    return Policy(tuple(variants), from_task), []


def validate(text: str) -> tuple[bool, str]:
    """(True, summary) or (False, the problems), for a person who is about to store the value."""
    policy, errors = parse(text.strip().lower())
    if policy is None:
        return False, "; ".join(errors)
    parts = [f"{variant.name} (weight {variant.weight})" for variant in policy.variants]
    return True, "ok: " + ", ".join(parts)


def variant_name(text: str, task_number: int) -> str:
    """The variant a task gets under a policy text of either kind (JSON or the old 'a|b|c' string); '' when there is none."""
    stripped = text.strip().lower()
    if stripped.startswith("{"):
        policy, _ = parse(stripped)
        return policy.variant_for(task_number).name if policy else ""
    parts = [part.strip() for part in stripped.split("|") if part.strip()]
    return parts[(max(task_number, 1) - 1) % len(parts)] if parts else ""
