# Changelog

Notable changes to the AHAWR stack: n8n workflows, `claude-runner`, `ahawr-retrieval`,
LiteLLM, the dashboard and the missions. Newest first. Commit ids are in brackets.

Version: **0.11.200** (claude-runner `0.11.110` → `0.11.200`, 2026-10-27; `0.11.100` → `0.11.110`, 2026-10-27; `0.11.0` → `0.11.100`, 2026-10-27; `0.10.100` → `0.11.0`, 2026-10-27; `0.10.0` → `0.10.100`, 2026-10-23; `0.9.0` → `0.10.0`, 2026-10-23; `0.8.0` → `0.9.0`, 2026-10-21; `0.7.400` → `0.8.0`, 2026-10-21; `0.7.300` → `0.7.400`, 2026-10-19; `0.7.200` → `0.7.300`, 2026-10-19; `0.7.100` → `0.7.200`, 2026-10-19; `0.7.0` → `0.7.100`, 2026-10-19; `0.6.100` → `0.7.0`, 2026-10-19; `0.6.0` → `0.6.100`, 2026-10-19; `0.5.510` → `0.6.0`, 2026-10-18; `0.5.500` → `0.5.510`, 2026-10-18; `0.5.400` → `0.5.500`, 2026-10-18; `0.5.300` → `0.5.400`, 2026-10-18; `0.5.200` → `0.5.300`, 2026-10-18; `0.5.100` → `0.5.200`, 2026-10-18; `0.5.0` → `0.5.100`, 2026-10-17; `0.4.200` → `0.5.0`, 2026-10-17; `0.4.100` → `0.4.200`, 2026-10-15; `0.4.0` → `0.4.100`, 2026-10-15; `0.3.0` → `0.4.0`, 2026-10-14; `0.2.0` → `0.3.0`, 2026-10-12; `0.1.0` → `0.2.0` on 2026-10-06); the stack's single version number is the runner's, see the README section "Versioning". Rule: new functions raise the middle number by 1 (`0.4.0` → `0.5.0`); a bug-fix-only release raises the last number by 100 (`0.5.0` → `0.5.100`) and its entry lists the fixed bugs; every push adds an entry with the version and the changes.

## 2026-10-27 (0.11.200, lessons library)

New function, OFF by default. Nothing is applied to the database or the live workflow yet.

- **Lessons library** (`claude-runner/docs/lessons.md`, `board_lessons*.py`, `board_lessons_ui.js`, `docs/board-lessons.sql`,
  `data-tables/ops/lessons_patch.py`). When a task is accepted after a failed attempt, or stops at the attempt cap, the runner stores one
  short lesson for the mission, checked against the Reviewer's reasons. The next Worker prompt of the same mission may get up to five of
  them as data. Switches: `lessons_enabled` (hermes_config, OFF) and `lessons_holdout_enabled` (mission config, OFF; every second task is
  the control). The activation steps and the measurement plan are in `claude-runner/docs/lessons.md`, "Activation (operator)".
- **Lessons in the mission and task views.** Routes `GET /api/board/missions/{id}/lessons/stats` and `GET /api/board/missions/{id}/tasks/{task_id}/lessons`; the Lessons panel with the statistics and the alternation switch (with confirm) in the mission view, the lessons given in the task view; `board.html` loads `board_lessons_ui.js`.
- The lessons table `board.lessons` is used when the board runs on PostgreSQL (own connection; the in-memory store with a logged warning when the table is not reachable).

## 2026-10-27 (0.11.200, stuck detector and review calibration)

New functions. The stuck detector is on by default (`0` turns each trigger off); the review calibration ships
with its flag OFF and is not applied to n8n.

- **Review calibration: `task_type` and Reviewer text** (`docs/review-calibration.md`, `data-tables/ops/review_calibration_patch.py`,
  not in git, not applied to the live workflow). An optional `task_type` on each task (`research`, `code`, `docs`,
  `ops`; missing, empty or unknown means `code`). `research` and `docs` tasks get a Reviewer text that checks only
  the acceptance criteria and the facts, not wording or style. `code` and `ops` keep the current Reviewer prompt
  and the `pass` condition. The patch edits the Code nodes `Parse Plan`, `Restore Saved Task State`,
  `Build Reviewer Run Input`, `Constants` and `Retry Current Task`, and is idempotent and anchor-checked.
- **Escalation to the Architect after 3 rejections** (same patch, flag `review_escalation_enabled`, OFF by default).
  Three consecutive stored `review_status` values that are not `pass` set the marker `escalation_to_architect`
  before the retry limit stops the task; the Architect input then gets a short summary of the three attempts
  (at most 300 characters of review feedback each). The routing of the Architect answer and the per-attempt
  `review_status` history are operator steps in the n8n interface; the patch does not change them.
- **Stuck detector for the local Worker** (`think_switch.py`, `config.py`, `api.py`, `claude-runner/README.md`,
  `.env.example`). Two triggers join the loop guard and turn thinking on for `loop_hold_steps` model calls, each
  with its own reason in the `thinking_switch` event:
  - `no file edit in N model steps`: after the first 6 model calls, `CLAUDE_RUNNER_THINK_STUCK_NOEDIT_STEPS`
    (default `12`) model calls without an `Edit`, `Write`, `NotebookEdit` or a `Bash` command with `>` or `sed -i`.
  - `the same error K times in a row`: `CLAUDE_RUNNER_THINK_STUCK_ERRORS` (default `3`) tool errors with the same
    first 160 characters, among the last 16 errors of the run.
  - Shortcut: the edit check reads only the last 256 KB of the run log, so an edit older than that is not seen.
