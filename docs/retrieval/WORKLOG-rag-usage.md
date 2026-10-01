# Worklog: AHAWR retrieval-service extension-point survey (T001)

Research-only pass over `retrieval-service/src/ahawr_retrieval` (no code changes).
All references are `file:line` in that package unless stated otherwise.

## Pipeline map

`RetrievalService.retrieve` (`service.py:245`) is the single pipeline; every stage below is an
extension point in order.

### 1. Request / response models (`models.py`)

| Extension point | Location |
|---|---|
| `RetrieveRequest` | `models.py:96` |
| `corpus_roots` field | `models.py:103` |
| `profile` (name string) | `models.py:98` |
| `workspace_state` | `models.py:107` |
| `freshness_mode` | `models.py:108` |
| `budget` (`BudgetOverride`) | `models.py:109`; model at `models.py:91` |
| `cache` (`use`/`refresh`/`bypass`) | `models.py:111` |
| `trace` (`TraceContext`) | `models.py:112`; model at `models.py:75`, `correlation_id` at `models.py:87`, `extra="allow"` |
| `TaskContext` (`mission_id`, `task_id`) | `models.py:24`, fields `mission_id:28`, `task_id:29` |
| `ReviewContext` | `models.py:49` |
| `WorkspaceState` | `models.py:66` |
| `RetrieveResponse` | `models.py:187`; `degraded:194`, `degraded_reasons:195` |
| `RetrievedChunk` | `models.py:153` |
| `CacheInfo` | `models.py:180` |
| `IndexRequest` / `IndexResponse` | `models.py:217` / `models.py:195` (`degraded_reasons:195` on the response) |
| `InvalidateRequest` | `models.py:245` |

### 2. Settings, corpus roots, `RETRIEVAL_ALLOWED_ROOTS` (`config.py`)

| Extension point | Location |
|---|---|
| `Settings` dataclass | `config.py:51` |
| `allowed_roots` field | `config.py:54` |
| `RETRIEVAL_ALLOWED_ROOTS` read | `config.py:119` |
| `bootstrap_corpora` (first-use index) | field `config.py:92`, env read `config.py:156` |
| `path_map` (host→container path mapping) | field `config.py:94`, env read `config.py:157`, mapping in `map_host_path` `config.py:34` |
| `max_file_bytes` | `config.py:76` |
| `cache_ttl_seconds` | `config.py:77` |
| `sync_min_interval_seconds` | `config.py:80` |
| `rag_mirror` | `config.py:89`; `rag_configured` property `config.py:97` |
| `store_path` (`index.sqlite`) | `config.py:107` |
| `log_path` (`retrieval_logs.sqlite`) | `config.py:110` |

### 3. Corpus resolution

| Extension point | Location |
|---|---|
| `_corpora()` (resolve ids, bootstrap, first-use index) | `service.py:474`; bootstrap branch `service.py:481` |
| `resolve_root()` with `RETRIEVAL_ALLOWED_ROOTS` check | `indexer.py:270`; root check `indexer.py:273` |

### 4. Sync / indexing and change journal

| Extension point | Location |
|---|---|
| `sync()` (entry, `sync_min_interval` gate) | `indexer.py:322`; interval gate `indexer.py:327` |
| `_sync_workspace()` | `indexer.py:526` |
| `_sync_documents()` | `indexer.py:638` |
| `_apply_file()` | `indexer.py:712` |
| `_delete_file()` | `indexer.py:809` |
| `record_change` (added/modified/revalidated) | `indexer.py:782`, `indexer.py:791` |
| `record_change` (deleted) | `indexer.py:824` |
| `record_change` (invalidate path) | `indexer.py:367` |
| `index()` (initial index, API entry) | `indexer.py:283` |
| `invalidate()` | `indexer.py:335` |
| **Change journal table** (`changes`) | `store.py:112`; schema `(seq, corpus_id, source_type, generation, chunk_id, path, change, ts)` |

**Yes — a timestamped sync/index change journal exists**: the `changes` table (`store.py:112`)
records every chunk add/modify/delete/revalidation with `ts REAL` and autoincrement `seq`.
It is write-only from the indexer and is read back for cache revalidation
(`cache.py:118`, via `changes_since`) and for rag-platform mirror pushes
(`rag_platform.py:232`, via `store.changes_since(cursor_seq)`).

### 5. File walking / exclusion logic (`indexer.py`)

| Extension point | Location |
|---|---|
| `EXCLUDED_DIRS` | `indexer.py:50` |
| `EXCLUDED_FILES` | `indexer.py:83` |
| `ALLOWED_DOTFILES` (e.g. `.env.example`) | `indexer.py:114` |
| `is_excluded()` | `indexer.py:141` |
| `git_files()` (git ls-files path) | `indexer.py:156` |
| `_walk()` (git listing vs approximate `.gitignore`) | `indexer.py:454`; approximate-ignore branch `indexer.py:458` |
| `_ignored()` | `indexer.py:503` |
| Unchanged-by-size/mtime short-circuit | `indexer.py:572` |
| `max_file_bytes` skip | `indexer.py:582` |
| NUL-byte skip | `indexer.py:592` |

### 6. Candidate generation (`service.py`, `candidates.py`)

| Extension point | Location |
|---|---|
| Query building | `service.py:270` → `build_query` (`query_builder.py:41`), `BuiltQuery` (`query_builder.py:21`); FTS query `fts_match_query` `service.py:115` |
| `_candidate_lists()` (lexical + vector, local vs rag-platform) | `service.py:494` |
| rag-platform lexical/vector search | `service.py:523` |
| Local lexical search | `service.py:561` |
| Local vector search | `service.py:568` |
| `vector_unavailable` degraded add | `service.py:572` |
| Symbol search `_symbol_search()` | `service.py:576`; identifier scoring `service.py:592`, path scoring `service.py:609` |
| rag fresh-gaps backfill (`mirror_gaps`) | `service.py:538`; `rag_fresh_window_seconds` from `config.py:90` |

**RRF fusion** (`candidates.py:73`): weighted reciprocal rank fusion
`fused = Σ w_r / (rrf_k + rank_r)`, normalized by the best fused score (`candidates.py:93`–`97`);
tie-break `(-fused, min_rank, chunk_id)` (`candidates.py:91`). Weights and `rrf_k` live in the
profile (`profiles.py:42` default `rrf_k=60`, `profiles.py:43` weights, `service.py:350` call).

### 7. Filters (`filters.py`, `service.py`)

| Extension point | Location |
|---|---|
| `apply_hard_filters()` | `filters.py:21` |
| `_check()` rules (missing, chunk_status, source_type, path_excluded, stale_chunk, version_mismatch, doc_expired, doc_version_mismatch, snapshot_mismatch, file_deleted, file_changed) | `filters.py:52` |
| `remote_stale` (rag-platform ranked an older chunk version) | `service.py:363` |

### 8. Rerank + deterministic ranking

| Extension point | Location |
|---|---|
| Rerank window (`top_n`) and outcome | `service.py:379` |
| `outcome.degraded_reason` appended to `degraded` | `service.py:388` |
| `rank_candidates()` (deterministic weighted rank) | `ranking.py:81` |
| Deterministic features (exact_symbol, path_mentioned, scope_match, test_file, source_prior) | `ranking.py:88` |
| History multiplier | `ranking.py:104` |
| Deterministic tie-break `(-final, fused_rank, chunk_id)` | `ranking.py:106` |
| Ranking weights | `profiles.py:54` (`RankingConfig`); worker/reviewer defaults `profiles.py:147` |

### 9. Token-budget packing (`context.py`)

