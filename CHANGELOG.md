# Changelog

Notable changes to the AHAWR stack: n8n workflows, `claude-runner`, `ahawr-retrieval`,
LiteLLM, the dashboard and the missions. Newest first. Commit ids are in brackets.

Version: **0.4.0** (claude-runner `0.3.0` → `0.4.0`, 2026-10-14; `0.2.0` → `0.3.0`, 2026-10-12; `0.1.0` → `0.2.0` on 2026-10-06); the stack's single version number is the runner's, see the README section "Versioning".

## 2026-10-15

- Loop guard for the local Worker thinking: the same tool call (name and arguments) 4 times in a row turns thinking on and holds it for 12 more model calls (`CLAUDE_RUNNER_THINK_LOOP_REPEATS`, `CLAUDE_RUNNER_THINK_LOOP_HOLD_STEPS`; 0 = off). Seen: 170 identical `grep -c` calls in 11 minutes.

- Worker memory safety. A Worker script that read the 9 GB model file into RAM (anon-rss 7-9 GB, six times in a night) made the Docker VM kernel kill processes and restart the VM. Now: `claude-runner` has `mem_limit: 6g`, `ahawr-retrieval` 4g; the claude process and everything a Worker starts run under `prlimit --data` (`CLAUDE_RUNNER_PROC_DATA_LIMIT_MB`, default 4096), so a script that allocates too much gets a `MemoryError` with a traceback and can fix itself, while a memory-mapped big file still works; the Worker's system prompt carries a MEMORY RULE (stream big files, use `gguf`); `mem_guard.py` is the backstop (kills the biggest process when one holds more than `CLAUDE_RUNNER_MEM_GUARD_PROC_MB`, default 5000, or the container passes `CLAUDE_RUNNER_MEM_GUARD_CGROUP_PERCENT`, default 85, of its limit) and writes a `mem_guard` event to the run log.

## 2026-10-14 (0.4.0)

- Dashboard: clickable filter values and full-text search. Clicking a status/source/role/model/provider value in the list (or cwd/session in the run header) toggles it in
  the active filter; fields combine with AND and values of one field with OR. A tag chip row with a "clear all" button replaces the old f-source/f-role/f-status
  selects and session chip. The filter is persisted in the URL hash (`#field=value&…`) and parsed back on load. A new run is auto-selected only when it matches
  the filter, and never when the filter already holds a session. `GET /api/runs` takes repeated params (model, provider, cwd, role, status), `session_id`, `source`
  and `limit` (after the filter, capped at 500); a non-matching provider/cwd filter hides the Hermes runs (provider `hermes`, cwd `""`). `GET /api/search` and
  `GET /v1/search` do case-insensitive full-text search over the per-run event logs with the same filters; limits are module constants (≥2-char query, 5 matches/run,
  default 50, max 200 results, 2000 runs scanned, 3 s budget, 160-char snippets) and a search stopped early sets the `incomplete` flag. Search covers Claude Code
  runs only — Hermes text lives in session messages, so that source returns no runs and notes it.
- Telegram control bot works (it never did: it read no messages). `tg_relay.py` long-polls Telegram with the token from `.env` (`TELEGRAM_BOT_TOKEN`) and posts each update to
  the bot workflow's webhook (`AHAWR_TG_WEBHOOK_PATH`); the workflow no longer polls. Fixed in `AHAWR_Status_Bot.json`: placeholder chat id, the update
  shape passed to Parse Command, data-loading nodes that had no incoming connection (status reads the active mission from the task state table), Switch nodes in a
  shape n8n ignores (every command went to the view handler), replies that referenced a node absent on most paths (HTML mode, buttons only when there are any).
  The bot menu ("/" list with descriptions) is set with `setMyCommands`; `/help` and `/commands` list the commands with descriptions (`tg_commands.py`).
  Commands that change the running mission still need the control-flag patch of the main workflow (not wired yet).
- Retrieval service uses PostgreSQL (`RETRIEVAL_DATABASE_URL`, database `retrieval` in `ahawr-postgres`, `sqlite_to_postgres.py` copied the embeddings) and runs with a
  1.5 GB memory limit; agents use it again (`hermes_config.retrieval_enabled`).