- **Tests:** `claude-runner/tests/test_think_switch.py` (the no-edit, error and default-value cases), and
  `claude-runner/tests/test_review_calibration_patch.py` (anchors, idempotency, a failing anchor writes nothing;
  skipped when `data-tables/ops` or the export is missing).

## 2026-10-27 (0.11.110)

Small change: the providers in the web UI are switched on, with the provider keys encrypted.

- `cryptography` is a runtime dependency of `claude-runner` (`pyproject.toml`).
- `CLAUDE_RUNNER_PROVIDERS_DB=1` on `claude-runner` and `ahawr-dashboard` in `docker-compose.yml` (`litellm_sync` stays off).
  The tables `board.providers` and `board.provider_models` exist in the n8n database and `board_operator` has the
  grants on them.
- The keys are stored in `/secrets/provider-secrets.json` on the new volume `ahawr-provider-secrets`, shared by both
  containers, encrypted with a Fernet master key from `CLAUDE_RUNNER_SECRETS_KEY` (host `.env`, back it up).
- `docs/providers.md` section 10 records the decision, section 12a the activation steps.

## 2026-10-27 (0.11.100)

Bug-fix step. No new functions.

Fixed bugs:

- A Worker that compared its files with HEAD wiped the uncommitted edits of the whole working tree three
  times on 2026-10-26 and 2026-10-27 (a `git stash` followed by a dropped stash, six path stashes, and
  `git show HEAD:<file> > <file>` for twelve files). The runner log of the Worker runs showed the commands.
  - The Worker profile now denies `git stash`, `checkout`, `restore`, `reset`, `clean`, `switch`, `rebase` and
    `apply` (also as `git -C <dir> ...`) and writing `git show` output to a file, next to the existing
    `commit` and `push` denial.
  - Before every Worker run the runner keeps a restore point of the tracked changes under
    `refs/ahawr/snapshots/<run_id>` (`git_snapshot.py`, `git stash create`: the tree and the stash list stay
    untouched; the newest 100 are kept; flag `CLAUDE_RUNNER_WORKER_GIT_SNAPSHOT`, off in the code and on in
    `docker-compose.yml`). A file comes back with `git checkout <ref> -- <file>`.

## 2026-10-27 (0.11.0)

New functions. Every one is behind a flag that is off by default; with the flag off behaviour is unchanged.

- **Host actions** (`HOST_ACTIONS`, `host_actions.py`, `docs/host-actions.md`): a task or the dashboard asks the
  runner to recreate or restart a container (`POST /v1/host-actions`). A whitelist decides the service and the
  tier: automatic for the dashboard and helper services, operator confirmation (Telegram notice and the dashboard
  Control tab) for `claude-runner`, n8n and llama. No task runs `docker` or touches the Docker socket.
- **Queue from the board** (`QUEUE_FROM_BOARD`, on by default, `0` turns it off): the queue shell reads the next
  mission from `board.queue` through `queue_board.py` and falls back to the static list. A mission that never
  ran (no Live State row yet) is taken from the board's queued group instead of being skipped.
- **Providers in the web UI** (`providers_db`, `litellm_sync`, `docs/providers.md`): provider type (API
  compatibility), models, prices and write-only API keys on a Providers tab; `/api/board/providers`, the provider
  registry in the runner, a secrets file (mode 0600, Fernet encryption when `cryptography` and a key are present)
  and LiteLLM route sync. Keys are never stored in the database or returned by the API.
- **Report pack** (`ahawr-report-pack`, `report_facts.py`, `GET /v1/report-facts`, `report_pack_patch.py`, flag
  `report_pack`, off): a generated facts pack for mission report tasks, a Reviewer rubric with a threshold, and a
  Telegram notice after two failed report attempts. The patch is in the live workflow; the flag is off and the
  cloud Worker for reports is not configured yet.
- **Telegram task position**: notices read `Task 27 of 29 (T027)` and, for a split plan, the part numbers
  (`telegram_progress_patch.py`, imported into the live workflow together with the report-pack patch).
- QA: the Postgres stack mode, the provider scenarios, DELETE with the version in the query, a secrets file
  in a temporary folder for the throwaway dashboard.

Known open items: 11 container tests fail for reasons outside this step (POSIX process groups, the static
whitelist test, the guards route, the queue dry run); the LiteLLM sync was not run against a real LiteLLM; the
design text of `docs/providers.md` sections 9, 10 and 15 still says "design only".

## 2026-10-23 (0.10.100)

Bug-fix step after the first real run of the QA container and a check of the whole QA path. No new
functions.

Fixed bugs:

- The QA container found its data (scenarios, fixtures, baselines) next to the installed copy of the
  code, where it does not exist (`no scenario files`). The container now runs the code from the
  read-only repository mount (`PYTHONPATH` in the `ahawr-qa` service).
- QA verdicts and screenshots were written into the read-only repository mount. New `QA_RUNS_DIR`
  (`qa_stack.QA_RUNS_DIR`, used by the runner, the stack and the CLI) points them at the writable
  `qa-runs` volume; `Dockerfile.qa` creates the folder with the user `node` as owner.
