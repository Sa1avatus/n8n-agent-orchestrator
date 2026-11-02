# Config cutover: legacy columns and their readers (T001A)

Status: inventory. This file was added (T001A); T004D changed `data-tables/ops/launch_mission.sh` and `notify.py` (see the disposition table). No workflow, data table, patch or other code was changed, and the live `hermes_config` row was not read (no database access).

## Sources

- Plan and naming: `docs/config-columns-plan.md` (sections 2 to 5; sections 4 and 5 give the migration recipe and the order), `docs/naming.md`.
- Step documents: `docs/config-roles-step1.md` (the 17 role columns), `docs/config-thinking.md`, `docs/config-retrieval.md`, `docs/config-engine.md`, `docs/config-overrides.md` (missions).
- Staged exports, both present:
  - `scratchpad/live_workflow_export.json`: "AHAWR v13 — Claude Code", id `7oMrsFqTiYMy0JJN`, `updatedAt` 2026-10-23T14:17:41.716Z, 102 nodes. It is 9 days old on 2026-11-01; any later change is not in this inventory.
  - `scratchpad/live_run_manager_export.json`: "Claude Code Run Manager v1", id `6WE2ATGEDzUopBJH`, `updatedAt` 2026-09-27T16:15:48.083Z, 34 nodes.

Method: every string parameter of every node of both exports was decoded and scanned for the legacy names and their Constants or Mission field names (42 terms, word boundaries). Comment-only hits were not counted as reads (noted per row). Each undefined-field behaviour was read from the code of the node that produces the value (Constants, Mission, Run Manager Normalize Request). A value that was not traced is marked UNKNOWN.

## Legacy columns

The list is the plan's inventory: 34 `hermes_config` columns (plan steps 0 to 4: 1 + 17 + 4 + 7 + 5 = 34) and the missions columns. Every `hermes_config` column has a JSON target. For missions, the plan's step 5 says 3 columns go; the sources show only `state_namespace` and `retrieval_corpora_json` as legacy missions columns, so the third is UNKNOWN. The two planned override names are not columns today (`docs/config-overrides.md`). The reader counts are rows in the tables below (workflow / Run Manager).

Live values of all columns are UNKNOWN (not read). The workflow reads `hermes_config` by `profile_id = claude-code`, limit 1 (node `Load Config`), and Constants throws when no enabled row exists.

| # | Legacy column | Table | JSON column and key now carrying it | Name in Constants or Mission | Undefined field (code) | Reader rows (workflow / Run Manager) |
|---|---|---|---|---|---|---|
| 1 | `architect_model` | hermes_config | `roles_json` `architect."1"` (`provider/model`, with `architect_provider`) | `planner_model` | `B3`  | 11 / 0 |
| 2 | `architect_provider` | hermes_config | `roles_json` `architect."1"` (with `architect_model`) | `planner_provider` | `B3`  | 11 / 0 |
| 3 | `worker_model` | hermes_config | `roles_json` `worker."1"` (with `worker_provider`) | `worker_model` | `B3`  | 8 / 0 |
| 4 | `worker_provider` | hermes_config | `roles_json` `worker."1"` (with `worker_model`) | `worker_provider` | `B3`  | 8 / 0 |
| 5 | `reviewer_model` | hermes_config | `roles_json` `reviewer."1"` (with `reviewer_provider`) | `reviewer_model` | `B3`  | 7 / 0 |
| 6 | `reviewer_provider` | hermes_config | `roles_json` `reviewer."1"` (with `reviewer_model`) | `reviewer_provider` | `B3`  | 7 / 0 |
| 7 | `service_provider` | hermes_config | `roles_json` `service."1"` (with `service_model`) | `service_provider` | `B3S`  | 5 / 5 |
| 8 | `service_model` | hermes_config | `roles_json` `service."1"` (with `service_provider`) | `service_model` | `B3S`  | 5 / 5 |
| 9 | `architect_max_polls` | hermes_config | `roles_json` `architect.options.max_polls` | `architect_max_polls` | `B1`  | 2 / 0 |
| 10 | `worker_max_polls` | hermes_config | `roles_json` `worker.options.max_polls` | `worker_max_polls` | `B1`  | 2 / 0 |
| 11 | `reviewer_max_polls` | hermes_config | `roles_json` `reviewer.options.max_polls` | `reviewer_max_polls` | `B1`  | 3 / 0 |
| 12 | `architect_poll_seconds` | hermes_config | `roles_json` `architect.options.poll_seconds` | `architect_poll_seconds` | `B1`  | 2 / 0 |
| 13 | `worker_poll_seconds` | hermes_config | `roles_json` `worker.options.poll_seconds` | `worker_poll_seconds` | `B1`  | 2 / 0 |
| 14 | `reviewer_poll_seconds` | hermes_config | `roles_json` `reviewer.options.poll_seconds` | `reviewer_poll_seconds` | `B1`  | 3 / 0 |
| 15 | `architect_max_retries` | hermes_config | `roles_json` `architect.options.max_retries` | `architect_max_provider_retries` | `B2`  | 3 / 0 |
| 16 | `worker_max_retries` | hermes_config | `roles_json` `worker.options.max_retries` | `worker_max_provider_retries` | `B2`  | 3 / 0 |
| 17 | `reviewer_max_retries` | hermes_config | `roles_json` `reviewer.options.max_retries` | `reviewer_max_provider_retries` | `B2`  | 5 / 0 |
| 18 | `max_tasks` | hermes_config | `features_json` `plan_no_truncate.max_tasks` (`docs/config-columns-plan.md:11`) | `max_tasks` | `B1`  | 2 / 0 |
| 19 | `max_attempts_per_task` | hermes_config | `engine_json` `max_attempts_per_task` | `max_attempts_per_task` | `B1`  | 3 / 0 |
| 20 | `retry_delays_json` | hermes_config | `engine_json` `retry_delays` | `retry_delays` | `B4`  | 6 / 4 |
| 21 | `worker_think_policy` | hermes_config | `thinking_json` `policy` (variants: `policy.variants`) | `worker_think_policy` | `B15`  | 2 / 0 |
| 22 | `worker_think_policy_from_task` | hermes_config | `thinking_json` `from_task` | `worker_think_policy_from_task` | `B15`  | 2 / 0 |
| 23 | `worker_think_off_after_seconds` | hermes_config | `thinking_json` `off_after_seconds` | `worker_think_off_after_seconds` | `B16`  | 2 / 0 |
| 24 | `worker_think_off_after_steps` | hermes_config | `thinking_json` `off_after_steps` | `worker_think_off_after_steps` | `B16`  | 2 / 0 |
| 25 | `retrieval_enabled` | hermes_config | `retrieval_json` `enabled` | `retrieval_enabled` | `B5`  | 5 / 0 |
| 26 | `retrieval_url` | hermes_config | `retrieval_json` `url` | `retrieval_url` | `B6`  | 3 / 0 |
| 27 | `retrieval_corpora_json` | hermes_config | `retrieval_json` `corpora` | `retrieval_corpora_json` | `B7`  | 2 / 0 |
| 28 | `retrieval_timeout_ms` | hermes_config | `retrieval_json` `timeout_ms` | `retrieval_timeout_ms` | `B8`  | 3 / 0 |
| 29 | `retrieval_worker_max_tokens` | hermes_config | `retrieval_json` `worker_max_tokens` | `retrieval_worker_max_tokens` | `B9`  | 2 / 0 |
| 30 | `retrieval_reviewer_max_tokens` | hermes_config | `retrieval_json` `reviewer_max_tokens` | `retrieval_reviewer_max_tokens` | `B10`  | 2 / 0 |
| 31 | `retrieval_label` | hermes_config | `retrieval_json` `label` | `retrieval_label` | `B11`  | 3 / 0 |
| 32 | `runner_url` | hermes_config | `engine_json` `runner_url` | `runner_url` | `B12`  | 10 / 7 |
| 33 | `run_manager_workflow_id` | hermes_config | `engine_json` `run_manager_workflow_id` | `run_manager_workflow_id` | `B13`  | 5 / 0 |
| 34 | `telegram_chat_id` | hermes_config | `engine_json` `telegram_chat_id` | `telegram_chat_id` | `B14`  | 13 / 0 |
| 35 | `state_namespace` | missions | UNKNOWN: no JSON key. `state_key` in `docs/naming.md`; the plan removes the column behind an alias (step 5) | `state_namespace` | `B17`  | 26 / 0 |
| 36 | `retrieval_corpora_json` | missions | `overrides_json` `retrieval.corpora` (`docs/config-overrides.md`, missions table) | `retrieval_corpora_json` | `B18`  | 2 / 0 |
| 37 | `mission_roles_json (planned, not a column)` | missions | `overrides_json` `roles` (planned) | `mission_roles_json` | `B19`  | 5 / 0 |
| 38 | `mission_features_json (planned, not a column)` | missions | `overrides_json` `features` (planned) | `mission_features_json` | `B21`  | 0 / 0 |

Undefined-field codes (used in the tables below):

| Code | Undefined, null or empty field |
|---|---|
| `B1` | Constants `num(value, field, 1)` (line 5) throws `Invalid <field>` for undefined, null or empty (`Number` gives NaN or 0). The run stops before any reader. Parse Plan, Advance Task and Retry Current Task keep a `?? n` fallback that is never reached. |
| `B2` | `num(value, field, 0)`: null or empty gives 0 retries with no error; an undefined key (column missing from the row) throws `Invalid <field>`. |
| `B3` | Constants gives `''` (`String(row.x ?? '').trim()`). `legacyRole` (Build Architect, Worker and Reviewer Run Input, line 243) uses the pair only when provider and model are both set. With no `roles_json` entry for the role, `pickModel` returns null and `pickOrThrow` throws `no model configured for role "<role>" on attempt 1`. |
| `B3S` | Constants gives `''` with no check. Run Manager Normalize Request uses the run's model (lines 14, 58) and provider (lines 15, 59); a missing provider becomes `anthropic` (line 57). |
| `B3U` | Telegram texts and Restore Review Notification Context: `String(m || '?')` shows `?`; the try/catch returns `?` or an empty string. |
| `B3C` | Prepare Verdict Retry line 4: the Constants value is used only when Build Reviewer Run Input has not run; otherwise the Build value is used. |
| `B4` | Empty gives `[15, 30, 60, 120, 300]` (Constants lines 6-13). Invalid JSON or a non-numeric element throws `Invalid retry_delays_json`. |
| `B5` | Anything other than `true` or `'true'` gives `false`: retrieval off. |
| `B6` | Empty gives `http://ahawr-retrieval:8500` (Constants line 36). |
| `B7` | Empty gives `[]`; text without `[` is split on commas; invalid JSON throws `Invalid retrieval_corpora_json`. Mission corpora take precedence (Constants lines 31-32). |
| `B8` | `optNum`: not finite or below 1000 gives 120000 (Constants line 53). |
| `B9` | `optNum`: not finite or below 100 gives 6000 (Constants line 54). |
| `B10` | `optNum`: not finite or below 100 gives 5000 (Constants line 55). |
| `B11` | Empty gives `null` as the trace label (Build Worker Retrieval Request and Build Reviewer Retrieval Request, line 24). |
| `B12` | Empty gives `http://claude-runner:8700` (Constants line 40); the HTTP nodes use the same default. |
| `B13` | Empty gives `nhjwX1G7FiVTO2Ah` (Constants line 43). The Run Manager export has id `6WE2ATGEDzUopBJH`; see Open points. |
| `B14` | Empty fails the `notEmpty` test (If, If2-If5, QA Notice Gate?): the Telegram send or the QA notice is skipped. |
| `B15` | Policy gives `''`; from-task gives `0` (`Number(...) || 0`). |
| `B16` | null, undefined or empty gives -1 (Constants lines 83-84). A non-numeric value stays NaN in Constants and becomes -1 in Build Worker Run Input (lines 558-559). |
| `B17` | Empty or missing throws `Mission "<id>" does not define state_namespace.` (Mission line 37; Constants line 16). No later node sees an empty value. |
| `B18` | Empty gives `[]`, then the workspace corpus when a working directory is set (Mission lines 44, 78). Invalid JSON throws (Mission line 51). |
| `B19` | Constants reads `mission.roles_json` (line 86). The Mission node has no `roles_json` output (0 hits in its code, no spread of the row), so the value is always `''` in the export and `mergeRoles` gets `{}`. |
| `B21` | Not traced: UNKNOWN. |

## Workflow readers

One row per node and legacy name. `Occ` counts the occurrences in that node; the sum per name equals `grep -o -w` on the raw export (see Checks). Expressions are excerpts of the code line.

