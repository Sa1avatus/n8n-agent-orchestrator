"""Risk tiers of a file path the Worker touches: core or supporting (T002A).

``tier_of`` is pure: it matches a path against an ordered sequence of
``(glob, tier)`` patterns (``Settings.risk_tier_patterns``) and returns the
tier of the first match. No I/O, so guards and tests can call it with any
path string.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Sequence
from typing import Literal

Tier = Literal["core", "supporting"]
CORE: Tier = "core"
SUPPORTING: Tier = "supporting"
# Conservative default tier: a path matching no pattern is treated as "core",
# so an unknown module is never classified as safe to edit.
UNMATCHED_TIER: Tier = "core"


def tier_of(path: str, patterns: Sequence[tuple[str, str]]) -> Tier:
    """Return ``"core"`` or ``"supporting"`` for ``path``.

    The first matching pattern wins. Backslashes in ``path`` are normalised to
    "/" so Windows and POSIX paths match the same patterns. A path that
    matches nothing gets ``UNMATCHED_TIER`` ("core"), stated here and below.
    """
    normalized = path.replace("\\", "/")
    for pattern, tier in patterns:
        if fnmatch.fnmatch(normalized, pattern):
            if tier == CORE:
                return CORE
            if tier == SUPPORTING:
                return SUPPORTING
            raise ValueError(f"risk tier pattern {pattern!r} has unknown tier {tier!r}")
    return UNMATCHED_TIER


def group_by_tier(
    paths: Iterable[str],
    patterns: Sequence[tuple[str, str]],
) -> dict[Tier, list[str]]:
    """Split ``paths`` into core and supporting lists, in first-seen order.

    Duplicate paths are collapsed; a path that matches no pattern gets
    ``UNMATCHED_TIER`` (see ``tier_of``). The only place grouping happens, so
    the report pack and the review pack share it.
    """
    grouped: dict[Tier, list[str]] = {CORE: [], SUPPORTING: []}
    seen: set[str] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        grouped[tier_of(path, patterns)].append(path)
    return grouped
