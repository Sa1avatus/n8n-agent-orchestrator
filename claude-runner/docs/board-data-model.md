# AHAWR Board — Data Model (read-only)

All four n8n data tables live in the `public` schema of the Postgres database
`n8n` (accessed via `data-tables/ops/pgdb.py`, `q()` = SELECT, `x()` = write).
Physical table names use the `data_table_user_*` prefix; n8n UI names are in
parentheses where they differ.

## missions

Physical table: `data_table_user_hYAqO52ZGHXxhhHX`
PK: `id` (integer, identity, sequence `data_table_user_hYAqO52ZGHXxhhHX_id_seq`, max seen: 99)

| Column | Type | Meaning |
|--------|------|---------|
| `id` | integer | Auto-increment row id (PK). |
| `mission_id` | text | Stable mission identifier (slug), e.g. `llama-cpp-dashboard`. |
| `state_namespace` | text | Live-state key for this mission (= the `state_key` in the Live State table), e.g. `llama.cpp-dashboard-v1`. |
| `working_directory` | text | Working folder for the mission, e.g. `D:\OpenAIProjects\llama.cpp`; NULL if none. |
| `version` | double precision | Mission plan revision number. |
| `enabled` | boolean | `t` = mission is enabled (visible to the queue/watcher), `f` = disabled. |
| `name` | string | Human-readable mission name, max 120 chars. |
| `objective` | text | Full mission objective text (up to 4000 chars). |
| `rules_json` | text | JSON array of mission rules (what must NOT be done). |
| `acceptance_criteria_json` | text | JSON array of acceptance criteria. |
| `retrieval_corpora_json` | text | JSON array of retrieval corpora names; NULL if unset. |
| `"createdAt"` | timestamp(3) with time zone | Row creation time. |
| `"updatedAt"` | timestamp(3) with time zone | Last row update time — usable as an optimistic-locking version. |

Example row (id=1, secrets redacted):

```
id=1  mission_id=simple-readonly-review  state_namespace=test
working_directory=NULL  version=1  enabled=false
name=Simple Read-Only Project Review
objective="Проведи безопасный read-only анализ доступного проекта. …"
rules_json=["Не изменять файлы", "Не создавать и не удалять файлы", …]
acceptance_criteria_json=["Определена структура проекта", …]
retrieval_corpora_json=NULL
createdAt=2026-09-11 05:13:36.294+00
updatedAt=2026-09-28 05:36:31.792+00
```

## Live State

Physical table: `data_table_user_Yn9lOhVhQ9gFymNs`
PK: `id` (integer, identity, sequence `data_table_user_Yn9lOhVhQ9gFymNs_id_seq`, max seen: 69)

| Column | Type | Meaning |
|--------|------|---------|
| `id` | integer | Auto-increment row id (PK). |
| `worker_session_id` | text | Current worker runner session id (the Claude Code session, set by Save Worker Run Context from `result.session_id`). |
| `state_key` | text | Mission state namespace (matches `missions.state_namespace`), e.g. `llama.cpp-dashboard-v1`. |
| `plan_json` | text | Full mission plan (JSON): summary, tasks with id/title/objective/acceptance_criteria/verification/scope/dependencies. |
| `task_index` | double precision | 0-based index of the current task in the plan (indexes `tasks[task_index]`). |
| `task_attempt` | double precision | Attempt number for the current task. |
| `status` | text | Current execution status. Values written by the workflow: `active` (Persist Initial Task State, Persist Task Progress, Persist Retry State), `completed` (Persist Task Progress when all tasks done), `failed` (Persist Workflow Failure State). Not `running` or `stopped`. |
| `review_feedback` | text | Latest reviewer feedback text. |
| `last_review_score` | double precision | Latest reviewer score (0–100 scale, as parsed by Parse Review). |
| `last_review_reason` | text | Latest reviewer reason text. |
| `updated_at` | text | ISO-8601 timestamp of the last state update (set by the workflow). |
| `reviewer_session_id` | text | Current reviewer runner session id (set by Save Reviewer Run Context from `result.session_id`). |
| `reviewer_run_id` | text | Reviewer run id (the runner `run_` id, set by Save Reviewer Run Context from `result.run_id`). |
| `reviewer_status` | text | Reviewer run status. |
| `worker_run_id` | text | Worker run id. |
| `worker_status` | text | Worker run status. |
| `architect_run_id` | text | Architect run id. |
| `architect_status` | text | Architect run status. |
| `architect_session_id` | text | Architect session id. |
| `"createdAt"` | timestamp(3) with time zone | Row creation time. |
| `"updatedAt"` | timestamp(3) with time zone | Last row update time — usable as an optimistic-locking version. |