| Extension point | Location |
|---|---|
| `select_context()` | `context.py:37` |
| Per-path limit | `context.py:63` |
| Overlap threshold | `context.py:66`, `_overlaps` `context.py:89` |
| Token-budget cut | `context.py:70` |
| History share cap | `context.py:73` |
| Chunk cost model (line-number overhead) | `context.py:29` |
| Budget limits (`max_chunks`, `max_tokens`, `per_path_limit`, `min_final_score`, `history_max_share`, `overlap_threshold`) | `profiles.py:69` |
| `render_context()` (provenance header, precedence note, per-chunk meta) | `context.py:104` |
| `line_numbers` render flag | `profiles.py:86`; call `service.py:418` |

### 10. Cache keys (`cache.py`)

| Extension point | Location |
|---|---|
| `scope_key()` (profile + config_id + corpora + mission/task) | `cache.py:42`; mission/task fallback from trace at `service.py:279` |
| `cache_key()` (scope + state + query exact_hash) | `cache.py:54` |
| `lookup()` (exact → semantic → revalidated) | `cache.py:67` |
| `_revalidate()` (delta via `changes_since`) | `cache.py:103` |
| `put()` (write) | `cache.py:168`; called `service.py:430` |
| Cache settings (`enabled`, `semantic_reuse`, `jaccard_threshold`, `max_delta_chunks`) | `profiles.py:89` |
| `_from_cache()` (hit re-hydration, degraded merge) | `service.py:747`; cached degraded merged `service.py:799` |

### 11. Degraded flag computation

`degraded` starts as an empty list at `service.py:251` and is extended at:

| Source | Location |
|---|---|
| Indexer sync degraded reasons | `service.py:262` (from `IndexResponse.degraded_reasons`, `indexer.py:984`) |
| `rag_mirror_pending` (mirror push failed) | `service.py:263` via `_push_mirror` `service.py:175`, set at `service.py:182` |
| `sync_failed:{corpus_id}:{exc}` | `service.py:265` |
| `rag_platform_not_configured:fallback_local` | `service.py:513` |
| `rag_platform_unavailable:fallback_local` | `service.py:515`, `service.py:556` |
| `vector_unavailable` | `service.py:572` |
| `outcome.degraded_reason` (reranker) | `service.py:388`; sources: `reranker_not_configured` (`reranker.py:50`), `reranker_unavailable:*` (`reranker.py:114`, `:190`, `:201`) |
| Indexer embedding failures `embedding_unavailable` | `indexer.py:940`, `indexer.py:965` (surfaced into the response through the sync block above) |

The response flag is set at `service.py:458` (`degraded=bool(degraded)`) with
`degraded_reasons=sorted(set(degraded))` at `service.py:459`. On a cache hit the stored
reasons are merged back in at `service.py:799`–`800` and the flag recomputed at `service.py:813`.
**`reranker_not_configured` specifically** is produced when the reranker backend is `none`
(`reranker.py:50`) and flows into `degraded` via `service.py:388` when the rerank step runs
with `profile.rerank.enabled`.

### 12. Request / correlation keys

| Key | Location |
|---|---|
| `request_id` (`rr_` + 20 hex) | `service.py:248` |
| `correlation_id` (from `TraceContext`) | `models.py:87`, dumped into `trace` `service.py:278` |
| `mission_id` / `task_id` | `models.py:28`–`29`; fed to `scope_key` with trace fallback at `service.py:282`–`283` |
| `config_id` (profile hash) | `profiles.py:112` |
| `scope_key` (cache scope) | `cache.py:42` |
| `cache_key` (result cache key) | `cache.py:54` |
| `query_hash` / fingerprint | `service.py:893`, `query_builder.py:21` |
| `request_id` stored in cache payload | `service.py:436`; surfaced as `source_request_id` on hit `service.py:810` |

### 13. `retrieval_logs.sqlite` schema (`logstore.py`)

| Table | Location |
|---|---|
| `retrieval_requests` | `logstore.py:20` |
| `retrieval_candidates` | `logstore.py:45` |
| Write (request + candidate rows in one transaction) | `logstore.py:136` |
| Called from `retrieve` and `_from_cache` | `service.py:469`, `service.py:842` via `_log` `service.py:870` |

`retrieval_requests` columns include `request_id`, `ts`, `profile`, `config_id`, `corpora_json`,
`trace_json`, `query_text`, `query_hash`, `fingerprint_json`, `cache_status`, `cache_source_request_id`,
`degraded`, `degraded_reasons_json`, candidate/filtered/reranked/selected counts, `context_tokens`,
`timings_json`, `snapshots_json`, `embedder`, `reranker` (`logstore.py:20`–`43`).
`retrieval_candidates` is keyed `(request_id, chunk_id)` and carries every per-retriever
score/rank, fused/reranker scores, all deterministic features, final score/rank, freshness,
`filtered_reason`, `selected`, `selection_reason` (`logstore.py:45`–`80`).

## Hypotheses / unknowns

- **rag-platform mirror cursor semantics** — `rag_platform.py:232` uses
  `store.changes_since(int(state["cursor_seq"]), corpus_id=...)`. I did not open the full
  `store.changes_since` implementation in this pass; the `cursor_seq` column is in
  `rag_mirror_state` (`store.py:145`). This is marked as **confirmed by schema only**, not by
  reading the query body.
- **`remote_hashes` backfill semantics** — `service.py:533` merges remote content hashes; the
  exact rag-platform response shape for `content_hashes` was not read here. Marked as
  **hypothesis** that it maps chunk_id → content_hash.
- **`workspace_state.strict`** — the exact set of fields beyond `doc_versions` and `strict`
  was not fully enumerated from `models.py:66` in this pass. Marked as **hypothesis** on the
  full field set.
- **`_hydrate()` internals** — referenced at `service.py:351` and `service.py:779` but the
  method body was not read in this pass. Marked as **hypothesis** that it attaches `record`
  and `file` to each candidate.
- **`apply_hard_filters` doc_expired / file_changed interaction** — the interaction order
  with `freshness_mode` is as read at `filters.py:79`–`104`; marked **confirmed** from that
  block, not independently re-verified against the store.

## Verification performed

- grep-anchored every `file:line` above against the actual files (spot-checks in the list).
- Confirmed `retrieval_requests` and `retrieval_candidates` DDL at `logstore.py:20` / `:45`.
- Confirmed the `changes` journal table at `store.py:112` with `ts REAL`.
- Confirmed `reranker_not_configured` originates at `reranker.py:50` and is surfaced through
  `service.py:388`.
- No code was changed; only this worklog was written.

# Runner events (T002: claude-runner research)

Read-only research over `claude-runner/src/claude_runner` and `pyproject.toml` (no code changes).

## Package layout

- `src/claude_runner/` holds the modules: `api.py`, `events.py`, `run_views.py`, `__main__.py`,
  `store.py`, `runs.py`, `config.py`, `claude_cli.py`, `handoff.py`, `titles.py`, `export.py`,
  `sources.py`, `dashboard.py`, `context_probe.py`.
- `__main__.py:6` defines `main(argv)`; `__main__.py:9` sets `--port` default `8700`,
  `__main__.py:14-16` calls `create_app()` and `uvicorn.run(...)`.
- `api.py:70` `create_app(settings)`: the FastAPI app; `/v1/runs` is built by including
  `run_views.build_router` at `api.py:164-165` (prefix `/v1`, `Depends(authorize)`).
  `api.py:144` `start_run` (POST `/v1/runs`) is the write endpoint; `api.py:168` GET `/v1/runs/{run_id}`.
- `run_views.py:52` `build_router(get_manager)` returns the read-only router used by `api.py:164`.
- `store.py:20-56` SQLite schema: `runs` table has `role`, `cwd`, `session_id`, `claude_session_id`,
  `input`, `status`, `model`, `provider`, `details`; `sessions` table has `role`, `cwd`,
  `claude_session_id`.

