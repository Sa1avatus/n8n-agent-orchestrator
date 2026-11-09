# Config readers: retrieval columns

Read-only inventory for the `retrieval_*` columns of the `hermes_config` Data Table (profile `claude-code`), with the
one `missions` column that carries retrieval corpora. Input for the plan in `docs/config-columns-plan.md` (sections 3-5).

## Sources

- Workflow export used: `/d/n8n/scratchpad/live_workflow_export.json` (workflow "AHAWR v13 — Claude Code", 102 nodes),
  modification time `2026-10-31 15:14:13 UTC`. It is the only fresh export of the live workflow found in the workspace.
- `AHAWR_Workflow.json` was NOT used (per the task rules). It is not the running workflow.
- Ops patches: `data-tables/ops/*_patch.py` (read only).
- Not used: `data-tables/ops/wf_before_ahawr-json-config-retrieval.json` (modified `2026-10-31 18:00:12 UTC`) is a
  pre-patch backup, not an export of the live workflow.

## Readers: workflow and patches

Column to constant mapping is read from the `Constants` Code node (`jsCode`, `Load Config` row = `hermes_config`
row for `claude-code`). Defaults are the literal fallbacks in that code. Lines are from the export's `jsCode`.

| reader (node or file:line) | column | constant (as used) | current default |
|---|---|---|---|
| `Constants` L37 | `retrieval_enabled` | `retrieval_enabled` (out, L50: `retrievalRequested && retrievalCorpora.length > 0`) | `false` (not set) |
| `Constants` L36 | `retrieval_url` | `retrieval_url` (L51, trailing `/` stripped) | `http://ahawr-retrieval:8500` |
| `Constants` L31-32 and L29 | `retrieval_corpora_json` | `retrievalCorpora` (L32: `mission.retrieval_corpora` takes precedence, else this column) | `[]` (empty) |
| `Constants` L54 | `retrieval_worker_max_tokens` | `retrieval_worker_max_tokens` (`optNum`, min 100) | `6000` |
| `Constants` L55 | `retrieval_reviewer_max_tokens` | `retrieval_reviewer_max_tokens` (`optNum`, min 100) | `5000` |
| `Constants` L53 | `retrieval_timeout_ms` | `retrieval_timeout_ms` (`optNum`, min 1000) | `120000` ms |
| `Constants` L56 | `retrieval_label` | `retrieval_label` (trimmed string) | `""` (empty; trace label becomes `null`) |
| `retrieval_json` | `retrieval_json` | none | no reader found (column not in the workflow yet; planned target only) |

Downstream readers of the `Constants` outputs (same export, all via `$('Constants').first().json.<name>`):

| reader (node) | column (through constant) | use |
|---|---|---|
| `Worker Retrieval Enabled?`, `Reviewer Retrieval Enabled?` (if) | `retrieval_enabled` | gate |
| `Attach Worker Context`, `Attach Reviewer Context` (code, L4) | `retrieval_enabled` | gate |
| `Retrieve Worker Context`, `Retrieve Reviewer Context` (http, url) | `retrieval_url` | URL + `/retrieve` |
| `Retrieve Worker Context`, `Retrieve Reviewer Context` (http, timeout) | `retrieval_timeout_ms` | timeout |
| `Build Worker Retrieval Request` (code, L23) | `retrieval_worker_max_tokens` | `budget.max_tokens` |
| `Build Reviewer Retrieval Request` (code, L23) | `retrieval_reviewer_max_tokens` | `budget.max_tokens` |
| `Build Worker Retrieval Request` (code, L24), `Build Reviewer Retrieval Request` (code, L24) | `retrieval_label` | `trace.label` |

Missions table (not `hermes_config`):

| reader (node or file:line) | column | constant | current default |
|---|---|---|---|
| `Mission` (code, L77) | `retrieval_corpora_json` (of the `missions` row, `row` = `$json`) | `parsedCorpora` -> `retrievalCorpora` (L78, falls back to the workspace corpus; L111 `retrieval_corpora`) | `[]`, then the workspace corpus |
| `Mission` (code, L51) | `retrieval_corpora_json` | error text only | - |

