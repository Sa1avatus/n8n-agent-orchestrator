# Plan: fewer configuration columns, more JSON

Status: plan, nothing is changed yet. Written 2026-10-21 at the operator's request ("get rid of the columns that
duplicate the new scheme; see what else can become JSON to reduce the number of columns").

## 1. Why

`hermes_config` has 41 columns, 37 of them settings. The new scheme already exists next to them:
`roles_json` (models per role and attempt, with options) and `features_json` (workflow features). Several old columns
now say the same thing twice (the legacy `*_provider` / `*_model` fall back behind `roles_json`; `max_tasks` is also
`features_json.plan_no_truncate.max_tasks`). Every new setting today means a new column added by hand in the n8n
interface, a new reader in Constants, a new row in the launch settings window and a new line in the docs. One JSON
object per concern with one shared shape removes that cost: a new parameter is a key, not a column.

## 2. Inventory of `hermes_config` (profile `claude-code`, id 5)

Reference counts are the number of mentions in the live workflow export, the runner and board source, the ops scripts
and the docs (2026-10-21).

| Group | Columns | Now | Target |
|---|---|---|---|
| Identity | `id`, `profile_id`, `enabled`, `createdAt`, `updatedAt` | the workflow finds its row by `profile_id` and `enabled` | stay columns |
| Roles: model | `architect_*`, `worker_*`, `reviewer_*`, `service_*` provider and model (8) | legacy fallback behind `roles_json` (workflow 5-13 mentions each, ops 4-19) | absorbed by `roles_json`, then removed |
| Roles: polling | `architect_`, `worker_`, `reviewer_` `max_polls`, `poll_seconds`, `max_retries` (9) | read by Constants (workflow 1-5 each), the runner defaults, the launch window | `roles_json` role `options` (`max_polls`, `poll_seconds`, `max_retries`) |
| Loop limits | `max_tasks` (30), `max_attempts_per_task` (5), `retry_delays_json` | Constants; `max_tasks` duplicates `features_json` (the values disagree today: 30 against 15) | `max_attempts_per_task` and `retry_delays` go to `engine_json`; `max_tasks` goes away (it lives in `features_json`) |
| Thinking | `worker_think_policy`, `worker_think_policy_from_task`, `worker_think_off_after_seconds`, `worker_think_off_after_steps` (4) | the local Worker's thinking policy; runner and board read them (src 17 and 9 mentions) | one `thinking_json` |
| Retrieval | `retrieval_enabled`, `retrieval_url`, `retrieval_corpora_json`, `retrieval_worker_max_tokens`, `retrieval_reviewer_max_tokens`, `retrieval_timeout_ms`, `retrieval_label` (7) | Constants only (workflow 3-7 each) | one `retrieval_json` |
| Infrastructure | `runner_url`, `run_manager_workflow_id`, `telegram_chat_id` (3) | read in many nodes (`runner_url` 12 workflow, 19 src) | `engine_json` (keys `runner_url`, `run_manager_workflow_id`, `telegram_chat_id`) |
| Already JSON | `roles_json`, `features_json` | | stay |

Result: from 41 columns to 10 (34 removed, 3 JSON columns added): `id`, `profile_id`, `enabled`, `createdAt`, `updatedAt`, `roles_json`, `features_json`,
`thinking_json`, `retrieval_json`, `engine_json`.

### `missions` (13 columns)

`mission_id`, `name`, `objective`, `working_directory`, `rules_json`, `acceptance_criteria_json`, `version`, `enabled`,
`state_namespace`, `retrieval_corpora_json`, `createdAt`, `updatedAt`, `id`. The two planned per-mission overrides
(`mission_roles_json`, `mission_features_json`) become ONE column `overrides_json`
(`{"roles": {...}, "features": {...}, "retrieval": {...}}`); `retrieval_corpora_json` moves into it. `state_namespace` is
equal to `mission_id` in every mission and `docs/naming.md` already calls it `state_key`: remove it last, behind an alias
(it is a boundary name: board code, Live State and the events table use it).

### Not worth changing

- Live State (21 columns): runtime state written by many nodes, with read-modify-write races if merged into JSON; the
  session, run and status triplets per agent could become one JSON per agent, but the gain is small and the risk is
  real. Leave.
- Events table (21 columns): a log, queried by columns. Leave.
- `board.mission_config`, `board.queue`: already separate tables with versions.

## 3. Target shape (same rules everywhere)