- Slot cache: `slot_keeper.py` runs in the `llama-cpp-dashboard` container (restores the slot when llama is healthy and again after a restart, runs the save loop,
  samples the GPU to a CSV); the watchdog and the apply scripts restore the slot after a llama restart; `slot_cache.py` reads the key from `.env` when the environment has none.
- The Reviewer may read the llama.cpp tree (`CLAUDE_RUNNER_REVIEWER_ADD_DIRS`), so missions that check files there are not judged from the text alone.
- Profile of cold prefill on the RTX 3060: `docs/prefill-profile-2026-10-14.md` (110 W against 170 W, Nsight kernel table); mission report of `llamacpp-prefill-cost`:
  `docs/mission-report-prefill-cost-ru.md`.

- Thinking policy of the local Worker as JSON (`hermes_config.worker_think_policy`): variants with weights, each a timeline of `["think"|"off", calls]` segments for first attempts and
  retries (default: 8 calls with thinking, 25 without, a 10-call burst, then without; a control variant without thinking), validated by `set_think_policy.py`; the older `pulse:O:T:P[:fN][:rN]` strings
  still work (`think_policy.py`, `think_switch.py`, `docs/thinking-control.md` section 2c). `data-tables/ops/think_report.py` gives the statistics by mode, attempt, duration, era and variant.

### Other changes of 2026-10-14

- The cost shown for a local run is what it would have cost on a cloud model: the steps made without thinking are priced as Haiku, the steps made with
  thinking as Sonnet (`claude_runner/local_pricing.py`, settings `AHAWR_LOCAL_PRICE_*`). The runner writes the thinking mode the run starts in
  (`thinking_state`) to its event log, the switches are already there (`thinking_switch`); the figure is computed from the steps' token counts when the
  run ends, with the Sonnet and Haiku totals of the whole run kept in `details.local_cost.by_profile`. Rebuild the runner image to apply.
- `slot_cache.py`: works with the router mode of llama-server (model in every request, slot report from `GET /slots?model=`, the model is loaded before a restore)
  and accepts Git Bash paths (`/d/llama-slots`).

## 2026-10-13

- Thinking of the local Worker can be limited by steps as well as by time: `hermes_config.worker_think_off_after_steps` (default `AHAWR_THINK_OFF_AFTER_STEPS`, 0 = none); time limit of a retry lowered to 30 minutes in `hermes_config`.
- Reviewer prompt: hints for the next attempt are concrete (no placeholders), complete in one pass and tracked point by point on a retry.

## 2026-10-12 (0.3.0)

- n8n database moved from SQLite to Postgres 16 (`ahawr-postgres` service): no more `SQLITE_BUSY` execution crashes.
- Thinking switch for local models without a llama restart (`litellm/runtime/local_thinking`, read by the LiteLLM hook per request).
- Task-started Telegram notice is split into several messages of up to 4096 characters.
- Dashboard: live generation text auto-scrolls to the end.
- Worker scratch folder `/tmp/dcfr-work` is a host bind mount and survives container recreation.
- Reviewer can search any folder under `/d` with `ahawr-search --root`.
- Thinking is switched off for a local task that has run longer than an hour (`AHAWR_THINK_OFF_AFTER_SECONDS`, default 3600;
  `claude_runner/think_switch.py`): the runner sums the runs of the Worker session, writes `off` to the LiteLLM flag and
  restores `on` when the task ends; a manual `off` is never touched.
- `CLAUDE_RUNNER_MAX_RUN_SECONDS` for a single run raised from 7200 to 10800 on this installation.
- Chat template `llama/templates/qwen38-same-prefix.jinja` (the effort paragraph is always emitted) keeps the prompt prefix equal with
  thinking on and off, so switching it no longer makes llama.cpp evaluate the whole context again (215 s for 52k tokens measured before).
  Mount `llama/templates` into the llama container and set `chat-template-file` in the preset; see `docs/thinking-control.md`.
