# Config engine: readers of the infrastructure and loop columns

Read-only inventory for the `hermes_config` column plan (`docs/config-columns-plan.md`, sections 3-5). It lists every
reader of five columns in the live workflow and in the ops patches, so that a later step can move them to `engine_json`
or `roles_json` with a known list of places to change.

Values are not written here: `telegram_chat_id`, `runner_url`, default literals and any secret are referred to by line only.

## Source and freshness

- Workflow export used: `scratchpad/live_workflow_export.json` (operator-staged, file time 2026-10-31 15:14, one
  workflow "AHAWR v13 — Claude Code", 102 nodes, workflow `updatedAt` 2026-10-23).
- Other copy not used: `data-tables/ops/_scratch/t001a/live_export.json` (same workflow, staged earlier today).
- The export was not checked against the running n8n: that needs docker, which a Worker does not have.
  UNKNOWN: whether the running workflow matches this export.
- Freshness signal: the export has no `const __features = parseFeatures(` (the `features_config_patch.py` anchor), so the
  features patch is not in this copy. UNKNOWN: whether it was applied on the live workflow.
- Line numbers in the tables are lines of the Code node's `jsCode` (1-based), not lines of the JSON file. The export is
  one line per file, so `grep -n` cannot give them.

## Workflow readers

Every node that reads one of the five columns. "Constants" is the Code node that turns the `hermes_config` row into
one object; the other nodes read that object by `$('Constants').first().json` or through a local alias.

| Node name | Node id | Column | How it is read |
|---|---|---|---|
| Load Config | 2ddbe4f0-cf72-4e6e-91b0-04af0123f0ec | all (row) | direct Data Table get: table `hermes_config`, filter `profile_id` = `claude-code`, limit 1 |
| Constants | 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74 | max_attempts_per_task | `row.max_attempts_per_task` in `num(...)`, line 66 |
| Constants | 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74 | retry_delays_json | `row.retry_delays_json` parsed as JSON array, lines 7-12 |
| Constants | 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74 | runner_url | `row.runner_url` with a default literal, line 40; output `runner_url`, line 46 |
| Constants | 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74 | run_manager_workflow_id | `row.run_manager_workflow_id` with a default literal, line 43; output line 45 |
| Constants | 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74 | telegram_chat_id | `row.telegram_chat_id`, output line 76 |
| Advance Task | cb806118-fcd1-4f10-8a41-7a900b14f972 | max_attempts_per_task | `cfg.max_attempts_per_task`, line 28; `cfg` is `__cfg` = `$('Constants')`, line 34 |
| Advance Task | cb806118-fcd1-4f10-8a41-7a900b14f972 | runner_url | `cfg.runner_url`, line 18; same `cfg` source |
| Retry Current Task | e382fada-efa5-4254-b36a-ad6ff5264ff7 | max_attempts_per_task | `$('Constants').first().json...`, line 17; `cfg.max_attempts_per_task`, line 44 |
| Retry Current Task | e382fada-efa5-4254-b36a-ad6ff5264ff7 | runner_url | `cfg.runner_url`, line 34; `cfg` = `__cfg` = `$('Constants')`, line 50 |
| Build Architect Run Input | 85110abf-cc5e-425b-b9ff-67272623d775 | runner_url | expression `$('Constants').first().json.runner_url`, line 388 |
| Build Worker Run Input | 7a280585-c94b-4f77-a4e2-0b105bf23bb1 | runner_url | `__lessonsCfg` = `$('Constants')`, line 387, read at line 398; `config` = `$('Constants')`, line 345 / 471, read at line 481; expression at line 553 |
| Build Reviewer Run Input | 64aeb5f4-db87-4026-b8d2-212c2b7f05e0 | runner_url | expression `$('Constants').first().json.runner_url`, line 450 |
| Prepare Verdict Retry | e4104c3b-57f0-4396-9f36-bb84993e72db | runner_url | `cfg` = `$('Constants')`, line 3; `cfg.runner_url`, line 26 |
| Prepare Results Update | 7f1b2c3d-0a11-4e55-9a10-results00001 | runner_url | `c` = `$('Constants')`, line 1; `c.runner_url`, line 2 |
| QA Request | 395e2d2c-41c6-5b08-a12a-da989e80d748 | runner_url | expression in the node `url` parameter |
| QA Poll | 4dc0da26-5935-5a67-b08d-a7e4121ed314 | runner_url | expression in the node `url` parameter |
| Architect Start | 6ec4f062-3f94-4b90-b6b2-aa29c56e26db | run_manager_workflow_id | expression `$('Constants').first().json.run_manager_workflow_id` in `workflowId.value` |
| Worker Start | ed579152-0ffb-4654-aacc-c6662a162637 | run_manager_workflow_id | same expression in `workflowId.value` |
| Reviewer Start | fae2a1f6-6773-4915-b7fc-f6e70c4ded5f | run_manager_workflow_id | same expression in `workflowId.value` |
| Reviewer Verdict Retry Start | 7688f46c-9b45-4dc8-b475-92799bb7266f | run_manager_workflow_id | same expression in `workflowId.value` |
| Send a text message | 5490cfed-129c-4f56-bcab-ac4bb88cdf5b | telegram_chat_id | expression in `chatId` |
| Send a text message3 | e91e1c09-f93a-4189-98f9-01a01764bd46 | telegram_chat_id | expression in `chatId` |
| Send a text message5 | bebb83b8-f48c-488d-a5a7-0148e9327b9c | telegram_chat_id | expression in `chatId` |
| Send a text message6 | e851973b-1e18-43d5-a49f-a744ee517675 | telegram_chat_id | expression in `chatId` |
| Send a text message7 | cbdcdb74-4d84-4c84-ba5d-af968a3e82fd | telegram_chat_id | expression in `chatId` |
| QA Notice | 540beb6c-d430-539c-881b-91743417d3dc | telegram_chat_id | expression in `chatId` |
| If | ab340ba4-4a58-4098-bf92-e3c81ba4c62f | telegram_chat_id | expression in the `leftValue` condition |
| If2 | 7836d81c-3c94-4afe-a20f-970b96ec0188 | telegram_chat_id | expression in the `leftValue` condition |
| If3 | 927bdb1e-bbdd-4b2f-88ef-c4899c8132eb | telegram_chat_id | expression in the `leftValue` condition |
| If4 | 3b24b23e-077d-4fca-a630-845a35ad7beb | telegram_chat_id | expression in the `leftValue` condition |
| If5 | 9a9d0373-1159-46d1-bcfc-e4bac6e44a32 | telegram_chat_id | expression in the `leftValue` condition |
| QA Notice Gate? | 19a5b74e-ef85-5382-b490-ed568421fb51 | telegram_chat_id | expression in the `leftValue` condition |