- The QA panel scenario failed on the Docker stack: the seeded verdict files lay on the host and the
  dashboard container could not see them. The folder is now shared with the dashboard container read-only.
- The QA worker moved from the retired `data-tables/ops/qa_worker.py` to `python -m claude_runner.qa_worker`
  (host mode, Docker stack).
- Role colours in the dashboard follow the logo (Architect amber, Worker orange, Reviewer violet,
  QA rose, Retrieval teal); QA no longer shows teal.
- The `ahawr-qa` service moves to the compose profile `qa-container` and the runner port 8700 stays
  published on 127.0.0.1: the in-process stack fails 9 of 17 UI scenarios that the Docker stack passes
  (Kanban order, stale-version message, Grid sort, launch settings), so UI QA keeps running on the
  host until the in-process stack is at parity. The API layer passes 49 of 49 in the container.
  See `claude-runner/docs/qa-container.md`.

## 2026-10-23 (0.10.0)

New functions of the missions that ran since 0.9.0: the workflow features as one JSON setting, all of QA
(a request queue that works, its own container, three layers, a smoke crawl), the Worker model by attempt
with an escalation block, and fixes found by the QA scenarios and the operator. Every new switch defaults
to off; with the switches off behaviour is unchanged. The release also takes the two missions that were
planned as 0.11.0 (the QA container and the QA layers): their code is in, their activation steps are the
operator's (see the reports in `reports/`).

- **Features config** (mission ahawr-features-config). `features_json` in `hermes_config` and the optional
  `mission_features_json` hold the workflow features (adaptive split, plan without truncation, run hygiene)
  as one JSON setting, validated by `features_config.py` and `board_features.js`; a Features section in
  the launch settings window (a row per feature plus the free JSON); the split patches are anchored
  edits; Telegram notices show the position of the task and of the part. `claude-runner/docs/features-config.md`.
- **QA gate** (mission ahawr-qa-gate). QA runs on the host through a request queue in the runner
  (`qa_requests.py`, `/v1/qa/requests`, claim and result) served by the host worker `qa_worker.py`; the
  workflow asks and waits with QA nodes; a failing QA sends the mission back at most twice, a blocked
  QA never rejects (fail-open or fail-closed by setting). The runner port is published on 127.0.0.1 only.
  Mode per `roles_json` (`qa.options.mode`: off, final, ui_tasks, nightly).
- **QA container** (mission ahawr-qa-container). `claude-runner/qa/Dockerfile.qa` (Chromium, pytest, ruff,
  mypy, node; non-root; no Docker socket) and `qa/compose-qa.patch` (service `ahawr-qa`, read-only root,
  tmpfs, `qa-runs` volume); the stack mode `inprocess` for the UI and API layers
  (`qa --stack docker|inprocess`); the dashboard fixture `DASHBOARD_QA_FIXTURE`; the worker moves into
  the package (`claude_runner.qa_worker`). Not built or started by the mission: `docs/qa-container.md`.
- **QA layers** (mission ahawr-qa-layers). One request runs up to three layers: `regression` (pytest, node,
  ruff, mypy against a per-project baseline), `api` and `ui`; each layer has its own verdict, a failing layer
  does not skip the others; opt in with `layers` in the `qa` options (default `["ui"]`). The QA panel on
  the mission page reads the verdicts grouped by layer. `docs/qa-layers.md`.
- **QA smoke crawl** (mission ahawr-qa-smoke). `python -m claude_runner.qa smoke`: opens the board, follows
  its links and the first three missions, clicks each control once within bounds (40 pages, 25 clicks per
  page, 10 minutes), never clicks the destructive list of `qa/smoke_seeds.yaml`, refuses hosts other than
  127.0.0.1, localhost and `qa-` containers. Detectors: console and page errors, responses >= 400, null or
  undefined in URLs, a mutation without the dashboard header, bad text, hidden messages, overflow, raw
  i18n keys, no-effect clicks. Off by default (`qa.options.smoke`). Known gap: a click crawl does not
  reproduce the three replayed bugs (a drag and a forced 409 are needed); the proposal is in the report.
- **Worker model by attempt and escalation.** `roles_json` sets the model per attempt (Haiku, then the
  local model, then Sonnet); the new key `worker_escalate` (`model`, `above_steps`, `below_score`) moves a
  task to a stronger model after a heavy task: a retry after more than `above_steps` steps with a score
  below `below_score`, or a whole split part whose split came from that many steps (the split writes
  `split_steps`). Validated in `roles_config.py` and `board_roles.js`. `docs/roles-config.md`.
- **Board store reconnects.** The dashboard rebuilds an unavailable board store (the database that starts
  after it) at most every 5 seconds, a lost connection counts as unavailable, and the board shows a loading
  note and retries a 503 for about 20 seconds instead of a permanent offline message.
- **Kanban.** An interrupted mission that stays in the queue waits in Queued instead of Running
  (`RUNNING_STALE_HOURS`); the board queue is synchronised with the queue shell.
- **Telegram relay in the dashboard container**; the host relay is retired.

Fixed bugs:

- Saving the Features section of the launch settings failed with 409: the `hermes_config` version was sent
  with a `Z` suffix and compared with `+00:00`.