One object per concern, the same shape as `roles_json` and `features_json`: no magic keys, a feature or a role is an
object; defaults in ONE table in a pure core (like `board_roles.js` and `board_features.js`); an empty or missing value
means "the defaults" and behaviour must stay byte-for-byte what it is today.

```json
// roles_json (exists): the polling and retry parameters join the role options
{"worker": {"1": "anthropic/claude-haiku-5-5", "2": "local/qwen3.8-27b-gsq-rco-iq2-s-mtp",
            "options": {"max_polls": 30, "poll_seconds": 3, "max_retries": 3}},
 "reviewer": {"1": "anthropic/sonnet", "options": {"max_polls": 20, "poll_seconds": 3, "max_retries": 5}}}

// thinking_json (new): the four worker_think_* columns
{"policy": {"variants": [...]}, "from_task": 1, "off_after_seconds": 1800, "off_after_steps": 40}

// retrieval_json (new): the seven retrieval_* columns
{"enabled": true, "url": "http://ahawr-retrieval:8500", "corpora": ["ahawr-workspace"],
 "timeout_ms": 60000, "worker_max_tokens": "<current value>", "reviewer_max_tokens": "<current value>", "label": "hybrid-v1"}

// engine_json (new): loop and infrastructure
{"max_attempts_per_task": 5, "retry_delays": [15, 30, 60, 120, 300], "runner_url": "http://claude-runner:8700",
 "run_manager_workflow_id": "6WE2ATGEDzUopBJH", "telegram_chat_id": "<id>"}
```

Naming follows `docs/naming.md`: units in names (`timeout_ms`, `*_seconds`), no `_json` suffix inside the object.

## 4. How a group is migrated (the same recipe for each)

1. **Core.** A pure core with the defaults table, validation, the per-mission override and `flatten()` to the legacy
   constants, in JS and in Python, with ONE shared cases file that both implementations pass (the `roles_cases.json` and
   `features_cases.json` pattern). A golden test: empty JSON flattens to exactly today's constants.
2. **Readers.** Constants reads the JSON first and the legacy column second, so nothing downstream changes. The runner
   and the board read through the same core. A surgical, idempotent patch with each anchor asserted once (never a
   wholesale node replacement); a test applies ALL ops patches in launch order to a copy of a fresh live export, twice.
3. **Operator step.** The operator adds the JSON column in the n8n interface (never raw SQL); a read-only migration
   script proposes the JSON from the legacy columns (a dry run, no secrets printed); the operator saves it.
4. **Window.** Both exist for one release: the JSON wins, the legacy column is the fallback. The launch settings window
   writes the JSON only. One or two missions run with the JSON before step 5.
5. **Removal.** The operator deletes the legacy columns in the n8n interface; the code drops the fallback in the next
   release; the changelog says which columns went and where their values live. Rollback before this step: clear the
   JSON column (the fallback takes over); after it: re-add the column and restore the value from the changelog entry.

Renames across a boundary (a column, an API field) follow `docs/naming.md`: an alias for one version, a changelog line,
the operator's approval.

## 5. Order and size

| Step | What | Columns removed | Risk |
|---|---|---|---|
| 0 | Settle the disagreeing duplicate `max_tasks` (30 in `hermes_config`, 15 in `features_json`); done inside `ahawr-features-config` | 1 | low |
| 1 | Roles: model columns and polling into `roles_json` | 17 | medium: many readers (workflow, runner, board, ops) |
| 2 | `thinking_json` | 4 | low: few readers |
| 3 | `retrieval_json` | 7 | low: Constants only |
| 4 | `engine_json` | 5 | medium: `runner_url` and `telegram_chat_id` are read in many nodes |
| 5 | `overrides_json` on missions, `state_namespace` alias | 3 | medium: a boundary name |

Each step is one mission written with the rules above, after `ahawr-features-config` and the providers mission, before
the code review and refactoring mission, which now also runs before 1.0 (`docs/review-refactor-plan.md` section 4, step 2 "hard-coded values" depends on it).

## 6. Risks and what is not changed

- The workflow reads columns in many nodes; a missed reader silently takes a default. Mitigation: the golden tests, the
  all-patches test, and a trial mission after each step.
- Secrets: `telegram_chat_id` is an identifier, not a secret, but it must not be printed in reports; API keys never go
  into any of these JSON objects (the providers mission stores them separately, write-only).
- The board UI writes whole JSON objects: use the existing version check and the diff-and-confirm step of the settings,
  so two editors do not overwrite each other.
- This plan changes no Docker topology and no database schema other than the n8n data table columns the operator adds
  and removes by hand.
