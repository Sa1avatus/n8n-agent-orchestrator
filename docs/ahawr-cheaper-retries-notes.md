# Runner retry, compaction and digest flow (research notes)

## T003 — AHAWR and Run Manager workflow nodes

Scope: `AHAWR_v13_ClaudeCode.json` and `Claude_Code_Run_Manager_v1.json`. All references are `file:line` in those JSON files.

### AHAWR_v13_ClaudeCode.json

Retry and session-continuation nodes:

- **Get Mission** (`l40`) — `dataTable get` on the mission table (limit 1). Emits `mission_id`, `enabled`, `objective`, `rules_json`, `acceptance_criteria_json`, `state_namespace`, `retrieval_corpora_json`, `working_directory`.
- **Mission** (`l54`) — `code`. Parses the Get Mission row into fields: `mission_id`, `enabled`, `objective`, `rules`, `acceptance_criteria`, `original_goal` (goal text composed of OBJECTIVE + RULES + ACCEPTANCE CRITERIA), `working_directory_block`, `retrieval_corpora`, `retrieval_corpus_roots`.
- **Prepare Current Task** (`l153`) — `code`. Reads `tasks[]`, `task_index`, `review_feedback`; emits `execution_route`, `current_task`, `worker_prompt` (including `task.acceptance_criteria`).
- **Resume Worker?** (`l3455`) — `if`. Condition: `(worker_run_id or worker_session_id)` AND `worker_status != 'completed'`. True → **Build Worker Run Input**; false → **Prepare Current Task**.
- **Load Previous Worker Report** (`l4019`) — `dataTable get` on "Autonomous Agent Task Attempts" (`ceJ7PFzVJvPN0LsV`). Filters `state_key = Constants.state_namespace`, `task_id` (from `tasks`/`current_task_id`), `event_type = worker_completed`; limit 1; `alwaysOutputData`.
- **Attach Worker Context** (`l3835`) — `code`. Fail-open merge of the retrieval response into base state. Fields: `retrieval_worker_context`, `retrieval_status`, `retrieval_request_id`, `retrieval_cache`, `retrieval_chunks`, `retrieval_degraded`, `retrieval_error`.
- **Build Worker Run Input** (`l3964`) — `code`. Reads `$('Attach Worker Context')` when `fromReport` else `$json`. Task from `s.tasks`/`s.task_index`. If unfinished (`worker_session_id` non-empty & status not completed) → `resumeText` containing `task.acceptance_criteria`; else full input with `worker_prompt`, `working_directory_block`, TASK id/title, OBJECTIVE, ACCEPTANCE CRITERIA (`task.acceptance_criteria`), VERIFICATION, SCOPE, `retrieval_worker_context`, `review_feedback`, previous worker report (truncated 16000). Mission reached via `$('Mission').first().json.working_directory_block` and `working_directory`. Writes `role`, `run_id`, `session_id`, `input`, `resume_input`, `model`, `provider`, `poll_seconds`, `max_polls`, `max_retries`, `retry_count`, `retry_delays`, `state_key`, `service_model`, `service_provider`, `runner_url`, `runner_api_key`, `working_directory`.
- **Build Reviewer Run Input** (`l3978`) — `code`. Reads `$('Reviewer State')` (`reviewer_run_id`, `reviewer_session_id`, `state_key`), task from one of the state restore nodes (`Prepare Current Task`, `Restore Saved Task State`, `Restore Task After State Save`, `Restore Retry State`, `Restore Plan After State Save`), `$('Load Worker Result').worker_output`, `$('Mission').first().json.original_goal` + `working_directory_block`. Writes the same run-manager fields plus `retrieval_reviewer_context`, `task.acceptance_criteria`, `verification`, `scope`, previous-review block.
- **Retry Current Task** (`l406`) — `code`. Checks `max_attempts_per_task`; writes `task_attempt += 1`, `review_feedback`, `execution_route` (`'failed'`/`'run_task'`).
- **Restore Retry State** (`l2915`) — `code`. Restores `task_attempt`, `worker_session_id`, `review_feedback` from saved state for the retry branch.
- **Load Worker Result** (`l2960`) — `code`. Extracts `worker_output` from the worker run result.
- **Save Worker Run Context** (`l3367`) / **Save Reviewer Run Context** (`l3354`) — `code`. Persist run/session ids and status to task state.
- **Reviewer State** (`l599`) — `set` node. Holds `reviewer_run_id`, `reviewer_session_id`, `state_key` for the reviewer branch.
- **Evaluate Worker** (`l226`) / **Evaluate Reviewer** (`l299`) / **Parse Review** (`l346`) / **Persist Parsed Review** (`l2463`) — parse and route worker/reviewer outcomes.
- **Load Task State** (`l802`) — `code`. Restores saved task state (`task_index`, `tasks`, `worker_session_id`, `review_feedback`, etc.).
- **All Tasks Completed?** (`l187`) / **Worker Start** (`l213`) / **Resume Reviewer?** (`l3411`) / **Review Passed?** (`l380`) / **Advance Task** (`l393`) — routing nodes.
- **Build Worker Retrieval Request** (`l3923`) / **Build Reviewer Retrieval Request** (`l3936`) — read `mission.mission_id`, `retrieval_corpus_roots`, task fields (incl. `acceptance_criteria`).

Connections (worker branch): `Attach Worker Context` → `Load Previous Worker Report` → `Build Worker Run Input` → `Worker Start`. Reviewer branch: `Build Reviewer Run Input` → `Reviewer Start` (mapping in `connectionMappings`, ~`l5063+`).

### Claude_Code_Run_Manager_v1.json

Retry, resume, compaction, digest nodes:

- **Normalize Request** (`l28`) — `code`. Normalizes the incoming request.
- **Start / Resume Run** (`l71`) — `httpRequest` POST `/v1/runs`. Sends `session_id` and builds `resumeText` when `retry_count > 0` and `session_id` is set.
- **Capture Run Response** (`l84`) — `code`. Uses the Retry Controller json when executed.
- **Get Run Status** (`l115`) — `httpRequest` GET `/v1/runs/{id}`.
- **Evaluate Status** (`l128`) — `code`. Detects `context_overflow`/transient failures, increments `poll_count`, sets `route`.
- **Resume Existing Session** (`l215`) — `httpRequest` POST `/v1/runs` with `session_id` (on `restart_session`).
- **Retry Controller** (`l296`) — `code`. Compares `retry_count` vs `max_retries`; sets `retry_delay_seconds` (default delays `[15,30,60,120,300]`).
- **Poll Wait** (`l401`) — `wait` for `poll_seconds`.
- **Build Result** (`l415`) — `code`. Final shape: `role`, `run_id`, `session_id`, `status`, `route`, `output`, retry fields, `compression_handoff`, `handoff_from_session_id`, etc.
- **Load Task State — Run Context** (`l681`) / **Build Persist State — Run Context** (`l695`) / **Load Task State — Status** (`l723`) — persist/restore run-context state.
- **Has Session?** (`l1035`) — `if` — whether a session exists.
- **Mark Compression Target — Start** (`l1048`) / **Mark Compression Target — Resume** (`l1061`) — `code` — flag that compaction is needed.
- **Compact Session** (`l1238`) — `httpRequest` POST `/v1/sessions/{id}/compact` with body `{mode:'always'}` on `context_overflow`.
- **Evaluate Compression** (`l1074`) — `code`. Parses `compression_status` (`completed`/`skipped`/`failed`).
- **Compression Failed?** (`l1108`) — `if` — routes to digest/handoff path when compaction failed.
- **Get Session Digest** (`l1275`) — `httpRequest` GET `/v1/sessions/{id}/digest` with `max_chars=16000`.
- **Compression Target?** (`l1142`) — `if` — decides resume-in-place vs fresh-session handoff.
- **Build Compression Handoff** (`l1155`) — `code`. Builds `handoff_input` from the digest (`digest` field) + `roleSession` from persisted state.
- **Start New Session from Handoff** (`l1198`) — `httpRequest` POST `/v1/runs` with `handoff_input`.

Connections: `Compact Session` → `Evaluate Compression` → `Compression Failed?` → {`Get Session Digest`, `Compression Target?`}; `Compression Target?` → {`Resume Existing Session`, `Start / Resume Run`}; `Build Compression Handoff` → `Start New Session from Handoff` → `Capture Run Response`.

