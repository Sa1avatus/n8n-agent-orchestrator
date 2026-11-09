# Mission overrides: readers in the workflow and the ops patches

Inventory for step 5 of `docs/config-columns-plan.md` (`overrides_json` on `missions`, the `state_namespace` alias).
Read only: nothing here changes a column, a node or a patch.

## Scope note

RULES 1 and ACCEPTANCE CRITERIA in the mission text describe the lessons mission. This work follows the
`overrides_json` OBJECTIVE: the inventory of the readers of the mission override columns (`retrieval_corpora_json`,
`state_namespace`, `overrides_json`, and the planned `mission_roles_json` / `mission_features_json`).

## Sources and what was checked

- Workflow: `scratchpad/live_workflow_export.json` (operator copy of the live export; workflow "AHAWR v13 — Claude Code",
  102 nodes). The file is one line of JSON, so workflow readers are given by node name, not by line.
  File date 2026-10-31 15:14. Patches changed after that date (`retrieval_config_patch.py` 18:13,
  `engine_config_patch.py` 18:56, `thinking_config_patch.py` 17:52): whether the live export at launch matches this copy is UNKNOWN.
- Patches: `data-tables/ops/*_patch.py` (grep for the names above, then the context of each hit).
- Not used: `AHAWR_Workflow.json` (not what runs, per `CLAUDE.md`).

Counts below are the number of mentions of the name in each node (parameters, whole node JSON).

## Readers: workflow and patches

### Workflow (live export copy)

| Column | Node (type) | Mentions | What the node does with it |
|---|---|---|---|
| `retrieval_corpora_json` | `Mission` (code) | 2 | `parseCorpora(row.retrieval_corpora_json)` builds the corpus list; the exact fallback order in the live code is not traced |
| `retrieval_corpora_json` | `Constants` (code) | 2 | `parseCorpora(row.retrieval_corpora_json)` is the fallback behind `mission.retrieval_corpora` |
| `mission_roles_json` | `Constants` (code) | 1 | `mission_roles_json: String(mission.roles_json ?? '')`, a role override from the mission row, passed on to the run inputs |
| `mission_roles_json` | `Build Architect Run Input` (code) | 1 | `mergeRoles(parse(config.roles_json), parse(config.mission_roles_json))` |
| `mission_roles_json` | `Build Worker Run Input` (code) | 1 | same `mergeRoles` line |
| `mission_roles_json` | `Build Reviewer Run Input` (code) | 1 | same `mergeRoles` line |
| `mission_roles_json` | `Split Task Notice` (code) | 1 | same `mergeRoles` line |
| `state_namespace` | `Mission` (code) | 3 | reads `row.state_namespace`, throws when it is empty, emits `state_namespace` |
| `state_namespace` | `Constants` (code) | 3 | reads `mission.state_namespace`, throws when it is empty, emits `state_namespace` |
| `state_namespace` | `Advance Task` (code) | 1 | `body: { mission: String(cfg.state_namespace) }` in a POST |
| `state_namespace` | `Load Task State`, `Persist Task Progress` and the other `Load*` / `Persist*` Data Table nodes (dataTable) | 1 to 2 each | `state_key` value: `={{$('Constants').first().json.state_namespace}}` |
| `state_namespace` | `Build Worker Retrieval Request`, `Build Reviewer Retrieval Request` (code) | 2 each | `trace.state_namespace` from Constants |
| `state_namespace` | `Prepare Results Update` (code) | 1 | `state_key: c.state_namespace` from Constants |
| `state_namespace` | `Retry Current Task`, `Reviewer State`, `Build *_Run_Input`, `Prepare Verdict Retry` (code, set) | 1 to 2 each | not traced further in this read: the exact use is UNKNOWN |
| `overrides_json` | none | 0 | not read: the column is planned, not in the export |
| `mission_features_json` | none | 0 | not read in the export; `features_config_patch.py` adds the read when it runs at launch |

The export shows the roles core already in place (`mergeRoles` in the run-input nodes), so `roles_config_patch.py`
appears applied to this copy. Whether `retrieval_config_patch.py` is applied is UNKNOWN: the export's `Constants` still
has `parseCorpora(row.retrieval_corpora_json)`, the line that patch replaces.

### Patches in `data-tables/ops`

