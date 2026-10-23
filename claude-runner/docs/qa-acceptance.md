# QA

QA checks a finished mission the way a user would: it opens the dashboard in a
throwaway stack, clicks and types through the user-level scenarios, and gives a verdict. It
is a separate step from the Reviewer, which judges each Worker attempt from the code and the
output. This document is the design; the mission `ahawr-qa-acceptance` (ROADMAP.md) builds it.

QA now has three layers: `regression`, `api` and `ui`. The layers, their verdicts and the
operator setting are described in [qa-layers.md](qa-layers.md); this document covers the `ui`
scenarios.

## Modes

QA has four modes, set by `mode` in the `qa` options of `roles_json` (see `roles-config.md`):

- `off` - QA never runs. This is the default when the feature is not switched on.
- `final` - QA runs once, at the end of a mission, and only if the mission changed UI files or routes. It is the default when QA is on.
- `ui_tasks` - `final`, plus a run after a task whose plan carries an explicit `ui_check` scenario written by the Architect.
- `nightly` - the whole stored scenario suite runs against the current main, independent of missions. It is started by `python -m claude_runner.qa nightly` and is not scheduled.

The validator already accepts `mode`, `max_minutes` and `max_cost_usd` under `qa`; the
meaning of the modes is set here and is not yet wired into the workflow.

The smoke crawl, a scenario-less layer that checks the stack for generic failures, is designed in
[qa-smoke.md](qa-smoke.md). It runs next to the scenarios described here and is off by default.

## Why QA does not run after every task

- Most tasks touch no UI. A QA run after them spends minutes and money for nothing.
- Each QA run costs minutes and money. Per task, that adds up quickly over a mission.
- A UI task often depends on later tasks. Its scenario fails until those tasks are done, which
  gives a false failure.
- Flaky per-task checks train the queue to ignore red. A check that is red for no real reason
  is worse than no check, so QA runs where a failure means something.

## What QA may and may not do

QA may, in the throwaway stack only: read the pages, click, type, and capture screenshots,
console errors and network errors.

QA may not touch: the production dashboard, the production database, n8n, the queue, or any
provider.

## Verdicts

- `pass` - every scenario step ran and matched the expected result.
- `fail` - a step ran and did not match. The verdict names the failing step and carries a
  screenshot of the failure.
- `blocked` - the stack did not start, so no scenario ran. Blocked is never a pass. A blocked
  run must be shown as blocked in the board and must not advance the mission.

## Fail to review to retry

A `fail` becomes a review, so the existing retry chain handles it:

1. The QA verdict is written as a review with `review_status` = `needs_changes`.
2. The failing step (its name and expected/actual text) is written as the review's
   `next_task`, which is the instruction the next attempt receives.
3. `Retry Current Task` picks it up: `combined_feedback = review_next_task || review_reason`,
   `task_attempt += 1`, and the task runs again. The attempt limit (`max_attempts_per_task`,
   default 3) and the blind guard apply as for any Reviewer verdict.

This follows the call chain documented under "Verdict → review_status" and "Retry Current
Task" below. `review_status` is written only by `Parse Review`; QA must not set it another way.

## Limits

The `qa` role in `roles_json` is decided as:

```json
{"qa": {"model": "anthropic/sonnet", "mode": "off", "max_minutes": 20, "max_cost_usd": 1}}
```

`max_minutes` (20) and `max_cost_usd` (1) cap one QA run. A run that exceeds either limit ends
as `blocked`, never `pass`.

## Stored artefacts

Per QA run: the verdict JSON, the screenshots, the console errors and the network errors. Files
are stored in the mission's folder and kept 30 days, then deleted. The verdict JSON path is
recorded in the event log.

## Scoring the planning models on the ui_check set (operator)

`claude-runner/tests/qa_ui_check_cases.json` holds 12 example plan tasks: 6 with `needed: true`
(visible on a page) and 6 with `needed: false` (no visible change). Each case has an `objective`,
the expected `needed` and a one-line `why`. The test only checks the shape and the 6/6 balance.

The test does not call any model, and the scoring is left to the operator. To score a planning
model by hand:

1. Give the model the planning prompt with one case's `objective` as the only task.
2. Read the `ui_check.needed` it returns for that task.
3. Compare it with the case's `needed`, and count the matches out of 12.
4. Record the model name and the score in your own notes; do not commit them.

A model that answers `true` for every case scores 6/12, so it is not better than the set's balance.

## Regression list, 2026-10-19 (0.7.400)

The fixes of 0.7.400 in `CHANGELOG.md` are the regression list QA must keep green on the
board: Kanban drag-and-drop (the empty `board.queue`, the missing `X-AHAWR-Dashboard` header on
mutation fetches, the duplicate-key error on queue moves, the false version conflict), the
Projects card alignment, and the mission `#` column in the Grid.

# Implementation notes: call chain

Research notes for the QA-acceptance mission: how the Architect (planning and final
acceptance) and the Reviewer are called by `data-tables/ops/launch_mission.sh`, how a
review verdict becomes `review_status` (including `needs_changes` and the retry chain),
and the anchor-and-idempotent pattern the patch scripts use.

## 1. What launch_mission.sh does

`data-tables/ops/launch_mission.sh` (whole file, 62 lines) is the operator entry point for a
mission:

1. Guards: aborts if an `AHAWR v13` execution is already running (line 13–14) or if an
   A/B/C test container occupies port 8033 (line 15); for a `local` Worker it additionally
   requires the `llama-cpp-server` container to be healthy (line 16–18).
