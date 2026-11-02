# Config roles, step 1: inventory of the 17 legacy columns

Scope of this step: list every reader of the 17 legacy `hermes_config` columns (`architect_*`, `worker_*`,
`reviewer_*`, `service_*` provider and model; `*_max_polls`, `*_poll_seconds`, `*_max_retries`), propose the
`roles_json` key each one moves to, and record today's default. No workflow, data table or patch was changed.

## Export used

| Item | Value |
|---|---|
| Source file | `data-tables/ops/wf_live_now.json` (the live export, already patched by the ops chain) |
| Working copy | `data-tables/ops/_scratch/t001a/live_export.json` (byte-identical to the source, checked with `cmp`) |
| Workflow | "AHAWR v13 — Claude Code", id `7oMrsFqTiYMy0JJN`, 102 nodes |
| Export timestamp | `updatedAt` 2026-10-23T14:17:41.716Z (8 days old on 2026-10-31) |
| Newer candidates checked | `ls -t data-tables/ops/*.json data-tables/ops/_scratch/*.json` lists `queue_watch.state.json`, `rm_live.json`, `wf_next.json` first; `wf_next.json` is the same workflow (same id, `updatedAt`, 102 nodes; the 17 column reads are identical). No newer full export exists. |
| Not used | `AHAWR_v13_ClaudeCode.json` (pre-launch template, not the live workflow) |

The export is 8 days old. If the operator stages a newer export, the node line numbers below must be re-checked.

## The 17 columns: proposed `roles_json` key and default

Role names and the `"provider/model"` format come from `claude-runner/docs/roles-config.md` (role list at line 110;
model format at lines 39–48). Polling and retry options go under `options`, as in `docs/config-columns-plan.md:58–61`.

| # | Legacy column | Constants field (export) | Proposed `roles_json` key | Default today |
|---|---|---|---|---|
| 1 | `architect_model` | `planner_model` | `architect."1"` (with column 2 as `provider/model`) | `opus` |
| 2 | `architect_provider` | `planner_provider` | `architect."1"` (with column 1) | `anthropic` |
| 3 | `worker_model` | `worker_model` | `worker."1"` (with column 4) | UNKNOWN |
| 4 | `worker_provider` | `worker_provider` | `worker."1"` (with column 3) | UNKNOWN |
| 5 | `reviewer_model` | `reviewer_model` | `reviewer."1"` (with column 6) | UNKNOWN |
| 6 | `reviewer_provider` | `reviewer_provider` | `reviewer."1"` (with column 5) | `anthropic` |
| 7 | `service_provider` | `service_provider` | `service."1"` (with column 8) | UNKNOWN |
| 8 | `service_model` | `service_model` | `service."1"` (with column 7) | UNKNOWN |
| 9 | `architect_max_polls` | `architect_max_polls` | `architect.options.max_polls` | `30` |
| 10 | `worker_max_polls` | `worker_max_polls` | `worker.options.max_polls` | `30` |
| 11 | `reviewer_max_polls` | `reviewer_max_polls` | `reviewer.options.max_polls` | `20` |
| 12 | `architect_poll_seconds` | `architect_poll_seconds` | `architect.options.poll_seconds` | `3` |
| 13 | `worker_poll_seconds` | `worker_poll_seconds` | `worker.options.poll_seconds` | `3` |
| 14 | `reviewer_poll_seconds` | `reviewer_poll_seconds` | `reviewer.options.poll_seconds` | `3` |
| 15 | `architect_max_retries` | `architect_max_provider_retries` | `architect.options.max_retries` | `5` |
| 16 | `worker_max_retries` | `worker_max_provider_retries` | `worker.options.max_retries` | `3` |
| 17 | `reviewer_max_retries` | `reviewer_max_provider_retries` | `reviewer.options.max_retries` | `5` |

Defaults and sources:

- Columns 1, 2, 6 and 9–17 (12 values) agree in both sources: `claude-runner/docs/board-data-model.md` lines 477–485
  (row id=5, profile `claude-code`) and `hermes_config.example.csv` line 4 (`claude-code;true;…`).
