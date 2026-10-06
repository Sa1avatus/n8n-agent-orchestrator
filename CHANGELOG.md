# Changelog

Notable changes to the AHAWR stack: n8n workflows, `claude-runner`, `ahawr-retrieval`,
LiteLLM, the dashboard and the missions. Newest first. Commit ids are in brackets.

Version: **0.2.0** (claude-runner `0.1.0` → `0.2.0`, 2026-10-06); the stack's single version number is the runner's, see the README section "Versioning".

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

### claude-runner
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
