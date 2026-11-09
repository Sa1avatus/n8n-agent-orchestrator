"""Pure engine log line parser (ahawr_runner/engine_log.py): every line shape of the
regexes of ``engine_segment`` in ``data-tables/ops/bench_series_table.py`` — the
generation tok/s line with its ms, the drafts accepted line, the expert cache slots,
the MiB of VRAM free occurrences, the low-VRAM warning and the 'engine started:'
restart marker. Unmatched and malformed lines give no samples and never raise."""

import pytest

from ahawr_runner.engine_log import (
    DraftsAccepted,
    EngineRestart,
    ExpertCacheSlots,
    Generation,
    LowVramWarning,
    Sample,
    VramFree,
    parse_line,
)

# every line below is built from the regexes quoted in engine_segment; the shapes are
# exactly those regexes, no format invented
CASES = [
    # the generation line and the drafts line live on one log line, as in engine_segment
    (
        "1234 generated in 200 ms (6.17 tok/s), drafts accepted 1230 of 1234",
        [
            Generation(tokens=1234, gen_ms=200, tok_s=6.17),
            DraftsAccepted(accepted=1230, total=1234),
        ],
    ),
    ("37 generated in 1 ms (37.0 tok/s)", [Generation(tokens=37, gen_ms=1, tok_s=37.0)]),
    ("engine started: strata q2_0", [EngineRestart(marker="engine started: strata q2_0")]),
    (
        "[2026-11-05 12:00:01] engine started: q2_0 (kv=8)",
        [EngineRestart(marker="[2026-11-05 12:00:01] engine started: q2_0 (kv=8)")],
    ),
    ("engine started:", [EngineRestart(marker="engine started:")]),
    ("expert cache 8 slots", [ExpertCacheSlots(slots=8)]),
    ("llama: 1024 MiB of VRAM free", [VramFree(free_mib=1024)]),
    # engine_segment counts the 'only' line in the free list too, hence both samples
    (
        "refusing offload: only 256 MiB of VRAM free",
        [VramFree(free_mib=256), LowVramWarning(free_mib=256)],
    ),
    # malformed: the tok/s group is not a number, the line is ignored without raising
    ("1234 generated in 200 ms (6.1.7 tok/s)", []),
    ("1234 generated in 200 ms ( tok/s)", []),
    # shapes that do not carry the exact regex of engine_segment are ignored
    ("1234 generated in 200 ms tok/s)", []),
    ("drafts accepted 4 of 5", []),
    ("expert cache slots", []),
    ("expert cache 8 slot", []),
    ("only MiB of VRAM free", []),
    ("42 MiB of VRAM used", []),
    ("engine started", []),
    ("", []),
    ("the quick brown fox 42", []),
]


@pytest.mark.parametrize("line, expected", CASES)
def test_parse_line_table(line: str, expected: list[Sample]) -> None:
    """Table-driven: each quoted shape yields its samples, garbage and malformed lines none."""
    assert parse_line(line) == expected


def test_parse_line_ne_raises_on_garbage() -> None:
    """A single garbage line never raises; the whole table already proves no raise."""
    assert parse_line("!! (( 123 generated in (NaN tok/s) only ?? MiB of VRAM free") == []
