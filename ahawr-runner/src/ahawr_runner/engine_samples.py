"""Store and figures for the engine samples of the bench UI (``board.engine_samples``).

Rows come from the live engine-log snapshot collector (source ``sampled``) or
from a backfill of engine log segments (sources ``backfill-segment`` and
``backfill-matched``).  Every row carries a ``sample_key``, so re-running the
collector or the backfill cannot duplicate a sample: the insert does nothing on
a ``sample_key`` conflict.  Access style follows ``board_pg.py``: parameterized
SQL on the store's connection (its ``search_path`` is the board schema), the
fixed table identifiers taken from ``docs/board-schema.sql``, never built from
values.

:func:`engine_window_figures` is pure: from the rows of a window it computes
the engine figures of ``engine_segment`` in ``data-tables/ops/bench_series_table.py``
(the reference formulas): the median generation tok/s of replies with at least
``MIN_GENERATED_TOKENS`` tokens, the share of accepted drafts, the expert cache
slots, the minimum free VRAM in MiB and the count of low-VRAM warnings.  The
per-source sample counts are reported so a mixed window is never mixed silently.
An empty window gives ``None``, its reason being ``NO_SAMPLES_REASON``, never
zeros.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any

_ENGINE_SAMPLES_TABLE = "board.engine_samples"
# the reference value of MIN_GENERATED_TOKENS in data-tables/ops/bench_series_table.py
MIN_GENERATED_TOKENS = 50
NO_SAMPLES_REASON = "no samples"
_WINDOW_COLUMNS = (
    "sampled_at, engine_run, generated_tokens, tok_per_s, drafts_accepted, "
    "drafts_total, expert_slots, vram_free_mib, low_vram, source, sample_key"
)
_INSERT_SQL = (
    f"INSERT INTO {_ENGINE_SAMPLES_TABLE} "
    f"({_WINDOW_COLUMNS}) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (sample_key) DO NOTHING"
)
_WINDOW_SELECT = (
    f"SELECT {_WINDOW_COLUMNS} FROM {_ENGINE_SAMPLES_TABLE} "
    "WHERE sampled_at >= %s AND sampled_at < %s "
    "ORDER BY sampled_at, sample_key"
)


def insert_sample(
    conn: Any,
    *,
    sampled_at: str | datetime,
    engine_run: int | None,
    generated_tokens: int | None,
    tok_per_s: float | None,
    drafts_accepted: int | None,
    drafts_total: int | None,
    expert_slots: int | None,
    vram_free_mib: int | None,
    low_vram: bool,
    sample_key: str,
    source: str = "sampled",
) -> None:
    """One engine sample; a repeated ``sample_key`` is a no-op (idempotent insert)."""
    conn.execute(
        _INSERT_SQL,
        (
            sampled_at,
            engine_run,
            generated_tokens,
            tok_per_s,
            drafts_accepted,
            drafts_total,
            expert_slots,
            vram_free_mib,
            low_vram,
            source,
            sample_key,
        ),
    )


def engine_window(
    conn: Any,
    since: str | datetime,
    until: str | datetime,
) -> list[dict[str, Any]]:
    """The samples of the window [since, until), oldest first."""
    return [
        dict(row)
        for row in conn.execute(_WINDOW_SELECT, (since, until)).fetchall()
    ]


def engine_window_figures(samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The engine figures of a window of samples; ``None`` when the window is
    empty, its reason being ``NO_SAMPLES_REASON`` ('no samples'), never zeros."""
    if not samples:
        return None
    rates = [
        float(row["tok_per_s"])
        for row in samples
        if row.get("tok_per_s") is not None
        and int(row.get("generated_tokens") or 0) >= MIN_GENERATED_TOKENS
    ]
    accepted = sum(int(row.get("drafts_accepted") or 0) for row in samples)
    total = sum(int(row.get("drafts_total") or 0) for row in samples)
    free = [int(row["vram_free_mib"]) for row in samples if row.get("vram_free_mib") is not None]
    by_source: dict[str, int] = {}
    for row in samples:
        source = row.get("source") or "sampled"
        by_source[source] = by_source.get(source, 0) + 1
    # shortcut: the first reported slots value, as engine_segment's re.search does;
    # upgrade to a per-engine-run value if samples of several runs mix in a window
    slots = next(
        (int(row["expert_slots"]) for row in samples if row.get("expert_slots") is not None),
        None,
    )
    return {
        "median_tok_s": round(statistics.median(rates), 1) if rates else None,
        "accepted_share_percent": round(100 * accepted / total, 1) if total else None,
        "expert_slots": slots,
        "min_free_vram_mib": min(free) if free else None,
        "low_vram_count": sum(1 for row in samples if row.get("low_vram")),
        "samples_by_source": by_source,
    }
