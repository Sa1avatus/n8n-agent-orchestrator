# Отчёт миссии ahawr-qa-layers: слои regression, api и ui в QA

Дата отчёта: 2026-10-23. Рабочая папка: `D:\n8n` (в Git Bash: `/d/n8n`). Коммитов, push, stash и изменений в Git не делалось. Файлов, кроме этого отчёта, не создавалось.

## 1. Итог

- Код трёх слоёв (`regression`, `api`, `ui`), объединённый вердикт, nightly-файл с ключом `layers` и CLI-команды `regression`, `baseline`, `api` написаны в репозитории (раздел 3).
- Проверки, которые ran: pytest, node, ruff, mypy и `git diff --check` в тех объёмах, что записаны в логах T001 и T027 (раздел 7). Новых ошибок относительно HEAD нет: ruff 70 = 70, mypy 71 против 72 в HEAD, pytest-падения те же два, что были до изменений.
- **Не запускалось:** Docker, Playwright, сборка образа, интеграционный прогон, сценарии, `baseline`, `nightly`, применение патча workflow. Причины в разделе 8.
- **Критично:** baseline `claude-runner` не снят (NOT RUN). Без него слой `regression` для любого кода вернёт `fail`, потому что в HEAD уже есть падения (раздел 6, риск 1).
- **Расхождение с кодом:** документ `claude-runner/docs/qa-layers.md` говорит, что `ui` читает smoke-обход. В коде обход не вызывается (раздел 10, риск 2).

## 2. Ограничения отчёта

- Список задач T001–T027 с заголовками в репозитории не найден (та же проблема описана в `reports/ahawr-qa-container-report-ru.md`, раздел 2). Поэтому номера сопоставлены только там, где артефакт подтверждён по файлу (раздел 3.3). Остальные номера учтены как диапазон: их результаты лежат в перечисленном в разделе 3 коде, но привязка «номер → файл» не установлена.
- Строки проверок цитируются только из логов T001 (`/tmp/ahawr-qa-layers/before/`) и T027 (`/tmp/ahawr-qa-layers/after/`). Вывод T021 (`t021/*.out`) не цитируется, потому что он не из этих логов.

## 3. Что построено

### 3.1 Файлы и функции (file:line)