Ops patches (`data-tables/ops/*_patch.py`): no reader found for any of the seven `hermes_config` columns.
The only `retrieval_` hits are `retrieval_worker_context` and `retrieval_reviewer_context`
(`lessons_patch.py:28`, `stable_prefix_patch.py:149,155`), which are context fields, not these columns.
`lessons_patch.py:5` mentions `retrieval_enabled` only in a comment, as the model for `lessons_enabled`.

## Coverage of the seven columns

| column | workflow readers | ops-patch readers |
|---|---|---|
| `retrieval_enabled` | `Constants` L37, L50; 2 if nodes; 2 attach nodes | none |
| `retrieval_url` | `Constants` L36, L51; 2 http nodes | none |
| `retrieval_corpora_json` | `Constants` L29, L32; `Mission` L77 (missions row) | none |
| `retrieval_worker_max_tokens` | `Constants` L54; `Build Worker Retrieval Request` L23 | none |
| `retrieval_reviewer_max_tokens` | `Constants` L55; `Build Reviewer Retrieval Request` L23 | none |
| `retrieval_timeout_ms` | `Constants` L53; 2 http nodes | none |
| `retrieval_label` | `Constants` L56; 2 build-request nodes L24 | none |

`retrieval_json` (planned in `docs/config-columns-plan.md` section 2): no reader found.

## Checks

- `grep -c 'retrieval_'` on the export returns 1 (the file is one JSON line). `grep -o 'retrieval_[a-z_]*' | wc -l`
  returns 63 in total; the seven columns account for 29 of them (`retrieval_enabled` 7, `retrieval_url` 4,
  `retrieval_corpora_json` 4, `retrieval_timeout_ms` 4, `retrieval_label` 4, `retrieval_worker_max_tokens` 3,
  `retrieval_reviewer_max_tokens` 3). The other 34 are context, request and status fields
  (`retrieval_worker_context`, `retrieval_reviewer_status`, ...), not columns. Every column occurrence in `jsCode`
  or node parameters is recorded above; `retrieval_corpus_roots` and `retrieval_corpora` in `Mission` are output
  fields, not columns.
- `git diff --check`: exit 0; the CRLF warnings concern other files already modified in the working copy.
- `git status`: `docs/config-retrieval.md` is the only file this task created.
- Open unknowns: the export is assumed to be the operator-staged copy; its node list was not compared with a live n8n
  instance. Whether `Load Config` is exactly the `hermes_config` table is inferred from the `profile_id` filter, not
  confirmed from the node's table id.

## Runner, board, launch window and docs readers (T001B)

Read only. Paths are under `ahawr-runner/src/ahawr_runner/` unless shown. `codex_*.py` is excluded.

Result of the verification command `grep -rn 'retrieval_' ahawr-runner/src/ahawr_runner --include=*.py --include=*.js --include=*.html | wc -l`: **33**. The table below has 29 rows that together cover the 33 lines of that output (rows 17 and 19 span several lines). The four `.json` i18n hits (`board_i18n_en.json:35,37`, `board_i18n_ru.json:35,37`) are outside that command and are listed separately.

Only one runner reader touches the seven hermes columns: the write allow-list `known_hermes_cols` at `board_pg.py:816-829` (the retrieval names are at 824-827). Every other hit concerns the mission field `retrieval_corpora` (the `missions` column `retrieval_corpora_json`), not the seven columns. No board endpoint writes them: the callers of `update_hermes_config` pass only `roles_json` (`board_api.py:1579`), `features_json` (`:1624`) and `thinking_json` (`:1642`). The launch settings window (`board_edit_win.js`) has no control for any of the seven columns.

