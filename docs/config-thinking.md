# Thinking settings: inventory of readers (T001, read only)

Status: inventory only. No code, patch or test was changed. Step 2 of `docs/config-columns-plan.md` (sections 3-5).

## 0. Sources

- Live workflow: `scratchpad/live_workflow_export.json`, workflow `7oMrsFqTiYMy0JJN` ("AHAWR v13 — Claude Code"),
  `updatedAt` 2026-10-23. Node names and line numbers below are from this file (the `jsCode` line numbers count from 1).
- Run Manager: `scratchpad/live_run_manager_export.json` ("Claude Code Run Manager v1", 34 nodes). It has NO `worker_think_*`
  and NO `thinking_json` string.
- `AHAWR_v13_ClaudeCode.json` was not used as a source.
- Repository text read: `docs/config-columns-plan.md` (sections 2-5), `docs/naming.md`, `claude-runner/src/claude_runner/board_features.js`,
  `board_roles.js` (header), `data-tables/ops/features_config_patch.py`, `data-tables/ops/roles_config_patch.py`,
  `data-tables/ops/_make_json_config_thinking_mission.py`.
- Column names are the four legacy columns of `hermes_config` (profile `claude-code`). Their stored values are in the live
  data table, which is not in the export: **the current stored values are UNKNOWN**.
- `thinking_json` is not read anywhere yet: 0 hits in the export, in `claude-runner/src` and in `data-tables/ops` (except the
  mission generator that plans it).

## 1. Readers table

Legacy column = the `hermes_config` column. "Value" = what the reader does with it.

### 1a. n8n workflow (fresh export)

| Reader | Location | Column | What it does with the value |
|---|---|---|---|
| node `Constants` | `jsCode` line 81 | `worker_think_policy` | `String(...).trim().toLowerCase()`; empty string when the column is empty |
| node `Constants` | `jsCode` line 82 | `worker_think_policy_from_task` | `Number(...) \|\| 0`; empty becomes 0 |
| node `Constants` | `jsCode` line 83 | `worker_think_off_after_seconds` | `null`/`undefined`/`''` become -1, else `Number(...)` |
| node `Constants` | `jsCode` line 84 | `worker_think_off_after_steps` | the same as line 83 |
| node `Build Worker Run Input` | `jsCode` line 556 | `worker_think_policy` (via `Constants`) | `think_policy: String($('Constants').first().json.worker_think_policy ?? '')` |
| node `Build Worker Run Input` | `jsCode` line 557 | `worker_think_policy_from_task` (via `Constants`) | `think_policy_from_task: Number(...) \|\| 0` |
| node `Build Worker Run Input` | `jsCode` line 558 | `worker_think_off_after_seconds` (via `Constants`) | `think_off_after_seconds`: the number, or -1 when not finite |
| node `Build Worker Run Input` | `jsCode` line 559 | `worker_think_off_after_steps` (via `Constants`) | `think_off_after_steps`: the number, or -1 when not finite |

Run Manager export: no reader (0 hits). Whether the Run Manager is patched in the live system is UNKNOWN (`launch_mission.sh:53`
runs a think patch on `rm_live.json`, and `:61` allows it to be skipped).

### 1b. Operator scripts (`data-tables/ops`, not in git)

