# RAG quality: research notes

Read-only research on the retrieval eval harness and the versioning rule. No code was changed.
Paths are relative to the repository root `/d/n8n`. Line numbers were read on 2026-10-27.

Corrections to the task brief: the eval CLI is `ahawr-retrieval-eval` (not `ahawr-retrieval eval`),
the ranking metrics live in `eval/metrics.py` (not `eval/ahawr_metrics.py`, which computes AHAWR
task outcomes), and the "Versioning" section is in the root `README.md`, not in
`retrieval-service/README.md`.

## Eval harness

### Entry point

- Console script: `ahawr-retrieval-eval = "ahawr_retrieval.eval.cli:main"`
  (`retrieval-service/pyproject.toml:31`).
- Subcommands (`retrieval-service/src/ahawr_retrieval/eval/cli.py:23-51`): `run`, `compare`,
  `validate-dataset`, `ahawr-metrics`, `build-silver`.
- The main `ahawr-retrieval` CLI (`src/ahawr_retrieval/cli.py`) has no eval subcommand.

### Dataset registration and loading (record schema)

There is no registry. A dataset is a JSON file passed by path with `--dataset` (`cli.py:24`, `:56`).
`EvalDataset.load` (`eval/datasets.py:85-92`) parses it with pydantic and resolves relative corpus
roots against the dataset file's folder (`datasets.py:88-91`).

All models use `extra="forbid"`, so unknown fields fail validation.

- `EvalDataset` (`datasets.py:74-82`): `dataset_id`, `version`, `kind` (`"gold"` or `"silver"`),
  `description`, `provenance`, `corpora` (min 1), `tasks` (min 1).
- `CorpusSpec` (`datasets.py:49-54`): `corpus_id`, `root`, `source_types` (`code`/`doc`, default both),
  `exclude_globs`.
- `EvalTask` (`datasets.py:57-71`): `id`, `profile` (default `worker`), `task` (dict), `review`,
  `query`, `relevant` (min 1 `JudgmentSpec`), `variants` (`VariantSpec` list), `tags`, `weak`
  (default false), `notes`. `EvalTask.judgments()` converts `relevant` to `Judgment` objects.
- `JudgmentSpec` (`datasets.py:23-33`): `path`, `symbol`, `section`, `chunk_id`, `grade` (0-3,
  default 1), `note`.
- `VariantSpec` (`datasets.py:36-42`): `id`, `feedback`, `expect` (`"reuse"` or `"reretrieve"`);
  used to measure cache decisions.

`EvalConfig` (`datasets.py:95-112`) is one retrieval configuration under test: `name`, `options`
(profile overrides), `embedder` (default `{"kind": "hashing"}`), `reranker_url`,
`reranker_api_key`, `reranker_model`, `rag`. `EvalConfig.load` expands `${VAR}` from the
environment (`datasets.py:110-112`, `_interpolate` at `:118`).

Existing data: `retrieval-service/eval/datasets/gold/ahawr-gold-v1.json` and
`ahawr-gold-ru.json`; configs in `retrieval-service/eval/configs/*.json`.

### Metrics that already exist

Ranking metrics (`eval/metrics.py`):

- `ranking_metrics` (`metrics.py:86-104`) returns `Recall@k`, `Precision@k`, `nDCG@k` for each k in
  `DEFAULT_KS = (1, 3, 5, 10)` (`metrics.py:16`), plus `MRR` (`metrics.py:103`).
- `file_level=True` repeats them at file granularity; `runner.py:172-179` stores these as
  `file_*` keys.
- `ContextRecall` is computed in `runner.py:180` from the selected context chunks.
- Per-task `context_tokens`, `degraded_reasons`, `reranker_ms` and `latency_ms` are recorded in
  `runner.py:181-191`.
- Cache metrics: `cache_hit_ratio`, `decision_accuracy`, `false_reuse`
  (`runner.py:232-241`).

Latency percentiles:

- **p50 and p95 of retrieval latency are measured**: `retrieval_latency_ms_p50` and
  `retrieval_latency_ms_p95` in `runner.py:253-254`, computed with `percentile`
  (`metrics.py:124-129`). The mean and the mean reranker latency are at `runner.py:252` and `:255`.
- The same system block also has `context_tokens_mean`, `degraded_rate`, cache ratios and
  `retrieval_calls_per_task` (`runner.py:251-261`).
- `ahawr_metrics.py:172` gives only a p95 of total task latency
  (`total_task_latency_s_p95`), and there is no p50 there.

Outcome metrics (`eval/ahawr_metrics.py`, separate from the retrieval ranking metrics):
`first_pass_rate`, `retry_rate`, `avg_retries_per_task`, `final_task_success_rate`
(`ahawr_metrics.py:154-173`). Inputs come from exported Data Tables (`load_rows`, `:23`) and the
retrieval log (`retrieval_usage`, `:124`).

### How the eval calls the search

- **In process, no HTTP.** `EvalRunner.run_config` (`runner.py:133`) calls `service.retrieve(...)`
  directly (`runner.py:160`, `:215`, `:218`) on a `RetrievalService` built in `_service`
  (`runner.py:84-115`).
- **Its own index, built on each run.** `_service` indexes every corpus in the dataset
  (`runner.py:100-108`) into `work_dir/index-<hash>` (`runner.py:88-89`). The hash comes from the
  embedder and rag settings, so configs with the same embedder and backend share one index.
- **Store: SQLite.** `Settings` is built directly (`runner.py:88-93`), so `database_url` stays at
  its default `None` (`config.py:84-85`). The store is `index.sqlite` in that folder
  (`config.py:119`).
- **Embedder and reranker** are chosen per config: `_embedder` (`runner.py:41-60`, default
  `hashing`), `_reranker` (`runner.py:63-68`, default no-op).

**Needs:** no running service and no prebuilt index. The first run builds the index from the
corpus roots on the local disk. Config `local` embedders and rerankers need model files, and
`openai` or `HttpReranker` need a reachable URL.

Runs and reports: `run_evaluation` (`runner.py:272-289`) writes `<config>.json` per configuration;
`cli.py:56-62` writes `report.md` and `comparison.json`.

### Where a new real dataset plugs in

1. Write a JSON file that matches `EvalDataset` (`datasets.py:74`). Put it in
   `retrieval-service/eval/datasets/gold/` next to the existing gold sets.
2. Check the judgments resolve: `ahawr-retrieval-eval validate-dataset --dataset <file>`
   (`cli.py:35-36`, `_validate` at `cli.py:109-154`, which prints `UNRESOLVED` lines).
3. Run it: `ahawr-retrieval-eval run --dataset <file> --config <config.json> --out <dir>`
   (`cli.py:23-29`).

Silver sets are generated with `build-silver` (`cli.py:44-51`). Their tasks should be marked
`weak: true`, and the runner reports them separately in `strong_only` (`runner.py:267-268`).

## Versioning

- The rule is in `CHANGELOG.md:6`, and the README section is `README.md:761-765` (rule since 2026-10-15).
- A new function raises the middle number by 1 and resets the last to 0 (`0.4.0` → `0.5.0`).
- A bug-fix-only release raises the last number by 100 (`0.5.0` → `0.5.100`), and its entry lists
  the fixed bugs.
- A small change raises the last number by 10; a tiny one (typo, link, one line) by 1.
- Every push adds a `CHANGELOG.md` entry with the version; the number lives in
  `claude-runner/pyproject.toml` and `claude-runner/src/claude_runner/api.py` (`FastAPI(version=...)`).

The CHANGELOG section dated 2026-10-15 is at `CHANGELOG.md:421-441` (versions 0.4.200, 0.4.100 and
an unversioned entry).

## Open points