- README: Data Tables are described for Postgres, not SQLite; new `docs/thinking-control.md`.
- Thinking policy `off_first` for the local Worker (first attempt at a task without thinking, a retry after a review with it, the hour limit
  counts the retries only), configurable in `hermes_config` (`worker_think_policy`, `worker_think_policy_from_task`,
  `worker_think_off_after_seconds`; example CSV updated) with the `.env` values as defaults. Workflows: `Constants`, `Build Worker Run Input`,
  Run Manager `Normalize Request` and the three run request nodes (patch `data-tables/ops/think_config_patch.py`); the runner stores the settings
  with each run (`runs.think_policy`, `think_from`, `think_after`). The repo copy of the Run Manager was re-synced with the live one.
- Prompt-cache diagnostics (`litellm/cache_trace.py`, `AHAWR_CACHE_TRACE`, output `litellm/trace/cache_trace.jsonl`): per request, the first
  message that differs from the previous request of the same conversation. Measured before: 96% of prompt tokens come from the cache;
  the misses are mostly the first step of a run (21%), mid-run partial misses and thinking switches.


## 2026-10-11

### Telegram control bot
- `AHAWR_Status_Bot.json` (generated by `scripts/patch_status_bot_control.py`, 39 nodes): long-polls
  Telegram every 60 s (getUpdates timeout 25 s as a request parameter, HTTP client timeout 30 s, ~1440
  executions/day) and answers only the chats named in `hermes_config.telegram_chat_id`; other chats are
  counted silently. Runs cannot overlap: a static-data `poll_in_progress` flag is set before each
  getUpdates and cleared after it, so a trigger that finds the flag set skips the run — the polling
  fix. Every mutating command is confirmed (inline Approve/Deny buttons or a one-time code, 2-minute
  TTL) before anything is applied.
- Confirmation codes: per-chat pending (a callback or `/approve` from another chat is rejected),
  2-minute TTL, one-time use (reuse leaves the value unchanged), expired codes are rejected with
  "code expired".
- Insert-based audit: every confirmed mutation is written to the `AHAWR Bot Audit Log` table (7 columns,
  `id` auto-increment) by a dedicated insert node; write verification re-reads the written row and the
  reply is only "было -> стало" if the re-read row carries the written value — a write failure audits
  `failed`. Queued commands (`/start`, `/resume`, `/restart`) are checked before re-read: the queue row
  has no key/value pair, so the reply is the operator launch line and the audit row says `approved`
  with `after = queued: <action>`.
- Honest queue replies: the bot cannot launch runs itself — confirmed `/start`, `/resume` and `/restart`
  insert a row into the `AHAWR Command Queue` table and the reply states the operator launch line
  (the operator executes it); confirmed `/retry` and `/accept` set the Live State flags the main
  workflow consumes during a running mission.
- Main-workflow control flags (both v13 workflows, patched by `scripts/patch_main_control_flags.py`):
  a pause gate that reads Live State before each new task starts (never mid-attempt); a stop check
  after the current step that sets `workflow_status = stopped` and clears the owner; `steer_note`
  appended to the review feedback of the next attempt only, then cleared; `accept_requested` OR'd into
  `Review Passed?` (reason recorded in `last_control`); `retry_requested` allowing one attempt beyond
  `max_attempts_per_task`; a `Clear Consumed Flags` node that clears `steer_note`, `accept_requested`
  and `retry_requested` before the next task starts.
- `tests/n8n-code/run.js` — 243 passed, 0 failed (n8n stub harness; the 101-assertion suite of
  `patch_status_bot_control.py` is a subset of this).
- The README gained the "The Telegram control bot" section (long-polling, chat allow-list, admin/viewer
  access levels, confirmation of mutating commands, audit log, the command list by category, and the
  two-bot/two-token constraint); the full command registry stays in `docs/telegram-bot.md`.
- `.env.example` documents four optional `TELEGRAM_STATUS_BOT_*` settings (descriptions only, no values):
  `TELEGRAM_STATUS_BOT_POLL_SECONDS` (default 30), `TELEGRAM_STATUS_BOT_TIMEOUT_SECONDS` (default 30,
  keep 25..30), `TELEGRAM_STATUS_BOT_CONFIRM_TTL_SECONDS` (default 120) and `TELEGRAM_STATUS_BOT_AUDIT_TABLE`
  (default `AHAWR Bot Audit Log`). The bot token itself stays in the n8n `telegramApi` credential,
  never in a version-controlled file.