Example row (id=1, plan_json truncated, secrets redacted):

```
id=1  worker_session_id=<worker session id>
state_key=llama.cpp-dashboard-v1
plan_json={"summary":"llama.cpp Windows 11 local LLM infrastructure …","tasks":[…]}
task_index=NULL  task_attempt=NULL  status=NULL
review_feedback=NULL  last_review_score=NULL  last_review_reason=NULL
updated_at=NULL
reviewer_session_id=NULL  reviewer_run_id=NULL  reviewer_status=NULL
worker_run_id=NULL  worker_status=NULL
architect_run_id=NULL  architect_status=NULL  architect_session_id=NULL
createdAt=2026-09-11 …  updatedAt=2026-09-28 …
```

Note: in this snapshot the row has only the first four columns populated;
the remaining columns are NULL. The `_session_id` columns hold the runner
session ids (the Claude Code session, `result.session_id`), while the
`_run_id` columns hold the runner run ids (`run_` + uuid).

## Event log

Physical table: `data_table_user_ceJ7PFzVJvPN0LsV`
PK: `id` (integer, identity, sequence `data_table_user_ceJ7PFzVJvPN0LsV_id_seq`, max seen: 5087)

| Column | Type | Meaning |
|--------|------|---------|
| `id` | integer | Auto-increment row id (PK). |
| `state_key` | text | Mission state namespace (same as Live State `state_key`). |
| `task_id` | text | Task identifier within the plan, e.g. `T003`. |
| `task_index` | double precision | 0-based task index (indexes `tasks[task_index]`). |
| `task_attempt` | double precision | Attempt number. |
| `event_type` | text | One of the five values written by the workflow: `worker_completed`, `reviewer_completed`, `review_parsed`, `worker_terminal`, `reviewer_terminal` (see below). |
| `worker_run_id` | text | Worker run id for this attempt. |
| `worker_status` | text | Worker run status. |
| `worker_output` | text | Worker output text (truncated in dumps). |
| `worker_error` | text | Worker error text, NULL if none. |
| `reviewer_run_id` | text | Reviewer run id. |
| `reviewer_status` | text | Reviewer run status. |
| `reviewer_output` | text | Reviewer output text. |
| `reviewer_error` | text | Reviewer error text, NULL if none. |
| `review_status` | text | Reviewer verdict status: `pass` or `needs_changes` (the `status` field of the verdict JSON parsed by Parse Review). |
| `review_score` | double precision | Reviewer verdict score on a 0–100 scale (the `score` field of the verdict JSON). |
| `review_reason` | text | Reviewer reason text. |
| `review_next_task` | text | Next task id if review passed, NULL otherwise. |
| `created_at` | text | ISO-8601 timestamp of the event. |
| `"createdAt"` | timestamp(3) with time zone | Row creation time. |
| `"updatedAt"` | timestamp(3) with time zone | Last row update time. |

Example row (id=4605, truncated, secrets redacted):

```
id=4605  state_key=ahawr-telegram-control  task_id=T003  task_index=2  task_attempt=2
event_type=review_parsed  worker_run_id=run_49710909c16043ceb684019fdc111d01
worker_status=completed  worker_output="All checks complete. Here is the full updated report. …"
worker_error=NULL  reviewer_run_id=NULL  reviewer_status=NULL
reviewer_output=NULL  reviewer_error=NULL
review_status=NULL  review_score=NULL  review_reason=NULL  review_next_task=NULL
created_at=…  createdAt=…  updatedAt=…
```

## plan_json

The plan is produced by the Architect run and parsed by the **Parse Plan** node of
`AHAWR_v13_ClaudeCode` (workflow `cLdAhawr13Cc9xKp`). The Architect output is a
JSON object:

```json
{
  "summary": "Short mission summary.",
  "tasks": [
    {
      "id": "T001",
      "title": "Task title",
      "objective": "What the task must achieve.",
      "acceptance_criteria": ["criterion 1", "criterion 2"],
      "verification": ["how the result is verified"],
      "scope": ["what the task may touch"],
      "dependencies": ["T000"]
    }
  ]
}
```

