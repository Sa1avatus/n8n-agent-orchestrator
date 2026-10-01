# Changelog

Notable changes to the AHAWR stack: n8n workflows, `claude-runner`, `ahawr-retrieval`,
LiteLLM, the dashboard and the missions. Newest first. Commit ids are in brackets.

## 2026-10-01

### claude-runner
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