| Часть | Где | Что сделано |
| --- | --- | --- |
| Слой regression: сравнение с baseline | `claude-runner/src/claude_runner/qa_regression.py:97-102` (`new_failures`), `:104-128` (`regression_verdict`) | Новая ошибка = ключ, которого нет в baseline инструмента. Исходы: `skipped` (нет профиля), `blocked`, `timeout`, `fail`, `pass` |
| Профили и baseline | `qa_regression.py:155` (`load_profiles`), `:241-243` (`read_baseline`, нет файла -> `{}`), `:255` (`write_baseline`) | Чтение `claude-runner/qa/projects.yaml`, запись baseline атомарно через временный файл |
| Слой api | `claude-runner/src/claude_runner/qa_api.py:58` (`load_routes`), `:105` (`check_routes`), `:154` (`_check_mutation`), `:171` (`_verdict`) | GET-маршруты и мутации (с заголовком `X-AHAWR-Dashboard`) по `claude-runner/qa/api_routes.yaml` |
| Слой ui | `claude-runner/src/claude_runner/qa_worker.py:302` (`_layer_command`) | Ранний `skipped`, если нет изменённых UI-файлов (`early_verdict`, `:159`), затем `python -m claude_runner.qa run`. 17 YAML-сценариев в `claude-runner/qa/scenarios/` |
| Объединение и таймауты | `qa_worker.py:370` (`_layer_verdict`), `:378` (`_bounded`), `:387` (`_run_layer`), `:401` (`run_layers`), `:436` (`_serve`, ветка `layers`) | Вердикты слоёв идут по порядку выполнения, каждый с полем `layer`. Ошибка слоя не останавливает следующие слои |
| Константы таймаутов | `qa_worker.py:48-57` (`RUN_TIMEOUT_SECONDS`, `REGRESSION_TIMEOUT_MINUTES = 25`, `LAYER_TIMEOUT_SECONDS`) | См. риск 3 в разделе 10 |
| Конфигурация | `claude-runner/src/claude_runner/roles_config.py:17` (`QA_OPTION_KEYS`), `:21` (`QA_LAYERS`), `:22` (`QA_DEFAULT_LAYERS = ("ui",)`), `:169` (`_check_layers`), `:187` (`qa_layers`) | Опция `layers` в `roles_json`, по умолчанию `["ui"]` |
| Валидация в UI | `claude-runner/src/claude_runner/board_roles.js:11-13`, `:260-262`, `:280-281` | Те же имена слоёв, что в Python |
| Запрос на проверку | `claude-runner/src/claude_runner/qa_requests.py:118` (проверка имени слоя), `:198` (поле `layers` не пишется при `None`) | Старый формат запроса сохраняется |
| CLI | `claude-runner/src/claude_runner/qa.py:66` (`regression`), `:73` (`baseline`), `:121` (`_api`), `:154` (`_nightly`; список слоёв в `:162`), `:203` (`_regression`), `:229` (`_baseline`) | Команды для контейнера |
| Патч workflow | `data-tables/ops/qa_workflow_patch.py:20-22` (аргумент `layers_json`), `:348-356` (условная запись `layers` в тело запроса), `:551-559` (`_checked_layers`), `:562` (`patch_workflow`) | Файл оператора, в git не входит; в n8n не импортируется |
| Документ | `claude-runner/docs/qa-layers.md` (195 строк) | Таблица слоёв `:12-16`, формат вердикта `:20-41`, правила `:49-59`, константы `:66-75`, nightly `:161-188` |
| Тесты | `claude-runner/tests/test_qa_regression.py` (40 тестов), `tests/test_qa_api.py` (9), `tests/qa_review.test.js` (17), `tests/board_qa.test.js` (14), `tests/test_roles_qa_mode.py`, `tests/test_qa_worker.py`, `tests/qa_regression_cases.json` | Числа по `after/compare.txt`; см. раздел 7 |

### 3.2 Отдельные замечания по коду

- `qa_worker.py:302`: проверка `early_verdict` для `ui` выполняется внутри `_layer_command`, поэтому правило «`skipped`, если UI не менялся» сохраняется и в пути `layers`.
- `qa.py:154-171`: nightly-файл содержит два ключа, `scenarios` и `layers`. Код возвращает 0, только если все вердикты обоих списков `pass`.

### 3.3 Соответствие номеров задач артефактам

| Задача | Артефакт (проверен по файлу) |
| --- | --- |
| T001 | `/tmp/ahawr-qa-layers/before/` (записано «до любых изменений») |
| T002 | `/tmp/ahawr-qa-layers/notes-runner.md` (только чтение, проверок не запускалось) |
| T003 | `/tmp/ahawr-qa-layers/notes-workflow-board.md` (только чтение, проверок не запускалось) |
| T004 | снимки статуса Git `/tmp/t004_status_before.txt`, `/tmp/t004_status_after.txt` (существуют; содержимое не разбиралось) |
| T021 | `/tmp/ahawr-qa-layers/t021/` (проверка патча workflow на копии экспорта; не цитируется по критерию 2) |
| T027 | `/tmp/ahawr-qa-layers/after/` (итоговая проверка) |
| T005–T020, T022–T026 | номер не сопоставлен с артефактом; их код входит в перечень 3.1 |

## 4. Слои и правила по исходам

Слои (`docs/qa-layers.md:12-16`):

| Слой | Что проверяет | Где выполняется |
| --- | --- | --- |
| `regression` | pytest, node, ruff, mypy проекта относительно baseline | проект в режиме только чтения, в контейнере QA |
| `api` | маршруты API дашборда | встроенный стек в контейнере QA |
| `ui` | сохранённые сценарии; smoke-обход только если `options.smoke` true и существует `qa_smoke.py` (не подключено, см. риск 2) | встроенный стек в контейнере QA |

Правила по исходам (`docs/qa-layers.md:49-59`; таблица ворот `docs/qa-gate.md:77-98` не меняется):