- `retrieval-service/README.md` does not contain a "Versioning" section, and it does not mention the
  eval CLI. Its documentation table points to `docs/retrieval/EVALUATION.md` for the eval harness.
  That file was not read for this note.
- `docs/review-calibration.md:77-78` quotes the same rule and gives "current version 0.11.0", which is
  older than `CHANGELOG.md:6` (0.11.300).
- No test was run and no code was changed, so nothing here is verified by execution.

## Pipeline: query, fusion, rerank

Read-only. Paths are relative to `retrieval-service/src/ahawr_retrieval/`. Defaults are the values in
the source; a profile can override them through the JSON file named by `RETRIEVAL_PROFILES_FILE`
(`config.py:151`, `load_profiles` at `profiles.py:177`).

### (a) Lexical query: term limit

- Query text: `tsquery_from_match` (`pg_store.py:119`) turns every term of the match query into an
  OR query. It has no limit.
- Term limit: `select_rarest_terms` (`pg_store.py:142`) keeps the rarest terms. It is called from
  `PgStore.lexical_search` (`pg_store.py:329`, call at `:340-342`) with `LEXICAL_MAX_TERMS`.
- Current constants (`pg_store.py`, module level, not read from env):
  - `LEXICAL_MAX_TERMS = 16` (`:134`), the term limit.
  - `LEXICAL_ROW_BUDGET = 10_000` (`:135-137`), the total chunk frequency the chosen terms may match.
  - `LEXICAL_MIN_TERMS = 3` (`:138`), the floor kept even when the budget is exceeded.
  - `_DF_TTL_SECONDS = 600.0` (`:139`), the document-frequency cache lifetime.
- Terms with frequency 0 are dropped (`:153`).
- Result limit: `retrievers.lexical_k` (profile default `RetrieverConfig.lexical_k = 50`,
  `profiles.py:39`) is passed as `limit` at `service.py:735-737`.
- Env: none. Changing the term limit means changing the constant.

### (b) Fusion: RRF weights and k

- Function: `fuse` (`candidates.py:75`). Score per retriever: `weight / (rrf_k + rank)` (`:90`).
  The fused score is normalized by the best candidate (`:95-98`).
- Call: `service.py:452`, `fuse(results, retrievers.weights, retrievers.rrf_k)[: profile.pool_size]`.
- Current defaults, `RetrieverConfig` (`profiles.py:35-45`):
  - `rrf_k = 60` (`:42`).
  - `weights = {"lexical": 1.0, "vector": 1.0, "symbol": 1.2, "fresh": 1.0}` (`:43-45`).
  - `lexical_k = 50` (`:39`), `vector_k = 50` (`:40`), `symbol_k = 20` (`:41`).
  - `pool_size = 60` (`Profile`, `profiles.py:113`): candidates kept after fusion.
- Per-file limit: `BudgetConfig.per_path_limit = 8` (`profiles.py:84`), applied in `context.py:84-85`
  and `:167-168`. `max_chunks = 10` and `max_tokens = 5000` for the Reviewer; `max_chunks = 12` and
  `max_tokens = 6000` for the default (`profiles.py:79-81`).
- Final ranking after the reranker: `rank_candidates` (`ranking.py:97`). Weights are in
  `RankingConfig` (`profiles.py:54-76`): `rerank 0.55`, `fused 0.25`, `exact_symbol 0.10`,
  `path_mentioned 0.08`, `scope_match 0.06`, `test_file 0.0`, `source_prior code 0.02`,
  `history_multiplier 0.5`, `large_patch_penalty 0.8`, `changed_path_bonus 0.0`.
  Without a reranker, `rerank` is added to `fused` (`ranking.py:122`).
- Reviewer overrides (`profiles.py:157-170`): `rerank 0.5`, `fused 0.2`, `path_mentioned 0.12`,
  `test_file 0.06`, `changed_path_bonus 1.5`.
- Gate before context: `min_final_score = 0.5` (`profiles.py:87`), checked at `context.py:77`.
- Env: `RETRIEVAL_LARGE_PATCH_LINES`, default 1000 (`chunking.py:27`), the large-patch threshold
  used by `ranking.py:64`. Nothing else in this section is set from env. The other values are
  profile fields.

### (c) Reranker: on/off, top N, threshold

- Backends: `NoopReranker` (`reranker.py:44`), `HttpReranker` (`reranker.py:60`, `POST /v1/rerank`),
  `LocalReranker` (`reranker.py:147`, ONNX on CPU).
- Selection: `build_reranker` (`service.py:110`), mode from `RETRIEVAL_RERANKER` (`config.py:140`,
  default `auto`). `auto` and `http` use the HTTP reranker when `RETRIEVAL_RERANKER_URL` is set
  (`config.py:141`), otherwise no reranking. `local` uses the CPU model. `none` disables it.
- On/off per profile: `RerankConfig.enabled = True` (`profiles.py:49`). The window is
  `valid[: profile.rerank.top_n]` (`service.py:484`), `top_n = 40` (`profiles.py:50`).
  Rerank call: `service.py:483-487`.
- Threshold: there is no reranker score threshold in the code. The only score gate is
  `min_final_score = 0.5` after ranking (`profiles.py:87`, `context.py:77`).
- Text sent to the reranker: `_rerank_text` (`service.py:890`). The header is excluded unless
  `RerankConfig.include_header` is true (`profiles.py:51`, `service.py:893`).
- Env (`config.py:140-150`):
  - `RETRIEVAL_RERANKER` (default `auto`).
  - `RETRIEVAL_RERANKER_URL` (default unset).
  - `RETRIEVAL_RERANKER_API_KEY` (unset).
  - `RETRIEVAL_RERANKER_TIMEOUT_SECONDS` (default `10`).
  - `RETRIEVAL_RERANKER_MAX_CHARS` (default `4000`), for HTTP.
  - `RETRIEVAL_RERANKER_MODEL` (default `DEFAULT_LOCAL_MODEL`).
  - `RETRIEVAL_RERANKER_MODEL_DIR` (unset).
  - `RETRIEVAL_RERANKER_THREADS` (unset).
  - `RETRIEVAL_RERANKER_LOCAL_MAX_CHARS` (default `2000`), for local.

### Naming for new settings

- Env variables: `RETRIEVAL_<AREA>_<NAME>` in upper case, with the unit as the suffix
  (`_SECONDS`, `_MAX_CHARS`, `_LINES`). Read them in `Settings.from_env` (`config.py:126`) with
  `e.get("NAME", "default")`, and parse them with `int(...)` or `float(...)`.
- Profile fields: snake_case on the pydantic models in `profiles.py` (`_Strict`, `extra` forbidden).
  Give the new field a default and a `Field(..., ge=, le=)` bound, as the existing ones do.
- Module constants that are not env-driven: UPPER_SNAKE with the unit in the name, as in
  `LEXICAL_ROW_BUDGET`.

## Pipeline: fragments and cutoff

Read-only. Paths are relative to `retrieval-service/src/ahawr_retrieval/`. Line numbers were read on 2026-10-27.
No code was changed.

### Current values

- Code fragment size: `ChunkingConfig` (`chunking.py:211-215`): `max_lines=80`, `max_chars=3200`,
  `window_overlap=5`, `doc_max_chars=2400`. Not read from env.
- Overlap: applied only to line windows of module chunks (`_module_chunks`, `chunking.py:718`) and of
  oversized symbols (`_symbol_chunks`, `chunking.py:675`). The window step is `chunking.py:754`.
  The split of symbol gaps (`chunking.py:689`, `:698`) uses overlap 0.
- Doc fragments: paragraph parts under `doc_max_chars` (`_paragraph_parts`, `chunking.py:321`;
  callers `chunking.py:301`, `:362`).