| File:line | Column | What it does with the value |
|---|---|---|
| `think_config_patch.py:6, 39-41, 47-49` | all four | the patch that writes the Constants and Build Worker Run Input lines above |
| `think_config_patch.py:83-98` | `worker_think_off_after_steps` | second stage: adds the steps line and the `think_off_after_steps` run field |
| `think_config_patch.py:124` | `worker_think_policy` | anchor check (`"worker_think_policy" not in jsCode`) |
| `roles_config_patch.py:48-50` | `worker_think_off_after_steps` | its `CONSTANTS_OLD` anchor. **Now 0 matches** in the fresh export (see section 6) |
| `adaptive_split_patch.py:260-266` | `worker_think_off_after_steps` | writes its own Constants and Build lines that read the steps column |
| `set_think_policy.py:1, 22, 35` | `worker_think_policy` | writer: reads the current value and updates it in `hermes_config` row 5 |
| `think_bandit.py:7, 50, 61, 63, 64` | `worker_think_policy` | reads, then `--apply` writes the chosen policy (row 5) |
| `think_uplift.py:1, 26` | `worker_think_policy` | reads the variants for a report (text only) |
| `think_report.py:219` | `worker_think_policy` | report text only |
| `launch_mission.sh:42, 53, 61` | all (indirect) | runs `think_config_patch.py` on the workflow and on the Run Manager |
| `_deploy_*.sh`, `_make_*_mission.py`, `_make_board_mission.py` | text | deploy and mission generators: text only, no node reader |
| `_make_json_config_thinking_mission.py:13-32` | `thinking_json` (planned) | mission text: the plan in section 5 |

### 1c. Runner (`claude-runner/src/claude_runner/`)

| File:line | Column / field | What it does with the value |
|---|---|---|
| `api.py:163-166` | run body `think_policy`, `think_policy_from_task`, `think_off_after_seconds`, `think_off_after_steps` | request model defaults: `""`, `0`, `-1`, `-1` |
| `api.py:259-271` | run fields | `policy = settings.think_policy == "off_first"`; passes the time and step limits to the think switch |
| `api.py:396-399` | run fields | maps the body fields into the run |
| `runs.py:180-183` | run fields | dataclass defaults `""`, `0`, `-1`, `-1` |
| `config.py:519-520` | `Settings.think_off_after_seconds/steps` | dataclass defaults `0`, `0` |
| `config.py:547-548` | `Settings.think_policy`, `think_policy_from_task` | defaults `""`, `1` |
| `config.py:644-645` | env `CLAUDE_RUNNER_THINK_OFF_AFTER_SECONDS/STEPS` | env defaults `0`, `0` |
| `config.py:659-660` | env `CLAUDE_RUNNER_THINK_POLICY`, `CLAUDE_RUNNER_THINK_POLICY_FROM_TASK` | env defaults `""`, `1` (minimum 1) |
| `think_policy.py:2` | docstring | names `hermes_config.worker_think_policy` as the source |
| `think_switch.py:4, 8` | docstring | says the time limit default is 3600 (conflicts with `config.py:644`, see section 2) |

### 1d. Board (python and js)

| File:line | Column | What it does with the value |
|---|---|---|
| `board_api.py:496` | all four | `_DEFAULT_KEYS`: the keys shown on the board from the `hermes_config` row |
| `board_pg.py:828-829` | all four | the field list of the board's save (`UPDATE` of `hermes_config`) |
| `board_plan.py:94-97` | all four | the mission config fields (the list of accepted config keys) |
| `board_mission.js:22` | `worker_think_policy` | comment: the mission config shape |
| `board_mission.js:59-60` | all four | the field list of the launch form |
| `board_mission.js:92-93` | `worker_think_policy`, `worker_think_policy_from_task` | the form rows (`json` and `number` types) |
| `board_think_ui.js:10, 25, 326, 343` | `worker_think_policy` | the policy editor: reads `config.worker_think_policy` and validates it |
| `board_i18n.js:363-364` | `worker_think_policy`, `..._from_task` | the labels `form.field.*` (the same keys occur in `board_i18n_en.json` and `board_i18n_ru.json`, 2 matches in total) |

### 1e. Launch settings window