No node reads `retry_delays_json` except Constants; no node reads `telegram_chat_id` as `.item.json` (the older form is
gone: `item.json.telegram_chat_id` count 0, `first().json.telegram_chat_id` count 12).

### Counts in the export

Counted with `grep -o` on the export file (occurrences; `grep -c` counts the single line of the file):

| Column | grep -o count | Nodes (excluding Load Config) | Table rows above |
|---|---|---|---|
| max_attempts_per_task | 6 | 3 (Constants, Advance Task, Retry Current Task) | 3 |
| retry_delays_json | 4 | 1 (Constants) | 1 |
| runner_url | 18 | 10 | 11 (Constants 1 row + 10 readers) |
| run_manager_workflow_id | 7 | 5 (Constants + 4 executeWorkflow nodes) | 5 |
| telegram_chat_id | 14 | 13 (Constants + 12 nodes) | 13 |

The occurrence counts are for the fresh export. The planning doc gives `runner_url` as "12 workflow" (2026-10-21).
The export has 18 occurrences in 10 nodes. UNKNOWN: the reason for the difference (the plan may count another export,
or the QA and Prepare Results Update nodes may be newer than the plan).

Note: `grep -c` on the export returns 1 for every column because the export is one line; use `grep -o | wc -l`.

## Constants node: how it loads hermes_config

Code node "Constants" (id 3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74), `jsCode`, 89 lines. Its input is the Data Table node
"Load Config" (type `n8n-nodes-base.dataTable`, operation `get`, table `hermes_config`, filter `profile_id` = `claude-code`,
limit 1).

Anchors a patch can match, each present once in the export unless stated:

| Line | Exact fragment | Use |
|---|---|---|
| 1 | `const rows = $('Load Config').all().map(item => item.json \|\| {});` | input rows |
| 3 | `const row = rows.find(` | the enabled `claude-code` row |
| 6 | `let retryDelays = [15, 30, 60, 120, 300];` | retry defaults |
| 44 | `return [{ json: {` | output object (one occurrence) |
| 45 | `  run_manager_workflow_id: runManagerWorkflowId,` | output key |
| 46 | `  runner_url: runnerUrl,` | output key (lessons patch anchor) |
| 65 | `  max_tasks: num(row.max_tasks,'max_tasks',1),` | legacy max_tasks (features patch anchor) |
| 66 | `  max_attempts_per_task: num(row.max_attempts_per_task,'max_attempts_per_task',1),` | output key |
| 76 | `  telegram_chat_id: String(row.telegram_chat_id ?? '').trim(),` | output key |
| 79 | `  retry_delays: retryDelays,` | output key |
| - | `const __features = parseFeatures(` | 0 occurrences: the features patch is not in this export |

The row is read from the Load Config items, not from a second query, so one Data Table get serves the whole run.
Defaults for `runner_url` (line 40) and `run_manager_workflow_id` (line 43) are literals in the code; their values are not
written here. A patch that moves these to `engine_json` must keep those literals or read them from a named constant.

## Readers in the ops patches (grep only)

`grep -c` per file (lines containing any of the columns is the same as the line lists below).

| Column | Patch file | Line | What it does |
|---|---|---|---|
| max_attempts_per_task | adaptive_split_patch.py | 210, 221 | expression `$('Constants').first().json.max_attempts_per_task` in a JS string |
| max_attempts_per_task | lessons_patch.py | 53 | `cfg.max_attempts_per_task` in the lessons report |
| max_attempts_per_task | stable_prefix_patch.py | 114, 132, 161 | same expression in the stable-prefix JS strings |
| max_attempts_per_task | stable_prefix_patch.py | 246-251 | assert that Constants has `max_attempts_per_task` |
| max_attempts_per_task | test_features_config_patch.py | 20 | test fixture value (not a reader) |
| runner_url | adaptive_split_patch.py | 871, 878, 884 | expression in node URLs |
| runner_url | lessons_patch.py | 9 (comment), 19 (`CONSTANTS_ANCHOR`), 43, 98, 169 | Constants anchor and `cfg.runner_url` / `__lessonsCfg.runner_url` readers |
| runner_url | report_pack_patch.py | 103 | `config.runner_url` in the report JS |
| runner_url | qa_workflow_patch.py | 291, 293 | check that Constants defines `runner_url` and `runner_api_key` |
| runner_url | qa_workflow_patch.py | 368 | expression in a QA node |
| runner_url | roles_config_patch.py | 88 | `$('Constants').first().json.runner_url` in a JS string |
| telegram_chat_id | qa_workflow_patch.py | 508, 543 | expression in the QA Notice `chatId` and gate |
| telegram_chat_id | telegram_gate_patch.py | 3, 14, 15 | rewrites `$('Constants').item.json.telegram_chat_id` to `.first()`; the export has no `.item.json` form left |
| retry_delays_json | - | - | no patch reader |
| run_manager_workflow_id | - | - | no patch reader |

## Not checked

- The running n8n workflow (needs docker). The export above is what was read.
- The Build Worker Run Input internals beyond the lines listed (only the reads of the five columns were traced).
- `docs/config-columns-plan.md` sections 3-5 were read from retrieved context, not re-opened in full.

## Runner, board and docs readers

Scope: `ahawr-runner/src/ahawr_runner` (Python and JS, `codex_*.py` excluded and not read), `ahawr-runner/tests`,
`docs/` (excluding `docs/cold-prefill`, which is run transcripts), `ahawr-runner/docs`, `README.md`, `.env.example`.
Whole-word counts (`grep -rwo`), read at the time of this inventory:

| Column | src | tests | docs (non-cold, excluding this file) | ahawr-runner/docs |
|---|---|---|---|---|
| max_attempts_per_task | 17 | 18 | 12 | 15 |
| retry_delays_json | 1 | 0 | 1 | 4 |
| runner_url | 21 | 13 | 5 | 4 |
| run_manager_workflow_id | 1 | 0 | 4 | 5 |
| telegram_chat_id | 0 | 1 | 8 | 6 |

Readers of the column value (not names that only look alike; see the note after the list):

- `max_attempts_per_task`: `ahawr-runner/src/ahawr_runner/board_stats.py:185-190` (`_max_attempts`, reads
  `board.hermes_config()`), which feeds the stats at `board_stats.py:291` and `:471`.
- `max_attempts_per_task`: `ahawr-runner/src/ahawr_runner/board_api.py:498` (`_DEFAULT_KEYS`, the placeholder keys)
  read by `_config_defaults` at `board_api.py:511-520` (`store.hermes_config()` at `:513`).
- `max_attempts_per_task`, `max_tasks`, `retry_delays`: key lists in `ahawr-runner/src/ahawr_runner/board_mission.js:58`
  and `board_plan.py:92`. UNKNOWN: how each list is used; not traced.