- Stored size: `token_count = estimate_tokens(draft.content)` at index time (`indexer.py:793`,
  `text.py:98`, about 4 characters per token).
- The chunking config enters at `Indexer.__init__` (`indexer.py:262-274`, default `ChunkingConfig()`).
  The only non-default use is the test at `tests/test_text_and_chunking.py:85`.
- Context budget: `BudgetConfig` (`profiles.py:79`): `max_chunks=12` (`:80`), `max_tokens=6000` (`:81`),
  `per_path_limit=8` (`:84`), `min_final_score=0.5` (`:87`), `history_max_share=0.2` (`:88`),
  `overlap_threshold=0.5` (`:89`). The reviewer profile sets `max_chunks=10, max_tokens=5000`
  (`profiles.py:172`, profile starts at `:151`).
- Packing: `select_context` (`context.py:48`) checks in order: `max_chunks` (`:74`), `min_final_score`
  (`:77`), duplicate content (`:80`), `per_path_limit` (`:84`), overlapping lines (`:87`, `_overlaps`
  at `:189`), token budget (`:91`), history share (`:94`). `chunk_cost` (`context.py:40`) adds the
  line-number tokens when `render.line_numbers` is on (default `True`, `profiles.py:96`).
- Related tests: at most `RELATED_TEST_CAP = 3` (`context.py:18`), packed after the originals by
  `_pack_related_tests` (`context.py:123`).
- Call site: `service.py:521-526` (`select_context`), with `profile.budget.max_chunks` and
  `max_tokens`.

### Path header

- Yes. `render_context` (`context.py:304`) emits per chunk `path=<record.path>` (`context.py:326`),
  `file=<root>/<path>` only when the corpus has a root and the source type is code or doc
  (`context.py:330`), then `lines=` (`:332`), `symbol=`, `section=`, `chunk=`, `sha256=`, `version=`,
  `snapshot=`, `freshness=` and `score=` with 3 decimals (`:344`).
- The outer header `=== RETRIEVED CONTEXT ... ===` is at `context.py:317`.
- Called at `service.py:533` and `service.py:1043`, only when `request.render_context` is set
  (`models.py:118`).

### Score for a cutoff

- `rank_candidates` (`ranking.py:97`) sets `candidate.final` at `ranking.py:128`:
  `relevance` (`ranking.py:122-127`) + `deterministic`, then the large-patch penalty and history
  multiplier. The reranker term is `weights.rerank * reranker + weights.fused * fused_norm`
  (`ranking.py:124`). Without a reranker it is `(rerank + fused) * fused_norm` (`ranking.py:122`).
  Outside the reranked window it is `fused * fused_norm` (`ranking.py:127`).
- Inputs on `Candidate` (`candidates.py:18-28`): `fused`, `fused_norm`, `reranker` (set at
  `service.py:501`), `reranker_raw` (`candidates.py:27`).
- The cutoff is `candidate.final < budget.min_final_score` in `select_context` (`context.py:77`), with
  reason `below_min_score` (`context.py:78`). It applies to the composite score, not the raw reranker.
- Visible afterwards: `final=round(candidate.final, 6)` at `service.py:940` and `score=` in the header.

### (d) Fragment size and header: how to measure

- Shorter fragments, no reindex, chunk shape only: `chunk_file(path, text, ChunkingConfig(max_chars=...,
  max_lines=...))` (`chunking.py:239`) is a pure function of the file text. It returns the chunks in
  memory and writes nothing. Measure count, line ranges and `estimate_tokens` per setting.
- Shorter fragments, retrieval effect: not possible on the live index. Chunk boundaries are fixed at
  index time. A test needs a second `Indexer(store, embedder, vector_index, settings,
  chunking=ChunkingConfig(...))` (`indexer.py:262-274`) on a scratch store, which re-embeds the corpus.
- Trimming at context assembly: possible only as an approximation. `select_context` costs a fragment
  with `record.token_count` (`context.py:40-46`), so trimming text afterwards leaves the selection
  unchanged. A script must recompute the cost itself.
- Header: `render_context` (`context.py:304`) only formats text. Calling it on a `selected` list from an
  existing retrieval with the header keys removed in a copy measures the header's token share without
  touching the index.

### (e) Score cutoff: how to measure

- Without reindex: yes. Per request, `RetrieveRequest.options` (`models.py:114`) is merged into the
  profile by `with_overrides` (`profiles.py:126-130`, `service.py:298-300`). Example:
  `options={"budget": {"min_final_score": 0.3}}`. The typed `BudgetOverride` (`models.py:91-93`) only
  has `max_chunks` and `max_tokens`, so `min_final_score` must go through `options`.
- Sweep values in a script against the live index. Retrieval reads the index; it does not rewrite it.
  The pipeline also stores to the cache and the feature log (`service.py:5`). Check those writes before
  a long sweep.
- Caveat: the reranker-off branch (`ranking.py:122`) gives a different score scale. A cutoff tuned with
  the reranker on does not carry over to a run with it off.

## Data sources

Read-only description of what `retrieval-service/src/ahawr_retrieval/usage.py` reads. Paths are relative
to `/d/n8n`. Checked on 2026-10-27 against a temp copy of the retrieval log (`/tmp/rag_eval/`), opened
read-only; no query rows were printed.

### Retrieval log (sqlite)

- File: `<RETRIEVAL_DATA_DIR>/retrieval_logs.sqlite` (`config.py:122-123`; `RETRIEVAL_DATA_DIR` defaults to
  `./var`, `config.py:53`). Opened by `RetrievalLog` (`logstore.py:143`) and by `usage_report`
  (`usage.py:515`), which closes it before the runner call (`usage.py:518`).
- `retrieval_requests` (`logstore.py:25`), one row per retrieval request. Columns used: `request_id`,
  `ts`, `profile` (the role, `worker` or `reviewer`), `context_tokens`, `trace_json`, and the denormalized
  `trace_mission_id` / `trace_task_id`.
- `retrieval_candidates` (`logstore.py:54`), one row per (`request_id`, `chunk_id`). Columns used:
  `request_id`, `path` (relative to the corpus root), `token_count`, `selected`.
- `iter_feature_rows` (`logstore.py:290`) joins candidates to requests on `request_id`; `requests()`
  (`logstore.py:282`) selects `*` from `retrieval_requests` with `ts >= since`.