2. Exports the live `AHAWR_v13` workflow (id `WF=7oMrsFqTiYMy0JJN`, line 20), saves the
   pre-patch copy to `wf_before_<mission>.json` (line 21).
3. Rewrites the mission filter of the `Get Mission` node to the target mission (line
   22–32) and asserts that the "Mission report" language patch is present (line 30).
4. Applies the patch scripts, each documented in the script as idempotent (line 33–39):
   - `notice_split_patch.py` — splits the TASK STARTED Telegram notice into messages of
     ≤ 4096 characters;
   - `telegram_model_patch.py` — models in the Telegram notices;
   - `telegram_mission_patch.py <mission>` — mission name on the first line of the notices;
   - `retry_patch.py` — retries for the Telegram nodes;
   - `blind_guard_patch.py` — a Reviewer that cannot read the evidence ends the task at
     attempt 3 (`failed_reviewer_blind`);
   - `cloud_first_patch.py` — a cloud Worker runs the first attempt only, retries go local
     (per-mission `CLOUD_FIRST_FROM_TASK`: 3 for `ahawr-dashboard-board-views`, 10 for
     `ahawr-dashboard-board-views-2`, otherwise 0 — line 38);
   - `think_config_patch.py` — thinking policy of the local Worker from `hermes_config`.
5. `ensure_live_state_row.py <mission>` (line 41) ensures the mission's Live State row for
   the Telegram control flags.
6. Run Manager sub-workflow (line 42–53): exported to `rm_live.json`, patched with
   `think_config_patch.py`, and only imported back if not "already patched"
   (`js_syntax_check.js` first); failure is not fatal — the runner then uses its `.env`
   defaults.
7. Telegram expression check (`tg_syntax_check.js`, line 54–55), then import of the
   patched workflow (line 56–57) and execution via `n8n-exec-dt.js` (line 59–60).

The workflow itself is never edited by hand at launch time; every change is a patch script
run against the exported JSON, in the fixed order above.

## 2. The call chain inside the workflow

All node names below are nodes of `AHAWR_v13_ClaudeCode.json` (the Claude Code variant of
`AHAWR v13` / "Get Mission"); line numbers refer to the exported JSON file at the time of
writing.

### Architect (planning, once per mission)

- `Get Mission` (line 40) — data table `missions` filtered by `mission_id`; builds
  `original_goal` (mission name, working directory, OBJECTIVE, RULES, ACCEPTANCE
  CRITERIA) and the `working_directory_block` (line 45).
- `Constants` (line 537–551) — reads the enabled `claude-code` row of the `hermes_config`
  data table (`Load Config`, line 478–505) and derives `planner_model`,
  `planner_provider`, `max_tasks`, `max_attempts_per_task`,
  `reviewer_max_provider_retries` (= `hermes_config.reviewer_max_retries`),
  `retry_delays`, runner URL/key, etc.
- `Load Prompts` (line 506–525) — data table `agent_prompts` (`returnAll: true`);
  `Prompt Templates` (line 526–538) picks, per `agent_id` (`architect`, `worker`,
  `reviewer`), the enabled prompt with the highest `version` and returns
  `architect_prompt` / `worker_prompt` / `reviewer_prompt` (`system_prompt` column).
  This is where the role prompts live — the `agent_prompts` data table, not a file; the
  repo ships `agent_prompts.example.csv` (README.md §`agent_prompts`, line 315–333, and
  the example CSV at line 389).
