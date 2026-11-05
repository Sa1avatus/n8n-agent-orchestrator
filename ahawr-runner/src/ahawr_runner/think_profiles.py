"""Catalog and lookup of thinking profiles (the ``"profiles"`` key of the
``thinking_json`` settings)::

    {"profiles": {"plan": {"variants": [...], "cap_steps": 12}, ...}}

Each entry is a think_policy variant list parsed by ``think_policy.parse`` plus a
cap on the thinking steps a task may spend. ``resolve_profile`` gives a task's
profile name the policy with its cap applied; a missing or unknown name resolves
to None, so the caller keeps the global policy. An invalid entry is skipped with
the reason logged.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

from ahawr_runner.think_policy import Policy, Variant, parse

# The profile names the Architect may pick per task; anything else resolves to None.
ALLOWED_PROFILE_NAMES = ("none", "plan", "plan+pulse", "heavy")

log = logging.getLogger("ahawr_runner.think_profiles")


def parse_profiles(thinking_json: str | None) -> dict[str, tuple[Policy, int]]:
    """Read the profile catalog of the ``thinking_json`` settings.

    Return name -> (policy parsed by ``think_policy.parse``, cap_steps). An entry
    with an invalid variants list or a cap that is not a whole number >= 1 is
    skipped with the reason logged, so the caller keeps the usable entries."""
    catalog: dict[str, tuple[Policy, int]] = {}
    text = (thinking_json or "").strip().lower()
    if not text:
        return catalog
    try:
        data = json.loads(text)
    except ValueError as exc:
        log.warning("thinking_json profiles: not valid JSON: %s", exc)
        return catalog
    entries = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        log.warning('thinking_json profiles: "profiles" must be an object of name -> entry')
        return catalog
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            log.warning(
                "thinking_json profile %r: an object with variants list and cap_steps",
                name,
            )
            continue
        cap = entry.get("cap_steps")
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            log.warning(
                "thinking_json profile %r: cap_steps must be a whole number >= 1, got %r",
                name,
                cap,
            )
            continue
        policy, errors = parse(json.dumps(entry))
        if policy is None:
            log.warning("thinking_json profile %r: %s", name, "; ".join(errors))
            continue
        catalog[name] = (policy, cap)
    return catalog


def resolve_profile(
    name: str | None, catalog: Mapping[str, tuple[Policy, int]]
) -> Policy | None:
    """The policy for the profile a task asks for, with its cap applied; None for a
    missing or unknown name, so the caller keeps the global policy."""
    key = (name or "").strip().lower()
    if key not in ALLOWED_PROFILE_NAMES or key not in catalog:
        return None
    policy, cap_steps = catalog[key]
    return _capped(policy, cap_steps)


def _capped(policy: Policy, cap_steps: int) -> Policy:
    """The same variants with every timeline capped to cap_steps think calls."""
    variants: list[Variant] = []
    for variant in policy.variants:
        first = _cap_timeline(variant.first, cap_steps)
        retry = _cap_timeline(variant.retry, cap_steps)
        repeat = variant.repeat
        if repeat:
            # a capped timeline that kept repeating would restart thinking after
            # the cap: end it with an open off and stop the repetition.
            first = _ends_off(first)
            retry = _ends_off(retry)
            repeat = False
        variants.append(
            Variant(
                name=variant.name,
                weight=variant.weight,
                first=first,
                retry=retry,
                repeat=repeat,
            )
        )
    return Policy(tuple(variants), policy.from_task)


def _ends_off(
    segments: tuple[tuple[str, int | None], ...]
) -> tuple[tuple[str, int | None], ...]:
    """The timeline with an open off at the end, so thinking cannot restart."""
    if segments and segments[-1] == ("off", None):
        return segments
    return segments + (("off", None),)


def _cap_timeline(
    segments: tuple[tuple[str, int | None], ...], cap_steps: int
) -> tuple[tuple[str, int | None], ...]:
    """A timeline whose think calls in total stay <= cap_steps: once the cap is full
    a think segment turns to off (the model calls stay counted in the timeline)."""
    left = cap_steps
    capped: list[tuple[str, int | None]] = []
    for mode, calls in segments:
        if mode == "off":
            capped.append((mode, calls))
            continue
        if calls is None:
            # think until the attempt end: the cap thinks, the rest of the attempt is off
            if left > 0:
                capped.append(("think", left))
                capped.append(("off", None))
            else:
                capped.append(("off", None))
            left = 0
        else:
            allowed = min(left, calls)
            left -= allowed
            if allowed > 0:
                capped.append(("think", allowed))
            # the calls past the cap fall under "timeline over" -> off
    return tuple(capped)
