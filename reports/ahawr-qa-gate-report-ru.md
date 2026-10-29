# Отчёт миссии ahawr-qa-gate

Отчёт написан оператором: Worker довёл миссию до задачи T025 и там остановился (в его оболочке нет Playwright), задачи T025–T027 выполнил оператор на хосте. Все числа ниже взяты из вывода команд, запущенных оператором 2026-10-22.

## 1. Что построено

QA больше не запускается внутри n8n. Workflow только спрашивает и ждёт, QA выполняет воркер на хосте.

| Часть | Где | Что делает |
|---|---|---|
| Очередь запросов QA | `claude-runner/src/claude_runner/qa_requests.py` (константы 21–30), маршруты `/v1/qa/requests` в `api.py` | запрос: id, mission_id, working_directory, mode, status (queued, running, done, error, timeout), вердикты; создание идемпотентно, claim и result требуют bearer, неснятый запрос истекает в timeout, хранится не больше 200 запросов |
| Хостовый воркер | `data-tables/ops/qa_worker.py` (вне git) | берёт один запрос за раз, сам находит изменённые файлы через git в рабочей папке миссии (`changed_files`, `has_ui_change`), для не-git и отсутствующей папки отдаёт verdict blocked (`early_verdict`), запускает `python -m claude_runner.qa run` с таймаутом 20 минут, убивает дерево процессов (`_kill_tree`), снимает стек (`_tear_down`), пишет heartbeat `heartbeat/qa_worker.json` |
| Патч workflow | `data-tables/ops/qa_workflow_patch.py` (вне git), применяется `launch_mission.sh` (строка 41) по режиму QA из `roles_json` | только узлы n8n: QA Request, QA Wait (QA Poll, QA Poll Done?, QA Poll Count, QA Poll Limit?, QA Join), QA Review, QA Needs Changes?, QA Notice Gate?, QA Notice; старые узлы executeCommand (`OLD_NODES`) удаляются |
| Документ | `claude-runner/docs/qa-gate.md` | архитектура, состояния, таблица fail-open / fail-closed, константы, команды оператора |

## 2. Таблица fail-open / fail-closed

Единственный исход, который возвращает миссию на доработку, это вердикт fail, и только пока `retries < QA_MAX_RETRIES` (2). Всё инфраструктурное принимает миссию с одним уведомлением «QA не смог запуститься».

| Случай | Исход |
|---|---|
| pass | принять |
| fail, повторов меньше 2 | needs_changes с названием шага и причиной, счётчик +1 |
| fail, повторов уже 2 | принять, уведомление «QA всё ещё падает» |
| skipped (UI-файлы не менялись) | принять молча |
| blocked, error, timeout, никто не взял запрос за 5 минут, runner недоступен, нет воркера | принять, уведомление «QA не смог запуститься» |

## 3. Константы

`QA_MAX_RETRIES` = 2 (`qa_workflow_patch.py:108`), `QA_CLAIM_WAIT_MINUTES` = 5, таймаут запуска воркера = 20 минут (`qa_worker.py:37`), интервал опроса = 30 с, `QA_WAIT_MAX_POLLS` = ceil((5 + 20) · 60 / 30) = 50 (`qa_workflow_patch.py:91–93`), хранение запросов = 200 (`qa_requests.py:23`). Опрос не короче срока жизни запроса в runner, поэтому узел не сдаётся раньше, чем runner пометит запрос timeout.

## 4. Проверка патча на свежей выгрузке

Копия `data-tables/ops/_scratch/live_export.json` (выгрузка живого workflow, 91 узел):

- первый запуск `python qa_workflow_patch.py final <копия>`: `QA nodes patched`, узлов 102, QA-узлы: QA Request, QA Wait, QA Poll, QA Poll Done?, QA Poll Count, QA Poll Limit?, QA Join, QA Review, QA Needs Changes?, QA Notice Gate?, QA Notice;
- второй запуск: `no change`;
- `node --check` на Code-узлах QA Poll Count и QA Review: код 0;
- режимы off и nightly оставляют workflow без изменений (тест `tests/test_ops_patches_qa.py`).

## 5. Проверки

- pytest (`test_qa_requests.py`, `test_qa_requests_api.py`, `test_qa_worker.py`, `test_qa_workflow_patch.py`, `test_ops_patches_qa.py`, `test_roles_qa_mode.py`, `test_api_qa.py`): все прошли, один пропуск (POSIX-тест на Windows). Дополнительно `test_qa_runner.py` и `test_qa_stack.py` без падений.
- node: `tests/qa_review.test.js` и `tests/board_qa.test.js`: 23 из 23.
- ruff на всех изменённых файлах: `All checks passed!`. mypy: единственная ошибка старая, `board_ops_redact.py:124`, файл не менялся.
- `git diff --check`: замечания только о переводах строк.
- QA-прогон на хосте (`PYTHONPATH=src python -m claude_runner.qa run`): 15 из 15 сценариев pass, fail 0, blocked 0; после прогона контейнеров `qa-*` не осталось. Вывод сохранён в `data-tables/ops/_scratch/qa_gate_qa_run.txt`.
- Оператор исправил один тест: `test_qa_worker.py` ждал константу `UI_PATTERN_SRC` в патче, но решение про UI-файлы теперь принимает воркер; тест проверяет общий шаблон воркера и отсутствие константы в патче.
- Не запускалось: полный pytest (на Windows много POSIX-падений, список в `_scratch/baseline_pytest.log`), живая проверка в n8n (ничего не импортировалось).

## 6. Что нашлось по пути

1. Старый патч QA был нерабочим в n8n (нет папок хоста, Docker и Playwright в контейнере), потому миссия и переписала его.
2. Панель QA на доске не подключена: у `BoardQa.render` нет вызова, в `board.html` нет контейнера. Новый сценарий `board-qa-panel.yaml` честно проверяет страницу миссии и это записывает. Доработка: смонтировать панель и вызвать `/api/board/missions/{id}/qa`.
3. Worker работает внутри контейнера claude-runner, где нет Playwright: запуск сценариев пришлось выполнить оператору на хосте.

## 7. Команды оператора (НЕ ЗАПУСКАЛИСЬ)

1. Добавить в `roles_json` профиля роль `qa` с `"mode": "final"` (пример в `claude-runner/docs/qa-gate.md`, раздел Operator).
2. Запустить воркер на хосте, предложенная строка для `_start_watchers_hidden.ps1`: `Start-Hidden $py "-X utf8 `"$ops\qa_worker.py`""`; переменная `CLAUDE_RUNNER_API_KEY` должна быть в окружении воркера.
3. Первый живой тест: мини-миссия с изменением одного UI-файла, проверить вердикт и уведомление.
4. Выключение: `mode` `off` в `roles_json`, затем остановить воркер.

## 8. Риски

- Живой обмен n8n и runner не проверялся: патч проверен только на копии выгрузки, узлы QA Review и QA Poll Count на синтаксис.
- Счётчик повторов хранится без новой колонки; поведение при перезапуске n8n между повторами не проверено.
- Воркер не запущен, пока режим выключен; при включённом режиме без воркера миссия принимается с уведомлением (строки 8 и 10 таблицы).

## 9. Предложение для CHANGELOG (новая функция: средний номер)

QA gate: очередь запросов QA в runner (`/v1/qa/requests`), хостовый воркер, узлы n8n только спрашивают и ждут; fail возвращает миссию (не больше двух раз), блокировки и ошибки QA миссию не отклоняют; выключено по умолчанию (`roles_json`, `qa.options.mode`).
