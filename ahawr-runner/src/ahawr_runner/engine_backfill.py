"""One-off backfill of a past engine log snapshot into ``board.engine_samples``.

    python -m ahawr_runner.engine_backfill --dry-run --log <snapshot> \
        --segments <map> [--runs <export>]
    python -m ahawr_runner.engine_backfill --apply --dsn <postgres dsn> --log <snapshot> \
        --segments <map> --runs <export>

The log is split at every ``engine started:`` line with ``engine_log.RESTART_RE``, the
same split as ``_engine_segment`` in ``data-tables/ops/bench_series_table.py``. The
segment map records ``n - 1`` for the n-th engine start (``record_segment`` in the bench
series scripts), so the stored ``engine_run`` of a mapped segment is index + 1.

(a) Bench missions: the setting a mission name carries ('bench-...-q20-<setting>') is
looked up in the segment map; every sample of those segments becomes a row with source
``backfill-segment``.
(b) Other missions: every Worker run request of the stored-runs export is looked up in
the whole log by the pair (output tokens, generation ms). A pair that appears more than
once is ambiguous and writes nothing, so only unique matches become rows with source
``backfill-matched``.
(c) Unmapped segments and unmatched requests get no rows.

Timestamps: matched rows take the matched run's ``started_at``; segment rows take the
mission window start (the earliest ``started_at`` of the mission's Worker runs), because
the engine log carries no timestamps.

``sample_key`` is a sha1 of the source, the segment and the line position (or the mission,
run id and pair), so re-running the script on the same snapshot writes nothing new:
``insert_sample`` is ``ON CONFLICT (sample_key) DO NOTHING``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .engine_collector import _sample_fields
from .engine_log import RESTART_RE, Generation, parse_line
from .engine_samples import insert_sample

SOURCE_SEGMENT = "backfill-segment"
SOURCE_MATCHED = "backfill-matched"
# bench missions carry their setting in the name: bench-<kind>-strata-q20-<setting>
# (the SETTINGS list of data-tables/ops/bench_series_table.py)
SETTING_MARK = "-q20-"
WORKER_ROLE = "worker"
# record_segment in the bench series scripts writes (restart count - 1) for a start, so
# a mapped index is one less than the engine_run the collector gives that segment
SEGMENT_INDEX_OFFSET = 1
# the text before the first 'engine started:' line: not a bench segment, never written
LEAD_SEGMENT = 0
MAX_LOG_READ_LINES = 200_000  # shortcut: the staged snapshot has 119,543 lines; stop if a
# log ever grows past this instead of building an unbounded sample list


@dataclass(frozen=True)
class SegmentSample:
    """One parsed engine-log sample, tagged with its segment and its stable key."""

    segment: int
    sample_key: str
    fields: dict[str, Any]
    pair: tuple[int, int] | None


@dataclass(frozen=True)
class RunRequest:
    """One request of a stored Worker run: its output tokens and generation ms."""

    mission: str
    run_id: str
    started_at: str
    output_tokens: int
    generation_ms: int


@dataclass(frozen=True)
class BackfillRow:
    """One row the backfill writes into board.engine_samples."""

    mission: str
    source: str
    sample_key: str
    sampled_at: str
    engine_run: int | None
    fields: dict[str, Any]


def segment_sample_key(segment: int, line_index: int, sample_index: int, line: str) -> str:
    """sha1 of the segment and of the line's position in the snapshot: the same file
    gives the same keys on every run, which is what makes the insert idempotent."""
    return hashlib.sha1(
        f"{SOURCE_SEGMENT}|{segment}|{line_index}|{sample_index}|{line}".encode()
    ).hexdigest()


def matched_sample_key(mission: str, run_id: str, tokens: int, gen_ms: int) -> str:
    """The key of a row matched to one stored run request."""
    return hashlib.sha1(
        f"{SOURCE_MATCHED}|{mission}|{run_id}|{tokens}|{gen_ms}".encode()
    ).hexdigest()


def load_segment_map(path: Path) -> dict[str, list[int]]:
    """'<label> <segment index>' lines -> the unique indices of each label in file
    order, the same reading as ``segment_index`` in bench_series_table.py."""
    mapping: dict[str, list[int]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        index = int(parts[1])
        if index not in mapping.setdefault(parts[0], []):
            mapping[parts[0]] = [*mapping[parts[0]], index]
    return mapping


def read_samples(path: Path) -> list[SegmentSample]:
    """Every sample of the log with its segment number: segment n is the text after the
    n-th 'engine started:' line. The restart line opens the segment and is not a sample,
    and the text before the first restart (segment 0) belongs to no bench setting."""
    samples: list[SegmentSample] = []
    segment = LEAD_SEGMENT
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_index, raw_line in enumerate(handle, start=1):
            if line_index > MAX_LOG_READ_LINES:
                print(
                    f"engine log {path}: more than {MAX_LOG_READ_LINES} lines, "
                    "the backfill stops here",
                    file=sys.stderr,
                )
                break
            line = raw_line.rstrip("\n")
            if RESTART_RE.search(line):
                segment += 1
                continue
            for sample_index, sample in enumerate(parse_line(line)):
                # the pair is the request itself: only a 'generated in ... ms' sample
                # carries it, the other samples of the same line match no request
                pair = (sample.tokens, sample.gen_ms) if isinstance(sample, Generation) else None
                samples.append(
                    SegmentSample(
                        segment=segment,
                        sample_key=segment_sample_key(segment, line_index, sample_index, line),
                        fields=_sample_fields(sample),
                        pair=pair,
                    )
                )
    return samples


def load_runs(path: Path) -> dict[str, list[dict[str, Any]]]:
    """mission -> its stored runs, from the export the runner's ``_pub_helper.py``
    writes: {mission: {"stats": ..., "runs": [...], "by_role": ...}}."""
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the runs export must be an object keyed by mission")
    runs_by_mission: dict[str, list[dict[str, Any]]] = {}
    for mission, payload in data.items():
        if isinstance(payload, list):
            runs = payload
        elif isinstance(payload, dict):
            runs = payload.get("runs", [])
        else:
            continue
        runs_by_mission[mission] = [row for row in runs if isinstance(row, dict)]
    return runs_by_mission


def run_requests(runs: list[dict[str, Any]], mission: str) -> list[RunRequest]:
    """The requests of Worker runs: one per step when the run stores steps, otherwise
    the run's aggregate (output tokens, generation ms) pair."""
    requests: list[RunRequest] = []
    for run in runs:
        if run.get("role") != WORKER_ROLE:
            continue
        run_id = str(run.get("run") or run.get("run_id") or "")
        started_at = str(run.get("started_at") or "")
        steps = run.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                tokens = step.get("output_tokens")
                gen_ms = step.get("generation_ms")
                if tokens and gen_ms:
                    requests.append(
                        RunRequest(mission, run_id, started_at, int(tokens), int(gen_ms))
                    )
        elif run.get("output_tokens") and run.get("generation_ms"):
            requests.append(
                RunRequest(
                    mission,
                    run_id,
                    started_at,
                    int(run["output_tokens"]),
                    int(run["generation_ms"]),
                )
            )
    return requests