- `max_attempts_per_task`: `board_mission.js:198` maps the key `attempts_per_task` to it. UNKNOWN: the callers of that map.
- Generic readers: `board_pg.py:529-538` (`SELECT * FROM` the hermes table) returns the whole row, so every column above
  reaches the caller of `hermes_config()`. `board.py:729-731` is the in-memory store's copy.
- Not readers of the column (they read a stats output field): `board_mission.js:401`, `board_tasks.js:92`,
  `board_task.js:107` read `stats.max_attempts_per_task`.
- Write whitelist, not a reader: `board_pg.py:820-827` (`known_hermes_cols` in `update_hermes_config`) lists
  `retry_delays_json`, `runner_url` and `run_manager_workflow_id` among the names it accepts.
- `runner_url` and `run_manager_workflow_id`: no Python or JS reader of the hermes column was found in src. The `runner_url`
  matches are other names: `dashboard.py:72, 88, 122, 161, 239, 257` (`DashboardSettings.runner_url`, from the env
  `DASHBOARD_RUNNER_URL`); `board_ready.py:132-149, 382-393` (a local, from `ANTHROPIC_BASE_URL` or `CLAUDE_RUNNER_URL`);
  `qa_worker.py:101-117` (`_runner_url()`, from the env). `board_ready.py:135-149` checks the runner URL it is given.
- `telegram_chat_id`: no src reader. Tests only: `ahawr-runner/tests/test_board_api_part2.py:124` (a fixture row) and the
  assertion at `:127` that the value is not returned by `_config_defaults`.

Note: `board_stats.py:291, 471`, `board_mission.js:401`, `board_tasks.js:92`, `board_task.js:107` and `dashboard.py` use
the same words for their own fields; they are listed so a search does not count them as readers.

### Fixtures and tests containing the columns (grep only)

- `ahawr-runner/tests/test_board_api_part2.py:124, 127` (`telegram_chat_id`, `runner_url`, `max_attempts_per_task`)
- `ahawr-runner/tests/test_board_pg.py:434, 438` (`max_attempts_per_task`, a row fixture)
- `ahawr-runner/tests/test_mission_stats.py:98` (writes `max_attempts_per_task` through `update_hermes_config`)
- `ahawr-runner/tests/test_board_stats.py:464-483`, `test_board.py:889, 1008`, `test_board_stats.py` (stats and queue overrides)
- `ahawr-runner/tests/fixtures/board_part2/task_stats.json:25`, `ahawr-runner/tests/board_mission.test.js:208`,
  `ahawr-runner/tests/e2e/test_board_views2_e2e.py:59` (stats fixtures, not the hermes row)
- `ahawr-runner/tests/test_dashboard.py:313, 869-1034` and `test_board_ready.py:800-802` (`runner_url` as a
  `DashboardSettings` field, not the column)
- `ahawr-runner/tests/test_qa_workflow_patch.py:416`, `test_report_pack_patch.py:70`, `test_roles_config_patch.py:411`
  (patch fixtures for the `runner_url` anchor)

### Docs naming the columns (grep only)

- `README.md:304, 314, 598, 739, 1205, 1215` (and the Russian copy `:1469, 1579, 1619`)
- `.env.example:243` (`telegram_chat_id`)
- `docs/guards.md:106`, `docs/config-columns-plan.md:25, 28, 71-72, 104, 114`, `docs/review-calibration.md:11, 38, 124`,
  `docs/telegram-bot.md:34, 64, 90, 230, 256`, `docs/retrieval/INTEGRATION.md:65`,
  `docs/mission-reports/mission-report-T013-ru.md:43, 70`
- `ahawr-runner/docs/board-data-model.md:446, 456-457, 465-466, 482-493`, `board-ops-notes.md:431-436`,
  `board-part2-notes.md:99`, `board-design.md:258`, `lessons.md:60, 72, 211`, `qa-acceptance.md:60, 162, 177, 246-247`,
  `roles-config.md:135, 206`, `report-pack.md:78`, `guards-notes.md:200, 374, 383, 717`

## Launch settings save path

Entry: the launch window in `ahawr-runner/src/ahawr_runner/board_edit_win.js` (`openWin` `:836`, `buildWindow` `:847`).
Its Save is `onNext` (`:815-835`), which runs in order: `saveQa()` (`:375-378`), `savePolls()` (`:381-388`),
`saveFeatures()` (`:512-516`), `saveThinking()` (`:546`), `saveRetrieval()` (`:580`), then `save().doSave(box)`.

Routes and handlers (`ahawr-runner/src/ahawr_runner/board_api.py`):

- `GET/PUT /api/board/roles`: `get_roles` `:1570-1573`, `put_roles` `:1575-1582` (global `roles_json`; no JS caller).
- `GET/PUT /api/board/missions/{mission_id}/roles`: `put_mission_roles` `:1595-1609` (per-mission `roles_json` in
  `mission_config`).
- `GET/PUT /api/board/features`: `get_features` `:1611-1615`, `put_features` `:1617-1627`.
- `PUT /api/board/missions/{mission_id}/features`: `put_mission_features` `:1676-1694`.
- `GET/PUT /api/board/thinking`: `:1629-1645`. `GET/PUT /api/board/retrieval`: `:1647-1661`.

Shared checks in `board_api.py`:

- Header: `_require_dashboard_header` `:129-131` (`x-ahawr-dashboard` must be `1`) runs first in every PUT above.
- Validation: `_roles_text` `:134-146` (`roles_config.validate_roles_json`, providers from `registry.profiles()`);
  `put_features` `:1622-1624` (`features_config.validate_features_json`); `_features_text` `:212` (not read in full).
- Version check: each PUT passes `body.get("expected_version")` to the store. `update_hermes_config` in
  `ahawr-runner/src/ahawr_runner/board_pg.py:797-811` reads `updatedAt` with `FOR UPDATE` and raises `_conflict` on a
  mismatch. `ConflictError` (`board.py:48-54`) is mapped to HTTP 409 (`board_api.py:424`).
  UNKNOWN: the in-memory `update_hermes_config` (`board.py:837`) and `set_mission_config`: not read.

Client side, JS:

- Roles (QA): `BoardRoles.saveQaRoles` in `board_roles.js:569-573` sends `{roles_json, expected_version: state.version}`
  and keeps the returned `version`. Polling: `savePolls` `board_edit_win.js:381-388` sends the same PUT after QA and
  carries the version of the first response. Text built by `BoardRoles.pollingRolesText` (`board_roles.js:638`).
- Features: `featPlan` `board_edit_win.js:501-511` calls `featuresSavePlan` (`board_mission_save.js:219-236`): one PUT per
  changed text, to `/api/board/features` or `/api/board/missions/{id}/features`, each with its own `expected_version` and
  the header `X-AHAWR-Dashboard: 1`. `saveFeatures` `board_edit_win.js:512-516` posts them through
  `postJson` (`board_mission_save.js:614-620`, which adds the header) and reloads with `loadFeatures`.