| # | Node | Legacy column (name in code) | Code excerpt | Lines | Occ | Undefined field (code) | Note |
|---|---|---|---|---|---|---|---|
| 1 | Mission | `state_namespace` | `const stateNamespace = String(row.state_namespace ?? '').trim();` | 34,37,107 | 3 | `B17` empty or missing: throws (Mission) | Line 37 is error text; reads are lines 34 and 107 (the Mission output). |
| 2 | Mission | `retrieval_corpora_json` | `throw new Error('Invalid retrieval_corpora_json for mission '${missionId}': ${error.message}');` | 51,77 | 2 | `B18` empty: `[]`, then the workspace corpus | Line 51 is error text; the read is line 77. |
| 3 | Architect Start | `run_manager_workflow_id` | `={{ $('Constants').first().json.run_manager_workflow_id }}` | 1 | 1 | `B13` empty: `nhjwX1G7FiVTO2Ah` |  |
| 4 | Parse Plan | `max_tasks` | `const maxTasks = Number($('Constants').first().json.max_tasks ?? 20);` | 35 | 1 | `B1` throws `Invalid <field>` (run stops) | Fallback `?? 20` is unreachable: Constants throws first. |
| 5 | Worker Start | `run_manager_workflow_id` | `={{ $('Constants').first().json.run_manager_workflow_id }}` | 1 | 1 | `B13` empty: `nhjwX1G7FiVTO2Ah` |  |
| 6 | Reviewer Start | `run_manager_workflow_id` | `={{ $('Constants').first().json.run_manager_workflow_id }}` | 1 | 1 | `B13` empty: `nhjwX1G7FiVTO2Ah` |  |
| 7 | Advance Task | `runner_url` | `url: cfg.runner_url + '/api/board/lessons/' + route,` | 18 | 1 | `B12` empty: `http://claude-runner:8700` |  |
| 8 | Advance Task | `state_namespace` | `body: { mission: String(cfg.state_namespace ?? ''), ...body },` | 20 | 1 | `B17` empty or missing: throws (Mission) |  |
| 9 | Advance Task | `max_attempts_per_task` | `max_attempts: Number(cfg.max_attempts_per_task ?? 1),` | 28 | 1 | `B1` throws `Invalid <field>` (run stops) | Fallback `?? 1` is unreachable. |
| 10 | Retry Current Task | `max_attempts_per_task` | `const maxAttempts = Number($('Constants').first().json.max_attempts_per_task ?? 3);` | 17,44 | 2 | `B1` throws `Invalid <field>` (run stops) | Fallbacks `?? 3` and `?? 1` are unreachable. |
| 11 | Retry Current Task | `runner_url` | `url: cfg.runner_url + '/api/board/lessons/' + route,` | 34 | 1 | `B12` empty: `http://claude-runner:8700` |  |
| 12 | Retry Current Task | `state_namespace` | `body: { mission: String(cfg.state_namespace ?? ''), ...body },` | 36 | 1 | `B17` empty or missing: throws (Mission) |  |
| 13 | Constants | `retry_delays_json` | `if (String(row.retry_delays_json ?? '').trim()) {` | 7,9,10,12 | 4 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 14 | Constants | `state_namespace` | `const stateNamespace = String(mission.state_namespace ?? '').trim();` | 15,16,77 | 3 | `B17` empty or missing: throws (Mission) | Line 16 error text is also a read path; no empty value reaches a reader. |
| 15 | Constants | `retrieval_corpora_json` | `} catch (e) { throw new Error('Invalid retrieval_corpora_json: ${e.message}'); }` | 29,32 | 2 | `B7` empty: `[]`; invalid JSON: throws |  |
| 16 | Constants | `retrieval_url` | `const retrievalUrl = (String(row.retrieval_url ?? '').trim() \|\| 'http://ahawr-retrieval:8500').replace(/\/` | 36,51 | 2 | `B6` empty: `http://ahawr-retrieval:8500` |  |
| 17 | Constants | `retrieval_enabled` | `const retrievalRequested = row.retrieval_enabled === true \|\| String(row.retrieval_enabled ?? '').toLowerCase` | 37,50 | 3 | `B5` not `true`: no retrieval |  |
| 18 | Constants | `runner_url` | `const runnerUrl = (String(row.runner_url ?? '').trim() \|\| 'http://claude-runner:8700').replace(/\/+$` | 40,46 | 2 | `B12` empty: `http://claude-runner:8700` |  |
| 19 | Constants | `run_manager_workflow_id` | `// configurable): hermes_config.run_manager_workflow_id, default nhjwX1G7FiVTO2Ah.` | 42,43,45 | 3 | `B13` empty: `nhjwX1G7FiVTO2Ah` | Line 42 is a comment; reads are lines 43 and 45. |
| 20 | Constants | `retrieval_timeout_ms` | `retrieval_timeout_ms: optNum(row.retrieval_timeout_ms, 120000, 1000),` | 53 | 2 | `B8` not finite or below 1000: 120000 |  |
| 21 | Constants | `retrieval_worker_max_tokens` | `retrieval_worker_max_tokens: optNum(row.retrieval_worker_max_tokens, 6000, 100),` | 54 | 2 | `B9` not finite or below 100: 6000 |  |
| 22 | Constants | `retrieval_reviewer_max_tokens` | `retrieval_reviewer_max_tokens: optNum(row.retrieval_reviewer_max_tokens, 5000, 100),` | 55 | 2 | `B10` not finite or below 100: 5000 |  |
| 23 | Constants | `retrieval_label` | `retrieval_label: String(row.retrieval_label ?? '').trim(),` | 56 | 2 | `B11` empty: trace label `null` |  |
| 24 | Constants | `planner_model` | `planner_model: String(row.architect_model ?? '').trim(),` | 57 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 25 | Constants | `architect_model` | `planner_model: String(row.architect_model ?? '').trim(),` | 57 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 26 | Constants | `planner_provider` | `planner_provider: String(row.architect_provider ?? '').trim(),` | 58 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 27 | Constants | `architect_provider` | `planner_provider: String(row.architect_provider ?? '').trim(),` | 58 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 28 | Constants | `worker_model` | `worker_model: String(row.worker_model ?? '').trim(),` | 59 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 29 | Constants | `worker_provider` | `worker_provider: String(row.worker_provider ?? '').trim(),` | 60 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 30 | Constants | `reviewer_model` | `reviewer_model: String(row.reviewer_model ?? '').trim(),` | 61 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 31 | Constants | `reviewer_provider` | `reviewer_provider: String(row.reviewer_provider ?? '').trim(),` | 62 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` |  |
| 32 | Constants | `service_model` | `service_model: String(row.service_model ?? '').trim(),` | 63 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 33 | Constants | `service_provider` | `service_provider: String(row.service_provider ?? '').trim(),` | 64 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 34 | Constants | `max_tasks` | `max_tasks: num(row.max_tasks,'max_tasks',1),` | 65 | 3 | `B1` throws `Invalid <field>` (run stops) | Line 65 is the only read; num() throws first. |
| 35 | Constants | `max_attempts_per_task` | `max_attempts_per_task: num(row.max_attempts_per_task,'max_attempts_per_task',1),` | 66 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 36 | Constants | `architect_max_provider_retries` | `architect_max_provider_retries: num(row.architect_max_retries,'architect_max_provider_retr` | 67 | 2 | `B2` null or empty: 0 retries; undefined: throws |  |
| 37 | Constants | `architect_max_retries` | `architect_max_provider_retries: num(row.architect_max_retries,'architect_max_provider_retries',0),` | 67 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 38 | Constants | `worker_max_provider_retries` | `worker_max_provider_retries: num(row.worker_max_retries,'worker_max_provider_retries',0` | 68 | 2 | `B2` null or empty: 0 retries; undefined: throws |  |
| 39 | Constants | `worker_max_retries` | `worker_max_provider_retries: num(row.worker_max_retries,'worker_max_provider_retries',0),` | 68 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 40 | Constants | `reviewer_max_provider_retries` | `reviewer_max_provider_retries: num(row.reviewer_max_retries,'reviewer_max_provider_retrie` | 69 | 2 | `B2` null or empty: 0 retries; undefined: throws |  |
| 41 | Constants | `reviewer_max_retries` | `reviewer_max_provider_retries: num(row.reviewer_max_retries,'reviewer_max_provider_retries',0),` | 69 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 42 | Constants | `architect_max_polls` | `architect_max_polls: num(row.architect_max_polls,'architect_max_polls',1),` | 70 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 43 | Constants | `worker_max_polls` | `worker_max_polls: num(row.worker_max_polls,'worker_max_polls',1),` | 71 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 44 | Constants | `reviewer_max_polls` | `reviewer_max_polls: num(row.reviewer_max_polls,'reviewer_max_polls',1),` | 72 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 45 | Constants | `architect_poll_seconds` | `architect_poll_seconds: num(row.architect_poll_seconds,'architect_poll_seconds',1)` | 73 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 46 | Constants | `worker_poll_seconds` | `worker_poll_seconds: num(row.worker_poll_seconds,'worker_poll_seconds',1),` | 74 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 47 | Constants | `reviewer_poll_seconds` | `reviewer_poll_seconds: num(row.reviewer_poll_seconds,'reviewer_poll_seconds',1),` | 75 | 3 | `B1` throws `Invalid <field>` (run stops) |  |
| 48 | Constants | `telegram_chat_id` | `telegram_chat_id: String(row.telegram_chat_id ?? '').trim(),` | 76 | 2 | `B14` empty: Telegram send or QA notice skipped |  |
| 49 | Constants | `retry_delays` | `retry_delays: retryDelays,` | 79 | 1 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 50 | Constants | `worker_think_policy` | `worker_think_policy: String(row.worker_think_policy ?? '').trim().toLowerCase()` | 81 | 2 | `B15` empty: `''`; from task: `0` |  |
| 51 | Constants | `worker_think_policy_from_task` | `worker_think_policy_from_task: Number(row.worker_think_policy_from_task ?? 0) \|\| 0,` | 82 | 2 | `B15` empty: `''`; from task: `0` |  |
| 52 | Constants | `worker_think_off_after_seconds` | `worker_think_off_after_seconds: (row.worker_think_off_after_seconds === null \|\| row.worker` | 83 | 5 | `B16` null, undefined or empty: -1 |  |
| 53 | Constants | `worker_think_off_after_steps` | `worker_think_off_after_steps: (row.worker_think_off_after_steps === null \|\| row.worker_t` | 84 | 5 | `B16` null, undefined or empty: -1 |  |
| 54 | Constants | `mission_roles_json` | `mission_roles_json: String(mission.roles_json ?? '').trim(),` | 86 | 1 | `B19` always empty (Mission has no `roles_json`) |  |
| 55 | Reviewer State | `state_namespace` | `={{ String($('Constants').first().json.state_namespace ?? '') }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 56 | Load Task State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 57 | Persist Initial Task State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 2 | `B17` empty or missing: throws (Mission) |  |
| 58 | Persist Task Progress | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 2 | `B17` empty or missing: throws (Mission) |  |
| 59 | Persist Worker Result | `state_namespace` | `={{ $('Constants').first().json.state_namespace }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 60 | Persist Worker Terminal State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 61 | Persist Reviewer Result | `state_namespace` | `={{ $('Constants').first().json.state_namespace }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 62 | Persist Reviewer Terminal State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 63 | Persist Parsed Review | `state_namespace` | `={{ $('Constants').first().json.state_namespace }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 64 | Persist Reviewer State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 2 | `B17` empty or missing: throws (Mission) |  |
| 65 | Persist Retry State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 2 | `B17` empty or missing: throws (Mission) |  |
| 66 | Load Worker Result | `state_namespace` | `={{ String($('Constants').first().json.state_namespace) }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 67 | Load Reviewer Result | `state_namespace` | `={{ String($('Constants').first().json.state_namespace) }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 68 | Restore Review Notification Context | `worker_model` | `Worker Run Input').last().json : { model: $('Constants').first().json.worker_model, provider: $('Constants').first().json.worker_provider });` | 22 | 1 | `B3U` empty: shows `?` | Shows `?`; the catch returns an empty string. |
| 69 | Restore Review Notification Context | `worker_provider` | `ts').first().json.worker_model, provider: $('Constants').first().json.worker_provider }); const r = ($('Build Reviewer Run Input').isExecuted ? $` | 22 | 1 | `B3U` empty: shows `?` | Shows `?`; the catch returns an empty string. |
| 70 | Restore Review Notification Context | `reviewer_model` | `viewer Run Input').last().json : { model: $('Constants').first().json.reviewer_model, provider: $('Constants').first().json.reviewer_provider })` | 22 | 1 | `B3U` empty: shows `?` | Shows `?`; the catch returns an empty string. |
| 71 | Restore Review Notification Context | `reviewer_provider` | `').first().json.reviewer_model, provider: $('Constants').first().json.reviewer_provider }); const f = (m, p) => String(m \|\| '?') + (p ? ' @' + p :` | 22 | 1 | `B3U` empty: shows `?` | Shows `?`; the catch returns an empty string. |
| 72 | Persist Workflow Failure State | `state_namespace` | `={{$('Constants').first().json.state_namespace}}` | 1 | 2 | `B17` empty or missing: throws (Mission) |  |
| 73 | Send a text message5 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 74 | Send a text message5 | `planner_model` | `hitect Run Input').last().json : { model: $('Constants').first().json.planner_model, provider: $('Constants').first().json.planner_provider });` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 75 | Send a text message5 | `planner_provider` | `s').first().json.planner_model, provider: $('Constants').first().json.planner_provider });const f=(m,p)=>String(m\|\|'?')+(p?' @'+p:'');return f(r.m` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 76 | If | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 77 | Send a text message | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 78 | If2 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 79 | If3 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 80 | Send a text message3 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 81 | Send a text message6 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 82 | Send a text message6 | `worker_model` | `Worker Run Input').last().json : { model: $('Constants').first().json.worker_model, provider: $('Constants').first().json.worker_provider });c` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 83 | Send a text message6 | `worker_provider` | `ts').first().json.worker_model, provider: $('Constants').first().json.worker_provider });const f=(m,p)=>String(m\|\|'?')+(p?' @'+p:'');return f(r.m` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 84 | Send a text message7 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 85 | Send a text message7 | `worker_model` | `Worker Run Input').last().json : { model: $('Constants').first().json.worker_model, provider: $('Constants').first().json.worker_provider });c` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 86 | Send a text message7 | `worker_provider` | `ts').first().json.worker_model, provider: $('Constants').first().json.worker_provider });const f=(m,p)=>String(m\|\|'?')+(p?' @'+p:'');return f(r.m` | 1 | 1 | `B3U` empty: shows `?` | Shows `?` for an empty value; the catch returns `?`. |
| 87 | If4 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 88 | If5 | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 89 | Worker Retrieval Enabled? | `retrieval_enabled` | `={{ $('Constants').first().json.retrieval_enabled === true }}` | 1 | 1 | `B5` not `true`: no retrieval |  |
| 90 | Retrieve Worker Context | `retrieval_url` | `={{ $('Constants').first().json.retrieval_url + '/retrieve' }}` | 1 | 1 | `B6` empty: `http://ahawr-retrieval:8500` |  |
| 91 | Retrieve Worker Context | `retrieval_timeout_ms` | `={{ $('Constants').first().json.retrieval_timeout_ms }}` | 1 | 1 | `B8` not finite or below 1000: 120000 |  |
| 92 | Attach Worker Context | `retrieval_enabled` | `const enabled = $('Constants').first().json.retrieval_enabled === true;` | 4 | 1 | `B5` not `true`: no retrieval |  |
| 93 | Reviewer Retrieval Enabled? | `retrieval_enabled` | `={{ $('Constants').first().json.retrieval_enabled === true }}` | 1 | 1 | `B5` not `true`: no retrieval |  |
| 94 | Retrieve Reviewer Context | `retrieval_url` | `={{ $('Constants').first().json.retrieval_url + '/retrieve' }}` | 1 | 1 | `B6` empty: `http://ahawr-retrieval:8500` |  |
| 95 | Retrieve Reviewer Context | `retrieval_timeout_ms` | `={{ $('Constants').first().json.retrieval_timeout_ms }}` | 1 | 1 | `B8` not finite or below 1000: 120000 |  |
| 96 | Attach Reviewer Context | `retrieval_enabled` | `const enabled = $('Constants').first().json.retrieval_enabled === true;` | 4 | 1 | `B5` not `true`: no retrieval |  |
| 97 | Build Worker Retrieval Request | `retrieval_worker_max_tokens` | `budget: { max_tokens: c.retrieval_worker_max_tokens },` | 23 | 1 | `B9` not finite or below 100: 6000 |  |
| 98 | Build Worker Retrieval Request | `state_namespace` | `trace: { mission_id: String(m.mission_id ?? ''), state_namespace: String(c.state_namespace ?? ''), task_id: String(task.id ?` | 24 | 2 | `B17` empty or missing: throws (Mission) |  |
| 99 | Build Worker Retrieval Request | `retrieval_label` | `pace ?? ''), task_id: String(task.id ?? ''), role: 'worker', label: c.retrieval_label \|\| null }` | 24 | 1 | `B11` empty: trace label `null` |  |
| 100 | Build Reviewer Retrieval Request | `retrieval_reviewer_max_tokens` | `budget: { max_tokens: c.retrieval_reviewer_max_tokens },` | 23 | 1 | `B10` not finite or below 100: 5000 |  |
| 101 | Build Reviewer Retrieval Request | `state_namespace` | `trace: { mission_id: String(m.mission_id ?? ''), state_namespace: String(c.state_namespace ?? ''), task_id: String(task.id ?` | 24 | 2 | `B17` empty or missing: throws (Mission) |  |
| 102 | Build Reviewer Retrieval Request | `retrieval_label` | `ce ?? ''), task_id: String(task.id ?? ''), role: 'reviewer', label: c.retrieval_label \|\| null }` | 24 | 1 | `B11` empty: trace label `null` |  |
| 103 | Build Architect Run Input | `architect_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 104 | Build Architect Run Input | `planner_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 105 | Build Architect Run Input | `architect_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 106 | Build Architect Run Input | `planner_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 107 | Build Architect Run Input | `worker_provider` | `worker_provider: config.worker_provider,` | 353 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 108 | Build Architect Run Input | `worker_model` | `worker_model: config.worker_model,` | 354 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 109 | Build Architect Run Input | `reviewer_provider` | `reviewer_provider: config.reviewer_provider,` | 355 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 110 | Build Architect Run Input | `reviewer_model` | `reviewer_model: config.reviewer_model` | 356 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 111 | Build Architect Run Input | `mission_roles_json` | `const roles = mergeRoles(parse(config.roles_json), parse(config.mission_roles_json));` | 358 | 1 | `B19` always empty (Mission has no `roles_json`) |  |
| 112 | Build Architect Run Input | `architect_poll_seconds` | `poll_seconds: ($('Constants').first().json.architect_poll_seconds),` | 377 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 113 | Build Architect Run Input | `architect_max_polls` | `max_polls: ($('Constants').first().json.architect_max_polls),` | 378 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 114 | Build Architect Run Input | `architect_max_provider_retries` | `max_retries: ($('Constants').first().json.architect_max_provider_retries),` | 379 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 115 | Build Architect Run Input | `retry_delays` | `retry_delays: ($('Constants').first().json.retry_delays),` | 380 | 2 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 116 | Build Architect Run Input | `state_namespace` | `state_key: ($('Constants').first().json.state_namespace),` | 384 | 1 | `B17` empty or missing: throws (Mission) |  |
| 117 | Build Architect Run Input | `service_provider` | `service_provider: ($('Constants').first().json.service_provider),` | 386 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 118 | Build Architect Run Input | `service_model` | `service_model: ($('Constants').first().json.service_model),` | 387 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 119 | Build Architect Run Input | `runner_url` | `runner_url: ($('Constants').first().json.runner_url),` | 388 | 2 | `B12` empty: `http://claude-runner:8700` |  |
| 120 | Build Worker Run Input | `architect_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 121 | Build Worker Run Input | `planner_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 122 | Build Worker Run Input | `architect_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 123 | Build Worker Run Input | `planner_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 124 | Build Worker Run Input | `worker_provider` | `worker_provider: config.worker_provider,` | 353 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 125 | Build Worker Run Input | `worker_model` | `worker_model: config.worker_model,` | 354 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 126 | Build Worker Run Input | `reviewer_provider` | `reviewer_provider: config.reviewer_provider,` | 355 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 127 | Build Worker Run Input | `reviewer_model` | `reviewer_model: config.reviewer_model` | 356 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 128 | Build Worker Run Input | `mission_roles_json` | `const roles = mergeRoles(parse(config.roles_json), parse(config.mission_roles_json));` | 358 | 1 | `B19` always empty (Mission has no `roles_json`) |  |
| 129 | Build Worker Run Input | `runner_url` | `url: __lessonsCfg.runner_url + '/api/board/lessons/select',` | 398,481,553 | 4 | `B12` empty: `http://claude-runner:8700` |  |
| 130 | Build Worker Run Input | `state_namespace` | `mission: String(__lessonsCfg.state_namespace ?? ''),` | 401,550 | 2 | `B17` empty or missing: throws (Mission) |  |
| 131 | Build Worker Run Input | `worker_poll_seconds` | `poll_seconds: ($('Constants').first().json.worker_poll_seconds),` | 545 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 132 | Build Worker Run Input | `worker_max_polls` | `max_polls: ($('Constants').first().json.worker_max_polls),` | 546 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 133 | Build Worker Run Input | `worker_max_provider_retries` | `max_retries: ($('Constants').first().json.worker_max_provider_retries),` | 547 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 134 | Build Worker Run Input | `retry_delays` | `retry_delays: ($('Constants').first().json.retry_delays),` | 549 | 2 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 135 | Build Worker Run Input | `service_provider` | `service_provider: ($('Constants').first().json.service_provider),` | 551 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 136 | Build Worker Run Input | `service_model` | `service_model: ($('Constants').first().json.service_model),` | 552 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 137 | Build Worker Run Input | `worker_think_policy` | `think_policy: String($('Constants').first().json.worker_think_policy ?? ''),` | 556 | 1 | `B15` empty: `''`; from task: `0` |  |
| 138 | Build Worker Run Input | `worker_think_policy_from_task` | `think_policy_from_task: Number($('Constants').first().json.worker_think_policy_from_task ?? 0) \|\| 0,` | 557 | 1 | `B15` empty: `''`; from task: `0` |  |
| 139 | Build Worker Run Input | `worker_think_off_after_seconds` | `off_after_seconds: Number.isFinite(Number($('Constants').first().json.worker_think_off_after_seconds ?? NaN)) ? Number($('Constants').first().json.worker_think_` | 558 | 2 | `B16` null, undefined or empty: -1 |  |
| 140 | Build Worker Run Input | `worker_think_off_after_steps` | `k_off_after_steps: Number.isFinite(Number($('Constants').first().json.worker_think_off_after_steps ?? NaN)) ? Number($('Constants').first().json.worker_think_` | 559 | 2 | `B16` null, undefined or empty: -1 |  |
| 141 | Build Reviewer Run Input | `architect_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 142 | Build Reviewer Run Input | `planner_provider` | `architect_provider: config.planner_provider,` | 351 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 143 | Build Reviewer Run Input | `architect_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 144 | Build Reviewer Run Input | `planner_model` | `architect_model: config.planner_model,` | 352 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 145 | Build Reviewer Run Input | `worker_provider` | `worker_provider: config.worker_provider,` | 353 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 146 | Build Reviewer Run Input | `worker_model` | `worker_model: config.worker_model,` | 354 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 147 | Build Reviewer Run Input | `reviewer_provider` | `reviewer_provider: config.reviewer_provider,` | 355 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 148 | Build Reviewer Run Input | `reviewer_model` | `reviewer_model: config.reviewer_model` | 356 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 149 | Build Reviewer Run Input | `mission_roles_json` | `const roles = mergeRoles(parse(config.roles_json), parse(config.mission_roles_json));` | 358 | 1 | `B19` always empty (Mission has no `roles_json`) |  |
| 150 | Build Reviewer Run Input | `reviewer_poll_seconds` | `poll_seconds: ($('Constants').first().json.reviewer_poll_seconds),` | 442 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 151 | Build Reviewer Run Input | `reviewer_max_polls` | `max_polls: ($('Constants').first().json.reviewer_max_polls),` | 443 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 152 | Build Reviewer Run Input | `reviewer_max_provider_retries` | `max_retries: ($('Constants').first().json.reviewer_max_provider_retries),` | 444 | 1 | `B2` null or empty: 0 retries; undefined: throws |  |
| 153 | Build Reviewer Run Input | `retry_delays` | `retry_delays: ($('Constants').first().json.retry_delays),` | 446 | 2 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 154 | Build Reviewer Run Input | `state_namespace` | `eviewer State').first().json.state_key ?? $('Constants').first().json.state_namespace ?? '')),` | 447 | 1 | `B17` empty or missing: throws (Mission) |  |
| 155 | Build Reviewer Run Input | `service_model` | `service_model: ($('Constants').first().json.service_model),` | 448 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 156 | Build Reviewer Run Input | `service_provider` | `service_provider: ($('Constants').first().json.service_provider),` | 449 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 157 | Build Reviewer Run Input | `runner_url` | `runner_url: ($('Constants').first().json.runner_url),` | 450 | 2 | `B12` empty: `http://claude-runner:8700` |  |
| 158 | Load Previous Worker Report | `state_namespace` | `={{ String($('Constants').first().json.state_namespace) }}` | 1 | 1 | `B17` empty or missing: throws (Mission) |  |
| 159 | Prepare Verdict Retry | `reviewer_max_retries` | `// The number of re-asks is hermes_config.reviewer_max_retries; when used up, fail with the parse error.` | 2 | 1 | `B2` null or empty: 0 retries; undefined: throws | Line 2 is a comment: not a read. |
| 160 | Prepare Verdict Retry | `reviewer_model` | `viewer Run Input').last().json : { model: $('Constants').first().json.reviewer_model, provider: $('Constants').first().json.reviewer_provider })` | 4 | 1 | `B3C` empty: used only when Build Reviewer did not run | Line 4: Constants value only when Build Reviewer Run Input has not run. |
| 161 | Prepare Verdict Retry | `reviewer_provider` | `').first().json.reviewer_model, provider: $('Constants').first().json.reviewer_provider });` | 4 | 1 | `B3C` empty: used only when Build Reviewer did not run | Line 4: Constants value only when Build Reviewer Run Input has not run. |
| 162 | Prepare Verdict Retry | `reviewer_max_provider_retries` | `const maxRetries = Number(cfg.reviewer_max_provider_retries ?? 0);` | 5,24 | 2 | `B2` null or empty: 0 retries; undefined: throws | Line 5 fallback `?? 0` is unreachable. |
| 163 | Prepare Verdict Retry | `reviewer_poll_seconds` | `poll_seconds: cfg.reviewer_poll_seconds, max_polls: cfg.reviewer_max_polls,` | 23 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 164 | Prepare Verdict Retry | `reviewer_max_polls` | `poll_seconds: cfg.reviewer_poll_seconds, max_polls: cfg.reviewer_max_polls,` | 23 | 1 | `B1` throws `Invalid <field>` (run stops) |  |
| 165 | Prepare Verdict Retry | `retry_delays` | `max_retries: cfg.reviewer_max_provider_retries, retry_count: 0, retry_delays: cfg.retry_delays,` | 24 | 2 | `B4` empty: `[15, 30, 60, 120, 300]`; invalid JSON: throws |  |
| 166 | Prepare Verdict Retry | `state_namespace` | `state_key: String(cfg.state_namespace ?? ''), service_model: cfg.service_model, service_provider:` | 25 | 1 | `B17` empty or missing: throws (Mission) |  |
| 167 | Prepare Verdict Retry | `service_model` | `state_key: String(cfg.state_namespace ?? ''), service_model: cfg.service_model, service_provider: cfg.service_provider,` | 25 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 168 | Prepare Verdict Retry | `service_provider` | `String(cfg.state_namespace ?? ''), service_model: cfg.service_model, service_provider: cfg.service_provider,` | 25 | 2 | `B3S` empty: Run Manager uses the run model; provider `anthropic` |  |
| 169 | Prepare Verdict Retry | `runner_url` | `runner_url: cfg.runner_url, runner_api_key: cfg.runner_api_key,` | 26 | 2 | `B12` empty: `http://claude-runner:8700` |  |
| 170 | Reviewer Verdict Retry Start | `run_manager_workflow_id` | `={{ $('Constants').first().json.run_manager_workflow_id }}` | 1 | 1 | `B13` empty: `nhjwX1G7FiVTO2Ah` |  |
| 171 | Prepare Results Update | `state_namespace` | `return [{ json: { state_key: c.state_namespace, runner_url: c.runner_url, runner_api_key: c.runner_api_key` | 2 | 1 | `B17` empty or missing: throws (Mission) |  |
| 172 | Prepare Results Update | `runner_url` | `return [{ json: { state_key: c.state_namespace, runner_url: c.runner_url, runner_api_key: c.runner_api_key } }];` | 2 | 2 | `B12` empty: `http://claude-runner:8700` |  |
| 173 | Split Task Notice | `architect_provider` | `architect_provider: config.planner_provider,` | 653 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 174 | Split Task Notice | `planner_provider` | `architect_provider: config.planner_provider,` | 653 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 175 | Split Task Notice | `architect_model` | `architect_model: config.planner_model,` | 654 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 176 | Split Task Notice | `planner_model` | `architect_model: config.planner_model,` | 654 | 1 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 177 | Split Task Notice | `worker_provider` | `worker_provider: config.worker_provider,` | 655 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 178 | Split Task Notice | `worker_model` | `worker_model: config.worker_model,` | 656 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 179 | Split Task Notice | `reviewer_provider` | `reviewer_provider: config.reviewer_provider,` | 657 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 180 | Split Task Notice | `reviewer_model` | `reviewer_model: config.reviewer_model` | 658 | 2 | `B3` empty pair is not used; no `roles_json` entry: throws `no model configured` | Legacy block of `__roleConfig` (`legacyRole` line 252). |
| 181 | Split Task Notice | `mission_roles_json` | `const roles = mergeRoles(parse(config.roles_json), parse(config.mission_roles_json));` | 660 | 1 | `B19` always empty (Mission has no `roles_json`) |  |
| 182 | QA Request | `runner_url` | `={{ $('Constants').first().json.runner_url }}/v1/qa/requests` | 1 | 1 | `B12` empty: `http://claude-runner:8700` |  |
| 183 | QA Poll | `runner_url` | `={{ $('Constants').first().json.runner_url }}/v1/qa/requests/{{ $('QA Request').first().json.id }}` | 1 | 1 | `B12` empty: `http://claude-runner:8700` |  |
| 184 | QA Notice Gate? | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |
| 185 | QA Notice | `telegram_chat_id` | `={{ $('Constants').first().json.telegram_chat_id }}` | 1 | 1 | `B14` empty: Telegram send or QA notice skipped |  |

Constants (`Constants`, node 25) is the single point where the hermes row becomes values. Every reader above reads its output field, so the behaviour in the table is the Constants behaviour unless the row says otherwise.

### legacy_columns_optional_patch.py (T003A)

`data-tables/ops/legacy_columns_optional_patch.py` changes the ten Constants reads that throw when their column is undefined (`num(row.X, ...)`): `max_tasks` and the nine role retry, poll and poll-seconds reads. Each takes the legacy column when it is set (`legacyOr`: `row?.X === undefined ? default : row.X`) and otherwise the core default. `null` and `''` keep their old values. It runs after the other ops patches, so the engine reads are already gone when it runs. Every row of the table above has one disposition:

| Disposition | Rows | Reason |
|---|---|---|
| Changed by this patch (Constants) | 34, 36-47 | `legacyOr` in Constants: the column when set, else the core default (features core for `max_tasks`, `board_roles.js` POLLING_DEFAULTS for the rest). |
| Output reader that now gets the default instead of a throw | 4, 112-114, 131-133, 150-152, 162-164 | Reads a Constants output that the change above always sets; the reader itself is unchanged. |
| Covered by engine_config_patch (`__engine` replaces the read) | 3, 5, 6, 19, 170 (run_manager_workflow_id); 7, 11, 18, 119, 129, 157, 169, 172, 182, 183 (runner_url); 9, 10, 35 (max_attempts_per_task); 13 (retry_delays_json); 49, 115, 134, 153, 165 (retry_delays); 48, 73, 76-81, 84, 87, 88, 184, 185 (telegram_chat_id) | The engine core reads these columns; the output names are unchanged. |
| Not a hermes_config read (Mission node) | 1, 2 | Mission reads the missions row; this patch edits Constants only. |
| Required mission value (`state_namespace`), throws when empty by design (B17) | 8, 12, 14, 55-67, 72, 98, 101, 116, 130, 154, 158, 166, 171 | Comes from the mission, not from a legacy column; no default applies. |
| Mission value (`roles_json`), always empty (B19) | 54, 111, 128, 149, 181 | Mission has no `roles_json` output; not a column default. |
| Already undefined-safe in Constants (`String(x ?? '')`, `optNum`, `parseCorpora`, `=== true`) | 15-17, 20-33, 50-53 | Undefined gives an empty or default value, no throw. Model and provider pairs are taken from roles_json first (roles_config_patch). |
| Output reader, already undefined-safe | 68-71, 74, 75, 82, 83, 85, 86, 89-97, 99, 100, 102, 117, 118, 135, 136, 137-140, 155, 156, 160, 161, 167, 168 | Constants outputs that are strings or defaulted numbers; the reader's own fallback (`?` or `''`) applies. |
| Legacy fallback block of `__roleConfig`, roles_json read first | 103-110, 120-127, 141-148, 173-180 | Model and provider come from roles_json (roles_config_patch) before the legacy block. An empty pair with no roles_json entry still throws `no model configured` (B3); that is documented behaviour, not an undefined read. |
| Comment, not a read | 159 | Line 2 is a comment. |

## Run Manager readers

The Run Manager does not read `hermes_config`: no legacy column name appears in any of its data table nodes. Legacy values reach it as input fields from the calling workflow (`service_*`, `runner_url`, `retry_delays`) and as the polling and thinking inputs listed after the table. Those input names (`poll_seconds`, `max_polls`, `max_retries`, `think_*`) are not legacy names, so they are not in the grep count.

| # | Node | Legacy column (name in code) | Code excerpt | Lines | Occ | Undefined field (code) | Note |
|---|---|---|---|---|---|---|---|
| 1 | Normalize Request | `service_model` | `service_model: String(i.service_model ?? i.model ?? '').trim(),` | 14,58 | 4 | `RMSVC_M` missing: run model; no model: `''` | Lines 14 and 58. |
| 2 | Normalize Request | `service_provider` | `service_provider: String(i.service_provider ?? i.provider ?? '').trim(),` | 15,59 | 4 | `RMSVC_P` missing: run provider; no provider: `anthropic` | Lines 15 and 59. |
| 3 | Normalize Request | `runner_url` | `runner_url: String(i.runner_url ?? '').trim().replace(/\/+$/, '') \|\| '` | 18 | 2 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 4 | Normalize Request | `retry_delays` | `retry_delays: (() => {` | 26,27 | 2 | `RMRETRY` not an array or invalid JSON: `[15, 30, 60, 120, 300]`, no error | Lines 26-45; the catch (line 39) falls back silently. |
| 5 | Start / Resume Run | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 6 | Get Run Status | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 7 | Resume Existing Session | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 8 | Retry Controller | `retry_delays` | `const delays=Array.isArray($json.retry_delays)?$json.retry_delays: [15,30,60,120,300];` | 3 | 2 | `RMRETRY` not an array or invalid JSON: `[15, 30, 60, 120, 300]`, no error | Non-array: default (line 3). |
| 9 | Build Result | `service_model` | `austed ?? false), failure_reason: String($json.failure_reason ?? ''), service_model: String($json.service_model ?? ''), service_provider: Strin` | 1 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 10 | Build Result | `service_provider` | `lure_reason ?? ''), service_model: String($json.service_model ?? ''), service_provider: String($json.service_provider ?? ''), compression_failed:` | 1 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 11 | Build Persist State — Run Context | `service_model` | `service_model: String(request.service_model ?? ''),` | 8 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 12 | Build Persist State — Run Context | `service_provider` | `service_provider: String(request.service_provider ?? ''),` | 9 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 13 | Restore Runner State — Status | `service_model` | `String(runner.model ?? ''), provider: String(runner.provider ?? ''), service_model: String(runner.service_model ?? ''), service_provider: Stri` | 3 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 14 | Restore Runner State — Status | `service_provider` | `r.provider ?? ''), service_model: String(runner.service_model ?? ''), service_provider: String(runner.service_provider ?? ''), working_directory:` | 3 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 15 | Restore Runner State — Status | `retry_delays` | `nner.max_retries ?? 3), retry_count: Number(runner.retry_count ?? 0), retry_delays: Array.isArray(runner.retry_delays) ? runner.retry_delays :` | 3 | 3 | `RMRETRY` not an array or invalid JSON: `[15, 30, 60, 120, 300]`, no error | Non-array: default. |
| 16 | Restore Runner State — Run Context | `service_model` | `String(runner.model ?? ''), provider: String(runner.provider ?? ''), service_model: String(runner.service_model ?? ''), service_provider: Stri` | 3 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 17 | Restore Runner State — Run Context | `service_provider` | `r.provider ?? ''), service_model: String(runner.service_model ?? ''), service_provider: String(runner.service_provider ?? ''), working_directory:` | 3 | 2 | `RMEMPTY` `String(x ?? '')`: empty string, no error |  |
| 18 | Restore Runner State — Run Context | `retry_delays` | `nner.max_retries ?? 3), retry_count: Number(runner.retry_count ?? 0), retry_delays: Array.isArray(runner.retry_delays) ? runner.retry_delays :` | 3 | 3 | `RMRETRY` not an array or invalid JSON: `[15, 30, 60, 120, 300]`, no error | Non-array: default. |
| 19 | Start New Session from Handoff | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 20 | Compact Session | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |
| 21 | Get Session Digest | `runner_url` | `={{ String($('Normalize Request').first().json.runner_url \|\| 'http://claude-runner:8700').replace(/\/+$/, '') + '/v1/` | 1 | 1 | `RMURL` missing or empty: `http://claude-runner:8700` |  |

Run Manager input keys that carry legacy values (not counted above):

| Input key | Normalize Request line | Undefined field |
|---|---|---|
| `poll_seconds` | 21 | `Math.max(1, Number(i.poll_seconds ?? 3))`: 3 |
| `max_polls` | 22 | `Math.max(1, Number(i.max_polls ?? 40))`: 40 |
| `max_retries` | 24 | `Math.max(0, Number(i.max_retries ?? 3))`: 3 |
| `think_policy` | 51 | `''` |
| `think_policy_from_task` | 52 | `0` |
| `think_off_after_seconds` | 53 | -1 |
| `think_off_after_steps` | 54 | -1 |

Behaviour codes for the Run Manager rows:

| Code | Run Manager rule (Normalize Request unless noted) |
|---|---|
| `RMSVC_M` | `service_model`: missing gives the run `model` (line 58); no model gives `''`. |
| `RMSVC_P` | `service_provider`: missing gives `provider` (line 59); no provider gives `anthropic` (line 57). |
| `RMURL` | `runner_url`: missing or empty gives `http://claude-runner:8700` (line 18); the request nodes apply the same default. |
| `RMRETRY` | `retry_delays`: not an array or invalid JSON gives `[15, 30, 60, 120, 300]`, silently (lines 26-45). |
| `RMEMPTY` | `String(x ?? '')` in Build Result, Build Persist State and Restore Runner State: empty string, no error. |

## Checks

- `ls scratchpad/live_workflow_export.json scratchpad/live_run_manager_export.json`: both exist.
- Count check (a Python script over the two exports, run with the reader rows above): for every name, the `grep -o -w` count equals the sum of `Occ` for that name. Result: all 42 names match in both exports.

| Name in code | `grep -o -w` workflow | Rows / occ workflow | `grep -o -w` Run Manager | Rows / occ Run Manager |
|---|---|---|---|---|
| `architect_max_polls` | 4 | 2 / 4 | 0 | 0 / 0 |
| `architect_max_provider_retries` | 3 | 2 / 3 | 0 | 0 / 0 |
| `architect_max_retries` | 1 | 1 / 1 | 0 | 0 / 0 |
| `architect_model` | 5 | 5 / 5 | 0 | 0 / 0 |
| `architect_poll_seconds` | 4 | 2 / 4 | 0 | 0 / 0 |
| `architect_provider` | 5 | 5 / 5 | 0 | 0 / 0 |
| `max_attempts_per_task` | 6 | 3 / 6 | 0 | 0 / 0 |
| `max_tasks` | 4 | 2 / 4 | 0 | 0 / 0 |
| `mission_roles_json` | 5 | 5 / 5 | 0 | 0 / 0 |
| `planner_model` | 6 | 6 / 6 | 0 | 0 / 0 |
| `planner_provider` | 6 | 6 / 6 | 0 | 0 / 0 |
| `retrieval_corpora_json` | 4 | 2 / 4 | 0 | 0 / 0 |
| `retrieval_enabled` | 7 | 5 / 7 | 0 | 0 / 0 |
| `retrieval_label` | 4 | 3 / 4 | 0 | 0 / 0 |
| `retrieval_reviewer_max_tokens` | 3 | 2 / 3 | 0 | 0 / 0 |
| `retrieval_timeout_ms` | 4 | 3 / 4 | 0 | 0 / 0 |
| `retrieval_url` | 4 | 3 / 4 | 0 | 0 / 0 |
| `retrieval_worker_max_tokens` | 3 | 2 / 3 | 0 | 0 / 0 |
| `retry_delays` | 9 | 5 / 9 | 10 | 4 / 10 |
| `retry_delays_json` | 4 | 1 / 4 | 0 | 0 / 0 |
| `reviewer_max_polls` | 5 | 3 / 5 | 0 | 0 / 0 |
| `reviewer_max_provider_retries` | 5 | 3 / 5 | 0 | 0 / 0 |
| `reviewer_max_retries` | 2 | 2 / 2 | 0 | 0 / 0 |
| `reviewer_model` | 12 | 7 / 12 | 0 | 0 / 0 |
| `reviewer_poll_seconds` | 5 | 3 / 5 | 0 | 0 / 0 |
| `reviewer_provider` | 12 | 7 / 12 | 0 | 0 / 0 |
| `run_manager_workflow_id` | 7 | 5 / 7 | 0 | 0 / 0 |
| `runner_url` | 18 | 10 / 18 | 8 | 7 / 8 |
| `service_model` | 10 | 5 / 10 | 12 | 5 / 12 |
| `service_provider` | 10 | 5 / 10 | 12 | 5 / 12 |
| `state_namespace` | 38 | 26 / 38 | 0 | 0 / 0 |
| `telegram_chat_id` | 14 | 13 / 14 | 0 | 0 / 0 |
| `worker_max_polls` | 4 | 2 / 4 | 0 | 0 / 0 |
| `worker_max_provider_retries` | 3 | 2 / 3 | 0 | 0 / 0 |
| `worker_max_retries` | 1 | 1 / 1 | 0 | 0 / 0 |
| `worker_model` | 13 | 8 / 13 | 0 | 0 / 0 |
| `worker_poll_seconds` | 4 | 2 / 4 | 0 | 0 / 0 |
| `worker_provider` | 13 | 8 / 13 | 0 | 0 / 0 |
| `worker_think_off_after_seconds` | 7 | 2 / 7 | 0 | 0 / 0 |
| `worker_think_off_after_steps` | 7 | 2 / 7 | 0 | 0 / 0 |
| `worker_think_policy` | 3 | 2 / 3 | 0 | 0 / 0 |
| `worker_think_policy_from_task` | 3 | 2 / 3 | 0 | 0 / 0 |

Rows per name are fewer than the grep count where one node mentions a name several times (`Occ`). Each name's row count and occurrence sum are shown above.

## Open points (UNKNOWN)

- Live values of every column, including the role defaults (`docs/config-roles-step1.md`, "Open risks").
- Whether an empty column arrives as `null` or as `''` from the Data Table node: decides whether `*_max_retries` gives 0 (`null`, `''`) or throws (undefined). Both behaviours are listed.
- `run_manager_workflow_id`: the code default `nhjwX1G7FiVTO2Ah` (Constants line 43) differs from the plan's example `6WE2ATGEDzUopBJH` (`docs/config-columns-plan.md:72`), which is also the id of the Run Manager export. Which id is live is UNKNOWN.
- `mission_roles_json`: the Mission node has no `roles_json` output, so the per-mission role override never reaches the run in this export (B19). Whether the live Mission node differs is UNKNOWN.
- `state_namespace` equals `mission_id` in live data: UNKNOWN.
- Whether the Run Manager is patched live: UNKNOWN (the export holds no reader of the hermes row).
- The workflow export is 9 days old. Re-check the line numbers if a newer export is staged.

## Blockers

None. Both staged exports were present.

## Ops readers (T001B)

Scope: `launch_mission.sh`, `roles_options_patch.py`, `roles_config_patch.py`, `features_config_patch.py`, `thinking_config_patch.py`, `retrieval_config_patch.py`, `engine_config_patch.py`, `overrides_config_patch.py`, `notify.py`, `pgdb.py`, `queue_watch.py`, `queue_board.py`, `queue_launcher.py`, `sync_board_queue.py`, `n8nq.sh`, `real_queue.sh`, `real_queue_board.sh`, `real_queue_rerun.sh`, `queue.md` (all in `data-tables/ops/`). Method: word-bounded grep for the 36 legacy names above (plus `mission_roles_json` and `mission_features_json`). 136 matching lines in these files. Not counted as readers: 119 `wf_*.json` workflow exports, 42 `_make_*.py` mission texts, `*.bak*` copies and logs; a whole-folder grep therefore reports 40 to 400 files per name.

Kinds: SQL = a query on the n8n database; anchor = text a patch searches for in the live workflow and replaces (the read it stands for is the pre-patch read); injected = JS a patch writes into the workflow (a live read after the patch); key = an object key or flat-field name, not a column read; comment = comment or docstring; text = a string in a message or a queue entry. Behaviour codes (B1 to B21) are those of the table above, because the ops code reads the same row fields.

Read-only, operator-owned: `real_queue.sh`, `real_queue_board.sh`, `real_queue_rerun.sh`, `queue.md`. Nothing in them was changed.

### SQL reads by column name: disposition (T004D)

Before T004D, these reads failed when the column was deleted. The scripts now read the JSON column (`roles_json` or `engine_json`) and select no legacy column.

| file:line | Column(s) | Disposition |
|---|---|---|
| `launch_mission.sh:16` | `worker_provider` | Fixed: SELECT `roles_json`, provider of `worker` attempt 1 (`worker."1"` `provider/model`). An absent entry gives an empty provider, so the local-llama health check on line 18 is skipped (see Operator action). |
| `launch_mission.sh:56` (was `:52`) | `run_manager_workflow_id` | Fixed: SELECT `engine_json`, key `run_manager_workflow_id`. An absent key gives an empty id; the export then fails inside the subshell at lines 55-67, which keeps its `|| echo` fallback. |
| `notify.py:65-67` | `telegram_chat_id` | Fixed: SELECT `engine_json`, key `telegram_chat_id`. An absent key gives no chat id, so `send()` returns False (it never raises, as before). |
| `real_queue.sh:149-150`, `:153` | `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`, `architect_model`, `architect_provider`, `max_attempts_per_task`, `service_model` | Operator action (read-only file, not changed) |
| `real_queue_board.sh:149-150`, `:153` | same as `real_queue.sh` | Operator action (not changed) |
| `real_queue_rerun.sh:96-97`, `:100` | same as `real_queue.sh` | Operator action (not changed) |

### Operator action

These files are operator-owned and were not changed. Their SQL writes and reads of the legacy role and engine columns must move to `roles_json` (`worker`, `reviewer`, `architect`, `service` with `provider/model` strings) and `engine_json` (`max_attempts_per_task`) before the columns are dropped:

- `real_queue.sh:149-150` and `real_queue_board.sh:149-150`: UPDATE of the `*_model` / `*_provider` columns, `max_attempts_per_task` and `max_tasks` on `id=5`; write the same values into `roles_json`, `engine_json` and `features_json` (`plan_no_truncate.max_tasks`) instead. The error handling of the script's `x()` helper was not read.
- `real_queue.sh:153` and `real_queue_board.sh:153`: SELECT of `worker_model`, `reviewer_model`, `architect_model`, `service_model`, `max_attempts_per_task`; read them from `roles_json` and `engine_json`.
- `real_queue_rerun.sh:96-97` and `:100`: the same UPDATE and SELECT.
- Before deploying the T004D change to the live database, check the live `hermes_config` row (`id=5`) holds `engine_json.telegram_chat_id`, `engine_json.run_manager_workflow_id` and a `roles_json` worker entry. Without them `notify.py` sends nothing and `launch_mission.sh` skips the local-llama check. The live row was not read for T004D.

### Other scripts in the Ops readers scope (T004D)

| file | Disposition |
|---|---|
| `pgdb.py` | Unchanged: `select *` (line 4) and the update of `enabled` (line 5); names no legacy column. |
| `n8nq.sh` | Unchanged: names no column. |
| `queue_watch.py`, `queue_launcher.py`, `sync_board_queue.py` | Unchanged: no legacy column name. |
| `queue_board.py` | Unchanged: line 134 is a comment. |
| `queue.md` | Unchanged: line 68 is the text of a queue entry (read-only, operator-owned). |

### Ops readers: JS injected or anchored by the patches, and other matches

| file:line | Column | Kind and read | Undefined / missing-column behaviour |
|---|---|---|---|
| `roles_options_patch.py:5` | (all) | comment | not a read |
| `roles_options_patch.py:48-50` | `architect_*`, `worker_*`, `reviewer_*` | injected `modelPair(...)` with `row.x_provider`, `row.x_model` | B3: `String(x ?? '')`; no throw; no `roles_json` entry gives `no model configured` |
| `roles_options_patch.py:51` | `service_provider`, `service_model` | injected `modelPair` | B3S: `''`, no check |
| `roles_config_patch.py:5` | (all) | docstring | not a read |
| `roles_config_patch.py:46` | `mission_roles_json` | marker string checked for idempotence | not a read |
| `roles_config_patch.py:48`, `:50` | `worker_think_off_after_steps` | anchor and the same read kept in the injected text | B16: -1 for undefined, null or empty |
| `roles_config_patch.py:52` | `mission_roles_json` | injected key; reads `mission.roles_json` | B19: `''` always (the Mission node has no `roles_json`) |
| `roles_config_patch.py:63-68` | `architect_*`, `worker_*`, `reviewer_*` (`legacy` map) | reads Constants fields `planner_*`, `worker_*`, `reviewer_*` | B3 (Constants value `''`); the pair is used only when both are set |
| `roles_config_patch.py:70` | `mission_roles_json` | reads Constants `mission_roles_json` | B19: `{}` |
| `roles_config_patch.py:88` | `runner_url` | reads Constants `runner_url` for the worker facts call | B12 at Constants; an empty value skips the call and `steps` stays null |
| `roles_config_patch.py:106`, `:110` | `worker_model`, `worker_provider` | anchor (regex) for the Constants read in the worker model and provider | B3 before the patch; replaced by `__cfg.model` and `__cfg.provider` (lines 122-123) |
| `roles_config_patch.py:114`, `:115` | `worker_model`, `worker_provider` | anchor | B3 before the patch; replaced by lines 122-123 |
| `roles_config_patch.py:125` | `worker_model`, `worker_provider` | anchor inside a `try/catch` that swallows the error | B3; the replacement (lines 129-130) has no `try`, so a missing model now throws |
| `roles_config_patch.py:135`, `:136` | `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider` | anchor of the review text | B3U: shows `?` |
| `roles_config_patch.py:139` | `reviewer_model`, `reviewer_provider` | anchor (`cfg.reviewer_*`) | B3; replaced by `__reviewer.model` and `__reviewer.provider` (line 140) |
| `roles_config_patch.py:198`, `:200` | `reviewer_model`, `reviewer_provider` | anchor (Constants read) | B3; replaced by `__cfg.model` and `__cfg.provider` (line 199 and 201) |
| `features_config_patch.py:5-7` | `max_tasks`, `mission_features_json` | docstring and comment | not a read |
| `features_config_patch.py:27` | `max_tasks` | anchor `num(row.max_tasks,'max_tasks',1)` | B1 before the patch |
| `features_config_patch.py:29`, `:36` | `mission_features_json` | injected `mission.mission_features_json` (the Mission node has no such output; `row.features_json` is JSON, not legacy) | UNKNOWN (B21, not traced) |
| `features_config_patch.py:34-35` | `max_tasks` | shortcut and upgrade comments | not a read |
| `features_config_patch.py:38` | `max_tasks` | key of the parsed features object | not a column read |
| `features_config_patch.py:42` | `max_tasks` | injected: the legacy read runs only when no features JSON sets `max_tasks` | B1: undefined, null or empty throws `Invalid max_tasks` |
| `thinking_config_patch.py:39` | `worker_think_policy` | injected `|| row.worker_think_policy` | B15: `''`; no throw |
| `thinking_config_patch.py:42` | `worker_think_policy` | anchor `String(row.worker_think_policy ?? '')` | B15 before the patch |
| `thinking_config_patch.py:43` | `worker_think_policy` | key with `__thinkPolicy`, not a column read | not a read |
| `thinking_config_patch.py:45-46` | `worker_think_policy_from_task` | anchor `Number(row.x ?? 0) || 0` | B15: `0` |
| `thinking_config_patch.py:49-50` | `worker_think_policy_from_task` | injected `__thinkValue('from_task', row.x ?? 0)` | B15: `0`; no throw |
| `thinking_config_patch.py:53-56` | `worker_think_off_after_seconds` | anchor with `=== null / undefined / ''` guards | B16: -1 before the patch |
| `thinking_config_patch.py:59-60` | `worker_think_off_after_seconds` | injected `__thinkNum(...)` (line 33) | B16: -1; no throw |
| `thinking_config_patch.py:63-66` | `worker_think_off_after_steps` | anchor with the same guards | B16: -1 before the patch |
| `thinking_config_patch.py:69-70` | `worker_think_off_after_steps` | injected `__thinkNum(...)` | B16: -1; no throw |
| `retrieval_config_patch.py:31` | `retrieval_corpora_json` | anchor `parseCorpora(row.retrieval_corpora_json)` | B7 before the patch |
| `retrieval_config_patch.py:32` | `retrieval_corpora_json` | flat field `__retrievalFlat.retrieval_corpora_json` | not a column read |
| `retrieval_config_patch.py:34` | `retrieval_url` | anchor (`URL_LINE`, removed by the patch) | B6 before the patch |
| `retrieval_config_patch.py:38-39` | `retrieval_enabled` | anchor (`REQUESTED_LINE`, removed) | B5 before the patch |
| `retrieval_config_patch.py:41-44`, `:46`, `:50`, `:55`, `:58` | `retrieval_*` | output keys of the return object, replaced by `__retrievalFlat` fields | not a column read |
| `retrieval_config_patch.py:45` | `retrieval_timeout_ms` | anchor `optNum(row.retrieval_timeout_ms, 120000, 1000)` | B8 before the patch |
| `retrieval_config_patch.py:48` | `retrieval_worker_max_tokens` | anchor `optNum(..., 6000, 100)` | B9 before the patch |
| `retrieval_config_patch.py:52` | `retrieval_reviewer_max_tokens` | anchor `optNum(..., 5000, 100)` | B10 before the patch |
| `retrieval_config_patch.py:57` | `retrieval_label` | anchor `String(row.retrieval_label ?? '').trim()` | B11 before the patch |
| `retrieval_config_patch.py:93-94` | `retrieval_enabled` | injected defaults for `flatten(row.retrieval_json, {...})` | B5: `false` |
| `retrieval_config_patch.py:95` | `retrieval_url` | injected default | B6: `http://ahawr-retrieval:8500` |
| `retrieval_config_patch.py:96` | `retrieval_corpora_json` | injected `parseCorpora(row.retrieval_corpora_json)` | B7 for empty (`[]`); undefined: UNKNOWN (`parseCorpora` is not in this file) |
| `retrieval_config_patch.py:97` | `retrieval_timeout_ms` | injected `optNum(...)` | B8: 120000 |
| `retrieval_config_patch.py:98`, `:99`, `:100` | `retrieval_worker_max_tokens`, `retrieval_reviewer_max_tokens`, `retrieval_label` | injected `optNum` / `String(... ?? '')` | B9, B10, B11 |
| `engine_config_patch.py:6` | (all) | docstring | not a read |
| `engine_config_patch.py:33` | `telegram_chat_id` | shortcut comment | not a read |
| `engine_config_patch.py:38` | `max_attempts_per_task` | anchor `num(row.max_attempts_per_task,...)` | B1 before the patch |
| `engine_config_patch.py:39` | `max_attempts_per_task` | injected `__engine.max_attempts_per_task` (value from the core) | UNKNOWN: the read is in `board_engine.js` (`__engineCore.flatten`, line 31 passes the whole `row`) |
| `engine_config_patch.py:42`, `:44` | `retry_delays_json` | anchor `String(row.retry_delays_json ?? '')` and `JSON.parse` | B4: empty gives the default; invalid JSON throws |
| `engine_config_patch.py:47`, `:50` | `retry_delays_json` | error message text | not a read |
| `engine_config_patch.py:56` | `runner_url` | anchor `String(row.runner_url ?? '')` | B12 before the patch |
| `engine_config_patch.py:59` | `runner_url` | injected `__engine.runner_url` (core) | UNKNOWN (core) |
| `engine_config_patch.py:61` | `run_manager_workflow_id` | anchor, default `'nhjwX1G7FiVTO2Ah'` | B13 before the patch |
| `engine_config_patch.py:64` | `run_manager_workflow_id` | injected `__engine.run_manager_workflow_id` (core) | UNKNOWN (core) |
| `engine_config_patch.py:65` | `telegram_chat_id` | anchor `String(row.telegram_chat_id ?? '').trim()` | B14 before the patch |
| `engine_config_patch.py:66` | `telegram_chat_id` | injected `__engine.telegram_chat_id` (core) | UNKNOWN (core) |
| `overrides_config_patch.py:31`, `:36` | `retrieval_corpora_json` | anchor (`CORPORA_OLD`) and injected `__override('retrieval_corpora', ... parseCorpora(row.retrieval_corpora_json))` | B7 for empty; undefined: UNKNOWN |
| `overrides_config_patch.py:40-41`, `:42-43` | `retrieval_enabled` | anchor and injected `__override('retrieval_enabled', row.retrieval_enabled ...)` | B5: `false` |
| `overrides_config_patch.py:46-47`, `:48-49` | `retrieval_url` | anchor and injected `__override` | B6 |
| `overrides_config_patch.py:53`, `:54-55` | `retrieval_timeout_ms` | anchor and injected `__override` | B8 |
| `overrides_config_patch.py:58`, `:59-60` | `retrieval_worker_max_tokens` | anchor and injected `__override` | B9 |
| `overrides_config_patch.py:63`, `:64-65` | `retrieval_reviewer_max_tokens` | anchor and injected `__override` | B10 |
| `overrides_config_patch.py:68`, `:69-70` | `retrieval_label` | anchor and injected `__override` | B11 |
| `notify.py:5` | `telegram_chat_id` | docstring | not a read |
| `queue_board.py:134` | `state_namespace` | comment | not a read |
| `queue.md:68` | `worker_model`, `worker_provider` | text of a queue entry (read-only, operator-owned) | not a read |
| `real_queue.sh:68`, `real_queue_board.sh:68`, `real_queue_rerun.sh:20` | `max_attempts_per_task` | comment after a shell variable | not a read |

Overrides ordering: `overrides_config_patch.py:31`, `:40`, `:46` anchor text that `retrieval_config_patch.py` (lines 31, 34-39, 45) replaces. If the retrieval patch runs first, the overrides anchors are no longer found; the order of the two patches was not checked.

Files with no legacy-name match: `pgdb.py`, `queue_watch.py`, `queue_launcher.py`, `sync_board_queue.py`, `n8nq.sh`.

Open risks: the `engine_config_patch.py` and `features_config_patch.py` reads of the Mission override (`mission_features_json`) and the core reads (`board_engine.js`) were not traced (UNKNOWN); the `real_queue*.sh` error handling was not read.

## Runner and board readers (T001C)

Scope: `claude-runner/src/claude_runner`. Grep: `grep -rnwE` with the 35 distinct names of the Legacy columns table (`retrieval_corpora_json` is in two rows of that table, so the earlier "38 names" was wrong), `grep -v codex_`: 303 lines. No `codex_*.py` file matches. Files of the scope list with no match: `roles_config.py`, `config.py`, `runs.py`, `handoff.py`, `board_mission_save.js`, `board_roles.js` (their legacy reads are built from role names, see the cores below).

Missing-field behaviour, by kind of read:

- SQL with the column named in SELECT, INSERT or UPDATE: a deleted column raises a SQL error (psycopg undefined column) and the whole call fails. Not a None.
- `SELECT *` (`hermes_config`, `board_pg.py:559-568`): a deleted column is absent from the dict; readers use `.get`, so the value is None.
- Python core `roles_config.legacy_role` (`roles_config.py:274`) and `flatten` (`:388`): `columns.get(...)`, so a missing column is None (no KeyError); a role with no model in `roles_json` and no legacy pair raises `ValueError("no model configured ...")` in `_pick` (`:363`).
- Python core `board_engine.flatten` (`board_engine.py:310-321`): `legacy_row.get(column)` then `_normalize`; None gives the `ENGINE_DEFAULTS` value.
- JS core `board_roles.js` `legacyRole` (`:339`) and `pickModel` (`:370`): `columns[name]` is `undefined`, the pair is skipped; `pickOrThrow` (`:391`) throws `no model configured`.
- JS core `board_engine.js` `flatten` (`:304-317`): `legacyRow[column]` is `undefined`, `normalize` gives the default.

Cores and cases file (paths opened and checked):

- Python core: `claude-runner/src/claude_runner/roles_config.py` (`legacy_role` :274, `resolve` :285, `flatten` :388), and `claude-runner/src/claude_runner/board_engine.py` (`LEGACY_COLUMNS` :17, `flatten` :310).
- JS core: `claude-runner/src/claude_runner/board_roles.js` (`legacyRole` :339, `pickModel` :370, `resolveRole` :419, `flatten` :586), and `claude-runner/src/claude_runner/board_engine.js` (`flatten` :304).
- Shared cases file: `claude-runner/tests/roles_cases.json` (exists; top-level keys `description`, `providers`, `validation`, `resolve`, `flatten`; 53 `legacy` blocks). Consumers: `claude-runner/tests/test_roles_config.py:25` and `claude-runner/tests/board_roles.test.js:9`.


Split: 129 rows are in the eight cover files (`api.py`, `board_api.py`, `board_edit_win.js`, `board_mission.js`, `board_pg.py`, `board_ready.py`, `board_stats.py`, `board_task.js`) and 174 rows are in the other 21 files. The table lists every grep line: one row per `file:line`, out-of-cover files first, in the order the reviewer gave. `UNKNOWN` is not used in this section: each row was resolved by opening the code at the cited lines.

| file:line | Column(s) | Kind | Missing-field behaviour |
|---|---|---|---|
| `board_engine.py:11` | `max_attempts_per_task` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.py:13` | `runner_url` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.py:14` | `run_manager_workflow_id` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.py:15` | `telegram_chat_id` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.py:18` | `max_attempts_per_task` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.py:19` | `retry_delays_json` | name in a list, map or key table | not a read; a missing column is not checked here; key maps to retry_delays_json; flatten reads it with .get |
| `board_engine.py:20` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.py:21` | `run_manager_workflow_id` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.py:22` | `telegram_chat_id` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.py:200` | `runner_url` | branch on the value | absent or empty takes the else branch; runner_url trailing slashes stripped only when the value is set |
| `board_engine.py:272` | `max_attempts_per_task` | branch on the value | absent or empty takes the else branch; _normalize returns None for None, so the default applies |
| `board_engine.js:5` | `max_attempts_per_task` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.js:7` | `runner_url` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.js:8` | `run_manager_workflow_id` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.js:9` | `telegram_chat_id` | literal default | not a read; the value the core uses when the key is unset |
| `board_engine.js:12` | `max_attempts_per_task` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.js:13` | `retry_delays_json` | name in a list, map or key table | not a read; a missing column is not checked here; key maps to retry_delays_json |
| `board_engine.js:14` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.js:15` | `run_manager_workflow_id` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.js:16` | `telegram_chat_id` | name in a list, map or key table | not a read; a missing column is not checked here |
| `board_engine.js:190` | `runner_url` | branch on the value | absent or empty takes the else branch; trailing slashes stripped only when the value is set |
| `board_engine.js:266` | `max_attempts_per_task` | branch on the value | absent or empty takes the else branch; checkAttempts runs on the value; null or undefined is normalised to null |
| `board_plan.py:76` | `worker_model` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry; a running mission may change it; not read from the database here |
| `board_plan.py:77` | `worker_provider` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:78` | `reviewer_model` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:79` | `reviewer_provider` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:80` | `service_model` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:81` | `service_provider` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:82` | `worker_max_polls` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:83` | `reviewer_max_polls` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:84` | `architect_max_polls` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:85` | `worker_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:86` | `reviewer_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:87` | `architect_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:88` | `worker_max_retries` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:89` | `reviewer_max_retries` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:90` | `architect_max_retries` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:92` | `max_attempts_per_task` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:93` | `max_tasks` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:94` | `worker_think_policy` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:95` | `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:96` | `worker_think_off_after_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:97` | `worker_think_off_after_steps` | name in a list, map or key table | not a read; a missing column is not checked here; MIDRUN_CONFIG_FIELDS entry |
| `board_plan.py:106` | `state_namespace` | name in a list, map or key table | not a read; a missing column is not checked here; NON_EDITABLE_CONFIG_FIELDS key; rejected while the mission runs (:509-510) |
| `overrides_config.py:15` | `retrieval_enabled` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:16` | `retrieval_url` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:18` | `retrieval_timeout_ms` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:19` | `retrieval_worker_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:20` | `retrieval_reviewer_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:21` | `retrieval_label` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `overrides_config.py:24` | `retrieval_enabled` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:25` | `retrieval_url` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:27` | `retrieval_timeout_ms` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:28` | `retrieval_worker_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:29` | `retrieval_reviewer_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:30` | `retrieval_label` | literal default | not a read; the value the core uses when the key is unset |
| `overrides_config.py:33` | `retrieval_corpora_json` | name in a list, map or key table | not a read; a missing column is not checked here; LEGACY_COLUMNS; _legacy_value applies json.loads; invalid JSON raises ValueError in resolve (:122-129) |
| `overrides_config.py:139` | `state_namespace` | branch on the value | absent or empty takes the else branch; state key is the namespace or, when it is None or empty, the mission id |
| `overrides_config.py:140` | `state_namespace` | branch on the value | absent or empty takes the else branch; state key is the namespace or, when it is None or empty, the mission id |
| `retrieval_config.py:11` | `retrieval_enabled` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:12` | `retrieval_url` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:13` | `retrieval_corpora_json` | literal default | not a read; the value the core uses when the key is unset; list default, copied with deepcopy in flatten |
| `retrieval_config.py:14` | `retrieval_timeout_ms` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:15` | `retrieval_worker_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:16` | `retrieval_reviewer_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:17` | `retrieval_label` | literal default | not a read; the value the core uses when the key is unset |
| `retrieval_config.py:20` | `retrieval_enabled` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key; _is_set treats None and "" as unset (:94-95) |
| `retrieval_config.py:21` | `retrieval_url` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `retrieval_config.py:22` | `retrieval_corpora_json` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `retrieval_config.py:23` | `retrieval_timeout_ms` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `retrieval_config.py:24` | `retrieval_worker_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `retrieval_config.py:25` | `retrieval_reviewer_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `retrieval_config.py:26` | `retrieval_label` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `thinking_config.py:10` | `worker_think_policy` | literal default | not a read; the value the core uses when the key is unset |
| `thinking_config.py:11` | `worker_think_policy_from_task` | literal default | not a read; the value the core uses when the key is unset |
| `thinking_config.py:12` | `worker_think_off_after_seconds` | literal default | not a read; the value the core uses when the key is unset |
| `thinking_config.py:13` | `worker_think_off_after_steps` | literal default | not a read; the value the core uses when the key is unset |
| `thinking_config.py:16` | `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key; _is_set treats None and "" as unset (:73-74) |
| `thinking_config.py:17` | `worker_think_off_after_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `thinking_config.py:18` | `worker_think_off_after_steps` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_overrides.js:8` | `retrieval_enabled` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:9` | `retrieval_url` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:11` | `retrieval_timeout_ms` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:12` | `retrieval_worker_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:13` | `retrieval_reviewer_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:14` | `retrieval_label` | name in a list, map or key table | not a read; a missing column is not checked here; RETRIEVAL_NAMES map |
| `board_overrides.js:17` | `retrieval_enabled` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:18` | `retrieval_url` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:20` | `retrieval_timeout_ms` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:21` | `retrieval_worker_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:22` | `retrieval_reviewer_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:23` | `retrieval_label` | literal default | not a read; the value the core uses when the key is unset |
| `board_overrides.js:25` | `retrieval_corpora_json` | name in a list, map or key table | not a read; a missing column is not checked here; LEGACY_COLUMN_OVERRIDES; legacyValue applies JSON.parse to the corpora; isSet (:36-38) treats null as unset |
| `board_retrieval.js:4` | `retrieval_enabled` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:5` | `retrieval_url` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:6` | `retrieval_corpora_json` | literal default | not a read; the value the core uses when the key is unset; list default, cloned with structuredClone in flatten |
| `board_retrieval.js:7` | `retrieval_timeout_ms` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:8` | `retrieval_worker_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:9` | `retrieval_reviewer_max_tokens` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:10` | `retrieval_label` | literal default | not a read; the value the core uses when the key is unset |
| `board_retrieval.js:13` | `retrieval_enabled` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key; isSet (:86-88) treats null as unset |
| `board_retrieval.js:14` | `retrieval_url` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_retrieval.js:15` | `retrieval_corpora_json` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_retrieval.js:16` | `retrieval_timeout_ms` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_retrieval.js:17` | `retrieval_worker_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_retrieval.js:18` | `retrieval_reviewer_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_retrieval.js:19` | `retrieval_label` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `dashboard.py:72` | `runner_url` | literal default | not a read; the value the core uses when the key is unset; DashboardSettings.runner_url dataclass default |
| `dashboard.py:88` | `runner_url` | dict .get or configured-value read | absent gives None; the caller default applies; env.get with the dataclass default when the variable is unset; no column read |
| `dashboard.py:122` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; settings field (str, always set by the default at :72) |
| `dashboard.py:161` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; settings field passed to RunnerSource |
| `dashboard.py:162` | `runner_url` | branch on the value | absent or empty takes the else branch; empty value skips the runner branch |
| `dashboard.py:239` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; settings field (str) |
| `dashboard.py:257` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; settings field passed to RemoteRunManager |
| `dashboard.py:435` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; guarded by ``if found``; no mission gives an empty set |
| `board.py:71` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; Mission field; required in the pydantic model, so a Mission without it fails validation |
| `board.py:206` | `state_namespace` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `board.py:259` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; live lookup; the comprehension keeps a mission only when its live row is not None |
| `board.py:287` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; live lookup; None gives status None (not active) |
| `board.py:339` | `state_namespace` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `board.py:798` | `state_namespace` | name in a list, map or key table | not a read; a missing column is not checked here; fixed names are skipped by the update loop (:800), so the value is never written |
| `board.css:495` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; CSS attribute selector |
| `board_features.js:4` | `max_tasks` | literal default | not a read; the value the core uses when the key is unset; plan_no_truncate default |
| `board_features.js:9` | `max_tasks` | literal default | not a read; the value the core uses when the key is unset; PARAM_MINIMUMS entry |
| `board_features.js:79` | `max_tasks` | JS property read | absent gives undefined, treated as unset; default or dash shown; destructured from features, which is merged with FEATURE_DEFAULTS first, so defined |
| `board_features.js:82` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; error message text |
| `board_features.js:102` | `max_tasks` | JS property read | absent gives undefined, treated as unset; default or dash shown; flatten; defined by the merge with FEATURE_DEFAULTS |
| `board_i18n.js:123` | `runner_url` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:124` | `run_manager_workflow_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:125` | `telegram_chat_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:393` | `state_namespace` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:402` | `worker_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:403` | `worker_provider` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:404` | `reviewer_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:405` | `reviewer_provider` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:406` | `architect_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:407` | `architect_provider` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:408` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:409` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:410` | `worker_think_policy_from_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n.js:539` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:34` | `state_namespace` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:37` | `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`, `architect_model`, `architect_provider`, `max_tasks`, `worker_think_policy`, `worker_think_policy_from_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:50` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:63` | `runner_url` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:64` | `run_manager_workflow_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_en.json:65` | `telegram_chat_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:34` | `state_namespace` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:37` | `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`, `architect_model`, `architect_provider`, `max_tasks`, `worker_think_policy`, `worker_think_policy_from_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:50` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:63` | `runner_url` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:64` | `run_manager_workflow_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_i18n_ru.json:65` | `telegram_chat_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_tasks.js:92` | `max_attempts_per_task` | JS property read | absent gives undefined, treated as unset; default or dash shown; stats null gives a dash |
| `board_think_ui.js:10` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; comment |
| `board_think_ui.js:25` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; comment |
| `board_think_ui.js:326` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; comment |
| `board_think_ui.js:343` | `worker_think_policy` | JS property read | absent gives undefined, treated as unset; default or dash shown; policyText gives "" for undefined (:96-104) |
| `board_thinking.js:4` | `worker_think_policy` | literal default | not a read; the value the core uses when the key is unset |
| `board_thinking.js:5` | `worker_think_policy_from_task` | literal default | not a read; the value the core uses when the key is unset |
| `board_thinking.js:6` | `worker_think_off_after_seconds` | literal default | not a read; the value the core uses when the key is unset |
| `board_thinking.js:7` | `worker_think_off_after_steps` | literal default | not a read; the value the core uses when the key is unset |
| `board_thinking.js:10` | `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key; isSet (:63-65) treats null and "" as unset |
| `board_thinking.js:11` | `worker_think_off_after_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `board_thinking.js:12` | `worker_think_off_after_steps` | name in a list, map or key table | not a read; a missing column is not checked here; mapping key |
| `features_config.py:8` | `max_tasks` | literal default | not a read; the value the core uses when the key is unset; plan_no_truncate default |
| `features_config.py:18` | `max_tasks` | literal default | not a read; the value the core uses when the key is unset; PARAM_MINIMUMS entry |
| `features_config.py:110` | `max_tasks` | Python [] index | absent gives KeyError; keys come from the FEATURE_DEFAULTS merge, so always present |
| `features_config.py:112` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; error message text |
| `features_config.py:113` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; error message text |
| `features_config.py:142` | `max_tasks` | Python [] index | absent gives KeyError; flatten; keys always present (merge) |
| `mcp_board.py:183` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; task_views; [] when absent (board.py:306-313) |
| `qa_fixture.py:64` | `state_namespace` | Python [] index | fixed: absent gives mission_id through overrides_config.resolve_state_key, as the core does; tests in test_qa_fixture.py (state_namespace) |
| `think_policy.py:2` | `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `api.py:394` | `worker_think_policy` | HTTP body field or dict key | body default from RunBody; absent gives that default; body field RunBody.think_policy (default "" at api.py:164) |
| `api.py:395` | `worker_think_policy_from_task` | HTTP body field or dict key | body default from RunBody; absent gives that default; body field RunBody.think_policy_from_task (default 0 at api.py:165) |
| `api.py:396` | `worker_think_off_after_seconds` | HTTP body field or dict key | body default from RunBody; absent gives that default; body field RunBody.think_off_after_seconds (default -1) |
| `api.py:397` | `worker_think_off_after_steps` | HTTP body field or dict key | body default from RunBody; absent gives that default; body field RunBody.think_off_after_steps (default -1) |
| `api.py:410` | `worker_think_policy` | Python [] index | absent gives KeyError; flatten returns all four keys, so no KeyError |
| `api.py:411` | `worker_think_policy_from_task` | Python [] index | absent gives KeyError; flatten returns all four keys, so no KeyError |
| `api.py:412` | `worker_think_off_after_seconds` | Python [] index | absent gives KeyError; flatten returns all four keys, so no KeyError |
| `api.py:413` | `worker_think_off_after_steps` | Python [] index | absent gives KeyError; flatten returns all four keys, so no KeyError |
| `board_api.py:263` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; _state_key_of: KeyError at :262 when the mission is absent |
| `board_api.py:499` | `architect_provider`, `architect_model`, `worker_provider`, `worker_model`, `reviewer_provider` | name in a list, map or key table | not a read; a missing column is not checked here; _DEFAULT_KEYS entry |
| `board_api.py:500` | `reviewer_model`, `max_tasks`, `max_attempts_per_task`, `worker_think_policy`, `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; _DEFAULT_KEYS entry |
| `board_api.py:527` | `max_attempts_per_task` | dict .get or configured-value read | absent gives None; the caller default applies; configured_value on the row; a ValueError here is caught by the caller's Exception handler (:516-517) |
| `board_api.py:528` | `max_attempts_per_task` | name in a list, map or key table | not a read; a missing column is not checked here; dict key written with the configured value; None stays None |
| `board_api.py:715` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; get_live_state; None gives live_state null |
| `board_api.py:719` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; task_views; [] when the live row is None (board.py:306-313) |
| `board_api.py:807` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; dict pop with a None default; no error when absent |
| `board_api.py:1127` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; get_live_state; None raises KeyError at :1129 |
| `board_api.py:1132` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; upsert after the :1127 check; not reached when the live row is None |
| `board_api.py:1462` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; mission_card_stats; falls back to the key when no mission matches (board_stats.py:241-249) |
| `board_api.py:1472` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; task_views; [] when absent |
| `board_api.py:1475` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; task_stats; KeyError at :1474 when the task is absent |
| `board_api.py:1484` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; timeline; _mission_run_ids handles a None live row (board_stats.py:39-50) |
| `board_api.py:1507` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; assigned to state_key for the cached figures; None live row is handled downstream |
| `board_edit_win.js:25` | `worker_model`, `worker_provider`, `reviewer_model` | name in a list, map or key table | not a read; a missing column is not checked here; settings tab keys; a missing value shows a dash (show :140-145) |
| `board_edit_win.js:26` | `reviewer_provider`, `architect_model`, `architect_provider`, `max_tasks` | name in a list, map or key table | not a read; a missing column is not checked here; settings tab keys; a missing value shows a dash |
| `board_edit_win.js:30` | `worker_model`, `worker_provider` | name in a list, map or key table | not a read; a missing column is not checked here; worker row; eff :153-158 |
| `board_edit_win.js:31` | `reviewer_model`, `reviewer_provider` | name in a list, map or key table | not a read; a missing column is not checked here; reviewer row; eff :153-158 |
| `board_edit_win.js:32` | `architect_model`, `architect_provider` | name in a list, map or key table | not a read; a missing column is not checked here; architect row; eff :153-158 |
| `board_edit_win.js:36` | `max_attempts_per_task`, `runner_url`, `run_manager_workflow_id`, `telegram_chat_id` | name in a list, map or key table | not a read; a missing column is not checked here; ENGINE_KEYS list |
| `board_edit_win.js:37` | `telegram_chat_id` | name in a list, map or key table | not a read; a missing column is not checked here; ENGINE_OVERRIDABLE filter |
| `board_edit_win.js:39` | `max_attempts_per_task` | name in a list, map or key table | not a read; a missing column is not checked here; label map key |
| `board_edit_win.js:41` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here; label map key |
| `board_edit_win.js:42` | `run_manager_workflow_id` | name in a list, map or key table | not a read; a missing column is not checked here; label map key |
| `board_edit_win.js:43` | `telegram_chat_id` | name in a list, map or key table | not a read; a missing column is not checked here; label map key; telegram_chat_id is read-only |
| `board_edit_win.js:63` | `max_tasks` | name in a list, map or key table | not a read; a missing column is not checked here; feature field list |
| `board_edit_win.js:76` | `max_tasks` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; i18n key |
| `board_edit_win.js:99` | `telegram_chat_id` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; comment |
| `board_edit_win.js:161` | `worker_model`, `worker_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; eff value; a missing value shows a dash |
| `board_edit_win.js:162` | `reviewer_model`, `reviewer_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; eff value; a missing value shows a dash |
| `board_edit_win.js:163` | `architect_model`, `architect_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; eff value; a missing value shows a dash |
| `board_edit_win.js:668` | `telegram_chat_id` | branch on the value | absent or empty takes the else branch; read-only key: a falsy value shows "not set" (engineShown :666-670) |
| `board_edit_win.js:688` | `max_attempts_per_task` | branch on the value | absent or empty takes the else branch; input type choice; no read |
| `board_edit_win.js:890` | `max_attempts_per_task` | branch on the value | absent or empty takes the else branch; a non-finite number stays text (Number.isFinite at :890) |
| `board_edit_win.js:1022` | `max_tasks` | name in a list, map or key table | not a read; a missing column is not checked here; move(); no-op when the row is missing (:955-958) |
| `board_mission.js:22` | `worker_model`, `worker_think_policy` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; comment |
| `board_mission.js:52` | `worker_model`, `worker_provider` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:53` | `reviewer_model`, `reviewer_provider` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:54` | `service_model`, `service_provider` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:55` | `worker_max_polls`, `reviewer_max_polls`, `architect_max_polls` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:56` | `worker_poll_seconds`, `reviewer_poll_seconds`, `architect_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:57` | `worker_max_retries`, `reviewer_max_retries`, `architect_max_retries` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:58` | `max_attempts_per_task`, `max_tasks` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:59` | `worker_think_policy`, `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:60` | `worker_think_off_after_seconds`, `worker_think_off_after_steps` | name in a list, map or key table | not a read; a missing column is not checked here; editable field list |
| `board_mission.js:68` | `state_namespace` | name in a list, map or key table | not a read; a missing column is not checked here; locked-field label key for state_namespace |
| `board_mission.js:85` | `worker_model` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field (fmtJson :121-127) |
| `board_mission.js:86` | `worker_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:87` | `reviewer_model` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:88` | `reviewer_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:89` | `architect_model` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:90` | `architect_provider` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:91` | `max_tasks` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:92` | `worker_think_policy` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:93` | `worker_think_policy_from_task` | JS property read | absent gives undefined, treated as unset; default or dash shown; form row; an undefined value gives an empty field |
| `board_mission.js:198` | `max_attempts_per_task` | branch on the value | absent or empty takes the else branch; fallback key for attempts_per_task |
| `board_mission.js:401` | `max_attempts_per_task` | JS property read | absent gives undefined, treated as unset; default or dash shown; statsText; no suffix when the value is null |
| `board_pg.py:308` | `retrieval_corpora_json` | row value after a SELECT that names the column | the SELECT fails first with a SQL error; no KeyError; row value of the SELECT at :437-440; a deleted column fails the SELECT first |
| `board_pg.py:319` | `retrieval_corpora_json` | row value after a SELECT that names the column | the SELECT fails first with a SQL error; no KeyError; fallback path; the same SELECT (:466-469) fails first |
| `board_pg.py:437` | `state_namespace` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; SELECT list; a deleted column raises a SQL error |
| `board_pg.py:439` | `retrieval_corpora_json` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; SELECT list; a deleted column raises a SQL error |
| `board_pg.py:448` | `state_namespace` | row value after a SELECT that names the column | the SELECT fails first with a SQL error; no KeyError; row value of the SELECT at :437-440 |
| `board_pg.py:466` | `state_namespace` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; SELECT list; a deleted column raises a SQL error |
| `board_pg.py:468` | `retrieval_corpora_json` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; SELECT list; a deleted column raises a SQL error |
| `board_pg.py:477` | `state_namespace` | row value after a SELECT that names the column | the SELECT fails first with a SQL error; no KeyError; row value of the SELECT at :466-469 |
| `board_pg.py:713` | `state_namespace` | name in a list, map or key table | not a read; a missing column is not checked here; known_mission_cols allowlist of the mission UPDATE; a listed dropped column gives a SQL error |
| `board_pg.py:847` | `architect_provider`, `architect_model` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist (:846-862); a listed dropped column is sent to UPDATE (:868-872) and fails |
| `board_pg.py:848` | `worker_provider`, `worker_model`, `reviewer_provider` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:849` | `reviewer_model`, `service_provider`, `service_model` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:850` | `max_tasks`, `max_attempts_per_task`, `architect_max_polls` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:851` | `worker_max_polls`, `reviewer_max_polls`, `architect_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:852` | `worker_poll_seconds`, `reviewer_poll_seconds` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:853` | `architect_max_retries`, `worker_max_retries`, `reviewer_max_retries` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:854` | `retry_delays_json`, `retrieval_enabled`, `retrieval_url` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:855` | `retrieval_corpora_json`, `retrieval_worker_max_tokens` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:856` | `retrieval_reviewer_max_tokens`, `retrieval_timeout_ms` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:857` | `retrieval_label`, `runner_url`, `run_manager_workflow_id` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:858` | `worker_think_policy`, `worker_think_policy_from_task` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:859` | `worker_think_off_after_seconds`, `worker_think_off_after_steps` | name in a list, map or key table | not a read; a missing column is not checked here; known_hermes_cols allowlist |
| `board_pg.py:1050` | `state_namespace` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; INSERT column list; a deleted column raises a SQL error |
| `board_pg.py:1052` | `retrieval_corpora_json` | SQL column list | a deleted column raises a SQL error for the whole statement; no None; INSERT column list; a deleted column raises a SQL error |
| `board_pg.py:1057` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; INSERT value from the Mission model |
| `board_ready.py:132` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here; check_runner_health parameter |
| `board_ready.py:135` | `runner_url` | branch on the value | absent or empty takes the else branch; empty URL returns a warning item (:135-136) |
| `board_ready.py:139` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; parameter passed to RunnerSource |
| `board_ready.py:149` | `runner_url` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; message text |
| `board_ready.py:172` | `worker_model` | name in a list, map or key table | not a read; a missing column is not checked here; check_llama_model parameter |
| `board_ready.py:181` | `worker_model` | branch on the value | absent or empty takes the else branch; empty model returns a warning item (:181-182) |
| `board_ready.py:182` | `worker_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; message text |
| `board_ready.py:197` | `worker_model` | branch on the value | absent or empty takes the else branch; compared with the model ids llama-server reports; not a column read |
| `board_ready.py:203` | `worker_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; message text |
| `board_ready.py:205` | `worker_model` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; parameter passed to ctx_from_models |
| `board_ready.py:209` | `worker_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; message text |
| `board_ready.py:212` | `worker_model` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; message text |
| `board_ready.py:382` | `runner_url` | literal default | not a read; the value the core uses when the key is unset; local variable, empty string |
| `board_ready.py:386` | `worker_model` | literal default | not a read; the value the core uses when the key is unset; local variable, empty string |
| `board_ready.py:388` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here; local variable from the local provider's env (not a column read) |
| `board_ready.py:389` | `runner_url` | name in a list, map or key table | not a read; a missing column is not checked here; local variable copied from the env value |
| `board_ready.py:393` | `runner_url` | branch on the value | absent or empty takes the else branch; env fallback when the variable is empty; not a column read |
| `board_ready.py:405` | `worker_model` | Python [] index | absent gives KeyError; flatten always returns the key; str(... or "") gives "" for None |
| `board_ready.py:406` | `runner_url`, `worker_model` | name in a list, map or key table | not a read; a missing column is not checked here; returned tuple member |
| `board_ready.py:421` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; get_live_state; None gives status None (not active) |
| `board_ready.py:428` | `runner_url`, `worker_model` | name in a list, map or key table | not a read; a missing column is not checked here; tuple unpacking of _service_urls |
| `board_ready.py:429` | `runner_url` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; runner_url passed to check_runner_health; empty gives a warning (:135) |
| `board_ready.py:431` | `worker_model` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; worker_model passed to check_llama_model; empty gives a warning (:181) |
| `board_ready.py:576` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; Mission field in a comprehension; required |
| `board_ready.py:712` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; get_live_state; the None case is checked at :713 |
| `board_ready.py:727` | `state_namespace` | name in a list, map or key table | not a read; a missing column is not checked here; keyword argument of the new Mission; required field |
| `board_stats.py:7` | `max_attempts_per_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `board_stats.py:197` | `max_attempts_per_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `board_stats.py:202` | `max_attempts_per_task` | dict .get or configured-value read | absent gives None; the caller default applies; configured_value on the hermes config dict; ValueError caught at :203 |
| `board_stats.py:247` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; next(..., None); no match falls back to the key (:249) |
| `board_stats.py:305` | `max_attempts_per_task` | dict .get or configured-value read | absent gives None; the caller default applies; _max_attempts: the configured value or None; ValueError caught at :203 |
| `board_stats.py:365` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; next(..., None); no match falls back to the key |
| `board_stats.py:407` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; next(..., None); no match falls back to the key |
| `board_stats.py:427` | `max_attempts_per_task` | display text, i18n key, selector, comment or docstring | not a read; no runtime behaviour; docstring |
| `board_stats.py:431` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; next(..., None); no match falls back to the key |
| `board_stats.py:485` | `max_attempts_per_task` | dict .get or configured-value read | absent gives None; the caller default applies; _max_attempts as at :305 |
| `board_stats.py:639` | `state_namespace` | Mission or settings attribute | Mission field is required, so a missing value fails validation; live lookups by it give None; passed to _mission_tasks; Mission field is required |
| `board_task.js:107` | `max_attempts_per_task` | JS property read | absent gives undefined, treated as unset; default or dash shown; stats null gives a dash |

Kinds in the table mean: literal default (a `DEFAULTS` or constant value, not a read); name in a list, map or key table (no value read); display text (i18n, CSS, comment or docstring); SQL column list (SELECT, INSERT or UPDATE); row value after a SELECT (the SELECT fails first); dict .get (None); Python [] index (KeyError); Mission or settings attribute (required field); JS property read (undefined, treated as unset); branch on the value (else branch); HTTP body field (RunBody default).

Proof: the table has 303 rows, one per grep line; the distinct `file:line` keys in the table equal the grep count. Cover rows 129 + outside-cover rows 174 = 303. The grep output and the table were compared by script, not by eye.

`git diff --check` does not check this file: it is untracked, so `git diff --check docs/config-cutover.md` exits 0 with no output. The check used here is `git diff --no-index --check /dev/null docs/config-cutover.md`. The file uses LF line endings (0 CRLF lines, checked by Python; `file` is not installed on this host).

Open risks: the SQL behaviour (a dropped column makes the statement fail) comes from reading the code, not from a run against the database; `board_api.py:527` and `board_stats.py:202` rely on `configured_value` returning None for an absent key (not run); `board_pg.py` allowlist names (`:713`, `:847-859`) fail only when a listed column is dropped and the UPDATE runs.

## Checks (T006A)

Scope: the ops files changed for the legacy-column work (`data-tables/ops/launch_mission.sh`, `notify.py`, `legacy_columns_optional_patch.py`, `overrides_config_patch.py`, `check_legacy_columns.py`) and the tests for them (`claude-runner/tests/test_legacy_columns_optional_patch.py`, `test_overrides_config_patch.py`, `test_all_patches_constants_roles.py`, `test_ops_legacy_reads.py`, `test_check_legacy_columns.py`, and `test_ops_patches_qa.py`, which reads `launch_mission.sh`), plus the five board JS tests named in the review. Other files modified today (`test_board_api.py`, `test_qa_fixture.py`) are not part of this check. The host is WSL Linux, not the Windows host of the runner notes.

Ran:

- `node --test tests/board_roles.test.js tests/board_mission.test.js tests/board_i18n.test.js tests/board_edit_win_features.test.js tests/board_lessons_ui.test.js` (from `claude-runner`): `# tests 173`, `# pass 173`, `# fail 0`, exit 0. Nothing failed, so no comparison with a clean state was needed.
- `python -m pytest tests/test_ops_patches_qa.py tests/test_all_patches_constants_roles.py -o addopts="" -q --no-cov`, three runs in a row (from `claude-runner`): run 1 `4 passed, 1 warning in 50.85s`, exit 0; run 2 `4 passed, 1 warning in 51.96s`, exit 0; run 3 `4 passed, 1 warning in 50.87s`, exit 0. No run failed. An earlier attempt failed both `test_ops_patches_qa.py` tests with `adaptive_split_patch failed: usage: adaptive_split_patch.py [-h] --src SRC --out OUT`; the cause is not established. `launch_mission.sh` was rewritten at 01:51:20, during that attempt.
- `python -m pytest` on the six test files (`-o addopts="" -q --no-cov`, earlier run, not repeated): `32 passed in 57.08s`, exit 0. It includes the double-apply tests `test_all_patches_twice_then_legacy_and_stripped_rows_resolve_the_same` and `test_all_launch_patches_twice_on_a_fresh_export`.
- `python -m ruff check --no-cache --config claude-runner/pyproject.toml` on `check_legacy_columns.py` and the five new test files: `All checks passed!`.
- `python -m ruff check` on `data-tables/ops/notify.py`: 12 errors before, 11 after (the "before" is a copy of the file with the SELECT line in its pre-edit single-line form, made in `/tmp`). After is at most before, as required. The 11 remaining are E501 at 5, 28, 29, 32, 73, 82; SIM115 at 19, 46, 83; SIM105 at 36; UP031 at 37, all on lines this task did not touch.
- `python -m ruff check` on `data-tables/ops/pgdb.py`: 8 errors. The file was not edited by this task (mtime 17:01, before the task's edits), so it has no before/after pair.
- `python -m ruff check` on `claude-runner/tests/test_ops_patches_qa.py`: working copy and `git show HEAD:` copy both `All checks passed!`. The file is tracked and already modified by other work; this task did not edit it.
- `python -m mypy --config-file claude-runner/pyproject.toml --python-version 3.12` on the new files: the 12 errors are all in `notify.py` (6) and `pgdb.py` (6), which the test imports pull in. None are in `check_legacy_columns.py` or the test files. Run with `MYPYPATH=data-tables/ops` so that `pgdb` resolves.
- `python -m mypy` on `notify.py`: before 6 errors (untyped `_env`, `mission_tag`, `send` and their callers), after 6. `pgdb.py`: 6 before, 6 after (not edited). The project pins `python_version = "3.11"`; under 3.11 mypy stops on a numpy stub from another venv (`Type statement is only supported in Python 3.12 and greater`), so the 3.12 override was used. The config was not changed.
- `python -m mypy --python-version 3.12` on `test_ops_patches_qa.py`, working copy and `git show HEAD:` copy: `Success: no issues found in 1 source file` for both.
- `bash -n data-tables/ops/launch_mission.sh`: ok.
- `git diff --check` (repository): exit 0.
- `git diff --no-index --check /dev/null <file>` for the untracked and ops files (check_legacy_columns.py, notify.py, launch_mission.sh, the five new test files, all six test files, docs/config-cutover.md): 0 whitespace hits each.

Fixed in this check (new or changed by this work): `check_legacy_columns.py` long lines (E501) wrapped with no behaviour change; the `notify.py` SELECT line (E501) wrapped; the 7 untyped test functions in `test_check_legacy_columns.py` annotated; `test_overrides_config_patch.py` workflow dict annotated `dict[str, Any]`; in `test_ops_legacy_reads.py` a `re.search` result narrowed with `assert`, `BASH` narrowed after the skipif, and `__exit__` returns `None`.

Not run: the full runner suite (not in scope), and mypy under the pinned Python 3.11 (blocked by the numpy stub above).

Not done, per the rules: nothing restarted, rebuilt or recreated; nothing imported into n8n; no data table written; nothing committed or pushed. The checks read files and used copies in `tmp_path` and `/tmp`.

Open risks:

- `test_ops_patches_qa.py` `_script_args` passes only the workflow path, but `launch_mission.sh` runs `adaptive_split_patch.py --src ... --out ...` and then `cp` the scratch output over `wf_next.json`. The harness therefore cannot model that step; this is a test-harness gap to fix in a separate task.
- One run showed `features_config_patch failed: anchor found 0 times` for `max_tasks`, which was not reproduced in the later runs.
- `notify.py` keeps its pre-existing lint and untyped functions; they are outside this task.
- The live `hermes_config` row was not read, so the T004D precondition is still unverified.

## Сводка на русском (T006B)

Документ только описывает удаление. Код, workflow, таблицы данных и строка `hermes_config` не менялись.

### Какие колонки можно удалить

Вердикт `data-tables/ops/check_legacy_columns.py` сейчас такой: ни одной колонки. На строке-заглушке из `/tmp` (только `profile_id` и `id`) скрипт печатает `SAFE: 0, NOT SAFE: 34`. У всех 34 записей в `LEGACY` есть читатели (`workflow=`, `run_manager=` или `operator`), а вердикт `SAFE` требует, чтобы читателей не было. Поэтому по вердикту пока не удаляется ни одна колонка. Вердикт изменится только после того, как читатели будут сняты и таблица `LEGACY` обновлена в том же изменении (это написано в докстринге скрипта).

Кандидаты по плану (`docs/config-columns-plan.md`, раздел 5), все колонки `hermes_config`:

| Шаг | Колонки | Число |
|---|---|---|
| 0 | `max_tasks` | 1 |
| 1 | `architect_model`, `architect_provider`, `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`, `service_provider`, `service_model`, `architect_max_polls`, `worker_max_polls`, `reviewer_max_polls`, `architect_poll_seconds`, `worker_poll_seconds`, `reviewer_poll_seconds`, `architect_max_retries`, `worker_max_retries`, `reviewer_max_retries` | 17 |
| 2 | `worker_think_policy`, `worker_think_policy_from_task`, `worker_think_off_after_seconds`, `worker_think_off_after_steps` | 4 |
| 3 | `retrieval_enabled`, `retrieval_url`, `retrieval_corpora_json`, `retrieval_timeout_ms`, `retrieval_worker_max_tokens`, `retrieval_reviewer_max_tokens`, `retrieval_label` | 7 |
| 4 | `max_attempts_per_task`, `retry_delays_json`, `runner_url`, `run_manager_workflow_id`, `telegram_chat_id` | 5 |

Шаг 5 (колонки `missions`, в скрипт не входят): `state_namespace` и `retrieval_corpora_json` из таблицы missions, третья колонка не установлена (UNKNOWN). `retrieval_corpora_json` указана и в шаге 3 (как ключ `retrieval_json` в скрипте), и в шаге 5 (как колонка missions, строка 36 таблицы). Таблица, где она лежит, проверяется в интерфейсе n8n перед удалением. В этом релизе ни одна колонка missions не удаляется: `state_namespace` остаётся псевдонимом `mission_id`.

### Порядок удаления

Порядок тот же, что в плане (раздел 5): шаги 0, 1, 2, 3, 4, 5. Внутри шага:

1. Значения уже лежат в JSON-колонке (`features_json`, `roles_json`, `thinking_json`, `retrieval_json`, `engine_json`, `missions.overrides_json`) и читаются оттуда (план, раздел 4).
2. Операторские скрипты перестают читать и писать колонку. `real_queue.sh`, `real_queue_board.sh` и `real_queue_rerun.sh` не менялись; их строки перечислены в разделе "Operator action".
3. Оператор удаляет колонки в интерфейсе n8n, не SQL. После этого код убирает fallback в следующем релизе.

Риск: до релиза, который убирает fallback, чтение удалённой колонки даёт `undefined` (коды B1-B21 в разделе "Legacy columns"). Поэтому колонку удаляют только при вердикте `SAFE`, то есть после снятия читателей.

### Откат

- До удаления колонки: очистить JSON-колонку, fallback на колонку снова действует (план, раздел 4, шаг 5).
- После удаления: добавить колонку заново в интерфейсе n8n и записать в неё значение из отчёта `check_legacy_columns.py` (поле `legacy=`).
- Оговорки к отчёту: он обрезает значение до 60 символов и ставит `...`, пишет `NULL` для пустого значения, а вместо значения пишет `***` для `telegram_chat_id` и для имён, содержащих `secret`, `password`, `passwd`, `api_key`, `credential`, `access_token` или `auth_token`. Для таких колонок значение берётся из файла строки, который оператор сохранил (шаг 1 ниже), а не из отчёта. План также требует записи в CHANGELOG о том, где лежат значения удалённых колонок.

### Что не проверено вживую

- Патченный workflow не импортирован в n8n; реального запуска миссии не было.
- Отчёт не запускался на продакшн-строке `hermes_config` (id 5): строка не читалась, доступа к БД не было.
- Значения всех колонок UNKNOWN (раздел "Open points"). Экспорт workflow от 2026-10-23 может не совпадать с живым состоянием.
- Проверка этой сводки: `tests/test_check_legacy_columns.py` (из `claude-runner`) `7 passed`, и вердикт `SAFE: 0, NOT SAFE: 34` на строке-заглушке. Реальной строкой проверки не было.

### Шаги оператора

1. Сохранить строку `hermes_config` (`profile_id` = `claude-code`, id 5) в JSON-файл вне git, например `<путь к файлу строки>`. В файле есть настройки, его не копируют в отчёты и в git.
2. Запустить `python data-tables/ops/check_legacy_columns.py --row-file <путь к файлу строки>`. Скрипт читает только этот файл и не открывает соединение с БД. Смотреть последнюю строку `SAFE: N, NOT SAFE: M` и `verdict=` у каждой колонки.
3. Удалить колонки в интерфейсе n8n, по порядку из раздела выше, и только те, у которых `verdict=SAFE` и которые входят в завершённый шаг.
4. Записать в CHANGELOG, какие колонки удалены и где лежат их значения.
