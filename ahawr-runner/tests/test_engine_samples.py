"""Store and figures for board.engine_samples (ahawr_runner/engine_samples.py):
the idempotent insert on sample_key, the window query and the pure
engine_window_figures (median tok/s with the MIN_GENERATED_TOKENS filter, the
accepted draft share, expert slots, minimum free VRAM, the low-VRAM count and
the per-source counts). The store is a sqlite-backed fake connection: no real
database is touched. The figures mirror engine_segment in
data-tables/ops/bench_series_table.py."""

import sqlite3
from pathlib import Path
from typing import Any

from ahawr_runner.engine_samples import (
    MIN_GENERATED_TOKENS,
    NO_SAMPLES_REASON,
    engine_window,
    engine_window_figures,
    insert_sample,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "docs" / "board-schema.sql"


class FakeBoardConn:
    """A sqlite3 connection exposing the psycopg-style execute the store uses:
    %s placeholders become sqlite's ?. shortcut: the table and its unique index
    are mirrored by hand from docs/board-schema.sql for tests only."""

    def __init__(self) -> None:
        # check_same_thread is off: a route handler touches the connection from the
        # runner's threadpool, not from the thread that built the fake store
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("ATTACH DATABASE ':memory:' AS board")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS board.engine_samples ("
            "sampled_at text, engine_run integer, generated_tokens integer, "
            "tok_per_s real, drafts_accepted integer, drafts_total integer, "
            "expert_slots integer, vram_free_mib integer, low_vram integer, "
            "source text NOT NULL DEFAULT 'sampled', sample_key text UNIQUE)"
        )
        # sqlite has no schema qualifier for CREATE INDEX: the UNIQUE column
        # above is the unique index ON sample_key the real DDL creates

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        return self._conn.execute(sql.replace("%s", "?"), params)


def _sample(
    sampled_at: str,
    *,
    generated_tokens: int = 60,
    tok_per_s: float | None = 10.0,
    drafts_accepted: int = 0,
    drafts_total: int = 0,
    expert_slots: int | None = None,
    vram_free_mib: int | None = None,
    low_vram: bool = False,
    source: str = "sampled",
    sample_key: str,
    engine_run: int = 1,
) -> dict[str, Any]:
    """One row of the fake store, shaped like a window query result."""
    return {
        "sampled_at": sampled_at,
        "engine_run": engine_run,
        "generated_tokens": generated_tokens,
        "tok_per_s": tok_per_s,
        "drafts_accepted": drafts_accepted,
        "drafts_total": drafts_total,
        "expert_slots": expert_slots,
        "vram_free_mib": vram_free_mib,
        "low_vram": low_vram,
        "source": source,
        "sample_key": sample_key,
    }


def test_median_only_replies_with_at_least_min_generated_tokens() -> None:
    samples = [
        _sample(
            "2026-11-08 10:00:00", generated_tokens=MIN_GENERATED_TOKENS - 1,
            tok_per_s=1.0, sample_key="a",
        ),
        _sample(
            "2026-11-08 10:01:00", generated_tokens=MIN_GENERATED_TOKENS,
            tok_per_s=10.0, sample_key="b",
        ),
        _sample("2026-11-08 10:02:00", generated_tokens=80, tok_per_s=20.0, sample_key="c"),
    ]
    figures = engine_window_figures(samples)
    assert figures is not None
    assert figures["median_tok_s"] == 15.0  # the 1.0 tok/s reply stays out of the median


def test_empty_window_is_none_with_no_samples_reason() -> None:
    assert engine_window_figures([]) is None
    assert NO_SAMPLES_REASON == "no samples"


def test_accepted_share_min_vram_and_low_vram_count() -> None:
    samples = [
        _sample(
            "2026-11-08 10:00:00", drafts_accepted=30, drafts_total=100,
            vram_free_mib=900, low_vram=False, sample_key="a",
        ),
        _sample(
            "2026-11-08 10:01:00", drafts_accepted=10, drafts_total=100,
            vram_free_mib=120, low_vram=True, sample_key="b",
        ),
        _sample(
            "2026-11-08 10:02:00", drafts_accepted=0, drafts_total=0,
            vram_free_mib=400, low_vram=True, sample_key="c",
        ),
    ]
    figures = engine_window_figures(samples)
    assert figures is not None
    assert figures["accepted_share_percent"] == 20.0  # (30 + 10 + 0) of (100 + 100 + 0)
    assert figures["min_free_vram_mib"] == 120
    assert figures["low_vram_count"] == 2


def test_expert_slots_and_mixed_sources_reported_separately() -> None:
    samples = [
        _sample("2026-11-08 10:00:00", expert_slots=None, sample_key="a"),
        _sample("2026-11-08 10:01:00", expert_slots=7, source="backfill-segment", sample_key="b"),
        _sample("2026-11-08 10:02:00", expert_slots=9, source="backfill-segment", sample_key="c"),
        _sample("2026-11-08 10:03:00", expert_slots=11, source="backfill-matched", sample_key="d"),
        _sample("2026-11-08 10:04:00", sample_key="e"),
        _sample("2026-11-08 10:05:00", sample_key="f"),
    ]
    figures = engine_window_figures(samples)
    assert figures is not None
    assert figures["expert_slots"] == 7  # the first reported value, like engine_segment's re.search
    assert figures["samples_by_source"] == {
        "sampled": 3,
        "backfill-segment": 2,
        "backfill-matched": 1,
    }


def test_insert_is_idempotent_on_sample_key() -> None:
    conn = FakeBoardConn()
    args: dict[str, Any] = dict(
        sampled_at="2026-11-08 10:00:00", engine_run=1, generated_tokens=60,
        tok_per_s=12.5, drafts_accepted=10, drafts_total=20, expert_slots=4,
        vram_free_mib=800, low_vram=False, sample_key="run1-line1",
    )
    insert_sample(conn, **args)
    insert_sample(conn, **args)  # the same sample again: the conflict is a no-op
    insert_sample(conn, **dict(args, sample_key="run1-line2", source="backfill-segment"))
    row = conn.execute(
        "SELECT count(*) AS n FROM board.engine_samples"
    ).fetchall()[0]
    assert row["n"] == 2


def test_window_query_returns_only_rows_in_window_in_order() -> None:
    conn = FakeBoardConn()
    insert_sample(
        conn, sampled_at="2026-11-07 23:59:59", engine_run=1, generated_tokens=60,
        tok_per_s=9.0, drafts_accepted=0, drafts_total=0, expert_slots=None,
        vram_free_mib=None, low_vram=False, sample_key="before",
    )
    insert_sample(
        conn, sampled_at="2026-11-08 12:00:00", engine_run=2, generated_tokens=60,
        tok_per_s=11.0, drafts_accepted=5, drafts_total=10, expert_slots=3,
        vram_free_mib=500, low_vram=True, sample_key="inside",
    )
    insert_sample(
        conn, sampled_at="2026-11-09 00:00:00", engine_run=3, generated_tokens=60,
        tok_per_s=13.0, drafts_accepted=0, drafts_total=0, expert_slots=None,
        vram_free_mib=None, low_vram=False, sample_key="after",
    )
    rows = engine_window(conn, "2026-11-08 00:00:00", "2026-11-09 00:00:00")
    assert [row["sample_key"] for row in rows] == ["inside"]
    figures = engine_window_figures(rows)
    assert figures is not None
    assert figures["median_tok_s"] == 11.0
    assert figures["accepted_share_percent"] == 50.0
    assert figures["low_vram_count"] == 1
    assert engine_window(conn, "2026-11-10 00:00:00", "2026-11-11 00:00:00") == []


def test_schema_ddl_for_engine_samples_is_only_if_not_exists() -> None:
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    engine_create_lines = [
        line
        for line in schema_text.splitlines()
        if "engine_samples" in line and line.lstrip().startswith("CREATE")
    ]
    assert engine_create_lines, "board-schema.sql has no engine_samples DDL"
    assert all("IF NOT EXISTS" in line for line in engine_create_lines)
    # one space per gap: the DDL pads the column names for alignment
    ddl_text = " ".join(schema_text.split())
    for column in (
        "CREATE TABLE IF NOT EXISTS board.engine_samples",
        "sampled_at timestamptz",
        "engine_run integer",
        "generated_tokens integer",
        "tok_per_s double precision",
        "drafts_accepted integer",
        "drafts_total integer",
        "expert_slots integer",
        "vram_free_mib integer",
        "low_vram boolean",
        "source text NOT NULL DEFAULT 'sampled'",
        "sample_key text",
    ):
        assert column in ddl_text, f"missing DDL piece: {column}"
    assert "CREATE UNIQUE INDEX IF NOT EXISTS engine_samples_sample_key_idx" in ddl_text
    assert "CREATE INDEX IF NOT EXISTS engine_samples_sampled_at_idx" in ddl_text