Parsing and validation (Parse Plan node, `AHAWR_v13_ClaudeCode.json`):

- `plan.tasks` must be a non-empty array; it is truncated to
  `hermes_config.max_tasks` (profile `claude-code` row id=5: `max_tasks=20`).
- Every task must have `id`, `title`, `objective` and
  `acceptance_criteria` (a JSON array); otherwise the node throws
  `Invalid task definition: ...`.
- `verification`, `scope` and `dependencies` are optional and default to `[]`.
- The parsed state carries `plan_summary`, `tasks`, `task_index: 0`,
  `task_attempt: 1`, `total_tasks`, `review_feedback: ''`.

Storage: **Persist Initial Task State** (and every later state write) stores
`plan_json` as

```json
{"summary": "<plan_summary>", "tasks": [...]}
```

i.e. `JSON.stringify({summary, tasks})` — not the raw Architect output.

Task fields and where the workflow uses them:

| Task field | Used by |
|------------|---------|
| `id` | `current_task_id` in state; `task_id` in every event-log row; `TASK <id>` line of prompts; `Split Task Notice`; `Approved Result` (mission-report detection). |
| `title` | `CURRENT TASK` / `TASK <id>: <title>` prompt lines; Telegram `TASK STARTED` notice; `Approved Result`. |
| `objective` | `OBJECTIVE` prompt line (Worker, Reviewer, notice). |
| `acceptance_criteria` | `ACCEPTANCE CRITERIA` prompt line (Worker, Reviewer, notice); resume text; fresh-retry input. |
| `verification` | `VERIFICATION` prompt line (Worker, Reviewer, notice, retrieval queries). |
| `scope` | `SCOPE` prompt line (Worker, Reviewer, notice, retrieval queries). |
| `dependencies` | **Not used** by any code node of `AHAWR_v13_ClaudeCode` — parsed and stored, but never read. (See "Parameters not applied by the workflow".) |

The per-task `dependencies` array is the only plan field that survives parsing
but is never consumed: execution is strictly linear in `task_index` order.

## Task status

The per-task status is **not stored in any column** of either table. It is
derived from the Live State row plus the Event log rows of that task.

State table (`Autonomous Agent Task State`) values of `status`:

- `active` — written by Persist Initial Task State, Persist Task Progress,
  Persist Retry State. This is the only value the main workflow writes while a
  task is in progress. (In the 2026-10-15 mission-board dump the only
  decompressed Live State row is `llama.cpp-dashboard-v1`, truncated mid
  `plan_json`; its `status` field is not recoverable from that stream. The
  separate `live-state-20261013-1201-control.dump` has a full row for the
  same `state_key` with `status = 'active'`.)
- `completed` — written by Persist Task Progress when
  `execution_route === 'all_tasks_completed'` (Advance Task ran out of tasks).
- `failed` — written by Persist Workflow Failure State (workflow-level
  failure, e.g. the attempt limit was reached, or a run/manager failed).

Event log values of `status` that matter per task:

- `worker_status` / `reviewer_status` — the runner run status strings
  (`completed`, `failed`, `interrupted`, …).

### Derivation algorithm

Let `t` be the task (its `id` is `task_id` in the event log, its 0-based
position is `task_index` in the Live State row and in each event row). The
per-task status is computed in this order:

1. **waiting** — `t.task_index > Live State.task_index` (the task is ahead of
   the current one; it has not been started yet). No event rows exist for it.
2. **running** — `t.task_index == Live State.task_index` and
   Live State `status = 'active'`. Within this, the latest event row for this
   `task_id` refines the sub-state:
   - no `worker_completed` row yet for this attempt → Worker is running;
   - latest `worker_completed` row present → Worker report is in place and the
     Reviewer is running or has finished (pending review).
   - a `worker_terminal` row for this attempt → the Worker run ended in error
     (the attempt is still "running" until it is retried or the limit is hit).
3. **accepted** — `t.task_index < Live State.task_index` (the workflow has
   already advanced past it), **or** Live State `status = 'completed'`, and the
   **latest `review_parsed` row** for this `task_id` has
   `review_status = 'pass'`.