- `PUT /api/board/features` and `PUT /api/board/roles` answered 500: the changelog audit could not
  serialize the timestamps of the `hermes_config` row (`board_pg._audit`).
- The QA runner step `type` with `replace` called a Playwright method that does not exist.
- The Features textareas had no accessible name (`aria-label`), so scenarios could not find them.
- The QA fixture schema lacked the `features_json` column.
- The smoke crawl waited 30 seconds for a control that a re-rendering page had removed (all controls are
  now read in one call) and never visited mission pages.
- A QA scenario clicked Next on the Review page, where the button reads Save changes.

## 2026-10-21 (0.9.0)

New functions of the missions that ran since 0.8.0: the QA role with its stack and runner, the
guards moved into the dashboard with their own tab, the per-attempt worker chain, and fixes found
by the QA scenarios. Every new switch defaults to off; with the switches off behaviour is unchanged.

- **QA role** (missions ahawr-qa-acceptance, ahawr-worker-attempt-chain). `python -m claude_runner.qa
  stack|run|nightly`: a throwaway Docker stack (`qa-*` containers with the four n8n data tables and
  a fixture board), a Playwright runner that clicks through the dashboard like a user (14 scenarios in
  `claude-runner/qa/scenarios`: Kanban drag-and-drop and conflict, settings, plan edit, language,
  guards strip and others), a verdict JSON per run and QA panels on the board. QA is OFF by default
  and is not yet connected to the workflow (the workflow patch follows `ahawr-features-config`).
  The Worker chain (`roles_json`): provider and model per role and per attempt (cloud first, local
  retries) read by the launch settings window and the workflow. See `claude-runner/docs/qa-acceptance.md`.
- **Guards moved into the dashboard container** (mission ahawr-guards-migration). New guards
  `mission_notice`, `idle`, `queue_restart`, `host_heartbeat` and `tg_relay` (the Telegram long-poll
  relay) in `guard_queue.py`, `guard_restart.py`, `guard_heartbeat.py` and `guard_relay.py`; dry-run
  mode and automatic disabling after three errors in a row; a host launcher `queue_launcher.py` that
  executes a restart request written by the container; heartbeat lines in `docker_watchdog.ps1` and
  `gpu_power_governor.py`. Cut over by the operator: `tg_relay` runs in the container; the notice
  guards wait for a host publisher of the queue log (`board.queue_log` is not fed yet), so
  `queue_watch.py` stays on the host. What cannot move (restart of the queue shell, `taskkill`,
  PowerShell, Docker Desktop) is listed in [`docs/guards.md`](docs/guards.md).
- **Guards tab** (`DASHBOARD_GUARDS_TAB`, off by default). A third dashboard tab next to Runs and
  Missions with three views (cards, flow diagram, timelines), a history of the last 500 checks per
  guard saved to the state volume, and `GET /api/guards/history?window=1h|24h|7d`. All texts are
  board i18n keys in English and Russian. With the flag on the old strip above the tabs is hidden.
- **Logo and diagrams:** the Result gear is replaced by a QA gear (rose, bug icon) in `mark.svg`,
  `logo.svg`, `favicon.svg`, `banner.svg` and `flow.svg` (`scripts/make_logo.py`).
- **Fixed bugs:** the settings window could not save a mission's launch settings when the mission had
  no config row yet; the Kanban version-conflict message was invisible; the mission cost was not shown
  in the report rows; Telegram notices vanished (silent retries with an unchecked chat id).
- **Store:** the Postgres board connection no longer uses prepared statements
  (`prepare_threshold=None`), which broke after the database restarted.
- **Docs:** `docs/config-columns-plan.md` (plan to move the `hermes_config` columns into JSON),
  `docs/guards.md` sections for the migration and the Guards tab.

## 2026-10-21 (0.8.0)

New functions of the missions that ran since 0.7.400: the guard supervisor in the dashboard,
thinking control for cloud runs, adaptive split of wide plans and tasks, and the agent guide.
Every new switch defaults to off; with the switches off behaviour is unchanged.

- **Guard supervisor in the dashboard app** (mission ahawr-guards-in-dashboard).
  `GuardSupervisor` (`guards.py`) is created in the dashboard `lifespan`, started there and
  stopped at shutdown. With `DASHBOARD_GUARDS` unset no guard task runs; with
  `DASHBOARD_GUARDS=1` each registered guard runs in its own task. `GET /api/guards` is
  read-only like the other dashboard read routes and returns `enabled`, `dry_run`, `running`
  and, per guard, `name`, `status`, `last_run`, `last_result`, `last_action`, `error_count`;
  no environment values. `.env.example` lists the variable names without values
  (`DASHBOARD_GUARDS`, `DASHBOARD_GUARDS_DRY_RUN`, `DASHBOARD_TELEGRAM_BOT_TOKEN`,
  `DASHBOARD_TELEGRAM_CHAT_ID`). See [`docs/guards.md`](docs/guards.md).
- **Cloud thinking control** (mission ahawr-cloud-thinking-control). `CLAUDE_RUNNER_CLOUD_THINK`
  (default off, `config.py`): when on, each cloud attempt runs with the policy's per-attempt
  mode (first attempt `first[0]`, retry `retry[0]`), each attempt as a fresh CLI process.
  `cloud_think.py` holds the mapping; `claude_cli.py` passes the mode to the child env
  (`CLOUD_THINK_ENV_VARS`). See [`docs/cloud-thinking.md`](docs/cloud-thinking.md).