### Mission goal and acceptance_criteria reachability

- **Mission-level** `goal` and `acceptance_criteria` live in the **Mission** node output (`original_goal`, `acceptance_criteria`).
- **Build Worker Run Input** (`l3964`) reaches mission data via `$('Mission').first().json.working_directory_block` and `$('Mission').first().json.working_directory`. The per-task `acceptance_criteria` comes from the plan's task object (`task.acceptance_criteria`) via **Prepare Current Task** (`l153`) or the state-restore nodes — not directly from the mission-level `acceptance_criteria` field.
- **Build Reviewer Run Input** (`l3978`) reaches mission data via `$('Mission').first().json.original_goal` and `$('Mission').first().json.working_directory_block`. Task-level `acceptance_criteria` again from the task object via the state-restore nodes.
- In both Run Input nodes, the mission-level `acceptance_criteria` field is **not read directly**; it is embedded inside `original_goal` (the composed goal text). The per-task `task.acceptance_criteria` is the one actually injected into the prompt.

### Minimal set of nodes to change (items 1 and 4 of the mission)

**Item 1 — fresh-session retry.** The existing chain is `Attach Worker Context` → `Load Previous Worker Report` → `Build Worker Run Input` → `Worker Start` (connections object: `Attach Worker Context` at `AHAWR_v13_ClaudeCode.json:5018-5022`, `Load Previous Worker Report` at `:5124-5128`, `Build Worker Run Input` at `:5102-5106`). The `Resume Worker?` IF node (`:3455`) routes true → `Build Worker Run Input`, false → `Prepare Current Task` (`:4774-4790`). To add a fresh-session-with-digest retry path:

- **Build Worker Run Input** (`AHAWR_v13_ClaudeCode.json:3964`) — currently emits either `resumeText` (unfinished session) or `fullInput` (fresh). The new branch: when the flag is set and a previous session exists, emit a fresh-session input seeded with the digest instead of `resumeText`. Reason: this is where the resume-vs-fresh decision is made, and it controls what `input` and `session_id` are sent to the Run Manager.
- **Start / Resume Run** (`Claude_Code_Run_Manager_v1.json:71`) — POST `/v1/runs`. Currently sends `session_id` for resume. For a fresh-session retry, omit `session_id` and carry a `handoff_input` (the digest) instead. Reason: this is the runner POST that decides whether the run attaches to an existing session or starts a new one.
- **Build Compression Handoff** (`Claude_Code_Run_Manager_v1.json:1155`) — already builds the digest-based `handoff_input` from the session digest. Reuse it for the fresh-session retry. Reason: the digest is already computed here; the fresh-session path needs the same `handoff_input` text.
- **Start New Session from Handoff** (`Claude_Code_Run_Manager_v1.json:1198`) — POST `/v1/runs` with `handoff_input`. This is the existing fresh-session start; the retry path routes here instead of `Start / Resume Run`. Reason: it already handles the "new session with digest" case.

**Item 4 — MISSION ACCEPTANCE CRITERIA block.** Add the mission-level `acceptance_criteria` to the worker prompt:

- **Build Worker Run Input** (`AHAWR_v13_ClaudeCode.json:3964`) — currently writes `task.acceptance_criteria` (per-task) but does not read `$('Mission').first().json.acceptance_criteria` (mission-level). Add `$('Mission').first().json.acceptance_criteria` and a short objective line to the `fullInput` block. Reason: the mission-level criteria are not in the per-task object; they are only embedded inside `original_goal` in the composed goal text, which is not currently injected into the worker prompt.
- **Build Reviewer Run Input** (`AHAWR_v13_ClaudeCode.json:3978`) — already has `$('Mission').first().json.original_goal` (which includes the mission-level `acceptance_criteria` as part of the composed goal text). **No change needed**: the mission-level acceptance criteria are already present in the reviewer prompt via `original_goal`.

## 1. Functions and config keys (where things live)

### claude-runner/src/claude_runner/runs.py
- `RunManager.start` — `runs.py:146`. Single entry point for both fresh and resumed runs. Takes a `StartRequest` (`input`, `model`, `role`, `provider`, `session_id`, `working_directory`). If the given `session_id` is already active, it attaches to that run (`runs.py:156-160`). Otherwise it binds a Claude session via `_bind` and launches one run.
- `RunManager._bind` — `runs.py:181`. Returns `(claude_session_id, resume, created)`. Returns `resume=True` when the session's transcript exists on disk (`runs.py:185-186`); `resume=False` and a new UUID when the session is new (`runs.py:190-194`).
- `RunManager._launch` — `runs.py:199`. Records the prompt event and spawns `_execute`.
- `RunManager._execute` — `runs.py:220`. Builds the command via `build_command`, probes the context window, runs the subprocess, and finishes the run record via `_finish`.
- `RunManager.compact` — `runs.py:448`. The runner-initiated `/compact` before resuming. Computes the threshold, skips if below, and launches a `kind="compact"` run.
- Session mission mapping (used in 4.1/4.7): session `95af7c4d…` (Sep 28, title "TASK T001: Исследовать runner…" / "Составить текст сводки") and session `2df8134c…` (Sep 28, "TASK T006: Составить текст короткого струк…" / the "Prompt is too long" overflow retry) are the `ahawr-fast-compaction` mission sessions; session `861530c1…` (T013 "Usage metric core", Sep 30–Oct 1) and `c12da75d…` (T011, Sep 30) are `ahawr-rag-effectiveness`; session `01776e40…` (Sep 29, "TASK T011: Документировать переменные и по…") is the fast-compaction-era T011 session. Evidence: run titles in the listing (`GET /v1/runs?limit=500`) and `cmp_734625e9`'s seq 5 compact-summary text, which is the T013 mission summary.
- `RunManager._finish` — `runs.py:356`. Updates run status, context tokens, cost; writes an `end` event.
- `RunManager.get` — `runs.py:421`. Reads a run; maps `interrupted` → `run_not_found` (the Run Manager treats that as "resume the saved session").
- `RunManager.cancel` — `runs.py:430`. Cancels a running run.

### claude-runner/src/claude_runner/claude_cli.py
- `build_command` — `claude_cli.py:106`. Builds the `claude` CLI argv. Chooses `--resume <id>` vs `--session-id <id>` (`claude_cli.py:123`). For `compact=True` it omits permission/tool args and adds the PreCompact settings hook (`claude_cli.py:146-166`).
- `_compact_settings_path` — `claude_cli.py:45`. Writes a settings file with a `PreCompact` hook that prints the compact instructions to stdout; Claude Code appends that to the compaction request.
- `_search_first_settings_path` — referenced at `claude_cli.py:162`.
- `transcript_exists` — imported at `runs.py:21`; used by `_bind` to decide resume vs fresh.