def setting_of(mission: str) -> str | None:
    """The bench setting a mission name carries, None for a non-bench mission."""
    if SETTING_MARK not in mission:
        return None
    return mission.rsplit(SETTING_MARK, 1)[1]


def mission_start(runs: list[dict[str, Any]]) -> str | None:
    """The earliest started_at of the mission's stored runs, None when none is present."""
    starts = [str(run["started_at"]) for run in runs if run.get("started_at")]
    return min(starts) if starts else None


def pair_index(samples: list[SegmentSample]) -> dict[tuple[int, int], list[SegmentSample]]:
    """(output tokens, generation ms) -> the log samples carrying that pair."""
    index: dict[tuple[int, int], list[SegmentSample]] = {}
    for sample in samples:
        if sample.pair is not None:
            index.setdefault(sample.pair, []).append(sample)
    return index


def false_match_risk(index: dict[tuple[int, int], list[SegmentSample]]) -> int:
    """How many log requests share their (tokens, ms) pair with another line: those can
    never be matched to a run without a wrong pick, so the backfill skips them."""
    return sum(len(items) for items in index.values() if len(items) > 1)


def build_rows(
    samples: list[SegmentSample],
    segment_map: dict[str, list[int]],
    runs_by_mission: dict[str, list[dict[str, Any]]],
    pair_index: dict[tuple[int, int], list[SegmentSample]],
) -> list[BackfillRow]:
    """Segment rows for the bench missions whose setting the map covers, matched rows
    for every other mission in the runs export. Unmapped segments and requests with no
    unique pair in the log write nothing."""
    rows: list[BackfillRow] = []
    for mission, runs in runs_by_mission.items():
        setting = setting_of(mission)
        if setting in segment_map:
            window_start = mission_start(runs)
            if window_start is None:
                # segment rows need a timestamp: a mission with no stored window has
                # nothing to stamp them with, so it stays without an engine block
                continue
            wanted = {index_offset + SEGMENT_INDEX_OFFSET for index_offset in segment_map[setting]}
            for sample in samples:
                if sample.segment in wanted:
                    rows.append(
                        BackfillRow(
                            mission=mission,
                            source=SOURCE_SEGMENT,
                            sample_key=sample.sample_key,
                            sampled_at=window_start,
                            engine_run=sample.segment,
                            fields=sample.fields,
                        )
                    )
            continue
        for request in run_requests(runs, mission):
            matches = pair_index.get((request.output_tokens, request.generation_ms), [])
            if len(matches) != 1:
                continue  # absent or ambiguous: never a wrong pick
            sample = matches[0]
            rows.append(
                BackfillRow(
                    mission=mission,
                    source=SOURCE_MATCHED,
                    sample_key=matched_sample_key(
                        mission, request.run_id, request.output_tokens, request.generation_ms
                    ),
                    sampled_at=request.started_at,
                    # segment 0 is the text before the first restart: no engine run
                    engine_run=sample.segment or None,
                    fields=sample.fields,
                )
            )
    return rows


