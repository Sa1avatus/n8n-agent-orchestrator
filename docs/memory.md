# Memory levels of a runner session

Inventory of where a session keeps its memory, read from `claude-runner/src/claude_runner/`
(`runs.py`, `handoff.py`, `claude_cli.py`, `store.py`, `events.py`, `config.py`, `context_probe.py`,
`api.py`) and `claude-runner/README.md`. Line numbers were checked against the working tree on the
day of writing. `UNKNOWN` means the code in this repository does not prove the fact.

Level 1 is the context of one Claude Code session while it runs. Level 2 is what the runner keeps
between sessions and runs, so a session can be resumed or handed over to a new one.

## Level 1: short term

### Session window

The context of the Claude Code process. The runner does not hold it; it only reads the size back.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Session transcript `<id>.jsonl` | `claude_cli.py:29-39` (`config_dir`, `transcript_exists`); path `<CLAUDE_CONFIG_DIR or ~/.claude>/projects/<project>/<claude_session_id>.jsonl` | Claude Code's context window: probed `--ctx-size` (`context_probe.py:119` `window_env`) or static 65536 (`config.py:200`) | Claude Code CLI (the runner does not write it; the path is the runner's assumption, `claude_cli.py:35`) | Claude Code on `--resume`; runner checks existence at `runs.py:375-379` | UNKNOWN: no delete of a transcript in `runs.py`, `claude_cli.py`, `handoff.py` |
| Context size per session, `sessions.context_tokens` | `store.py:36` (column), `store.py:129-135` (`touch_session`), `runs.py:719` (write), `claude_cli.py:382-391` (`final_context_tokens`) | INTEGER, no limit in the schema | runner, from the stream usage (`claude_cli.py:339`, `352`) or the `compact_boundary` post size (`claude_cli.py:355-359`) | `runs.py:911` (compaction threshold check), `api.py` session view | reset to 0 when the Claude session id changes (`store.py:124-125`); otherwise kept |
| Live size of the last run, `runs.context_tokens` | `store.py:56`, `runs.py:690` | INTEGER | runner (`runs.py:690`) | dashboard run views (`run_views.py:75`) | kept with the run row (no delete, see Level 2) |

### Autocompact (Claude Code's own trigger)