| # | file | line | reader | what it reads or writes |
|---|---|---|---|---|
| 1 | `board.py` | 79 | `Mission` dataclass field | `retrieval_corpora: list[str] \| None` (mission) |
| 2 | `board_edit_win.js` | 27 | launch window, section `ctx` keys | `retrieval_corpora` shown in the context section (mission) |
| 3 | `board_edit_win.js` | 35 | `LOCKED` list | `retrieval_corpora` greyed while running (mission) |
| 4 | `board_edit_win.js` | 659 | summary card section | `labelOf(box, "retrieval_corpora")` (mission) |
| 5 | `board_edit_win.js` | 660 | summary card section | `lockedNote("retrieval_corpora")` (mission) |
| 6 | `board_i18n.js` | 351 | `form.locked.retrieval_corpora` | locked-field text (mission) |
| 7 | `board_i18n.js` | 368 | `form.field.retrieval_corpora` | label text (mission) |
| 8 | `board_mission.js` | 18 | doc comment | lists `retrieval_corpora` in the mission shape |
| 9 | `board_mission.js` | 69 | locked-reason map | `retrieval_corpora` -> `form.locked.retrieval_corpora` (mission) |
| 10 | `board_mission.js` | 94 | `FORM_ROWS` config row | `retrieval_corpora` listarea (mission) |
| 11 | `board_mission_save.js` | 108 | comment | config list fields include `retrieval_corpora` (mission) |
| 12 | `board_pg.py` | 411 | mission SELECT | `retrieval_corpora_json` (mission) |
| 13 | `board_pg.py` | 428 | mission row mapper | `_read_json(r["retrieval_corpora_json"])` (mission) |
| 14 | `board_pg.py` | 439 | mission SELECT | `retrieval_corpora_json` (mission) |
| 15 | `board_pg.py` | 456 | mission row mapper | `_read_json(row["retrieval_corpora_json"])` (mission) |
| 16 | `board_pg.py` | 685 | mission UPDATE allow-list `known_mission_cols` | `retrieval_corpora_json` (mission) |
| 17 | `board_pg.py` | 824-827 | hermes UPDATE allow-list `known_hermes_cols` (`update_hermes_config`) | the seven hermes columns (write only, no reader of the value here) |
| 18 | `board_pg.py` | 1021 | mission INSERT | `retrieval_corpora_json` (mission) |
| 19 | `board_pg.py` | 1034-1035 | mission INSERT value | `json.dumps(mission.retrieval_corpora)` (mission) |
| 20 | `board_plan.py` | 110 | `MIDRUN_CONFIG_FIELDS` message | `retrieval_corpora` locked mid-run (mission) |
| 21 | `board_ready.py` | 53 | field list | `retrieval_corpora` (mission) |
| 22 | `board_ready.py` | 599 | mission builder | `list(source.get("retrieval_corpora") or [])` (mission) |
| 23 | `board_ready.py` | 620 | mission builder | `list(source.retrieval_corpora or [])` (mission) |
| 24 | `board_ready.py` | 653 | key tuple | `retrieval_corpora` (mission) |
| 25 | `board_ready.py` | 664 | mission builder | `list(source.retrieval_corpora or [])` (mission) |
| 26 | `board_ready.py` | 669 | key tuple | `retrieval_corpora` (mission) |
| 27 | `board_ready.py` | 735 | mission builder | `retrieval_corpora=rows["retrieval_corpora"] or None` (mission) |
| 28 | `board_ready.py` | 773 | mission builder | `list(mission.retrieval_corpora or [])` (mission) |
| 29 | `qa_fixture.py` | 71 | fixture loader | `json.loads(row.get("retrieval_corpora_json") or "null")` (mission) |

Hits in `board_i18n_en.json:35,37` and `board_i18n_ru.json:35,37`: `form.locked.retrieval_corpora` and `form.field.retrieval_corpora` (the same keys as rows 6-7, English and Russian).

Generic hermes readers that do not name a column (so they read the seven columns only implicitly): `board.py:221` and `:729` (`hermes_config()`), `board_pg.py:529` (`hermes_config()`), `board_ops_api.py:266` (snapshot section). None of them has a `retrieval_` name.

Tests (`ahawr-runner/tests`): `test_board_views_api.py:539` and `test_qa_fixtures.py:56` cover only the mission field `retrieval_corpora_json`. No test names one of the seven hermes columns, and no test reads `retrieval_json`.

Docs that describe the columns (not readers, but a change must reach them):