- `hermes_config.example.csv` gained four optional columns — `telegram_status_bot_poll_seconds`,
  `telegram_status_bot_timeout_seconds`, `telegram_status_bot_confirm_ttl_seconds`,
  `telegram_status_bot_audit_table` — left empty in every row, so they fall back to the defaults above.

## 2026-10-06

### AHAWR workflow
- Reviewer result access (item 5): the `RESULT PLACEMENT RULE` is opt-in prompt text,
  not in the shipped defaults. The `architect` and `worker` rows of
  `agent_prompts.example.csv` do not carry the rule; to enable it, an operator appends
  the exact paragraph (given in `docs/ahawr-cheaper-retries.md` §5.3) to the
  `system_prompt` column of the `architect` and `worker` rows in the mission's
  `agent_prompts` data table. Files the Reviewer must verify (notes, artifacts, logs)
  then go into the mission working directory or a subfolder of it, never /tmp, and the
  Worker cites the exact path in its report. The Reviewer stays read-only:
  `config.py:326` (`dontAsk`, allowed tools read-only Bash only, `Edit`/`Write`/
  `NotebookEdit` and `Bash(* >*)` denied), covered by
  `tests/test_config_cli.py::test_reviewer_default_profile_allows_read_only_bash_and_ahawr_search`
  and `test_result_placement_rule_in_prompts_keeps_reviewer_read_only` (asserts the rule is
  absent from the shipped default rows while the Reviewer profile stays read-only).
  No workflow node, per-run flag, or restart changes; `CLAUDE_RUNNER_REVIEWER_ADD_DIRS`
  remains the alternative (wider read access, restart). The example T001 of
  `ahawr-dashboard-theme` (notes in `/tmp/ahawr-theme/T001-notes.md`) would now pass on
  the first review with the rule enabled: notes land in the mission folder the Reviewer
  can read. Documented in `docs/ahawr-cheaper-retries.md` §5.3, including the exact opt-in
  paragraphs, the enable step, and the example task.

### AHAWR workflow
- Mission results table. `AHAWR_Mission_Results.json` (sub-workflow "AHAWR Mission Results", id `AhawrMissionRes01`)
  keeps the n8n data table `mission_results` up to date: AHAWR calls it, without waiting, after every saved stage
  (worker result, reviewer result, parsed review, workflow failure, approval) through two new nodes, `Prepare Results
  Update` and `Update Mission Results`. It reads the task history and the state, asks claude-runner
  (`POST /v1/stats/runs`) for the time, cost, speed and cache figures of the mission's runs and upserts one row per
  mission (status, tasks closed, reviewer/operator split, average score, attempts per task, Worker hours, cost
  estimate/real, tok/s, cache). Every new node continues on error, so a failure never stops a mission; if the runner
  has no such endpoint, only the progress columns are written. `verified_works` is the operator's note and is never
  written by the workflow. Import the sub-workflow first, then the patched AHAWR.

### claude-runner
- `POST /v1/stats/runs` (read-only, same bearer as the other `/v1` views): time, cost, tok/s and cache figures for a list
  of run ids and the compactions of their sessions (`mission_stats.py`, test `test_stats_for_a_set_of_runs`).