def coverage_table(
    segment_map: dict[str, list[int]],
    runs_by_mission: dict[str, list[dict[str, Any]]],
    rows: list[BackfillRow],
) -> list[dict[str, Any]]:
    """Per mission: the stored Worker run requests and the rows the backfill wrote,
    tagged with the source the rows carry."""
    written: dict[str, int] = {}
    for row in rows:
        written[row.mission] = written.get(row.mission, 0) + 1
    table: list[dict[str, Any]] = []
    for mission, runs in runs_by_mission.items():
        setting = setting_of(mission)
        source = SOURCE_SEGMENT if setting in segment_map else SOURCE_MATCHED
        table.append(
            {
                "mission": mission,
                "source": source,
                "run_requests": len(run_requests(runs, mission)),
                "rows": written.get(mission, 0),
            }
        )
    return table


def setting_coverage(
    samples: list[SegmentSample], segment_map: dict[str, list[int]]
) -> list[dict[str, Any]]:
    """For every setting of the segment map: how many segments it names and how many
    log samples fall in them (used when no runs export gives the mission names)."""
    table: list[dict[str, Any]] = []
    for setting, indexes in segment_map.items():
        wanted = {index + SEGMENT_INDEX_OFFSET for index in indexes}
        table.append(
            {
                "setting": setting,
                "segments": len(indexes),
                "samples": sum(1 for sample in samples if sample.segment in wanted),
            }
        )
    return table


def apply_rows(conn: Any, rows: list[BackfillRow]) -> int:
    """Write the rows through engine_samples.insert_sample: the unique sample_key makes
    a second run over the same snapshot write nothing."""
    for row in rows:
        insert_sample(
            conn,
            sampled_at=row.sampled_at,
            engine_run=row.engine_run,
            generated_tokens=row.fields["generated_tokens"],
            tok_per_s=row.fields["tok_per_s"],
            drafts_accepted=row.fields["drafts_accepted"],
            drafts_total=row.fields["drafts_total"],
            expert_slots=row.fields["expert_slots"],
            vram_free_mib=row.fields["vram_free_mib"],
            low_vram=row.fields["low_vram"],
            sample_key=row.sample_key,
            source=row.source,
        )
    return len(rows)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill engine samples from an engine log snapshot."
    )
    parser.add_argument("--log", required=True, help="the engine log snapshot to read")
    parser.add_argument(
        "--segments", required=True, help="the '<label> <segment index>' setting map"
    )
    parser.add_argument(
        "--runs",
        help="export of the stored Worker runs, mission -> {'runs': [...]}",
    )
    parser.add_argument("--dsn", help="postgres DSN, needed only with --apply")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the coverage, write nothing")
    mode.add_argument("--apply", action="store_true", help="insert the rows (idempotent)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    log_path = Path(args.log)
    segments_path = Path(args.segments)
    for path in (log_path, segments_path):
        if not path.is_file():
            print(f"missing input: {path}", file=sys.stderr)
            return 1
    if args.apply and not args.dsn:
        print("--apply needs --dsn (the board postgres DSN)", file=sys.stderr)
        return 1

    samples = read_samples(log_path)
    index = pair_index(samples)
    segment_map = load_segment_map(segments_path)
    runs_by_mission: dict[str, list[dict[str, Any]]] = {}
    if args.runs:
        try:
            runs_by_mission = load_runs(Path(args.runs))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"unreadable runs export {args.runs}: {exc}", file=sys.stderr)
            return 1

    rows = build_rows(samples, segment_map, runs_by_mission, index)
    generation_requests = sum(1 for sample in samples if sample.pair is not None)
    print(
        f"engine log {log_path}: {len(samples)} samples, {generation_requests} "
        f"generation requests, {false_match_risk(index)} of them share a (tokens, ms) pair"
    )
    if runs_by_mission:
        print("mission\tsource\trun requests\trows written")
        for line in coverage_table(segment_map, runs_by_mission, rows):
            print(f"{line['mission']}\t{line['source']}\t{line['run_requests']}\t{line['rows']}")
    else:
        print(
            "no --runs export: the per-mission coverage needs the stored Worker runs "
            "(an operator export from the runner database); the settings are covered "
            "straight from the segment map"
        )
        print("setting\tsegments\tsamples in those segments")
        for line in setting_coverage(samples, segment_map):
            print(f"{line['setting']}\t{line['segments']}\t{line['samples']}")
    print(f"rows the backfill writes: {len(rows)}")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    import psycopg  # imported only for --apply; a --dry-run needs no database

    with psycopg.connect(args.dsn) as conn:
        written = apply_rows(conn, rows)
    print(f"applied {written} rows (a rerun of the same log writes nothing new)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