| file | lines | note |
|---|---|---|
| `ahawr-runner/docs/board-data-model.md` | 25, 38, 458-464, 488-491 | column table and an example. Example at 490 gives `retrieval_worker_max_tokens=5000`, `retrieval_reviewer_max_tokens=60000`; the workflow defaults are 6000 and 5000 (rows 25-26 above). The example is inconsistent with the default. |
| `ahawr-runner/docs/board-ops-notes.md` | 431-433 | list of the retrieval fields in a note |
| `README.md` | 713, 716, 717 (EN); 1552, 1555, 1556 (RU) | operator steps: set `hermes_config.retrieval_enabled = true`, give the mission a `working_directory` |
| `ahawr-runner/README.md` | 184 | corpus from `working_directory`; mentions `missions.retrieval_...` |
| `CHANGELOG.md` | 665 | `hermes_config.retrieval_enabled` |
| `docs/config-columns-plan.md` | 27, 32, 37, 39, 66, 103 | the plan (section 2 and the target shape) |
| `docs/retrieval/INTEGRATION.md` | 72-80, 95, 192-195 | table of the seven columns with defaults (`retrieval_timeout_ms` 120000, `retrieval_worker_max_tokens` 6000, `retrieval_reviewer_max_tokens` 5000, `retrieval_label` `hybrid-v1`). Line 73 says `retrieval_corpora_json` takes "comma-separated corpus ids, or a JSON array"; see the UNKNOWN below. |
| `docs/retrieval/EVALUATION.md` | 85, 92, 95 | A/B settings of the seven columns |
| `docs/retrieval/ROADMAP.md` | 43 | `retrieval_label` values for rollout |
| `docs/roadmap.json` | 459 | planned `retrieval_json` (generated roadmap, not a reader) |

Not counted as readers: `docs/cold-prefill/partB-runner-events/*.jsonl` (event logs that happen to contain these strings).

## Proposed keys

Target shape from `docs/config-columns-plan.md:66-68`: `{enabled, url, corpora, timeout_ms, worker_max_tokens, reviewer_max_tokens, label}`. Naming follows `docs/naming.md`: units in the key name (`timeout_ms`, `*_max_tokens`), no `_json` suffix inside the object. Defaults are today's values from the `Constants` code (rows 22-28 above). Validation is what the workflow code does today, where it is known.

| column | proposed key | type | default (today) | validation range | note |
|---|---|---|---|---|---|
| `retrieval_enabled` | `enabled` | boolean | `false` | must be `true` or `false`; anything else: UNKNOWN (code uses truthiness) | the code also requires `corpora` non-empty for the effective flag (`Constants` L50) |
| `retrieval_url` | `url` | string | `http://ahawr-retrieval:8500` | non-empty; `http` or `https` scheme; trailing `/` stripped (`Constants` L51) | scheme check: UNKNOWN (not in the code read) |
| `retrieval_corpora_json` | `corpora` | array of strings (was a JSON string) | `[]` | JSON array of strings; each item non-empty: UNKNOWN; comma-separated form (INTEGRATION.md:73): UNKNOWN whether still accepted | the mission field `retrieval_corpora` overrides it (`Constants` L32); the per-mission override moves into `missions` overrides (config-columns-plan.md:39), not here |
| `retrieval_timeout_ms` | `timeout_ms` | integer | `120000` | integer, min `1000` (`optNum`, Constants L53); max: UNKNOWN | unit ms |
| `retrieval_worker_max_tokens` | `worker_max_tokens` | integer | `6000` | integer, min `100` (`optNum`, Constants L54); max: UNKNOWN | the value is a token budget (`budget.max_tokens`) |
| `retrieval_reviewer_max_tokens` | `reviewer_max_tokens` | integer | `5000` | integer, min `100` (`optNum`, Constants L55); max: UNKNOWN | same as above, for the Reviewer |
| `retrieval_label` | `label` | string | `""` (trace label `null`) | trimmed string; allowed values: UNKNOWN (docs use `hybrid-v1`, `off`) | written to the retrieval trace (`trace.label`) |
| `retrieval_json` (new) | the object itself | object | `{}`: all defaults above (empty means "the defaults", config-columns-plan.md:55) | the seven keys, each validated as above; unknown keys: UNKNOWN (plan does not say) | type of `retrieval_json` column: UNKNOWN (plan says JSON; the column does not exist yet) |

Notes for the target:
- Live column types: UNKNOWN. `ahawr-runner/docs/board-data-model.md:458-464` gives `boolean`, `text` and `double precision`; that was not checked against the live Data Table.
- `corpora` changes type from a JSON string to an array; a reader of the old string must be changed in the same step (rows 12-29 are the mission field, a separate column, not this change).

## Checks (T001B)

- `grep -rn 'retrieval_' ahawr-runner/src/ahawr_runner --include=*.py --include=*.js --include=*.html | wc -l`: `33`; the reader table has 29 rows covering 33 lines (row 17 spans 824-827, row 19 spans 1034-1035). The four `.json` i18n hits are listed separately.
- No source file changed (read only).
- `git diff --check`: see the result in the final report.