- Thinking and retrieval: `thinkingSavePlan` `board_mission_save.js:241-250` and `retrievalSavePlan` `:252-261`, the same
  pattern (`expected_version` from the state, header set).

Diff and confirm:

- The Review page (page 4) builds the diff in `reviewHtml` `board_edit_win.js:716-723` from `currentDiff`, `qaRows()`,
  `pollRows()`, `featPlan().diff`, `thinkPlan().diff` and `retPlan().diff`. A field with no change sends no request.
- The launch window's Save does not call `window.confirm`. `onSave` in `board_mission_save.js:331-348` is the other entry
  point, with the confirm at `:348`.

Columns: the five columns in this inventory have no UI field in the launch window, and no route passes them to
`update_hermes_config`. The four calls are `board_api.py:1580` (`roles_json`), `:1625` (`features_json`), `:1643`
(`thinking_json`) and `:1661` (`retrieval_json`). UNKNOWN: how `max_attempts_per_task`, `retry_delays_json`, `runner_url`,
`run_manager_workflow_id` and `telegram_chat_id` are written today (the n8n UI or an operator script is the likely path;
not checked).

Functions to reuse for a save path in this window: `_require_dashboard_header`, `_roles_text`, `_stamp`, `_config_version`
(`board_api.py:331`, `:337`), `update_hermes_config` (the store method), `BoardRoles.saveQaRoles`, `BoardRoles.pollingRolesText`,
`featuresSavePlan`, `thinkingSavePlan`, `retrievalSavePlan`, `postJson` (`board_mission_save.js:614`) and `save()`
(`board_edit_win.js:105`).

## Not checked (inventory)

- `codex_*.py` files: excluded by request, not read.
- The n8n workflow export: not read in this section; the workflow readers above are from the earlier sections.
- The `board_api.py` `_store_guard` and `_stamp` bodies (`:116`, `:331`): located, not read.

## Key mapping and defaults

Target: the five loop and infrastructure columns move to `engine_json` (`docs/config-columns-plan.md`, section 3). Line
numbers are the 1-based lines of the Constants `jsCode` in `scratchpad/live_workflow_export.json` (node id
`3c50a3dc-f6b1-48a3-a17f-6b5e1d522b74`), not of the JSON file. `max_tasks` is NOT moved: it lives in `features_json`
(`ahawr-runner/docs/features-config.md`, flattened by `flatten()`).

| Legacy column | engine_json key | Type | Validation (Constants code) | Default today (file:line) |
|---|---|---|---|---|
| `max_attempts_per_task` | `max_attempts_per_task` | number (integer intended) | `num(..., 1)`: finite and >= 1, otherwise throws (line 66) | None in code: a missing or non-numeric value throws. Live row value UNKNOWN; `docs/config-columns-plan.md:25` records 5. Runner side: no default, `_max_attempts` returns `None` when absent (`ahawr-runner/src/ahawr_runner/board_stats.py:190-193`) |
| `retry_delays_json` | `retry_delays` | array of numbers >= 0 | Parsed from the JSON string (see below); each element `Number(v)` must be finite and >= 0 (lines 7-12) | `[15, 30, 60, 120, 300]` (line 6, `let retryDelays`) |
| `runner_url` | `runner_url` | string, base URL | Trimmed, trailing `/` removed (line 40); no scheme or format check | `http://ahawr-runner:8700` (line 40). Same value as a runner-side constant, `ahawr-runner/src/ahawr_runner/qa_worker.py:56` |
| `run_manager_workflow_id` | `run_manager_workflow_id` | string (n8n workflow id) | Trimmed; empty falls back to the default (line 43); no format check | `nhjwX1G7FiVTO2Ah` (line 43) |
| `telegram_chat_id` | `telegram_chat_id` | string (chat id) | Trimmed (line 76); no check | None: an absent value is the empty string (line 76). The value is not recorded here |

Precedence, for each key: the `engine_json` key if it is present, then the legacy column, then the default in the table.
An empty string or a blank value counts as absent, as the Constants code already treats it for the string keys. The
current code reads only the legacy columns; the `engine_json` step is the target, not today's behaviour.

Patch: `data-tables/ops/engine_config_patch.py` (a file; not applied to the live workflow) embeds the pure core of
`board_engine.js` in `Constants` and reads the five keys through it. Nodes other than `Constants` that read these columns
directly: none. Every other reader uses `$('Constants')` or a local bound to it (checked on the scratchpad export), so no
second patch is needed. Known gaps of the patch:

- A blank legacy `max_attempts_per_task` with an empty `engine_json` gives the core default `5`; the current code throws.
- An invalid `engine_json` makes `Constants` throw a message that names no key, because the core can quote a non-string
  `telegram_chat_id`. Upgrade: make `checkText` in `board_engine.js` omit the value.

Parsing of the legacy `retry_delays_json` string (`retry_delays` in `engine_json` must follow the same rule):

- Trim the string. Empty means absent and the default `[15, 30, 60, 120, 300]` applies (lines 6-7).
- `JSON.parse` it (line 9). The result must be an array and every element must satisfy `Number(v)` finite and >= 0 (line
  10). The array is then mapped with `Number` (line 11), so numeric strings are accepted.
- Any failure throws `Invalid retry_delays_json: ...` (line 12) and the run does not start.
- UNKNOWN: whether an empty array `[]` is meant to be valid. The code accepts it and produces no delays.

Facts that are UNKNOWN:

- The live `hermes_config` row values for all five columns. The row is not readable without docker; only the export's code
  defaults are written above.
- Whether `max_attempts_per_task` must be an integer. The code accepts any finite number >= 1.
- The unit of `retry_delays`. The values look like seconds, but no code or doc names the unit; `docs/naming.md` asks for
  a unit in the name (for example `retry_delays_seconds`), while the target shape in `docs/config-columns-plan.md:71`
  uses `retry_delays`. The key name is a decision for the operator, not changed here.

## Отчёт: настройки движка в `engine_json` (T001A-T006A)

Цель: пять циклических и инфраструктурных значений (`max_attempts_per_task`, `retry_delays`, `runner_url`,
`run_manager_workflow_id`, `telegram_chat_id`) читаются из одного JSON-ключа `engine_json` в `hermes_config`. Для ключа,
которого в JSON нет или он пуст, действует легаси-колонка, а при её отсутствии значение по умолчанию. Версия не меняется.

### Что изменено (file:line)

- `ahawr-runner/src/ahawr_runner/board_engine.py` (новый): значения по умолчанию `:10-15`, колонки `:17-24`,
  `parse_engine` `:93`, `validate_engine_json` `:119`, `flatten` `:124`.
- `ahawr-runner/src/ahawr_runner/board_engine.js` (новый): то же ядро для браузера и для `Constants`
  (`parseEngine` `:84`, `flatten` в конце ядра, экспорт `BoardEngine`).
