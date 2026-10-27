# Review calibration

Research for the Reviewer/Architect calibration work. Facts are taken from the workflow export `AHAWR_v13_ClaudeCode.json` (grep only, read-only), `data-tables/ops/*_patch.py`, `README.md` ("Versioning") and `CHANGELOG.md`. The export is the file in the repository root; the live workflow is exported at launch and may differ.

## Current flow

### Nodes (exact n8n names)

| Step | Node | Line in export | What it does |
|---|---|---|---|
| Constants | `Constants` | 541 | Reads the `hermes_config` row; builds `retry_delays`, `max_attempts_per_task` and the thinking-policy fields. |
| Architect input | `Build Architect Run Input` | 3960 (code 3951) | Builds the Architect run input; the planning prompt is `$('Prompt Templates').first().json.architect_prompt`. |
| Reviewer input (prompt) | `Build Reviewer Run Input` | 3988 (code 3979) | Builds the Reviewer run input; the Reviewer prompt is `$('Prompt Templates').first().json.reviewer_prompt`, sent with `session_id` from `Reviewer State`. |
| Worker input | `Build Worker Run Input` | 3974 (code 3965) | Builds the Worker run input; passes `working_directory` from `Mission`. |
| Plan parsing (first) | `Parse Plan` | 131 (node name line 140) | Parses the Architect output into `plan`, `tasks`, `task_index: 0`, `task_attempt: 1`; throws `Invalid task definition` for a missing field. |
| Plan parsing (resume) | `Restore Saved Task State` | 851 | Re-parses the stored `plan_json` (three attempts: direct, unescaped quotes, JSON string) and restores the state for resume. |
| Review parsing | `Parse Review` | 337 | Reads the first JSON object of the Reviewer output and sets `review_status` (lower-cased) and `review_score`. Accepts only `pass` or `needs_changes`. Contains no numeric threshold. |
| Acceptance decision | `Review Passed?` | 361 (IF node) | Compares `review_status` with `"pass"`. This is the only acceptance condition. |
| Review storage | `Persist Parsed Review` | 2272 | Writes `review_status` (from `review.status`) to the task-state row. |
| Attempt counter and retry | `Retry Current Task` | 407 | `currentAttempt = Math.max(Number(state.task_attempt ?? 1), loggedAttempt)`; `maxAttempts` comes from `Constants.max_attempts_per_task` (default 3); stops the task at `currentAttempt >= limit`; otherwise sets `task_attempt: currentAttempt + 1`. |
| Reviewer verdict retry | `Reviewer Verdict Retry Start` | 4077 | Asks the Reviewer again in the same session when no verdict could be read. |

Other nodes that matter: `Reviewer Completed?` (name line 333, the branch before `Parse Review`), `Advance Task` (name line 403; sets the next `task_index` with `task_attempt: 1`), `Persist Initial Task State` (line 1028), `Persist Retry State` (line 2912; not traced in detail).

### Anchors to patch

Each anchor below was checked with `grep -o -F -- "<anchor>" AHAWR_v13_ClaudeCode.json | wc -l`. Every one occurs exactly once.

| Hook point | Node | Anchor (verbatim) | Occurrences |
|---|---|---|---|
| Review parsing (pass status) | `Parse Review` | `return ['pass','needs_changes'].includes(status)` | 1 |
| Review stored | `Parse Review` | `review_status: String(review.status ?? '').toLowerCase()` | 1 |
| Acceptance condition | `Review Passed?` | `"rightValue": "pass"` | 1 |
| Constants (new flag goes after this line) | `Constants` | `  retry_delays: retryDelays,` | 1 |
| Architect planning prompt | `Build Architect Run Input` | `$('Prompt Templates').first().json.architect_prompt ?? '',` | 1 |
| Reviewer run input | `Build Reviewer Run Input` | `  session_id: (String($('Reviewer State').first().json.reviewer_session_id ?? ''))` | 1 |
| Worker run input | `Build Worker Run Input` | `  working_directory: ($('Mission').first().json.working_directory),` | 1 |
| Attempt counter (max) | `Retry Current Task` | `const maxAttempts = Number($('Constants').first().json.max_attempts_per_task ?? 3);` | 1 |
| Attempt counter (current) | `Retry Current Task` | `const currentAttempt = Math.max(Number(state.task_attempt ?? 1), loggedAttempt);` | 1 |
| Retry limit | `Retry Current Task` | `if (currentAttempt >= limit) {` | 1 |
| Plan validation | `Parse Plan` | `Invalid task definition` | 1 |