- **Adaptive split** (mission ahawr-adaptive-split). Three switches in `hermes_config`
  (`adaptive_split`, `plan_no_truncate`, `run_hygiene`), all off:
  - *Plan gate* (`plan_gate.js`): flags wide tasks; a plan of more than `max_tasks` (15) tasks
    is no longer cut silently but cut into 2-5 parts at phase boundaries (a part may reach
    `max_tasks_hard`, 30), each part its own mission; a plan needing more than 5 parts is
    refused.
  - *Runtime split* (`shouldSplit`, `task_split.js`): a task that timed out, had 2 compactions
    or 150+ steps is replaced by 2-4 narrower tasks with letter-suffixed ids (T013A); at most 6
    splits and 40 tasks.
  - *Run hygiene*: a new run cancels the previous run of the same session.
  - Runner: the run summary gains `compactions`, `steps`, `edits`, `files_changed`; cancel,
    interrupt and timeout kill the whole process group; superseded queued runs never start; a
    watchdog ends hung children and orphans (`child_hung`, `orphan`); task-id parsers accept
    letter suffixes.
  - Ops: `data-tables/ops/adaptive_split_patch.py` (idempotent, not applied to the live
    workflow) and `mission_lint.py`. See [`docs/adaptive-split.md`](docs/adaptive-split.md) and
    [`docs/mission-authoring.md`](docs/mission-authoring.md).
- **Agent guide.** `CLAUDE.md` (loaded for every Worker, Reviewer and Architect run) and
  [`docs/naming.md`](docs/naming.md); the plan of the code review and refactoring after 1.0 is
  [`docs/review-refactor-plan.md`](docs/review-refactor-plan.md).

## 2026-10-19 (0.7.400)

Bug-fix release: Kanban drag-and-drop and the queue; the Projects layout; the mission number in the Grid.

- **Fixed: cards could not be dragged in the Kanban** (four causes). (1) The `board.queue` table was empty (the live order lives in `real_queue.sh`), so there was nothing to move: it is filled with the waiting missions in the script order. (2) The drag request, the Lanes task reorder, task edits and the Thinking calls were sent without `X-AHAWR-Dashboard` and got 403; a test now checks that every mutation fetch of the board carries the header. (3) Moving, adding and removing queue rows ended with a duplicate-key error (500): the positions are shifted through negative numbers now. (4) The queue version check compared with a stamp kept in the process and gave a false conflict; it compares with the database now. The screen no longer waits for the slow redraw of the hidden queue list after a move, and the Queued column is drawn in the queue order.
- **Fixed: the mission rows of a project card** were unevenly aligned (status, name, tasks and hours); they sit in columns now, the card has more room.
- **Grid:** a `#` column with the number of the mission (its row in the missions table, `number` in `GET /api/board/summary`), sortable; the alias stays in the ID column.
- Queued mission `ahawr-qa-acceptance` (QA by user-level UI scenarios on a throwaway stack; at the end of a mission and nightly, not after every task); roadmap item added.

## 2026-10-19 (0.7.300)

Bug-fix release: mixed languages on the board; the Grid sorts by id and shows dates.

- **Fixed: the tab names above the board stayed Russian** after English was chosen (`dashboard.html`); they follow the board language now (and switch at once).
- **Fixed: texts in one language only.** The task editor was Russian only; task cards and the task page, Thinking, Readiness and History panels (titles, buttons, table headers, confirmations, errors, task and check statuses) were English only. All use the translation tables now (`L(key, English)` in the modules, the English text stays the fallback). The server text "not enough data, N more attempts per variant needed" is English and shown in the board language; server notes (CLAUDE.md, host check, database unavailable) are English.
- **Fixed: new Russian texts never reached the board.** `GET /api/board/i18n` filled the keys missing in the database only for English; a key added to `board_i18n_ru.json` after the first seeding stayed English in the Russian mode. Every shipped language is filled now, a text edited in the translation editor still wins. A test checks that the English and Russian tables have the same keys.
- **Grid:** the columns ID, Started and Last activity with sorting by a click on the header (a mission without a date goes last). The dates come from the first and the last event of the mission in the event log (one query for the whole board, `mission_times()`), for a mission without events from the creation of its row. Kanban cards show "since <date>". `GET /api/board/summary` carries `started_at`, `last_at` and `created_at`.

## 2026-10-19 (0.7.200)

Bug-fix release: task statuses of the board and the Sprint view.

- **Fixed: accepted tasks were shown as rejected.** `_derive_status` (`board.py`) compared the Reviewer's free text (`review_reason`) with "pass" instead of the verdict `review_status`, so every task the workflow had moved past was "not_accepted" (the summary showed "rejected"). Lanes had all old tasks in the Rejected column, Sprint and the Kanban chips counted "0 out of N". The status is derived from `review_status` now, as in `TaskStatuses.derive`; the test data of `test_board_api.py` carries a real verdict.
- **Fixed: the Sprint view opened an empty mission.** The backlog was sorted alphabetically and the first queued mission (without a plan, "0 out of 0") was selected. Running missions come first now, then the queue in its order; the running mission is selected. A mission that the Architect has not planned yet says so; the thinking share of the KPI tile is filled.

## 2026-10-19 (0.7.100)