- `ahawr-runner/src/ahawr_runner/board_api.py:1671-1687`: `GET /api/board/engine` и `PUT /api/board/engine`
  (заголовок `X-AHAWR-Dashboard`, проверка текста до сохранения, `expected_version`).
- `ahawr-runner/src/ahawr_runner/board_stats.py:25`, `:185-191`: `configured_value` читает значения через `flatten`.
- `ahawr-runner/src/ahawr_runner/board_edit_win.js`: ключи `:36-37`, блок «Настройки движка» `:598-` (`makeEngineFields`
  `:619`, `fillEngine` `:643`, `engineSavePlan` `:705`, `saveEngine` `:722`). `telegram_chat_id` в окне только для чтения.
  В этом файле есть и правки других задач, поэтому весь `git diff` файла больше одного блока.
- Подписи: `board_i18n_en.json:59-75`, `board_i18n_ru.json:59-75`, `board_i18n.js:113-129` (ключи `ew.eng.*`, внутри
  `BUNDLED_EN` `:19-677`). `board.html:53` загружает `board_engine.js`.
- Тесты: `ahawr-runner/tests/test_board_engine.py`, `test_propose_engine_json.py`, `board_engine.test.js`,
  `engine_cases.json` (общие случаи для Python и JS), `board_edit_win_features.test.js:371-` (раздел engine).
- Операторские скрипты, не в git: `data-tables/ops/propose_engine_json.py` (сухой прогон),
  `data-tables/ops/engine_config_patch.py` (патч `Constants`), `data-tables/ops/test_engine_config_patch.py`.

### По задачам T001A-T006A

Заметки самих задач мне не передали, поэтому соответствие ID и частей ниже сделано по содержимому файлов и записи
CHANGELOG «Step: engine settings». Сверьте с заметками задач.

- T001A, ядро значений: сделано. `board_engine.py:9-124` (`parse_engine` `:93`, `validate_engine_json` `:119`, `flatten` `:124`);
  `board_engine.js` (`parseEngine` `:84`, `validateEngineJson` `:113`, `flatten` после них).
- T002A, маршруты: сделано. `board_api.py:1671-1687`, `GET` и `PUT /api/board/engine`, с заголовком и `expected_version`.
- T003A, `error_rules` (правила ошибок для Evaluate Status и Retry Controller, таблица правил в окне): НЕ РЕАЛИЗОВАНО.
  `grep -rn error_rules ahawr-runner/src data-tables/ops docs` даёт одно совпадение: `data-tables/ops/transient_400_patch.py:8`,
  строка docstring «Interim step: engine_json.error_rules replaces the sets». Чтения ключа нет ни в `board_engine.py`,
  ни в `board_engine.js`, ни в `board_api.py`, ни в `board_edit_win.js`; таблицы правил в окне нет (слово «rule» в
  `board_edit_win.js` встречается только в разделе правил миссии `:24`, `:765-767`). Остаются правила по умолчанию из
  `transient_400_patch.py:5-8`: HTTP 400 и слова «модель больше не доступна» считать временной ошибкой; два повтора Retry
  Controller; затем переход на модель следующей попытки, как для 429 (`failover_patch.py`). Этот ops-патч не часть плана
  `engine_json`, и применён ли он к живому workflow, я не проверял.
- T004A, читатели значений: сделано. `board_stats.py:184-191` (`configured_value`, через `flatten`), его вызовы
  `board_stats.py:201` и `board_api.py:526` (`_config_defaults` `:512`).
- T005A, интерфейс: сделано. `board_edit_win.js:36-37` (ключи), `:598-` (блок), `:619` `makeEngineFields`, `:643`
  `fillEngine`, `:705` `engineSavePlan`, `:722` `saveEngine`; подписи `board_i18n_en.json:59-75`, `board_i18n_ru.json:59-75`,
  `board_i18n.js:113-129` (внутри `BUNDLED_EN` `:19-677`); `board.html:53`.
- T006A, проверки и отчёт: сделано. Раздел этого документа и проверки в финальном отчёте.

### Шаги оператора

1. Сухой прогон. Скопируйте строку `hermes_config` профиля `claude-code` в файл JSON (вручную, на рабочей копии) и выполните
   `python data-tables/ops/propose_engine_json.py <файл.json>`. Скрипт только печатает предложенный `engine_json`
   (значение `telegram_chat_id` не печатается) и ничего не записывает.
2. Сохраните предложенный JSON в `engine_json`: в окне настроек запуска раздел «Настройки движка» и Save либо
   `PUT /api/board/engine` с заголовком `X-AHAWR-Dashboard: 1` и `expected_version`.
3. Патч свежего экспорта: скопируйте экспорт workflow в отдельный файл и выполните
   `python data-tables/ops/engine_config_patch.py <копия.json>`. Скрипт переписывает файл на месте, поэтому не запускайте его
   на оригинале. Повторный запуск печатает `already patched`. Импорт патченного файла в n8n выполняете вы сами.
4. Позже, когда `engine_json` проверен в n8n, удалите легаси-колонки в интерфейсе n8n (не SQL). До удаления
   `telegram_chat_id` должен быть в `engine_json`: после удаления пустое значение даст пустой chat id.

### Что не проверялось вживую

- Импорт патченного экспорта в n8n и работа `Constants` в живом workflow.
- Окно настроек в браузере и `PUT /api/board/engine` на живой доске.
- Значения легаси-колонок в живой строке `hermes_config`: их не читал ни один шаг, сухой прогон покажет их только при запуске
  оператором.

### Откат

Очистите `engine_json` (сохранить пустую строку через окно или `PUT`). Пустой `engine_json` даёт легаси-значения, что
закреплено тестом `golden_empty` в `engine_cases.json`. Возвращать старый экспорт workflow не нужно: патченный `Constants`
читает легаси-колонки, если ключа в JSON нет. Исключение: см. риск про пустой `max_attempts_per_task` ниже.

### Открытые риски

- Патч при пустом `engine_json` и пустом легаси `max_attempts_per_task` даёт 5, а текущий код бросает ошибку (известный
  разрыв в `engine_config_patch.py`).
- Невалидный `engine_json` заставляет `Constants` бросить ошибку без имени ключа (`board_engine.js` `checkText`).
- Окно настроек показывает источник `default`, когда `engine_json` пуст, даже если легаси-колонка задана
  (`board_edit_win.js:643-655` берёт `ENGINE_DEFAULTS`). Это расходится с записью в CHANGELOG (`engine_json` или легаси).
- Единица и имя `retry_delays` не решены (см. раздел выше).
- Разделы инвентаря выше (`Launch settings save path`, `UNKNOWN`) описывают состояние до этого шага.

### Версия