- The `compact` event now carries `duration_ms`, taken from Claude Code's `compact_metadata` (summary request included); the dashboard shows it ("context compacted (auto): 59.9K → 9.75K tokens in 2.3min") and the Markdown export too. Older Claude Code builds that do not report it leave it empty. The same number was already stored in `details.compact` of finished runs, so past compactions can be read from there.
- Fresh-session retry: `CLAUDE_RUNNER_RETRY_FRESH_SESSION=1` (default `0`) makes a retry with a
  previous `session_id` start a new Claude Code session instead of compacting and resuming the
  old one. The retry input carries the task, review findings, previous report, and the previous
  session's digest (newest last); `CLAUDE_RUNNER_RETRY_INPUT_CHARS` (default 32000) is the
  character budget — only the digest is cut, and a marker notes the cut. Both settings are
  overridable per provider with `…__RETRY_FRESH_SESSION` and `…__RETRY_INPUT_CHARS`.
  `tests/test_runs.py` gained `test_retry_fresh_session_per_provider_override` (on → new
  session, off → resume existing, per-provider override), `test_retry_input_cut_keeps_the_fixed_parts`
  (digest cut, fixed parts kept, unchanged when it fits), `test_compact_skipped_when_fresh_session_retry_is_on`
  (compact endpoint returns `skipped` with reason `fresh_session_retry` when the switch is on), and
  `test_compact_runs_when_fresh_session_retry_is_off` (compact still runs when the switch is off).
  `.env.example` documents the two new variables; `claude-runner/README.md` has a "Fresh-session retry"
  section. `RunManager.compact()` returns `skipped("fresh_session_retry")` when
  `retry_fresh_session_for` is true, so no `/compact` runs when fresh-session retry is on.
- `tests/conftest.py` now puts `src/` first on `sys.path`, so tests always run against the source
  tree even when the venv has a stale (non-editable) copy of `claude_runner`.

## 2026-10-02

### claude-runner
- The dashboard gained an Auto/Light/Dark theme switcher in the sidebar. An inline script in
  `<head>` (before any stylesheet) reads the `ahawr-dashboard-theme` localStorage key and applies
  `data-theme` to `<html>` before first paint, so there is no flash of the wrong theme; it is
  wrapped in try/catch and falls back to Auto on error. `:root` sets `color-scheme: light dark`
  with `light` for `[data-theme="light"]` and `dark` for `[data-theme="dark"]`, and each palette
  defines the full set of CSS variables (the `--mono` monospace stack is theme-invariant). The
  choice is persisted via localStorage and cycles Auto → Light → Dark with the "Theme" button.
- `tests/test_dashboard.py` gained `test_dashboard_theme_switcher`: it checks the "Theme" button
  and the `ahawr-dashboard-theme` key, the Auto → Light → Dark cycle, that the localStorage
  read is in `<head>` before any stylesheet, and that the dark palette defines every visual
  variable the light one does.
- `claude-runner/README.md` documents the Theme button: Auto follows the OS
  `prefers-color-scheme`, the localStorage persistence (with Auto removing the key), the early
  `<head>` script and the `color-scheme` declarations.

## 2026-10-05

### AHAWR workflow
- `Parse Review` no longer fails the run on an unreadable verdict. A final
  `{"status","score","reason","next_task"}` object whose strings contain unescaped double quotes (a
  local Reviewer wrote a shell command with `""` in `reason`) is read leniently; with no verdict at
  all the new loop (`Review Parsed?` → `Prepare Verdict Retry` → `Reviewer Verdict Retry Start` →
  `Verdict Retry Result`) asks the Reviewer again in the same session for the JSON only, up to
  `hermes_config.reviewer_max_retries` times, and fails with the original error when they run out.

### claude-runner
- Search-first hook: with `SEARCH_FIRST` a PreToolUse hook refuses exploratory `grep`/`rg`/`find`/
  `Grep`/`Glob` until the session has used `ahawr-search` and reminds every few searches after it.
  Over 26 local Worker runs the prompt rule ("MANDATORY: search with ahawr-search first") gave 4
  runs with a call, and only early in the run, against 413 `grep` commands. Enabled for the `LOCAL`
  provider in `docker-compose.yml`.
- `ahawr-search` accepts `--query`/`-q`, an unquoted multi-word query and the usual spellings of
  `--k` and `--budget`; an unknown option is reported and ignored.