Bug-fix release: the launch settings window showed empty fields, and the board read and wrote the wrong hermes_config profile.

- **Fixed: the launch settings fields were empty.** The form showed only the mission's own config, which is empty for most missions; models, providers, attempts per task, max tasks and the thinking policy come from the hermes_config profile. `GET /api/board/missions/{id}` has a `defaults` object now (a whitelist of the profile keys, no chat ids or URLs); an empty field shows the default as grey placeholder text, the summary card shows the effective values.
- **Fixed: `PgBoardStore.hermes_config()` read an arbitrary row.** The table has several profiles (`default`, `test`, `openrouter`, `opencode`, `claude-code`); the read took the first row and `update_hermes_config` ran an `UPDATE` without `WHERE`, which would have changed every profile. Both use the `claude-code` profile now.

## 2026-10-19 (0.7.0)

New function: the launch settings of a mission are edited in a window with steps (mockup `claude-runner/docs/board-mission-edit-mockup.html`), and the n8n data tables no longer bloat.

- **Launch settings window** (`board_edit_win.js`): the form of the mission page is replaced by a summary card and the button "Edit launch settings". The window has four steps (Mission; Execution from "attempts per task" with Worker, Reviewer and Architect as one line "model + provider"; Context; Review with the before/after table and "Save changes"), a progress bar, an "unsaved changes" badge, expand (⤢), Esc/✕ with a discard question, and pauses the auto-refresh while it is open. The form controls are moved into the window, so reading, the dirty check and the save route are the old ones. English and Russian texts.
- **Fixed: the data tables of n8n grew to 170 MB** (limit 200 MB, a warning in n8n). The board kept one Postgres connection without autocommit, so the first read left a transaction open for hours; it pinned the vacuum horizon and "Autonomous Agent Task State" (196 rows) took 154 MB. The connections of `board_pg.py`, `board_ops_store_pg.py` and `board_ops_livestate.py` use autocommit now (writes stay in explicit `transaction()` blocks); the two tables were shrunk with `VACUUM FULL` (154 MB → 0.7 MB).
- **Removed from the repository** (`docs/`): everything that was produced by mission runs, not written as documentation of the stack: the cheaper-retries notes and design, the cold-prefill analysis and part A, the compaction analysis, the prefill profile, the retrieval worklog and its fixture, and the whole `docs/telegram/` folder (findings, reviews, test logs). The documentation that stays: `docs/retrieval/{ARCHITECTURE,EVALUATION,INTEGRATION,ROADMAP}.md`, `telegram-bot.md`, `thinking-control.md` and the roadmap page. The files are in the git history.
- The next step, the rename of `claude-runner` to `ahawr-runner`, moves to 0.8.0.

## 2026-10-19 (0.6.100)

Bug-fix release: the board looks like the agreed mockup (`claude-runner/docs/board-views-mockup.html`) and the mission screen no longer breaks.

- Kanban, Lanes, Sprint and Grid had no styles of the mockup in `board.css` (only Projects had); they are styled now (cards with a state stripe, lane notes by state, sprint columns, KPI tiles, the grid table with task segments), light and dark.
- The mission and task screens replace the queue list and the view body and the whole page scrolls (before, the queue list stayed on top and each panel got a thin slice of the height); going back to a view hides them.
- The mission screen is split into pages with a tab bar and a Back/Next pager (`board_tabs.js`): Launch, Tasks, Thinking, Readiness, History, CLAUDE.md; the launch form is a card with labels on the left; the page is remembered per browser tab.
- The "Runs" links of the board open in the top window: inside the board frame they loaded the dashboard a second time (a doubled side menu).

## 2026-10-19 (0.6.0)

The mission board: four missions in one release (board-ui2, ops-control, board-views, board-views-2), new functions, so the middle number rises.

- **Operations control** (`board_ops_*`): process control and loop protection from the board, schema in `docs/board-ops-schema.sql`, notes in `docs/board-ops.md`.
- **Task cards and plan editing** (board-ui2): task cards with stats and attempts, plan editor, thinking panel, readiness checks and history; notes in `docs/board-part2-notes.md`.
- **Board views** (`docs/board-views.md`): `GET /api/board/summary`, project rules (`GET`/`PUT /api/board/project-rules`), the Projects, Kanban, Lanes, Sprint and Grid views, task ordering by dependencies (`board_order.js` and `board_order.py`), `POST /api/board/missions/{id}/tasks/reorder`, `POST .../tasks/{task}/dependencies`, `GET /api/board/missions/{id}/timeline`, `GET /api/runs?mission=`.
- **Translations**: English and Russian tables (`board_i18n_en.json`, `board_i18n_ru.json`), `GET /api/board/i18n`, a translation editor.
- Run list of the dashboard shows provider, kind, title and tok/s of remote runs (`remote_manager.py`); mission cards of the queue have their own CSS.
- Fixes found while releasing: the static route did not serve names with digits (`board_i18n.js` gave 404 and broke the whole board, now covered by a test); the i18n scripts load before the scripts that use them and `BoardI18n.load` no longer needs `getJSON` to exist; `BUNDLED_EN` lacked 8 keys (`board_i18n.test.js` was red); the summary route no longer reads the runner for every mission before it answers (figures are filled in the background, task lists are cached; a cold call took 100 s, a warm one now takes about 3 s); the board opens on the Projects view and the old flat queue list is hidden behind the views (it requested ~175 URLs per refresh).
- Known open points: the Postgres board connection is not rolled back after an SQL error (InFailedSqlTransaction poisons it until a restart); the Windows host fails 63 tests of files outside this release (POSIX fake CLI and `/d/...` paths), they are meant for Linux; the Playwright e2e of the new views was not run to the end.
- Mission `ahawr-dashboard-board-views-2`: T015 (the Russian report) failed review four times; the operator wrote the report (`data-tables/ops/_reports/board-views-2-report-ru.md`) and closed the mission.