Миссия просит версию `0.12.x`. `ahawr-runner/pyproject.toml:7` и `ahawr-runner/src/ahawr_runner/api.py:308` уже стоят на
`1.0.0`, поэтому `0.12.x` ниже текущей версии. Выбор версии за оператором; в CHANGELOG для этого шага записано «no version bump».

### Конфликт с миссией

Правила миссии 1 и критерии приёмки описывают функцию уроков (lessons): `ahawr-runner/src/ahawr_runner/board_lessons.py`,
`board_lessons_extract.py`, `data-tables/ops/lessons_patch.py`, `ahawr-runner/docs/lessons.md`. Этот план `engine_json` её не
реализовал и не трогал. Текст правил миссии я не сверял дословно: он не передан в задаче.

## Error rules

Read-only inventory. No code was changed by this step. The rule list is a plan: nothing in the code reads `error_rules` yet
(`grep -rn error_rules ahawr-runner/src data-tables/ops docs` finds only the docstring at `transient_400_patch.py:8`
and the notes in this file).

Sources: `scratchpad/live_run_manager_export.json` (Run Manager, a list with one workflow; line numbers are 1-based lines of
the node's `jsCode`). The export already contains the `transient_400_patch.py` and `failover_patch.py` changes (Evaluate
Status line 28 has 400; Retry Controller line 4 has `failoverAfter`). It has no `Constants` node, so `engine_config_patch.py`
cannot be checked on it. Main workflow nodes come from `scratchpad/live_workflow_export.json`.

### Inventory: every decision point

| # | Where | Exact literal values | Decides today |
|---|---|---|---|
| 1 | Normalize Request, line 24 | `max_retries`: `Math.max(0, Number(i.max_retries ?? 3))` | Retry limit of the run (default 3) |
| 2 | Normalize Request, lines 26-45 | `retry_delays` default `[15,30,60,120,300]` (line 44); accepts an array or a JSON string | Delays between retries |
| 3 | Normalize Request, state | `fallback_provider`, `fallback_model`, `full_input`, `failed_over_from` (added by `failover_patch.py` NORMALIZE_NEW) | Failover target and the input to resend |
| 4 | Evaluate Status, `const match` | http from text: the regex in "Regexes" below; used only when no envelope or body status | `httpCode` of a failed poll |
| 5 | Evaluate Status, line 26 | `runNotFound`: http 404; errorCode `run_not_found` or `not_found`; text `run not found` or `run_not_found` | Not transient: route `restart_session` or `start_new` |
| 6 | Evaluate Status, line 28 | `transientCodes = new Set([400,408,409,425,429,500,502,503,504,529])` | Transient when `httpCode` is in the set |
| 7 | Evaluate Status, line 29 | `transientWords`: the 14 words in "Legacy list" below | Transient when a word is in the lower-case error text |
| 8 | Evaluate Status, lines 32-33 | errorCode contains `context_window`; regex in "Regexes" below | `contextOverflow`: transient |
| 9 | Evaluate Status, line 34 | `transient = !runNotFound && status==='failed' && (contextOverflow \|\| code \|\| word)` | Selects `transient_error` |
| 10 | Evaluate Status, lines 37-43 | route: `transient_error`; `permanent_failure` for any other failed run; `timeout` when `polls >= max_polls` (default 40) | Final route of the poll |
| 11 | Transient Error? node | route equals `transient_error`: to Retry Controller; else to Still Running? | Enters the retry path |
| 12 | Retry Controller, line 2 | `max = Number($json.max_retries ?? 3)` | Retry limit |
| 13 | Retry Controller, line 3 | default `[15,30,60,120,300]`; delay index `min(next-1, length-1)` (line 15) | Delay per retry |
| 14 | Retry Controller, line 4 | `failoverAfter = Math.min(max, 2)` (= `FALLBACK_AFTER_RETRIES = 2`, `failover_patch.py:27`) | Failover after 2 retries |
| 15 | Retry Controller, lines 5-10 | failover when `retries >= failoverAfter && fallback_model && !failed_over_from`: resets `retry_count`, clears `session_id`, `input = full_input` | Failover, once per run |
| 16 | Retry Controller, lines 12-13 | `retries >= max`: `route: 'provider_unavailable'`, `final_status: 'failed_provider_unavailable'` (Build Result copies the route) | Fail after the retries |
| 17 | Retry Controller, line 15 | `retry_delay_seconds = delays[min(next-1, len-1)] ?? 30` | Wait before the next run |
| 18 | Retry Wait node | `amount = retry_delay_seconds ?? 15` (no unit in the node) | Wait; unit UNKNOWN |
| 19 | Retry Exhausted? node | `retry_exhausted` true: to Build Result; false: to Retry Wait | Exit or wait |
| 20 | Has Session?, Mark Compression Target, Evaluate Compression | runs after Retry Wait and before each start with a session; the runner's `POST /v1/sessions/{id}/compact` compacts only when the context is large (its own settings, named in the node comment) | Compaction; the rule does not force it (see Open questions) |
| 21 | claude_cli.py, line 20 | `SESSION_MISSING_RE = no conversation found` | `session_not_found` (line 463) |
| 22 | claude_cli.py, line 21 | `LIMIT_RE`, regex in "Regexes" below | `rate_limit` (line 473) |
| 23 | claude_cli.py, line 22 | `OVERLOAD_RE = overloaded` | `overloaded` (line 475) |
| 24 | claude_cli.py, line 25 | `RETRY_HTTP = {rate_limit: 429, overloaded: 529, server_error: 500}` | `http_code` for those codes (lines 479-484) |
| 25 | claude_cli.py, line 26 | `TRANSIENT_HTTP = {408, 429, 500, 502, 503, 504, 529}` (no 400, 409, 425) | Keeps `api_status` as `http_code` (lines 467, 481) |
| 26 | claude_cli.py, line 441 | timeout: code `timeout`, http 408 | Runner timeout becomes transient in Evaluate Status |
| 27 | claude_cli.py, lines 459-491 | codes `max_turns`, `max_budget`, `session_not_found`, `rate_limit`, `overloaded`, `server_error`, `api_error` (or `terminal_reason`), `cli_error`, `subtype` or `execution_error` | Failure codes; only the transient ones reach the retry path, as http |
| 28 | claude_cli.py, lines 496-499 | appends ` (rate limit)` and ` (overloaded)` to the text when missing | Lets the words path see the category |
| 29 | transient_400_patch.py, lines 17-20 | adds 400 to the codes; adds `no longer available`, `no provider serves`, `model not found` to the words | Applied: export lines 28-29 contain them |
| 30 | failover_patch.py, lines 27 and 35 | `FALLBACK_AFTER_RETRIES = 2`; `FAILOVER_LOOK_AHEAD = 5` | Worker failover timing and lookahead |
| 31 | reviewer_failover_patch.py, lines 20 and 24-36 | `FAILOVER_LOOK_AHEAD = 5`; `fallback_provider`/`fallback_model` set in Build Reviewer Run Input; no failover when the provider is `local` | Reviewer failover (main workflow; not in the Run Manager export) |
| 32 | engine_config_patch.py, lines 38-39, 53-54, 65-66 | carries `max_attempts_per_task`, `retry_delays`, `telegram_chat_id` into Constants; does not carry `max_retries` or any error rule | Constants fields the nodes read |
| 33 | live_workflow_export.json, Constants | `worker_max_provider_retries: num(row.worker_max_retries, ..., 0)` (and the reviewer and architect fields); read by Build Worker Run Input as `max_retries` | Retry limit of each role; live row value UNKNOWN |