4. **not accepted** — the **latest `review_parsed` row** for this `task_id`
   has `review_status = 'needs_changes'` (the review rejected the report; a
   retry is pending or in progress — the task is still at the same
   `task_index` with a higher `task_attempt`).
5. **not accepted (failure)** — Live State `status = 'failed'` on the current
   task (Persist Workflow Failure State, e.g. the attempt limit was reached).
6. **skipped** — **not implemented.** No node, no flag, no column writes a
   skip. The only "skip" anywhere is a compaction skip inside the runner
   (unrelated to task status). If a task is to be skipped, it would have to be
   added: a new `status` value (e.g. `skipped`) written to the state table and
   a corresponding event row, plus a branch in Advance Task that advances
   without running the Worker.

Note: the Live State `status` column is **not** `running` — that is a derived
per-task state, not a stored column value. The only stored values are
`active`, `completed`, and `failed`.

Columns used by the algorithm: Live State `status`, `task_index`,
`task_attempt`; event log `event_type`, `task_id`, `task_index`,
`task_attempt`, `review_status` (and `review_next_task` as a cross-check that
the pass advanced the workflow).

### Check against real events from the dump

The dump's event table (decompressed from the `COPY` section of
`datatables-20261015-1149-ahawr-dashboard-mission-board.dump`) contains
93 decompressed lines, of which 4 are valid 21-field rows and 89 are
malformed (unescaped tabs produce field counts of 9–97 instead of 21). The
4 valid rows are all for task `T003` (mission `ahawr-telegram-control`):

| id | event_type | task_index | task_attempt | review_status | review_score |
|----|------------|-----------|-------------|---------------|--------------|
| 4605 | `review_parsed` | 2 | 2 | `needs_changes` | 55 |
| 4606 | `worker_completed` | 2 | 3 | `\N` | 0 |
| 4607 | `reviewer_completed` | 2 | 3 | `\N` | 0 |
| 4608 | `review_parsed` | 2 | 3 | `needs_changes` | 40 |

Both `review_parsed` rows have `review_status = 'needs_changes'` (no `pass`
row occurs in this snapshot — the `pass` string appears 18 times in the
decompressed stream but only inside malformed rows, never as a
`review_status` value). The `worker_completed` and `reviewer_completed`
rows have `review_status = '\N'` (NULL) and `review_score = 0`, consistent
with the writer-node column table above.

Row 4605 (task_attempt=2) and row 4608 (task_attempt=3) are both
`needs_changes` at the same `task_index=2`: the workflow was still on T003
when the snapshot was taken, retrying after two failed reviews. This matches
the **not accepted** condition above. No `pass` row appears in the dump, so
the **accepted** condition (a `review_parsed` row with `review_status='pass'`
on an earlier `task_index` than the live one) is not exercised by this
snapshot. The other three event types (`worker_terminal`, `reviewer_terminal`)
do not occur in this snapshot, but they are documented writers of the same
table (see below).

The Live State row for `ahawr-telegram-control` is **not present** in the
mission-board dump. The only decompressed Live State row in that dump is
`llama.cpp-dashboard-v1` (id=1), truncated mid-`plan_json`; its
`status`, `task_index`, and `task_attempt` fields are not recoverable from
the corrupted stream. The separate `live-state-20261013-1201-control.dump`
has a full Live State row for `llama.cpp-dashboard-v1` with
`status = 'active'`, `task_index = 3`, `task_attempt = 1`, but that is a
different mission. No dump in `/d/n8n/data/pg-backups` contains a Live State
row for `ahawr-telegram-control`.

The `skipped` status therefore does not exist in the current workflow or in
any table column; it is a candidate for the "not applied yet" list.

### Event types and writers

The event log is the table **Autonomous Agent Task Attempts** (physical
`data_table_user_ceJ7PFzVJvPN0LsV`; created by the **Ensure Task Attempt
History Table** node). Every row describes one event of one attempt of one
task of one mission. Rows are written by five DataTable nodes; the
`created_at` value is `new Date().toISOString()` at write time.

The `event_type` column carries exactly the five values the workflow writes.
Each value and its writer:

| `event_type` | Writer node | Meaning |
|--------------|-------------|---------|
| `worker_completed` | **Persist Worker Result** | The Worker run of the attempt finished and its report was captured. Carries `worker_run_id`, `worker_status`, `worker_output` and the review the Reviewer just returned (`review_score`, `review_status`, `review_reason`, `review_next_task` copied from the review object). |
| `reviewer_completed` | **Persist Reviewer Result** | The Reviewer run finished. Carries `reviewer_run_id`, `reviewer_status`, `reviewer_output`; `review_score` is written as `0` (the real score lands in the `review_parsed` row). |
| `review_parsed` | **Persist Parsed Review** | The Reviewer output was parsed into the verdict object (`status` pass/`needs_changes`, `score` 0–100, `reason`, `next_task`). Carries `review_status`, `review_score`, `review_reason`, `review_next_task`, `reviewer_output` (raw), `reviewer_run_id`, `reviewer_status`. |
| `worker_terminal` | **Persist Worker Terminal State** | The Worker run ended without a usable report (provider failure / HTTP error). Carries `worker_error`, `worker_run_id`, `worker_status`, `worker_output`. |
| `reviewer_terminal` | **Persist Reviewer Terminal State** | The Reviewer run ended without a usable output. Carries `reviewer_error`, `reviewer_run_id`, `reviewer_status`, `reviewer_output`. |

`task_id` in every row is resolved from `tasks[task_index].id` (fallback
`current_task_id`, then `task_id` on the item), so event rows for different
attempts of the same task share one `task_id`.

Column values by writer (empty = left NULL):

| Column | `worker_completed` | `reviewer_completed` | `review_parsed` | `worker_terminal` | `reviewer_terminal` |
|--------|--------------------|------------------------|------------------|-------------------|----------------------|
| `worker_output` | worker report | previous worker report (from Load Worker Result) | previous worker report | partial worker output | previous worker report |
| `worker_run_id` | this attempt's Worker run id | from Load Worker Result | from Load Worker Result | this attempt's Worker run id | from Load Worker Result |
| `worker_status` | Worker run status | Worker run status | Worker run status | Worker run status | Worker run status |
| `worker_error` | — | — | — | Worker error text | — |
| `reviewer_output` | — | Reviewer run output | raw Reviewer output | — | Reviewer run output |
| `reviewer_run_id` | — | this attempt's Reviewer run id | this attempt's Reviewer run id | — | this attempt's Reviewer run id |
| `reviewer_status` | — | Reviewer run status | Reviewer run status | — | Reviewer run status |
| `reviewer_error` | — | — | — | — | Reviewer error text |
| `review_status` | review `status` | — | review `status` | — | — |
| `review_score` | review `score` | `0` | review `score` | `0` | `0` |
| `review_reason` | review `reason` | — | review `reason` | — | — |
| `review_next_task` | review `next_task` | — | review `next_task` | — | — |

Notes on the values:

- `review_score` is the reviewer's verdict score on a 0–100 scale as parsed by
  **Parse Review**; the example row in the table description above shows a
  NULL because that particular row (a `review_parsed` row whose review object
  was not fully populated in the snapshot) did not carry one. The schema type
  is `double precision`.
- `review_status` is the verdict `status` string (`pass` / `needs_changes`),
  not the run status of the Reviewer.
- `review_next_task` is the `next_task` field of the verdict JSON.
- `worker_status` / `reviewer_status` are the runner run status strings
  (`completed`, `failed`, `interrupted`, …) as reported by the Claude runner.

## Run linkage

Run ids are created by the Claude runner, not by n8n. In
`claude-runner/src/claude_runner/runs.py:214` the runner mints
`run_id = "run_" + uuid4().hex` for every started run and inserts a `runs`
row (`store.create_run`, store.py:125) with that id, the session id, role,
model, provider, cwd and input; events of the run live in
`/data/events/<run_id>.jsonl` (EventLog, runs.py:277-279), not in the runner's
SQLite file. The runner's `sessions` table (store.py:21-30) maps the runner
session id to the Claude Code session id.

n8n links its state rows to those run ids in three places:

1. **Live State / Task State table** (`Autonomous Agent Task State`, physical
   `data_table_user_Yn9lOhVhQ9gFymNs`): columns `worker_run_id`,
   `worker_session_id`, `worker_status`, `reviewer_run_id`,
   `reviewer_session_id`, `reviewer_status`, `architect_run_id`,
   `architect_status`, `architect_session_id` hold the run ids (`run_` + uuid)
   and session ids of the current roles. The main workflow's
   **Save Worker Run Context** and **Save Reviewer Run Context** nodes
   (`AHAWR_v13_ClaudeCode`) extract `result.run_id`, `result.session_id`,
   `result.status` from the runner response; the Run Manager
   (`Claude_Code_Run_Manager_v1`, workflow `cLdRunMgr1AhWr7q`) then persists
   them in its **Build Persist State — Run Context** code node (which also
   clears the stale ids on a `restart_session`/`start_new` route) and writes
   the row with its **Persist Agent State — Run Context** upsert. The main
   workflow's own state upserts (Persist Initial Task State, Persist Task
   Progress, Persist Retry State, Persist Workflow Failure State, Persist
   Reviewer State) write the same columns from the in-workflow state.
2. **Event log** (`Autonomous Agent Task Attempts`): every event row carries
   `worker_run_id` / `reviewer_run_id` of the attempt it describes, so an event
   row can be joined to a runner run by `worker_run_id = runs.run_id`
   (resp. `reviewer_run_id = runs.run_id`) in the runner's store.
3. **Status Bot** (`AHAWR_Status_Bot`): `Shape Live State` reads the Live State
   row and reports `current_run_id = worker_run_id || reviewer_run_id ||
   architect_run_id` for `/status`; the mission's runs are otherwise resolved
   from the runner store by session id (`store.session_run_ids`).

The `runs` schema (store.py:31-59) also carries the thinking-policy fields
(`think_policy`, `think_from`, `think_after`, `think_after_steps`, `think_task`)
that the Workflow sends with every Worker run request; see
"Parameters not applied by the workflow" for which of them are applied and
which are not.

## Parameters not applied by the workflow

Parameters that exist in `hermes_config` (row id=5, profile `claude-code`)
or on the plan/state but are **not** applied per-task by
`AHAWR_v13_ClaudeCode`. The workflow itself is not changed; each row below is
the patch that would apply it.

| Parameter | Current state | What is applied | What is not applied (per-task) | Workflow patch needed |
|-----------|---------------|-----------------|-------------------------------|-----------------------|
| `worker_think_policy` | JSON policy string, lowercased in **Constants** | Applied globally: sent with **every** Worker run request (Build Worker Run Input) and stored on the runner run. Not varied per task. | A per-task variant: the policy could differ by `task_index`. | In Build Worker Run Input, select the policy for `tasks[task_index]` instead of `$('Constants').first().json.worker_think_policy` (e.g. `worker_think_policies` array indexed by `task_index`, or a per-task column on the plan). The reviewer run never receives think fields. |
| `worker_think_policy_from_task` | Number in **Constants** (1) | Applied: the policy starts from task number N (1-based). The runner applies the timeline from that task. Not a per-task value. | A per-task "from" value: each task could start thinking from a different step. | Same patch as above: read `think_from` per task instead of one global value. |
| `worker_think_off_after_seconds` | Number in **Constants** (1800; null/-1 = never) | Applied globally: seconds after which thinking turns off, sent with every Worker run. | A per-task deadline: thinking could be turned off after different seconds per task. | Same patch: read per-task value. |
| `worker_think_off_after_steps` | Number in **Constants** (40; null/-1 = never) | Applied globally: steps after which thinking turns off, sent with every Worker run. | A per-task step count. | Same patch: read per-task value. |
| `task.dependencies` | Plan field, parsed to `[]` by default | Parsed and stored in `plan_json`. | **Not applied at all**: no code node reads `dependencies`; execution is strictly linear in `task_index`. No ordering, no gating, no parallelism. | To apply: in **Prepare Current Task** / **Advance Task**, compute the ready set from `dependencies` (all predecessor tasks accepted) and start only ready tasks; track per-task status in the state table (e.g. a JSON map task_id → status). The event log and the `task_index`/`task_attempt` columns would then need to index the ready set, not the plan order. |
| `service_model` / `service_provider` | In **Constants** | Applied to every run request (worker, reviewer, architect) as `service_model`/`service_provider`. | A per-task service model: scoring/classification could use a different model per task. | In Build Worker/Reviewer/Architect Run Input, select per-task service model instead of the Constants value. |
| `mission.version` | `missions.version` column | Read by the **Mission** node only to pick the mission row; not used in execution. | A per-task version: the plan revision could change per task. | Not applicable as an execution parameter; if a per-task plan revision is wanted, it would be a new column on the plan, not on `missions.version`. |
| `skipped` (task status) | No column, no flag | Not present in the state table or the event log. | A per-task "skipped" status that the operator can set to skip a task without running it. | Add a `skipped` value to the state table `status` column (or a `task_statuses_json` map), a flag the Status Bot can set (like `retry_requested`/`accept_requested`), and a branch in **Advance Task** that advances `task_index` without starting a Worker run when the flag is set. No current node writes it. |