- `ahawr-search` no longer hides why it failed. A Worker ran `ahawr-search "docs/PORT.md"
  /d/…/project --max 20` and got `retrieval unavailable`: argparse had taken `--max` as an
  abbreviation of `--max-tokens`, the service rejected a budget of 20 (HTTP 422, minimum 100),
  and every failure printed the same line. Now `--max`, `-n` and `--count` set the number of
  fragments, abbreviations are off, the budget is clamped to 100–100000, an existing folder
  given as an extra path argument (`/…`, `./…`, `../…`) becomes the root unless `--root` is set,
  and the failure line says what happened: request rejected (with the HTTP status and detail),
  service failed, unreachable, or no answer in time (a folder searched for the first time is
  indexed by that call, so repeat it or add `--timeout 120`). The default timeout is 30 s instead
  of 10: the first search of a folder indexes it inside the request, which took 8-9 s for 30 small
  files, so in a live mission every new folder timed out (four calls in a row, the same folder
  three times) while the service went on indexing. `--help` shows how to search
  another folder with `--root`. Applies after the runner is rebuilt.
- The Reviewer profile allows read-only Bash by default (`ahawr-search`, `grep`, `ls`, `wc`, `head`,
  `tail`, `cat`, `diff`, `bash -n`, read-only `git` incl. `git -C <dir>`) and denies Bash redirections
  to a file and `git … --output`. In `dontAsk` a command runs only if a rule allows it, and a local
  mission logged 105 denied Bash calls from its Reviewers, so they judged reports they could not
  check. `CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS` replaces the list (empty = no Bash as before).
  Applies after the runner is rebuilt.

## 2026-10-01

### ahawr-retrieval (mission ahawr-rag-effectiveness)
- Measured first: in the GDN mission the Worker's file-level precision was 46% and recall 25%; one
  3408-line `.patch` took 34.4% of the selected fragments and 44.8% of the tokens; the Worker never
  searched (RAG was one context block at task start).
- `changed_paths` request field (optional) and an automatic Reviewer focus: files the Worker changed
  after its request for the same task are boosted for the reviewer profile; part of the cache key.
- `corpus_roots`: a corpus for the agent's actual working folder is indexed on first use and
  incrementally after that; a corpus whose root is gone is reported stale and serves nothing.
- Related tests (`tests/test_x.py`, `conftest.py`, imported fakes) are added to the context after the
  original fragments, at most 3 files. Same-corpus A/B on the gold sets: nDCG@5 and MRR unchanged,
  ContextRecall +0.008. Open: gold-v1 latency +50% mean / +120% p95 in a single run per version.
- Backups and edit artefacts are no longer indexed (`RETRIEVAL_EXCLUDE_GLOBS` replaces the list);
  `.patch`/`.diff` files over 1000 lines are demoted unless the query names them.
- An intentionally disabled reranker is a note, not `degraded`.
- `ahawr-retrieval usage`: per-profile precision/recall, used token share, `ahawr-search` calls and
  missed files from the retrieval log joined with claude-runner events.
  Open: on live data it reports 0 opened files, because claude-runner run summaries carry no
  mission/task id to match runs to requests (the fixtures assumed one).
- Fixed on deploy: an existing retrieval log failed to open (`no such column: trace_mission_id`)
  because the new indexes were created before the column migration.

### claude-runner
- `ahawr-search "query" [--k N] [--budget T]` in the image: on-demand retrieval for agents from the
  folder they run in, 1500-token default budget, fail-open (exit 0 when the service is down).
- Runs may last up to `CLAUDE_RUNNER_MAX_RUN_SECONDS` (raised to 10800 in this stack's `.env`):
  eval-heavy Worker runs hit the 2-hour limit.
- git in the image uses `core.autocrlf=true`, so a Windows checkout mounted into the container no
  longer shows every CRLF file as modified (85 false changes confused a scope check).
- The dashboard's summary strip showed the Claude session's cumulative cost and API time: a
  resumed Worker run of 19.6 min showed $4.07 and 90 min of API time, the sum of three attempts.
  It now shows the run's own share, which the runner already stored as `cost_usd`; the runner also
  stores the run's API time (`api_ms` in `GET /v1/runs/{run_id}`, `run_duration_api_ms` in
  `details`) as the difference to the previous run of the same session.

## 2026-09-30

### claude-runner
- The runner's `/compact` before resuming a session follows the probed window too:
  `COMPACT_MIN_TOKENS` is taken as a share of 65536 (40000 → 60000 at 96K), or `COMPACT_MIN_PCT`
  sets the percentage, never above the autocompact trigger. With the fixed 40000 a 96K Worker was
  compacted at 46-52K before each retry, which took longer (4.6-7.1 min) than the retry itself.