| # | Исход слоя | Действие с миссией | Уведомление |
| --- | --- | --- | --- |
| 1 | `pass` | принять | `QA passed` |
| 2 | `fail`, `retries < QA_MAX_RETRIES` (2) | вернуть на доработку, счётчик +1 | `QA failed` с слоем и шагом |
| 3 | `fail`, `retries = QA_MAX_RETRIES` | принять | `QA still failing after the retries` |
| 4 | `skipped` (нет изменённых UI-файлов или нет профиля проекта) | принять | нет (молча) |
| 5 | `blocked` (нет папки, нет git, нет команды в профиле) | принять | `QA could not run` с причиной |
| 6 | `error` (слой упал или воркер сообщил ошибку) | принять | `QA could not run` с причиной |
| 7 | `timeout` (слой превысил лимит) | принять | `QA could not run` (timed out) |
| 8 | нет воркера (нет heartbeat, никто не взял запрос) | принять | `QA could not run` (no worker) |
| 9 | нет профиля в `qa/projects.yaml` | принять как `skipped`, причина `no QA profile` | нет |

Только `fail` возвращает миссию на доработку. Все остальные исходы миссию принимают.

## 5. Форма объединённого вердикта

Запрос передаёт `layers`; вердикт — один список, по одной записи на каждый слой, в порядке выполнения. Каждая запись содержит `layer` (`docs/qa-layers.md:22-30`):

```json
[
  {"layer": "regression", "scenario": "regression", "verdict": "fail", "reason": "new failure tests/test_x.py::test_y"},
  {"layer": "ui", "scenario": "board", "verdict": "pass"}
]
```

- Запрос без `layers` — это `["ui"]` в старом формате: без поля `layer` (`docs/qa-layers.md:39-41`).
- Nightly-файл `qa/nightly-verdict.json`: `{"scenarios": [...], "layers": [...]}` (`qa.py:154-171`). Старый файл-список читается как `scenarios`.

## 6. Baseline: NOT RUN и конфликт с критерием 3

- Команда `python -m claude_runner.qa baseline claude-runner` **NOT RUN**. Её выполняет оператор внутри контейнера `ahawr-qa`. Счётчиков нет.
- Причина: команда пишет файл `claude-runner/qa/baselines/claude-runner.json` (`qa.py:229-245`; путь = рабочая папка профиля + `baseline` из `projects.yaml`). Критерий 3 задачи T028 требует, чтобы был создан только этот отчёт. Поэтому команда не запускалась. Сейчас `claude-runner/qa/baselines/` содержит только `.gitkeep`.
- Следствие: `read_baseline` без файла возвращает `{}` (`qa_regression.py:242-243`), а `new_failures` считает всё новым (`:97-102`). В HEAD уже есть падения: pytest 2 (`before/pytest_failed.txt`), ruff 70 (`after/ruff_counts_head.txt`), mypy 72 (`after/mypy_head.log`). Значит, первый прогон `regression` без baseline вернёт `fail` и отправит миссию назад.

## 7. Проверки, которые выполнились (строки из логов)

### 7.1 T001 — до изменений (`/tmp/ahawr-qa-layers/before/`)

- `before/env.txt`: `Interpreter: Python 3.12.15`; `docker on PATH: no`; `playwright on PATH: no`; `import yaml works: yes`.
- `before/pytest.log`: `2 failed, 457 passed, 1 warning in 85.58s (0:01:25)`.
- `before/pytest_failed.txt`: `tests/test_board.py::TestUnavailableStoreApi::test_status_is_503_with_a_readable_reason`; `tests/test_dashboard.py::test_board_static_whitelist_pattern`.
- `before/node.log`: `# tests 87` / `# pass 87` / `# fail 0` (board_roles); `# tests 10` / `# pass 10` / `# fail 0` (board_qa); `# tests 13` / `# pass 13` / `# fail 0` (qa_review).
- `before/ruff.log`: `All checks passed!` (только 6 файлов, поэтому сравнение ведётся с HEAD).
- `before/mypy.log`: `Found 1 error in 1 file (checked 6 source files)`; `exit=1`.

### 7.2 T027 — итоговая проверка (`/tmp/ahawr-qa-layers/after/`)