Regexes (exact text, applied to the lower-case error text):
- Evaluate Status, context overflow: `/context ?window ?exceeded|exceeds the available context|context length|prompt is too long|maximum context/`
- claude_cli.py line 21 `LIMIT_RE` (IGNORECASE): `usage limit|rate limit|hit your limit|limit reached`
- claude_cli.py line 22 `OVERLOAD_RE` (IGNORECASE): `overloaded`
- claude_cli.py line 20 `SESSION_MISSING_RE` (IGNORECASE): `no conversation found`
- Evaluate Status, http from text: `/\b(4\d\d|5\d\d)\b/`

Retry counts and delays in the sources:
- Retry limit (`max_retries`): default 3 in Normalize Request (line 24) and Retry Controller (line 2); roles defaults worker 3,
  architect 5, reviewer 5 (`ahawr-runner/src/ahawr_runner/roles_config.py:36-38`, `board_roles.js:28-30`).
- Minimum 0 (`board_roles.js:25`, `roles_config.py:33`). No upper bound is enforced anywhere in the sources.
- Delays: `[15, 30, 60, 120, 300]` (Normalize Request line 44, Retry Controller line 3). Unit: UNKNOWN.

### Rule format

A rule is an object `{id, match{http[], words[], codes[]}, action, retries, note}`:
- `id`: string, unique within the list.
- `match.http`: list of integers. `match.words`: list of strings, stored in lower case. `match.codes`: list of strings, lower case.
- `action`: `retry` | `failover` | `compact` | `fail`.
- `retries`: integer from 0 to 5. The upper bound is the largest retry default in the sources (5, roles defaults above).
- `note`: string for the operator.

Limits: at most 40 rules; ids unique; `action` and `retries` checked on save. None of this is enforced yet.