Stale anchors from `claude-runner/docs/report-pack.md` that no longer match this export:

- `mission_roles_json: String(mission.roles_json ?? '').trim()` returns 0 occurrences. The `Constants` node has changed; use `  retry_delays: retryDelays,` above.
- `"rightValue":"pass"` (no space after the colon) returns 0. The export uses `"rightValue": "pass"`.

### plan_json

The Architect output is `{"summary": "...", "tasks": [...]}`. `Persist Initial Task State` and every later state write store `plan_json` as `JSON.stringify({summary, tasks})`, not the raw Architect output.

Fields that the parser requires (`Parse Plan`, line 131): `plan.tasks` must be a non-empty array; each task needs `id`, `title`, `objective` and `acceptance_criteria`. `verification`, `scope` and `dependencies` are optional. `dependencies` is parsed but never read by any node (`claude-runner/docs/board-data-model.md`).

The parsed state carries `plan_summary`, `tasks`, `task_index`, `task_attempt`, `total_tasks`, `review_feedback`, `last_review_score`, `last_review_reason`, `reviewer_run_id`, `reviewer_status`, `reviewer_session_id`, `worker_run_id`, `worker_status`, `worker_session_id`, `architect_run_id`, `architect_status`, `architect_session_id`, `execution_route` (`run_task` or `all_tasks_completed`).

### Where review_status and the attempt count come from

- `review_status` is produced in `Parse Review` (line 337) from the Reviewer's JSON `status`, stored by `Persist Parsed Review` (line 2272), and read by `Review Passed?` (line 361).
- The attempt count is the state row's `task_attempt`, merged with `loggedAttempt`, the highest attempt logged in the `Autonomous Agent Task Attempts` data table (`Retry Current Task`, line 407). The new value is written by the retry path (`Persist Retry State`, line 2912, not traced in detail).
- Acceptance has no numeric pass score in the workflow. The numeric bar, if any, lives in the Reviewer prompt, which is loaded from a data table and is not in the export. `Retry Current Task` only uses `review_score < 70` in the blind guard (`AHAWR_BLIND_GUARD`, `data-tables/ops/blind_guard_patch.py`).

## Patch-script pattern to follow

New script: `data-tables/ops/review_calibration_patch.py` (it does not exist yet). Follow `data-tables/ops/think_config_patch.py`, the most complete example:

- CLI: `python think_config_patch.py <workflow.json> [<workflow.json> ...]`. It patches each file in place; the output keeps the file's layout (indented for repo files, compact for exports) and line endings.
- Helpers: `node(w, name)` finds a node by name; `sub(code, old, new, what)` asserts `code.count(old) == 1` and raises `anchor for <what> not found exactly once`.
- Idempotency: a per-script `MARK` string; `patch(w)` returns an empty list when the mark is already present, and `main()` prints `already patched`.
- Write: a temp file (`path + ".tmp"`) and `os.replace`, so a failed write never truncates the target.

`data-tables/ops/blind_guard_patch.py` is the shorter variant: one anchor chosen from alternatives with `next(a for a in (...) if js.count(a) == 1)`, the same `MARK` check, but it writes the destination directly without a temp file. Prefer the `think_config_patch.py` write path.

Run the script on a copy of the live export and check the result before any import. The patch file is written; it is never imported into n8n (`CLAUDE.md`, "Conventions").

## Versioning rule

From `README.md` section "Versioning" (rule since 2026-10-15): the number is `X.Y.Z`. A new function raises the middle number by 1 and resets the last to 0. A bug-fix-only release raises the last number by 100. A small change raises it by 10; a tiny one (typo, link, one line) by 1. Every push adds a `CHANGELOG.md` entry. The number is set in `claude-runner/pyproject.toml` and `claude-runner/src/claude_runner/api.py` (`FastAPI(version=...)`). The current version is 0.11.0 (`CHANGELOG.md` line 6).

`CHANGELOG.md` has three entries dated 2026-10-15: `## 2026-10-15 (0.4.200)` (line 338), `## 2026-10-15 (0.4.100)` (line 346) and `## 2026-10-15` (line 352).

## Design

Status: proposal. Nothing here is implemented. All behaviour stays unchanged until the flag is enabled (see "Feature flag"). Line numbers refer to the export and must be re-checked on a fresh export.

### task_type

An optional field on each task in `plan_json`:

| Value | Meaning | Acceptance rule |
|---|---|---|
| `research` | Finds or explains facts; writes no code | Criteria and facts only (see below) |
| `code` | Changes code or tests | Unchanged: current Reviewer prompt and `pass` condition |
| `docs` | Changes Markdown or other documentation | Criteria and facts only (see below) |
| `ops` | Operator scripts, configuration, runtime steps | Unchanged, same as `code` |

