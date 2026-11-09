"""Pure engine log parser: every engine log line becomes a typed sample.

The regexes are copied verbatim from ``engine_segment`` (and its cached helper
``_engine_segment``) in ``data-tables/ops/bench_series_table.py``: the restart split
line, the generation line, the drafts-accepted line, the expert cache slots, the MiB
of VRAM free occurrences and the low-VRAM warning. The generation regex adds one
capture group around the generation ms, which ``engine_segment`` does not capture, so
the sample keeps it for the backfill. A line that matches no regex, or matches with a
non-numeric group, yields no samples and never raises.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# regexes verbatim from engine_segment in data-tables/ops/bench_series_table.py
RESTART_RE = re.compile(r"(?m)^.*engine started:.*$")
# engine_segment: r"(\d+) generated in \d+ ms \(([\d.]+) tok/s\)" -- ms captured as a group here
GENERATION_RE = re.compile(r"(\d+) generated in (\d+) ms \(([\d.]+) tok/s\)")
# engine_segment: r"([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)"
DRAFTS_RE = re.compile(r"([\d.]+) tok/s\), drafts accepted (\d+) of (\d+)")
# engine_segment: r"expert cache (\d+) slots"
EXPERT_CACHE_RE = re.compile(r"expert cache (\d+) slots")
# engine_segment: r"(\d+) MiB of VRAM free"
VRAM_FREE_RE = re.compile(r"(\d+) MiB of VRAM free")
# engine_segment: r"only \d+ MiB of VRAM free" -- the number captured here to type the sample
LOW_VRAM_RE = re.compile(r"only (\d+) MiB of VRAM free")


@dataclass(frozen=True)
class Generation:
    tokens: int
    gen_ms: int
    tok_s: float


@dataclass(frozen=True)
class DraftsAccepted:
    accepted: int
    total: int


@dataclass(frozen=True)
class ExpertCacheSlots:
    slots: int


@dataclass(frozen=True)
class VramFree:
    free_mib: int


@dataclass(frozen=True)
class LowVramWarning:
    free_mib: int


@dataclass(frozen=True)
class EngineRestart:
    marker: str


Sample = Generation | DraftsAccepted | ExpertCacheSlots | VramFree | LowVramWarning | EngineRestart


def _tok_s_or_none(text: str) -> float | None:
    """float() of the tok/s group; None when the group is not a number."""
    try:
        return float(text)
    except ValueError:
        return None


def parse_line(line: str) -> list[Sample]:
    """Every sample one engine log line carries; unmatched or malformed lines give []."""
    samples: list[Sample] = []

    restart = RESTART_RE.search(line)
    if restart:
        samples.append(EngineRestart(marker=restart.group(0)))

    gen = GENERATION_RE.search(line)
    if gen:
        tok_s = _tok_s_or_none(gen.group(3))
        if tok_s is not None:
            samples.append(
                Generation(tokens=int(gen.group(1)), gen_ms=int(gen.group(2)), tok_s=tok_s)
            )

    drafts = DRAFTS_RE.search(line)
    if drafts:
        samples.append(DraftsAccepted(accepted=int(drafts.group(2)), total=int(drafts.group(3))))

    cache = EXPERT_CACHE_RE.search(line)
    if cache:
        samples.append(ExpertCacheSlots(slots=int(cache.group(1))))

    free = VRAM_FREE_RE.search(line)
    if free:
        samples.append(VramFree(free_mib=int(free.group(1))))

    low = LOW_VRAM_RE.search(line)
    if low:
        samples.append(LowVramWarning(free_mib=int(low.group(1))))

    return samples
