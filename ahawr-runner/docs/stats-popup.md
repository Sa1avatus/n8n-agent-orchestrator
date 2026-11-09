# Stats popup (bench figures per mission, task and project)

The popup behind the `stats_popup` flag shows the bench figures of a mission, a task
or a project: run-based metrics, per-task/per-attempt review rows, and an engine block
when the collector has samples. The routes are `GET /api/board/missions/{id}/stats/detail`,
`GET /api/board/missions/{id}/tasks/{key}/stats/detail` and `GET /api/board/projects/{key}/stats`
(`src/ahawr_runner/board_api.py:1660-1727`). The collector side is in
[stats-collector.md](stats-collector.md).

## Flags and rollback

Two feature flags, both off by default (`src/ahawr_runner/features_config.py:18-19`):

| Flag | Effect when on |
| --- | --- |
| `stats_popup` | the stats detail routes answer; with the flag off they raise `stats detail is not enabled (stats_popup off)` |
| `stats_engine_sampler` | the engine-log collector runs and the detail routes add the `engine` block (`board_api.py:1673,1698,1725`) |

Rollback: turn the flags off in the board settings (`features_json`, the same
diff-and-confirm step as the other settings). With the flags off nothing runs and the
responses are byte-for-byte the same; the only durable artifacts are the
`board.engine_samples` table and the compose mount, whose rollback is the `DROP TABLE`
statement in [stats-collector.md](stats-collector.md).

## Sources of the figures

- **Run ids:** the `board_stats` linkage (`_mission_run_ids`, `_task_run_ids`,
  `_attempt_runs`, imported in `stats_figures.py:20-26`). Run records live in the
  runner's sqlite store; the step entries behind `steps`, `output_tokens` and the
  generation time come from the run activity log (`kind == "step"`,
  `stats_figures.py:61-75`).
- **Review rows:** every `review_parsed` event of the mission state
  (`stats_figures.py:41-43`), grouped by (state key, task id) so the T001 of two
  missions of one project stays apart (`_reviews_by_task`, `stats_figures.py:46-58`).
- **Engine samples:** `board.engine_samples` (see [stats-collector.md](stats-collector.md));
  the window is the span of the scope's runs: earliest start to latest finish
  (`board_api.py:106-118`).

## Run-based metrics (`run_figures`, `stats_figures.py:78-173`)

Formulas as in `data-tables/ops/bench_series_table.py`'s `mission_row`. A ratio that
cannot be computed is `null` with its reason in `reasons`; sums over an empty scope are
a real 0.

| Metric | Formula | Where from |
| --- | --- | --- |
| `closed_tasks` | reviewed tasks whose last row has `review_status == "pass"` (line 117) | review rows |
| `planned_tasks` | number of tasks in the mission's live plan; falls back to the reviewed-task count, else `null` with reason `no plan and no review rows` (lines 118-120) | live plan |
| `avg_final_review_score` / `avg_first_review_score` | mean of `review_score` of the last / first row per task, rounded to 1; `null` reason `no review scores` (lines 102-116) | review rows |
| `extra_attempts` | total review rows minus the task count; `null` reason `no reviewed tasks` (lines 128-132) | review rows |
| `worker_hours` | role minutes from `stats_for_runs` (`mission_stats.py:67`) | stored runs |
| `minutes_per_task` | `worker_hours * 60 / reviewed tasks`, rounded to 1 (lines 134-136) | runs + reviews |
| `steps` | Worker activity entries with `kind == "step"` (lines 61-75) | activity log |
| `steps_per_task` | `steps / reviewed tasks`; `null` reason `no reviewed tasks` (line 138) | activity log + reviews |
| `output_tokens` | sum of `output_tokens` over those step entries (line 73) | activity log |
| `tokens_per_step` | `output_tokens / steps`; `null` reason `no Worker steps` (line 140) | activity log |
| `tok_per_s` | `stats_for_runs`' `tok_per_s` when some Worker step measured its generation time, else `null` reason `no Worker steps with measured generation time` (lines 141-145) | stored runs |
| `cache_share_percent` | `100 * stats["cache_ratio"]` rounded to 1, only when `input + cache_read` tokens exist, else `null` reason `no step input tokens` (lines 146-150) | stored runs |
| `generation_share_percent` | generation seconds / `worker_hours * SECONDS_PER_HOUR` (3600), rounded to 1; `null` reason `no Worker time` (lines 151-156) | activity log + runs |
| `compactions` | `stats_for_runs`' `compactions`, counted through the session linkage (`mission_stats.py:29-39`) | stored runs |
| `reviewer_minutes` | `stats_for_runs`' `reviewer_minutes` | stored runs |
| `cost_usd` | `(input*in + output*out + cache_read*read + cache_write*write) / 1e6`, rounded to 2 (lines 162-171) | token totals |