- `Build Architect Run Input` (line 3951–3960) — builds the Run Manager request:
  `role: "architect"`, `input` = `architect_prompt` + `BEGIN MISSION … END MISSION`
  (the mission's `original_goal`) + the working-directory tail; `model` / `provider`
  from `planner_model` / `planner_provider`; `run_id` and `session_id` (empty at first).
- `Architect Start` (line 56–81) — `n8n-nodes-base.executeWorkflow` calling the Claude Code
  Run Manager sub-workflow, id from `hermes_config.run_manager_workflow_id`
  (default `nhjwX1G7FiVTO2Ah`).
- `Evaluate Architect` (line 82–94) — normalizes status/route/output into
  `architect_status`, `architect_route`, `architect_output`, `architect_run_not_found`,
  `transient_error`.
- `Architect Completed?` (line 95–128) — IF `architect_route == "completed"`.
- `Parse Plan` (line 129–141) — extracts the Architect's JSON object, validates
  `tasks[]` (each task: `id`, `title`, `objective`, `acceptance_criteria`; optional
  `verification`, `scope`, `dependencies`), slices to `max_tasks`, initializes
  `task_index: 0`, `task_attempt: 1`, `review_feedback: ''`. The Architect is called once
  at the start of the mission and again whenever a plan must be rebuilt; it never reviews —
  the Reviewer does.

### Worker (per task, per attempt)

- `Prepare Current Task` / resume-state nodes build the task state; `Build Worker Run Input`
  (line 3962–3975) composes `role: "worker"`, `input` = `worker_prompt` + task block
  (`OBJECTIVE`, `ACCEPTANCE CRITERIA`, `VERIFICATION`, `SCOPE`, optional mission criteria
  and retrieval context), plus `PREVIOUS REVIEW FEEDBACK` and, on a retry, the whole
  previous report; `model` / `provider` from `worker_model` / `worker_provider` (with the
  cloud-first switch when patched, see §4); `run_id`, `session_id` (resume), `retry_count`,
  `retry_delays`, `think_policy` fields.
- `Worker Start` (executeWorkflow to the Run Manager, per connections: `Build Worker Run
  Input` → `Worker Start`).
- `Evaluate Worker` → `Worker Completed?` → `Load Worker Result` / `Load Previous Worker
  Report` (data table `Autonomous Agent Task Attempts`, `event_type = worker_completed`,
  line 3990–4032) feed the Reviewer.

### Reviewer (per task, per attempt)

- `Reviewer State` (line 611–610) — carries `current_task_id`, `task_index`,
  `task_attempt`, `reviewer_run_id`, `reviewer_session_id`.
- `Build Reviewer Run Input` (line 3976–3989) — `role: "reviewer"`, `input` =
  `reviewer_prompt` + `ORIGINAL GOAL` + `CURRENT TASK` + `OBJECTIVE` + `ACCEPTANCE
  CRITERIA` (+ optional mission criteria) + `VERIFICATION` + `SCOPE` + optional
  `PREVIOUS REVIEW OF THIS TASK (score …)` block on retries + `WORKER RESULT:` (the
  Worker's report) + retrieval context + tail. `model` / `provider` from
  `reviewer_model` / `reviewer_provider`; `resume_input` continues the existing reviewer
  session.
- `Reviewer Start` (line 280–287; executeWorkflow to the Run Manager) — called from
  `Build Reviewer Run Input` (connections at line 5674–5678).
- `Evaluate Reviewer` (line 288–300) — normalizes to `reviewer_status`,
  `reviewer_route`, `reviewer_output`.
- `Reviewer Completed?` (line 301–334) — IF `reviewer_route == "completed"`.

### Verdict → review_status

- `Parse Review` (line 335–347) — the only node that writes `review_status`. It scans the
  Reviewer output for the first balanced JSON object and keeps the one whose
  `status` is `pass` or `needs_changes`, `score` is 0–100, and which has both `reason`
  and `next_task`. It outputs
  `review_status: String(review.status).toLowerCase()`, `review_score`, `review_reason`,
  `review_next_task`, plus `review_valid`. If nothing strict matches, the lenient
  `lenientReviewObject()` fallback (line 337) recovers the final
  `{"status","score","reason","next_task"}` object when a model left unescaped double
  quotes inside `reason`/`next_task`; still no verdict → `review_valid: false` with
  `review_parse_error`. (Same extraction logic lives in
  `retrieval-service/src/ahawr_retrieval/eval/ahawr_metrics.py::review_status`,
  lines 36–67.)
- `Review Parsed?` (line 4033–4066) — IF `review_valid === true`.
- `Review Passed?` (line 348–391) — IF `review_status == "pass"` OR the
  accept-gate flag `accept_requested` is set. The operator can force acceptance
  without a pass verdict.
- `Advance Task` (line 392–404) — on pass: `task_index += 1`, `task_attempt: 1`,
  `review_feedback: ''`, `execution_route: 'run_task'` (or `all_tasks_completed` when the
  last task is done).
- `Retry Current Task` (line 405–417) — on `needs_changes`:
  `combined_feedback = review_next_task || review_reason` (+ operator steer / remark from
  `Accept Gate Flags`), `task_attempt += 1`, `execution_route: 'run_task'`. The attempt
  limit is `limit = max_attempts_per_task + 1` when `retry_requested` is set, else
  `max_attempts_per_task` (default 3). At the limit: `execution_route: 'failed'`,
  `final_status: 'failed_task_attempt_limit'`. The blind guard (below) intercepts before
  this when the Reviewer cannot read the evidence.

### Verdict retry chain (unreadable verdict)

- `Review Parsed?` false → `Prepare Verdict Retry` (line 4067–4079) — re-asks the
  Reviewer in the same session (`session_id` from `Verdict Retry Result` / `Save Reviewer
  Run Context`) with a prompt demanding JSON only:
  `{"status":"pass" or "needs_changes","score":0-100,"reason":…,"next_task":…}`.
  `attempt = $runIndex` (0 on the first re-ask); `maxRetries =
  hermes_config.reviewer_max_retries` (the `reviewer_max_provider_retries` column); when
  used up it throws with the original parse error.
- `Reviewer Verdict Retry Start` (line 4080–4113; executeWorkflow) → `Verdict Retry
  Result` (line 4114–4131) → back to `Parse Review` (line 5736–5740).
  Chain: `Parse Review` → `Review Parsed?` → (false) `Prepare Verdict Retry` →
  `Reviewer Verdict Retry Start` → `Verdict Retry Result` → `Parse Review` again.

### Accept gate

`Accept Gate Live State` (line 4575) / `Accept Gate Flags` (line 4589) read the
mission's Live State row (`state_key = state_namespace`) for the control flags
(`accept_requested`, `steer_note`, `operator_feedback`, `retry_requested`). `Review
Passed?` and `Retry Current Task` both consume these flags, so a human can force a pass,
append steer text, or allow one extra attempt.

## 3. The anchor-and-idempotent patch pattern

All four patch scripts in `data-tables/ops/` share the same discipline:

1. **Anchor strings**: a patch is a list of exact old-string → new-string pairs. The
   exact old text must occur in the node code — not merely somewhere — and the new text
   is written verbatim.
2. **Exactly-once check**: every replacement asserts
   `code.count(old) == 1` (`f"anchor for {what} not found exactly once"`), so an
   ambiguous or missing anchor aborts instead of corrupting the file.
3. **Marker**: a per-script constant `MARK` (a comment or unique token inserted by the
   patch, e.g. `think_policy`, `cloud_first_switch`, `AHAWR_BLIND_GUARD`) is checked
   before applying; if it is already present the script skips that part and prints
   "already patched". A second run therefore changes nothing.
4. **Dry-run/apply**: none of the four scripts has an explicit `--dry-run` / `--apply`
   CLI flag (unlike e.g. `restore_state_row.py`, `think_bandit.py` in the same folder,
   which print first and write only with `--apply`). They all print what they did
   ("patched: …" / "already patched") and then write the file in place; the operator
   can preview the result by running the script on a copy before the real import
   (that is what `launch_mission.sh` effectively does: patch a copy, run the Telegram
   syntax check, then import — line 54–57).
5. **Safety**: writes go to a temp file and `os.replace` (`think_config_patch.py`
   line 171–173), so a failed write never truncates the target; output formatting
   (indentation, CRLF) is preserved (line 164–170).

### `think_config_patch.py`

- `MARK = "think_policy"` (line 20); `sub(code, old, new, what)` at line 30–32 (the
  exactly-once check).
- Anchors: `CONSTANTS_OLD` (line 35) / `WORKER_OLD` (line 44) / `NORMALIZE_VARIANTS`
  (line 57–62, two shapes: repo vs live state object) / `BODIES` (line 72–76, the three
  Run Manager body nodes ending in `if (wd) …working_directory = …; return body/p;
  })() }}`).
- `patch(w)` (line 121–152): applies Constants, Build Worker Run Input, Normalize
  Request, the three body nodes, then `patch_steps(w)` (line 82–118, second stage for
  `worker_think_off_after_steps`, same pattern with a regex-found assignment anchor at
  line 112–114), then raises `Compact Session` timeout to 1920000 ms (line 148–151).
  Returns the list of patched parts; `main()` (line 155–178) prints "already patched"
  when the list is empty.

### `cloud_first_patch.py`

- `MARK = "cloud_first_switch"` (line 16); `sub` at line 22–24;
  `FROM_TASK = CLOUD_FIRST_FROM_TASK` env (0-based task index, line 17),
  `LOCAL_MODEL = "qwen3.8-27b-gsq-rco-iq2-s-mtp"` (line 19).
- `patch_workflow(w)` (line 27–74): in `Build Worker Run Input` only. If `MARK` is in
  the code it only updates the threshold number with
  `re.sub(r"(Number\(s\.task_index \?\? 0\) >= )\d+", …)` (line 33) — the per-mission
  threshold can change between launches. Otherwise it inserts a block above
  `return { json: {` (anchor at line 48), switching `model`/`provider` (anchors at
  line 49–52) and, for the `attempt == 2` retry, a fresh session with full input
  (anchors at line 53–62, two variants: repo `freshRetry` vs live). Also fixes the
  Telegram notice wording "(from N)" → "(out of N)" (line 66–73, marker-free string
  replace).
- `main()` (line 77–84) writes in place and prints "patched: …" or "already
  patched".

### `blind_guard_patch.py`

- `MARK = "AHAWR_BLIND_GUARD"` (line 8); `BLIND` (line 9–17) is the inserted JS block,
  commented with the marker; `ANCHOR = "if (currentAttempt >= maxAttempts) {"` (line 18),
  with `ANCHOR_LIMIT = "if (currentAttempt >= limit) {"` (line 19) for the
  Telegram-control-patched variant.
- `patch(w)` (line 22–30): finds the `Retry Current Task` node (exactly one, line 23),
  returns `False` if `MARK in js` (already patched), otherwise picks the anchor whose
  `count == 1` (line 28) and inserts `BLIND` right before it. The inserted logic: from
  attempt 3, while below `maxAttempts`, a Reviewer whose `review_score < 70` and whose
  `reviewReason` matches the "cannot read the evidence" regex ends the task with
  `final_status: 'failed_reviewer_blind'` instead of retrying.
- `__main__` (line 33–39): reads `src`, optional `dst` (second argument, same file
  when absent), prints "added" / "already there", writes JSON.

### Other patch scripts called by launch_mission.sh

`notice_split_patch.py`, `telegram_model_patch.py`, `telegram_mission_patch.py`,
`retry_patch.py` follow the same pattern (idempotent, anchor-checked, in-place write);
`ensure_live_state_row.py` only ensures the Live State row exists and is tolerant
(`|| echo "live state row not ensured"`).

## 4. Summary of the values

| Concept | Where | Value |
|---|---|---|
| Architect called | `Build Architect Run Input` → `Architect Start` (executeWorkflow) | `role: "architect"`, prompt = `agent_prompts.architect` (`architect_prompt`), models from `hermes_config.architect_model/provider` |
| Reviewer called | `Build Reviewer Run Input` → `Reviewer Start` (executeWorkflow) | `role: "reviewer"`, prompt = `agent_prompts.reviewer` (`reviewer_prompt`), models from `hermes_config.reviewer_model/provider` |
| `review_status` written | `Parse Review` node | `pass` / `needs_changes` (lower-cased), from the Reviewer's verdict JSON `status` |
| `Review Passed?` | IF node | `review_status == "pass"` OR accept-gate `accept_requested` |
| `Retry Current Task` | IF/JS node | `task_attempt += 1`, `execution_route: 'run_task'`; at limit → `failed_task_attempt_limit` |
| Blind guard (patched in) | `Retry Current Task`, inserted before `if (currentAttempt >= maxAttempts)` | attempt ≥ 3, score < 70, evidence unreadable → `failed_reviewer_blind` |
| Verdict retry chain | `Review Parsed?` → `Prepare Verdict Retry` → `Reviewer Verdict Retry Start` → `Verdict Retry Result` → `Parse Review` | up to `hermes_config.reviewer_max_retries` re-asks in the same session, JSON only |
| Cloud-first (patched in) | `Build Worker Run Input` | cloud provider on the first attempt only; from `CLOUD_FIRST_FROM_TASK` onward (per mission) retries run on the local model (`qwen3.8-27b-gsq-rco-iq2-s-mtp`) with a fresh session at attempt 2 |
| Thinking policy (patched in) | `Constants`, `Build Worker Run Input`, `Normalize Request`, three Run Manager body nodes | `worker_think_policy`, `worker_think_policy_from_task`, `worker_think_off_after_seconds` (+ `worker_think_off_after_steps` second stage) passed to the runner; empty/0/-1 = runner `.env` defaults |

## Implementation notes: UI stack

Research notes for the QA-acceptance mission: the Playwright/e2e setup, the
X-AHAWR-Dashboard header guard, the board queue methods, the readiness checks,
and the two files the throwaway stack needs (the board schema SQL and the
dashboard Dockerfile).

### Playwright / e2e setup

- `claude-runner/tests/e2e/` exists with two files: `test_board_views_e2e.py`
  (Projects, Kanban, Grid views) and `test_board_views2_e2e.py` (Lanes view,
  the mission task table and the double-click paths).
- Playwright is **not declared in any pyproject extra**: it is absent from
  `claude-runner/pyproject.toml` (neither in `dependencies` nor in
  `[project.optional-dependencies]`; the only extra is `dev`). Both e2e modules
  self-skip via `pytest.importorskip("playwright.sync_api", reason="playwright is
  not installed (pip install playwright && playwright install chromium)")`
  when Playwright is not installed, and additionally need a Chromium build
  (`playwright install chromium`). So for the throwaway stack it must be added
  manually (`pip install playwright && playwright install chromium`); it is not a
  declared extra.
- Markers: none. The tests use plain `@pytest.fixture` / `@pytest.fixture(scope="module")`
  and no `@pytest.mark`; they are collected by the ordinary `testpaths = ["tests"]`.
- Fixtures: the static board files are served from `src/claude_runner` through
  `page.route` on the fake origin `http://board.test`; the `/api/board/*` calls
  are answered from `tests/fixtures/board_views_e2e/` (a stateful mock for the
  i18n endpoints). There is no `serve_board.py`; the network is mocked with
  `page.route`. Every non-board.test URL is aborted and recorded, and the tests
  fail if any recorded URL points at the live dashboard port `8701`.

### X-AHAWR-Dashboard header guard

- Function: `board_header_guard(request, call_next)` in
  `claude-runner/src/claude_runner/dashboard.py:111-121`, registered as a global
  middleware via `app.middleware("http")(board_header_guard)` at line 123.
- Rule: it returns a 403 `{"error": "missing X-AHAWR-Dashboard header"}` when
  `request.url.path.startswith("/api/board/")` AND `request.method in
  ("POST", "PUT", "DELETE")` AND `request.headers.get("x-ahawr-dashboard") !=
  "1"`. Read-only (GET) board routes are open and pass through.
- Routes it covers: every board mutation route registered by
  `board_api.add_board_routes` (POST/PUT/DELETE under `/api/board`), e.g.
  `/api/board/missions/create` (POST), `/api/board/missions/{id}` (POST/DELETE),
  `/api/board/queue` (POST), `/api/board/queue/add` (POST),
  `/api/board/queue/{mission_id}` (DELETE), `/api/board/queue/move` (POST),
  `/api/board/queue/set-enabled` (POST), `/api/board/queue/reorder` (POST),
  `/api/board/queue/first` (POST), `/api/board/queue/last` (POST),
  `/api/board/queue/retry-not-completed` (POST), `/api/board/missions/{id}/start`
  (POST), `/api/board/missions/{id}/stop` (POST), `/api/board/missions/{id}/disable`
  (POST), `/api/board/missions/{id}/config` (POST), `/api/board/flags/{key}`
  (POST/DELETE), `/api/board/missions/{id}/claudemd` (PUT),
  `/api/board/changelog/{id}/rollback` (POST), `/api/board/templates/{name}`
  (POST/DELETE), `/api/board/project-rules` (PUT),
  `/api/board/i18n/languages` (POST), `/api/board/i18n/{lang}/{key}` (PUT), and the
  live-state/task mutation routes. GET routes (e.g. `/api/board/queue`,
  `/api/board/missions`, `/api/board/summary`, `/api/board/missions/{id}/readiness`)
  are open and not guarded.
- Note: `cancel_run` (`POST /api/runs/{run_id}/cancel`, `dashboard.py:318`) enforces
  the same header directly in the handler; it is not under `/api/board` so it is
  covered by the per-route check, not by `board_header_guard`.

### Board queue methods and the SQL they run

`PgBoardStore` in `claude-runner/src/claude_runner/board_pg.py`; every write runs
in an explicit transaction, locks the whole queue (`_lock_queue`:
`SELECT position FROM queue ORDER BY position FOR UPDATE`), compares the version
(`_queue_version`), writes the changelog row first, then applies the change.
The table is `board.queue` (PK `position`).

- `set_queue(entries, expected_version)` — `board_pg.py:838` — deletes all rows
  (`DELETE FROM queue`) and re-inserts
  `INSERT INTO queue (position, mission_id, enabled, attempts_per_task,
  retry_failed, note) VALUES (%s, %s, %s, %s, %s, %s)`.
- `add_queue(mission_id, expected_version, position=None, ...)` — `board_pg.py:881`
  — checks `SELECT mission_id FROM queue WHERE mission_id = %s`, shifts the tail
  down by one (`_shift_positions`), then
  `INSERT INTO queue (position, mission_id, enabled, attempts_per_task,
  retry_failed, note) VALUES (%s, %s, %s, %s, %s, %s)`.
- `remove_queue(mission_id, expected_version, confirmed=False)` — `board_pg.py:920`
  — `DELETE FROM queue WHERE mission_id = %s`, then renumbers positions
  contiguously (park as negatives, then `row_number() OVER (ORDER BY -position) - 1`).
- `move_queue(mission_id, to_position, expected_version)` — `board_pg.py:953`
  — parks the row as `position = -1000000`, shifts the affected range
  (`_shift_positions`), then `UPDATE queue SET position = %s WHERE mission_id = %s`.
- Helper `_shift_positions(where, params, delta)` — `board_pg.py:869` —
  `UPDATE queue SET position = -(position + %s) - 1 WHERE {where}` then
  `UPDATE queue SET position = -position - 1 WHERE position < 0 AND position >
  -1000000` (avoids a transient duplicate primary key).

The corresponding API routes (all require the header guard) are in
`board_api.py`: `GET /queue`, `POST /queue`, `POST /queue/add`,
`DELETE /queue/{mission_id}`, `POST /queue/move`, `POST /queue/set-enabled`,
`POST /queue/reorder`, `POST /queue/first`, `POST /queue/last`,
`POST /queue/retry-not-completed`.

### Board readiness check signature

`collect_readiness` in `claude-runner/src/claude_runner/board_ready.py:297`:

```python
async def collect_readiness(
    board: BoardStore,
    mission: Mission,
    settings: Any | None = None,
    host_visible: bool = True,
    preflight_max_age_seconds: float = 3600.0,
) -> dict[str, Any]:
```

It runs each readiness check as its own item (`{"check", "status", "reason"}`)
and returns the summary via `_summarize` (`mission_id`, `status`, `items`,
`errors`, `confirmed_needed`). `start_guard(summary, confirmed=False)` (line 350)
refuses the start when any check is `error` unless `confirmed`.

### Board schema SQL and the dashboard Dockerfile

- Board schema SQL: `claude-runner/docs/board-schema.sql` — creates the `board`
  schema and its tables (`templates`, `queue`, `mission_config`,
  `task_overrides`, `changelog`, `flags`, `preflight`, `project_rules`,
  `i18n_languages`, `i18n_strings`). The operations tables (`commands`,
  `agent`, `settings`, roles) are a separate file, `claude-runner/docs/board-ops-schema.sql`.
- Dashboard Dockerfile: `claude-runner/Dockerfile`. The dashboard image is built
  from this file by the `ahawr-dashboard` service in `docker-compose.yml`.

### Implementation notes: role inventory

`reviewer_model` / `reviewer_provider` (and `reviewer_max_polls` /
`reviewer_max_retries`) are known in the following places.
Mark: **change** = inside the mission's allowed change list (RULE 1);
**outside scope, list in report** = not in the allowed list.
One row per distinct file found by `grep -rln "reviewer_model\|reviewer_provider"`.
Places named by the task that are **not** counted in that 123 are listed in the
"named places" rows that follow the 123 grep rows.

| file | function / location | what it does | mark |
|------|--------------------|--------------|------|
| `claude-runner/src/claude_runner/board_api.py` | `_DEFAULT_KEYS` (line 315), `_config_defaults` (line 321) | `_DEFAULT_KEYS` lists hermes_config keys shown as defaults on the board, including `reviewer_provider`, `reviewer_model`; `_config_defaults` reads the hermes_config row and returns default values for those keys | change |
| `claude-runner/src/claude_runner/board_pg.py` | `update_hermes_config` (line 778), `hermes_config` reader (line 822), `known_hermes_cols` (line 797–811) | `update_hermes_config` updates the hermes_config row; `known_hermes_cols` includes `reviewer_model`, `reviewer_provider`, `reviewer_max_polls`, `reviewer_max_retries`; the `hermes_config()` reader returns the current row values including the reviewer fields | change |
| `claude-runner/src/claude_runner/board_plan.py` | `MIDRUN_CONFIG_FIELDS` (line 75) | Config fields a running mission may change mid-run; includes `reviewer_model`, `reviewer_provider`, `reviewer_max_polls`, `reviewer_max_retries` | change |
| `claude-runner/src/claude_runner/board_mission.js` | `MIDRUN_CONFIG_FIELDS` (line 51), `FORM_ROWS` (line 87–88) | JS mirror of board_plan.py's MIDRUN_CONFIG_FIELDS; mission form rows for `reviewer_model` and `reviewer_provider` | change |
| `claude-runner/src/claude_runner/board_edit_win.js` | `PAGES` (line 25–26), `ROLES` (line 31), summary card (line 88) | Launch settings window page 2 "exec" includes `reviewer_model`, `reviewer_provider`; role list includes them; summary card displays reviewer model/provider | change |
| `claude-runner/src/claude_runner/board_i18n.js` | `form.field.reviewer_model` (line 347), `form.field.reviewer_provider` (line 348) | English i18n labels "Reviewer: model" / "Reviewer: provider" | change |
| `claude-runner/src/claude_runner/board_i18n_en.json` | `form.field.reviewer_model` / `form.field.reviewer_provider` (line 37) | English i18n labels "Reviewer: model" / "Reviewer: provider" | change |
| `claude-runner/src/claude_runner/board_i18n_ru.json` | `form.field.reviewer_model` / `form.field.reviewer_provider` (line 37) | Russian i18n labels "Reviewer: модель" / "Reviewer: провайдер" | change |
| `claude-runner/docs/board-data-model.md` | hermes_config column table (line 441–442, 449, 455), example values (line 480, 483, 485) | Documents `reviewer_provider`, `reviewer_model`, `reviewer_max_polls`, `reviewer_max_retries` columns and example values | outside scope, list in report |
| `claude-runner/docs/board-design.md` | example config (line 257) | Example `reviewer_model='sonnet'` in board design doc | outside scope, list in report |
| `claude-runner/docs/board-ops-notes.md` | field list (line 428) | Lists `reviewer_model`, `reviewer_provider` among config fields | outside scope, list in report |
| `claude-runner/docs/board-ops.md` | `model_pairs` column (line 111), `switch_model_pair` (line 282) | Documents `model_pairs` containing reviewer fields and parameterized update | outside scope, list in report |
| `claude-runner/docs/qa-acceptance.md` | Reviewer call chain (line 58, 109, 153, 252, 257) | Documents reviewer retry chain, `reviewer_max_provider_retries`, model/provider sourcing | outside scope, list in report |
| `claude-runner/README.md` | `hermes_config` section (line 284–313, 1165–1192) | Documents reviewer fields in hermes_config | outside scope, list in report |
| `claude-runner/tests/board_mission.test.js` | fixture (line 42, 58) | Test fixture with `reviewer_model`, `reviewer_provider`; field list in assertions | outside scope, list in report |
| `claude-runner/tests/test_board_api.py` | fixture (line 117–118, 152–153) | Test fixture and assertion loop for reviewer fields | outside scope, list in report |
| `data-tables/ops/_edit_queue_cloud.py` | SQL update (line 25, 28) | Patches SQL to set `reviewer_model='sonnet'`, `reviewer_provider='anthropic'` | outside scope, list in report |
| `data-tables/ops/_make_qa_mission.py` | instruction text (line 25) | Documents that reviewer_model/reviewer_provider must be found via grep | outside scope, list in report |
| `data-tables/ops/_wf_tg_test.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/_wf_tg_test2.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/bot_new_20261013.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` schema definitions | outside scope, list in report |
| `data-tables/ops/bot_repo_20261014.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` schema definitions | outside scope, list in report |
| `data-tables/ops/m3_before.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/m3_after.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/main_flags_import.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/main_flags_try.json` | workflow JSON (6 matches) | Contains `reviewer_model`/`reviewer_provider` in workflow node code (reviewer-completed check, reviewer state fields, Parse Reviewer jsCode, Load Prompts) | outside scope, list in report |
| `data-tables/ops/main_live2_before_flags.json` | workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/patch_verdict_retry.py` | JS code (line 55–56, 181) | Verdict retry workflow: reads `cfg.reviewer_model`, `cfg.reviewer_provider`, `cfg.reviewer_max_polls` | outside scope, list in report |
| `data-tables/ops/real_queue.log` | log output | Contains reviewer model/provider in log lines | outside scope, list in report |
| `data-tables/ops/real_queue.out` | log output | Contains reviewer model/provider in output lines | outside scope, list in report |
| `data-tables/ops/real_queue.sh` | shell script (line 133, 137) | SQL update and select with `reviewer_model`, `reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261009` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261011-bot` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261011-models` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261011-q4` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261011-think` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261012-pg` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261013-prefill` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261013-retry` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue.sh.bak-20261014-t013` | shell script backup | Backup of real_queue.sh with `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/real_queue_rerun.sh` | shell script | Contains `reviewer_model`/`reviewer_provider` | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/probe19.js` | rescue script (line 21–22) | Probe code with `reviewer_model`/`reviewer_provider` schema | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/probe20.js` | rescue script (line 18–19) | Probe code with `reviewer_model`/`reviewer_provider` schema | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/probe22.js` | rescue script (line 14–15) | Probe code with `reviewer_model`/`reviewer_provider` schema | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/probe23.js` | rescue script (line 18–19) | Probe code with `reviewer_model`/`reviewer_provider` schema | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/probe24.js` | rescue script (line 18–19) | Probe code with `reviewer_model`/`reviewer_provider` schema | outside scope, list in report |
| `data-tables/ops/rescue/claude-runner-tmp-20261011/run_debug.js` | rescue script (line 38–39, 80) | Rescue script with `reviewer_model`/`reviewer_provider` schema and CONFIG_ROW | outside scope, list in report |
| `data-tables/ops/set_reviewer_sonnet.sh` | shell script (line 18, 20) | SQL update setting `reviewer_model='sonnet'`, `reviewer_provider='anthropic'` | outside scope, list in report |
| `data-tables/ops/telegram_model_patch.py` | Python script (line 44) | Telegram bot patch: displays `reviewer_model`/`reviewer_provider` in status line | outside scope, list in report |
| `data-tables/ops/think_steps_archive.jsonl` | JSONL archive | Contains `reviewer_model`/`reviewer_provider` in archived step data | outside scope, list in report |
| `data-tables/ops/wf_after_verdict_retry.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-cloud-thinking-control.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-board-ui2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-board-views-2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-board-views.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-click-filters.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-mission-board.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-ops-control.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-dashboard-theme.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-guards-in-dashboard.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-qa-acceptance.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-rag-effectiveness.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-retry-and-microcompact.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-telegram-bot-completion.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_ahawr-telegram-control.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-haiku-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-haiku-r3.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-haiku-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-haiku.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local-gpu96-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local-iq3-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local-iq3-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local-iq3.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-local.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-opus-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-opus-r3.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-opus-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-opus.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-sonnet-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-sonnet-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-api-sonnet.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-haiku-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-haiku-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-haiku.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local-gpu96-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local-iq3-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local-iq3-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local-iq3.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-local.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-opus-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-opus.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-sonnet-r2.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-sonnet-t4k.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_bench-rag-web-sonnet.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_jsa-discover-llm-http-client.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-fa-longctx-decode.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-gdn-transactional-prompt-cache.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-prefill-cost.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-prefill-kernels.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-speed-tuning.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-upstream-port-repair.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_llamacpp-upstream-port.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_rag-api-missing-auth-401.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_rag-web-admin-auth-errors.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_results_deploy.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_results_hook.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_review_message.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_task_message.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_before_verdict_retry.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_fixed_hook.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_fixed_hook_import.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_fixed_wait.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_fixed_wait_import.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_live_now.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_live_patched.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_next.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_review_message.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_review_message_live.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_task_message.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_task_message_live.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `data-tables/ops/wf_verdict_retry.json` | n8n workflow JSON | Contains `reviewer_model`/`reviewer_provider` in workflow node code | outside scope, list in report |
| `claude-runner/src/claude_runner/board_ready.py` | `REVIEWER_KEYS` (line 51), `collect_readiness` (line 297) | Readiness check: reads `reviewer_session_id`, `reviewer_run_id`, `reviewer_status`; collects readiness state for the reviewer role | change |
| `claude-runner/src/claude_runner/board_stats.py` | `reviewer_run_id` readers (lines 126, 141, 151, 232, 404, 465), `reviewer_minutes` | Per-role stats: reads `reviewer_run_id` to attribute runs, `reviewer_minutes` for QA label | change |
| `claude-runner/src/claude_runner/config.py` | `ROLES` (line 17), reviewer `RoleProfile` (line 326) | Role validation: `ROLES` includes "reviewer"; `RoleProfile` dataclass for reviewer role validation | change |
| `claude-runner/src/claude_runner/mission_stats.py` | `ROLES` (line 15), `reviewer_minutes` (line 93), `stats_for_runs` (line 63) | Final report: `ROLES` includes "reviewer"; `reviewer_minutes` column in mission_results; `stats_for_runs` aggregates per-role | change |
| `claude-runner/src/claude_runner/titles.py` | role-to-title mapping (lines 27–28) | Maps "reviewer" head to role title for display | outside scope, list in report — title mapping is cosmetic, not in allowed change list |
| `claude-runner/src/claude_runner/board_ops_actions.py` | model+provider switch text (lines 97, 220) | Ops action: switches worker/reviewer model+provider in action text | outside scope, list in report — read-only ops UI, not in allowed change list |
| `claude-runner/src/claude_runner/board_ops_settings.py` | `model_pairs` id/worker/reviewer/architect (lines 98, 125) | Ops settings: lists model pairs with reviewer field for configuration display | outside scope, list in report — settings UI display only, not in allowed change list |
| `claude-runner/src/claude_runner/board_queue.js` | `fmtTime` (lines 61–63), reviewer chip (line 95) | Queue card: formats worker_hours+reviewer_minutes; displays reviewer chip | change |
| `claude-runner/src/claude_runner/board_task.js` | score/reason (lines 104–105) | Task card: displays reviewer score and reason | change |
| `claude-runner/src/claude_runner/board_tasks.js` | `fmtTime` (lines 56–58, 82) | Tasks card: formats reviewer_minutes in card display | change |
| `claude-runner/src/claude_runner/board_view_lanes.js` | `reviewer_minutes` (line 120) | View lanes: displays reviewer_minutes in lane view | change |
| `claude-runner/src/claude_runner/dashboard.html` | `.role.reviewer` CSS (line 101), role check (line 851) | Dashboard HTML: CSS class for reviewer role, role check in JS | outside scope, list in report — CSS/JS in dashboard.html is not in allowed change list |
| `data-tables/ops/telegram_model_patch.py` | `reviewer_model`/`reviewer_provider` display (line 44) | Telegram bot status/cost: displays reviewer model/provider in status line. Searched `grep -rln "telegram" claude-runner/src/claude_runner` — no `reviewer_model`/`reviewer_provider` reference in Telegram bot source | outside scope, list in report |
| `claude-runner/src/claude_runner/board_ops_api.py` | `GET /watchers` (lines 225–226) | Queue watcher: API endpoint for queue watch state. Searched `grep -rln "watcher" claude-runner/src/claude_runner` — no `reviewer_model`/`reviewer_provider` reference | outside scope, list in report |
| `claude-runner/src/claude_runner/board_ops_parsers.py` | `parse_queue_watch_state` (line 189) | Queue watcher: parses queue watch state for UI. No `reviewer_model`/`reviewer_provider` reference | outside scope, list in report |
| `claude-runner/src/claude_runner/board_dnd.js` | "stall" comments (lines 11, 161) | Stalled-run guard: drag-and-drop logic with stall-related comments. No `reviewer_model`/`reviewer_provider` reference. Searched `grep -rln "stall" claude-runner/src/claude_runner` | outside scope, list in report |
| `claude-runner/src/claude_runner/config.py` | "stall" comment (line 543) | Stalled-run guard: comment in config about stall behavior. No `reviewer_model`/`reviewer_provider` reference | outside scope, list in report |
| `claude-runner/src/claude_runner/events.py` | `feed` (lines 163, 235) | Event log writer: writes reviewer events to the feed log. No `reviewer_model`/`reviewer_provider` reference. Searched `grep -rln "event" claude-runner/src/claude_runner` | outside scope, list in report |

Total distinct files: **123** (8 in `claude-runner/src/claude_runner`, 2 in `claude-runner/tests`, 6 in `claude-runner/docs` + README, 107 in `data-tables/ops` — 33 non-wf + 74 `wf_*.json`). The 12 named-place src files above are **not** in that 123 because none references `reviewer_model`/`reviewer_provider`.
