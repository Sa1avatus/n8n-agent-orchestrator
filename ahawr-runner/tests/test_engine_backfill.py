"""Engine log backfill (ahawr_runner/engine_backfill.py).

Fixtures use the real line shapes of the snapshot: the restart marker 'engine started:',
the 'N generated in M ms (X tok/s), drafts accepted A of T' request line, the expert cache
slots line and the 'only N MiB of VRAM free' warning. Covered: the segment split by
RESTART_RE with the n-1 index offset of the segment map, unique (output tokens, ms) pairs
matching a stored Worker run while an ambiguous or absent pair writes nothing, the two
sources, the false-match count, idempotence of a rerun on the sample_key, and the missing
input and missing --dsn guards. No real database: the sqlite fake connection of
test_engine_samples records the inserts.
"""

import json
from pathlib import Path
from typing import Any

from ahawr_runner.engine_backfill import (
    SOURCE_MATCHED,
    SOURCE_SEGMENT,
    apply_rows,
    build_rows,
    coverage_table,
    false_match_risk,
    load_runs,
    load_segment_map,
    main,
    pair_index,
    read_samples,
    setting_of,
)
from tests.test_engine_samples import FakeBoardConn

LOG_TEXT = (
    "warmup 42 generated in 500 ms (0.5 tok/s)\n"
    "engine started: strata serve model=kv-q4\n"
    "strata serve: prompt 4216 tokens = 0 reused + 4216 read in 124680 ms (33.8 tok/s), "
    "136 generated in 12712 ms (10.7 tok/s), drafts accepted 87 of 113, 1 checkpoints\n"
    "engine started: strata serve model=kv-int8\n"
    "60 generated in 300 ms (2.0 tok/s)\n"
    "60 generated in 300 ms (2.0 tok/s)\n"
    "80 generated in 400 ms (1.0 tok/s)\n"
    "expert cache 8192 slots\n"
    "only 128 MiB of VRAM free\n"
)
SEGMENTS_TEXT = "kv-q4 0\nkv-int8 1\nprior-tail 5\n"

BENCH_MISSION = "bench-mistral-strata-q20-kv-int8"
OTHER_MISSION = "audio-dub-dashboard"
UNMAPPED_MISSION = "bench-mistral-strata-q20-not-in-map"

RUNS_EXPORT: dict[str, Any] = {
    BENCH_MISSION: {
        "stats": {},
        "runs": [
            {
                "run": "b1",
                "role": "worker",
                "started_at": "2026-11-06 08:00:00",
                "output_tokens": 60,
                "generation_ms": 300,
                "steps": 2,
            }
        ],
        "by_role": {"worker": 1},
    },
    OTHER_MISSION: {
        "stats": {},
        "runs": [
            {
                "run": "r1",
                "role": "worker",
                "started_at": "2026-11-04 10:00:00",
                "output_tokens": 80,
                "generation_ms": 400,
                "steps": 3,
            },
            {
                "run": "r2",
                "role": "worker",
                "started_at": "2026-11-04 11:00:00",
                "output_tokens": 60,
                "generation_ms": 300,
                "steps": 1,
            },
            {
                "run": "r3",
                "role": "worker",
                "started_at": "2026-11-04 12:00:00",
                "output_tokens": 1234,
                "generation_ms": 9999,
                "steps": 1,
            },
            {
                "run": "r4",
                "role": "reviewer",
                "started_at": "2026-11-04 13:00:00",
                "output_tokens": 80,
                "generation_ms": 400,
                "steps": 1,
            },
            {
                "run": "r5",
                "role": "worker",
                "started_at": "2026-11-04 14:00:00",
                "steps": [
                    {"output_tokens": 136, "generation_ms": 12712},
                    {"output_tokens": 60, "generation_ms": 300},
                ],
            },
        ],
        "by_role": {"worker": 4, "reviewer": 1},
    },
    UNMAPPED_MISSION: {
        "runs": [{"run": "u1", "role": "worker", "started_at": "2026-11-05 00:00:00"}]
    },
}


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    """The staged snapshot, the segment map and the runs export of a fixture."""
    log_path = tmp_path / "engine.log"
    log_path.write_text(LOG_TEXT)
    segments_path = tmp_path / "segments.txt"
    segments_path.write_text(SEGMENTS_TEXT)
    runs_path = tmp_path / "runs.json"
    runs_path.write_text(json.dumps(RUNS_EXPORT))
    return log_path, segments_path, runs_path