- Schema drift: the temp copy has no `trace_mission_id` / `trace_task_id` columns (the table ends at
  `reranker`). `RetrievalLog` adds them on open (`logstore.py:157-165`, "v2 logs predate the denormalized
  task correlation keys"). `request_scope` (`usage.py:263-276`) falls back to `trace_json`
  (`state_namespace` or `mission_id`, and `task_id`) when the columns are absent or empty. Opening a copy
  with `RetrievalLog` writes to it, so inspect copies with a read-only connection.

### Runner journal and API

- Run journal: `<runner data dir>/events/<run_id>.jsonl`, one JSON object per line (`events.py:306`,
  `events.py:330-340`). Each entry carries `seq` (1-based, assigned on append), `t` (Unix time) and `kind`.
  Kinds: `user`, `text`, `thinking`, `tool_use` (`id`, `name`, `input`), `tool_result` (`id`, `is_error`,
  `text`), `init`, `retry`, `compact`, `result`, `step` (`events.py:71-139`).
- `tool_use.input` is kept whole up to 8,000 characters; a larger input keeps its keys and clips the long
  values (`events.py:51-57`).
- `GET /v1/runs?limit=N` (`run_views.py`, `list_runs`) returns run summaries: `run_id`, `title`
  (from the prompt, `title_of`), `role`, `started_at`, `status`, and others (`run_views.py:53-75`). The
  summary has no mission or task id.
- `GET /v1/runs/{run_id}/events?after=N` (`run_views.py:110-121`) returns the entries with `seq > N`
  (`events.py:368-381`). `usage_report` calls it with `after=0` for each matched run (`usage.py:533-536`).

### Joining a query to its run

Retrieval request to run, done in `usage.py`:

1. Request key: `(mission_id, task_id)` from `request_scope` (`usage.py:263-276`); role is `profile`.
2. Run key: the summary has no ids, so `enrich_runs` (`usage.py:66-95`) takes `TASK T\d+` from the title
   (`_TITLE_TASK`, `usage.py:63`). It picks the latest request of the same role that started before
   `started_at`, within 6 h for a Worker run with a task id (`WORKER_RUN_GAP_S`, `usage.py:61`) or 600 s for
   a Reviewer run without one (`REVIEWER_RUN_GAP_S`, `usage.py:62`). A task id in the title must match the
   request's task.
3. Match: `_match_run_to_request` (`usage.py:323-357`) accepts a run when its role equals the request's
   `profile`, its key equals the request key, and `request.ts <= started_at < next same-key same-role
   request.ts`. Runs with no match are dropped.

### How "used paths" are derived

`opened_files_from_events` (`usage.py:230-260`) collects the opened paths of each matched run:

- `Read`, `Edit`, `Write`: `input.file_path`, stripped and kept as given (`usage.py:245-248`).
- `Bash`: the first word `ahawr-search` counts as a search call and is not parsed. Otherwise
  `_collect_operands` (`usage.py:179-227`) splits the command on `&&`, `||`, `;`, `|`, runs `shlex.split`,
  and reads operands only of the commands in `READ_COMMANDS` (`usage.py:24-26`: `cat`, `sed`, `head`,
  `tail`, `grep`, `awk`, `less`, `more`, `nl`, `wc`, `sort`, `uniq`). Flags are skipped (`_is_flag`).
  Flags in `_VALUE_FLAGS` (`usage.py:37-54`) consume their value. `grep` and `sed` skip their first
  operand (the pattern or script) unless `-e` or `-f` is given. Redirections and numbers are skipped.
- `_path_arg` (`usage.py:147-157`) is the last filter for a Bash operand. It rejects flags, redirection
  targets, numbers and operators. It strips quotes and a trailing `;`, and keeps the token only if it has
  a `/` or an extension (`_EXTENSION_RE`, `usage.py:133`). A bare word such as `echo` or `ls` is dropped.
- `ahawr-search` calls: Bash first word `ahawr-search`, plus the MCP tool `mcp__ahawr__ahawr_search`
  (`usage.py:22`, `usage.py:249-258`).

Opened paths are matched to the selected paths: an opened path that equals a selected relative path, or
ends with `/` plus it, maps to that path (longest match). Other opened paths stay absolute and show only in
`missed_files` (`_match_opened_to_selected`, `usage.py:305-320`).

Metrics per request, then summed per profile (`compute_usage`, `usage.py:360-499`):

- `precision` = Σ|selected ∩ opened| / Σ|selected| (`usage.py:480`).
- `recall` = Σ|selected ∩ opened| / Σ|opened| (`usage.py:481`). The denominator includes unmatched
  absolute paths (`usage.py:424`), so opening files outside the selection lowers recall.
- `token_share` = tokens of selected files that were opened / Σ `context_tokens` (`usage.py:483`).

Known limits: a `cd x && ahawr-search ...` chain is not counted as a search call (only the first word is
checked). `_path_arg` has no direct test; it is exercised through the Bash fixtures in
`retrieval-service/tests/test_usage.py:163-237` and the end-to-end test at `test_usage.py:507-578`.




## Baseline

Measured on 2026-10-27 against the existing index (read-only; live DB `data/database.sqlite` untouched — timestamp 2026-10-11, no writes). Used the in-process hashing embedder (no embedding service, no GPU). Command:

```
ahawr-retrieval-eval run --dataset real --config retrieval-service/eval/configs/hybrid-symbol.json --out /tmp/rag_eval --freshness-mode verify
```

Key lines from `/tmp/rag_eval/run.log` (90 queries, dataset `real` / `silver` from `eval/data/real_queries.jsonl`):

```
Retrieval quality (hybrid-symbol):
Recall@5: 0.182  Recall@10: 0.215
Precision@5: 0.211  Precision@8: 0.175
MRR: 0.299  nDCG@5: 0.182  nDCG@10: 0.190
ContextRecall: 0.208  file_Recall@5: 0.182  file_MRR: 0.299
```

```
System:
retrieval_latency_ms_mean: 125  p95: 174
context_tokens_mean: 2572
cache_hit_ratio: 0.000  decision_accuracy: 0.000  calls/task: 1.000
```

Flat summary (saved to `/tmp/rag_eval/baseline.json`):

| metric | value |
|---|---|
| precision@5 | 0.211 |
| precision@8 | 0.175 |
| recall@5 | 0.182 |
| recall@8 | 0.203 |
| MRR | 0.299 |
| p50_ms | 120.54 |
| p95_ms | 173.93 |
| number of queries | 90 (silver set from real_queries.jsonl) |
| date | 2026-10-27 |
| index state | read-only; index built in `/tmp/rag_eval/work/index-d23a714a203b` (28 MB sqlite); live database unchanged |

No code or database changed; `git status` shows only `docs/rag-quality.md`.


## Dataset: real_queries.jsonl

Generated by `retrieval-service/src/ahawr_retrieval/eval/real_queries.py` from the live retrieval log
(`/d/rag-tmp/rag-logs-live/retrieval_logs.sqlite`, 4530 requests, 2026-09-27 to 2026-10-27) and the runner
journals (`/data/events/`, 2014 files) plus `GET /v1/runs` (500 runs).

- **Total records**: 90
- **By role**: reviewer 45, worker 45
- **By query_type**: path 42, words 41, code 7 (worker: path 22, words 21, code 2; reviewer:
  path 20, words 20, code 5); assigned by rule: `path` when the query names a path or file,
  `code` when it names an identifier (`snake_case`, CamelCase or `()`), otherwise `words`
- **Selection**: deterministic sample, `--per-role 45`, round-robin across query types (each type's
  records in log ts order), balanced 45/45 by role
- **Secret scan**: the literal verification pattern
  `grep -Eic '(token|password|secret|api[_-]?key|postgres://|chat_id)'` on the file returns 0
  (the builder drops any record matching it, in any field); the builder's `_has_secret`
  (private keys, sk-, gh_, AKIA, key=value, bearer) also finds 0 matches
- **Content scan**: all records have exactly the allowed keys {id, query, role, expected_paths,
  query_type}; queries are single-line (first line of the `components.task` title, cut at 200
  characters) and never start with `{`; all expected_paths are non-empty, exist under `/d/n8n`
  (verified with `os.path.isfile`), and are root-relative

The file is at `retrieval-service/eval/data/real_queries.jsonl`.

## Search gate

- **Flag:** `AHAWR_SEARCH_SKIP_PATH` (env, truthy = `1`/`true`/`on`).
- **Behaviour:** when the flag is set, `search_text` (`search_cli.py:162`) skips retrieval
  if `os.path.exists(query)` — that is, when the query names an existing file or path
  in the working folder. It returns `"search skipped: query names an existing path (...)"`
  instead of calling `/retrieve`. The gate is in `search_text` (`search_cli.py:154-158`),
  so it covers both the CLI (`main`) and any direct import of `search_text`; it does
  not cover `fetch` (`search_cli.py:113`), which bypasses `resolve_root`. The check
  is against the process cwd (`os.path.exists(query)`), not against `root`.
- **Why skipped:** avoids an unnecessary retrieval call when the query is clearly a
  direct file/path reference rather than a content search; the service would index
  the folder (8–9 s on first call) for no useful fragments.
- **Proposed Worker rule (ready to paste):**
  > When `AHAWR_SEARCH_SKIP_PATH` is enabled, do not call `ahawr-search` with a query
  > that is an existing file or folder path — the gate will skip it and return
  > `"search skipped: ..."`. If the target is a file, open it directly (`Read` / `Bash`);
  > if you need content from it, name a concept or symbol rather than the path itself.

## Search gate: insertion point

Read-only. Paths are relative to the repository root `/d/n8n`. Line numbers were read on 2026-10-27.
No code was changed.

- Query received: `main` (`claude-runner/src/claude_runner/search_cli.py:177`). The query is built at
  `search_cli.py:251` (`args.query = (args.query_option or " ".join(words)).strip()`), and the empty-query
  check is at `search_cli.py:252-253`.
- HTTP call: `search_text` (`search_cli.py:143`) calls `resolve_root` (`search_cli.py:154`), then
  `fetch_detailed` (`search_cli.py:159-169`), which POSTs to `url + "/retrieve"` (`search_cli.py:78-79`).
  `main` calls `search_text` at `search_cli.py:260-269`. `fetch` (`search_cli.py:113-124`) is a second
  caller of `fetch_detailed` (`search_cli.py:123`) and bypasses `resolve_root`, so a gate only in `search_text`
  does not cover it.
- Gate position: after `resolve_root` and before `fetch_detailed`, that is in `search_text` at
  `search_cli.py:154-158`. A gate there covers the CLI and any import of `search_text`. A gate placed in
  `main` (after `search_cli.py:254`) would miss direct callers of `search_text`.
- Working directory: there is no `os.getcwd()` call. `main` takes `root = args.root`, or a folder argument
  found by `os.path.isdir` (`search_cli.py:241-249`), and falls back to `"."` (`search_cli.py:250`).
  `resolve_root` applies `os.path.realpath` to it (`search_cli.py:40`), which resolves against the process
  working directory.
- Flags and env: argparse in `main` (`search_cli.py:182-236`). `allow_abbrev=False` (`search_cli.py:191`).
  `parse_known_args` at `search_cli.py:237`; unknown options are printed to stderr (`search_cli.py:238-239`).
  The only env read is `AHAWR_RETRIEVAL_URL`, as the default of `--url` at `search_cli.py:228`. It is
  read when the parser is built, so `monkeypatch.setenv` before `main()` is enough. `DEFAULT_URL`,
  `DEFAULT_TIMEOUT` and `DEFAULT_BUDGET` are at `search_cli.py:21-26`. `ALLOWED_PREFIXES` is at
  `search_cli.py:32`.
- Test pattern (`claude-runner/tests/test_search_cli.py`): `_CapturingTransport` (`test_search_cli.py:23-38`)
  is an `httpx.MockTransport` that stores the last request in `.request`. `_run` (`test_search_cli.py:41-50`)
  calls `main(argv, transport=...)` under `redirect_stdout`. A test passes the flags as `argv`, passes
  `--root /workspace` (allowed prefix), and checks `transport.request` or the stdout string. Env tests use
  `monkeypatch.setenv` (`test_search_cli.py:222-227`). A test that must show nothing was sent asserts
  `transport.request is None` (`test_search_cli.py:88`, `:336`). `search_text` is tested directly at
  `test_search_cli.py:303-336`. Tests are plain functions with `monkeypatch: pytest.MonkeyPatch`.
- Run: `python -m pytest tests/test_search_cli.py -q --no-cov` from `claude-runner/`.

## Experiment variants (read-only; no DB writes; env set per command)

Fresh baseline (run 2026-10-28, `--out /tmp/rag_eval/baseline2`): precision@5 = 0.2089, recall@8 = 0.2033, MRR = 0.2986, p95 = 225.89 ms.
Original baseline (line 415, `--out /tmp/rag_eval`): precision@5 = 0.211, recall@8 = 0.203, MRR = 0.299, p95 = 173.93 ms.
Every variant run uses `--dataset real --config retrieval-service/eval/configs/hybrid-symbol.json --out /tmp/rag_eval/<variant> --freshness-mode verify`.
All percentages below are relative to the fresh baseline2 (precision@5 = 0.2089, recall@8 = 0.2033, MRR = 0.2986, p95 = 225.89 ms).

| variant (env settings) | env used | precision@5 | recall@8 | MRR | p95 (ms) | rel change vs baseline |
|---|---|---|---|---|---|---|
| baseline (no override) | — | 0.211 | 0.203 | 0.299 | 173.93 | — |
| baseline2 (no override, re-run) | — | 0.2089 | 0.2033 | 0.2986 | 225.89 | — |
| lex_boost_path | RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH=1 | 0.2089 | 0.2033 | 0.2986 | 218.21 | prec 0.0%, rec 0.0%, MRR 0.0%, p95 -3.4% |
| lex_keep_phrase | RETRIEVAL_LEXICAL_SHAPE_KEEP_PHRASE=1 | 0.2089 | 0.2033 | 0.2986 | 221.63 | prec 0.0%, rec 0.0%, MRR 0.0%, p95 -1.9% |
| lex_drop_noise | RETRIEVAL_LEXICAL_SHAPE_DROP_NOISE=1 | 0.2089 | 0.2033 | 0.2986 | 225.47 | prec 0.0%, rec 0.0%, MRR 0.0%, p95 -0.2% |
| b_weight_15_05_k30 (lex 1.5, vec 0.5, RRF k=30) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=30, RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5, RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5 | 0.220 | 0.171 | 0.297 | 507.66 | prec +5.3%, rec -0.032, MRR -0.6%, p95 +124.7% |
| b_weight_05_15_k30 (lex 0.5, vec 1.5, RRF k=30) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=30, RETRIEVAL_RANKING_LEXICAL_WEIGHT=0.5, RETRIEVAL_RANKING_VECTOR_WEIGHT=1.5 | 0.189 | 0.152 | 0.266 | 432.53 | prec -9.6%, rec -0.051, MRR -10.9%, p95 +91.5% |
| b_weight_12_10_k60 (lex 1.2, vec 1.0, RRF k=60) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=60, RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.2, RETRIEVAL_RANKING_VECTOR_WEIGHT=1.0 | 0.209 | 0.147 | 0.263 | 454.21 | prec 0.0%, rec -0.056, MRR -11.8%, p95 +101.1% |
| b_rrf_k10 (default weights, RRF k=10) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=10 | 0.178 | 0.204 | 0.304 | 457.44 | prec -14.9%, rec +0.001, MRR +1.7%, p95 +102.5% |
| b_rrf_k60 (default weights, RRF k=60) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=60 | 0.198 | 0.145 | 0.260 | 443.82 | prec -5.3%, rec -0.059, MRR -12.9%, p95 +96.5% |
| b_cap_1 (per-file cap 1) | RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=1 | 0.104 | 0.230 | 0.317 | 347.54 | prec -50.0%, rec +0.026, MRR +6.0%, p95 +53.9% |
| b_cap_2 (per-file cap 2) | RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=2 | 0.151 | 0.226 | 0.306 | 370.08 | prec -27.7%, rec +0.023, MRR +2.4%, p95 +63.8% |
| b_cap_3 (per-file cap 3) | RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=3 | 0.187 | 0.220 | 0.302 | 361.56 | prec -10.6%, rec +0.017, MRR +1.1%, p95 +60.1% |
| combo_1 (fusion + fragment, T014+T015) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=30, RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5, RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5, RETRIEVAL_MAX_FRAGMENT_LINES=40, RETRIEVAL_MAX_FRAGMENT_CHARS=1600 | 0.2289 | 0.1572 | 0.2889 | 195.76 | prec +9.6%, rec -0.046, MRR -3.2%, p95 -13.3% |
| combo_2 (fusion + fragment + lex_boost_path) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=30, RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5, RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5, RETRIEVAL_MAX_FRAGMENT_LINES=40, RETRIEVAL_MAX_FRAGMENT_CHARS=1600, RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH=1 | 0.2289 | 0.1572 | 0.2889 | 166.16 | prec +9.6%, rec -0.046, MRR -3.2%, p95 -26.4% |
| combo_3 (fusion + fragment + per-file cap 3) | RETRIEVAL_RANKING_USE_RRF=1, RETRIEVAL_RANKING_RRF_K=30, RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5, RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5, RETRIEVAL_MAX_FRAGMENT_LINES=40, RETRIEVAL_MAX_FRAGMENT_CHARS=1600, RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=3 | 0.2044 | 0.1913 | 0.2938 | 176.72 | prec -2.1%, rec -0.012, MRR -1.6%, p95 -21.8% |
| c_reranker_threshold_02 (reranker auto + threshold 0.2) | RETRIEVAL_RERANKER=auto, RETRIEVAL_RERANKER_URL=http://host.docker.internal:8200, RETRIEVAL_RERANKER_THRESHOLD=0.2 | not verified — reranker service unreachable (`curl: (7) Failed to connect to host.docker.internal port 8200 after 2 ms: Couldn't connect to server`), fastembed (`local`) unavailable; run completed but no reranker latency recorded (`reranker_latency_ms_mean`: None in `/tmp/rag_eval/c_rerank_auto_02/hybrid-symbol.json`) and metrics identical to baseline | — | — | — | — | — |
| c_reranker_threshold_05 (reranker auto + threshold 0.5) | RETRIEVAL_RERANKER=auto, RETRIEVAL_RERANKER_URL=http://host.docker.internal:8200, RETRIEVAL_RERANKER_THRESHOLD=0.5 | not verified — same blocker: `curl: (7) Failed to connect to host.docker.internal port 8200 after 2 ms: Couldn't connect to server`, fastembed unavailable; no reranker latency impact measurable | — | — | — | — | — |
| d_fragment_lines_40_chars_1600 | RETRIEVAL_MAX_FRAGMENT_LINES=40, RETRIEVAL_MAX_FRAGMENT_CHARS=1600 | 0.2111 | 0.2020 | 0.3087 | 178.84 | prec +1.1%, rec -0.001, MRR +3.4%, p95 -20.8% |
| d_fragment_lines_20_chars_800 | RETRIEVAL_MAX_FRAGMENT_LINES=20, RETRIEVAL_MAX_FRAGMENT_CHARS=800 | 0.2000 | 0.2114 | 0.3169 | 227.15 | prec -4.3%, rec +0.008, MRR +6.1%, p95 +0.6% |
| d_path_header_on | RETRIEVAL_RENDER_PATH_HEADER=1 | 0.2089 | 0.2033 | 0.2986 | 199.12 | prec 0.0%, rec 0.0%, MRR 0.0%, p95 -11.9% |
| e_score_cutoff_03 | RETRIEVAL_RANKING_MIN_FINAL_SCORE=0.3 | 0.2089 | 0.2033 | 0.2986 | 179.50 | prec 0.0%, rec 0.0%, MRR 0.0%, p95 -20.4% |
| e_score_cutoff_05 | RETRIEVAL_RANKING_MIN_FINAL_SCORE=0.5 | 0.2089 | 0.2000 | 0.2983 | 181.07 | prec 0.0%, rec -1.6%, MRR -0.1%, p95 -19.8% |
| e_score_cutoff_07 | RETRIEVAL_RANKING_MIN_FINAL_SCORE=0.7 | 0.1867 | 0.2031 | 0.2976 | 161.38 | prec -10.6%, rec 0.0%, MRR -0.3%, p95 -28.6% |

Notes:
- (a) Each lexical option alone (`RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH`, `RETRIEVAL_LEXICAL_SHAPE_KEEP_PHRASE`, `RETRIEVAL_LEXICAL_SHAPE_DROP_NOISE`) produces identical ranking metrics to the baseline (all three variants: precision@5 = 0.2089, recall@8 = 0.2033, MRR = 0.2986); the p95 latency is within -3.4% to -0.2% of the fresh baseline p95. The three flags are wired (`pg_store.py:128-130`) and exercised per query; the small metric differences vs the original baseline (line 415) come from run-to-run variance, not from the flags.
- (b) Fusion weight pairs: increasing lexical weight at the expense of vector (`15_05_k30`) raises precision@5 by +5.3% but drops recall@8 by -0.032; the reverse (`05_15_k30`) lowers both. RRF with lower k (`k=10`) improves MRR slightly (+1.8%) but reduces precision (-14.9%); with default k (`60`) recall drops (-0.058). Per-file cap (`1`, `2`, `3`) lowers precision as expected (-50.0%, -27.7%, -10.6%) but improves recall@8 (+0.026, +0.023, +0.017) and MRR (+6.0%, +2.4%, +1.1%), with cap=1 giving the highest MRR (0.317) and recall@8 (0.230) but the lowest precision (0.104). No variant reaches precision@5 +20% relative to the fresh baseline2 (needs ≥ 0.2507); the best is `b_weight_15_05_k30` at +5.3%.
- No DB/index writes: every run uses `--freshness-mode verify` and writes to `/tmp/rag_eval/<variant>/work/index-...` (read-only from the live database perspective; no writes to `data/database.sqlite`).
- Key evidence quoted: each variant's `hybrid-symbol.json` summary lines. Example from `lex_boost_path` (re-run, `/tmp/rag_eval/lex_boost_path/hybrid-symbol.json`): `precision@5`: 0.2089, `recall@8`: 0.2033, `MRR`: 0.2986, `p95_ms`: 218.21.
- (c) Reranker (`c_reranker_threshold_02`, `c_reranker_threshold_05`): marked not verified. The reranker service is unreachable — `POST http://host.docker.internal:8200/v1/rerank` fails with `curl: (7) Failed to connect to host.docker.internal port 8200 after 2 ms: Couldn't connect to server` — and the built-in CPU cross-encoder (`RETRIEVAL_RERANKER=local`) requires `fastembed`, which is not installed (`ModuleNotFoundError: No module named 'fastembed'`). `RETRIEVAL_RERANKER_THRESHOLD` is read only by `reranker.py:29-35` and applies to reranker scores; with `RETRIEVAL_RERANKER=auto` the runner degrades silently to no reranking (`degraded_reasons`: empty set, `reranker_latency_ms_mean`: None in `/tmp/rag_eval/c_rerank_auto_02/hybrid-symbol.json`), so no reranker latency impact can be recorded and p95 is not measured for a working reranker (rows 507-508 record p95 as `—` with this reason).
- (d) Fragment limits and path header (all three rows run with the same command as the other rows, each with its own `--out`; `--freshness-mode verify` builds a scratch index per run, so the live index is untouched). Fragment limits (`RETRIEVAL_MAX_FRAGMENT_LINES`, `RETRIEVAL_MAX_FRAGMENT_CHARS`) are read at `chunking.py:30-31` and applied in `chunk_file` (`chunking.py:243-275`); the Indexer chunks through the same `chunk_file` (`indexer.py:749`), so the eval scratch index is re-chunked per setting and retrieval metrics are valid. `d_fragment_lines_40_chars_1600` (run 2026-10-28, `/tmp/rag_eval/d_frag_40_1600/hybrid-symbol.json`): precision@5 = 0.2111, recall@8 = 0.2020, MRR = 0.3087, p95 = 178.84 ms, `context_tokens_mean` = 2184; `d_fragment_lines_20_chars_800` (`/tmp/rag_eval/d_frag_20_800/hybrid-symbol.json`): precision@5 = 0.2000, recall@8 = 0.2114, MRR = 0.3169, p95 = 227.15 ms, `context_tokens_mean` = 1637; `d_path_header_on` (`/tmp/rag_eval/d_path_header/hybrid-symbol.json`): precision@5 = 0.2089, recall@8 = 0.2033, MRR = 0.2986, p95 = 199.12 ms, `context_tokens_mean` = 2574 — the header (`RETRIEVAL_RENDER_PATH_HEADER`, `context.py:20-23`, applied in `render_context` at `context.py:354`) adds no context tokens in this run.
- (e) Score cutoff (`e_score_cutoff_03`, `e_score_cutoff_05`, `e_score_cutoff_07`): all verified (each run builds its own scratch index; `RETRIEVAL_RANKING_MIN_FINAL_SCORE` read at `ranking.py:55-56`, applied at `ranking.py:171-172` and `context.py:77`). Results (`/tmp/rag_eval/e_score_03/hybrid-symbol.json`, `e_score_05/hybrid-symbol.json`, `e_score_07/hybrid-symbol.json`): 0.3 → identical to baseline (p95 179.50 ms, prec 0.2089, rec 0.2033, MRR 0.2986); 0.5 → p95 181.07 ms, prec 0.2089, rec 0.2000, MRR 0.2983; 0.7 → p95 161.38 ms, prec 0.1867 (down -10.6%), rec 0.2031, MRR 0.2976. Every reranker-related row notes p95 explicitly (even when unmeasurable, marked `—` with the reason).

## Conclusion (T016 — parameter selection with thresholds)

Thresholds applied to every measured variant against the fresh baseline2 (line 487: `precision@5` = 0.2089, `recall@8` = 0.2033, `p95` = 225.89 ms):

- `precision@5` must rise by ≥ 20% relative to baseline: `0.2089 * 1.20 = 0.2507`.
- `recall@8` must be ≥ baseline − 0.03: `0.2033 − 0.03 = 0.1733`.
- `p95` must be ≤ 5 s (`≤ 5000 ms`).

Verdict against all three thresholds:

| variant | env variables | prec@5 | vs ≥ 0.2507 | rec@8 | vs ≥ 0.1733 | p95 (ms) | vs ≤ 5000 | verdict |
|---|---|---|---|---|---|---|---|---|
| baseline (baseline2, fresh run) | — | 0.2089 | — | 0.2033 | — | 225.89 | — | — |
| `b_weight_15_05_k30` (best prec lift, T014-style fusion) | `RETRIEVAL_RANKING_USE_RRF=1`, `RETRIEVAL_RANKING_RRF_K=30`, `RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5`, `RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5` | 0.220 | +5.3% (`FAIL`) | 0.171 | −0.032 (`FAIL`) | 507.66 | PASS | `REJECTED`: prec does not reach +20% (0.220 < 0.2507) and recall@8 (0.171) is below 0.1733 |
| `d_fragment_lines_40_chars_1600` (best MRR/p95 combo candidate, T015-style fragment) | `RETRIEVAL_MAX_FRAGMENT_LINES=40`, `RETRIEVAL_MAX_FRAGMENT_CHARS=1600` | 0.2111 | +1.1% (`FAIL`) | 0.2020 | PASS | 178.84 | PASS | `REJECTED`: prec rise only +1.1% (0.2111 < 0.2507) |
| `d_fragment_lines_20_chars_800` | `RETRIEVAL_MAX_FRAGMENT_LINES=20`, `RETRIEVAL_MAX_FRAGMENT_CHARS=800` | 0.2000 | −4.3% (`FAIL`) | 0.2114 | PASS | 227.15 | PASS | `REJECTED`: prec falls (0.2000 < 0.2507) |
| `lex_boost_path` / `lex_keep_phrase` / `lex_drop_noise` | `RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH=1` (or `KEEP_PHRASE=1`, `DROP_NOISE=1`) | 0.2089 | 0.0% (`FAIL`) | 0.2033 | PASS | ~219 | PASS | `REJECTED`: no prec lift |
| `e_score_cutoff_03` | `RETRIEVAL_RANKING_MIN_FINAL_SCORE=0.3` | 0.2089 | 0.0% (`FAIL`) | 0.2033 | PASS | 179.50 | PASS | `REJECTED`: no prec lift |
| `e_score_cutoff_07` | `RETRIEVAL_RANKING_MIN_FINAL_SCORE=0.7` | 0.1867 | −10.6% (`FAIL`) | 0.2031 | PASS | 161.38 | PASS | `REJECTED`: prec drops sharply |
| `combo_1` (fusion + fragment) | `RETRIEVAL_RANKING_USE_RRF=1`, `RETRIEVAL_RANKING_RRF_K=30`, `RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5`, `RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5`, `RETRIEVAL_MAX_FRAGMENT_LINES=40`, `RETRIEVAL_MAX_FRAGMENT_CHARS=1600` | 0.2289 | +9.6% (`FAIL`) | 0.1572 | −0.046 (`FAIL`) | 195.76 | PASS | `REJECTED`: prec +9.6% < +20% (0.2289 < 0.2507) and recall@8 0.1572 < 0.1733 |
| `combo_2` (combo_1 + lex_boost_path) | combo_1 env plus `RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH=1` | 0.2289 | +9.6% (`FAIL`) | 0.1572 | −0.046 (`FAIL`) | 166.16 | PASS | `REJECTED`: prec +9.6% < +20% and recall@8 0.1572 < 0.1733 |
| `combo_3` (combo_1 + per-file cap 3) | combo_1 env plus `RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=3` | 0.2044 | −2.1% (`FAIL`) | 0.1913 | −0.012 (`PASS`) | 176.72 | PASS | `REJECTED`: prec −2.1% (0.2044 < 0.2507); recall and p95 pass but prec gate fails |
| `b_cap_1` / `b_cap_2` / `b_cap_3` | `RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=1` (or 2, 3) | 0.104 / 0.151 / 0.187 | −50.0% / −27.7% / −10.6% (`FAIL`) | 0.230 / 0.226 / 0.220 | PASS | 347.54 / 370.08 / 361.56 | PASS | `REJECTED`: prec far below threshold |
| `b_rrf_k10` / `b_rrf_k60` | `RETRIEVAL_RANKING_USE_RRF=1`, `RETRIEVAL_RANKING_RRF_K=10` (or 60) | 0.178 / 0.198 | −14.9% / −5.3% (`FAIL`) | 0.204 / 0.145 | PASS / `FAIL` (0.145 < 0.1733) | 457.44 / 443.82 | PASS | `REJECTED` |
| `b_weight_05_15_k30` / `b_weight_12_10_k60` | `RETRIEVAL_RANKING_USE_RRF=1`, `RETRIEVAL_RANKING_RRF_K=30` (or 60) with weights 0.5/1.5 (or 1.2/1.0) | 0.189 / 0.209 | −9.6% / 0.0% (`FAIL`) | 0.152 / 0.147 | `FAIL` | 432.53 / 454.21 | PASS | `REJECTED`: both fail prec and recall |

Three measured combinations (run 2026-10-28, same command as the other variants, `--freshness-mode verify`, results in `/tmp/rag_eval/combo_1`, `/tmp/rag_eval/combo_2`, `/tmp/rag_eval/combo_3`):

- `combo_1` = `b_weight_15_05_k30` + `d_fragment_lines_40_chars_1600` → precision@5 = 0.2289, recall@8 = 0.1572, MRR = 0.2889, p95 = 195.76 ms.
- `combo_2` = combo_1 + `RETRIEVAL_LEXICAL_SHAPE_BOOST_PATH=1` → precision@5 = 0.2289, recall@8 = 0.1572, MRR = 0.2889, p95 = 166.16 ms.
- `combo_3` = combo_1 + `RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE=3` → precision@5 = 0.2044, recall@8 = 0.1913, MRR = 0.2938, p95 = 176.72 ms.

Verdict: `NONE ACCEPTED`. Every measured variant — single-variant and combination — fails the `precision@5` +20% relative-rise gate (threshold 0.2507, computed from the fresh baseline2 precision@5 = 0.2089). The best precision lift is `combo_1` and `combo_2` at 0.2289 (+9.6% vs baseline2 0.2089), below the 0.2507 threshold; both also fail recall@8 (0.1572 < 0.1733). `combo_3` passes recall@8 (0.1913 ≥ 0.1733) and p95 (176.72 ms ≤ 5000 ms) but its precision falls (0.2044 < 0.2507). All three combinations pass p95 (195.76, 166.16, 176.72 ms, all ≤ 5000 ms). No measured variant reaches +20% precision, so no parameter set can be accepted.

Rejected with exact reasons (single variants and combinations):
- `b_weight_15_05_k30`: `REJECTED` — prec lift only +5.3% (needs ≥ +20%); rec@8 0.171 < 0.1733.
- `d_fragment_lines_40_chars_1600`: `REJECTED` — prec lift only +1.1% (needs ≥ +20%).
- `lex_boost_path` / `lex_keep_phrase` / `lex_drop_noise`: `REJECTED` — 0.0% prec lift.
- `e_score_cutoff_03` / `e_score_cutoff_05`: `REJECTED` — 0.0% prec lift.
- `e_score_cutoff_07`: `REJECTED` — prec drops −10.6%.
- Per-file caps (`b_cap_1`/`2`/`3`): `REJECTED` — prec drops −50.0% / −27.7% / −10.6%.
- RRF k variants (`b_rrf_k10`/`60`): `REJECTED` — prec drops −14.9% / −5.3%.
- `b_weight_05_15_k30` / `b_weight_12_10_k60`: `REJECTED` — prec drops and recall drops.
- `combo_1` (fusion + fragment): `REJECTED` — prec +9.6% (0.2289 < 0.2507); rec@8 0.1572 < 0.1733.
- `combo_2` (combo_1 + lex_boost_path): `REJECTED` — prec +9.6% (0.2289 < 0.2507); rec@8 0.1572 < 0.1733.
- `combo_3` (combo_1 + per-file cap 3): `REJECTED` — prec −2.1% (0.2044 < 0.2507); recall and p95 pass but prec gate fails.

Untested (not run; no saved result):
- Any 1–3 combination other than `combo_1`, `combo_2`, `combo_3` (e.g., `b_weight_15_05_k30` + `lex_boost_path` alone; `d_fragment_lines_20_chars_800` + `e_score_cutoff_03`; `d_path_header_on` + any fragment setting).
- Reranker threshold variants (`c_reranker_threshold_02` / `05`) — service unreachable, `fastembed` missing; no latency or ranking impact measurable.

## Live comparison

Operator procedure to check a winning parameter set (T016 verdict) on live missions,
comparing retrieval usage before and after the change. The commands are written for the
operator on the host and are not run here; only the env vars of the `ahawr-retrieval`
container change — the index, the database and the runner are not touched.

Minimum: at least 5 live missions in the "before" window and at least 5 in the "after"
window. Each mission issues retrieval requests; the per-role, per-query-type and per-day
breakdown rows are only meaningful once the window holds several requests.

### 1. Capture the baseline before the rebuild

1. Note the current `RETRIEVAL_*` values the container runs with (only the vars that will
   change; do not print the full `.env`).
2. Note `t_start` (ISO UTC) BEFORE the 5 baseline missions begin, then let at least 5 live
   missions run on the current settings.
3. Capture the usage report — the per-profile table plus the T019 breakdown:

```
docker compose exec ahawr-retrieval ahawr-retrieval usage \
  --runner-url http://claude-runner:8700 \
  --runner-key "$RETRIEVAL_RUNNER_API_KEY" \
  --since <t_start> --json > /tmp/usage_before.json
```

The container reads the key from `RETRIEVAL_RUNNER_API_KEY`. The operator must set that
in `.env` to the same value as `CLAUDE_RUNNER_API_KEY`, or pass `--runner-key <key>`
explicitly. If the runner has no key (`CLAUDE_RUNNER_API_KEY` empty), the flag can be
omitted. `--runner-key` defaults to `$RETRIEVAL_RUNNER_API_KEY` (`cli.py:52`); `--db`
defaults to `/data/retrieval_logs.sqlite` (`RETRIEVAL_DATA_DIR=/data`,
`docker-compose.yml:83`). The `--json` payload carries the per-profile dict plus the
`breakdown` key (role / query_type / day), each entry holding `requests`, `selected_files`,
`precision`, `recall` (`cli.py:206-209`, `usage.py:382-416`).

### 2. Rebuild with the winning env vars

The T016 verdict is `NONE ACCEPTED` (docs/rag-quality.md:579): no parameter set passed the
gates, so this procedure applies when a future measurement produces an accepted set. The
example env list below (`combo_1`'s values) illustrates the variable names only; it is not
a recommendation.

1. Set the winning env vars in `.env` (the env column of the accepted T016 row; e.g.
   `RETRIEVAL_RANKING_USE_RRF=1`, `RETRIEVAL_RANKING_RRF_K=30`,
   `RETRIEVAL_RANKING_LEXICAL_WEIGHT=1.5`, `RETRIEVAL_RANKING_VECTOR_WEIGHT=0.5`,
   `RETRIEVAL_MAX_FRAGMENT_LINES=40`, `RETRIEVAL_MAX_FRAGMENT_CHARS=1600`) and leave every
   other var unchanged.
2. Recreate the container and check it is up:

```
docker compose up -d --build ahawr-retrieval
docker compose ps ahawr-retrieval
```

   If only env vars changed, `docker compose up -d ahawr-retrieval` is enough; `--build`
   is needed only when code changed too.
3. Note `t_after` (ISO UTC).

### 3. Capture the usage after the rebuild

After at least 5 live missions have run on the new settings:

```
docker compose exec ahawr-retrieval ahawr-retrieval usage \
  --runner-url http://claude-runner:8700 \
  --runner-key "$RETRIEVAL_RUNNER_API_KEY" \
  --since <t_after> --json > /tmp/usage_after.json
```

### 4. Numbers to compare

Compare the same window in `usage_before.json` and `usage_after.json`:

- Per profile (`worker`, `reviewer`): `selected_files`, `opened_files`, `precision`,
  `recall`, `token_share`, `selected_tokens`, `context_tokens`, `ahawr_search_calls`,
  `missed_files` (`cli.py:218-238`, `usage.py:555-565`).
- T019 breakdown (`breakdown.role`, `breakdown.query_type`, `breakdown.day`, printed as
  "breakdown by role / query_type / day", `cli.py:240-252`): `requests`, `selected_files`,
  `precision`, `recall` for every key.
- Watch for regressions per key: precision or recall down, `token_share` down,
  `ahawr_search_calls` up, or more `missed_files` than before.

### 5. Roll back

Roll back when the after metrics regress (precision/recall down, `token_share` down,
`missed_files` up) or retrieval errors show up in the log during the window:

1. Restore the pre-change `RETRIEVAL_*` values in `.env` (the values noted in step 1).
2. Recreate the container: `docker compose up -d --build ahawr-retrieval` (or
   `docker compose up -d ahawr-retrieval` when only env vars are restored).
3. After at least 5 further live missions, re-capture the usage report with `--since`
   covering the window after the rollback and confirm the per-profile and breakdown
   numbers are back at their baseline levels.

Keep `usage_before.json` and `usage_after.json` as the record; do not delete them.