- Columns 3, 4, 5, 7, 8 are UNKNOWN: the two sources disagree. `board-data-model.md:479–481` gives worker
  `local`/`qwen3.8-27b-gsq-rco-iq2-s-mtp`, reviewer `anthropic`/`sonnet`, service `anthropic`/`sonnet`; the example CSV
  line 4 gives worker `anthropic`/`sonnet`, reviewer `anthropic`/`opus`, service empty. The Constants node
  (export, lines 58–63) only falls back to `''`, which is not a production default.
- The live `hermes_config` row (id=5 of `data_table_user_1S21PsMqT0FpR7eD`) was **not read** (no docker access in this
  task). Every default above comes from the docs, not from the live database.

## Workflow and patches readers

### A. Readers in the export (node name, code location)

Searched every `jsCode` and `text` parameter of the 102 nodes for the 17 column names and their Constants field names
(22 terms). Result: 66 matching lines across 10 nodes. The 11 QA nodes have no hits.

| Node | Matching lines | Code location (line in the node's code) |
|---|---|---|
| Constants | 17 | L56–63 (`row.<column>` reads), L66–74 (`num(row.<column>, …)`) |
| Build Architect Run Input | 11 | L351–356 legacy model/provider block; L376 `poll_seconds`; L377 `max_polls`; L378 `max_retries`; L385–386 `service_*` |
| Build Worker Run Input | 11 | L351–356 legacy block; L438 `poll_seconds`; L439 `max_polls`; L440 `max_retries`; L444–445 `service_*` |
| Build Reviewer Run Input | 11 | L351–356 legacy block; L438 `poll_seconds`; L439 `max_polls`; L440 `max_retries`; L444–445 `service_*` |
| Prepare Verdict Retry | 6 | L2 (comment), L4 (`reviewer_model`/`reviewer_provider` fallback), L5 `reviewer_max_provider_retries`, L23 poll and max_polls, L24 max_retries, L25 `service_*` |
| Split Task Notice | 6 | L548–553 legacy model/provider block |
| Restore Review Notification Context | 1 | L22 (fallback `worker_*` and `reviewer_*` expressions) |
| Send a text message5 | 1 | L1 (`planner_model`/`planner_provider` fallback expression) |
| Send a text message6 | 1 | L1 (`worker_model`/`worker_provider` fallback expression) |
| Send a text message7 | 1 | L1 (`worker_model`/`worker_provider` fallback expression) |

Per-column hits (`grep -c` semantics, one count per matching line): architect_model 5, architect_provider 5,
worker_model 8, worker_provider 8, reviewer_model 7, reviewer_provider 7, service_provider 5, service_model 5,
architect_max_polls 2, worker_max_polls 2, reviewer_max_polls 3, architect_poll_seconds 2, worker_poll_seconds 2,
reviewer_poll_seconds 3, architect_max_retries 1, worker_max_retries 1, reviewer_max_retries 2. The sum is 75
because a line that names two columns counts once for each.

The exact line of each hit is in the notes file `data-tables/ops/_scratch/t001a/T001A_inventory.md` (§4), which is
gitignored.

### B. Readers in `data-tables/ops/*_patch.py` (file:line)

Recounted in this step with a per-line regex over the 22 `*_patch.py` files (the export count above is per line; the
patch counts below are per line too).

| Column | Hit count | file:line |
|---|---|---|
| architect_model | 1 | `roles_config_patch.py:64` |
| architect_provider | 1 | `roles_config_patch.py:63` |
| worker_model | 12 | `cloud_first_patch.py:49,50` · `notice_split_patch.py:20,95` · `roles_config_patch.py:66,106,114,125,135` · `telegram_model_patch.py:13,44` · `test_telegram_progress_patch.py:109` |
| worker_provider | 13 | `cloud_first_patch.py:42,51,52` · `notice_split_patch.py:20,95` · `roles_config_patch.py:65,110,115,125,135` · `telegram_model_patch.py:13,44` · `test_telegram_progress_patch.py:110` |
| reviewer_model | 5 | `roles_config_patch.py:68,136,139,198` · `telegram_model_patch.py:44` |
| reviewer_provider | 5 | `roles_config_patch.py:67,136,139,200` · `telegram_model_patch.py:44` |
| service_provider | 0 | none in the glob |
| service_model | 2 | `failover_patch.py:70,77` |
| architect_max_polls | 1 | `test_features_config_patch.py:24` |
| worker_max_polls | 1 | `test_features_config_patch.py:25` |
| reviewer_max_polls | 1 | `test_features_config_patch.py:26` |
| architect_poll_seconds | 1 | `test_features_config_patch.py:27` |
| worker_poll_seconds | 1 | `test_features_config_patch.py:28` |
| reviewer_poll_seconds | 1 | `test_features_config_patch.py:29` |
| architect_max_retries | 1 | `test_features_config_patch.py:21` |
| worker_max_retries | 1 | `test_features_config_patch.py:22` |
| reviewer_max_retries | 1 | `test_features_config_patch.py:23` |

Two patches are not in the glob but read the columns: `patch_verdict_retry.py` (utility, lines 11–12, 36, 38, 55–58,
181) and `cloud_first_patch.py` (retired; listed above). `roles_config_patch.py` also reads `roles_json` and
`mission_roles_json` (lines 46–52, 70), which is the target of this migration.

Files with 0 hits on the 17 columns: `blind_guard_patch.py`, `features_config_patch.py`, `lessons_patch.py`,
`notice_cap_patch.py`, `qa_workflow_patch.py`, `report_pack_patch.py`, `retry_patch.py`,
`review_calibration_patch.py`, `stable_prefix_patch.py`, `telegram_gate_patch.py`, `telegram_mission_patch.py`,
`telegram_progress_patch.py`, `think_config_patch.py`.

## Launch order of the ops patches

Source: `data-tables/ops/launch_mission.sh`, lines 33–45 (`PYTHONIOENCODING=utf-8 python "$SP/<name>.py" "$SP/wf_next.json"`):

1. `notice_split_patch.py` (L33)
2. `telegram_model_patch.py` (L34)
3. `telegram_mission_patch.py` (L35)
4. `telegram_progress_patch.py` (L36)
5. `report_pack_patch.py` (L37)
6. `lessons_patch.py` (L38)
7. `retry_patch.py` (L39)
8. `telegram_gate_patch.py` (L40)
9. `blind_guard_patch.py` (L41)
10. `think_config_patch.py` (L42)
11. `roles_config_patch.py` (L43)
12. `failover_patch.py` (L44)
13. `qa_workflow_patch.py` (L45)

Plus, in a subshell, `launch_mission.sh` L53–54: `think_config_patch.py` then `failover_patch.py` on `rm_live.json`
(Run Manager sub-workflow).

`launch_mission.sh.new` (an older alternate order, used by `test_all_patches_order.py:24`) is not the launch order
and is not used here.

## Checks

| Check | Result |
|---|---|
| `cmp data-tables/ops/_scratch/t001a/live_export.json data-tables/ops/wf_live_now.json` | identical |
| Export node count (Python `json`) | 102 |
| Export matching lines (22 terms, Python regex) | 66 lines, 10 nodes; per-node counts match section A |
| Patch hits per column (Python regex over `*_patch.py`) | line sets match section B; the regex counts occurrences, so one line with two mentions adds one extra count: `worker_model` 13 (line 66 twice), `worker_provider` 14 (line 65 twice), `reviewer_model` 6 (line 68 twice), `reviewer_provider` 6 (line 67 twice) against the 12, 13, 5, 5 lines in section B |
| `grep -n` of default lines in `board-data-model.md` and `hermes_config.example.csv` | lines 477–485 and CSV line 4 (see Defaults) |
| `git status --short` | run after the write; this step adds only `docs/config-roles-step1.md` |

Not run: the live `hermes_config` row (no docker access, not allowed to run it); any n8n import or data-table write
(none needed or allowed).

## Open risks

- The export is 8 days old. Any change after 2026-10-23 is not in this inventory.
- Five defaults (worker, reviewer model, service) are UNKNOWN until the live row is read by the operator.
- `docs/config-roles-step1.md` is a new durable doc, which `CLAUDE.md` normally discourages for status work; it exists
  here because the task names it. Remove it if it is only a scratch record.

## Runner, board, UI and docs readers

Scope: `claude-runner/src/claude_runner` (Python and JS), `claude-runner/tests`, `claude-runner/docs`, `docs/`, `README.md`,
`.env.example`. The grep used the 17 names plus the Constants field names of the table above. `docs/cold-prefill/*.jsonl`
(run transcripts; 171 of 1341 files have hits) is excluded: it is not documentation. `dashboard.py` and `dashboard.html` have
no hit. `board_roles.js` and `board_features.js` have no hit on a legacy column (only `roles_json` and `features_json`).

### A. Runner and board code (read-only)

| Location | Symbol | Columns | What it does with them |
|---|---|---|---|
| `claude-runner/src/claude_runner/board_api.py:494-497` | `_DEFAULT_KEYS`, read at 506 in `_config_defaults` | 1–4, 5–6 (architect, worker, reviewer provider and model) | Shows the hermes_config defaults on the board |
| `claude-runner/src/claude_runner/board_pg.py:817-823` | allow-list in `update_hermes_config` (def at 797) | all 17 | Columns the PostgreSQL store may write; `roles_json` and `features_json` at 830 |
| `claude-runner/src/claude_runner/board_plan.py:76-90` | `MIDRUN_CONFIG_FIELDS` (used at 509) | 3–8, 9–17 except the architect model and provider (15 names) | Fields that may change while a mission runs |
| `claude-runner/src/claude_runner/board_mission.js:51-57` | `MIDRUN_CONFIG_FIELDS` (JS copy, comment at 50) | same 15 names | Same list for the board UI |
| `claude-runner/src/claude_runner/board_mission.js:77-90` | `FORM_ROWS` | 1–6 (architect, worker, reviewer model and provider), rows at 85–90 | Mission form rows, labelled by `form.field.*` |
| `claude-runner/src/claude_runner/board_edit_win.js:23-26` | `PAGES`, page "exec" keys | 1–6 | Keys of the launch window's Execution page |
| `claude-runner/src/claude_runner/board_edit_win.js:29-32` | `ROLES` | 1–6 (architect, worker, reviewer); service is not in the window | Role rows of the window |
| `claude-runner/src/claude_runner/board_edit_win.js:119-124` | `eff()` | the keys above | Shows the form value, or the hermes_config default as a placeholder |
| `claude-runner/src/claude_runner/board_edit_win.js:127-129` | summary card | 1–6 | Shows "model · provider" per role |
| `claude-runner/src/claude_runner/board_ready.py:401` | `_service_urls`, `worker_model` (`config.get`) | 3 (worker model) | Model name probed against llama-server (`check_llama_model`, 172–212) |
| `claude-runner/src/claude_runner/board_i18n.js:356-361`, `board_i18n_en.json:37`, `board_i18n_ru.json:37` | `form.field.*` labels | 1–6 | Label texts only, not values |
| `claude-runner/tests/roles_cases.json:759-767, 784-789, 803-804, 820-821, 842-843` | `legacy_fallback_*` inputs | 3, 4, 5, 6, 10, 13, 16 | Shared cases file: legacy inputs that must resolve |

### B. Runner tests that name a column (not run in this step)

`test_roles_config_patch.py` (19 lines), `board_mission.test.js` (6), `test_board_api.py` (5), `board_mission_save.test.js` (3),
`test_board_pg.py` (3), `test_roles_config.py` (2: `legacy_role`, lines 102–103), `test_board.py` (2),
`test_board_api_part2.py` (2), `test_mcp_board.py` (2), `test_board_ready.py` (1), `test_failover_patch.py` (1),
`test_qa_runner.py` (1). Counts are lines matching the 17 names and the legacy-column patterns, from the grep above.

### C. Docs and examples

| Location | Columns | Note |
|---|---|---|
| `README.md:288-313` (`#### hermes_config`) | 1–17 | Column list. The Russian copy is at `README.md:1191-1214` |
| `README.md:660-661` | 3, 4 | `hermes_config.worker_model` and `worker_provider` |
| `README.md:885` | 1 | Says the compression model is independent of `architect_model` |
| `.env.example:277-279` | 1–6 | Commented examples only; no service or poll keys |
| `claude-runner/docs/board-data-model.md:413-420` | 7–8 | Service model and provider, applied to every run |
| `claude-runner/docs/board-data-model.md:437-455` | 1–17 | Column table with types (double precision for the numeric ones) |
| `claude-runner/docs/board-data-model.md:478-485` | 1–17 | Example row (id=5, profile `claude-code`); source of the defaults in section above |
| `claude-runner/docs/board-design.md:256-257` | 3, 4, 5, 1 | Example update of row id=5 |
| `claude-runner/docs/board-ops-notes.md:358, 428-430, 452` | 3, 4, 5, 6, 1, 2, 7, 8, 9–17 | Ops notes; `board_ready.py:279-295` probe of `worker_model` (line 452) |
| `claude-runner/docs/board-ops.md:111, 282` | 3–6 | `model_pairs` entries and `switch_model_pair` |
| `claude-runner/docs/board-ops-agent.md:200` | 3, 4 | `switch_model_pair` |
| `claude-runner/docs/cloud-thinking-notes.md:217` | 4 | Reads `worker_provider` from Constants |
| `claude-runner/docs/lessons.md:44, 74` | 4, 7 | Anchor note; service_model found only in field lists |
| `claude-runner/docs/qa-acceptance.md` | 134 matching lines | Not read line by line: UNKNOWN which columns each line uses |
| `docs/telegram-bot.md:68-69, 85-93, 227-230` | 3, 4, 5, 6, 12–14 (type and range rows at 85–93) | `/set` and `/model` allow-list and examples |

Patches under `data-tables/ops` are in section B of the workflow part above and are not repeated here.

## Core and save path

### Python and JS roles core

- Python core: `claude-runner/src/claude_runner/roles_config.py` (356 lines; no FastAPI or route import). Public
  names: `validate_roles_json` (54), `validate_roles` (64), `merge` (237), `legacy_role` (245), `resolve` (256),
  `qa_layers` (198). Constants: `MODEL_SEPARATOR = "/"` (10), `OPTIONS_KEY = "options"` (11), `FIRST_ATTEMPT_KEY = "1"` (12),
  `COMMON_OPTION_KEYS` (16: `fresh`, `think`, `max_polls`, `poll_seconds`, `max_retries`), `QA_OPTION_KEYS` (17, includes
  `smoke`), `FALLBACK_ROLES` (224: `qa` to `reviewer`, `report` to `worker`), `LEGACY_OPTION_KEYS` (223).
- JS core: `claude-runner/src/claude_runner/board_roles.js`, lines 3–551, bounded by `// === pure roles core` (3) and
  `// === end pure roles core ===` (551). Functions: `validateRolesJson` (147), `validateRoles` (166), `mergeRoles` (300),
  `legacyRole` (304), `pickModel` (335), `resolveRole` (384), QA helpers (427–545), `loadQaRoles` and `saveQaRoles`
  (526–539, the `api` argument is injected). Export block starts at 553.
- Names differ between the two cores: `merge` and `mergeRoles`, `resolve` and `resolveRole`. The shared cases file is
  what keeps the two in step.
- Parity gap: `QA_OPTION_KEYS` in JS (`board_roles.js:11`) has no `smoke`, while the Python set has it
  (`roles_config.py:17`). JS `checkOptions` (`board_roles.js:279-291`) therefore reports `qa.options.smoke` as an unknown
  option; Python accepts a boolean (`roles_config.py:173-174`). `QA_SETTABLE_OPTIONS` in JS (`board_roles.js:427`) does
  list `smoke`. Whether a test covers this: UNKNOWN (not checked in this step).
- Python importers of the core: `board_ready.py:41`, `board_stats.py:31`, `qa.py:36`, `qa_requests.py:20`,
  `api.py:41` (`QA_LAYERS`), `board_providers_api.py:34`. Which of them pass legacy columns into `resolve`: UNKNOWN
  (not traced here).

### Shared cases file and the tests that run it

- Cases: `claude-runner/tests/roles_cases.json` (one file for both cores).
- Python runs it: `claude-runner/tests/test_roles_config.py:24` (`(Path(__file__).parent / "roles_cases.json")`).
- JS runs it: `claude-runner/tests/board_roles.test.js:9` (`CASES = JSON.parse(...)`); run with
  `node --test tests/board_roles.test.js` from `claude-runner/`.
- The features cases (`claude-runner/tests/features_cases.json`) follow the same pattern: `test_features_config.py:12`
  and `board_features.test.js:9`.

### flatten() today

There is no `flatten` in the roles core. The only `flatten` in the runner is for features:
`claude-runner/src/claude_runner/features_config.py:137-149` (Python) and `claude-runner/src/claude_runner/board_features.js:97-110`
(JS, exported at 117). Both return the same eight keys: `plan_no_truncate` (0/1), `max_tasks`, `max_tasks_hard`,
`adaptive_split` (0/1), `split_steps`, `max_splits`, `max_total_tasks`, `run_hygiene` (0/1). No caller of either
`flatten` exists in `src/claude_runner` beyond its export; `test_features_config.py:9` imports it. Consumer: UNKNOWN.
None of the proposed `roles_json` keys in part 1 goes through `flatten`.

### Consistency with the proposed keys (part 1)

- `"provider/model"` with `/` (`MODEL_SEPARATOR`) and attempt key `"1"` (`FIRST_ATTEMPT_KEY`) match `architect."1"`,
  `worker."1"`, `reviewer."1"` and `service."1"` in part 1 (`KNOWN_ROLES` at `roles_config.py:15` and `board_roles.js:9`
  include all four roles).
- `options` (`OPTIONS_KEY`) and the option names `max_polls`, `poll_seconds`, `max_retries` match the `.options.*` keys
  in part 1 (`COMMON_OPTION_KEYS`).
- Part 1's Constants field names (for example `architect_max_provider_retries`) are not roles keys and are not used in
  the core.

### Launch-window save path (roles_json)

1. The window's Save runs `onNext` in `claude-runner/src/claude_runner/board_edit_win.js` (the last page). It calls `saveQa()`
   (`board_edit_win.js:301-306`), then `saveFeatures()`, then `save().doSave(box)`.
2. `saveQa()` calls `BoardRoles.saveQaRoles(...)` (`board_roles.js:534-539`). If the QA diff is empty it returns without a
   request (`board_roles.js:532-533`). Otherwise it sends `PUT` with body `{roles_json, expected_version}`
   (`board_roles.js:536`).
3. `postJson` in `claude-runner/src/claude_runner/board_mission_save.js:587-600` sends the header `X-AHAWR-Dashboard: 1`
   (line 596).
4. Route: `PUT /api/board/missions/{mission_id}/roles`, `claude-runner/src/claude_runner/board_api.py:1575-1589`
   (`put_mission_roles`). It runs `_require_dashboard_header` (the header check), `_roles_text` (131-143), then
   `roles_config.validate_roles_json(text, providers)` (141; `providers` from `registry.profiles()`).
5. Version check: `expected_version` is passed to `store.set_mission_config(..., expected_version=...)`
   (`board_api.py:1585-1586`). The client treats a 409 as "the plan changed" (`board_mission_save.js:587-588`). The store
   method itself was not read: its conflict behaviour is UNKNOWN.
6. Diff and confirm for roles: the window's Review page (page 4) shows the diff, including `qaRows()`
   (`board_edit_win.js:545-557`), and the Save click starts the PUT. The `window.confirm` in `board_mission_save.js:324`
   belongs to `onSave`, which is a different entry point and is not on this path.
7. The other 17 columns, when edited in the window, go through `doSave` to `POST /api/board/missions/{id}/config`
   (`board_mission_save.js:334-365`; route `board_api.py:1275-1300`, with `expected_version` and `dry_run` diff).
8. The global `PUT /api/board/roles` (`board_api.py:1555-1562`) has no caller in `claude-runner/src/claude_runner/*.js`.
   The only reference is the fake API in `board_roles.test.js:255-256`. The launch window does not use it.

### Checks for this step

| Check | Result |
|---|---|
| `grep -rn -E` of the 17 names and the Constants names over `claude-runner/src/claude_runner`, `claude-runner/docs`, `docs`, `README.md`, `.env.example` (`cold-prefill` excluded) | hits listed in sections A–C; every listed reader has its file:line |
| `grep -n "roles_json" board_api.py` etc. | save route lines 1553–1589 confirmed by reading the code |
| `git diff --check` | run after the write (see the report) |

### Open risks (part 2)

- `smoke` parity gap between the JS and Python QA option sets (see above).
- Consumer of `flatten()` output: UNKNOWN.
- Which runtime callers pass legacy columns into `resolve`: UNKNOWN.
- `qa-acceptance.md` (134 hits) was not read line by line.
- Store conflict behaviour on a stale `expected_version` for roles: UNKNOWN.

## Итог шага 1 (T006): что изменено, шаги оператора, что не проверено, откат

### Соответствие задач файлам

Идентификаторы T001–T005 ниже соответствуют определениям миссии.

| Задача | Что сделано | Файл:строки |
|---|---|---|
| T001 | Инвентаризация 17 legacy-колонок (этот документ) | `docs/config-roles-step1.md:1-305` (файл новый, не отслеживается git) |
| T002 | Ядро ролей: константы и значения по умолчанию, `flatten` в Python и JS, общие кейсы | `claude-runner/src/claude_runner/roles_config.py:28-39` (константы), `:388` (`flatten`); `claude-runner/src/claude_runner/board_roles.js:586` (`flatten`); `claude-runner/tests/roles_cases.json` (golden: `golden_empty_roles_json` 1401, `golden_missing_roles_json` 1438); `claude-runner/tests/test_roles_config.py:73` (`test_shared_flatten_case`); `claude-runner/tests/board_roles.test.js` |
| T003 | Читатели: `Constants` берет роли из `roles_json`, legacy-колонки последним fallback; `_config_defaults` на доске | `data-tables/ops/roles_options_patch.py:84` (`replacements`), `:104` (`patch`); `claude-runner/tests/test_roles_options_patch.py:35`; `claude-runner/src/claude_runner/board_api.py:500` (`_config_defaults`, `flatten` at 509) |
| T004 | Окно запуска: polling-хелперы, сохранение, проверка опций, форма миссии | `claude-runner/src/claude_runner/board_roles.js:610` (`POLLING_ROLES`), `:614` (`pollingValuesOf`), `:625` (`pollingDiffRows`), `:638` (`pollingRolesText`); `board_edit_win.js:312, 353, 359, 371, 374`; `board_mission.js:366-374` (`configValues`); `roles_config.py:193` (`_check_polling`), `:205` (`_is_whole_number`); `claude-runner/tests/test_board_roles_api.py:101, 108, 122, 133`; `claude-runner/tests/board_mission.test.js` |
| T005 | Сухой прогон: печатает предлагаемый `roles_json` | `data-tables/ops/propose_roles_json.py:22` (`propose`), `:29` (`legacy_roles`), `:49` (`_overlay`), `:64` (`_existing_roles`), `:80` (`main`); `claude-runner/tests/test_propose_roles_json.py:63-115` |

Линтеры (запущены на `roles_config.py`, `propose_roles_json.py`, `roles_options_patch.py`):

- `python -m ruff check ...`: `All checks passed!`
- `python -m mypy ...`: `Success: no issues found in 3 source files`

### Шаги оператора

1. Взять строку `hermes_config` профиля `claude-code` (один JSON-объект) из staged-файла и запустить только чтение:
   `python data-tables/ops/propose_roles_json.py <файл-строки.json>`. Скрипт ничего не записывает и не ходит в сеть;
   на выходе предлагаемый `roles_json`.
2. Сохранить предложенный JSON в окне запуска миссии (страница Save). Окно отправляет
   `PUT /api/board/missions/{id}/roles` с `expected_version`. Ответ 409 означает, что план изменился; окно нужно открыть заново.
3. Применить `data-tables/ops/roles_options_patch.py` обычной процедурой запуска. Внимание: патча пока нет в
   `data-tables/ops/launch_mission.sh` (строка 43 запускает только `roles_config_patch.py`); тест
   `test_roles_options_patch.py:22-27` добавляет его вручную. Без строки в `launch_mission.sh` процедура его не запустит.
   Добавление строки меняет ops-файл и требует отдельного решения.
4. Позже, после живой проверки `roles_json`, удалить 17 legacy-колонок в интерфейсе n8n (не SQL).

### Что не проверено live

- Нет доступа к docker: живая строка `hermes_config` не читалась, `propose_roles_json.py` на ней не запускался.
- `roles_options_patch.py` не применялся к живому workflow. Тесты работают на выгрузке от 2026-10-23.
- Сохранение в окне запуска не проверялось в браузере. Поведение хранилища при устаревшей версии для ролей остается UNKNOWN.
- Значения по умолчанию колонок 3, 4, 5, 7, 8 остаются UNKNOWN (документы расходятся).
- Node-тесты запущены только для `board_roles.test.js` (T002) и `board_mission.test.js` (T004). T003 в этом прогоне
  проверен только `test_roles_options_patch.py`.

### Откат

Очистить `options` у ролей в `roles_json` (или сохранить `roles_json` пустым). Для ключа, которого нет в `roles_json`,
`Constants` берет legacy-колонку; пустой или отсутствующий `roles_json` дает прежние значения
(`data-tables/ops/roles_options_patch.py:1-15`, docstring). Код менять не нужно. Legacy-колонки до проверки не удалять.