Summary of the "not applied yet" list (parameters the workflow does **not**
read per-task): `worker_think_policy`, `worker_think_policy_from_task`,
`worker_think_off_after_seconds`, `worker_think_off_after_steps`,
`task.dependencies`, `service_model`/`service_provider` (per-task variant),
`mission.version` (per-task variant), and the `skipped` task status (no
column, no flag, no node writes it). All others (model, provider, poll
seconds, max polls, max retries, retry delays, retrieval, working directory,
state namespace, max tasks, max attempts per task) are applied per run/request
as-is.

## hermes_config (row id=5)

Physical table: `data_table_user_1S21PsMqT0FpR7eD`
PK: `id` (integer, identity, sequence `data_table_user_1S21PsMqT0FpR7eD_id_seq`, max seen: 6)

| Column | Type | Meaning |
|--------|------|---------|
| `id` | integer | Auto-increment row id (PK). |
| `profile_id` | text | Config profile name, e.g. `claude-code`. |
| `enabled` | boolean | Whether this profile is active. |
| `architect_provider` | text | Architect LLM provider, e.g. `anthropic`. |
| `architect_model` | text | Architect model, e.g. `opus`. |
| `worker_provider` | text | Worker LLM provider, e.g. `local`. |
| `worker_model` | text | Worker model, e.g. `qwen3.8-27b-gsq-rco-iq2-s-mtp`. |
| `reviewer_provider` | text | Reviewer LLM provider, e.g. `anthropic`. |
| `reviewer_model` | text | Reviewer model, e.g. `sonnet`. |
| `service_provider` | text | Service LLM provider (used for scoring/classification). |
| `service_model` | text | Service LLM model. |
| `max_tasks` | double precision | Max tasks per mission (1–500). |
| `max_attempts_per_task` | double precision | Max attempts per task (1–10). |
| `architect_max_polls` | double precision | Architect max poll cycles. |
| `worker_max_polls` | double precision | Worker max poll cycles. |
| `reviewer_max_polls` | double precision | Reviewer max poll cycles. |
| `architect_poll_seconds` | double precision | Architect poll interval (seconds). |
| `worker_poll_seconds` | double precision | Worker poll interval (seconds). |
| `reviewer_poll_seconds` | double precision | Reviewer poll interval (seconds). |
| `architect_max_retries` | double precision | Architect max retries. |
| `worker_max_retries` | double precision | Worker max retries. |
| `reviewer_max_retries` | double precision | Reviewer max retries. |
| `retry_delays_json` | text | JSON array of retry delays in seconds, e.g. `[15,30,60,120,300]`. |
| `telegram_chat_id` | text | Telegram chat id for notifications (REDACTED in this doc). |
| `retrieval_enabled` | boolean | Whether retrieval is enabled. |
| `retrieval_url` | text | Retrieval service URL, e.g. `http://ahawr-retrieval:8500`. |
| `retrieval_corpora_json` | text | JSON array of retrieval corpora, e.g. `["ahawr-workspace"]`. |
| `retrieval_worker_max_tokens` | double precision | Max tokens for worker retrieval. |
| `retrieval_reviewer_max_tokens` | double precision | Max tokens for reviewer retrieval. |
| `retrieval_timeout_ms` | double precision | Retrieval timeout in ms. |
| `retrieval_label` | text | Retrieval label, e.g. `hybrid-v1`. |
| `runner_url` | text | Claude runner URL, e.g. `http://claude-runner:8700`. |
| `run_manager_workflow_id` | text | n8n workflow id for the run manager. |
| `"createdAt"` | timestamp(3) with time zone | Row creation time. |
| `"updatedAt"` | timestamp(3) with time zone | Last row update time — usable as an optimistic-locking version. |
| `worker_think_policy` | text | JSON thinking policy for the worker (see below). |
| `worker_think_policy_from_task` | double precision | Task index from which the thinking policy applies. |
| `worker_think_off_after_seconds` | double precision | Seconds after which thinking is turned off. |
| `worker_think_off_after_steps` | double precision | Steps after which thinking is turned off. |