Matching semantics:
- The list applies only to a failed run that is not run-not-found. Those two guards stay in code (Evaluate Status line 26 and line 34).
- Rules are evaluated in array order; the first rule that matches wins. If none matches, the run fails (today's `permanent_failure`).
- A rule matches when every non-empty match field matches. An empty field is ignored; a rule with all fields empty matches every failed run.
- `http`: equal to the run's `httpCode` (see inventory row 4).
- `words`: a word matches when it is a substring of the lower-case error text (Evaluate Status uses `lower.includes`). Matching is case-insensitive because both sides are lower case.
- `codes`: a code matches when it is a substring of the lower-case runner failure code (Evaluate Status uses `errorCode.includes` for `context_window`).
- `http` AND `words` when both are given: both must match. A bare 429 is then not matched by a rule that also lists words.

Meaning of `action`:
- `retry`: retry up to `retries` times, waiting `retry_delays` as Retry Controller does today. After `retries` retries, fail over the way `FALLBACK_AFTER_RETRIES` does today (a fallback model when one exists, once per run, in a fresh session), or fail (`provider_unavailable`) when no failover target exists. This is the exact current behaviour: `failoverAfter = min(max, 2)`, failure at `max`.
- `failover`: switch to the fallback model at once. NOT in the current code: the Retry Controller always waits for `failoverAfter` retries first. The at-once form is UNKNOWN.
- `compact`: same counting and waiting as `retry`. The runner compacts the saved session before the next start when its own threshold says so. Forced compaction is UNKNOWN.
- `fail`: no retry; route `permanent_failure`.

### Legacy list (reproduces today's behaviour)

Today's rule is an OR of three checks. Because `AND` applies inside one rule, each check is its own rule. Order matters: the context rules come first, as `contextOverflow` does in Evaluate Status.

1. `legacy-context-code`: `match.codes = [context_window]`, action `compact`, retries = role `max_retries`. Verified: Evaluate Status line 32.
2. `legacy-context-words`: `match.words = [context window exceeded, contextwindow exceeded, context windowexceeded, contextwindowexceeded, exceeds the available context, context length, prompt is too long, maximum context]`, action `compact`, retries = role `max_retries`. Verified: the regex on line 33 expands exactly to these 8 words (`context ?window ?exceeded` has four spellings).
3. `legacy-transient-http`: `match.http = [400, 408, 409, 425, 429, 500, 502, 503, 504, 529]`, action `retry`, retries = role `max_retries`. Verified: Evaluate Status line 28. The 400 came from `transient_400_patch.py:18`; before that patch the set was `[408,409,425,429,500,502,503,504,529]`.
4. `legacy-transient-words`: `match.words = [temporarily overloaded, upstream error, upstream request failed, service unavailable, overloaded, timeout, timed out, connection reset, connection refused, rate limit, too many requests, no longer available, no provider serves, model not found]`, action `retry`, retries = role `max_retries`. Verified: Evaluate Status line 29 (14 words; 11 before the patch, plus the 3 from `transient_400_patch.py:20`).

`retries` = role `max_retries` is not an integer. The format has no value for "use the role's count" (see Open questions).

### RECOMMENDED preset (mission "Default rules to ship")

Source: `data-tables/ops/_make_error_rules_mission.py`, the OBJECTIVE text. The mission gives no retry counts; "retry then failover" and "retry twice" use 2 here, the value of `FALLBACK_AFTER_RETRIES`. Each rule is in the mission's order. Verdict per rule: verified means every literal was found in the sources; UNKNOWN means at least one literal or the behaviour was not found, with the part named.

1. `weekly-limit-text`: `words [weekly limit]` (no http), action `retry`, retries 2. UNKNOWN. Added by review so the weekly-limit text matches without an http code. `weekly limit` is in no source list; it appears only in the docstring of `reviewer_failover_patch.py:5`.
2. `rate-limit-429`: `http [429]`, `words [rate limit, too many requests]`, action `retry`, retries 2. UNKNOWN. 429 (line 28) and `rate limit`, `too many requests` (line 29) are verified. Also `LIMIT_RE` does not match `You've hit your weekly limit` (it needs `hit your limit`). With the AND rule, a bare 429 without a word would fail (it is not transient in the new list), while today it retries.
3. `transient-5xx`: `http [408, 409, 425, 500, 502, 503, 504, 529]`, `words [timeout, overloaded, upstream, connection reset]`, action `retry`, retries 2. UNKNOWN. All 8 codes (line 28), `timeout`, `overloaded`, `connection reset` (line 29) are verified. The bare word `upstream` is not in the list: only `upstream error` and `upstream request failed` are. With AND, a 5xx without one of these words would fail.
4. `model-gone`: `http [400, 404]`, `words [no longer available, no provider serves, model not found, does not exist]`, action `failover`, retries 0. UNKNOWN. 400 (line 28) and the first three words (line 29) are verified. `does not exist` is in no source. 404 cannot reach this rule: Evaluate Status line 26 marks http 404 as run-not-found, which is never transient. `failover` at once: see the Meaning of action.
5. `credits-402`: `http [402]`, `words [insufficient credits, credit balance, quota exceeded, usage limit]`, action `failover`, retries 0. UNKNOWN. 402 is in no set, so today the run is `permanent_failure` (line 42). None of the four words is in Evaluate Status. `usage limit` is only in `LIMIT_RE` (claude_cli.py:21), which does not feed the words list.
6. `auth-401-403`: `http [401, 403]`, `words [invalid api key, expired]`, action `failover`, retries 0. UNKNOWN. 401, 403 and both words are in no source; today these runs are `permanent_failure`.
7. `context-413`: `http [413]`, `words [context length]`, action `compact`. UNKNOWN. `context length` is verified (line 33). 413 is in no source. `compact` as described above.
8. `other-400`: `http [400]`, action `retry`, retries 2. VERIFIED for the retry path: 400 in transientCodes (line 28), retry then failover after `FALLBACK_AFTER_RETRIES = 2` (failover_patch.py:27). It must come after rule 3, which takes 400 with the words first.
9. `else-fail`: empty `match`, action `fail`. VERIFIED: a failed, non-transient run is `permanent_failure` (line 42), and Build Result copies the route into `final_status`.

Result: the RECOMMENDED list is not a superset of the LEGACY list. Shipping it as written would stop retrying some runs that retry today, for example a 503 without a listed word, or `connection refused`. See Open questions.

### Open questions

- Mission text. `data-tables/ops/_make_error_rules_mission.py` has the RULES and ACCEPT lists copied from a roles mission: RULES[0] points to CLAUDE.md and naming, and ACCEPT[0] to `docs/config-roles-step1.md` and `roles_options_patch.py`. Neither mentions lessons files. The note above (`Конфликт с миссией`) describes the lessons reading, which this file does not contain. Treated as a copy-paste error; this plan changes only the files named in T002-T006.
- AND between `http` and `words`. The legacy list needs OR, which needs separate rules; the recommended list needs AND. Decide: keep AND and write separate rules for OR cases, or give a rule an OR mode.
- Legacy `retries`. Today the count is the role's `max_retries` (Constants `worker_max_provider_retries`, default 3 only in roles defaults). The format needs a way to say "use the role's count", or the legacy rules copy the value at save time.
- `failover` at once and `compact` forced: no code path for either. Decide whether they are added or the rules use `retry` with a counter of 0.
- 404 in the recommended rule 3 cannot match (run-not-found precedes). Decide whether run-not-found wins or the rule is removed.
- Per-rule delays: the format has none; all rules use `retry_delays`. The unit of `retry_delays` stays UNKNOWN (see the Key mapping section).
- The live values of `worker_max_retries`, `reviewer_max_retries`, `architect_max_retries` and of the Retry Wait unit are not readable without docker: UNKNOWN.

## Правила ошибок: шаги оператора

Раздел для оператора. Ничего из описанного здесь в этой задаче не выполнялось: ни запусков, ни вызовов провайдеров, ни импорта в n8n.

Что есть в коде и данных:
- `ahawr-runner/src/ahawr_runner/board_engine.py`: проверка списка, `LEGACY_ERROR_RULES` (поведение как сейчас) и
  `RECOMMENDED_ERROR_RULES` (предустановка из раздела «RECOMMENDED preset»).
- `data-tables/ops/error_rules_patch.py` (не в git): правит экспорт Run Manager.
- Окно настроек движка (`board_edit_win.js`): таблица правил и кнопка «Load recommended».

### Порядок патчей на свежем экспорте

Все три патча работают с экспортом Run Manager (`<run_manager.json>`), в этом порядке:

1. `failover_patch.py`
2. `transient_400_patch.py`: по docstring его можно запускать и до, и после шага 3, результат тот же.
3. `error_rules_patch.py`

`engine_config_patch.py` работает с основным workflow (узел Constants, файл `<workflow.json>`), не с экспортом
Run Manager, и не трогает `error_rules`. Поэтому он не является предусловием `error_rules_patch.py`: в docstring
патча такого требования нет.

Перед запуском сохраните копию исходного экспорта: патч переписывает файл на месте (через `.tmp` и `os.replace`).
Патч идемпотентен и не пишет файл, если какой-либо якорь найден не ровно один раз.

```
python transient_400_patch.py <копия>/run_manager.json
python error_rules_patch.py <копия>/run_manager.json
```

### Импорт результата

Патч ничего не импортирует в n8n. Импорт делает оператор: импортировать изменённый файл Run Manager через интерфейс n8n.
Перед импортом проверьте, что патч внёс изменения: `grep -c error_rule_id <файл>` должен вернуть ненулевое число.

### Включение RECOMMENDED

1. В окне настроек движка нажмите «Load recommended» (ключ `ew.eng.rule.recommended`): таблица заполнится 9 правилами.
2. Сохраните через обычное сохранение: окно покажет diff, запрос `PUT /api/board/engine` идёт с версией, прочитанной при
   загрузке (`board_api.py`, эндпоинт `engine_json`).
3. Внимание: сохранение записывает список в `engine_json`, но ни один патч ещё не передаёт `error_rules` из запроса в
   Normalize Request. Пока этот шаг не сделан, сохранённый список не меняет поведение запусков.
4. Из 9 правил RECOMMENDED 7 помечены UNKNOWN в разделе «RECOMMENDED preset». Включать предустановку целиком
   до их проверки не рекомендуется.

### Откат

Очистите таблицу правил в окне настроек и сохраните. Пустой список удаляет ключ `error_rules`, и
`board_engine.py:342` (`rules or LEGACY_ERROR_RULES`) возвращает поведение по умолчанию. Откат самого патча: вернуть
сохранённую копию исходного экспорта и импортировать её.

### Что не проверялось вживую

- Реальные запуски миссий: не выполнялись.
- Вызовы провайдеров: не выполнялись.
- Импорт в n8n: не выполнялся; экспорт взят из `scratchpad/live_run_manager_export.json`, а не свежий.
- Окно настроек в браузере и `PUT /api/board/engine` на живой доске: не проверялись.
- Правила RECOMMENDED: 7 из 9 UNKNOWN; живые значения `max_retries` неизвестны.