| Column or name | File:line | Reader or writer | What it does |
|---|---|---|---|
| `mission_features_json` | `features_config_patch.py:29` | reader (emitted into `Constants`) | `parseFeatures(row.features_json, mission.mission_features_json)` |
| `mission_features_json` | `features_config_patch.py:36` | reader (emitted into `Constants`) | `max_tasks` set-check over `row.features_json` and `mission.mission_features_json` |
| `mission_features_json` | `features_config_patch.py:5` | comment only | docstring |
| `mission_roles_json` | `roles_config_patch.py:52` | writer (emitted into `Constants`) | `mission_roles_json: String(mission.roles_json ?? '')` |
| `mission_roles_json` | `roles_config_patch.py:70` | reader (emitted into run inputs) | `mergeRoles(parse(config.roles_json), parse(config.mission_roles_json))` |
| `mission_roles_json` | `roles_config_patch.py:5, 46` | comment and marker | docstring; `CONSTANTS_MARK` |
| `mission_roles_json` | `roles_options_patch.py:5` | comment only | docstring: the Mission override role |
| `mission_roles_json` | `report_pack_patch.py:40, 42` | anchor and writer (into `Constants`) | anchors on the `mission_roles_json` line of `Constants` and adds `report_pack` after it |
| `retrieval_corpora_json` | `retrieval_config_patch.py:31` | anchor (replaced) | `parseCorpora(row.retrieval_corpora_json)` in `CORPORA_FALLBACK_OLD` |
| `retrieval_corpora_json` | `retrieval_config_patch.py:32` | reader (replacement) | `__retrievalFlat.retrieval_corpora_json` |
| `retrieval_corpora_json` | `retrieval_config_patch.py:96` | reader (emitted into `Constants`) | `retrieval_corpora_json: parseCorpora(row.retrieval_corpora_json)` passed to `flatten()` |
| `retrieval_corpora_json` | `retrieval_config_patch.py:8, 28` | comment and anchor | docstring; `mission.retrieval_corpora` precedence |
| `state_namespace` | `lessons_patch.py:45` | reader | `body: { mission: String(cfg.state_namespace ?? '') }` in the lessons POST; the binding of `cfg` is UNKNOWN (not traced in this read) |
| `state_namespace` | `lessons_patch.py:101` | reader | `mission: String(__lessonsCfg.state_namespace ?? '')`; `__lessonsCfg` is `$('Constants').first().json` (line 87) |
| `state_namespace` | `test_engine_config_patch.py:30`, `test_features_config_patch.py:31` | test fixtures | mission dicts used by the patch tests |
| `mission_features_json` | `test_features_config_patch.py:184-188` | test | sets `mission_features_json` for the `max_tasks` test |

`overrides_json`: no hit in `data-tables/ops/*_patch.py` or in the export.

## UNKNOWN

- Whether the live n8n export at launch equals `scratchpad/live_workflow_export.json` (its date is before several patch edits).
- Whether `overrides_json` exists in the live `missions` table (not read: the Data Table schema was out of scope).
- `lessons_patch.py:45`: which variable `cfg` is bound to (not traced).
- Whether `retrieval_config_patch.py` has been applied to the live workflow (the export still has the legacy line).
- Readers in the runner and board source (`ahawr-runner/`): out of scope for this task.

## Checks run

- Python parse of the export (list top level, 1 workflow, 102 nodes) and a per-node count of the five names.
- `grep -n` over `data-tables/ops/*_patch.py` for `overrides_json`, `retrieval_corpora_json`, `state_namespace`,
  `mission_roles_json`, `mission_features_json`: the hits in the table above match this grep.

## Readers: runner, board, UI, docs

Read only (T001B). Scope: `ahawr-runner/src/ahawr_runner` (not `codex_*.py`), `ahawr-runner/tests`, `docs`.
Command: `grep -rn 'retrieval_corpora_json\|state_namespace\|overrides_json' ahawr-runner/src ahawr-runner/tests docs`.
Line numbers are from that grep and from the reads named in each row.

The names `retrieval_corpora` (no `_json`, the API field of the mission) and `mission.state_namespace` in code are
included where they read the column. The column grep does not match `retrieval_corpora`, so those hits are listed
separately (marked "field").

### Runner (Python), `ahawr-runner/src/ahawr_runner`