### claude-runner/src/claude_runner/context_probe.py
- `probe_context` — `context_probe.py:65`. Reads `--ctx-size` for the model from llama-server `GET /v1/models`. Returns the window (int) or `None` on failure.
- `ctx_from_models` — `context_probe.py:44`. Extracts the per-slot context from the `/v1/models` payload (handles parallel slots).
- `max_trigger` — `context_probe.py:83`. The latest safe autocompact trigger: `window - min(max_output, TOOL_RESERVE) - PRECOMPUTE_BUFFER`.
- `compact_pct` — `context_probe.py:90`. Autocompact trigger as a % of the window: `COMPACT_PCT`, else `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, else static `44344/65536` = **67.66%**.
- `autocompact_trigger` — `context_probe.py:103`. `min(window * compact_pct/100, max_trigger(window))`.
- `resume_compact_threshold` — `context_probe.py:108`. The pre-resume `/compact` threshold: `COMPACT_MIN_PCT` of the window, else `COMPACT_MIN_TOKENS` scaled from 65536; capped at the autocompact trigger.
- `window_env` — `context_probe.py:119`. Returns the env overrides (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) for a probed window.

### claude-runner/src/claude_runner/config.py
- `ProviderProfile` — `config.py:171`. Per-provider settings: `compact_min_tokens`, `compact_min_pct`, `compact_pct`, `context_probe_url`, `context_probe_key`, `autocompact_threshold`, `max_output_tokens`, `search_first`, `env`.
- `PROVIDER_RUNNER_KEYS` — `config.py:151`. Keys that configure the runner (not the process): `COMPACT_MIN_TOKENS`, `COMPACT_MIN_PCT`, `CONTEXT_PROBE_URL`, `CONTEXT_PROBE_KEY`, `COMPACT_PCT`, `SEARCH_FIRST`.
- `AUTO_COMPACT_WINDOW = 65_536` — `config.py:165`. The static local-model window.
- `AUTO_COMPACT_TOOL_RESERVE = 20_000` — `config.py:166`.
- `DEFAULT_MAX_OUTPUT_TOKENS = 8_192` — `config.py:167`.
- `Settings.compact_mode` — `config.py:375` (default `"auto"`; `CLAUDE_RUNNER_COMPACT_MODE`, `config.py:400`).
- `Settings.compact_min_tokens` — `config.py:376` (default `120_000`; `CLAUDE_RUNNER_COMPACT_MIN_TOKENS`, `config.py:443`).
- `Settings.compact_instructions` — `config.py:377` (`CLAUDE_RUNNER_COMPACT_INSTRUCTIONS`, `config.py:424-427`).
- `Settings.local_compact_instructions` — `config.py:380` (`CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS`, `config.py:445`).
- `Settings.compact_timeout_seconds` — `config.py:381` (`CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS`, `config.py:446`).
- `_providers` — `config.py:279`. Parses `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` blocks.
- `_autocompact_threshold` — `config.py:209`.
- `_pct` — used at `config.py:301-302` to parse `COMPACT_PCT` / `COMPACT_MIN_PCT`.

### claude-runner/src/claude_runner/handoff.py (the digest module)
- `session_digest` — `handoff.py:75`. Builds the digest from the session's runs' activity logs.
- `_run_lines` — `handoff.py:43`. Renders one run's event log into digest lines.
- Per-entry char limits — `handoff.py:17-21`: `TEXT_CHARS=1500`, `TOOL_INPUT_CHARS=300`, `TOOL_RESULT_CHARS=400`, `RESULT_CHARS=3000`, `PROMPT_CHARS=400`.

### claude-runner/src/claude_runner/api.py
- `POST /v1/runs` — `api.py:144-161`. Maps the `RunBody` (+ headers) to a `StartRequest` and calls `manager.start`.
- `GET /v1/sessions/{session_id}/digest` — `api.py:185-193`. Calls `session_digest(runs, session_id, max_chars)` with `max_chars` defaulting to `12_000`, range `500..200_000`.
- `POST /v1/sessions/{session_id}/compact` — `api.py:195-200`. Calls `manager.compact`.
- `GET /v1/sessions/{session_id}` — `api.py:176-183`.
- `GET /v1/runs/{run_id}`, `POST /v1/runs/{run_id}/cancel` — `api.py:168-174`.

## 2. Current retry sequence (step by step)

There is **no explicit "needs_changes" code path inside `claude-runner`**. The `needs_changes` retry is driven by the n8n Run Manager, which then re-calls the same runner endpoints. The runner's own machinery is:

1. **Resume vs fresh is decided by `_bind` (`runs.py:181`).**
   - If the requested `session_id` is known and its transcript exists → `resume=True`, reuse the same `claude_session_id` (`runs.py:185-186`).
   - If it is unknown but a transcript exists at that id → bind it and resume (`runs.py:187-189`).
   - Otherwise → new UUID, `resume=False`, `created=True` (`runs.py:190-194`).
   - `build_command` (`claude_cli.py:123`) emits `--resume <id>` when `resume=True`, else `--session-id <id>`.

2. **The pre-retry `/compact` is a separate, explicit step — it is NOT auto-run inside `start`.**
   - The Run Manager calls `POST /v1/sessions/{id}/compact` (`api.py:195`) → `RunManager.compact` (`runs.py:448`).
   - Threshold resolution in `compact` (`runs.py:481-490`):
     - `min_tokens` (from the request body) wins if provided (`runs.py:452`, `481`).
     - Else, if the provider has `compact_min_tokens` (`COMPACT_MIN_TOKENS`), use that (`runs.py:482`).
     - Else, if the provider has `context_probe_url`, probe the window and set
       `threshold = resume_compact_threshold(window, provider, threshold)` (`runs.py:486-490`).
   - `resume_compact_threshold` (`context_probe.py:108-116`): `COMPACT_MIN_PCT` of the window, else `COMPACT_MIN_TOKENS` scaled from 65536; **capped at the autocompact trigger** so a resumed session starts below it.
   - Skip rules (`runs.py:468-495`): `mode=="off"` → `skipped("compaction_disabled")`; session busy → `skipped("session_busy")`; session missing → `skipped("session_not_found")`; `mode=="auto"` and `context_tokens < threshold` → `skipped("below_threshold")`.
   - If it compacts, it launches a `kind="compact"` run with prompt `/compact [<instructions>]` (`runs.py:499-529`), waiting on `compact_timeout_seconds` (`runs.py:530`, `runs.py:256`).
   - The local provider gets custom instructions: `/compact <text>` rides on the command (`runs.py:504`) and/or the PreCompact hook (`_compact_settings_path`, `claude_cli.py:45`).

3. **The resume run itself.**
   - `start` (`runs.py:146`) → `_bind` → `_launch` (`runs.py:176`) → `_execute` (`runs.py:220`).
   - `_execute` probes the window again (`runs.py:259-278`) and applies `window_env` overrides (`runs.py:264`), so the resumed run runs with the probed window + autocompact trigger.
   - **Auto-compaction at 67.66%** is Claude Code's own behaviour: the runner only sets the env (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) via `window_env`; the CLI performs the autocompact itself. The 67.66% figure is `compact_pct` (`context_probe.py:90-100`) = `44344 / 65536`, i.e. the static trigger's share of the 65536 window.

4. **Digest is built and served separately.**
   - `GET /v1/sessions/{id}/digest` (`api.py:185`) → `session_digest` (`handoff.py:75`).
   - `session_digest` reads the session's last 500 runs (`handoff.py:76`), renders each run's event log via `_run_lines` (`handoff.py:43`), then keeps **newest-first** lines until the `max_chars` budget is hit (`handoff.py:84-88`), prefixing `[N earlier entries omitted]` if any are dropped (`handoff.py:91-92`).
   - **How big it can get:** `max_chars` defaults to `12_000` and may be requested up to `200_000` (`api.py:187`). The digest is a hard-capped character budget, so its size is bounded by that query parameter.
   - The n8n "Compression Target?" / "Build Compression Handoff" node (see `Claude_Code_Run_Manager_v1.json`) uses the digest to hand the progress to a fresh session when compaction fails.

### Resume vs fresh, in one line
- **Resume**: same `session_id`, transcript exists → `_bind` returns `resume=True`, CLI uses `--resume`; the session's history is in-context.
- **Fresh**: new `session_id`/UUID → `resume=False`, CLI uses `--session-id`; the session starts empty, and (when used by the Run Manager) is seeded with the digest from the previous session.

## 3. Extension points for a switchable fresh-session retry

The clean insertion point is **`RunManager.start` / `_bind`** (`runs.py:146` / `runs.py:181`), with a new config knob read from env in `Settings.from_env` (`config.py:395`) and exposed as a `ProviderProfile` field.

- **Proposed config variable:** `CLAUDE_RUNNER_RETRY_FRESH_SESSION` (env key) with `ProviderProfile.retry_fresh_session: bool` (per-provider), defaulting to the global `Settings.retry_fresh_session: bool = False`. It would be added to `PROVIDER_RUNNER_KEYS` (`config.py:151`) and parsed in `_providers` (`config.py:279`) alongside `COMPACT_MIN_PCT` / `SEARCH_FIRST`.
- **Insertion point:** in `_bind` (`runs.py:181`), when the flag is on and the request carries a previous `session_id` whose transcript exists, **do not reuse it** — instead start a fresh `claude_session_id` (`resume=False`) and, before launching, fetch `session_digest(runs, old_session_id, max_chars)` (`handoff.py:75`, `api.py:193`) and prepend it to the run's `input` (the "resume" prompt) so the new session carries forward what the old one already did. `build_command` (`claude_cli.py:123`) already handles `resume=False` via `--session-id`, so no CLI change is needed.
- This makes "fresh-session retry" switchable without touching the compaction or digest paths; it composes with the existing `COMPACT_MODE`/`COMPACT_MIN_PCT`/`COMPACT_MIN_TOKENS` settings.

## 4. Measured timings (T002: runner events and logs)

Source: live runner API, `GET http://claude-runner:8700/v1/runs?limit=500` and
`GET /v1/runs/{id}/events` (store keeps the newest 500 runs, covering
2026-09-28T16:50Z → 2026-10-05T20:25Z). Every row below cites the run id and the
event seq (from `events`) or the run record field it came from.