- `after/pytest.log`: `2 failed, 353 passed, 1 skipped, 1 warning in 71.32s (0:01:11)`. Список падений совпадает с T001 (`after/before_failed_sorted.txt`).
- `after/pytest_new_files.log`: `exit=0` (новые файлы `test_qa_regression.py` и `test_qa_api.py`, сумма 49 тестов по `after/compare.txt`).
- `after/pytest_targeted.log`: только строка `.   [100%]`. Строки сводки в логе нет. `after/compare.txt:23` пишет `exit=0 (1 passed)`, но в логе этих слов нет.
- `after/node.log`: `# tests 94` / `# pass 94` / `# fail 0` (board_roles); `# tests 14` / `# pass 14` / `# fail 0` (board_qa); `exit=0`.
- `after/node_qa_review.log`: `# tests 17`, `# pass 17`, `# fail 0`, `exit=0`.
- `after/ruff.log`: `Found 70 errors.`, `[*] 19 fixable with the `--fix` option.`; `after/ruff_counts_head.txt`: `Found 70 errors.`
- `after/mypy.log`: `Found 71 errors in 10 files (checked 19 source files)`; `exit=1`. `after/mypy_head.log`: `Found 72 errors in 10 files (checked 11 source files)`.
- `after/compare.txt:70`: `exit=0. Only 'LF will be replaced by CRLF' warnings (7 lines); no whitespace errors.` (`git diff --check`).

Итог сравнения (`after/compare.txt`): новых падений pytest, node, mypy и ruff нет.

## 8. Что не запускалось и почему

- **Docker не запускался.** `before/env.txt`: `docker on PATH: no`. Не выполнены: сборка образа `ahawr-qa`, стек docker, проверка контейнера, запуск команд внутри `ahawr-qa`.
- **Playwright не запускался.** `before/env.txt`: `playwright on PATH: no`. Ни один из 17 сценариев в браузере не прогонялся. Слой `ui` проверен только юнит-тестами и JS-тестами.
- **baseline не снят** (раздел 6).
- **Интеграционный прогон не выполнялся** (`qa api` и `qa run` против стека): нет docker.
- **Сценарии не запускались** ни в одном режиме.
- **`nightly` не запускался.**
- **Патч workflow не применялся.** Проверка T021 (`t021/*.out`) вне T001/T027 и не цитируется.
- **Сводка `pytest_targeted` отсутствует в логе** (раздел 7.2).

## 9. Шаги оператора (все NOT RUN)

Порядок важен: baseline должен быть снят до включения слоя `regression`.

1. **NOT RUN:** собрать образ и запустить контейнер `ahawr-qa`, как в `claude-runner/docs/qa-container.md:82-83` (`docker compose build ahawr-qa`, `docker compose up -d ahawr-qa`).
2. **NOT RUN:** снять baseline внутри контейнера: `docker compose exec ahawr-qa python -m claude_runner.qa baseline claude-runner`. Проверить, что создан `claude-runner/qa/baselines/claude-runner.json`, и записать его счётчики.
3. **NOT RUN:** перезапустить QA worker: `docker compose restart ahawr-qa`.
4. **NOT RUN:** включить слои в `roles_json`, в опции `qa`, через интерфейс n8n или доски (не SQL): `{"qa": {"1": "<модель>", "options": {"mode": "final", "layers": ["regression", "api", "ui"]}}}` (`docs/qa-layers.md:79-83`). Имена слоёв проверяются валидатором (`roles_config.py:169`).
5. **NOT RUN:** применить патч workflow к свежему экспорту: `python data-tables/ops/qa_workflow_patch.py final <свежий_экспорт>.json '["regression","api","ui"]'` (`qa_workflow_patch.py:20-22`). Сначала проверить синтаксис на копии. Патч пишет файл, в n8n его не импортируют.
6. **NOT RUN:** запустить nightly: `docker compose exec ahawr-qa python -m claude_runner.qa nightly`. Результат в `claude-runner/qa/nightly-verdict.json`. Запуск только оператором (`docs/qa-layers.md:192`).

## 10. Риски

