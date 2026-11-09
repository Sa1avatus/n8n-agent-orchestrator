# Engine-log snapshot collector (board.engine_samples)

Prepared, NOT applied: nothing here was run. The operator applies the table, the compose
mount and the container restart; the code is already in the runner (`src/ahawr_runner/engine_collector.py`,
`src/ahawr_runner/engine_samples.py`, wired in `src/ahawr_runner/board_api.py:527`
`start_engine_collector` and `src/ahawr_runner/dashboard.py:219`, stopped by the same lifespan's
`finally` at `dashboard.py:223`; the start-up wiring is tested in `tests/test_engine_collector_wiring.py`
(flag off: no thread, unreachable database: the dashboard answers anyway and the reason is logged,
client exit: the thread ends).

## What it does

A guard-style watcher inside the dashboard container, started only when the `stats_engine_sampler`
feature flag is on (`features_config.FEATURE_DEFAULTS`, off by default). Every poll reads the Strata
engine log from its own byte offset, parses the lines of
`data-tables/ops/bench_series_table.py::engine_segment` through `engine_log.parse_line`, stamps each
batch with the collector's own UTC time (the engine log has no timestamps) and inserts one row per
sample into `board.engine_samples` with source `sampled`. The rows are idempotent on `sample_key`,
so a retried batch cannot duplicate a sample. The loop exits on the stop event (the dashboard
shutdown) or when the database retries of one batch are spent.

With the flag off nothing runs and the responses stay byte-for-byte the same (the Engine tab of the
statistics popup reports `no samples`).

The engine log lives inside the ahawr-strata container at
`/data/state/strata/launch/docker.engine.log`, volume `docker-live_strata-data`; the dashboard can
see it only through the read-only mount below.

## Table (operator step)

The DDL is in `docs/board-schema.sql:167-185` (`CREATE TABLE IF NOT EXISTS`, idempotent, with the
`engine_samples_sample_key_idx` unique index and the `engine_samples_sampled_at_idx` index):

```
docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 < ahawr-runner/docs/board-schema.sql
```

Rollback (drops only the new table, nothing else is touched):

```
docker exec -i ahawr-postgres psql -U n8n -d n8n -c "DROP TABLE IF EXISTS board.engine_samples;"
```

The QA stack applies `docs/board-schema.sql` to its own temporary database automatically
(`src/ahawr_runner/qa_stack.py:53,144`), so the QA gate needs no extra step.

## Compose mount (operator step, patch not applied)

`docs/stats-collector-compose.patch` is a unified diff against the live `docker-compose.yml` (the
ahawr-dashboard service). It adds the collector settings to the environment, one read-only volume
mount `docker-live_strata-data:/strata-data:ro`, and the `external: true` declaration of that
volume at the end of the `volumes:` section:

```
patch -p1 --dry-run < ahawr-runner/docs/stats-collector-compose.patch   # then: patch -p1 < ...
```

The patch was dry-run against a copy of the live compose file (`patch -p1 --dry-run`, exit 0) and
every hunk's anchor and context lines were compared line by line against the current file. No YAML
parser is installed in the runner venv, so the patched file was not parsed, only checked by `patch`.
Rollback: `git checkout docker-compose.yml` on the host, or remove the added mount line and its
`external: true` declaration, plus the five `DASHBOARD_ENGINE_*` environment lines.

## Settings (container environment, all bounded)

| Variable | Default | Meaning |
| --- | --- | --- |
| `DASHBOARD_ENGINE_LOG_PATH` | `/strata-data/state/strata/launch/docker.engine.log` | the mounted engine log |
| `DASHBOARD_ENGINE_POLL_SECONDS` | `60` | seconds between polls |
| `DASHBOARD_ENGINE_MAX_BATCH_LINES` | `200` | complete lines one poll may consume |
| `DASHBOARD_ENGINE_MAX_DB_RETRIES` | `3` | retries of a failing batch before the loop exits |
| `DASHBOARD_ENGINE_BACKOFF_SECONDS` | `5` | seconds between retries |

A non-number or a non-positive value keeps the default and logs the offending value
(`engine_collector.py:_positive_number`). The path is a setting, never a literal in the loop.

## Enabling

1. Apply the DDL and the compose patch, recreate `ahawr-dashboard` (the runner asks the operator
    through `POST /v1/host-actions`, it never runs docker itself).
2. Turn on the flag in the board settings (`features_json`): `stats_popup` for the popup itself,
    `stats_engine_sampler` for the collector.
3. Check the dashboard log: `engine collector stopped: offset <n>, engine_run <m>` appears when the
    process ends; a missing or unreadable log file is a warning, not a crash.

## What the Engine tab shows

`engine_window_figures` (`src/ahawr_runner/engine_samples.py`) reads a window of rows and computes
the median generation tok/s of replies with at least `MIN_GENERATED_TOKENS` (50, the reference value
of `engine_segment`), the share of accepted drafts, the expert cache slots, the minimum free VRAM in
MiB and the count of low-VRAM warnings. An empty window returns `null` with the reason `no samples`,
never zeros, and the per-source counts (`sampled`, `backfill-matched`, `backfill-segment`) are
reported so a mixed window is visible.