## Checks (T007)

Scope conflict (operator to confirm): RULES 1 and ACCEPTANCE CRITERIA 1-6 of the mission text refer to a
'lessons' mission (lessons library, `board_lessons*.py`), not to the retrieval-config work. They were NOT applied.
This check used the T007 objective and its own criteria only. The retrieval files checked below are the scope.

Environment: run from `ahawr-runner/` with Python 3.12.15, ruff, mypy 1.20.2, node.

- `python -m pytest tests/test_retrieval_config.py tests/test_propose_retrieval_json.py -q --no-cov`: ran, exit 0.
  Key line: `...............................  [100%]` (31 tests). The tests import `ahawr_runner.retrieval_config` and
  load `data-tables/ops/propose_retrieval_json.py` in-process; no runner restart, no provider request, no DB write.
- `node --test tests/board_retrieval.test.js`: ran, exit 0. Key lines: `# tests 23`, `# pass 23`, `# fail 0`.
- `python -m ruff check` on `src/ahawr_runner/retrieval_config.py`, `tests/test_retrieval_config.py`,
  `tests/test_propose_retrieval_json.py`: ran, exit 0. Key line: `All checks passed!`
- `python -m ruff check` on `data-tables/ops/propose_retrieval_json.py` and `data-tables/ops/retrieval_config_patch.py`:
  ran, exit 1. The one finding is `data-tables/ops/retrieval_config_patch.py:42:101: E501 Line too long (109 > 100)`.
  Not fixed: this task changes no code. The ops folder is git-ignored (`.gitignore:41`), so it is outside git checks.
- `python -m mypy src/ahawr_runner/retrieval_config.py tests/test_retrieval_config.py tests/test_propose_retrieval_json.py`
  without a version flag: exit 2, a parse error in a site-packages `numpy` stub (`Type statement is only supported in
  Python 3.12 and greater`). It is a mypy environment issue, not a finding in the changed files.
  With `--python-version 3.12`: ran, exit 0. Key line: `Success: no issues found in 3 source files`.
- `git diff --check` (tracked files, repo-wide): exit 0; only CRLF `warning:` lines for files already modified before
  this mission.
- New untracked files (`git diff --no-index --check /dev/null <file>`) for `retrieval_config.py`, `board_retrieval.js`,
  `test_retrieval_config.py`, `test_propose_retrieval_json.py`, `board_retrieval.test.js`, `retrieval_cases.json` and
  `docs/config-retrieval.md`: 0 whitespace issues each.

Not run: the ops script itself was not executed on a real `hermes_config` row (only through the tests). No `ruff`
or `mypy` on `board_retrieval.js` (JavaScript; node test only). The wider test suite was not run (narrow checks only).

Side effects of this task: none. No restart, no import of the live workflow, no provider request, no DB write.
Only this file was edited.

Open risks: the E501 in `retrieval_config_patch.py:42` stays until the ops script is fixed by its owner. An earlier
review note said the step adding the `retrieval_json` column must come before the save step (lines 201-217). Checked:
step 2 is the column, step 3 is the save, so no change was needed.

## Сводка: `retrieval_json` (на русском)

Семь колонок `retrieval_*` в `hermes_config` теперь можно задать одним JSON-объектом `retrieval_json`. Старые колонки
остаются запасным вариантом: ключ из `retrieval_json` побеждает, отсутствующий ключ берётся из колонки, а если и её нет,
то из значения по умолчанию. Пустой `retrieval_json` означает поведение без изменений.

### Что изменено

- `ahawr-runner/src/ahawr_runner/retrieval_config.py` (новый файл): `parse`, `validate`, `flatten`; таблица
  `JSON_KEYS` связывает семь ключей с колонками (`enabled`, `url`, `corpora`, `timeout_ms`, `worker_max_tokens`,
  `reviewer_max_tokens`, `label`).
- `ahawr-runner/src/ahawr_runner/board_retrieval.js` (новый файл): та же проверка в браузере. Его ядро между маркерами
  `// === pure retrieval core` встраивается в узел `Constants` патчем.
- `ahawr-runner/src/ahawr_runner/board_api.py`: `GET /api/board/retrieval` и `PUT /api/board/retrieval`. PUT требует
  заголовок `X-AHAWR-Dashboard`, проверяет текст до сохранения и принимает `expected_version`.