- The local model's context window follows llama-server. Before each run of a provider with
  `CONTEXT_PROBE_URL` (set in `docker-compose.yml` to `http://host.docker.internal:8033`, key from
  `LLAMACPP_API_KEY`) the runner reads the model's `--ctx-size` from `GET /v1/models` and sets
  `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to it. The autocompact
  trigger is a percentage of that window (`COMPACT_PCT`, else `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`,
  else 67.66% = the static 44344 / 65536), capped so one reply and one large tool output still
  fit: 64K compacts at 44344 as before, 96K (the transactional-replay + `--cpu-mtp` build fits it
  on 12 GB) at 66516, 32K IQ3_XXS at 11576. The run's event log gets a `context_window` record;
  without an answer the static settings apply.

### Prompts and workflow
- Working language is English. The Architect writes the plan and every task in English whatever
  the mission's language, keeping verbatim texts (paths, identifiers, required strings and
  headings) in the original language; the Worker writes notes, reports and code comments in
  English; the Reviewer writes `reason` and `next_task` in English. Measured on the GDN mission:
  English takes 5-15% fewer tokens (Qwen 490 → 416 on a task, Claude 753 → 675), and the Worker
  no longer mixes English rules with a Russian task and its own Russian notes.
- Mission report in the mission's language. For a non-English mission the Architect adds a last
  task `Mission report (<language>)` that returns a report of the whole mission in that language
  without changing files; the Reviewer checks the language, coverage and facts. `Approved Result`
  takes that task's Worker output as `final_report`, and the `✅ WORKFLOW APPROVED` Telegram
  notice shows it instead of the last review's reason.

## 2026-09-29

Results of the `ahawr-fast-compaction` mission, plus fixes found while it ran [11a278e].

### claude-runner
- Shorter compaction summaries for the local provider. A PreCompact hook, passed with
  `--settings`, gives Claude Code short compact instructions: 1500–2000 tokens in fixed
  sections (Task, Findings, Changed files, Checks, Next step). Before this, summaries were
  about 8K tokens and took about 6 minutes.
  - Turn it on or off with `CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS`.
  - Override the text with `CLAUDE_RUNNER_COMPACT_INSTRUCTIONS`.
  - Bound a compaction with `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS`.
- Startup check of the auto-compact headroom. The runner refuses to start if Claude Code's
  trigger leaves less than 20000 tokens below the context window. The recommended local
  settings use a 65536-token window and the built-in trigger at 44344. See the
  `CLAUDE_RUNNER_PROVIDER_LOCAL__*` block of `.env.example`.
- Tool output is capped for the local provider, so one Bash or Read result cannot fill the
  window: `BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`.
- A final report cut at `max_tokens` is now stored whole. Before, only the continuation was
  kept.
- `cost_usd` is now this run's own share. Claude Code restores the session's total cost on
  `--resume`, so the runner subtracts the previous run's total. The cumulative value stays in
  `details`.
- The local Worker model is priced like Claude Sonnet 5 ($2 in, $10 out, $0.20 cache read,
  $2.50 cache write per Mtok) through `/etc/claude-code/managed-settings.json`. This is an
  estimate for comparison, not a bill.
- pytest, ruff and mypy are installed in the image, so Workers do not build their own venv.
- Deny rules cover `.env`, `.env.local`, `.env.*.local`, `.env.production` and the other
  variants, but keep `.env.example` readable.

### n8n workflows
- Claude Code Run Manager: `context_overflow` is passed through, so a "Prompt is too long"
  error forces a compaction before the retry.
- AHAWR (both variants): the retry counter takes the highest attempt logged for the task.
  Resetting `task_attempt` by hand no longer lets a task run past `max_attempts_per_task`.
- Worker and Reviewer prompts are in English. The Worker now:
  - starts from RETRIEVED CONTEXT and reads files by line range;
  - batches independent reads into one turn;
  - never prints environment variables;
  - checks every acceptance criterion with evidence before reporting.

### Other
- Data Table CSVs: the repository keeps `hermes_config.example.csv` (profiles for Hermes, Claude
  Code on Anthropic, Claude Code with a local Worker, OpenAI), `agent_prompts.example.csv` and
  `missions.example.csv`; live exports and mission files live in the gitignored `data-tables/`.
- Repository renamed to `n8n-agent-orchestrator`.
- An AHAWR task now stops at `max_attempts_per_task`; before, the failure route was lost and the
  task was retried without end.
- LiteLLM hook test (`litellm/test_ahawr_hooks.py`).
- `docs/compaction-analysis.md`: how Claude Code 2.1.283 compacts, and what can be tuned.
- New missions: `llamacpp-speed-tuning`, `ahawr-rag-effectiveness` (v2, on-demand
  `ahawr-search`) and `ahawr-retry-and-microcompact`.

## 2026-09-28

### Retries and recovery
- A retried Worker gets its previous full report back and must return a complete updated
  report. The Reviewer checks that the parts it accepted earlier are still there [9c406ec].
- The Worker keeps its session across `needs_changes` retries [162b793].
- A failed or timed-out task resumes where it stopped [590de39]:
  - a context overflow is retried, and every retry goes through compaction;
  - `GET /v1/sessions/{id}/digest` hands progress to a fresh session when compaction fails;
  - resuming sends RESUME TASK with the acceptance criteria, not the whole task again.
- A passed review now advances to the next task. Each new task starts new Worker and
  Reviewer sessions [ae2cc88].
- The Reviewer gets the current task and the latest Worker result. The attempt counter now
  grows [5adc24a].

### Dashboard (`http://localhost:8701`)
- Live runs of Claude Code and Hermes in a Trajectory layout: a timeline, a ledger, steps
  with TTFT, tokens and tok/s, and an inspector [03f6364, 1d186bc].