### 4.1 Compactions of the two mission sessions

Mission attribution by run titles and dates: the `ahawr-fast-compaction`
session is `95af7c4d…` (Sep 28, T001/T006 "Исследовать runner…"/"Составить текст
сводки"); `cmp_734625e9` (session `861530c1…`) belongs to
`ahawr-rag-effectiveness` (T013 "Usage metric core", Sep 30–Oct 1 — its compact
prompt text is the T013 summary). All compactions below have
`trigger=manual`, i.e. the runner's `POST /v1/sessions/{id}/compact`
(`runs.py:448`); no event in the stored window has `trigger=auto`, and the
auto-compact at the 67.66% trigger left no `compact` event.

| Run id | Session (mission) | pre → post tokens (event seq) | Duration (started→finished, result `duration_ms`) | Cost (run share) |
|---|---|---|---|---|
| cmp_9c9e19e8 | 95af7c4d (fast-compaction, Sep 28) | 43269 → 11866 (seq 3) | 8m31s (17:06:07→17:14:38, 510117 ms) | 1.5303 |
| cmp_fa43f633 | 2df8134c (fast-compaction, Sep 28) | 43841 → 8074 (seq 5) | 6m54s (21:23:55→21:30:49, 413127 ms) | 7.3582 |
| cmp_734625e9 | 861530c1 (rag-effectiveness, Oct 1) | 61766 → 2952 (seq 4) | 6m47s (04:09:54→04:16:41, 405717 ms) | 0.1747 |

`cmp_734625e9` details from its events: seq 1 prompt `/compact` with the short
1500–2000-token structured instructions (hard cap 2000); seq 2
`context_window` probe = **98304**; seq 4 `compact` manual, 61766 → 2952;
seq 6 `user` event carries the PreCompact hook stdout (the same instructions);
seq 7 `result` `duration_ms=405717`, `cost_usd=0.174688` (this run's share);
final context 2952 tokens.

### 4.2 Other compactions in the stored window (for scale)

Same columns, trigger manual for all:

| Run id | pre → post | Duration | Cost |
|---|---|---|---|
| cmp_0218a78c | 43527 → 9558 | 6m51s (409577) | 2.5040 |
| cmp_7f3d625c | 45068 → 7776 | 9m34s (572898) | 4.1762 |
| cmp_824b956d | — (no compact event; `context_tokens` null) | 2m54s (173037) | 1.5807 |
| cmp_e5c13955 | 49526 → 7547 | 5m25s (324098) | 0.5757 |
| cmp_3feb5270 | 43073 → 8460 | 5m46s (345387) | 0.6622 |
| cmp_8b4f14ce | 51568 → 5775 | 7m03s (421883) | 2.0619 |
| cmp_a7099243 | 45798 → 13138 | 4m36s (274717) | 0.4598 |
| cmp_5db8dbf8 | 47876 → 3433 | 5m36s (334804) | 0.9701 |
| cmp_aa415ef1 | 53378 → 9735 | 5m32s (331578) | 1.9182 |
| cmp_010e28b1 | 56401 → 6103 | 6m07s (365478) | 4.7873 |
| cmp_74ea28f2 | 60761 → 16626 | 6m53s (410951) | 1.0960 |
| cmp_b6de3532 | 61127 → 2161 | 7m19s (437974) | 6.1413 |
| cmp_c16cca02 | 65081 → 8084 | 7m32s (451090) | 3.4640 |
| cmp_c2efed05 | 60367 → 10267 | 7m05s (425182) | 2.6297 |

Pre-compact context grew from ~43K (Sep 28) to 47–65K (Oct 5): consistent with
the CHANGELOG note that a 96K Worker was compacted at 46–52K before each retry
(`CHANGELOG.md:100-104`). Post-compact summaries span 2161–16626 tokens.

Sum of the pre-retry compactions attributed to `ahawr-fast-compaction`
(§4.1: `cmp_9c9e19e8` 8m31s, `cmp_fa43f633` 6m54s; plus `cmp_0218a78c` 6m51s
and `cmp_7f3d625c` 9m34s from the fast-compaction-era T011 session `01776e40`):
15m25s for the two main sessions, 31m50s with the `01776e40` pair. The mission
text's "32 min (5–8 min each)" is reproduced by the four compactions of the
fast-compaction sessions within a few seconds (31m50s ≈ 32 min).

### 4.3 Retry (resume) runs of the two sessions

Durations from `started_at`/`finished_at` in the run listing; `context_tokens`
is the run record value at finish; cost is the run's share.

| Run id | Session (mission) | Title | Duration | ctx tokens | Cost |
|---|---|---|---|---|---|
| run_2fc1217c | 861530c1 (rag, T013) | RESUME TASK T013 | 1h01m03s (02:28:25→03:29:28) | 44127 | 2.8446 |
| run_355b6192 | 861530c1 (rag, T013) | TASK T013 | 16m32s (03:30:31→03:47:03) | 50641 | 0.5681 |
| run_b52ee2f7 | 861530c1 (rag, T013) | TASK T013 | 19m36s (03:48:02→04:07:38) | **60866** | 0.6612 |
| cmp_734625e9 | 861530c1 (rag, T013) | /compact | 6m47s | 2952 | 0.1747 |
| run_26f29698 | 861530c1 (rag, T013) | TASK T013 (post-compact) | 4m13s (04:16:41→04:20:54) | 33059 | 0.1735 |
| run_216ec12e | c12da75d (rag, T011) | TASK T011 | 1h27m56s (17:36:57→19:04:53) | 38273 | — |
| run_49c6442c | c12da75d (rag, T011) | TASK T011 | 15m38s (19:07:13→19:22:11) | **55971** | — |
| cmp_010e28b1 | c12da75d (rag, T011) | /compact | 6m07s | 6103 | 4.7873 |
| run_4e446f6b | c12da75d (rag, T011) | TASK T011 (post-compact) | 6m59s (19:28:49→19:35:48) | 38324 | 0.2632 |
| run_95a213d6 | c12da75d (rag, T011) | TASK T011 | 8m48s (19:37:29→19:46:17) | 55864 | 0.4460 |
| run_47176ba2 | 01776e40 (fast, T011 "Документировать") | TASK T011 | 25m26s (01:54:36→02:20:02) | 43037 | — |
| cmp_0218a78c | 01776e40 (fast, T011) | /compact | 6m51s | 9558 | 2.5040 |
| run_e9898e6f | 01776e40 (fast, T011) | TASK T011 (post-compact) | 3m43s (02:27:44→02:31:27) | 34919 | — |
| run_781677ad | 01776e40 (fast, T011) | TASK T011 | 12m14s (02:32:04→02:44:18) | 42439 | — |
| cmp_7f3d625c | 01776e40 (fast, T011) | /compact | 9m34s | 7776 | 4.1762 |
| run_60f0cae2 | 01776e40 (fast, T011) | TASK T011 (post-compact) | 4m07s (03:38:23→03:42:30) | 30046 | — |
| run_cf91771b | 2df8134c (fast, T006) | TASK T006 | 11m23s (19:48:14→19:59:37) | 34154 | — |
| run_09cb02db | 2df8134c (fast, T006) | "stopped… Prompt is too long" | 6m25s (19:59:58→20:06:23) | — |
| run_220bbf37 | 2df8134c (fast, T006) | "stopped… Prompt is too long" retry | 1h16m12s (20:06:59→21:23:11) | 42703 | 6.9565 |
| cmp_fa43f633 | 2df8134c (fast, T006) | /compact | 6m54s | 8074 | 7.3582 |
| run_058b4c47 | 2df8134c (fast, T006) | TASK T006 (post-compact) | 6m34s (21:30:49→21:37:23) | 44213 | — |

Reading: the pre-retry compaction (6m07s–9m34s) is in fact **longer** than the
immediately following post-compact step (3m43s–6m59s), matching the CHANGELOG
figure "4.6–7.1 min, longer than the retry itself" (`CHANGELOG.md:100-104`).
The overflow-retry run `run_220bbf37` (76 min, cost 6.96) is the long
"resume" leg of session `2df8134c`; the T013 RESUME leg `run_2fc1217c` is
61 min. Neither has `api_ms` stored (null) — the CHANGELOG's "19.6 min / $4.07
/ 90 min API time over three attempts" (`CHANGELOG.md:92-96`) cannot be
reproduced from these events.

### 4.4 Prompt sizes

Per-run `context_tokens` (run record, at finish) for the runs above:
30046–60866 before compaction, 2952–16626 after compaction. The pre-compact
prompt of `cmp_734625e9` was 61766 tokens (event seq 4); the post-compact
first step `run_26f29698` ended at 33059. Step-level prompt sizes
(`input_tokens` per `step` event) were not read for every run here; the
run-level `context_tokens` is the measured prompt size at finish.

### 4.5 Figures the T002 mission text does not contain — other sources

These values appear in section 4.5 only as context; they are **not** part of
the T002 mission text (see 4.7 for every figure that *is* in it). Each is
labeled with its real source:

- **Prefill/generation tok/s of the Sep 28 measurement** (~330–400 tok/s
  prefill, ~25–30 tok/s generation; prefill 1405 tok in 6 s; summary 8192 tok
  in 350 s; checkpoint 47916→6373): source is the `ahawr-fast-compaction`
  mission text (`data-tables/missions/mission_ahawr-fast-compaction.csv`
  line 4), which measured them from llama-cpp-server logs. **No
  llama-cpp-server logs exist in this repo** (`data/` holds only
  `n8nEventLog*.log` and `crash.journal`), and `GET /v1/models` was not
  probed (requests to `http://127.0.0.1:8033` are disallowed) — not verified
  from runner events here.
- **Prompt sizes of 47158 tok / restored checkpoint 45753 / first post-compact
  step 15502 tok (4263 cached)**: same source as above (fast-compaction
  mission text, llama-cpp-server logs of 2026-09-28); not verified from
  runner events here.
- **`duration_api_ms` = 0** in every compact run's `result` event (e.g.
  `cmp_734625e9` seq 7): the gateway (LiteLLM in front of llama.cpp) does not
  report per-request API time, so per-request timings are not in the runner
  events either. Measured, source: `GET /v1/runs/cmp_734625e9520849b7b7fd2d0d4a0af5ba/events`.
- **Auto-compaction frequency** (~44K every 10–20 steps): no `trigger=auto`
  compact events exist in the 500-run window; all 17 stored compactions are
  `manual`. **Not verified from events.**
- **RAG mission usage figures** (45 requests, 19 Worker / 21 Reviewer runs,
  0.7–1.4 s latency, precision/recall 63/50 and 82/50; GDN: 34/32 runs,
  46/25 and 25/35; 1146 tool calls, 0 RAG): source is the
  `ahawr-rag-effectiveness` mission text
  (`data-tables/missions/mission_ahawr-rag-effectiveness.csv` lines 4 and 6)
  and `CHANGELOG.md:64-81`; not reproducible from runner events alone (events
  carry no retrieval-log join).

### 4.6 Mission-text figures vs events

The T002 mission text (`data-tables/missions/mission_ahawr-retry-and-microcompact.csv`,
lines 4 and 6) states the figures below. Each row gives the measured value
retrieved from runner events (`GET /v1/runs?limit=500`,
`GET /v1/runs/{id}/events`) or marks it "mission text, not verified".

| # | Figure in mission text | Measured value (source) | Verdict |
|---|---|---|---|
| 1 | 219 min of auto-compaction out of ~567 min Worker work (48 compactions) | No `trigger=auto` compact events in the 500-run store; all 17 stored compactions are `trigger=manual` (e.g. `cmp_734625e9` seq 4, `trigger=manual`) | **Mission text, not verified** — no auto-compact events exist in the stored window |
| 2 | Runner pre-retry compactions: 32 min total, 5–8 min each | Attributed to `ahawr-fast-compaction`: `cmp_9c9e19e8` 8m31s (510117 ms, `runs.py` result `duration_ms`; run record started 17:06:07→finished 17:14:38), `cmp_fa43f633` 6m54s (413127 ms), plus fast-compaction-era T011 session `01776e40`: `cmp_0218a78c` 6m51s (409577 ms), `cmp_7f3d625c` 9m34s (572898 ms). Total 15m25s (two main sessions) or 31m50s (four compactions) | **Reproduced within a few seconds of 32 min** by the four fast-compaction compactions (31m50s); individual durations 6m51s–9m34s are slightly above the stated 5–8 min range |
| 3 | 13 retries, 1 of 12 first-attempt passes | Retry/resume legs observed in the stored window (e.g. `run_2fc1217c` 1h01m03s "RESUME TASK T013", `run_220bbf37` 1h16m12s overflow retry, T011/T006 chains in 4.3) | **Mission text, not verified** — per-task pass/attempt counts are n8n workflow state, not in runner events |
| 4 | F: Worker generation 23–32 tok/s (MTP acceptance 64–87%), prefill 194–276 tok/s | No llama-cpp-server logs in the repo; runner events carry no tok/s | **Mission text, not verified** — source is llama-cpp-server logs (out of scope: `http://127.0.0.1:8033` requests disallowed) |
| 5 | Retries: T013 — 4 attempts, T014 — 2, T015 — 3 | Session `861530c1` (T013) shows 4 runs in the window: `run_2fc1217c` (RESUME), `run_355b6192`, `run_b52ee2f7`, `run_26f29698` | T013 consistent with 4 legs (events only show run-level legs, not attempt numbering); T014/T015 runs are **not in the stored 500-run window** — not verified |
| 6 | `cmp_734625e9` took 6.8 min (prefill 76K in 4.8 min + 2.2K summary) | Measured: `cmp_734625e9` result `duration_ms=405717` = **6m47s** (events seq 7); pre-compact prompt **61766** tokens (event seq 4, `pre_tokens`), post **2952** tokens (`post_tokens`) | **6.8 min vs 6m47s — consistent** (rounded). **76K vs measured 61766** — the mission text overstates the pre-compact prompt by ~14K tokens (76K ≈ 98304-window probe minus reserve, i.e. the context the summary request carried, not `pre_tokens`); the 2.2K summary ≈ measured `post_tokens` 2952 |
| 7 | Retry after it: 4.2 min | `run_26f29698` (post-compact, session `861530c1`): 04:16:41→04:20:54 = **4m13s**, `context_tokens` 33059, cost 0.1735 | **4.2 min vs 4m13s — consistent** (rounded) |

### 4.7 Log check (read-only)

- `data/n8nEventLog*.log`: no compaction or run-timing lines (`grep` for
  compact/duration found nothing).
- No llama-cpp-server logs in the repo (see 4.5).

## Verification

Every function and key named above was confirmed with `grep -n` in `claude-runner/src/claude_runner`:
- `resume_compact_threshold`, `probe_context`, `window_env`, `autocompact_trigger`, `compact_pct`, `max_trigger` → `context_probe.py`
- `session_digest`, `TEXT_CHARS`, `TOOL_INPUT_CHARS`, `TOOL_RESULT_CHARS`, `RESULT_CHARS`, `PROMPT_CHARS`, `max_chars` → `handoff.py` / `api.py`
- `compact_mode`, `compact_min_tokens`, `compact_instructions`, `local_compact_instructions`, `compact_timeout_seconds`, `COMPACT_MIN_PCT`, `COMPACT_MIN_TOKENS`, `COMPACT_PCT`, `CONTEXT_PROBE_URL`, `CONTEXT_PROBE_KEY`, `AUTO_COMPACT_WINDOW`, `AUTO_COMPACT_TOOL_RESERVE`, `DEFAULT_MAX_OUTPUT_TOKENS`, `PROVIDER_RUNNER_KEYS`, `_providers`, `_autocompact_threshold` → `config.py`
- `start`, `_bind`, `_launch`, `_execute`, `compact`, `_finish`, `build_command`, `_compact_settings_path` → `runs.py` / `claude_cli.py`

The `CLAUDE_RUNNER_RETRY_FRESH_SESSION` / `retry_fresh_session` names are **proposed** (not yet in the code); everything else is verified present.

### 4.8 Binary findings: microcompact mode and `USE_API_CONTEXT_MANAGEMENT`

Verified against the installed Claude Code binary (`/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`, ~241 MB; no `cli.js`). Byte offsets are positions in the binary.

**1. Microcompact mode resolver `fr()`** — offset `200278090`:

```js
function fr(){let{value:e,source:n}=Ss(Dl,"off");if(n==="fallback")return"unknown";return e==="on"||e==="shadow"?e:"off"}
```

- `Dl` = `"tengu_zany_pike"` — offset `200271979` (feature key for the tengu/GrowthBook flag controlling microcompact behavior).
- `Ss(e, n)` = `Ke().getFeatureValueWithSource(e, n)` — wrapper at offset `199555148`. The `getFeatureValueWithSource` method itself is at offset `199216635`. Returns `{value, source}`.
- Default mode is `"off"`. Per the quoted code, `"disabled"` source (GrowthBook disabled) gives `fr()` = `"off"`. `"fallback"` source (remote values loaded but the key `tengu_zany_pike` is missing) gives `fr()` = `"unknown"`. Otherwise `"on"` or `"shadow"` pass through verbatim; any other value falls back to `"off"`.

Mode semantics (offset `200278266`, planner `ur()`):
- `"shadow"` → never clears tool uses.
- `"on"` + no replies → clears.
- otherwise → server-cleared check.

`bNo()` (offset `200272569`) plans a `clear_tool_uses_20250919` edit only for `"on"` (or `"unknown"` with server evidence). The planned edit is returned as `{returnAfterIdle, edit}` and sent to the API. `Et()` (offset `200278322`) checks whether the server already applied a `clear_tool_uses_20250919` edit in the response (`context_management.applied_edits`).

**2. `USE_API_CONTEXT_MANAGEMENT` env var** — appears 3× in the binary:

- String table entry — offset `101055820`.
- Env feature map entry `USE_API_CONTEXT_MANAGEMENT:()=>IC` — offset `196663089`.
- `IC = H.bool()` — offset `196667063` (boolean feature; **not** a triBool/enum).
- Env feature object built by `var a = a7e(YU, T)` — offset `196697004`, where `YU` is the big `CLAUDE_CODE_*` env map (built by `var l={};Qr(l,{...})` near offset `196654000`). `a7e` creates per-var getters reading `process.env[E]` parsed by schema.
- **Only code usage** — offset `199443338`, in the context-management beta header gate:

```js
_be = C("context_management","context-management-2025-06-27")   // offset ~198336869
when: (e) => {
  let n = a.USE_API_CONTEXT_MANAGEMENT && !1,   // forced to false
      r = rN(e.model);
  return e.firstPartyCapabilityBetas && (n || r);
}
```

The `&& !1` neutralizes the env var: even if `USE_API_CONTEXT_MANAGEMENT=1` is set, `n` is always `false`. The beta header is only added via the model-capability fallback `rN(e.model)` when `e.firstPartyCapabilityBetas` is truthy.

**3. `rN(e.model)` — model-capability check** — offset `199440286`:

```js
function rN(e){
  let n=Ue(e), r=Fh(n,"context_management",e);
  if(r===!1) return !1;
  let s=Fl(e);
  if(s==="foundry") return !0;
  if(zN(s)) return !n.includes("claude-3-");
  return r || n==="claude-mythos-5";
}
```

- `zN(e)` — offset `197607665`: `function zN(e=Pe()){return e==="firstParty"||fH(e)||e==="foundry"||e==="mantle"}`. True for first-party, Anthropic AWS, Anthropic Google Cloud, Foundry, and Mantle providers.
- `fH(e)` — offset `197607356`: `return e==="anthropicAws"||e==="anthropicGoogleCloud"`.
- `Fl(e)` — offset `197607356`: returns the resolved provider for the model.
- `Fh(n, "context_management", e)` — offset `197602424`: `return ZXe(t,e)??dWt(e,t,r)` — checks the `CLAUDE_CODE_MODEL_CAPABILITIES` env var for `"context_management"` in the model's capability list, falling back to served capability lookup.
- `Ue(e)` — offset `196360231`: model ID normalization (identity/NaN-safe equality).

So `rN` returns true when: the model's capabilities include `"context_management"` (via `CLAUDE_CODE_MODEL_CAPABILITIES` or served lookup), OR the provider is Foundry, OR the provider is first-party/AWS/GCP/Foundry/Mantle and the model is not a `claude-3-*` model, OR the model is `claude-mythos-5`.

**4. Request body: `context_management` field** — offset `206189172`:

```js
...Mk && jm && lr.includes(_be) && {context_management: Mk}
```

- `Mk` is the planned edit from `bNo()` (the `clear_tool_uses_20250919` edit object).
- `lr` is the list of active betas; `_be` is the `context-management-2025-06-27` beta.
- The edit is included in the API request **only when** the beta is active. The server applies it and returns `applied_edits` in the response (see `SNo()` at offset `200275357`, which reads `contextManagement.applied_edits` from the response).

**5. Provider detection for custom `ANTHROPIC_BASE_URL`** — offset `197608118`:

```js
function Uh(e){
  try{ let t=new URL(e).host;
    return ["api.anthropic.com"].includes(t);
  }catch{return !1}
}
```

- `Uh` returns `true` only when the base URL host is exactly `api.anthropic.com`. The host check is at offset `197608159`: `return["api.anthropic.com"].includes(t)`.
- `Ng()` — offset `197607873`: `function Ng(){let e=process.env.ANTHROPIC_BASE_URL;if(!e)return!0;return Uh(e)}` — if no `ANTHROPIC_BASE_URL` is set, returns `true` (default first-party). If set to a non-Anthropic host (e.g. LiteLLM at `127.0.0.1:8033`), returns `false`.
- `Us()` — offset `197607792`: `function Us(){if(a._CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL)return!0;return Ng()}` — the `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL` env var can force first-party regardless of the base URL. The env-var check is at offset `197607811`.
- `xa()` — offset `197607745`: `function xa(){return Pe()==="firstParty"&&Us()}` — true only when the provider is first-party AND the base URL is Anthropic (or assume-first-party is set).

For a custom `ANTHROPIC_BASE_URL` (LiteLLM): `Us()` returns `false`, so `xa()` is false, and `firstPartyCapabilityBetas` is false (it requires `xa()` or equivalent first-party detection). Therefore the beta-header gate `e.firstPartyCapabilityBetas && (n || r)` is `false` — the `context-management-2025-06-27` beta is **not** added to the request.

**6. GrowthBook fallback and mode** — offset `199555148` (`getFeatureValueWithSource`):

```js
getFeatureValueWithSource(e,n){
  let r=this.getEnvironmentOverrides();
  if(r&&e in r) return {value:r[e],source:"override"};
  let s=this.readConfigOverrides();
  if(s&&e in s) return {value:s[e],source:"override"};
  if(!this.deps.isEnabled()&&!this.deps.isDiskCacheReadableWhileDisabled())
    return {value:n,source:"disabled"};
  ...
  if(g!==void 0) return {value:Ou(g,n),source:"payload"};
  if(this.remoteEvalFeatureValues.size>0) return {value:n,source:"fallback"};
  ...
}
```

Per the quoted code: if GrowthBook is **unreachable or disabled**, `source` is `"disabled"` and `fr()` returns `"off"`. `"fallback"` occurs only when remote values are loaded but the key `tengu_zany_pike` is missing, giving `fr()` = `"unknown"`. **Hypothesis:** for the local runner (custom `ANTHROPIC_BASE_URL`), GrowthBook remote eval is unavailable, so the most likely source is `"disabled"` and `fr()` = `"off"`, not `"unknown"`. Either way, the **not applicable** verdict below rests on the `firstPartyCapabilityBetas` / beta gate, not on the mode.

**7. Runner env passthrough** — `claude-runner/src/claude_runner/claude_cli.py:186-203` (`child_env`):
- Strips `CLAUDE_RUNNER_*` vars.
- Overlays the provider `env` dict.
- Defaults `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1`.

So a provider env block **can** carry `USE_API_CONTEXT_MANAGEMENT`, but the installed CLI's beta-header gate forces it to `false` (`&& !1`), so it currently **cannot enable** the `context-management-2025-06-27` header as-is.

**8. Repo check** — no occurrences of `USE_API_CONTEXT_MANAGEMENT` or `API_CONTEXT` in any repo `.py`, `.md`, `.json`, or `.yml`; no "microcompact" in `claude-runner/src/claude_runner/*.py` or `README.md`.

**Commands used** (all produce the cited offsets when re-run):

```bash
# fr() mode resolver
grep -boa 'function fr(){let{value:e,source:n}=Ss(Dl,"off")' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 200278090:function fr(){let{value:e,source:n}=Ss(Dl,"off")

# Dl feature key
grep -boa 'Dl="tengu_zany_pike"' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 200271979:Dl="tengu_zany_pike"

# clear_tool_uses_20250919 (all occurrences)
grep -boa 'clear_tool_uses_20250919' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 100532932:clear_tool_uses_20250919
# 200203317:clear_tool_uses_20250919
# 200272599:clear_tool_uses_20250919
# 200275539:clear_tool_uses_20250919
# 200278322:clear_tool_uses_20250919

# USE_API_CONTEXT_MANAGEMENT (all occurrences)
grep -boa 'USE_API_CONTEXT_MANAGEMENT' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 101055820:USE_API_CONTEXT_MANAGEMENT
# 196663089:USE_API_CONTEXT_MANAGEMENT
# 199443338:USE_API_CONTEXT_MANAGEMENT

# USE_API_CONTEXT_MANAGEMENT&&!1 gate
grep -boa 'USE_API_CONTEXT_MANAGEMENT&&!1' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 199443338:USE_API_CONTEXT_MANAGEMENT&&!1

# firstPartyCapabilityBetas (all occurrences)
grep -boa 'firstPartyCapabilityBetas' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 101013456:firstPartyCapabilityBetas
# 199443392:firstPartyCapabilityBetas
# 199443488:firstPartyCapabilityBetas
# 199444065:firstPartyCapabilityBetas

# rN (all occurrences)
grep -boa 'function rN(' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 199440286:function rN(
# 200768553:function rN(

# zN provider check
grep -boa 'function zN(' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 197607665:function zN(
# 201338527:function zN(
# 203378555:function zN(
# 205003233:function zN(

# xa first-party AND base-URL check
grep -boa 'function xa(' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 197215266:function xa(
# 197607745:function xa(
# 201147608:function xa(
# 202211648:function xa(
# 208975156:function xa(
# 212991280:function xa(
# 226604486:function xa(
# 230765359:function xa(
# 232350262:function xa(

# Us assume-first-party env var
grep -boa 'function Us(' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 196790834:function Us(
# 197305131:function Us(
# 197607792:function Us(
# 200230788:function Us(
# 208842892:function Us(
# 210916239:function Us(
# 211172053:function Us(
# 212251483:function Us(
# 212958032:function Us(
# 214127847:function Us(
# 219520642:function Us(
# 221004807:function Us(
# 228927149:function Us(
# 229346301:function Us(
# 232897535:function Us(
# 234264101:function Us(

# Ng ANTHROPIC_BASE_URL reader
grep -boa 'function Ng(' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 197607873:function Ng(
# 218382021:function Ng(
# 221046777:function Ng(
# 232549080:function Ng(

# Uh api.anthropic.com host check
grep -boa 'return\["api.anthropic.com"\].includes' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 197608159:return["api.anthropic.com"].includes

# _CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL (all occurrences)
grep -boa '_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 98349960:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 100121594:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 101082580:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 196695693:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 196939716:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 196940274:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 196940775:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 197607811:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 199357573:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 199357615:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 202365314:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 202365358:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 203103281:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 206076511:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 206108723:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 206150892:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL
# 232417080:_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL

# context_management:Mk request body
grep -boa 'context_management:Mk' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 206189172:context_management:Mk

# applied_edits (all occurrences)
grep -boa 'applied_edits' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 100533148:applied_edits
# 200203162:applied_edits
# 200275508:applied_edits
# 200278286:applied_edits

# getFeatureValueWithSource method body
grep -boa 'getFeatureValueWithSource(e,n){' /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
# 199216635:getFeatureValueWithSource(e,n){

# Function body reads (dd commands; first line of output)
# Uh at 197608118
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=197608118 count=80
# function Uh(e){try{let t=new URL(e).host;return["api.anthropic.com"].includes(t)
# zN at 197607665
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=197607665 count=80
# function zN(e=Pe()){return e==="firstParty"||fH(e)||e==="foundry"||e==="mantle"}
# xa at 197607745
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=197607745 count=80
# function xa(){return Pe()==="firstParty"&&Us()}function Us(){if(a._CLAUDE_CODE_A
# Us at 197607792
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=197607792 count=80
# function Us(){if(a._CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL)return!0;return Ng()
# Ng at 197607873
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=197607873 count=80
# function Ng(){let e=process.env.ANTHROPIC_BASE_URL;if(!e)return!0;return Uh(e)}f
# getFeatureValueWithSource at 199216635
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=199216635 count=600
# getFeatureValueWithSource(e,n){let r=this.getEnvironmentOverrides();if(r&&e in r)return{value:r[e],source:"override"};let s=this.readConfigOverrides();if(s&&e in s)return{value:s[e],source:"override"};if(!this.deps.isEnabled()&&!this.deps.isDiskCacheReadableWhileDisabled())return{value:n,source:"disabled"};...
# fr at 200278090
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=200278090 count=100
# function fr(){let{value:e,source:n}=Ss(Dl,"off");if(n==="fallback")return"unknown";return e==="on"||
# rN at 199440286
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=199440286 count=200
# function rN(e){let n=Ue(e),r=Fh(n,"context_management",e);if(r===!1)return!1;let s=Fl(e);if(s==="foundry")return!0;if(zN(s))return!n.includes("claude-3-");return r||n==="claude-mythos-5"}
# USE_API_CONTEXT_MANAGEMENT&&!1 at 199443338
dd if=/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe bs=1 skip=199443338 count=60
# USE_API_CONTEXT_MANAGEMENT&&!1,r=rN(e.model);return e.firstP
```

**Verdict for provider local**

**Not applicable.** For a custom `ANTHROPIC_BASE_URL` (LiteLLM at `127.0.0.1:8033`):

1. `Uh()` (offset `197608118`) returns `false` for any host other than `api.anthropic.com`, so `Us()` returns `false` and `firstPartyCapabilityBetas` is `false`.
2. The beta-header gate `e.firstPartyCapabilityBetas && (n || r)` is therefore `false` — the `context-management-2025-06-27` beta is **not** sent in the request.
3. Without the beta, `Mk && jm && lr.includes(_be)` is `false` (offset `206189172`), so the `context_management` edit is **not** included in the API request body.
4. GrowthBook source is `"disabled"` (unreachable/disabled) or `"fallback"` (key missing among loaded remote values); either way, `bNo()` returns early (no edit planned) — the verdict rests on the `firstPartyCapabilityBetas` gate, not on the mode. **Hypothesis:** the runner's most likely case is `"disabled"` → `fr()` = `"off"`.
5. `Et()` (offset `200278322`) checks for server-applied `applied_edits`; since no beta is sent, the server never applies an edit, so this check is always false.

The only way to enable clearing for a custom provider would be to set `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL` (offset `197607811`, the check inside `Us()`) to force `Us()` to `true`, AND have the model's capabilities include `"context_management"` (via `CLAUDE_CODE_MODEL_CAPABILITIES` or served lookup). Neither is set in the runner's current config. **Hypothesis:** the runner could set `CLAUDE_CODE_MODEL_CAPABILITIES` in the provider env block to include `"context_management"` for its local model, combined with `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL=1`, to enable the beta header — but the LiteLLM/llama.cpp server would not understand the `context_management` field in the request, so the edit would be ignored server-side. This is an **unverified hypothesis** — no reproduction was performed.

## T005 — Reviewer role permissions and result placement

### Reviewer role configuration in claude-runner

The Reviewer role is defined in `claude-runner/src/claude_runner/config.py`:

- **Permission mode**: `dontAsk` (line 311). A Bash command runs only if a rule allows it; everything else is denied without a prompt.
- **Allowed tools**: `_REVIEWER_BASH_ALLOW` (lines 111–117). The allowed Bash commands are read-only inspection commands (`ahawr-search`, `grep`, `ls`, `wc`, `head`, `tail`, `cat`, `md5sum`, `sha256sum`, `stat`, `file`, `diff`, `cmp`, `sort`, `uniq`, `cut`, `pwd`, `cd`, `bash -n`) plus read-only `git` subcommands (`diff`, `status`, `log`, `show`, `rev-parse`, `ls-files`, `ls-tree`, `cat-file`, `blame`, `grep`, `describe`), each also as `git -C <dir> …` and bare forms.
- **Disallowed tools**: `_READ_ONLY_DENY` (line 55: `Edit`, `Write`, `NotebookEdit`), `_SECRET_DENY` (lines 58–69: `Read(./.env)`, `Read(./.env.local)`, etc., in `./` and `**/`), and `_REVIEWER_BASH_DENY` (line 118: `Bash(* >*)`, `Bash(git * --output*)`, `Bash(*.env*)`).
- **Working directory / added directories**: The Reviewer's working directory is the mission's `working_directory` (set by the workflow node **Build Reviewer Run Input**, `AHAWR_v13_ClaudeCode.json:3978`). Extra folders are passed via `CLAUDE_RUNNER_REVIEWER_ADD_DIRS` (comma-separated), parsed at `config.py:420` and emitted as `--add-dir` flags in `claude_cli.py:128-129`. In `dontAsk` mode a role can read only its working directory and the `add_dirs` folders.
- **Shell access**: Yes, but restricted to the allowed read-only commands above. Writes, builds, redirects to files, and git state changes are denied.

### What agent_prompts tells Architect and Worker about note placement

In `agent_prompts.example.csv`:

- **Architect prompt** (lines 2–84) instructs the Architect to decompose the mission into Worker tasks. Each task must include `id`, `title`, `objective`, `acceptance_criteria`, `verification`, `scope`, `dependencies`. The prompt does **not** prescribe a specific path for Worker notes; it says the Mission-report task's objective is to "read the results of the previous tasks (the files and notes named in their scopes and criteria)". So the Architect decides per-task where notes live by naming paths in the task's `scope` or `acceptance_criteria`.
- **Worker prompt** (lines 85–133) tells the Worker to "Work ONLY on the current task" and to put evidence in the final report as `file:line` references or short command-output quotes. It does not specify a fixed note path like `/tmp/ahawr-theme/T001-notes.md`; the task's `scope` and `acceptance_criteria` determine where results must be placed.
- **Reviewer prompt** (lines 134–171) tells the Reviewer to "Check precisely: look for specific lines with Grep, open files with Read offset/limit" and "Do not ask the Worker to paste long raw outputs verbatim: ask for the specific file:line and the few lines (at most ~30) that prove the point. Check the rest yourself with Grep/Read."

### Two options and the chosen one

**Option A — Give the Reviewer read access to the Worker's working folder and /tmp.**
- Mechanism: set `CLAUDE_RUNNER_REVIEWER_ADD_DIRS=/tmp/<worker-scratch>,<worker-working-dir>` (or a subset).
- Security rationale: The Reviewer stays read-only because `dontAsk` mode plus the deny list (`Edit`, `Write`, `NotebookEdit`, `Bash(* >*)`, `Bash(*.env*)`) blocks any write, regardless of which directories are added. `add_dirs` only widens the *read* surface; it does not grant write access. However, it exposes the Worker's scratch files (which may contain intermediate results, debug notes, or sensitive data from the mission) to the Reviewer. If the Worker's scratch contains secrets or personal data, this is a leak vector. Also, it depends on the Worker actually placing its results in a predictable, documented location; if the Worker writes to an undocumented path, the Reviewer still cannot find it.

**Option B — Prompt rule: results the Reviewer must check go into the report or the mission folder.**
- Mechanism: add a rule to the Architect and Worker prompts: "The Worker must place any file the Reviewer needs to verify (notes, artifacts, logs) in the mission's working directory (or a subfolder of it) and cite its path in the report. The Reviewer then checks that path with Grep/Read."
- Security rationale: The Reviewer needs no extra directory access. Its working directory is already the mission folder, and it can read anything the Worker places there. This keeps the Reviewer's read surface minimal (mission folder only) and avoids exposing the Worker's private scratch space. It also makes the contract explicit: if the Worker's acceptance criteria say "notes in `/tmp/...`", the Architect must either change the criteria or ensure the Worker copies the relevant file into the mission folder. The Reviewer's read-only posture is preserved without widening permissions.

**Chosen option: Option B.**

Justification:
1. **Security**: Option B keeps the Reviewer's read surface to the mission working directory, which is the minimum necessary to verify the Worker's work. Option A widens the read surface to `/tmp` and the Worker's scratch, increasing the chance the Reviewer sees data it should not (secrets, unrelated mission data, or personal files).
2. **Explicitness**: Option B makes the placement contract part of the task plan. The Architect names the path in the task's `scope`/`acceptance_criteria`; the Worker follows it; the Reviewer checks the named path. This is deterministic and auditable.
3. **Reviewer stays read-only**: Both options keep the Reviewer read-only, but Option B does so without adding any new permissions. The Reviewer's tool set and permission mode are unchanged (`config.py:311-314`).

**Name of the switch (proposed; implemented as opt-in prompt text, off by default)**

The proposed switch was a **Constants field `result_placement_rule`**. It does not exist in the workflow: `grep -n "result_placement_rule" /d/n8n/AHAWR_v13_ClaudeCode.json` returns no matches, and the **Constants** node (`AHAWR_v13_ClaudeCode.json:540`) returns only fields such as `runner_url`, `state_namespace`, and the planner/worker/reviewer model/provider settings; it has no `result_placement_rule`.

**T011 shipped the rule as opt-in prompt text, not in the shipped defaults.**
The `architect` and `worker` rows of `agent_prompts.example.csv` do **not**
carry the rule; they are the shipped defaults. To enable the rule, an operator
appends the exact paragraph (given in `docs/ahawr-cheaper-retries.md` §5.3)
to the `system_prompt` column of the `architect` and `worker` rows in the
mission's `agent_prompts` data table. With the rule absent, behaviour is
exactly as before and the Reviewer still reads only the mission working
directory. No workflow node, per-run flag, or restart is involved; the rule
lives entirely in the prompt text.

- **No Constants field, no Build-Run-Input edit**: the rule is a prompt
  paragraph, so neither **Build Worker Run Input** (l3964) nor
  **Build Architect Run Input** (l3941/3950) changes.

Comparison against the existing `CLAUDE_RUNNER_REVIEWER_ADD_DIRS` (`.env.example:114`, `claude-runner/README.md:83`): that switch is static per runner, takes a comma-separated list of folders, and only takes effect after a runner restart; it widens the Reviewer's read access to `/tmp` and other folders. The prompt rule instead stays inside the prompt contract, changes only what the Architect and Worker are told to do, and adds no directory access. The Reviewer stays read-only in both cases (`config.py:311-314`).

### Verification: config locations can be found with grep

- `grep -n "reviewer" /d/n8n/claude-runner/src/claude_runner/config.py` → line 311 (`"reviewer": RoleProfile("dontAsk", ...)`)
- `grep -n "REVIEWER_BASH_ALLOW" /d/n8n/claude-runner/src/claude_runner/config.py` → lines 111–117
- `grep -n "ADD_DIRS" /d/n8n/claude-runner/src/claude_runner/config.py` → line 420
- `grep -n "add_dirs" /d/n8n/claude-runner/src/claude_runner/claude_cli.py` → lines 128–129
- `grep -n "REVIEWER_ADD_DIRS" /d/n8n/.env.example` → line 114
- `grep -n "REVIEWER_ADD_DIRS" /d/n8n/claude-runner/README.md` → line 83