### Row id=5 (profile_id = claude-code)

```
id=5  profile_id=claude-code  enabled=true
architect_provider=anthropic  architect_model=opus
worker_provider=local  worker_model=qwen3.8-27b-gsq-rco-iq2-s-mtp
reviewer_provider=anthropic  reviewer_model=sonnet
service_provider=anthropic  service_model=sonnet
max_tasks=20  max_attempts_per_task=3
architect_max_polls=30  worker_max_polls=30  reviewer_max_polls=20
architect_poll_seconds=3  worker_poll_seconds=3  reviewer_poll_seconds=3
architect_max_retries=5  worker_max_retries=3  reviewer_max_retries=5
retry_delays_json=[15,30,60,120,300]
telegram_chat_id=<redacted>
retrieval_enabled=true  retrieval_url=http://ahawr-retrieval:8500
retrieval_corpora_json=["ahawr-workspace"]
retrieval_worker_max_tokens=5000  retrieval_reviewer_max_tokens=60000
retrieval_timeout_ms=60000  retrieval_label=hybrid-v1
runner_url=http://claude-runner:8700
run_manager_workflow_id=6WE2ATGEDzUopBJH
worker_think_policy={"variants":[
    {"name":"plan+pulse","weight":2,
     "first":[["think",8],["off",25],["think",10],["off",null]],
     "retry":[["think",8],["off",25],["think",10],["off",null]]},
    {"name":"control","weight":1,
     "first":[["off",null]],
     "retry":[["off",null]]}
  ],"from_task":1}
worker_think_policy_from_task=1
worker_think_off_after_seconds=1800  worker_think_off_after_steps=40
createdAt=2026-09-27 15:36:40.348+00
updatedAt=2026-10-13 14:37:52.415+00
```

## Optimistic locking

Every table has an `"updatedAt"` column (`timestamp(3) with time zone`,
default `CURRENT_TIMESTAMP(3)`). This column can serve as a version for
optimistic locking: a writer reads the current `updatedAt`, performs the
update, and sets a new `updatedAt`; if the `updatedAt` changed since the
read, the update is a conflict and should be retried.

The `missions` table also has a `version` column (`double precision`) that
tracks plan revision, but it is a business-level version, not a row-level
timestamp — `updatedAt` is the correct optimistic-locking column.

## Appendix — how the data was read

No live SELECT statements were executed: `pgdb.py` requires the
`ahawr-postgres` Docker container, which is not available in this
environment, and no other live Postgres access was attempted.

All schema and row data in this document were read from the pg_dump
backup file

```
data/pg-backups/datatables-20261015-1149-ahawr-dashboard-mission-board.dump
```

specifically: the `CREATE TABLE` statements (one per data table) and the
zlib-compressed `COPY` sections (decompressed to extract the rows).

The following SELECTs are suggested for the operator who does have live
access (via `pgdb.py q()`); none were executed for this document:

```sql
-- Suggested: list all tables in the public schema
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_name;

-- Suggested: distinct event_type values (confirms the five values documented
-- above; not executed — docker unavailable in this environment)
SELECT DISTINCT event_type
FROM "data_table_user_ceJ7PFzVJvPN0LsV"
ORDER BY event_type;

-- Result (verified from the decompressed COPY section of the dump; the
-- event stream decompresses to 93 lines, of which 4 are valid 21-field rows
-- and 89 are malformed due to unescaped tabs in the worker/reviewer output
-- text):
--   review_parsed        (2 valid rows: ids 4605, 4608)
--   reviewer_completed   (1 valid row: id 4607)
--   worker_completed     (1 valid row: id 4606)
-- (worker_terminal and reviewer_terminal do not occur in this snapshot;
--  the 89 malformed lines are not valid event rows and cannot be attributed
--  to any of the five event types)
```

**Warning:** the example values in this document are a snapshot from the
pg_dump file dated **2026-10-15**. They reflect the state of the tables at
that moment and may not match the current live data.

No INSERT, UPDATE, DELETE, CREATE, or ALTER statements were executed.