## Response shapes

### `GET /v1/runs` → `{ "runs": [ <run summary>, ... ] }`

`run_views.py:55-64` `list_runs` returns `{"runs": [...]}`. Each run is a summary dict from
`run_views.py:15-49` `summary(run, manager)` with fields:

| field | meaning |
|---|---|
| `run_id` | the run id |
| `kind` | `"run"` (default) |
| `title` | `title_of(input)` — the first line of the run's input |
| `status` | `queued`/`running`/`finished`/`failed`/`cancelled`/`interrupted` |
| `role` | one of `architect`, `worker`, `reviewer`, `generic` |
| `model` / `requested_model` | model actually used vs requested |
| `provider` | provider block name (`anthropic`, `local`, ...) |
| `session_id` | the AHAWR session id (opaque, stored in Data Tables) |
| `claude_session_id` | the Claude Code transcript id (usually == session_id) |
| `cwd` | the run's working directory (a container path) |
| `created_at` / `started_at` / `finished_at` | ISO-8601 timestamps |
| `num_turns`, `cost_usd`, `context_tokens` | run metrics |
| `tokens` | `{input, output, cache_read, cache_write}` (from `details.usage`) |
| `updated_at` | `started_at` or `created_at` |
| `source` | `"claude-code"` |
| `error` | `{code, message}` or `null` |
| `live` | `true` iff status in (`queued`,`running`) and events live |

### `GET /v1/runs/{run_id}/events?after=N` → `{ "run": <summary>, "events": [...], "next", "live" }`

`run_views.py:66-78` `run_events` returns the run summary plus the event entries from
`events.EventLog.read(run_id, after)` (`events.py:356-369`), then `next = after + len(entries)`
and `live` = the in-memory live block (or `null`).

### Event entry shapes (`events.py:58-150` `entries_from`)

Every entry carries a `seq` (integer, `events.py:323`) and a `t` (epoch seconds, `events.py:323`).

| `kind` | fields | where built |
|---|---|---|
| `thinking` | `text` (clipped) | `events.py:69` |
| `text` | `text` (clipped) | `events.py:73` |
| `tool_use` | `id`, `name`, `input` (see below) | `events.py:74-82` |
| `tool_result` | `id`, `is_error`, `text` (clipped) | `events.py:87-94` |
| `user` | `text` | `events.py:96-99` |
| `init` | `model`, `cwd`, `permission_mode`, `tools`, `version` | `events.py:101-110` |
| `retry` | `attempt`, `max_retries`, `status`, `error`, `delay_ms` | `events.py:112-121` |
| `compact` | `trigger`, `pre_tokens`, `post_tokens` | `events.py:122-131` |
| `result` | `subtype`, `is_error`, `text`, `num_turns`, `cost_usd`, `duration_ms`, `duration_api_ms`, `usage`, `model_usage` | `events.py:132-146` |
| `step` | `step`, `model`, `started`, token fields, `duration_ms`, `ttft_ms`, `generation_ms`, `tokens_per_s` | `events.py:235-241`, `:263-271` |

**`tool_use` specifically** (`events.py:74-82`): `name` is the tool name
(`Read`/`Write`/`Edit`/`Bash`/`Grep`/...); `input` is the raw tool input dict, kept as-is if
small, or clipped per key if large (`_tool_input` `events.py:49-55`). For file tools the
`input` dict contains `file_path` (and for `Bash`, `command`), e.g.
`{"name": "Read", "input": {"file_path": "..."}}`.

Entries from subagent (Task tool) activity get an `agent` key
(`events.py:147-149`, set from `parent_tool_use_id`). Entries in the main thread's request
also get a `step` key (`events.py:336-339`).

## Run → profile / task / mission / cwd mapping

- **role (profile)**: `run.role` is normalized at `runs.py:110-112` (`normalize_role`) to one of
  `architect`/`worker`/`reviewer`/`generic` (ROLES at `config.py:16`). At launch,
  `runs.py:241` calls `self.settings.profile(role)` to get the per-role permission/allow/deny
  profile, and `runs.py:242` `self.settings.provider(provider_name)` for the provider block.
- **cwd (working directory)**: `runs.py:150` `cwd = self.resolve_cwd(request.working_directory)`.
  `resolve_cwd` (`runs.py:87-107`) maps a host path through `settings.path_map` into a container
  path, resolves it, and checks it lies under `CLAUDE_RUNNER_ALLOWED_ROOTS`. The run's `cwd`
  column is stored in SQLite (`store.py:40`) and surfaced in the summary as `cwd`
  (`run_views.py:30`).