`claude-runner/src/claude_runner/board_edit_win.js` is the launch settings editor (its header, line 3: "The launch settings
editor as a window"):

| File:line | Column | What it does with the value |
|---|---|---|
| `board_edit_win.js:26` | `worker_think_policy_from_task`, `worker_think_policy` | the list of fields in one step |
| `board_edit_win.js:592, 596` | `worker_think_policy_from_task`, `worker_think_policy` | moves the values into the window |
| `board_edit_win.js:653` | `worker_think_policy` | validation: a non-empty string is an error (`ew.badjson`) |

Whether the window saves through the version check and diff-and-confirm step is UNKNOWN (not checked in this read).

### 1f. Docs (hits listed for completeness; they are not readers)

- `docs/config-columns-plan.md:26, 32, 63, 102` (plan, the `thinking_json` target)
- `docs/cloud-thinking.md:187-188`
- `docs/thinking-control.md:30, 31, 35, 50, 71`
- `docs/roadmap.json:459`
- `CHANGELOG.md:658, 673, 693, 694`
- `claude-runner/docs/board-data-model.md:408-411, 418`: the documented defaults (see section 2). Outside the verification path (`docs/` only).

## 2. Current default constants per column

| Legacy column | Constants (workflow) | Build Worker Run Input | Runner (`api.py` / `runs.py` / `config.py`) | Documented |
|---|---|---|---|---|
| `worker_think_policy` | `''` (line 81) | `''` (line 556) | `""` (`api.py:163`, `runs.py:180`, `config.py:547`) | `board-data-model.md:408` |
| `worker_think_policy_from_task` | `0` (line 82: `\|\| 0`) | `0` (line 557) | `0` (`api.py:164`, `runs.py:181`); **`1`** (`config.py:548`, env default `config.py:660`) | `1` (`board-data-model.md:409`) |
| `worker_think_off_after_seconds` | `-1` when empty (line 83) | `-1` when not finite (line 558) | `-1` (`api.py:165`, `runs.py:182`); `0` (`config.py:519`, env `config.py:644`) | `1800` (`board-data-model.md:410`); `3600` (`think_switch.py:4`, docstring) |
| `worker_think_off_after_steps` | `-1` when empty (line 84) | `-1` when not finite (line 559) | `-1` (`api.py:166`); `0` (`config.py:520`, env `config.py:645`) | `40` (`board-data-model.md:411`) |

The defaults disagree across the three layers. Which one applies on the live system is UNKNOWN: it depends on the stored row
values, which are not in the export. The code path that runs for a mission's Worker is the workflow (`-1`/`0`), so
`-1` and `0` are the values the code produces when the column is empty today.

## 3. Proposed JSON key per column (one key each)

| Legacy column | Proposed key in `thinking_json` |
|---|---|
| `worker_think_policy` | `policy` (its variants: `policy.variants`) |
| `worker_think_policy_from_task` | `from_task` |
| `worker_think_off_after_seconds` | `off_after_seconds` |
| `worker_think_off_after_steps` | `off_after_steps` |

Target shape (from the mission text and `config-columns-plan.md:64`):

```json
{"policy": {"variants": [...]}, "from_task": 1, "off_after_seconds": 1800, "off_after_steps": 40}
```

The target values (`1`, `1800`, `40`) are the documented defaults, not the code defaults in section 2. Choosing them as the
JSON defaults would change behaviour for a row with empty columns (code `0`/`-1` today). This is an open point (section 6).

## 4. Planned names (following the `board_features` / `board_roles` pattern)

| Item | Planned name | Source / note |
|---|---|---|
| Pure core, JS | `claude-runner/src/claude_runner/board_thinking.js` | like `board_features.js` and `board_roles.js` (the `// === pure ... core ===` markers). The name is proposed, not established. `board_think.py` and `board_think_ui.js` already exist, so `board_thinking.js` is a near-clash: an open point |
| Pure core, Python | `claude-runner/src/claude_runner/thinking_config.py` | like `features_config.py` and `roles_config.py`. Proposed: UNKNOWN whether a Python core is wanted |
| Shared cases file | `claude-runner/tests/thinking_cases.json` | like `features_cases.json` and `roles_cases.json` (both in `claude-runner/tests`) |
| Patch | `data-tables/ops/thinking_config_patch.py` | named in `_make_json_config_thinking_mission.py:21`; ends in `_patch.py` (`docs/naming.md:77`) |
| Migration dry run | `data-tables/ops/propose_thinking_json.py` | named in `_make_json_config_thinking_mission.py:23`; read-only |
| Golden test | the cases file: empty JSON flattens to today's constants | `config-columns-plan.md:81` |

## 5. Planned anchor lines for the patch

Patch rules (`docs/naming.md`, the mission text, `features_config_patch.py:8`): idempotent, every anchor asserted exactly
once, nothing written if an anchor is missing. Anchors below are the exact text of the fresh export, checked with a count:

| Node | Anchor (exact text) | Count in export |
|---|---|---|
| `Constants` | `return [{ json: {` (the start of the return, where the core block is inserted, as `features_config_patch.py:26`) | 1 |
| `Constants` | `  worker_think_policy: String(row.worker_think_policy ?? '').trim().toLowerCase(),` (line 81, the first of the four legacy lines) | 1 |
| `Build Worker Run Input` | `think_policy: String($('Constants').first().json.worker_think_policy ?? ''),` (line 556) | 1 |
| `Build Worker Run Input` | `think_off_after_steps: Number.isFinite(Number($('Constants').first().json.worker_think_off_after_steps ?? NaN))` (line 559) | 1 |

Planned change: lines 81-84 read `thinking_json` first and the legacy column second; the four legacy lines stay (the JSON
wins, the column is the fallback) and the run fields on lines 556-559 keep their names.

## 6. Open points

- UNKNOWN: current stored values of the four columns on the live row (not in the export; a read of the data table is needed).
- UNKNOWN: which default applies when both the JSON and the column are empty (code `0`/`-1` vs documented `1`/`1800`/`40`).
  The target shape is the documented one; the golden test has to decide.
- UNKNOWN: whether the Run Manager is patched live (the export has no reader, `launch_mission.sh:53`).
- Anchor drift: `roles_config_patch.py:48` (`CONSTANTS_OLD`, `Number(row.worker_think_off_after_steps)` + `} }];`) matches 0 times
  in the fresh export. `roles_json` lines (`Constants` lines 85-86) already follow the think lines, so that patch appears applied
  and this anchor is stale for a re-run. Not changed here.
- UNKNOWN: whether `board_edit_win.js` saves through the version check and diff-and-confirm step (not read in full).
- Naming: `board_thinking.js` is close to `board_think.py` / `board_think_ui.js`; a different name may be wanted.
- Not checked in this inventory: `claude-runner/tests/` (8 files mention the columns: e.g. `test_board_api.py`, `board_mission.test.js`),
  `claude-runner/docs/` (`board-data-model.md`, `board-design.md`, `lessons.md`, `cloud-thinking-notes.md`,
  `qa-acceptance.md`, `board-ops-notes.md`), `scratch`/`_scratch` copies under `data-tables/ops`. They are not readers of the
  live system, but a change would need to reach the tests.

## 7. Checks (T006A)

Targeted checks only, on the working tree (Windows host, Git Bash). Not the whole suite.

- `python -m pytest tests/test_thinking_config.py tests/test_board_thinking_api.py tests/test_propose_thinking_json.py tests/test_board_api_part2.py --no-cov` (in `claude-runner/`): `48 passed in 12.51s`, exit 0.
- `python -m pytest tests/test_thinking_config_patch.py tests/test_propose_thinking_json.py --no-cov -v`: `19 passed in 1.75s`, exit 0. `test_thinking_config_patch.py` covers `data-tables/ops/thinking_config_patch.py`: a second apply returns False and leaves the workflow unchanged (`test_first_apply_patches_and_second_apply_changes_nothing`); each of the four anchors becomes exactly one replacement (`test_each_anchor_becomes_exactly_one_replacement`); a missing or doubled anchor raises `SystemExit` and writes nothing (`test_missing_anchor_raises_and_writes_nothing`, `test_duplicate_anchor_raises`). Modelled on `tests/test_roles_config_patch.py`.
- `node --test tests/board_thinking.test.js tests/board_edit_win_features.test.js`: `# tests 25`, `# pass 25`, `# fail 0`.
- `python -m ruff check` on `thinking_config.py`, `api.py`, `test_thinking_config.py`, `test_board_thinking_api.py`, `test_propose_thinking_json.py`: 0 errors. On `board_api.py`, `board_pg.py`, `test_board_api_part2.py`: 28, 26 and 6 errors, the same counts as the `git archive HEAD` copy (old errors, not fixed).
- `python -m ruff check` on `data-tables/ops/thinking_config_patch.py`, `data-tables/ops/propose_thinking_json.py`, `tests/test_thinking_config_patch.py`: `All checks passed!`. These three files are untracked (`git ls-files` lists none), so there is no HEAD baseline. Before the fix they had 9 errors (8 E501 and 1 E402); the E402 is the `sys.path` import, suppressed with `# noqa: E402` as in `data-tables/ops/propose_roles_json.py:19`.
- `MYPYPATH=src python -m mypy --python-version 3.12 data-tables/ops/thinking_config_patch.py data-tables/ops/propose_thinking_json.py tests/test_thinking_config_patch.py`: `Success: no issues found in 3 source files`. Without `--python-version 3.12` (the project's `3.11`) mypy stops on a numpy stub of another venv (`numpy/__init__.pyi:737: error: Type statement is only supported in Python 3.12`).
- `python -m mypy src/claude_runner/thinking_config.py src/claude_runner/board_api.py src/claude_runner/board_pg.py src/claude_runner/api.py`: `Found 70 errors in 11 files (checked 4 source files)`. The HEAD copy gives `Found 70 errors in 11 files`. `thinking_config.py` has 0 errors; `api.py:254` (`unused-ignore`) is also at HEAD.
- `git diff --check -- CHANGELOG.md claude-runner/src/claude_runner/board_api.py claude-runner/src/claude_runner/board_pg.py claude-runner/src/claude_runner/api.py claude-runner/src/claude_runner/board_edit_win.js claude-runner/tests/board_edit_win_features.test.js`: exit 0, no output. Only git's CRLF notices appear on stderr.
- Untracked new files (`thinking_config.py`, `board_thinking.js`, `thinking_config_patch.py`, `propose_thinking_json.py`, `test_thinking_config_patch.py`, `docs/config-thinking.md`): `git diff --no-index --check /dev/null <file>` prints 0 whitespace lines for each. That command exits 1 whenever the files differ, so the exit code is not used.

Not run: the full pytest and node suites; the patched Constants JS was not executed in node (the test checks the anchors and the replacement count, not the JS output).

Open: the planned `policy` key is not accepted (section 6).

## 8. Сводка на русском (T006B)

Код и патч написаны и проверены целевыми тестами (раздел 7). Живая система не менялась: ничего не импортировано в n8n,
ничего не перезапускалось и в `hermes_config` ничего не записано.

### 8.1 Что изменено

- Ядро: `claude-runner/src/claude_runner/thinking_config.py` (`validate`, `flatten`). Ключи `JSON_KEYS` на строках 15-19:
  `from_task`, `off_after_seconds`, `off_after_steps`. Ключ `policy` пока не принимается.
- Runner: `claude-runner/src/claude_runner/api.py:168` (поле `thinking_json` в теле запуска), `:391-401` (`flatten`; при
  ошибке ответ `bad_thinking_json`, 422), `:410-413` (поля запуска).
- Board API: `claude-runner/src/claude_runner/board_api.py:501-506` (`_thinking_fields`), `:1628-1643` (`GET` и `PUT
  /api/board/thinking`; сохранение через `update_hermes_config` с проверкой версии).
- Фронтенд: `claude-runner/src/claude_runner/board_thinking.js` (`BoardThinking.validate`, `thinkingSavePlan`); окно
  настроек запуска `claude-runner/src/claude_runner/board_edit_win.js:59, 521-541, 753` (поле `thinking_json`, загрузка и
  сохранение).
- Патч workflow: `data-tables/ops/thinking_config_patch.py` (четыре якоря, идемпотентен, `main` на строке 102).
- Dry run: `data-tables/ops/propose_thinking_json.py` (читает одну строку `hermes_config` и печатает предлагаемый
  `thinking_json`; ничего не пишет и никуда не отправляет).
- `data-tables/ops/launch_mission.sh:43` уже вызывает `thinking_config_patch.py` для `wf_next.json`, сразу после
  `think_config_patch.py` (строка 42).
- Тесты: `claude-runner/tests/test_thinking_config.py`, `test_board_thinking_api.py`, `test_propose_thinking_json.py`,
  `test_thinking_config_patch.py`, `board_thinking.test.js`, кейсы `thinking_cases.json`.
- `CHANGELOG.md`, раздел Unreleased, строки 21-33.

### 8.2 Шаги оператора

1. Запустить `data-tables/ops/propose_thinking_json.py` на строке `hermes_config` профиля `claude-code`. Скрипт только
   печатает предлагаемый `thinking_json`.
2. Сохранить этот JSON в поле `thinking_json` в окне настроек запуска. Текст проверяется до сохранения; сохранение идет
   через `PUT /api/board/thinking` с проверкой версии.
3. Применить патч к workflow самостоятельно: `python data-tables/ops/thinking_config_patch.py <путь к wf_next.json>`. Скрипт
   пишет файл; импорт в n8n делает оператор.
4. Позже, когда legacy-колонки больше не нужны, удалить четыре колонки `worker_think_*` в интерфейсе n8n (не SQL-командой).

### 8.3 Что не проверено на живой системе

- Патч не применен к живому workflow и не импортирован в n8n. Узел `Constants` не выполнялся в node: тест проверяет якоря и
  число замен, а не вывод JS.
- Текущие значения четырех колонок неизвестны: их нет в экспорте. Какой default действует сейчас, тоже неизвестно (разделы 2 и 6).
- Runner не перезапускался. Запуск с `thinking_json` через живой `POST /v1/runs` не проверялся.
- Окно настроек не проверялось в браузере.
- Полные наборы pytest и node не запускались.
- Неизвестно, применяется ли патч Run Manager в живой системе (раздел 6).

### 8.4 Откат

Очистить `thinking_json` (пустая строка или `{}`). Тогда `flatten` возвращает значения legacy-колонок (golden-кейсы в
`thinking_cases.json`). Менять workflow не нужно: патч читает legacy-колонки как запасной вариант. Откат работает, пока
legacy-колонки не удалены.

### 8.5 Проверки T006A (результаты из раздела 7, в T006B не перезапускались)

- pytest, четыре файла (`thinking_config`, `board_thinking_api`, `propose_thinking_json`, `board_api_part2`): `48 passed in 12.51s`, exit 0.
- pytest, `thinking_config_patch` и `propose_thinking_json`: `19 passed in 1.75s`, exit 0.
- `node --test tests/board_thinking.test.js tests/board_edit_win_features.test.js`: `# pass 25`, `# fail 0`.
- ruff: новые файлы 0 ошибок. `board_api.py` 28, `board_pg.py` 26, `test_board_api_part2.py` 6: столько же, сколько в HEAD
  (старые ошибки, не исправлялись). Файлы патча и dry run: `All checks passed!`.
- mypy по трем файлам патча (с `--python-version 3.12`): `Success: no issues found in 3 source files`. По четырем файлам
  src: `Found 70 errors in 11 files`, как в HEAD; `thinking_config.py` без ошибок.
- `git diff --check` по изменённым файлам: exit 0. Новые файлы: 0 строк с пробельными ошибками.

Не запускались: полный pytest, полный node и выполнение JS-узла `Constants` в node.

### 8.6 Открытые риски

- Ключ `policy` не принимается: он считается неизвестным ключом.
- Default при пустых и JSON, и колонках не решен: код дает 0/-1, документация 1/1800/40.
- `launch_mission.sh:43` запускает патч при запуске миссии. Патч идемпотентен, но оператор должен знать, что он может
  примениться без ручного шага.
- Якорь `roles_config_patch.py:48` устарел (раздел 6).