Claude Code compacts the transcript by itself when the context reaches its trigger. The runner only
sets the inputs and checks them.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Autocompact trigger | `config.py:261` (`_autocompact_threshold`), `context_probe.py:93` (static 44344 = 67.66% of 65536) | window − min(max_output, 20000) − 13000 (`claude-runner/README.md`, "Claude Code's own autocompact trigger"; the formula is not in code in scope, UNKNOWN there) | operator, via provider block `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` / `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (`config.py:219-221`); runner sets env from the probe (`context_probe.py:119`) | Claude Code | UNKNOWN (done by Claude Code; the runner does not clear) |
| Startup validation of the trigger | `config.py:358-369` | trigger must stay in the 65536 window and leave 20000 tokens of headroom (`config.py:358-369` docstring and checks) | runner at startup (refuses to start on violation) | runner | n/a (config, not data) |
| Event record of the window used | `runs.py:540-544` | n/a | runner writes `context_window` event | dashboard, event log | with the event file (Level 2, `events.py:308-318`) |
| Pre-resume `/compact` threshold | `config.py:513` (`compact_min_tokens` = 120000), `runs.py:864-865`, `runs.py:901-914` | 120000 default; provider `…__COMPACT_MIN_TOKENS` overrides (`runs.py:901-902`); probed window scales it (`runs.py:908-910`, `context_probe.py:108`) | runner (`runs.py:883-914`) | runner decides whether to compact before resume (`mode == "auto" and tokens < threshold`, `runs.py:912-917`) | n/a: the compaction result is recorded (see PreCompact summary) |

### PreCompact summary (custom compact instructions)

The text that tells Claude Code how to summarise. The summary itself is written by Claude Code into
the transcript.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Instruction text (default) | `config.py:37` (`DEFAULT_COMPACT_INSTRUCTIONS`), `config.py:618-621` (`CLAUDE_RUNNER_COMPACT_INSTRUCTIONS` overrides; empty = stock summarizer) | Target 1500–2000 tokens, hard cap 2000 per `claude-runner/README.md:115`; the runner code in scope does not enforce it (UNKNOWN). The `max_tokens` cap 3000 is in `ahawr_hooks.py`, outside this scope (UNKNOWN here) | operator (env) or default in code | Claude Code (via hook or `/compact` text) | n/a (config) |
| PreCompact settings file `compact-settings.json` | `claude_cli.py:46-70` (`_compact_settings_path`), `claude_cli.py:52` (path `config_dir()/compact-settings.json`), `claude_cli.py:56` (`PreCompact` key) | the hook prints the instruction text (`claude_cli.py:42-43`) | runner, written on each run that needs it (`claude_cli.py:190-203`) | Claude Code appends hook stdout to the compaction request (behaviour stated in `claude-runner/README.md:112`, not proven by code here) | overwritten by the next run that writes it; never deleted (UNKNOWN whether anything removes it) |
| Search-first settings (also carries `PreCompact`) | `claude_cli.py:72-100` | same text as above | runner | Claude Code | overwritten, as above |
| Which runs get the instructions | `claude_cli.py:184-194` (`compact_hook`: local provider with `isolates_credentials`, and `local_compact_instructions` on, `config.py:517`) | n/a | runner | n/a | n/a |
| `/compact <instructions>` text on a resume compaction | `runs.py:916-931` (`"/compact " + compact_instructions`, local provider only) | same text | runner | Claude Code | n/a (sent as the prompt of a `kind="compact"` run) |
| Compaction result | `claude_cli.py:355-361` (`compact_boundary`, pre/post tokens), `runs.py:962-974` | n/a | Claude Code events, read by runner | runner (`runs.py:962-974`), event log (`runs.py:637-638`) | with the event file (`events.py:308-318`) |
| Compaction run timeout | `config.py:518` (`compact_timeout_seconds` = 600), `runs.py:516` | 600 s default | operator (env `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS`) | runner | n/a |

## Level 2: middle

### Session resume

Lets a new run continue the same Claude Code session, or start a new one when resume is not possible.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| `sessions` table in `runner.sqlite` | `store.py:28-36` (schema), `store.py:82` (connect), `runs.py:191` (path `settings.data_dir / "runner.sqlite"`), `store.py:1` (on the runner's `/data` volume) | UNKNOWN: no row limit in code | runner: `bind_session` (`store.py:115-127`, called at `runs.py:373`, `378`, `383`, `386`), `touch_session` (`store.py:129-135`, called at `runs.py:355`, `718-719`) | runner: `_bind` (`runs.py:360-386`), `compact` (`runs.py:886-911`), `api.py` `get_session` | UNKNOWN: no `DELETE` or `DROP` in `store.py` (grep: 0 matches) |
| `runs` table | `store.py:38` (schema), `runs.py:191` (store) | UNKNOWN | runner (`create_run`, `finish`) | runner, dashboard | UNKNOWN: same as above |
| Resume decision | `runs.py:375-379` (resume if `transcript_exists`), `runs.py:326-333` (fresh retry), `runs.py:362-368` docstring | n/a | runner (`_bind`) | runner (`_launch`, `runs.py:356`) | n/a |
| Fresh session on retry (no resume) | `runs.py:326`, `runs.py:897-900` (compaction skipped when fresh retry is on) | n/a | runner | runner | n/a |

### Digest handoff

A digest of what the previous session already did. It is built on demand from the runs table and
the event logs, never stored, and it is used as input of a new session.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Digest builder `session_digest` | `handoff.py:76-100`; keeps the newest lines when cut (`handoff.py:85-93`) | `max_chars` argument; per entry: `TEXT_CHARS` 1500, `TOOL_INPUT_CHARS` 300, `TOOL_RESULT_CHARS` 400, `RESULT_CHARS` 3000, `PROMPT_CHARS` 400 (`handoff.py:18-22`) | runner, built on demand (not stored) | (a) `api.py:480-489` `GET /v1/sessions/{id}/digest`, default `max_chars` 12000 (range 500–200000); (b) Codex compaction `runs.py:920-925`; (c) fresh-session retry input (via `runs.py:335`) | none: the digest is not persisted; it is rebuilt each time from Level 2 sources below |
| Retry input cut | `runs.py:53-68` (`cut_retry_input`), `runs.py:335` | `CLAUDE_RUNNER_RETRY_INPUT_CHARS`, default 32000 (`config.py:197`, `config.py:568`, `config.py:676-677`) | runner | runner (`runs.py:335`), then the new session reads it on stdin (`runs.py:582`) | n/a: the input is cut to the budget, the digest part is trimmed first; the prompt is also recorded as a `prompt` event (`runs.py:402`) |
| Run activity log (digest source) | `runs.py:216` (`EventLog(settings.data_dir / "events", …)`), `events.py:292-306` (directory and open), `handoff.py:80` (read) | `event_retention_days` default 14 (`config.py:558`, `config.py:668`) | runner, one `<data>/events/<run_id>.jsonl` per run (`events.py:303-306`) | `handoff.py:80` (`manager.events.read`), dashboard, `search.py` | pruned by age: `events.py:300-301` (prune on start when retention > 0), `events.py:308-318` (deletes files older than the cutoff by mtime) |
| Digest in the Codex handoff | `runs.py:920-925` (`CODEX_HANDOFF_PREAMBLE`, `CODEX_HANDOFF_TASK_CHARS` at `runs.py:46-50`) | budget = `retry_input_chars` − preamble | runner | Codex thread start | n/a |

### Live State

One row per mission, keyed by `state_key` (the mission's `state_namespace`). It holds the current
execution state of the mission: session ids, plan, task index, attempt and status. The n8n workflow
is the main writer; the runner's board code reads it and writes it through the board routes.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Live State table (n8n data table) | `board_pg.py:52` (`_LIVE_STATE_TABLE = data_table_user_Yn9lOhVhQ9gFymNs`); model `board.py:90-110` (`LiveState`) | UNKNOWN: no limit in code; `plan_json` is a free JSON object | n8n workflow (writes not in repo, UNKNOWN which fields); runner routes below | runner, dashboard and board (see Readers) | no delete path in `board_pg.py` (Pg store has no delete); see Clearing below |
| Session ids: `worker_session_id`, `reviewer_session_id`, `architect_session_id` | `board.py:94`, `board.py:103`, `board.py:110` | string | workflow; `board_pg.py:740-768` (upsert takes any known column) | `board_pg.py:491-530` (`get_live_state` maps the columns) | overwritten by the next write |
| Plan: `plan_json` (Python field `plan`) | `board.py:95`; `board_pg.py:740-768` (plan serialised to `plan_json`) | JSON object | `POST /api/board/live-states/{state_key}` (`board_api.py:830-840`); plan routes `board_api.py:884, 927, 966, 985, 1004, 1021, 1039` | `board_api.py:843` (`GET /missions/{id}/plan`), `board.py:307-309` (task statuses), `board_ready.py:217-219` (readiness: plan present) | overwritten by the next plan write |
| Task index and attempt: `task_index`, `task_attempt` | `board.py:96-97` | float (number) | workflow; `board_api.py:1085-1089` (task attempt write) | `board.py:307-309`, `board_stats.py` (via store) | overwritten |
| Status: `status` (`active`, `stopped`, ...) | `board.py:98`; stop route `board_api.py:1121-1132` (writes `status="stopped"`, line 1132) | string | workflow; stop route | `board.py:255-259` (running missions), `board.py:269-287` (mission status), `board_ready.py:235` (must not be running) | overwritten; a stale `active` row is listed as not completed (`board.py:85-87`, `board.py:117-128` `is_stale_active`, 6 hours) |
| Review feedback and score: `review_feedback`, `last_review_score`, `last_review_reason` | `board.py:99-101` | text, number | workflow | dashboard (`board_api.py:715-718` returns the whole row) | overwritten |
| Run ids: `worker_run_id`, `worker_status`, `reviewer_run_id`, `reviewer_status`, `architect_run_id`, `architect_status` | `board.py:104-109` | string | workflow | `board.py:287`, dashboard | overwritten |
| Create | `board.py:404` (create_mission path), `board_ready.py:14` (atomic create with the missions row and queue entry), `board.py:893-946` | n/a | runner (`board_ready.create_mission`) | n/a | n/a |
| Optimistic lock | `board_pg.py:740-768` (`SELECT "updatedAt" ... FOR UPDATE`, conflict when the version differs); in-memory `board.py:810` | `updatedAt` stamp | every write passes `expected_version` (`board_api.py:831-839`) | n/a | n/a |
| Delete of the row with a mission | `board_api.py:786-800` (in-memory store only; the Pg store raises, `board_api.py:791-793`) | n/a | operator via `DELETE /missions/{id}` with `confirm=true` for a running mission | n/a | the row is removed only in memory |
| Control flags (Telegram) in a table also named "Autonomous Agent Live State" | `board_ops_livestate.py:1-10` (`data_table_user_innMqY8MMiUE9Lrf`) | whitelisted columns | `set_flags` in `board_ops_livestate.py` (version check, changelog) | `get_flags` in the same module | UNKNOWN whether this is the same physical table as `board_pg.py:52` |

Related tables the workflow writes per task (named in `claude-runner/docs/lessons.md:38-39`):
`Autonomous Agent Task Attempts` (events, `task_attempt`, `review_status`) and `Autonomous Agent Task State`.
Their link to the Live State row is UNKNOWN in this inventory (the workflow export is not in the repo).

## Level 3: long term

Memory that outlives a mission. The only store the runner writes for it is the lessons library. The
other items are inputs that the Worker or the search reads.

| item | location (file:line) | size limit | writer | reader | clearing |
|---|---|---|---|---|---|
| Lessons library `board.lessons` (Pg) | `claude-runner/docs/board-lessons.sql:11-28` (table, CHECK limits); store `board_lessons.py:242` (`PgLessonStore`) | mission 64, task_id 16, situation 400, lesson 300, evidence 1200 characters (`board-lessons.sql:13-19`) | `POST /api/board/lessons/write` (`board_api.py:1818-1836`) via `build_candidate` and `decide` (`board_lessons_extract.py:129`, `:183`) | `POST /lessons/select` (`board_api.py:1838-1857`); `GET /missions/{id}/lessons` (`board_api.py:1760`) | status `retired` (not deleted): `board_lessons.py:398-408`; delete only with `confirm` (`board_api.py:1811-1816`, `board_lessons.py:334`) |
| Lessons in memory | `board_lessons.py:79` (`InMemoryLessonStore`), used when the table is not reachable (`board_api.py:151-168`) | same | same | same | lost on restart |
| Lesson-shown link `board.lesson_shown` | `board-lessons.sql:34-40` (`ON DELETE CASCADE`) | one row per lesson, mission and task | `record_shown` (`board_lessons.py:357-377`, called from `board_api.py:1856`) | `lessons_for_task` (`board_lessons.py:410`) | cascades with the lesson |
| Retrieval corpora of a mission | `board.py:79` (`retrieval_corpora`) | list of names | operator, mission config | retrieval service (`retrieval-service/src/ahawr_retrieval/pg_store.py`, read-side only seen) | UNKNOWN: the corpus contents are not in the runner |
| Mission working directory | mission field `working_directory` (`test_board_api.py:1193-1194` shows the field) | path | operator | Worker (UNKNOWN which code reads it for memory) | not a runner store |
| Search index of the work folder and corpora | `ahawr-search` (retrieval service) | UNKNOWN | indexer (not traced) | `ahawr-search` | UNKNOWN |

## Lessons library findings

Facts from the code, read in this inventory. The library is OFF by default (`lessons_enabled`).

**Record shape.** `LessonCandidate` (`board_lessons.py:54-63`) has: `mission`, `task_id`, `task_index`
(1 to 999), `kind` (`fixed-after-retry` or `gave-up`), `situation`, `lesson`, `evidence` (a dict).
`Lesson` (`board_lessons.py:66-76`) adds: `id`, `created_at`, `status` (`active` or `retired`),
`times_shown`, `times_first_attempt_passed_after`, `times_seen`, `version`, `updated_at` (the lock stamp).
The evidence keys are set in `board_lessons_extract.py:117-124`: `failed_review_reason`,
`accepted_review_reason`, `failed_score`, `accepted_score`, `failed_attempt`, `accepted_attempt`,
`touched_paths` (at most 5 paths, 120 characters each). The lesson text is the failing review's
`review_next_task`, or its `review_reason` when that is empty (`board_lessons_extract.py:99-107`).

**How a lesson reaches a task (store to Worker input).**
1. Write: the workflow posts the task's review rows to `POST /lessons/write` (`board_api.py:1818-1823`).
   `build_candidate` (`board_lessons_extract.py:129-150`) picks the first `review_status == "pass"` row
   as the accepted attempt and the attempt before it as the failure; no pass after `max_attempts`
   rows gives `gave-up`.
2. Dedup: `decide` (`board_lessons_extract.py:183-193`) returns `drop` (the lesson repeats a mission
   rule), `bump` (a near-duplicate exists; `bump_duplicate` raises `times_seen`) or `add`.
3. Select: the Worker prompt builder calls `POST /lessons/select` (`board_api.py:1838`). It returns
   nothing when `lessons_enabled` is off (`board_api.py:210-211`, `1840-1841`) or when the holdout
   withholds the task (`board_stats.py:508-516`, `board_api.py:1844-1845`). Otherwise it calls
   `select_lessons(store.list(mission, "active"), ...)` (`board_api.py:1846-1852`).
4. Ranking and cap: `select_lessons` (`board_lessons_extract.py:222-249`) keeps only `active` rows,
   ranks by shared file paths, shared scope words (from task title, objective and scope), then kind,
   then `created_at`. **Cap per task: 5 lessons** (`LESSONS_K`, `board_lessons_extract.py:34`) and
   **1500 characters** for the rendered block (`LESSONS_BLOCK_MAX_CHARS`, `board_lessons_extract.py:35`).
5. Counting: `record_shown` (`board_api.py:1855-1856`) raises `times_shown` and inserts `lesson_shown`.
6. Input: `lessons_patch.py` (docstring lines 5-9) appends the block to the Worker input in
   `Build Worker Run Input`. The Reviewer input is not in the patch's anchors (UNKNOWN in the live
   workflow; the export is not in the repo).
7. Outcome: `POST /lessons/outcome` (`board_api.py:1859`) calls `record_outcome`
   (`board_lessons.py:195-210`, Pg `:379-396`), which sets `first_attempt_passed` once per
   lesson-task link and then retires the row when `_is_retire_due` (`board_lessons.py:46-51`) holds.

**review_status and the attempt counters.** They are not stored in the lessons library. They live in
the event log (`Autonomous Agent Task Attempts`, read by the runner through `board_pg.py:536-546`
`PgBoardStore.events`, columns `review_status`, `review_score`, `task_attempt`, `task_index`).
`review_status` is the Reviewer's verdict (`pass` or `needs_changes`), and `_derive_status` reads it
(`CHANGELOG.md`, 2026-10-19, 0.7.200). The per-task attempt counter is `task_attempt` in the event
rows and in the Live State row (`board_pg.py:494-520`). The lessons library copies the scores and attempt
numbers into `evidence`; it does not keep `review_status` as a column.

## Gap list

Each item: EXISTS (file:line) or MISSING. UNKNOWN is marked where the code in this repository does not prove it.

| item | status | evidence (file:line) |
|---|---|---|
| `board_lessons.py` InMemory store | EXISTS | `board_lessons.py:79` |
| Pg store with version check | EXISTS | `board_lessons.py:242`; version check `board_lessons.py:269-276` (`updated_at` in the UPDATE WHERE, `ConflictError`) |
| Guarded routes: list | EXISTS | `board_api.py:1760-1763` (GET, no header check) |
| Guarded routes: get | EXISTS | `board_api.py:1783-1788` |
| Guarded routes: edit (PUT) | EXISTS | `board_api.py:1790-1797`, `_require_dashboard_header` at `board_api.py:1792` |
| Guarded routes: retire | EXISTS | `board_api.py:1799-1803` |
| Guarded routes: restore | EXISTS | `board_api.py:1805-1809` |
| Guarded routes: delete with confirm | EXISTS | `board_api.py:1811-1816`; confirm in store `board_lessons.py:167`, Pg `board_lessons.py:334` |
| SQL files | EXISTS, not applied | `claude-runner/docs/board-lessons.sql:1-6` ("NOT APPLIED"); UNKNOWN whether the live database has it |
| `board_lessons_extract.py` | EXISTS | `board_lessons_extract.py:129` (`build_candidate`), `:183` (`decide`), `:216` (`render_lessons_block`) |
| Selection function | EXISTS | `board_lessons_extract.py:222-249` (`select_lessons`) |
| `times_shown` recording | EXISTS | `board_lessons.py:183` (InMemory), `board_lessons.py:357-377` (Pg, `times_shown + 1`), called from `board_api.py:1856` |
| `data-tables/ops/lessons_patch.py` | EXISTS | `data-tables/ops/lessons_patch.py:1-9` (docstring, anchors); not in git (CLAUDE.md) |
| `claude-runner/docs/lessons.md` | EXISTS | `claude-runner/docs/lessons.md:198-203` (counters), `:235-264` ("Activation (operator)") |
| `lessons_enabled` flag | EXISTS | read `board_api.py:210-211`; gate `board_api.py:1840`; patch `lessons_patch.py` FLAG_MARK |
| Mission Lessons panel | EXISTS | `board_mission.js:340` (call), `:772` (`renderLessonsSection`), `:739` (`lessonsStatsHtml`) |
| Holdout switch in mission view | EXISTS | `board_mission.js:753-776` (checkbox `lessons_holdout_enabled`), `:832` (write) |
| Task view lessons | EXISTS | `board_task.js:76-85` (`givenLessonsHtml`); route `board_api.py:1772-1781`. The 2026-10-27 close note (`data-tables/ops/_close_lessons_t013_2026_10_27.py`) said this route was missing; it is present now |
| Stats split by lessons | EXISTS | `board_stats.py:546-560` (`lessons_stats`), route `board_api.py:1765-1770` |
| Lesson scope stored as a field | MISSING | `LessonCandidate` has no scope field (`board_lessons.py:54-63`); `scope` is an input of `/lessons/select` only (`board_api.py:1851`) |
| `lessons_same_workdir` flag | MISSING in code | documented at `claude-runner/docs/lessons.md:173` and `:196`; no match in `claude-runner/src` |
| `lessons_holdout_every` setting | MISSING in code | `lessons.md:207` names it; `board_stats.py:514` says the period is fixed at 2 (shortcut) |
| Lessons written for the workflow's Reviewer input | UNKNOWN | `lessons_patch.py` anchors the Worker input only (docstring lines 5-9) |

**Does lesson scope need a new database column or table?** No. Scope is per mission today: the
`mission` column of `board.lessons` (`board-lessons.sql:13`) is the scope key (`lessons.md:194-196`).
The optional same-workdir widening needs the mission's working directory at selection time, which is
already in the missions table, so it needs a join, not a column. Any extra scope text can go into
`evidence` (`board-lessons.sql:19`, 1200-character limit, `board_lessons.py:41-44` `_check_evidence`)
without DDL. A new column or table would be a database schema change, which CLAUDE.md lists under
"Ask first"; none is needed for the features described in `lessons.md`.

## Open points (UNKNOWN in this inventory)

- Who deletes Claude Code transcripts (`~/.claude/projects/...jsonl`): no code in scope deletes them.
- Whether `runner.sqlite` `sessions` and `runs` rows are ever removed: no `DELETE` in `store.py`.
- Where the enforcement of the 1500–2000 token instruction target happens: not in the scope files.
- The autocompact trigger formula (window − min(max_output, 20000) − 13000) is documented in
  `claude-runner/README.md` only; the code that computes it is `config.py:261` and the constants it
  uses were not traced further.

## Сводка по библиотеке уроков (русский)

Состояние на 2026-10-31 по рабочей копии; номера строк проверены по коду. Строки английской части выше,
где сказано MISSING для поля `scope` и маршрута promote, устарели: они есть (см. ниже).

### Что изменилось (file:line)

- `claude-runner/src/claude_runner/board_lessons.py:24-25` - константы `LESSONS_PROMOTE_MIN_MISSIONS = 3` и ключ `lessons_promote_min_missions`.
- `board_lessons.py:58-66` - `promote_min_missions`: читает ключ из hermes_config, по умолчанию 3; bool, не целое и значение меньше 1 дают ошибку.
- `board_lessons.py:69-82` - `promoted_scope`: правило повышения. Вне тестов не вызывается (в `board_lessons_extract.py` ссылок нет), поэтому `decide` ничего не повышает автоматически.
- `board_lessons.py:103` - поле `Lesson.scope`: `mission` (по умолчанию) или `project`.
- `board_lessons.py:203-208` (InMemory) и `:391-395` (Pg) - `promote`: ставит `scope = project` и проверяет `expected_version`.
- `board_lessons.py:270` и `:336` - Pg-хранилище читает и фильтрует колонку `scope`.
- `claude-runner/src/claude_runner/board_api.py:1810-1814` - `POST /lessons/{lesson_id}/promote`; заголовок `X-AHAWR-Dashboard: 1` проверяется, флаг `lessons_enabled` не нужен.
- `board_api.py:1862` - в `/lessons/select` параметр `scope` означает слова области задачи, а не поле урока.
- `claude-runner/docs/board-lessons.sql` - таблица `board.lessons`; колонки `scope` в нём нет.
- `claude-runner/docs/lessons_scope.sql` - новый файл, НЕ применён: добавляет колонку `scope`.
- `claude-runner/tests/test_board_lessons.py:561-591` - тесты `promoted_scope` и `promote_min_missions`.

### Флаги

- `lessons_enabled` - OFF по умолчанию. Читается из строки hermes_config профиля `claude-code` (`board_api.py:210-211`). Шлюзы: запись `board_api.py:1831`, выборка `:1851`, исход `:1872`. Выключен - блок не добавляется и вызовов рантайма нет (`claude-runner/docs/lessons.md`, раздел "Flag").
- `lessons_promote_min_missions` - сколько разных миссий нужно для автоматического повышения; по умолчанию 3 (`board_lessons.py:24`). Код его читает, но вызова в рабочем пути нет, значение сейчас ни на что не влияет. `claude-runner/docs/lessons.md:255` ещё пишет "not read by code yet": в части чтения это устарело.
- `lessons_holdout_enabled` - настройка миссии, OFF; чередование задач проверяется в `board_stats.py:509-513`.

### Что ещё не измерено

- Эффект уроков не измерен. Метрика `lessons_stats` (`board_stats.py:613`) сравнивает долю первых попыток, прошедших с первого раза, у задач с блоком и без него. Нужны несколько миссий с `lessons_enabled` включённым и `lessons_holdout_enabled` (чередование). Одна миссия ничего не доказывает.
- Размер выборки в `claude-runner/docs/lessons.md` (раздел "Measurement plan") - расчёт, а не измерение: около 82 задач на вариант при доле 0.60 и 0.80.
- Не проверено: автоматическое повышение (не подключено), колонка `scope` в живой базе, маршрут promote на живой доске, n8n-патч (`lessons_patch.py` читает только `lessons_enabled`).

### Шаги оператора

1. Таблица: если `board.lessons` ещё нет, применить `claude-runner/docs/board-lessons.sql` после `board-schema.sql` (идемпотентен).
2. Колонка `scope`: Pg-хранилище её читает (`board_lessons.py:270`), без неё запросы упадут. Файл `claude-runner/docs/lessons_scope.sql` сам помечен как предложение; это изменение схемы, решение за оператором (CLAUDE.md, "Ask first").
3. n8n: вручную в интерфейсе n8n (не SQL) добавить boolean-колонку `lessons_enabled` в строку hermes профиля `claude-code` (таблица данных `data_table_user_1S21PsMqT0FpR7eD`). Колонку `lessons_promote_min_missions` добавлять не нужно: workflow её не читает.
4. Патч workflow: `PYTHONIOENCODING=utf-8 python data-tables/ops/lessons_patch.py <workflow.json>`, затем `node data-tables/ops/js_syntax_check.js <patched.json>`; ожидается `bad 0`. Импорт в n8n делает оператор (шаги 4-6 в `lessons.md`, раздел "Activation (operator)").
5. Включить: `lessons_enabled` = `true` в строке hermes. Выключить: `false`; сохранённые уроки остаются в таблице.

### Открытые пункты

- Номер версии: предложение 0.12.0 в CHANGELOG совпадает с "Proposed 0.12.0"; `claude-runner/pyproject.toml` стоит на 1.0.0. Выбор номера за оператором.
- Колонка для входа оператора в `promoted_scope` не решена (см. заголовок `lessons_scope.sql`).