| File | Lines | Name | Reader or writer, what it does |
|---|---|---|---|
| `board.py` | 71 | `state_namespace` | `Mission` dataclass field |
| `board.py` | 79 | `retrieval_corpora` (field) | `Mission.retrieval_corpora: list[str] \| None = None` |
| `board.py` | 258, 286 | `state_namespace` | reads Live State by `mission.state_namespace` |
| `board.py` | 797 | `state_namespace` | fixed field: `update` refuses to change `mission_id` and `state_namespace` |
| `board.py` | 205, 338 | `state_namespace` | docstrings only |
| `board_api.py` | 262 | `state_namespace` | returns `mission.state_namespace` (state key helper) |
| `board_api.py` | 714, 718, 800, 1120, 1125 | `state_namespace` | Live State and task views by the state key; delete pops the Live State row (800) |
| `board_api.py` | 1455, 1465, 1468, 1477, 1500 | `state_namespace` | stats and task routes by the state key |
| `board_pg.py` | 409, 411, 420, 437, 439, 448 | `state_namespace`, `retrieval_corpora_json` | mission SELECT (both `SELECT` strings) |
| `board_pg.py` | 428, 456 | `retrieval_corpora_json` | row mapper: `_read_json(...)` into `retrieval_corpora` |
| `board_pg.py` | 683, 685 | `state_namespace`, `retrieval_corpora_json` | mission UPDATE column list (`known_mission_cols`) |
| `board_pg.py` | 825 | `retrieval_corpora_json` | hermes UPDATE column list (`known_hermes_cols`, write only) |
| `board_pg.py` | 1020, 1022, 1027 | `state_namespace`, `retrieval_corpora_json` | mission INSERT column list and value |
| `board_plan.py` | 106 | `state_namespace` | message for a field locked while the mission runs |
| `board_ready.py` | 421, 576, 712, 727 | `state_namespace` | Live State by the mission key; duplicate check (576); create (727) |
| `board_stats.py` | 246, 364, 406, 430 | `state_namespace` | match the mission by `state_key` |
| `dashboard.py` | 435 | `state_namespace` | run ids of a mission by its state key |
| `mcp_board.py` | 183 | `state_namespace` | task views by the state key |
| `qa_fixture.py` | 63, 71 | `state_namespace`, `retrieval_corpora_json` | fixture loader: `row["state_namespace"]`, `json.loads(row.get("retrieval_corpora_json") or "null")` |
| `retrieval_config.py` | 13 | `retrieval_corpora_json` | `DEFAULTS` (today's constant `[]`) |
| `retrieval_config.py` | 22 | `retrieval_corpora_json` | `JSON_KEYS`: `corpora` maps to this column |

### Board UI (JavaScript and i18n), `ahawr-runner/src/ahawr_runner`

| File | Lines | Name | Reader or writer, what it does |
|---|---|---|---|
| `board_retrieval.js` | 6 | `retrieval_corpora_json` | `DEFAULTS` (`[]`) of the pure retrieval core |
| `board_retrieval.js` | 15 | `retrieval_corpora_json` | `JSON_KEYS`: `corpora` maps to this name |
| `board_mission.js` | 68 | `state_namespace` | locked-reason map: `form.locked.state_namespace` |
| `board_mission.js` | 69, 94 | `retrieval_corpora` (field) | locked-reason map and `FORM_ROWS` row (`listarea`) |
| `board_i18n.js` | 373 | `state_namespace` | `form.locked.state_namespace` text |
| `board_i18n_en.json` | 34 | `state_namespace` | same key, English |
| `board_i18n_ru.json` | 34 | `state_namespace` | same key, Russian |
| `board_edit_win.js` | 27, 35, 848, 849 | `retrieval_corpora` (field) | launch window: context page keys, `LOCKED` list, summary card label and locked note. No column name in this file |
| `board_mission_save.js` | 108 | `retrieval_corpora` (field) | comment: config list fields |

### Tests, `ahawr-runner/tests`

| File | Lines | Name | Note |
|---|---|---|---|
| `test_board.py` | 69, 70, 818, 1144, 1152, 1167, 1215, 1247, 1267, 1286 | `state_namespace` | `Mission(...)` fixtures |
| `test_board_api.py` | 35, 112, 1096, 1143, 1149, 1193, 1252, 1353 | `state_namespace` | `Mission(...)` fixtures |
| `test_board_api_part2.py` | 27 | `state_namespace` | `Mission(...)` fixture |
| `test_board_features_api.py` | 30 | `state_namespace` | `Mission(...)` fixture |
| `test_board_lessons_api.py` | 314 | `state_namespace` | `Mission(...)` fixture |
| `test_board_providers_api.py` | 107 | `state_namespace` | `Mission(...)` fixture |
| `test_board_roles_api.py` | 29 | `state_namespace` | `Mission(...)` fixture |
| `test_board_ready.py` | 44, 307, 314, 504, 545, 606 | `state_namespace` | `Mission(...)` fixtures |
| `test_board_stats.py` | 60, 412 | `state_namespace` | `Mission(...)` fixtures |
| `test_board_views_api.py` | 531, 707 | `state_namespace` | mission rows (531), orphan mission (707) |
| `test_board_views_api.py` | 539 | `retrieval_corpora_json` | row built with `json.dumps(m["retrieval_corpora"])` |
| `test_qa_fixtures.py` | 48, 104 | `state_namespace` | fixture rows and state keys |
| `test_qa_fixtures.py` | 56 | `retrieval_corpora_json` | `_json_column(row, "retrieval_corpora_json")` |
| `test_dashboard_mission_filter.py` | 14 | `state_namespace` | `Mission(...)` fixture |
| `test_mission_stats.py` | 32 | `state_namespace` | `Mission(...)` fixture |
| `test_mcp_board.py` | 21, 194 | `state_namespace` | `Mission(...)` fixtures |
| `test_task_id_suffix.py` | 69 | `state_namespace` | `Mission(...)` fixture |
| `test_retrieval_config.py` | 18 | `retrieval_corpora_json` | expected defaults |
| `test_propose_retrieval_json.py` | 30, 95, 159 | `retrieval_corpora_json` | migration dry-run fixtures (`ROW`, a legacy string value) |
| `retrieval_cases.json` | 6, 37, 48, 109 | `retrieval_corpora_json` | golden cases (defaults and overrides) |
| `board_mission.test.js` | 100 | `state_namespace` | asserts that `state_namespace` has no form row |
| `fixtures/board_part2/missions.json` | 12, 26, 40, 54 | `state_namespace` | fixture data (`sk_running`, `sk_queued`, `sk_done`, `sk_failed`) |
| `fixtures/board_part2/changelog.json` | 12, 26 | `state_namespace` | fixture data (`sk_running`) |

### Docs (`docs/`)

| File | Lines | Note |
|---|---|---|
| `config-columns-plan.md` | 27, 37-39, 105 | the plan: the seven retrieval columns, `state_namespace` alias, `overrides_json` |
| `config-retrieval.md` | 24, 47, 48, 61, 73, 90, 105-129, 140, 147-160, 252 | earlier inventory of the retrieval columns and the proposed `retrieval_json` keys |
| `naming.md` | 38, 88 | `state_key` / `state_namespace` naming rule |
| `rag-quality.md` | 317 | retrieval trace: `state_namespace` or `mission_id` when the columns are absent |
| `retrieval/INTEGRATION.md` | 73, 80, 106 | the `retrieval_corpora_json` column in `hermes_config` and `missions`; trace fields |
| `retrieval/ARCHITECTURE.md` | 224 | trace fields |
| `telegram-bot.md` | 3, 182 | `state_namespace` value of the bot's mission |
| `roadmap.json` | 459 | roadmap text naming `overrides_json` |
| `roadmap.html` | 98 | same roadmap text, HTML copy |
| `docs/cold-prefill/partB-runner-events/*.jsonl` | 829 hits in total | run event logs: data, not readers. Per-line listing omitted |

### Launch settings window and its save route (for T004A)

- Window: `ahawr-runner/src/ahawr_runner/board_edit_win.js`. Header and pages 1-16; `save()` at 117; summary card at 145;
  `retPlan()` at 586 and `saveRetrieval()` at 592 (retrieval JSON, not the columns); review page `reviewHtml()` at 863;
  discard confirm at 945.
- Form and data path: `ahawr-runner/src/ahawr_runner/board_mission.js` (`FORM_ROWS` at 78-94, `renderMission` at 212);
  `ahawr-runner/src/ahawr_runner/board_mission_save.js` (`readForm` at 72, `currentDiff` at 167, `retrievalSavePlan` at 252-260).
- Diff and confirm: `currentDiff` (`board_mission_save.js:167`) and `diffHtml` (`:184`) build the diff; `onSave`
  (`:331`) shows the diff and calls `window.confirm` at `:348`; `doSave` (`:358`) sends the save only after confirm.
- Version check, client side: `doSave` sends `expected_version` with `POST /api/board/missions/{id}` (`:384-385`,
  value `mission.updated_at`) and with `POST /api/board/missions/{id}/config` (`:388-389`, value `version`). The retrieval
  save sends `expected_version` with `PUT /api/board/retrieval` (`board_mission_save.js:252-260`).
- Version check, server side: route `update_mission` at `ahawr-runner/src/ahawr_runner/board_api.py:763-774`
  (`expected_version` popped at `:769`); config route at `board_api.py:1301`; retrieval route `@router.put("/retrieval")`
  at `board_api.py:1659`.
- Store check: `BoardStore.update_mission` at `board.py:787` calls `_check_version` at `board.py:794`; `_check_version`
  is at `board.py:662-666` and raises `ConflictError` on a mismatch. `PgBoardStore.update_mission` at `board_pg.py:667`
  checks `expected_version != stored` at `board_pg.py:680` and calls `_conflict` at `:681`.

## Proposed keys

Target: `missions.overrides_json` = `{"roles": {...}, "features": {...}, "retrieval": {...}}` (`docs/config-columns-plan.md:37-39`),
and `retrieval_json` for the global retrieval object (`docs/config-retrieval.md:160`). Defaults are today's constants.
Names follow `docs/naming.md`: units in the key, no `_json` suffix inside an object.

| legacy column | table | key path | type | default (today) | source of the default | note |
|---|---|---|---|---|---|---|
| `retrieval_enabled` | `hermes_config` | `retrieval.enabled` | boolean | `false` | `retrieval_config.py:11` | the code also needs `corpora` non-empty for the effective flag (`docs/config-retrieval.md:153`) |
| `retrieval_url` | `hermes_config` | `retrieval.url` | string | `"http://ahawr-retrieval:8500"` | `retrieval_config.py:12` | scheme check: UNKNOWN |
| `retrieval_corpora_json` | `hermes_config` | `retrieval.corpora` | array of strings | `[]` | `retrieval_config.py:13` | global default |
| `retrieval_corpora_json` | `missions` | `overrides_json.retrieval.corpora` | array of strings | `null` (no override) | `board.py:79` | per mission; `retrieval_corpora` wins over the global value (`Constants` L32) |
| `retrieval_timeout_ms` | `hermes_config` | `retrieval.timeout_ms` | integer | `120000` | `retrieval_config.py:14` | minimum `1000` (`retrieval_config.py:28-32`); maximum: UNKNOWN |
| `retrieval_worker_max_tokens` | `hermes_config` | `retrieval.worker_max_tokens` | integer | `6000` | `retrieval_config.py:15` | minimum `100`; maximum: UNKNOWN |
| `retrieval_reviewer_max_tokens` | `hermes_config` | `retrieval.reviewer_max_tokens` | integer | `5000` | `retrieval_config.py:16` | minimum `100`; maximum: UNKNOWN |
| `retrieval_label` | `hermes_config` | `retrieval.label` | string | `""` | `retrieval_config.py:17` | allowed values: UNKNOWN |
| `mission_roles_json` (planned) | `missions` | `overrides_json.roles` | object | `{}` (no override) | code today emits `String(mission.roles_json ?? '')` (`roles_config_patch.py:52`), so the empty string | the column does not exist in `src` (0 hits); the planned name only |
| `mission_features_json` (planned) | `missions` | `overrides_json.features` | object | `{}` (no override) | default used by `parseFeatures` when the column is null: UNKNOWN (not traced) | planned name only |
| `overrides_json` (new) | `missions` | the whole object | JSON object | `{}` | none (new) | column type (JSON or text): UNKNOWN; column not in the export or `src` |
| `state_namespace` | `missions` | alias of `mission_id` | string | equals `mission_id` in the plan (`docs/config-columns-plan.md:39`) | `board.py:71` | alias, no removal in this release: both names are accepted; the stored value is not changed. Equality with `mission_id` is UNKNOWN in live data: test fixtures use different values (`test_board_api.py:35`: `mission_id="m1"`, `state_namespace="sk1"`) |
| `retrieval_json` (new) | `hermes_config` | `retrieval` (the object) | JSON object | `{}` (all defaults above) | `board_retrieval.js:3-11` | the column exists in the UI and in `PUT /retrieval` (`board_api.py:1659`); whether the column exists in the database: UNKNOWN |

Open points (UNKNOWN): whether `overrides_json` exists in the live `missions` table; whether the live `state_namespace`
equals `mission_id` in every row; the type of `overrides_json` and `retrieval_json`; the features default in
`parseFeatures`; the scheme and maximum checks for the numeric keys.

## Checks run (T001B)

- `grep -rn 'retrieval_corpora_json\|state_namespace\|overrides_json' ahawr-runner/src ahawr-runner/tests docs`: 995 hits.
  Hits in `docs/config-overrides.md` itself are excluded from the tables. Hits outside `docs/cold-prefill` are listed above
  by line. `docs/cold-prefill` (829 hits) is run-log data, summarised.
- `grep -l ... | grep codex_`: no `codex_*` file matches the names, so nothing is excluded by the codex rule.
- Launch window functions and lines: read with `sed`/`grep -n` in `board_edit_win.js`, `board_mission_save.js`, `board_api.py`,
  `board.py`, `board_pg.py`.

## Сводка (на русском)

### Что изменено

- `ahawr-runner/src/ahawr_runner/overrides_config.py`: ядро на Python, `DEFAULTS`, `LEGACY_COLUMNS`, `validate`
  (строка 109), `resolve` (122), `flatten` (132). Тесты: `ahawr-runner/tests/test_overrides_config.py`.
- `ahawr-runner/src/ahawr_runner/board_overrides.js`: то же ядро на JavaScript. Общие кейсы:
  `ahawr-runner/tests/overrides_cases.json`, тест `ahawr-runner/tests/board_overrides.test.js`.
- `ahawr-runner/src/ahawr_runner/board_api.py:774-779`: при `POST /api/board/missions/<id>` поле `overrides_json`
  проверяется через `overrides_config.validate` до записи.
- `ahawr-runner/src/ahawr_runner/board_edit_win.js:600-650`: окно запуска читает и сохраняет `overrides_json`
  (страница 3, «Mission overrides (JSON)»), сохранение идёт с `expected_version`.
- `data-tables/ops/propose_overrides_json.py`: dry run, предлагает `overrides_json` из legacy-колонок. Ничего не пишет.
  Тест: `ahawr-runner/tests/test_propose_overrides_json.py`.
- `data-tables/ops/overrides_config_patch.py`: патч `Constants` workflow. Читает `overrides_json` первым, legacy-колонку
  вторым. Не в git. Тест: `ahawr-runner/tests/test_overrides_config_patch.py`.
- `CHANGELOG.md`, раздел «Step 5». Этот документ: `docs/config-overrides.md`.

### Шаги оператора

1. Добавить колонку `overrides_json` в таблицу `missions` в интерфейсе n8n. Не через SQL.
2. Выгрузить строки `missions` с legacy-колонками в файл (копия для оператора) и запустить
   `python data-tables/ops/propose_overrides_json.py <rows.json>` (или `-` для stdin). Если хотя бы одна миссия
   отклонена, на stdout ничего не выводится.
3. Сохранить предложенный JSON каждой миссии через окно запуска: страница 3, поле «Mission overrides (JSON)».
   Окно показывает diff и просит подтверждения перед сохранением.
4. Применить патч обычной процедурой оператора: `python data-tables/ops/overrides_config_patch.py <workflow.json>`.
   Патч идемпотентен, пишет файл, в n8n его не импортируют. До этого шага `Constants` читает legacy-колонку.
5. После того как одна или две миссии отработали с JSON, удалить legacy-колонки `retrieval_*` (см. таблицу
   «Proposed keys») в интерфейсе n8n. Удаление выполняется отдельным шагом и после него откат идёт через запись
   CHANGELOG.

### Что не проверено живьём

Ни один из пунктов ниже в этой задаче не запускался против живой базы, живого workflow или живой доски:

- `propose_overrides_json.py` на реальных строках `missions`;
- `overrides_config_patch.py` на живом экспорте (дата экспорта раньше нескольких правок патчей, см. «Sources»);
- сохранение через окно запуска в браузере и `POST /api/board/missions/<id>` на живой доске;
- наличие колонки `overrides_json` в живой таблице `missions` и её тип (UNKNOWN, см. «Open points»);
- откат, описанный ниже.

### Откат

Очистить колонку `overrides_json` у миссий. Constants вернётся к legacy-колонкам, потому что пустой JSON даёт
сегодняшние константы (см. docstring `overrides_config_patch.py`). Откат работает только до шага 5. После удаления
legacy-колонок нужно восстановить колонку и значения из записи CHANGELOG.

### Конфликт области

В тексте задачи RULES 1 и ACCEPTANCE CRITERIA описывают миссию lessons, а OBJECTIVE описывает `overrides_json`.
Работа идёт по OBJECTIVE (см. «Scope note» выше). Задача также говорит, что оператор уже добавил колонку
`overrides_json`. В этом документе её наличие в живой таблице не проверено (UNKNOWN), поэтому шаг 1 оператора
остаётся обязательной проверкой.