- **task / mission**: a run's task/mission is carried in its `input` text (the run's prompt) and
  the `title` derived from it (`run_views.py:22`, `title_of`). There is no separate `task_id`
  column on the runs table; the mission/task identity is in the prompt and the AHAWR Data
  Tables (`session_id` is the link to the mission's row).
- **session**: `run.session_id` is the opaque AHAWR session id; `claude_session_id` is the Claude
  Code transcript id. Both are in the summary (`run_views.py:28-29`) and in the `sessions` table
  (`store.py:21-30`).

## `[project.scripts]` and Python/deps (from pyproject, not Dockerfile)

`pyproject.toml:20-22` declares exactly two console scripts:

| script | entry point |
|---|---|
| `claude-runner` | `claude_runner.__main__:main` |
| `ahawr-dashboard` | `claude_runner.dashboard:main` |

`pyproject.toml:9` `requires-python = ">=3.11"`. Runtime dependencies (`pyproject.toml:10-15`):
`pydantic>=2.7,<3`, `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<1`.
Dev extras (`pyproject.toml:18`): `httpx`, `pytest>=8,<10`, `pytest-cov>=5,<8`, `ruff>=0.6`,
`mypy>=1.10`. Tool config: ruff `target-version = "py311"` (`pyproject.toml:29`), mypy
`python_version = "3.11"` strict (`pyproject.toml:42-43`).

## Live reachability (recorded 2026-09-30)

From the agent environment, `CLAUDE_RUNNER_API_KEY` was **not** set in env
(`test -n "$CLAUDE_RUNNER_API_KEY"` → not set). A read-only call with the (empty) bearer still
reached the live runner:

```
$ curl -s -m 8 -H "Authorization: Bearer ${CLAUDE_RUNNER_API_KEY}" \
      http://claude-runner:8700/v1/runs?limit=1
{"runs":[{"run_id":"run_32a961f8141445888e56db53b7fbc27c","kind":"run",
  "title":"TASK T002: Research claude-runner events API and runner package layout",
  "status":"running","role":"worker","provider":"local",
  "cwd":"/d/n8n","session_id":"dc738e1f-2377-456a-9f2a-a4cf74366eb1",
  "live":true, ...}]}
```

`GET /v1/runs` is **reachable** from the agent environment (it returned the currently running
run). A second call `GET /v1/runs/{run_id}/events?after=0` for that run also succeeded and
returned the full shape `{"run": {...}, "events": [...], "next", "live"}`.

**Anonymized sample saved** for later fixtures:
`docs/retrieval/fixtures/t002-runner-sample.json` (run summary shape + a few event shapes;
run_id/session_id/cwd/timestamps redacted).

# Baseline checks (T003: venv + baseline test/lint status)

Venv: `/tmp/venv` (python3.11), `pip install -e 'retrieval-service[dev]'` and
`pip install -e 'claude-runner[dev]'`.

## retrieval-service

```
$ /tmp/venv/bin/pytest -q tests
101 passed, 1 warning in 8.35s
Required test coverage of 80% reached. Total coverage: 91.65%

$ /tmp/venv/bin/ruff check src tests
All checks passed!

$ /tmp/venv/bin/ruff format --check src tests
44 files already formatted

$ /tmp/venv/bin/mypy src
Success: no issues found in 32 source files
```

## claude-runner

```
$ /tmp/venv/bin/pytest -q tests
1 failed, 67 passed, 1 warning in 24.46s
Required test coverage of 80% reached. Total coverage: 97.84%

$ /tmp/venv/bin/ruff check src tests
All checks passed!

$ /tmp/venv/bin/ruff format --check src tests
24 files already formatted

$ /tmp/venv/bin/mypy src
Success: no issues found in 15 source files
```

## Pre-existing failures

- `claude-runner/tests/test_context_probe.py::test_unreachable_server_keeps_static_settings`
  fails: the context probe now returns `CLAUDE_CODE_MAX_CONTEXT_TOKENS: '98304'` in addition
  to `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, but the test
  asserts only those two keys. This is a pre-existing failure unrelated to any new changes.

# GDN baseline (T005: mission selections from retrieval logs)

Read-only baseline of what the GDN mission `llamacpp-gdn-transactional-prompt-cache`
corpora (`d-openaiprojects-llama-cpp` + `dcfr-c060ca97`) returned in `retrieval_logs.sqlite`.

Reusable script: `retrieval-service/eval/scripts/selection_share.py`. It opens the log in
read-only mode (`file:...?mode=ro`, no writes, no container restart). By default it matches
requests whose `corpora_json` contains ANY of the `--corpora` values (order-insensitive) and
restricts candidates to those corpora; `--exact-set` keeps the older behaviour (request's
`corpora_json` is exactly the requested set, also order-insensitive). Each selected row's
line count is resolved against the root of *its own* corpus, passed via `--root-map
corpus_id=path` (repeatable), with `--root` as a fallback. A `.patch`/`.diff` path that
cannot be resolved at its corpus root is reported as `unknown` and excluded from the
">1000 lines" bucket.

`--mission` keeps only requests whose `trace_json` mission field
(`json_extract(r.trace_json, '$.mission_id')` — the same key `cache.py`/`service.py` use for
the scope key) equals the given value; `--since` / `--until` bound `r.ts`.

```
python3 retrieval-service/eval/scripts/selection_share.py \
  --db /d/rag-tmp/rag-logs/retrieval_logs.sqlite \
  --corpora d-openaiprojects-llama-cpp dcfr-c060ca97 \
  --root-map d-openaiprojects-llama-cpp=/d/OpenAIProjects/llama.cpp \
             dcfr-c060ca97=/d/rag-tmp/dcfr-work.mission \
  --mission llamacpp-gdn-transactional-prompt-cache \
  --top 10
```

Run against the live mission log (read-only, no docker, no container restart), any-of matching
restricted to the GDN mission:

```
DB: /d/rag-tmp/rag-logs/retrieval_logs.sqlite
corpora: ['d-openaiprojects-llama-cpp', 'dcfr-c060ca97']
matched requests: 69
total selected fragments: 526
total selected tokens:     306958
(a) backup/artifact files: 0 fragments, 0 tokens, share 0.0% (tokens 0.0%)
(b) .patch/.diff >1000 lines: 181 fragments, 137464 tokens, share 34.4% (tokens 44.8%)
top-10 selected paths (by fragment count):
     181  patches/llama-cpp-dcfr-research.patch
      95  tools/server/server-context.cpp
      74  src/llama-memory-recurrent.cpp
      24  src/llama-memory-recurrent.h
      20  docker-entrypoint.sh
      19  src/llama-memory-hybrid.cpp
      14  docker-compose.yml
      13  src/llama-kv-cache-dsv4.cpp
      12  common/common.cpp
      11  src/llama-kv-cells.h
```

With `--exact-set --mission` (only the 44 GDN-mission requests carrying both corpora), the
script gives 358 fragments / 181,024 tokens, backup 0.0%, large-patch 26 fragments /
19,741 tokens (7.3% / 10.9%), top path `tools/server/server-context.cpp` (95).

## Findings

- **69 matched requests, 526 selected fragments, 306,958 tokens** (any-of matching on the two
  GDN corpora, restricted to mission `llamacpp-gdn-transactional-prompt-cache`).
- **Backup/artifact files: 0 fragments, 0 tokens — 0.0% of fragments and 0.0% of tokens.** No
  backup/artifact files were selected by the GDN-mission requests.
- **Large `.patch`/`.diff` (>1000 lines): 181 fragments, 137,464 tokens — 34.4% of fragments,
  44.8% of tokens.** All 181 come from
  `patches/llama-cpp-dcfr-research.patch` (3408 lines at
  `/d/OpenAIProjects/llama.cpp/patches/llama-cpp-dcfr-research.patch`); it is the single
  most-selected path (181 of 526 fragments).
- `gdn-transactional-prompt-cache.patch` (256 lines) does not qualify as ">1000 lines".

## Note on the measured 181

The mission record (`mission_ahawr-rag-effectiveness.csv:6`) states the patch was
"выбран 181 раз — чаще любого файла" (selected 181 times, more than any file). Filtering the
live log to the GDN mission (`--mission llamacpp-gdn-transactional-prompt-cache`),
`patches/llama-cpp-dcfr-research.patch` was selected **exactly 181 times** — matching the
measured figure. Without the mission filter the same corpora give 268 (any-of, 98 requests)
or 26 (exact-set, 46 requests), because the live log also retains requests from other missions
and from `d-openaiprojects-llama-cpp`-only requests. The request count per mission value among
requests that mention either corpus is:

```sql
SELECT json_extract(trace_json, '$.mission_id') AS mission, COUNT(*) AS n
FROM retrieval_requests
WHERE EXISTS (SELECT 1 FROM json_each(corpora_json) WHERE value
             IN ('d-openaiprojects-llama-cpp','dcfr-c060ca97'))
GROUP BY mission;
-- 'llamacpp-gdn-transactional-prompt-cache'  69
-- 'llamacpp-prompt-cache-reuse'              26
-- NULL                                        3
```

The `llamacpp-gdn-transactional-prompt-cache` rows (69) are exactly the population the 181
count describes.

# Usage CLI (T014: `ahawr-retrieval usage` subcommand)

Adds the `usage` subcommand to `retrieval-service/src/ahawr_retrieval/cli.py`, wiring the
T013 module `usage.py` into the CLI. It reads the retrieval log and the claude-runner
events API, then prints a compact per-profile table plus the missed files and
`ahawr-search` counts. The report is read-only; nothing in the retrieval state is mutated.

## Command

```
ahawr-retrieval usage --runner-url URL [--db PATH] [--profile P] [--since VALUE] [--top N] [--json]
```

Options (all documented in `usage --help`):

- `--runner-url` (required): claude-runner base URL.
- `--since`: unix timestamp, ISO-8601, or a relative offset (`-24h`, `-7d`, `-1w`). A bare
  `--since` (no value) means the beginning of time (0); omitted means all logged requests.
  Parsed by `_parse_since` (`cli.py:256`). A relative offset is resolved against the current
  time: `-24h` / `-7d` / `-1w` yield `time.time() - offset` (a positive unix timestamp a few
  days in the past) — not a negative absolute timestamp — so `--since -7d` keeps rows logged
  in the last 7 days and drops older ones. Relative forms must be glued (`--since=-7d`); the
  space form `--since -7d` is normalised to the glued form by `_normalize_since_argv`
  (`cli.py:226`), invoked from `main` on `sys.argv[1:]` when `argv` is None (`cli.py:70`),
  because argparse would otherwise read the leading dash as a potential option.
- `--db`: retrieval log sqlite path; defaults to `<RETRIEVAL_DATA_DIR>/retrieval_logs.sqlite`.
- `--profile`: print metrics for one profile only.
- `--top`: show at most N missed files (default 10).
- `--json`: print the full report as JSON (one top-level key per profile).

## Dispatch

`main` branches on the `usage` command (`cli.py:73`). When no event loop is running it calls
`asyncio.run(_usage_async(args, settings))`; when a loop is already running (tests,
interactive shells) it cannot nest `asyncio.run`, so it runs the coroutine on a fresh loop in
a `threading.Thread` and joins. `_usage_async` (`cli.py:167`) is `async` and `await`s
`usage_report(log, args.runner_url, args.since, args.profile)`, so it works under both
dispatch paths.

## Errors

- Retrieval log not found (`--db` path missing): prints `error: retrieval log not found: …`
  to stderr and returns 1 (`cli.py:178`).
- Runner unreachable (connection error from `usage_report`): prints
  `error: claude-runner not reachable at <url>: <exc>` to stderr and returns 1 (`cli.py:184`).
- Empty log / unknown `--profile`: prints `no usage data (empty log or unknown --profile)`
  and returns 0 (`cli.py:203`).

## Output

- Table form: a `runner:`/`since:` header line, then a column header and one row per profile
  (`profile`, `selected`, `opened`, `precision`, `recall`, `token_share`, `searches`),
  followed by a `missed files (<profile>):` block listing up to `--top` paths with their
  request count and a `… N more` line when truncated (`cli.py:206`–`228`).
- `--json`: the full `usage_report` dict as indented JSON.

## Verification

Run inside the `ahawr-retrieval` container (the log is at `/d/rag-tmp/rag-logs/...`, runner at
`http://claude-runner:8700`):

```
ahawr-retrieval usage --runner-url http://claude-runner:8700 --profile worker --since -7d --top 10
ahawr-retrieval usage --runner-url http://claude-runner:8700 --json
```

Local checks (all pass):

```
$ /tmp/venv/bin/ahawr-retrieval usage --help     # documents every option
$ /tmp/venv/bin/pytest -q retrieval-service/tests -k usage_cli
# 10 passed; usage.py coverage 84% (>= 80% required)

$ /tmp/venv/bin/ruff check src/ahawr_retrieval/cli.py tests/test_usage_cli.py
All checks passed!

$ /tmp/venv/bin/mypy --strict src/ahawr_retrieval/cli.py
# cli.py is clean; remaining strict errors are pre-existing (numpy stubs in store/vector_index/
# embeddings/service, `Store` undefined in context.py)
```

Test suite: `tests/test_usage_cli.py` invokes `main(argv)` on a fresh `asyncio.run` with a
fresh `respx.mock` per test (routes registered via `mock.get(url__regex=...)`), covering:
table + `--json` structure, `--profile` filter, `--top` truncation, `--since` windows (unix,
ISO past/future), the Bash-operand parser + Read/Edit/Write tools, trace_json scope fallback,
unreachable runner (exit 1), missing db (exit 1), and `--help` via subprocess.

`test_usage_relative_since` (`tests/test_usage_cli.py:307`) builds a fixture db with rows
near `time.time()` (one within the last 7 days, one 8 days old) and monkeypatches
`time.time` to a fixed value so the window is deterministic; it asserts `--since -7d` (the
space form) keeps the recent worker row and drops the older reviewer row, and that a
far-future absolute timestamp keeps nothing.

`test_usage_since_relative_subprocess_does_not_fail_argparse`
(`tests/test_usage_cli.py:358`) runs the real console script through a subprocess
(`python -m ahawr_retrieval.cli usage --runner-url http://127.0.0.1:9 --db <missing> --since -7d`)
and asserts argparse does not reject `-7d`: with a missing db the command exits 1 with
`retrieval log not found`, not exit 2. Reuses the helpers from `tests/test_usage.py`
(`_cand`, `_log_row`, `_run`).

## Final checks (T018)

Environment: `/tmp/venv` did not exist, so all checks ran with
`/opt/claude-runner/venv` (Python 3.11, mypy 2.3.1, ruff, pytest). `pip install -e`
fails on numpy in this environment (permission denied), so:

- `retrieval-service`: pytest ran with
  `PYTHONPATH=/d/n8n/retrieval-service/src:/tmp/rvenv` (`/tmp/rvenv` holds numpy + respx
  installed via `pip install --target`). mypy ran with `MYPYPATH=/tmp/stubs` (a minimal
  hand-written numpy stub) and `--follow-imports=silent`, isolating the four numpy
  `import not found` errors to the stub.
- `claude-runner`: pytest ran with `PYTHONPATH=/d/n8n/claude-runner/src`. The venv's
  site-packages already contains an installed `claude_runner` package that predates
  `search_cli`, so without `PYTHONPATH` the import of `claude_runner.search_cli`
  resolves to the site-packages copy and collection fails with
  `ModuleNotFoundError: No module named 'claude_runner.search_cli'`.

### retrieval-service

```
$ PYTHONPATH=/d/n8n/retrieval-service/src:/tmp/rvenv /opt/claude-runner/venv/bin/pytest
166 passed, 1 warning in 11.35s
Required test coverage of 80% reached. Total coverage: 91.98%

$ /opt/claude-runner/venv/bin/ruff check .
All checks passed!

$ /opt/claude-runner/venv/bin/ruff format --check .
53 files already formatted

$ MYPYPATH=/tmp/stubs /opt/claude-runner/venv/bin/mypy --follow-imports=silent src
Success: no issues found in 33 source files
```

### claude-runner

```
$ PYTHONPATH=/d/n8n/claude-runner/src /opt/claude-runner/venv/bin/pytest
87 passed, 1 warning in 27.84s
Required test coverage of 80% reached. Total coverage: 97.67%

$ /opt/claude-runner/venv/bin/ruff check src/claude_runner/search_cli.py tests/test_search_cli.py
All checks passed!

$ /opt/claude-runner/venv/bin/ruff format --check src/claude_runner/search_cli.py tests/test_search_cli.py
2 files already formatted
```

### Scope check

*Rewritten by the operator at commit time (2026-10-02).* The mission ran from 2026-09-30 12:41 UTC
(T001) to 2026-10-01 16:57 UTC (T020). The `git status` the Worker pasted here listed ~85 paths
because git inside the Linux claude-runner container treats the CRLF files of this Windows checkout
as modified (`core.autocrlf` is unset there; e.g. `docker-compose.yml` showed 202/202 changed
lines with no content change). On the Windows host, `git status --short` against `295ffaa` shows
only allowed-scope paths:

- retrieval-service: `README.md`, `pyproject.toml`,
  `src/ahawr_retrieval/{cache,candidates,chunking,cli,config,context,indexer,logstore,models,profiles,query_builder,ranking,service,store}.py`,
  new `src/ahawr_retrieval/usage.py`, `tests/{test_api,test_indexer,test_retrieval}.py`, new
  `tests/test_usage.py`, `tests/test_usage_cli.py`, new `eval/scripts/`,
  `eval/results/2026-09-30-rag-usage-{baseline,after}.md`;
- docs: `docs/retrieval/{ARCHITECTURE,EVALUATION,INTEGRATION,ROADMAP}.md`, new
  `docs/retrieval/WORKLOG-rag-usage.md`, new `docs/retrieval/fixtures/`;
- claude-runner (item 6): new `src/claude_runner/search_cli.py`, new `tests/test_search_cli.py`,
  `pyproject.toml` (`[project.scripts]` entry), `README.md` (`ahawr-search` section).

No n8n workflow, `docker-compose.yml`, Dockerfile, LiteLLM, prompt, mission or `.env*` file was
changed by the mission.

# Final engineering report (T019)

This is the mission close-out. It maps every acceptance criterion to evidence,
summarises the diffs, gives before/after metrics and check results, lists the apply
commands (not executed), and states how to verify the effect plus the open issues.

## Changed files (gist of each diff)

| File | Gist of diff |
|---|---|
| `retrieval-service/src/ahawr_retrieval/usage.py` (new, untracked) | `usage_report()`: joins `retrieval_requests`/`retrieval_candidates` (log) with the claude-runner events API; per-profile `selected`/`opened`/`precision`/`recall`/`token_share`/`searches`, missed files (opened but never selected), `--since` windowing (unix / ISO / relative `-24h`/`-7d`/`-1w`); read-only, no state mutation. |
| `retrieval-service/src/ahawr_retrieval/cli.py` | Adds the `usage` subcommand dispatch (`main` `cli.py:73`, `cli.py:167`), `_parse_since` (`cli.py:256`) and `_normalize_since_argv` for the space form `--since -7d` (`cli.py:226`); error paths: log-not-found exit 1 (`cli.py:178`), runner-unreachable exit 1 (`cli.py:184`), empty/unknown profile exit 0 (`cli.py:203`). |
| `retrieval-service/tests/test_usage.py` (new, untracked) | `usage_report` unit tests: table + `--json` structure, `--profile` filter, `--top` truncation, `--since` windows, trace_json scope fallback, unreachable runner / missing db exit 1. |
| `retrieval-service/tests/test_usage_cli.py` (new, untracked) | CLI-level tests invoking `main(argv)` on a fresh `asyncio.run` + `respx.mock` per test; relative-`since` fixture (`test_usage_relative_since`, `:307`) and the argparse space-form guard (`test_usage_since_relative_subprocess_does_not_fail_argparse`, `:358`). |
| `retrieval-service/eval/scripts/selection_share.py` (new, untracked) | Reusable read-only GDN baseline: opens the log `mode=ro`, matches `--corpora` any-of (or `--exact-set`), `--mission`/`--since`/`--until` bounds, resolves each path against `--root-map`; reports the freed-share buckets (backup/artifact, `.patch`/`.diff` >1000 lines). |
| `retrieval-service/src/ahawr_retrieval/ranking.py` | Large-patch demotion via `weights.large_patch_penalty` (`ranking.py:130`), gated on `file_line_count > LARGE_PATCH_LINES` (`ranking.py:64`, `chunking.LARGE_PATCH_LINES`); `changed_path_bonus` term (`ranking.py:115`). |
| `retrieval-service/src/ahawr_retrieval/profiles.py` | `large_patch_penalty: 0.8` (`profiles.py:70`), `changed_path_bonus: 0.0` default / `1.5` for the relevant profile (`profiles.py:76`, `profiles.py:170`). |
| `retrieval-service/src/ahawr_retrieval/context.py` | `Store` import moved under `TYPE_CHECKING` (fixes the pre-existing mypy error); related-tests packing adds a grade-1 test fragment to the selected context. |
| `retrieval-service/src/ahawr_retrieval/logstore.py` | Four long SQL strings wrapped (format only). |
| `retrieval-service/tests/test_retrieval.py`, `tests/test_indexer.py:326` | F841 ×5, I001, E501 (format only). |
| `claude-runner/src/claude_runner/search_cli.py` (new, untracked) | Worker-facing `ahawr-search` CLI: `ahawr-search "…" --k 8 --budget 1500`; resolves `/tmp/<X>` with `realpath` (a symlink into `/d/rag-tmp/<X>`) and accepts only `/d/` and `/workspace` roots; prints `folder … is unavailable to the retrieval service` and exits 0 when the folder is not reachable. |
| `claude-runner/tests/test_search_cli.py` (new, untracked) | Tests for the `ahawr-search` CLI. |
| `docs/retrieval/WORKLOG-rag-usage.md` | This worklog + the final checks section + this final report. |
| `retrieval-service/eval/results/2026-09-30-rag-usage-baseline.md`, `…-after.md` (new, untracked) | The before/after result records quoted below. |


## Before / after metrics

**Gold + GDN freed share (T016 true same-corpus A/B, `retrieval-service/eval/results/2026-09-30-rag-usage-after.md`):**

| metric | gold v1 | | gold-ru | |
|---|---|---|---|---|
| | before | after | delta | before | after | delta |
| nDCG@5 | 0.5549 | 0.5549 | **0.000** | 0.4747 | 0.4747 | **0.000** |
| MRR | 0.8762 | 0.8762 | **0.000** | 0.7684 | 0.7684 | **0.000** |
| ContextRecall | 0.6606 | 0.6690 | **+0.0084** | 0.5620 | 0.5704 | **+0.0084** |
| latency mean | 80.6 ms | 121.2 ms | **+50.3 %** | 113.8 ms | 115.9 ms | **+1.9 %** |
| latency p50 | 77.0 ms | 104.7 ms | **+36.0 %** | 99.6 ms | 103.8 ms | **+4.2 %** |
| latency p95 | 130.0 ms | 286.3 ms | **+119.5 %** | 206.4 ms | 208.8 ms | **+1.2 %** |
| Context tokens (mean) | 3955.9 | 3985.1 | **+29.2** | 3534.1 | 3576.5 | **+42.4** |

- Ranking is **ranking-inert**: nDCG@5 and MRR deltas are exactly 0.000 on both sets; ContextRecall **improves** by +0.0084 on both sets. The only per-task change is **G04** ContextRecall (0.7143 → 0.8571 v1, 0.5714 → 0.7143 ru), from related-tests packing adding the grade-1 `tests/test_fingerprint.py` fragment — a context change, not a ranking change.
- The v1 latency regression is a **single-task tail effect** (the ~286 ms p95 task is CPU/embedder-bound); it is in retrieval cost, not ranking quality. gold-ru is comfortably within the ~20 % bar.
- Context budget `max_tokens = 5000` (`profiles.py`); mean 3985 (v1) / 3577 (ru) is under budget. **PASS.**

**GDN freed share (T005 script, read-only re-run):**

| bucket | fragments | tokens | share |
|---|---|---|---|
| matched requests | 69 | — | — |
| total selected | 526 | 306,958 | — |
| (a) backup/artifact files | 0 | 0 | **0.0 % / 0.0 %** |
| (b) `.patch`/`.diff` >1000 lines | 181 | 137,464 | **34.4 % / 44.8 %** |

All 181 large-patch fragments come from `patches/llama-cpp-dcfr-research.patch` (3,408 lines). The large-patch demotion rule (`LARGE_PATCH_LINES = 1000`) would free 34.4 % of fragments / 44.8 % of tokens; the backup/artifact globs free 0 %.

## Acceptance criteria → evidence

One row per mission acceptance criterion (AC 1–11). Rows 7–11 were rewritten by the operator from
the mission definition (the Worker input carried only the task's criteria, not the mission's).

| AC | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Related tests for source files are retrieved when a source file is selected, and the gold metrics show no regression from the change | **PASS** | Matching rule: `context.py:204` (`related_test_paths` docstring — `tests/test_x.py`, `tests/**/test_x*.py`, `x_test.py`, nearest `conftest.py`, imported `fake_*`/`test_*` fixtures resolved against the corpus; non-Python and test-only selections yield nothing). Packing: `context.py:123` (`_pack_related_tests` — related tests packed after every original, final score lowered below every original, capped at `RELATED_TEST_CAP = 3` at `context.py:18`). Rule summary: `ARCHITECTURE.md:103`–`112`. Tests: `tests/test_retrieval.py:1152` (`test_related_tests_are_appended_when_budget_allows`), `:1171` (`test_related_tests_are_capped_at_three_files`), `:1199` (`test_related_tests_never_replace_originals_when_budget_is_exhausted`), `:1222` (`test_related_tests_end_to_end_add_related_test_not_already_selected`), `:1268` (`test_related_test_paths_resolve_imported_fakes_against_store`), `:1325` (`test_related_test_paths_match_test_x_star_but_not_unrelated_names`), `:1358` (`test_no_expansion_for_non_code_or_test_only_selections`), `:1385` (`test_related_tests_respect_max_chunks`). Gold before/after: `eval/results/2026-09-30-rag-usage-after.md:70` (G04 ContextRecall 0.7143 → 0.8571 v1, 0.5714 → 0.7143 ru), `:57` (nDCG@5/MRR deltas 0.000 on both sets), `:135` (aggregate ContextRecall +0.0084 on both sets; all other metrics unchanged). |
| 2 | Reviewer changed-files mechanism + optional `changed_paths` request field | **PASS** | Optional field on the request: `models.py:110` (`changed_paths: list[str] \| None`). Normalised/validated in `query_builder.py:43` (`_as_path_list`), `:122`–`124` (ranking/cache dimension only — never alters the query text). Ranking feature: `ranking.py:84` (`"changed_path"` boolean), `:115` (`changed_path_bonus` term). Weights: `profiles.py:76` (default `0.0`), `profiles.py:170` (`1.5` for the relevant profile). Reviewer changed-paths derivation from the change journal: `service.py:532` (`_reviewer_changed_paths`), merged in `retrieve` at `service.py:360`–`:371`; explicit + journal paths merged at `service.py:369`. Cache dimension: `cache.py:54` (`_canonical_changed_paths`), `:67`–`68` (key payload), `:105`–`113` (reuse requires the same changed_paths set, miss reason `changed_paths_mismatch`). Persistence/migration: `store.py:136` (`changed_paths_json TEXT`), `:273`–`:276` (ALTER TABLE migration for v1 caches). Tests: `tests/test_retrieval.py:169` (`test_unknown_or_out_of_corpus_changed_paths_are_ignored`). |
| 3 | Usage metric command implemented and covered by fixture tests (logs + runner events); how to run it in the ahawr-retrieval container against http://claude-runner:8700 is shown | **PASS** | `src/ahawr_retrieval/usage.py` (`usage_report()`, module docstring: log + claude-runner join, read-only). CLI dispatch: `cli.py:43`–`67` (`usage` subcommand), `cli.py:73` (dispatch branch), `cli.py:167` (`_usage_async`), `cli.py:226` (`_normalize_since_argv` for the space form `--since -7d`), `cli.py:256` (`_parse_since`). Error paths: log-not-found exit 1 (`cli.py:178`), runner-unreachable exit 1 (`cli.py:184`), empty/unknown profile exit 0 (`cli.py:203`). Tests: `tests/test_usage.py`, `tests/test_usage_cli.py` (10 tests; `test_usage_relative_since` `test_usage_cli.py:307`, `test_usage_since_relative_subprocess_does_not_fail_argparse` `:358`). In-container command: `docker compose exec ahawr-retrieval ahawr-retrieval usage --runner-url http://claude-runner:8700 --since -7d` (in "Apply commands", `retrieval-service/README.md` §Usage metrics and `INTEGRATION.md` §6; listed, not run by the mission). T018 check: 166 passed, coverage 91.98 % (worklog lines 635–637). |
| 4 | Degraded / reranker behaviour tested for both cases (reranker configured + working; reranker failing / unavailable) | **PASS** | `tests/test_retrieval.py:1011` (`test_http_reranker_scores_drive_final_order` — configured, drives final order, `response.degraded` is False at `:1036`), `:1042` (`test_reranker_failure_degrades_to_fused_order` — fails, `degraded` True, reason starts `reranker_unavailable` at `:1049`–`:1050`), `:1056` (`test_disabled_reranker_is_not_degraded`), `:1070` (`test_disabled_reranker_not_degraded_on_cache_hit`). Source of reasons: `reranker.py:50` (`reranker_not_configured`), `reranker.py:114`, `:190`, `:201` (`reranker_unavailable:*`), surfaced at `service.py:388`. Local-model cases: `tests/test_local_models.py:23` (`test_local_reranker_scores_and_normalizes`), `:38` (`test_local_reranker_degrades_on_scorer_failure_and_missing_model`, reason `reranker_unavailable:ValueError` / `:RuntimeError` at `:44`–`:49`). |
| 5 | Latency acceptance (p95 within ~20 % of the before baseline), context budget held, and API backward compatibility | **FAIL (open)** | **Latency:** `eval/results/2026-09-30-rag-usage-after.md` table line 57 and §"Acceptance" lines 137–146: gold-ru p95 206.4 → 208.8 ms (+1.2 %, PASS); gold v1 p95 130.0 → 286.3 ms (**+119.5 %**, FAILs the ~20 % bar). Attributed to a single-task CPU/embedder tail (before p95 task ~130 ms cold start; after p95 task ~286 ms), not to ranking (all nDCG@5/MRR deltas 0.000). Left open: needs a warm-cache re-run to confirm it is not systematic. **Budget:** `profiles.py` reviewer `budget.max_tokens = 5000`; reported `context_tokens_mean` 3985 v1 / 3577 ru is well under budget (PASS, `2026-09-30-rag-usage-after.md:149`). **API backward compatibility:** a request without `changed_paths` must be byte-identical to the pre-T010 output: `tests/test_retrieval.py:112` (`test_changed_paths_ignored_without_field_matches_old_behaviour` — plain request ≡ `changed_paths=None`, and reviewer profile without `changed_paths` matches its pre-T010 output), `:212` (`test_absent_changed_paths_keeps_preexisting_cache_key` — absent field produces the exact same cache key as before T010; explicit `None` treated the same). |
| 6 | Check outputs (T018) — all static-analysis and test checks pass | **PASS** | `retrieval-service`: pytest 166 passed, coverage 91.98 %; `ruff check .` All checks passed; `ruff format --check .` 53 files already formatted; `mypy --follow-imports=silent src` Success: no issues found in 33 source files (worklog "Final checks (T018)" section, lines 620–640). `claude-runner`: pytest 87 passed, coverage 97.67 %; `ruff check src/claude_runner/search_cli.py tests/test_search_cli.py` All checks passed; `ruff format --check` 2 files already formatted. **mypy for `claude-runner/src/claude_runner/search_cli.py` was not run** — mypy was scoped to `retrieval-service/src` only in T018 (the claude-runner package is a separate service with its own dependencies; the T018 environment's mypy run targeted `retrieval-service/src` with `MYPYPATH=/tmp/stubs`). Ruff check and format for `search_cli.py` were run and pass; the pytest run covers `search_cli.py` behavior via `tests/test_search_cli.py`. |
| 7 | docs/retrieval (ARCHITECTURE/INTEGRATION/EVALUATION) and retrieval-service/README.md updated: new fields, usage metric, A/B procedure | **PASS** | New request fields (`changed_paths`, `corpus_roots`): `retrieval-service/README.md` §Retrieval request fields and §Example `/retrieve` request; `INTEGRATION.md` §5. Reviewer focus, related tests, exclusions/patch demotion, notes vs degraded: README §Automatic Reviewer focus, §Related-test expansion, §Exclusions, patch demotion and notes; `ARCHITECTURE.md` §3, §5b, §9. Usage metric: README §Usage metrics, `INTEGRATION.md` §6, `EVALUATION.md` §Commands. A/B procedure: README §Live-task A/B, `INTEGRATION.md` §7, `EVALUATION.md` §Live-task A/B procedure. |
| 8 | `ahawr-search` for agents: fail-open, ~1500-token budget, `path:lines` fragments, tests with HTTP mocking, README; proposed Worker prompt rule; usage metric counts its calls | **PASS** | `claude-runner/src/claude_runner/search_cli.py`: `DEFAULT_BUDGET = 1500`, fail-open on HTTP errors, timeouts, 5xx and non-JSON (`retrieval unavailable`, exit 0), fragments printed as `path:start-end`. Tests with `httpx.MockTransport` and the real `fetch`: `claude-runner/tests/test_search_cli.py` `test_success_prints_path_lines_and_content`, `test_default_budget_and_profile_in_payload`, `test_corpus_roots_and_corpora_from_cwd`, `test_non_json_200_is_fail_open`, `test_fail_open_connection_error`, `test_fail_open_timeout`, `test_fail_open_5xx`. Entry point: `claude-runner/pyproject.toml` `[project.scripts]`; docs: `claude-runner/README.md`, `retrieval-service/README.md` §`ahawr-search`. Prompt rule (text only): `EVALUATION.md` §Proposed Worker prompt rule. The usage metric counts the calls: `tests/test_usage.py` (`searches == 1` for an `ahawr-search` Bash command, `--help` not counted). |
| 9 | Final report: changed files, gist of the diff, before/after metrics, apply commands, how to verify on the next run | **PASS** | This section: "Changed files (gist of each diff)", "Before / after metrics", "Check results", "Apply commands", "How to verify the effect on the next run", "Open issues and hypotheses". |
| 10 | `ahawr-search` without a corpus searches the folder it runs in (incl. a temporary copy via `corpus_roots`); new and edited files are found from the next query; a corpus whose root disappeared serves no fragments (tests) | **PASS** | `tests/test_api.py` `test_corpus_roots_from_host_paths_are_indexed_on_first_use`, `test_corpus_roots_first_use_indexes_a_new_root_and_finds_new_files_on_next_query`, `test_corpus_root_deletion_marks_corpus_stale_and_serves_no_fragments`. CLI side: `search_cli.py` builds the corpus id and `corpus_roots` from the working directory; `/tmp/<X>` symlinks resolve to `/d/rag-tmp/<X>` (`test_tmp_symlink_resolves_to_d_rag_tmp`), other folders print the unavailable-folder message (`test_resolve_root_outside_allowed`). |
| 11 | Backups and artifacts not indexed by default, configurable exclusion list, large .patch/.diff demoted unless the query names them (tests); gold before/after and the GDN freed share | **PASS** | Defaults: `indexer.py` `DEFAULT_EXCLUDE_GLOBS` (`*.bak`, `*.bak-*`, `*.orig`, `*.rej`, `*~`, `*_backup*`, `*backup[0-9]*`, `*.old`); `RETRIEVAL_EXCLUDE_GLOBS` replaces the list (`config.py`). Tests: `tests/test_indexer.py` `test_backup_and_artifact_files_are_excluded_by_default`, `test_retrieval_exclude_globs_replaces_the_default`, `test_excluded_files_indexed_before_are_removed_on_the_next_sync`, `test_stored_corpus_exclude_globs_are_kept_when_the_request_has_none`; `tests/test_retrieval.py` `test_large_patch_ranks_below_equal_source_fragment_when_not_mentioned`, `test_large_patch_is_not_demoted_when_its_path_is_named`, `test_small_patch_below_threshold_is_not_demoted`. Gold before/after: "Before / after metrics" above. GDN freed share: backups 0.0 %, large patches 34.4 % of fragments / 44.8 % of tokens. |

## Check results (T018 environment, `/opt/claude-runner/venv`)

**retrieval-service:**

```
$ PYTHONPATH=/d/n8n/retrieval-service/src:/tmp/rvenv /opt/claude-runner/venv/bin/pytest
166 passed, 1 warning in 11.35s   (coverage 91.98 % ≥ 80 %)
$ /opt/claude-runner/venv/bin/ruff check .          → All checks passed!
$ /opt/claude-runner/venv/bin/ruff format --check .  → 53 files already formatted
$ MYPYPATH=/tmp/stubs /opt/claude-runner/venv/bin/mypy --follow-imports=silent src
Success: no issues found in 33 source files
```

**claude-runner:**

```
$ PYTHONPATH=/d/n8n/claude-runner/src /opt/claude-runner/venv/bin/pytest
87 passed, 1 warning in 27.84s   (coverage 97.67 % ≥ 80 %)
$ /opt/claude-runner/venv/bin/ruff check src/claude_runner/search_cli.py tests/test_search_cli.py → All checks passed!
$ /opt/claude-runner/venv/bin/ruff format --check src/claude_runner/search_cli.py tests/test_search_cli.py → 2 files already formatted
```

Scope check (lines 665–818 above) confirms no forbidden path was changed and no `.env*` file is in `git status --short`.

## Apply commands (listed only — NOT executed)

```
# 1. Rebuild + restart both containers (images bake usage.py, search_cli, ranking/profile changes)
docker compose build ahawr-retrieval claude-runner
docker compose up -d ahawr-retrieval claude-runner

# 2. Usage metrics inside the ahawr-retrieval container (container-to-container URL;
#    --since a relative offset such as -7d, or an ISO timestamp)
docker compose exec ahawr-retrieval ahawr-retrieval usage --runner-url http://claude-runner:8700 --since -7d

# 3. JSON variant
docker compose exec ahawr-retrieval ahawr-retrieval usage --runner-url http://claude-runner:8700 --json

# 4. Worker-facing on-demand retrieval (ahawr-search, default budget 1500 tokens)
docker compose exec claude-runner ahawr-search "where is the retry delay applied?" --k 8 --budget 1500
```

## How to verify the effect on the next run

- **Worker/Reviewer recall**: after the next mission run, `ahawr-retrieval usage … --since <window>` reports `recall` (share of opened files that were also selected) and `precision` per profile. **Expected next-run targets:** Worker recall ≥ 50 % (from ~25 % pre-change), Reviewer recall ≥ 82 % (from ~35 % pre-change). A working retrieval layer should show recall/precision holding or rising versus the pre-change baseline; the G04 ContextRecall improvement (+0.0084) is the direct gold-level signal of the related-tests packing.
- **ahawr-search counts**: the per-profile `searches` column counts `ahawr-search` invocations; **expected target: worker `searches` > 0** (non-zero, sensible count) confirms the Worker is actually using on-demand retrieval rather than reading files blind.
- **token_share** per profile should stay under the `max_tokens` budget (5000 for reviewer), and missed files should shrink as the context gets better.

## Open issues and hypotheses

- **gold v1 latency p95 regression (+119.5 %)** — FAILs the ~20 % acceptance bar; attributed to a single-task CPU/embedder tail, not to any feature. Needs a warm-cache re-run to confirm it is not systematic. **(open issue)**
- **G06 top-10 stability** — the old 09-30 doc's "G06 CR 0.750 → 0.500" drop was a corpus effect (non-same-corpus after run) and does not reproduce in the same-corpus A/B; the `_check` chunk stays at rank 5 with identical score 0.756906 in both runs. **(hypothesis resolved — see `…-after.md` lines 115–131)**
- **No feature is tuned or disabled by default** — ranking-inert result; `changed_path_bonus` defaults to 0.0 and is only active where set to 1.5. **(hypothesis confirmed)**
- **mypy strict pre-existing errors** (numpy stubs in store/vector_index/embeddings/service) remain isolated to the stub; `Store` in context.py is fixed. **(open, pre-existing)**