- `ahawr-runner/src/ahawr_runner/board_pg.py:830`: `retrieval_json` добавлен в список колонок `hermes_config`.
- `ahawr-runner/src/ahawr_runner/board_mission_save.js:252-259`: `retrievalSavePlan` (diff и запрос; без изменений запроса нет).
- `ahawr-runner/src/ahawr_runner/board_edit_win.js`: поле `retrieval_json` в окне запуска (загрузка около строк 562-564,
  сохранение около строки 577, секция около строки 686).
- `ahawr-runner/src/ahawr_runner/board_i18n_en.json`, `board_i18n_ru.json`, `board_i18n.js`: тексты ключей `retrieval`.
- `data-tables/ops/propose_retrieval_json.py` (новый файл, каталог не в git): только чтение, печатает предложенный JSON.
- `data-tables/ops/retrieval_config_patch.py` (новый файл, каталог не в git): патч узла `Constants`.
- Тесты: `ahawr-runner/tests/test_retrieval_config.py`, `test_propose_retrieval_json.py`, `board_retrieval.test.js`,
  `retrieval_cases.json`; в `board_edit_win_features.test.js` разделы 8-10; в `test_board_api_part2.py` проверки маршрута.
- `docs/config-retrieval.md` (этот раздел), `CHANGELOG.md` (запись предложенной версии 0.12.0).

`ahawr-runner/pyproject.toml` и `ahawr-runner/src/ahawr_runner/api.py` не менялись. Ничего не импортировано в n8n и
ничего не записано в базу.

### Что делает оператор

Команды запускает оператор; ни одна из них не применена автоматически.

1. Получить предложенный JSON из строки `hermes_config` (профиль `claude-code`), только чтение:
   `python data-tables/ops/propose_retrieval_json.py <row.json>` (или `<row.csv>`). При ошибке выход с кодом 1 и причина
   в stderr. Userinfo из `retrieval_url` удаляется до печати.
2. Добавить колонку `retrieval_json` в таблицу `hermes_config` в интерфейсе n8n (не SQL). Тип колонки в живой таблице
   не проверен.
3. Сохранить предложенный JSON в поле `retrieval_json` окна запуска. Сохранение показывает diff и проверяет
   `expected_version`. Пока колонка не существует, сохранение не проходит.
4. На копии свежего экспорта n8n запустить
   `python data-tables/ops/retrieval_config_patch.py <копия_экспорта.json>`. Скрипт перезаписывает переданный файл и
   идемпотентен: вывод `patched Constants` или `already patched`. Импортирует патченный файл оператор сам.
5. Позже, после проверки в работе, удалить колонки `retrieval_enabled`, `retrieval_url`, `retrieval_corpora_json`,
   `retrieval_timeout_ms`, `retrieval_worker_max_tokens`, `retrieval_reviewer_max_tokens`, `retrieval_label` в
   интерфейсе n8n. Колонка `retrieval_corpora` в таблице `missions` этим шагом не затрагивается.

### Что не проверено вживую

- Патч не запускался на реальном экспорте n8n и не импортирован. Для `retrieval_config_patch.py` нет теста; проверено
  только `python -m py_compile`.
- Окно запуска в браузере не открывалось. `PUT /api/board/retrieval` не вызывался против живой доски.
- Узел `Constants` не выполнялся в n8n: приоритет ключа над колонкой и значения по умолчанию проверены тестами ядра
  (`flatten`), а не запуском workflow.
- Тип колонки `retrieval_json` в живой таблице не проверен.
- `board_edit_win_features.test.js` в этой сессии не запускался.

Что запущено и с каким результатом: `python -m pytest tests/test_retrieval_config.py tests/test_propose_retrieval_json.py
--no-cov` дал `31 passed in 1.67s`; `node --test tests/board_retrieval.test.js` дал `# pass 23`, `# fail 0`;
`git diff --check` для `docs/config-retrieval.md` и `CHANGELOG.md` вернул код 0.

### Откат

Очистить `retrieval_json` в окне запуска (сохранить пустой текст; проверка версии остаётся). `flatten` тогда берёт
значения из старых колонок, как до изменения. Пока колонки не удалены (шаг 5), это полный откат. После шага 5 старые
значения нужно восстанавливать из резервной копии таблицы.