The cost uses the runner's local pricing Sonnet profile, else the named constant
`SONNET_PRICE_USD_PER_MILLION_TOKENS = (2.0, 10.0, 0.2, 4.0)` USD per 1M tokens
(input, output, cache read, cache write) — `stats_figures.py:30-38`. Shortcut: every
run of the scope is priced as Sonnet, as the bench table does; per-run profiles are the
upgrade path (`stats_figures.py:159-161`).

## Run retention limit

Run records stay in the runner's sqlite store (no pruning found in the runner code: no
`DELETE` touches the runs table), but the activity log files the step figures are read
from are pruned after `CLAUDE_RUNNER_EVENT_RETENTION_DAYS` days, default **14**
(`config.py:759,905`; the pruning in `events.py:321-326`; README: "Claude Code activity
is kept ... for CLAUDE_RUNNER_EVENT_RETENTION_DAYS days (default 14)"). So the
run-based metrics show for past missions only while their activity logs are inside that
window; older missions lose the `steps`, `output_tokens` and generation-time figures.

## Engine metrics (`engine_window_figures`, `engine_samples.py:95-126`)

Same reference as `engine_segment` in `bench_series_table.py`; the parsed line shapes
are in `engine_log.py:20-24`:

- `median_tok_s`: median of `tok_per_s` over samples with at least
  `MIN_GENERATED_TOKENS = 50` generated tokens (`engine_samples.py:30,100-105`),
  rounded to 1; `null` when no sample qualifies.
- `accepted_share_percent`: `100 * drafts accepted / drafts total`, rounded to 1;
  `null` when the window carries no draft counts.
- `expert_slots`: the first reported `expert cache <n> slots` value (shortcut: like
  `engine_segment`'s first `re.search`; upgrade per engine run if a window mixes runs,
  `engine_samples.py:113-118`).
- `min_free_vram_mib`: minimum non-null `vram_free_mib` in the window.
- `low_vram_count`: samples flagged `low_vram`.
- `samples_by_source`: per-source counts, so a mixed window never mixes silently.

An empty window gives `engine: null` with `engine_reason: "no samples"`
(`NO_SAMPLES_REASON`, `engine_samples.py:31`; `board_api.py:130-142`), never zeros.

### Limits of the engine block

- Only the periods when the collector actually ran are covered: it polls every
  `DASHBOARD_ENGINE_POLL_SECONDS` (default 60) while `stats_engine_sampler` is on. A
  window that spans downtime contributes no samples there.
- The engine log has no timestamps: the collector's own UTC clock is the only time
  source (`engine_collector.py:115-117`). The window's bounds are the stored run start
  and finish times, not engine-log time.
- Past missions have no engine block unless the backfill below was run. The popup
  labels backfilled rows by source: `sampled` (live), `backfill-segment`,
  `backfill-matched` (`engine_backfill.py:45-46`).
- The replay empty-reply share is **omitted**: it is a bench-only figure from files
  outside the container, and no stored source for it was found (no `empty`-reply field
  exists in `stats_figures.py` or `engine_samples.py`).

## Backfill for past missions (`engine_backfill.py`)

Inputs staged by the operator (a Worker cannot read the container): the engine log
snapshot `data-tables/ops/engine_log_snapshot_20261108.log` and the segment map
`data-tables/ops/bench_kv_series_segments.txt`.

```
python -m ahawr_runner.engine_backfill --dry-run --log <snapshot> --segments <map> [--runs <export>]
python -m ahawr_runner.engine_backfill --apply --dsn <postgres dsn> --log <snapshot> --segments <map> --runs <export>
```

`--dry-run` prints the coverage and writes nothing; run it first. `--apply` needs
`--dsn` (the board postgres DSN) and writes only once the table exists (the DDL in
[stats-collector.md](stats-collector.md)). Matches are by (output tokens, generation
ms) pairs, only unique pairs become rows, ambiguous ones are skipped (false-match risk
reported by the dry run); segment rows are stamped with the mission window start,
matched rows with the run's `started_at`. The insert is idempotent on `sample_key`
(`engine_samples.py:36-41`), so a rerun writes nothing new.