## 2026-10-18 (0.5.510)

Small step (documentation and images, no runtime code changed).

- New logo set in `docs/img`, built by `scripts/make_logo.py`: `banner.svg`, `logo.svg`, `mark.svg`, `favicon.svg` and `flow.svg`. The mark is a ring of six meshed gears around the n8n axis (Missions, Architect, Retrieval, Worker, Reviewer, Result), the legend of the banner has the same colours and abstract descriptions (roles, not models). The "How a mission runs" scheme uses the same colours. The roadmap page has the favicon.
- README "Versioning": the scale of the last number is complete (+100 a bug-fix release, +10 a small change, +1 a tiny one).

## 2026-10-18 (0.5.500)

Fixed bugs in the roadmap and the README figures (documentation only, no runtime code changed).

- Fixed: "Missions in the queue" counted the missions of other projects (JSA, the llama.cpp fork) and the finished ones: 34. `scripts/make_roadmap.py --from-queue` now counts only the missions whose working directory is this repository (7 completed, 14 still in the queue).
- Fixed: the roadmap listed work of other projects (JSA missions, llama.cpp server and benchmark missions, the prefill-kernels failure and the GPU power sweep). The roadmap is the AHAWR roadmap only; those items are removed, the later phase is "Parallel missions and Hermes parity".

## 2026-10-18 (0.5.400)

Documentation step (no runtime code changed): the link to the published roadmap page is in the quick links at the top of `README.md` (before it was only in the "Where we are" block).

## 2026-10-18 (0.5.300)

Documentation step (no runtime code changed).

- The roadmap page is published with GitHub Pages from `main`, folder `/docs`: https://sa1avatus.github.io/n8n-agent-orchestrator/roadmap.html (`docs/index.html` redirects to it, `docs/.nojekyll` keeps the Markdown files in `docs/` as plain files). `README.md` and `ROADMAP.md` link to it; `docs/roadmap.json` has the new `page_url` field.
- The open decisions of the roadmap no longer list the key rotation (an operator note that does not belong on a public page).

## 2026-10-18 (0.5.200)

Documentation step (no runtime code changed; the version number moves by 100 as for a fix release because the middle number is kept for the 0.6.0 board views release).

- `README.md` has a new front page: banner, quick links, the figures table, the mission flow diagram, what is inside, where we are, quick start and the documentation index. The old introduction is kept under "About"; the rest of the README is unchanged. The figures table and the "where we are" block are generated.
- New `ROADMAP.md` and `docs/roadmap.html` (a self-contained page: Graphite-and-Amber, light variant for light systems, timeline with expandable milestones). Both are built from `docs/roadmap.json` by `scripts/make_roadmap.py` (`--check` tells when they are stale, `--from-queue` fills the mission counts from the queue on the operator machine); the page template is `scripts/roadmap_template.html`.
- Images `docs/img/banner.svg` and `docs/img/flow.svg` (no external fonts).
- Rule: every project keeps a `ROADMAP.md`, updated in the same commit as the changelog entry.
- Telegram task notice: "(out of N)" instead of "(from N)" in `AHAWR_v13.json`, `AHAWR_v13_ClaudeCode.json` and the patch scripts that re-apply the wording at every mission start.

## 2026-10-17 (0.5.100)

Bug-fix step (dashboard).

- Fixed: the runs page could not be scrolled to the end of a run. 0.5.0 put the run view into a new `#runview` block inside `#main`; neither was a flex column with `min-height: 0`,
  so the event ledger grew to its full height (3 900 px and more), and the page (`overflow: hidden`) cut it at the window edge. A run looked frozen at its first minutes while it went on
  (seen on `TASK T017`: the last visible row was step 5 of 43). `#main` and `#runview` are flex columns with `min-height: 0` again; the ledger scrolls inside the window.
- Fixed: the polling of one run stopped for good after a drawing error or a request that never answered (the timer was set only after a successful draw). A drawing error is now shown in the
  header and logged, the polling goes on; every `GET` of the page gives up after 25 s.
- Fixed: a `#board` link was read as a run filter (`/api/runs?board=` answered 400) and the board tab was left collapsed to a strip when the page was opened on it; the board tab owns its hash now.
- Added the approved design of the board views as a reference for the next board missions: `claude-runner/docs/board-views-mockup.html`.

## 2026-10-17 (0.5.0)

New functions: the mission board in the dashboard, board statistics over HTTP, a retrieval request that never waits for the index sync.