def _rows(tmp_path: Path) -> list[Any]:
    """The rows the fixture writes: the segment branch for the bench mission, the
    matched branch for every other mission of the export."""
    log_path, segments_path, runs_path = _fixture(tmp_path)
    samples = read_samples(log_path)
    segment_map = load_segment_map(segments_path)
    runs_by_mission = load_runs(runs_path)
    return build_rows(samples, segment_map, runs_by_mission, pair_index(samples))


def test_read_samples_tags_each_sample_with_its_segment(tmp_path: Path) -> None:
    log_path, _, _ = _fixture(tmp_path)
    samples = read_samples(log_path)
    # the restart marker opens a segment and is not a sample of it; the warmup line
    # before the first restart is segment 0
    assert sorted({sample.segment for sample in samples}) == [0, 1, 2]
    # the 'only 128 MiB of VRAM free' line matches both the free and the low-VRAM
    # regexes of engine_log.py, so it yields two samples with no pair
    assert len(samples) == 9
    pairs = {sample.pair for sample in samples if sample.pair is not None}
    assert pairs == {(42, 500), (136, 12712), (60, 300), (80, 400)}
    assert len([sample for sample in samples if sample.pair == (60, 300)]) == 2
    # only the generation sample of the mixed line carries the pair, its drafts sample does not
    assert len([s for s in samples if s.pair == (136, 12712)]) == 1


def test_setting_of_reads_the_bench_setting_and_returns_none_for_others() -> None:
    assert setting_of(BENCH_MISSION) == "kv-int8"
    assert setting_of(OTHER_MISSION) is None
    assert setting_of("bench-a-q20-b-q20-c") == "c"