- Export of runs and sessions, and the RAG context of each prompt [585e297].
- A Stop button for Claude Code runs. It also stops the tool processes the run started
  [6f6126b].

### Retrieval
- Built-in CPU embedder (multilingual-e5-small) and optional reranker, 8 chunks per file
  [561cd69].
- Dockerfile variants and patches are indexed [10024b1]. Indexing follows `.gitignore`, and
  the context is line-numbered and trimmed [c3e9e63].

### Other
- Telegram messages are sent as plain text and cut to Telegram's limit [2d0dba5].
- Every role resolves paths against the mission folder, which the TASK STARTED notice shows
  [ec9f47a, 897a69d].
- Missions for JSA, the llama.cpp prompt cache and RAG admin auth [35dee91, 2dfe04d].

## 2026-09-27

- **AHAWR on Claude Code**: `claude-runner` runs the Claude Code CLI headless behind the
  Hermes-compatible `/v1/runs` API. Added `Claude_Code_Run_Manager_v1.json` and
  `AHAWR_v13_ClaudeCode.json` [da320d7].
- LiteLLM container between Claude Code and a local llama.cpp server [2ce5372]. It is built
  and started with the rest of the stack [0d7998f].
- Each role is routed to its own provider (`CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>`)
  [b582a55]. The local model is chosen only in `hermes_config` [a3daaf7].
- A LiteLLM hook moves Claude Code's mid-conversation system messages into user messages
  for Qwen-style chat templates [1ad5e08].
- Run Manager calls no longer depend on workflow ids or input mapping [7f2a81f]. Dropped
  the n8n credential from the runner HTTP nodes [b33de5c].
- Projects: `missions.working_directory` selects the folder and the retrieval corpus, and
  the host drive is mounted at `/d` [190a0ad, 667d044, 631251b].
- **Context Retrieval Layer** (`ahawr-retrieval`, AHAWR v13): hybrid BM25, vector and symbol
  search with provenance, and an eval harness. It runs as its own container in the compose
  stack [189f7d0, 61b3e75, 82242ca].
- Hermes Run Manager v5: fixed the `retry_count` reset and the stale compression target
  [78ed272].

## 2026-09-11 – 2026-09-17

- First n8n + Hermes workflows (AHAWR, Hermes Run Manager), Data Tables for config, prompts
  and missions, a Dockerfile and scripts, and bug fixes [8e07cf0 … d79545f].
