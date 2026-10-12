# Control Bot — defect re-verification (T001)

**Workflow file**: `AHAWR_Status_Bot.json` (25 nodes, 22 connection entries)
**Method**: targeted `python3 -c` / heredoc JSON dumps of named nodes only (no workflow edits).
**Verdicts**: Defect 1 **confirmed**, Defect 3 **confirmed**, Defect 4 **confirmed**, Defect 5 **partially confirmed** (parameters are within spec, but executions can overlap and updates can be processed twice).

---

## Workflow structure (full node chain and connections)

Chain: `Poll Telegram → Prepare Poll → Config → Get Updates → Expand Updates → Parse Command → Allowed Chat? → Route Category → Dispatch → {Handle View | Handle Control | Handle Edit | Handle Confirm | Handle Help} → Send Reply`. Branch from `Allowed Chat?` (false) → `Count Ignored`. From `Handle Confirm`: fan-out to **both** `Apply Mutation` and `Send Reply`; `Apply Mutation → Build Audit Row → Write Audit Log`.

Complete connection list (source → target, main branch, index):

```
Poll Telegram  → Prepare Poll (0)
Prepare Poll   → Config (0)
Config         → Get Updates (0)
Get Updates    → Expand Updates (0)
Expand Updates → Parse Command (0)
Parse Command  → Allowed Chat? (0)
Allowed Chat?  → Route Category (0)     Allowed Chat? → Count Ignored (1)
Route Category → Dispatch (0)
Dispatch       → Handle View (0) | Handle Control (1) | Handle Edit (2) | Handle Confirm (3) | Handle Help (4)
Handle View    → Send Reply (0)
Handle Help    → Send Reply (0)
Handle Control → Send Reply (0)
Handle Edit    → Send Reply (0)
Handle Confirm → Apply Mutation (0)  and  Handle Confirm → Send Reply (1)
Apply Mutation → Build Audit Row (0)
Build Audit Row → Write Audit Log (0)
```