1. **Высокий.** Без baseline слой `regression` возвращает `fail` на любом коде (`qa_regression.py:242-243`, `:97-102`). В HEAD уже есть 2 падения pytest, 70 ruff и 72 mypy. Включать `regression` до шага 2 нельзя.
2. **Высокий.** Smoke-обход не подключён. `smoke_enabled` (`qa_worker.py:413`) нигде не вызывается. `qa_smoke.py` содержит только загрузку seeds и проверку хоста (`qa_smoke.py:47-114`), обхода нет. `docs/qa-layers.md:16` и `:89` говорят обратное. Миссия `ahawr-qa-smoke` в ROADMAP остаётся открытой (`ROADMAP.md:121`).
3. **Средний.** Бюджет регрессии. `REGRESSION_TIMEOUT_MINUTES = 25` (`qa_worker.py:49`), но дедлайн всех слоёв одного запроса равен `RUN_TIMEOUT_SECONDS` = 20 минут (`qa_worker.py:48`, `:405`), и `_wait_for_run` ограничен тем же значением (`:295`). Три слоя делят 20 минут, а `docs/qa-layers.md:68` обещает 25 минут для regression. Документ и код расходятся.
4. **Средний, по чтению кода, не проверено запуском.** При таймауте слоя поток отпускается (`qa_worker.py:378-385`, `shutdown(wait=False)`), но дочерний процесс живёт до 20 минут. Следующий слой может начаться, когда предыдущий ещё работает, в том числе с собственным стеком.
5. **Средний.** Валидация `mode` в UI не проверяет значение: в `board_roles.js` нет проверки значений `mode`, Python проверяет его в `roles_config.py:159`. В `tests/roles_cases.json` нет случая с `must be one of` (`grep -c` = 0). Расхождение отмечено в `notes-workflow-board.md:16` и не устранено. Слои в JS проверяются (`board_roles.js:260-262`).
6. **Средний.** Счётчики pytest до и после не сходятся: 457 passed против 353 passed + 1 skipped. Список падений одинаковый. `before/` не содержит команды запуска, поэтому разницу нельзя объяснить по логам. Нужно подтвердить до слияния.
7. **Низкий.** Строка `1 passed` для `pytest_targeted` в логе отсутствует (раздел 7.2).
8. **Низкий.** Регрессия в контейнере не проверена: команды `ruff check src tests` и `mypy src` из `qa/projects.yaml` в образе не запускались. Соответствие путей `container_path` тоже не проверено.
9. **Низкий.** `docs/qa-layers.md:68` даёт 25 минут для regression, а код работает с 20-минутным общим дедлайном (см. риск 3).

## 11. Предложение для CHANGELOG

Версия: `claude-runner/pyproject.toml:7` сейчас `0.9.0`. Предлагается минорный подъём до `0.10.0` (так назван релиз в `ROADMAP.md:122`). Версию в `pyproject.toml` и `api.py` менять вместе. В этом отчёте версия не менялась.

Текст для `CHANGELOG.md` (файл на английском, поэтому и предложение на английском):

```markdown
## <release date> (0.10.0)

- **QA layers** (mission ahawr-qa-layers). QA runs up to three layers in one request: `regression`
  (pytest, node, ruff and mypy against a per-project baseline), `api` (dashboard routes) and `ui`
  (the stored scenarios). Each layer yields its own verdict with a `layer` field; a failing layer
  does not skip the others. Opt in with `layers` in the `qa` options of `roles_json`; the default is
  `["ui"]`, which keeps the old output. The nightly run writes a `layers` key next to `scenarios`.
  The QA panel on the mission page reads the verdicts grouped by layer. See `claude-runner/docs/qa-layers.md`.
- **Known gaps:** the smoke crawl is not wired (`options.smoke` is read by nothing yet); the regression
  layer needs its baseline before it is enabled, otherwise every existing failure reports as new.
```

## 12. Что не проверено

- Ни один из шагов раздела 9 не выполнялся.
- Слои в контейнере, сценарии в браузере, интеграция с n8n и применение патча не проверялись.
- Таймаут слоёв (риски 3 и 4) проверен только чтением кода.
- Причина расхождения счётчиков pytest (риск 6) не установлена.

Изменён и создан только этот файл: `reports/ahawr-qa-layers-report-ru.md`.