- The Architect fills in `task_type` in its plan output. The Architect prompt gets one added line that lists the four values.
- A missing field, an empty value or an unknown value means `code`. This is the only default; no other field depends on it.
- Normalisation happens where the task is read: `Parse Plan` (line 131) and `Restore Saved Task State` (line 851), so that the value stored in `plan_json` and the value used on resume agree. The field is not required by the `Parse Plan` validation and a missing field must not throw `Invalid task definition`.

### Acceptance rules and thresholds

- `code` and `ops`: no threshold is added. `Parse Review` and `Review Passed?` keep their current behaviour, and the Reviewer prompt is not changed. The workflow has no numeric pass threshold, so this type stays as it is.
- `research` and `docs`: the Reviewer checks only the acceptance criteria and the stated facts. It does not reject for wording, style, structure or length. The `pass` condition is unchanged; only the Reviewer prompt differs.

Reviewer prompt addition, appended to the Reviewer prompt only for the task type named. Text for `research`:

```
This is a research task. Check only two things: (1) every acceptance criterion is met; (2) the facts stated are correct, using the files the task names. Do not reject for wording, style, structure or length. A needs_changes verdict must name the criterion or the fact that failed.
```

Text for `docs`:

```
This is a documentation task. Check only two things: (1) every acceptance criterion is met; (2) every name, path, command and value written in the text matches the repository. Do not reject for wording, style, structure or length. A needs_changes verdict must name the criterion or the wrong item that failed.
```

`code` and `ops` add no text.

### Escalation

Trigger: after 3 consecutive attempts of the same task whose stored `review_status` is not `pass`. The count uses only the stored `review_status` value. It never parses the reason text (`last_review_reason`, `review_feedback`).

- `review_status` is stored per task today only as the latest value (`Persist Parsed Review`, line 2272). Counting three consecutive rejections therefore needs a per-attempt value in the `Autonomous Agent Task Attempts` data table. A new column, for example `review_status`, is added in the n8n interface; no raw SQL.
- The check runs when the third consecutive rejection is recorded, before `Retry Current Task` (line 407) stops the task at `currentAttempt >= limit`. With the default `max_attempts_per_task` of 3, the retry limit would otherwise end the task first, so the escalation check must come before that stop.
- Marker: the task status is set to `escalation to Architect`. The field name is chosen by `docs/naming.md` when the change is implemented.
- While the marker is set, the Worker does not run that task again.

Architect summary: when the marker is set, the Architect run gets a short input with the task id, the title, the acceptance criteria and the review remarks of the three attempts (the `review_feedback` text of each attempt, at most 300 characters each). The Architect answers with JSON of this form:

```
{"decision": "accept with reservations" | "split the task" | "rewrite the criteria", "reason": "..."}
```

- `accept with reservations`: the task is marked accepted; the reservations are stored with the task.
- `split the task`: the task is replaced by the subtasks the Architect lists, each with `task_type`.
- `rewrite the criteria`: the acceptance criteria are replaced, the attempt count is reset to 1, and the task runs again.

Any other value is treated as no decision: the task stays at the marker and the run is reported to the operator.

### Feature flag

The change ships behind a flag that is OFF by default, read from `Constants` on the line after the anchor `  retry_delays: retryDelays,` (Constants row of the anchor table). With the flag off, the workflow behaves byte-for-byte as before, including the `Reviewer Verdict Retry Start` path.

### Operator procedure

The patch is `data-tables/ops/review_calibration_patch.py` (it does not exist yet; see "Patch-script pattern to follow"). The operator applies it during a pause, when no task is running in the queue.

1. Export the live workflow to a file outside the repository, for example `export_live.json`.
2. Copy the export: `cp export_live.json export_patched.json`.
3. Run the patch on the copy only: `python data-tables/ops/review_calibration_patch.py export_patched.json`. Expect `patched` on the first run and `already patched` on a second run.
4. Check the copy: every anchor from the table above occurs exactly once; the JSON parses; the JavaScript of the changed nodes passes a syntax check.
5. Apply during a pause: import the checked copy into n8n through the n8n interface, add the `review_status` column to the attempts data table in the n8n interface, and keep the flag OFF.
6. Turn the flag ON only after a test task of each type has run.

The patch script does not import into n8n. It writes a file, and the operator decides when and how that file is used; the script is never imported into n8n by any step.