- **Mission board** ("Миссии" tab of the dashboard, `/static/board.html`; mission `ahawr-dashboard-mission-board`, 30 tasks). Missions of the n8n tables as cards in five groups
  (running, queued, finished, not finished, disabled) with task n of N, attempts, time, tokens, cache share, cost, speed and thinking share; the figures are loaded after the
  first draw, three cards at a time. Queue cards move with drag-and-drop or "выше/ниже" buttons (a running card is not draggable; the server refuses a stale version with 409).
  A mission opens as a form "Конфигурация запуска" (name, objective, rules, acceptance criteria, folder, enabled, attempts 3/5/10, models and providers per role, max tasks,
  Worker thinking policy and `from_task`, retrieval corpora, hints, required files): "Сохранить" shows the diff of the changed fields only, asks to confirm and sends only those
  fields with the expected version; "Сбросить"; "Скопировать из другой миссии". A CLAUDE.md editor with four templates (large file, where the data is, what not to do, GGUF) shows
  whether the text goes to the folder or stays in the config. "Новая миссия" takes another mission or a stored template as the pattern. All values are escaped (`esc()`, `textContent`).
- Board backend `/api/board` (`board_api.py`, `board.py`, `board_pg.py`, `board_plan.py`, `board_ready.py`, `board_stats.py`, `board_think.py`): store interface with an in-memory and a
  Postgres implementation (schema `board`, role `board_operator`; `claude-runner/docs/board-schema.sql`, `board-role.sql`), server-side guards (a task that ran cannot be edited),
  optimistic locking, `board.changelog` with rollback, readiness checks, thinking-policy validation and per-variant statistics, mission statistics linked to runs. The dashboard gets
  the DSN from `DASHBOARD_BOARD_PG_DSN` (docker-compose passes it, `.env` holds it). Documents: `claude-runner/docs/board-*.md`.
- `remote_manager.py`: the dashboard has no runner database, so the board statistics read runs, events and sessions over the runner HTTP API (finished runs are cached;
  a finished mission's figures are kept for 10 minutes).
- Retrieval: `RETRIEVAL_SYNC_WAIT_SECONDS` defaults to 0 (was 15): a request answers at once from the current index and the sync runs in the background (-1 keeps the
  synchronous mode). The usage metric accepts task ids with a letter suffix (`T013A`).
- Mission reports (`docs/*mission-report*`) are no longer in the repository; `docs/mission-reports/` is ignored.
- Checked with Playwright (35 checks on the live dashboard: tabs, groups, statistics, drag-and-drop and buttons, the form, diff and save, 409, CLAUDE.md, templates of a new
  mission, XSS values, 503, mobile width, dark theme). The check found and fixed before the release: the queue read failed for a version method of the Postgres store (400); table
  names with capital letters were not quoted (every Postgres call failed); an empty plan of a mission being planned failed the whole list; the board statistics needed a local
  run manager the dashboard does not have; the cards loaded one by one (about 30 s) and the queue render was replaced by the mission render because the board scripts shared one
  global scope (each file is wrapped now); text fields were number inputs; the diff listed every list and number field as changed and saved lists as text; the CLAUDE.md editor was
  never called and failed on a missing file; the new-mission form crashed and was removed by the redraw; the dashboard rewrote a `#board` link into a run filter and left the
  board pane collapsed to a strip.
- Known gaps (not in the 30 tasks of the mission): the task cards view of a mission (per-task figures and editing), the thinking-policy statistics and layout preview, the
  preflight / backup / rollback screens and the queue-to-`real_queue.sh` apply step are in the API but not in the page yet; the board queue table starts empty.

## 2026-10-15 (0.4.200)

Bug-fix step (retrieval).

- Fixed: the retrieval lexical query took minutes on large corpora. A task text gives 64 terms joined by OR, which matched every chunk of the corpus (78 232 of 78 232), and `ts_rank_cd` scored them all (median 121 s on `routeweaver-test`; 8 terms 3 s, 16 terms 18 s, 32 terms 54 s). The Postgres store now keeps the rarest terms by document frequency within a budget of 10 000 matched chunks (at least 3, at most 16; frequencies cached for ten minutes). New queries on the largest corpus take 0.4-1.7 s in the lexical stage and about 3-4 s end to end.
- Fixed: every request waited up to 15 s for a corpus sync that was already running and never finished while files changed. A request that finds a sync running now waits at most 1 s; only the request that starts a sync waits for it.
- Fixed: the usage metric (`ahawr-retrieval usage`) reported 0 opened files and 0 searches for every profile. The runner API gives a run only a title, so no run was matched to a request; the correlation keys are now taken from the nearest earlier request of the same role (and task id from the title), command words (`grep`, `echo`, `-20;`) are no longer counted as opened files, and `--runner-key` is accepted. Measured 11-15.10: Worker precision 8.6%, recall 16.9%; Reviewer 5.4% and 44%.

## 2026-10-15 (0.4.100)

Bug-fix step.

- Fixed: in the dashboard a click on the role, model, provider, status or source in a run card of the left list created a filter, so merely selecting a run could change the filter by accident. The list no longer makes filters; only the values in the header of the open run do (`dashboard.html`, `renderList`; tests in `test_dashboard.py` updated).

## 2026-10-15

- Local runs get `CLAUDE_CODE_ATTRIBUTION_HEADER=0` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` (`AHAWR_LOCAL_SHARED_PREFIX`, default on): the first request of every task started with a per-task hash (char 47) and a working-directory path (char ~2700), so no two tasks shared a prefix; now 85% of it is the same. Measured by capturing the first request of three tasks.

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