def test_only_unique_pair_matches_write_rows(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    matched = [row for row in rows if row.source == SOURCE_MATCHED]
    assert len(matched) == 2  # r1's unique pair and r5's first step
    assert {row.mission for row in matched} == {OTHER_MISSION}
    assert {row.engine_run for row in matched} == {1, 2}
    # r2 (ambiguous pair), r3 (no pair in the log) and the reviewer run write nothing
    assert len(rows) == 2 + 6


def test_a_request_before_the_first_restart_has_no_engine_run(tmp_path: Path) -> None:
    """The text before the first 'engine started:' line is segment 0: a match there
    writes a row, but it belongs to no engine run, so engine_run is None."""
    log_path = tmp_path / "lead.log"
    log_path.write_text("lead 42 generated in 500 ms (0.5 tok/s)\nengine started: strata serve\n")
    samples = read_samples(log_path)
    runs = {"lead-mission": [{"run": "l1", "role": "worker", "started_at": "2026-11-01 00:00:00",
                              "output_tokens": 42, "generation_ms": 500, "steps": 1}]}
    rows = build_rows(samples, {}, runs, pair_index(samples))  # no setting mapped
    assert len(rows) == 1
    assert rows[0].source == SOURCE_MATCHED
    assert rows[0].engine_run is None


def test_ambiguous_pairs_are_counted_as_false_match_risk(tmp_path: Path) -> None:
    log_path, _, _ = _fixture(tmp_path)
    samples = read_samples(log_path)
    assert false_match_risk(pair_index(samples)) == 2  # the two identical 60/300 requests


def test_segment_rows_use_the_map_index_plus_one_and_the_window_start(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    segment_rows = [row for row in rows if row.source == SOURCE_SEGMENT]
    assert len(segment_rows) == 6
    assert all(row.mission == BENCH_MISSION for row in segment_rows)
    # 'kv-int8 1' records the second engine start as index 1, so engine_run is 2
    assert {row.engine_run for row in segment_rows} == {2}
    assert {row.sampled_at for row in segment_rows} == {"2026-11-06 08:00:00"}
    assert all(row.engine_run != 0 for row in rows)  # segment 0 is never written


def test_unmapped_setting_and_requests_with_no_pair_get_no_rows(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    assert not [row for row in rows if row.mission == UNMAPPED_MISSION]
    # 'prior-tail 5' names a segment that is not in this log: it writes nothing
    assert not [row for row in rows if row.engine_run == 5]


def test_sample_keys_are_stable_when_the_log_is_read_twice(tmp_path: Path) -> None:
    log_path, _, _ = _fixture(tmp_path)
    first = {sample.sample_key for sample in read_samples(log_path)}
    second = {sample.sample_key for sample in read_samples(log_path)}
    assert first == second
    assert len(first) == 9


def test_rerunning_the_backfill_adds_no_rows(tmp_path: Path) -> None:
    rows = _rows(tmp_path)
    assert len(rows) == 8
    conn = FakeBoardConn()
    apply_rows(conn, rows)
    apply_rows(conn, rows)
    count = conn.execute("SELECT count(*) AS n FROM board.engine_samples").fetchall()[0]
    assert count["n"] == 8


def test_coverage_table_reports_each_mission_with_its_source(tmp_path: Path) -> None:
    log_path, segments_path, runs_path = _fixture(tmp_path)
    samples = read_samples(log_path)
    segment_map = load_segment_map(segments_path)
    runs_by_mission = load_runs(runs_path)
    rows = build_rows(samples, segment_map, runs_by_mission, pair_index(samples))
    table = coverage_table(segment_map, runs_by_mission, rows)
    by_mission = {line["mission"]: line for line in table}
    assert by_mission[BENCH_MISSION] == {
        "mission": BENCH_MISSION,
        "source": SOURCE_SEGMENT,
        "run_requests": 1,
        "rows": 6,
    }
    assert by_mission[OTHER_MISSION]["source"] == SOURCE_MATCHED
    assert by_mission[OTHER_MISSION]["run_requests"] == 5  # 3 aggregates + 2 steps of r5
    assert by_mission[OTHER_MISSION]["rows"] == 2
    assert by_mission[UNMAPPED_MISSION]["rows"] == 0


def test_dry_run_writes_nothing_and_reports_the_counts(tmp_path: Path, capsys: Any) -> None:
    log_path, segments_path, runs_path = _fixture(tmp_path)
    exit_code = main(
        [
            "--dry-run",
            "--log",
            str(log_path),
            "--segments",
            str(segments_path),
            "--runs",
            str(runs_path),
        ]
    )
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "5 generation requests" in out
    assert "2 of them share a (tokens, ms) pair" in out
    assert f"{BENCH_MISSION}\t{SOURCE_SEGMENT}\t1\t6" in out
    assert f"{OTHER_MISSION}\t{SOURCE_MATCHED}\t5\t2" in out
    assert "dry run: nothing written" in out


def test_missing_input_stops_with_the_checked_path(tmp_path: Path, capsys: Any) -> None:
    absent = tmp_path / "absent.log"
    exit_code = main(["--dry-run", "--log", str(absent), "--segments", "segments.txt"])
    assert exit_code == 1
    assert f"missing input: {absent}" in capsys.readouterr().err


def test_apply_without_a_dsn_is_rejected(tmp_path: Path, capsys: Any) -> None:
    log_path, segments_path, _ = _fixture(tmp_path)
    exit_code = main(["--apply", "--log", str(log_path), "--segments", str(segments_path)])
    assert exit_code == 1
    assert "--apply needs --dsn" in capsys.readouterr().err