Workflow settings: top-level `active: true`, `settings.executionOrder: "v1"` (settings also holds `binaryMode: "separate"` — these are the *only* settings keys; there is no no-overlap / concurrency setting anywhere in the file). Node line numbers in `AHAWR_Status_Bot.json` (line of each node's `"name"` field): Poll Telegram 22, Prepare Poll 35, Get Updates 129, Expand Updates 148, Parse Command 161, Allowed Chat? 195, Route Category 221, Dispatch 415, Handle View 428, Handle Control 441, Handle Edit 454, Handle Confirm 467, Handle Help 480, Apply Mutation 493, Build Audit Row 506, Write Audit Log 550, Send Reply 567, Setup note (sticky) 578. (Evidence sections cite the `jsCode` line of each quoted fragment: Prepare Poll 26, Expand Updates 139, Parse Command 152, Route Category 212, Handle View 419, Handle Control 432, Handle Edit 445, Handle Confirm 458, Apply Mutation 484, Build Audit Row 497.)

---

## Defect 1 — write path: approved mutations/control transitions are never persisted — **CONFIRMED**

Evidence:

- **Apply Mutation** (line 484) is a plain Code node. Its edit branch ends with:
  ```js
  return [{ json: { table: p.table, key: p.key, value: val, profile_id: cfg.profile_id, mission_id: cfg.active_mission_id, updated_at: now } }];
  ```
  and its control branch ends with `return [{ json: out }];` where `out = { table: 'Autonomous Agent Live State', owner: runOwner, updated_at: now, ... }`. It builds only the *intended* state object; it never writes it to any table. Checked: the jsCode contains **no** `$setWorkflowStaticData`, **no** `$getWorkflowStaticData`, no HTTP call, no dataTable write.
- The **only** non-`get` dataTable node in the workflow is **Write Audit Log** (operation `update`, table `AHAWR Bot Audit Log`). There is no write node for `hermes_config`, `missions`, or `Autonomous Agent Live State`.
- The main workflows never touch the Live State table either:
  ```
  AHAWR_v13.json: 'Autonomous Agent Live State' count = 0
  AHAWR_v13_ClaudeCode.json: count = 0
  Hermes_Run_Manager_v5.json: count = 0
  Claude_Code_Run_Manager_v1.json: count = 0
  AHAWR_Status_Bot.json: count = 2   (Load Live State read + Apply Mutation output object)
  ```
  So the `Autonomous Agent Live State` row is never created by anything, `Load Live State` always returns `{}`, and approved control transitions (stop/pause/restart/steer/queue) and edit values are never written anywhere — they vanish with the execution.

---

## Defect 3 — confirmation code is generated but never checked; roles are not enforced — **CONFIRMED**

Evidence:

- **Code is generated** — Handle Control (jsCode line 432):
  ```js
  const code = 'CONF-' + String(chatId).replace(/\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase();
  ```
  stored pending:
  ```js
  data.bot_pending = pending;
  data.bot_pending_ttl_ms = Date.now() + pending.ttl_ms;
  ```
  (same storage in Handle Edit, jsCode line 445, for `/model`, `/undo`, `/mission`, `/set`).
- **Code is never checked** — Handle Confirm (line 458) decides **only** by command or callback data:
  ```js
  if (command === '/approve') { decided = 'approve'; reason = args.join(' '); }
  else if (command === '/deny') { decided = 'deny'; reason = args.join(' '); }
  else if (cb === 'confirm') { decided = 'approve'; }
  else if (cb === 'deny') { decided = 'deny'; }
  ```
  The reply text is never compared against the one-time code; neither `/approve` nor the inline "Approve" button requires it. The message itself says "Reply with the one-time code … or use the buttons", so the code is cosmetic.
- **Roles not enforced** — `Parse Command` (line 152) computes `allowed: isAllowed` (chat in `hermes_config.telegram_chat_id`). Only **Handle View** consults it (line 428):
  ```js
  if (command === '/whoami') { out.push('Access level: ' + (p.allowed ? 'admin (control and edit)' : 'viewer (read-only)')); ... }
  ```
  **Handle Control, Handle Edit, and Handle Confirm contain no `p.allowed` reference at all** (verified: `'p.allowed' in jsCode` → False for all three). Consequence: any allowed chat — including one reported as "viewer (read-only)" — can create a pending command and approve/deny it. The "admin vs viewer" distinction is display-only.
- Unit tests assert only that a code is issued (`tests/n8n-code/run.js` code-issued assertions); they never verify that a reply containing the code is accepted or that a viewer's `/approve` is rejected.

---

## Defect 4 — audit rows are never written (dead chain) — **CONFIRMED**

Evidence:

- **Handle Confirm** (line 458) builds the audit object in its output:
  ```js
  return [{ json: { ..., pending: pending, audit: { who: String(chatId), when: new Date().toISOString(), command: pending.action, before: '—', after: pending.action, result: result } } }];
  ```
  This same item fans out to **both** Apply Mutation and Send Reply.
- **Apply Mutation** (line 484) receives it but its returned object has **no `audit` key** (verified: the string `audit` does not appear anywhere in its jsCode; its returns are `{ table, key, value, ... }` / `{ table, owner, ... }`).
- **Build Audit Row** (line 497):
  ```js
  const a = $json.audit ?? null;
  if (!a) return [];
  ```
  so it always yields `[]`, and **Write Audit Log** (line 550, operation `update`, table `AHAWR Bot Audit Log`) receives no items. The audit table is never written, for approve, deny, or mutation.
- The isolated unit test passes only because the harness feeds the audit object directly into Build Audit Row (test bypasses the real Handle Confirm → Apply Mutation hand-off).

---

## Defect 5 — polling behavior — **PARTIALLY CONFIRMED** (parameters are within spec, but executions can overlap and updates can be processed twice)

Evidence:

- **Poll Telegram** scheduleTrigger (line 10): `"secondsInterval": 30` → 86400/30 = **2880 executions/day** (≤ ~3000, within the required bound).
- **Get Updates** (lines 96–121):
  ```
  url: {{ 'https://api.telegram.org/bot' + $credentials('telegramApi').token + '/getUpdates' }}
  offset: {{ $json.offset }}      (line 102)
  limit: 100                      (line 106)
  timeout: "30"                   (line 110)   ← getUpdates long-poll timeout, within 25–30 s
  allowed_updates: ["message","callback_query"]  (line 114)
  options.timeout: 60000          (line 119)   ← HTTP client timeout, 60 s
  ```
- **callback_query is handled**: `Parse Command` (line 152)
  ```js
  } else if (u.callback_query) {
    chatId = u.callback_query.message?.chat?.id;
    callbackData = u.callback_query.data ?? null;
    command = 'callback_query';
  }
  ```
  and `Route Category` (line 212) maps `command === 'callback_query'` → category `confirm`.
- **Offset is read and written** via static data:
  - Prepare Poll (line 26) reads it: `const offset = Number.isFinite(Number(data.telegram_update_offset)) ? Number(data.telegram_update_offset) : 0;`
  - Expand Updates (line 139) writes it back: `data.telegram_update_offset = maxId + 1;` (only when fresh updates exist).

**Overlap / duplicate-processing analysis** — **verdict: partially confirmed**:

- A single getUpdates call blocks for up to **30 s** (long-poll `timeout`, line 110); the HTTP client itself waits up to **60 s** (`options.timeout: 60000`, line 119).
- The schedule fires **every 30 s** (`secondsInterval: 30`, line 10). A run therefore takes up to 30–60 s, i.e. as long as or longer than the 30 s interval. If n8n does not suppress a new trigger firing while a previous execution is still active, the next run can start **overlapping** the previous one (two getUpdates in flight simultaneously).
- **Duplicate processing is possible under overlap**: both overlapping runs read the offset from the *same* static-data key (`telegram_update_offset`, Prepare Poll line 26) *before* either writes it back (Expand Updates line 139). If run A already fetched updates `N..M` but has not yet written `offset = M+1`, run B (started later) reads the *old* offset and re-fetches the same `N..M`. Both then filter with `u.update_id >= currentOffset` and both keep them, so each update can be handled **twice**. The write-back is not atomic with respect to the next trigger: it is a read-then-write on shared static data with no lock, no generation check, and no "skip if previous run active" guard.
- **No no-overlap setting exists in the workflow**: `settings` contains only `executionOrder: "v1"` and `binaryMode: "separate"`; the full file contains **no** `overlap`, `concurr`, `skipIf`, `maxConcurrent`, `lock`, or `mutex` entry (verified: all counts 0). There is no trigger-level flag to prevent concurrent runs and no guard that suppresses a poll while one is in flight.

**Conclusion**: the parameters (30 s interval, 30 s long-poll, 60 s HTTP timeout) are individually within the documented spec, so the *parameter* defect is **not** confirmed; but because runs can overlap and the offset is a shared read-then-write with no concurrency guard, **overlapping executions and duplicate processing of updates are possible** — hence Defect 5 is **partially confirmed** (not fully, because it is not a parameter error, but a real concurrency hazard).

---

## All dataTable nodes in AHAWR_Status_Bot.json

| # | Node | Operation | Target table | Notes |
|---|---|---|---|---|
| 9 | Load Live State | get | `Autonomous Agent Live State` | filter `state_key` = `{{ $('Config').first().json.state_namespace }}`, limit 1 |
| 10 | Missions | get | `missions` | no filter, limit 1 |
| 11 | Audit Recent | get | `AHAWR Bot Audit Log` | no filter, limit 100 |
| 12 | Load Config Row | get | `hermes_config` | no filter, limit 1 |
| 13 | Load Mission Row | get | `missions` | no filter, limit 1 |
| 22 | Write Audit Log | update | `AHAWR Bot Audit Log` | columns who/when/command/before/after/result; `updateMatchingRows: false` |

No other dataTable nodes exist; the only write node in the whole workflow is Write Audit Log, and it never receives items (Defect 4).

---

## Storage of `bot_pending` and the offset (getWorkflowStaticData)

Both use `$getWorkflowStaticData('global')`:

- **`bot_pending`** (Handle Control / Handle Edit): full pending object `{ action, chat_id, created_at, ttl_ms: 120*1000, kind: 'control'|'edit', args, key?, value?, table? }`; sibling key `bot_pending_ttl_ms = Date.now() + pending.ttl_ms` (2-minute TTL). Handle Confirm clears both (`data.bot_pending = null`) on decide/expiry.
- **`telegram_update_offset`** (Prepare Poll / Expand Updates): `offset = Number.isFinite(Number(data.telegram_update_offset)) ? Number(data.telegram_update_offset) : 0;` and after processing, `data.telegram_update_offset = maxId + 1`.
- Also in static data: `bot_ignored_count` (Parse Command / Count Ignored) for chats not in the allow-list.

---

## Live State row key/column layout (as used by Load Live State and Apply Mutation)

- **Row key**: `state_key`, matched against the Config node value `state_namespace` (currently `"llama.cpp-dashboard-v1"`); `matchType: allConditions`, `limit: 1`.
- **Columns used** by Apply Mutation / Apply Mutation's Live State schema (per `docs/telegram-bot.md` §6):

| Column | Type | Usage |
|---|---|---|
| `state_key` | string (PK) | filter key = Config.state_namespace |
| `workflow_status` | string | set by control verbs (`stopped`, `starting`) |
| `owner` | string | run owner chat ID (set from `p.chat_id`) |
| `current_run_id` | string | carried over on stop/restart |
| `paused` | boolean | set by `/pause [reason\|off]` |
| `pause_reason` | string | reason or `''` when off |
| `steer_note` | string | appended for `/steer` (after existing `operator_feedback`) |
| `operator_feedback` | string | read as prior feedback for `/steer` |
| `last_control` | string | set for resume/retry/accept/start verbs |
| `queue` | array (mission IDs) | `add/list/rm/move/clear` via `/queue` |
| `updated_at` | string (ISO) | set on every mutation |

Because no writer exists for this table (Defect 1), the row is effectively always empty.

---

## Defect 2 — main workflow control gap: control flags are read nowhere in the execution loop — **CONFIRMED**

**Scope**: `AHAWR_v13_ClaudeCode.json` (91 nodes) and `AHAWR_v13.json` (85 nodes). Read-only: `grep -c` per keyword plus targeted `python3` JSON dumps of named nodes only (same name-anchored read pattern as `scripts/patch_task_started_notice.py`, which locates nodes via `find_node(wf, name)` and never edits by id). No workflow file is modified by this task.

**Grep commands** (one per file, over the control vocabulary):

```
for f in AHAWR_v13_ClaudeCode.json AHAWR_v13.json; do echo "=== $f ==="; for w in paused pause_reason steer_note stop workflow_status last_control queue command_queue; do printf '%s: ' "$w"; grep -c "$w" "$f"; done; done
```

Output (verbatim):

```
=== AHAWR_v13_ClaudeCode.json ===
paused: 0
pause_reason: 0
steer_note: 0
stop: 1
workflow_status: 0
last_control: 0
queue: 0
command_queue: 0
=== AHAWR_v13.json ===
paused: 0
pause_reason: 0
steer_note: 0
stop: 1
workflow_status: 0
last_control: 0
queue: 0
command_queue: 0
```

The command exits 1 only because the final `grep -c` in each file has 0 matches; the counts above are the verbatim result. The single `stop` hit in each file is at line 3955 — the resume text inside **Build Worker Run Input** ("Your previous attempt at this task stopped before it finished…"), i.e. prose in the worker prompt, not a control flag. So no node in either main workflow reads `paused`, `pause_reason`, `steer_note`, any `stop` flag, `workflow_status`, `last_control`, or any command queue. The control verbs of the status bot (`/pause`, `/stop`, `/steer`, `/accept`, `/retry`) have no reader side in the execution loop: combined with Defect 1 (the bot's Apply Mutation never persists them), every control decision is lost.

**Anchor nodes and insertion points (name-anchored; node names and line numbers are identical in both files up to line 3964)**

| New behaviour | Anchor node (name, line) | Insertion point |
|---|---|---|
| pause (gate before a new task starts) | `Prepare Current Task` (code, line 153) — selects `tasks[task_index]`, builds `worker_prompt`; reached from `Continue Workflow?`[0] (`run_task`), `Restore Plan After State Save`, `Resume Worker?` | A gate between `Continue Workflow?` and `Prepare Current Task` (new IF node on the `Continue Workflow? → Prepare Current Task` edge) that reads Live State `paused`/`pause_reason` and, when set, holds instead of preparing the task |
| stop (step/attempt completion) | `Worker Completed?` (if, line 260) — condition `$json.worker_route == 'completed'`; and `Reviewer Completed?` (if, line 333) — condition `String($json.reviewer_route ?? $json.route ?? '').toLowerCase() == 'completed'` | A stop check at/after `Evaluate Worker` → `Worker Completed?` and `Evaluate Reviewer` → `Reviewer Completed?` (or a gate before `Persist Worker Result` / `Persist Reviewer Result`, line 1577 / 2017) that reads `stop_requested` and aborts the step |
| steer (next-attempt feedback) | `Retry Current Task` (code, line 406) — builds `review_feedback: reviewNext || reviewReason` for the retry; `Prepare Current Task` (line 153) consumes `const feedback = String($json.review_feedback ?? '')` in the first-attempt prompt | Append `steer_note` to `review_feedback` in `Retry Current Task` (both return branches) and/or in `Prepare Current Task` before `workerPrompt` is built |
| accept (accept and advance) | `Review Passed?` (if, line 380) — pass → `Advance Task` (line 393), false → `Retry Current Task` (line 406); terminal accept via `Approved Result` (code, line 466) | Treat `accept_requested` as a pass: OR it into the `Review Passed?` condition (or a gate on the false branch that routes to `Advance Task` instead of `Retry Current Task`); `Approved Result` is where the accepted final result is emitted |
| retry (verdict retry loop) | `Review Parsed?` (if, line 4031, ClaudeCode only) → `Prepare Verdict Retry` (line 4065) → `Reviewer Verdict Retry Start` → `Verdict Retry Result` (line 4104) → back to `Parse Review` (line 346); the task-retry decision itself is `Retry Current Task` (line 406) + `Persist Retry State` (upsert) + `Restore Retry State` | `Retry Current Task` is the decision point (`currentAttempt` vs `max_attempts_per_task`); an operator `retry_requested` flag would bypass its `failed` branch. In v13 the loop is absent (see below) |

**Differences between the two files**

- Node sets: `AHAWR_v13.json` is a strict subset (85 of 91). Only in `AHAWR_v13_ClaudeCode.json`: `Review Parsed?` (line 4031), `Prepare Verdict Retry` (line 4065), `Reviewer Verdict Retry Start`, `Verdict Retry Result` (line 4104), `Prepare Results Update`, `Update Mission Results`.
- Verdict retry: in v13 `Parse Review` connects directly to `Persist Parsed Review`; in ClaudeCode `Parse Review → Review Parsed?` fans out to `Persist Parsed Review` and `Prepare Verdict Retry` (the re-ask loop, driven by `reviewer_max_provider_retries`). A control-driven `/retry` anchor therefore has no equivalent loop in v13 — the only retry decision node is `Retry Current Task` in both.
- Results update: in ClaudeCode `Approved Result → Prepare Results Update[0] + If5[0]` and `Persist Worker Result → Prepare Results Update[0] + Load Worker Result[0]`; in v13 `Approved Result → If5[0]` and `Persist Worker Result → Load Worker Result[0]` only (no mission-results update chain on the pass/fail paths).
- `Approved Result` jsCode: 833 chars in ClaudeCode (adds `final_report` from `Evaluate Worker`) vs 380 chars in v13 (no `final_report`).
- `Build Worker Run Input`: 7573 chars in ClaudeCode (includes the fresh-session retry switch) vs 4382 chars in v13; the line-3955 comment names the "Claude Code Run Manager" vs "Hermes Run Manager".
- All other anchor names, conditions (`Worker Completed?`, `Reviewer Completed?`, `Continue Workflow?` `run_task`, `Task Completed After State Save?` `all_tasks_completed`, `All Tasks Completed?` `all_tasks_completed`, `Review Passed?` `pass`), node line numbers up to line 3964, and the two state-table column lists are identical.

**Live State columns — existing vs. needed**

Existing columns (per `docs/telegram-bot.md` §6, and as used by `Load Live State` in `AHAWR_Status_Bot.json`):

| Column | Type | Status in the main workflows |
|---|---|---|
| `state_key` | string (PK) | never read (no Live State node in either file) |
| `workflow_status` | string | never read |
| `owner` | string | never read |
| `current_run_id` | string | never read |
| `paused` | boolean | never read — this is the pause gate input |
| `pause_reason` | string | never read |
| `steer_note` | string | never read — this is the steer input |
| `operator_feedback` | string | never read |
| `last_control` | string | never read |
| `queue` | array (mission IDs) | never read — no command-queue reads (`queue`: 0 matches) |
| `updated_at` | string (ISO 8601) | never read |

New columns needed (not present in the documented schema and absent from both files):

| Column | Type | Used by |
|---|---|---|
| `stop_requested` | boolean | stop check at `Worker Completed?` / `Reviewer Completed?` |
| `accept_requested` | boolean | accept gate at `Review Passed?` / `Approved Result` |
| `retry_requested` | boolean | retry decision in `Retry Current Task` |

The control columns the status bot already manages (`paused`, `pause_reason`, `steer_note`, `last_control`, `queue`, `workflow_status`) are documented but have **no reader side in either main workflow** — so even if Defect 1 were fixed, nothing in the loop would act on them. The main workflows also have **no Live State read or write node at all**: the only Live State touchpoints in the whole stack are `Load Live State` (get, filter `state_key = Config.state_namespace`, limit 1) and `Apply Mutation` (builds but never writes) in `AHAWR_Status_Bot.json`. For reference, the tables the main workflows do read/write are `Autonomous Agent Task State` (18 columns: `state_key`, `plan_json`, `task_index`, `task_attempt`, `status`, `review_feedback`, `last_review_score`, `last_review_reason`, `updated_at`, `architect_run_id`, `architect_status`, `architect_session_id`, `reviewer_run_id`, `reviewer_status`, `reviewer_session_id`, `worker_run_id`, `worker_status`, `worker_session_id`; created by `Ensure Task State Table`, line 774) and `Autonomous Agent Task Attempts` (18 columns: `state_key`, `task_id`, `task_index`, `task_attempt`, `event_type`, `worker_run_id`, `worker_status`, `worker_output`, `worker_error`, `reviewer_run_id`, `reviewer_status`, `reviewer_output`, `reviewer_error`, `review_status`, `review_score`, `review_reason`, `review_next_task`, `created_at`; created by `Ensure Task Attempt History Table`, line 1359) — identical in both files, none of them a control column.

**Patch pattern to follow** (`scripts/patch_task_started_notice.py`): name-anchored lookup via `find_node(wf, name)`; idempotency (skip if the target text is already present); pre/post validation of unique node ids/names and that all connections resolve to known names; write back with `json.dumps(wf, indent=2, ensure_ascii=False) + "\n"`; and a check that an untouched anchor node (e.g. `Build Worker Run Input` jsCode) is byte-identical before/after. Any new control-gate node must be added with a unique id/name and its incoming/outgoing connections rewired so that `check_workflow` passes.

---

## Design decisions (T003)

Each of the four questions below settles to exactly one chosen answer.

### Q1 — Start and resume

| Option | Mechanism | Verifiable in this environment? |
|---|---|---|
| **A. Command-queue table + operator-side watcher** | The bot persists an approved start/resume into an `AHAWR Command Queue` table (the Defect-1 write fix makes this possible). An operator-side watcher polls the table read-only (`n8nq.sh`) and, on a row with `status = 'approved'`, runs the documented launch procedure. | **Yes.** The launch path already exists and is runnable as described: `data-tables/ops/launch_mission.sh` re-exports/imports AHAWR v13 with the mission filter set (`n8n export:workflow` → patch `Get Mission` → import), then runs it via `n8n-exec-dt.js` inside the n8n container. No new node, credential, or API surface is needed. |
| **B. Execute Workflow from the bot** | An `n8n-nodes-base.executeWorkflow` node in `AHAWR_Status_Bot.json` runs the main workflow synchronously. | **No.** A mission runs for hours, but the bot's execution is one ~30 s polling cycle — a synchronous call cannot wait it out and would block every other update. None of the bot's 25 nodes is of type `executeWorkflow` (verified), and Defect 1 shows the bot has no persistent write path for a start to hand off. |
| **C. n8n public API** | The bot POSTs to `/api/v1/workflow/{id}/execute` with an n8n API key. | **No.** It needs an n8n public API key and a reachable n8n HTTP endpoint. No such key exists in this stack (the only credential is `telegramApi`), and publishing the API would add a secret surface the design explicitly avoids ("no secrets in the workflow file"). |

**Chosen: Option A.**

What the operator must provide:

1. The mission id (row key of `missions`); the mission row must have a non-empty `state_namespace` — without it the workflow cannot restore saved state (resume degrades to a fresh run).
2. Docker access to the `n8n-autonomous-agents` container (the launch line is `docker exec -d ...`).
3. No AHAWR execution already running — `launch_mission.sh` exits with "… AHAWR execution(s) still running" if one is (it checks `execution_entity` status in `('running','new','waiting')`).

Exact launch line, returned to the user (start and resume are the same line — resume just re-executes the same workflow id and the `Autonomous Agent Task State` table restores progress):

```bash
bash data-tables/ops/launch_mission.sh <mission_id>
```

Under the hood it ends with:

```bash
docker exec -d -e N8N_RUNNERS_BROKER_PORT=5680 n8n-autonomous-agents \
  sh -c "node /tmp/n8n-exec-dt.js 7oMrsFqTiYMy0JJN > /tmp/cli_exec_<mission_id>.log 2>&1"
```

The watcher loop (operator-side, read-only SQL): `select ... from "<AHAWR Command Queue>" where status = 'approved'` via `n8nq.sh`; on a match run the launch line, then update the same row to `status = 'executed'` (or `'failed'`) with `executed_at` and `result`. The row also carries `chat_id`, `role`, and the confirmation `code`, so the audit trail is complete even though the watcher is read-only against n8n.

### Q2 — Polling

**Chosen: `getUpdates timeout` = 25 s (request parameter), schedule interval = 60 s, HTTP client timeout = 30 s.**

- `timeout` request parameter = **25** (within the 25–30 s spec; the current file has `"timeout": "30"`). Passed as a query parameter to `getUpdates`, so it is a long-poll duration.
- HTTP client `options.timeout` = **30000 ms** (down from 60000). Worst-case single-run duration ≈ min(25 s long-poll + overhead, 30 s client timeout) ≈ **30 s**.
- Schedule interval = **60 s** > 30 s worst case → a run can never overlap the next trigger. (Currently interval 30 s ≤ run 30–60 s, which is exactly the Defect-5 overlap: two getUpdates in flight both read the same `telegram_update_offset` before either writes it back.)
- Executions per day: **86400 / 60 = 1440** (≤ the ~3000 bound; the current 30 s schedule gives 86400 / 30 = 2880).
- Additional guard: set a static-data run flag (`data.poll_in_progress = true`) in `Prepare Poll` and clear it at the end of `Expand Updates`; `Prepare Poll` skips the run when the flag is set. With this, even a misconfigured interval cannot produce two getUpdates in flight, and the offset read→write race disappears.

**callback_query handling** (unchanged parse, tightened routing):

- `Parse Command` (line 152) keeps its current extraction: `chatId = u.callback_query.message?.chat?.id`, `callbackData = u.callback_query.data ?? null`, `command = 'callback_query'`.
- `Route Category` maps `command === 'callback_query'` → category `confirm` → `Handle Confirm`.
- In the fixed design, `Handle Confirm` looks up `bot_pending[chatId]` (per-chat storage, Q3): a `callback_data` of `confirm` approves **only if the callback's chat is the pending's owner**; `deny` likewise. Unknown `callback_data` or a callback from a different chat is rejected (no change applied). Callbacks never need the one-time code — they are bound to the owner chat instead.

### Q3 — Confirmation

**Chosen: per-chat pending store keyed by `chat_id`, carrying role, with a checked one-time code, single use, 120 s TTL.**

- **Storage**: workflow static data key `bot_pending` becomes a map keyed by chat id:
  `bot_pending[chat_id] = { action, kind, args, role, code, created_at, ttl_ms }`
  (currently a single global `bot_pending` — two pending actions from two chats collide; the map fixes that). `bot_pending_ttl_ms[chat_id]` likewise per chat.
- **Code format**: `CONF-<digits of chat_id>-<6 random base36 uppercase chars>`, e.g. `CONF-1234567890-A1B2C3` (current generator, line 432, unchanged).
- **TTL**: 120 s (`CONFIRM_TTL_SEC = 120`), checked against `created_at` per pending.
- **Role**: `role = 'admin'` when the chat is in `hermes_config.telegram_chat_id`, else `'viewer'`. Viewers may not create or confirm mutating commands — `Handle Control`, `Handle Edit`, and `Handle Confirm` must reject them (currently `p.allowed` is consulted only in `Handle View`; Defect 3).
- **Single use**: a pending is consumed exactly once — on approve or deny it is cleared; on expiry it is cleared. A wrong code does not consume it (the correct code stays valid until TTL), but it is never applied.
- **Rejection cases**:
  1. No pending for this chat → "No pending change."
  2. Expired (`now > created_at + 120 s`) → "expired", cleared.
  3. Reply code ≠ stored code → rejected, not applied (pending kept until TTL).
  4. `role = 'viewer'` → command rejected (control/edit/confirm all refused).
  5. Callback from a chat other than the pending's owner → rejected.
  6. `/approve` after the pending was already consumed (double confirm) → "No pending change."

### Q4 — Schemas

**1. `AHAWR Bot Audit Log`** (n8n Data Table) — existing 6 columns plus the needed extensions:

| Column | Type | Notes |
|---|---|---|
| `who` | string | Chat id of the operator (already holds the chat id, so no separate `chat_id` column is needed). |
| `when` | string | ISO 8601 timestamp. |
| `command` | string | Action string, e.g. `set max_tasks = 100`, `stop`, `model worker claude-sonnet-4`. |
| `role` | string | `admin` or `viewer`. |
| `target_table` | string | `hermes_config`, `missions`, or `Autonomous Agent Live State`; `—` for view/confirm-only. |
| `row_key` | string | `profile_id`, `mission_id`, or `state_key`; `—` if not applicable. |
| `field` | string | Field changed (`worker_model`, `max_tasks`, …); `—` for control verbs. |
| `before` | string | Previous value, or `—`. |
| `after` | string | New value or action description. |
| `result` | string | `approved` or `denied`. |
| `reason` | string | Free text (`/deny [reason]`, `/accept [reason]`); empty when none. |

**2. `AHAWR Command Queue`** (only needed because Option A was chosen):

| Column | Type | Notes |
|---|---|---|
| `id` | number | PK, auto-increment. |
| `action` | string | `start` or `resume`. |
| `mission_id` | string | Mission to launch. |
| `state_key` | string | Mission's `state_namespace` (restoration key). |
| `chat_id` | string | Operator who requested it. |
| `role` | string | `admin` or `viewer`. |
| `status` | string | `pending` → `approved` → `executed` / `failed`. |
| `code` | string | The one-time confirmation code that approved it. |
| `created_at` | string | ISO 8601. |
| `executed_at` | string | ISO 8601, set by the watcher. |
| `result` | string | Launch outcome / error from `launch_mission.sh`. |

**3. `Autonomous Agent Live State` — new columns** (on top of the existing 11 columns, `state_key` … `updated_at`):

| Column | Type | Used by |
|---|---|---|
| `mission_id` | string | Active mission id; set by the watcher/launch when start/resume fires. |
| `last_started_at` | string (ISO 8601) | When the mission was last started. |
| `stop_requested` | boolean | Stop check at `Worker Completed?` / `Reviewer Completed?` (Defect 2). |
| `accept_requested` | boolean | Accept gate at `Review Passed?` / `Approved Result` (Defect 2). |
| `retry_requested` | boolean | Retry decision in `Retry Current Task` (Defect 2). |

### Q4b — Field whitelist for `/set`, `/model`, `/mission`

The single editable whitelist (Config node `editable_json`, identical to `EDITABLE` in `scripts/patch_status_bot_control.py` and to `docs/telegram-bot.md` lines 101–115 — verified field by field):

| Field | Table | Type | Min | Max |
|---|---|---|---|---|
| `worker_model` | `hermes_config` | string | 1 | 120 |
| `worker_provider` | `hermes_config` | string | 1 | 60 |
| `reviewer_model` | `hermes_config` | string | 1 | 120 |
| `reviewer_provider` | `hermes_config` | string | 1 | 60 |
| `max_tasks` | `hermes_config` | number | 1 | 500 |
| `max_attempts_per_task` | `hermes_config` | number | 1 | 10 |
| `architect_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `worker_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `reviewer_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `name` | `missions` | string | 1 | 120 |
| `objective` | `missions` | string | 1 | 4000 |
| `working_folder` | `missions` | string | 1 | 300 |
| `enabled` | `missions` | boolean | — | — |

Validation: string length in [min, max]; number finite and in [min, max]; boolean `true`/`false` (case-insensitive).

**Matches / differences per command:**

- **`/set`** — uses the table above with full type/range validation. **Matches** `docs/telegram-bot.md` exactly.
- **`/model <role> <model> [--provider name]`** — targets `<role>_model` / `<role>_provider` for `role` ∈ {architect, worker, reviewer, service}. **Difference 1**: the whitelist contains only `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider` — there is no `architect_model` or `service_model`/`service_provider` field, so `/model architect …` / `/model service …` would write keys outside the documented whitelist. **Difference 2**: the `/model` code path builds the pending directly and performs **no** type/range validation (no min/max check), unlike `/set`.
- **`/mission <id> edit <field> …`** — docs claim editing `name`, `working_folder`, `objective` plus `rule add|del|set N` / `criterion add|del|set N`. **Difference**: the code implements only `/mission <id> enable|disable` (`enabled` boolean); `edit <field>` is not implemented — and worse, `/mission <id> edit …` is misparsed as **`disable`** (`args[1] === 'enable' ? 'true' : 'false'`), so a documented edit command silently disables the mission. Rule/criterion list operations are absent from the code entirely.
