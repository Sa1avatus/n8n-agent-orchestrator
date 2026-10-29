# Отчёт миссии ahawr-qa-container: QA в собственном контейнере

Дата отчёта: 2026-10-22. Рабочая папка: `D:\n8n` (в Git Bash: `/d/n8n`). Коммитов и push не делалось.

## 1. Итог

- Код, тесты и артефакты развёртывания написаны в репозитории: сервис `ahawr-qa`, образ `claude-runner/qa/Dockerfile.qa`, патч `claude-runner/qa/compose-qa.patch`, загрузчик фикстур, режим стека `inprocess`, рабочий процесс `qa_worker.py` внутри контейнера и два скрипта оператора (только печатают команды).
- **Не запускалось: сборка образа, сценарии в контейнере и паритет двух режимов (15 сценариев).** В оболочке агента нет docker и Playwright. Все шаги развёртывания отмечены как НЕ ЗАПУСК (раздел 8).
- Docker-compose, `claude-runner/Dockerfile`, контейнеры, очередь и производственная база не менялись.

## 2. Ограничения отчёта

- Результаты по каждой задаче T001-T019 (оценки ревьюера, число попыток) хранятся в таблице состояния n8n. Оболочка агента не может их прочитать: `data-tables/ops/pgdb.py` вызывает `docker exec ahawr-postgres`, а docker здесь нет (`FileNotFoundError: 'docker'`).
- Журнал очереди `data-tables/ops/real_queue.log` для этой миссии содержит только старт: строка 3518, `started ahawr-qa-container (run 1, attempts per task: 5)`.
- Перечень задач с id, заголовками и scope в репозитории не найден: `data-tables/ops/_make_qa_container_mission.py` задает миссию одной целью и шестью частями (Part 1-6), а не списком T001-T019; `claude-runner/qa/fixtures/n8n_tables.json` содержит заглушки (`'t'`, `'e'`, `'x'`), а не строки состояния этой миссии. Заголовки и scope для каждой строки T001-T019 в таблице ниже поэтому не приведены — их источник (таблица состояния n8n) здесь недоступен. Результаты задач, не найденные, отмечены «результат не найден»; ничего не выдумано.
- `data-tables/ops/_accept_fc_t019.py` относится к миссии ahawr-features-config, а не к этой миссии. Не трогалось.

## 3. Что построено (T001-T019 как диапазон; по частям задания Part 1-6)

**Источник списка задач.** Список T001-T019 этой миссии в репозитории не найден. Команда `grep -rIn 'T001' /d/n8n/data-tables /d/n8n/reports` находит только посторонние вхождения (шаблоны `agent_prompts.csv`, отчёты других миссий, макеты доски); ни одно не относится к `ahawr-qa-container`. `data-tables/ops/_make_qa_container_mission.py` (строки 13-23) задаёт миссию одной целью и шестью частями (Part 1-6), а не 19 задачами. Журнал `real_queue.log:3518` содержит только старт миссии. Заголовки, scope и результаты T001-T019 лежат в таблице состояния n8n, которую оболочка агента прочитать не может (раздел 2).

**Поэтому соответствие отдельных T-id частям не установлено, и построчная таблица по задачам не приводится.** Ниже сведены реальные части задания Part 1-6 и артефакты, которые они породили (проверены по файлам). Все 19 задач T001-T019 покрыты этим перечнем только как диапазон: T001-T019 → Part 1-6, без привязки конкретной задачи к конкретной части.

| Часть задания | Что сделано | Где |
|---|---|---|
| 1. Проектный документ | Архитектура до и после, таблица прав, два режима, шаги оператора | `claude-runner/docs/qa-container.md` (разделы: строка 32 «Rights table», строка 78 «Operator steps») |
| 2. Загрузчик фикстур | `FixtureRows` (строка 20), `load_fixture` (строка 26), `board_store_from_rows` (строка 48) | `claude-runner/src/claude_runner/qa_fixture.py` |
| 2. Настройка дашборда | `QA_FIXTURE_ENV = "DASHBOARD_QA_FIXTURE"` (строка 58); выбор хранилища из фикстуры (строка 157); отказ при заданном DSN (строки 566-571) | `claude-runner/src/claude_runner/dashboard.py` |
| 3. Режим `inprocess` | `STACK_MODES = ("docker", "inprocess")` (строка 31); диспетчеризация (строки 108-114); `_inprocess_up` (строка 226) | `claude-runner/src/claude_runner/qa_stack.py` |
| 3. Выбор режима в CLI | `STACK_MODE_ENV = "QA_STACK_MODE"` (строка 22); опция `--stack` (строка 41); значение из опции или env, иначе `DEFAULT_STACK_MODE` (строка 87) | `claude-runner/src/claude_runner/qa.py` |
| 4. Worker в репозитории | `container_path` через `map_host_path` из config (строки 139-140); `main` (строка 326); 364 строки | `claude-runner/src/claude_runner/qa_worker.py` |
| 4. Старый файл | Заменён заметкой «retired», не удалён, при запуске выходит с кодом 2 | `data-tables/ops/qa_worker.py` (14 строк) |
| 5. Образ | Non-root `USER node`, `QA_STACK_MODE=inprocess`, CMD `python -m claude_runner.qa_worker`, Chromium в образе | `claude-runner/qa/Dockerfile.qa` |
| 5. Патч compose | Сервис `ahawr-qa`, read-only, tmpfs, лимиты памяти и CPU, volume `qa-runs`, снят порт `127.0.0.1:8700:8700` | `claude-runner/qa/compose-qa.patch` |
| 5. Статические тесты | Нет docker CLI и сокета, non-root, read-only монтирования, только две rw-привязки, нет privileged, порт снят | `claude-runner/tests/test_qa_container_static.py:61-104` |
| 6. Скрипты оператора | Печатают команды, ничего не запускают: retire (PID, `taskkill /PID ... /T /F`) и rollback | `data-tables/ops/_retire_host_qa_worker.sh` (55 строк), `data-tables/ops/_rollback_host_qa_worker.sh` (32 строки) |

Новые тесты: `tests/test_qa_fixture.py` (6 тестов), `tests/test_qa_worker.py` (15 тестов; `git status --short` показывает `?? claude-runner/tests/test_qa_worker.py`, файл новый и не отслеживается), `tests/test_qa_stack_inprocess.py` (6 тестов), `tests/test_qa_container_static.py` (9 тестов). Тесты дашборда для фикстуры: `tests/test_dashboard.py:1143-1151`.

## 4. Архитектура до и после

- **До:** хост-процесс `data-tables/ops/qa_worker.py` забирает запрос из очереди раннера через порт, опубликованный на `127.0.0.1:8700`, находит изменённые файлы через git на хосте и запускает `python -m claude_runner.qa run`. Тот поднимает временный стек Docker (`qa-*`, Postgres) через `qa_stack.py`.
- **После:** сервис `ahawr-qa` (Linux, Python 3.12, Node 22, Playwright с Chromium) внутри compose-сети. Воркер `claude_runner.qa_worker` ходит в раннер по `http://claude-runner:8700`, порт раннера на хост не публикуется. В контейнере стек режима `inprocess` (дашборд-процесс в той же среде, без Postgres и Docker). Хостовый воркер выводится из работы оператором.

## 5. Таблица прав (по `qa-container.md`, сверено с `compose-qa.patch`)

| Путь в контейнере | Источник | Режим | Кто пишет |
|---|---|---|---|
| хост-диск `D:` целиком (`/d`) | bind, `read_only: true` | ro | никто |
| рабочие папки миссий (`/workspace`) | bind | ro | никто |
| `/qa-runs` | volume `qa-runs` | rw | только прогон QA |
| `/d/n8n/data-tables/ops/heartbeat` | bind, создаётся при отсутствии | rw | только `qa_worker.json` |
| корень ФС | образ | read_only | никто |
| `/tmp`, `/home/node` | tmpfs | rw | прогон, теряется при выходе |

Расхождение: документ описывает `/repo` (только git-checkout), а compose монтирует весь диск `D:` как `/d` read-only. Это шире документа и открывает для тестов, которые пишут агенты, всё дерево диска, включая файлы с секретами, если они лежат на диске (раздел 9).

## 6. Два режима стека

- `docker` (по умолчанию на хосте): `qa_stack.stack_up` поднимает Postgres и дашборд в контейнерах, снимает по id.
- `inprocess` (по умолчанию в `ahawr-qa`, через `QA_STACK_MODE=inprocess`): дашборд в дочернем процессе на свободном порту, фикстура `qa/fixtures/*.json` через `DASHBOARD_QA_FIXTURE`.
- Выбор: опция `--stack` имеет приоритет над `QA_STACK_MODE`. Неизвестное значение отклоняется (`qa_stack.py:31`).
- Документ `qa-container.md` называет переменную `QA_STACK`, в коде её имя `QA_STACK_MODE` (`qa.py:22`). Документ надо поправить.

## 7. Результаты загрузчика фикстур (считано в этом прогоне)

Команда: `load_fixture(Path('qa/fixtures/<name>.json'))` для двух файлов.

```
default {'missions': 12, 'live_state': 9, 'queue': 7}
empty {'missions': 0, 'live_state': 0, 'queue': 0}
```

Счётчики совпадают с файлами фикстур; `test_fixture_counts_match_cases` проверяет то же самое в тестах.

## 8. Проверки

### 8.1 Перезапущено при подготовке этой редакции отчёта

Команды выполнены повторно в этом шаге (22 октября 2026) в оболочке агента без docker и без Playwright. `git apply --check` и `git status --short` — из `/d/n8n`; ruff, mypy и pytest — из `/d/n8n/claude-runner`. Строки ниже — вывод этих запусков.

- `git apply --check claude-runner/qa/compose-qa.patch` → `apply-check exit=0`, вывода нет.
- `python -m ruff check src/claude_runner/qa_fixture.py src/claude_runner/qa_worker.py src/claude_runner/qa.py src/claude_runner/qa_stack.py src/claude_runner/qa_runner.py src/claude_runner/qa_requests.py src/claude_runner/dashboard.py` → `All checks passed!`
- `python -m mypy src/claude_runner/qa_fixture.py src/claude_runner/qa_worker.py src/claude_runner/qa.py` → `Found 26 errors in 3 files (checked 3 source files)`. Последняя показанная строка: `src/claude_runner/board_pg.py:1492: error: Returning Any from function declared to return "str | None"  [no-any-return]`. Эти ошибки лежат в файлах `board*.py`, которые подтянуты импортами; базовое число до изменений не измерялось (stash запрещён).
- `python -m pytest tests/test_qa_fixture.py tests/test_qa_container_static.py tests/test_qa_worker.py tests/test_qa_stack_inprocess.py --no-cov -p no:cacheprovider` → `41 passed, 1 warning in 12.89s`
- `python -m pytest tests/test_qa_cli.py tests/test_qa_stack.py tests/test_qa_runner.py --no-cov -p no:cacheprovider` → `135 passed, 1 warning in 2.74s`
- `python -m pytest tests/test_dashboard.py -k "qa or fixture or QA" --no-cov -p no:cacheprovider` → `2 passed, 44 deselected, 1 warning in 1.09s`
- `python -m pytest tests/test_dashboard.py --no-cov -p no:cacheprovider` → `FAILED tests/test_dashboard.py::test_board_static_whitelist_pattern` и `1 failed, 45 passed, 1 warning in 58.81s`. Связь с фикстурами QA не установлена; на чистом HEAD проверка не делалась (stash запрещён), поэтому «старое ли падение» не подтверждено.
- `git status --short -- reports` → `?? reports/`
- `git status --short -- claude-runner/tests/test_qa_worker.py` → `?? claude-runner/tests/test_qa_worker.py` (новый неотслеживаемый файл).

### 8.2 Из прошлых задач (T001-T019)

Результаты T001-T019 не читались (раздел 2). Этот отчёт не переписывает их и не выдаёт их за свои.

### 8.3 НЕ ЗАПУСК

- Сборка образа `docker compose build ahawr-qa`: НЕ ЗАПУСК, нет docker.
- Сценарии в контейнере (`docker compose exec ahawr-qa python -m claude_runner.qa run --tag ui`): НЕ ЗАПУСК.
- Паритет 15 сценариев в двух режимах (`--stack docker` и `--stack inprocess`): НЕ ЗАПУСК, нет docker и Playwright. Список из 15 сценариев не зафиксирован: в `claude-runner/qa/scenarios` 17 файлов.
- Запуск `docker compose config` для проверки интерполяции переменных: НЕ ЗАПУСК.
- Скрипты оператора `_retire_host_qa_worker.sh` и `_rollback_host_qa_worker.sh`: НЕ ЗАПУСК (по заданию это только печать).
- Playwright-сценарии, включая `board_qa_panel` и `save_validation_error_then_persist`: НЕ ЗАПУСК.

## 9. Риски

1. **Монтирование всего диска `D:` read-only в `/d`** (`compose-qa.patch`). Тесты, написанные агентами, видят все файлы диска, в том числе `.env`. Лучше сузить монтирование до репозитория и папки миссий, как в документе.
2. **Для Postgres и n8n в контейнере нет пути.** Режим `inprocess` покрывает только UI и API. Сценарии, которым нужна реальная база, в контейнере не запустятся. Это требует отдельной изолированной схемы docker-in-docker и одобрения.
3. **Интерполяция путей в compose:** `${AHAWR_HOST_DRIVE:-D:\}` и `D:\n8n\...` содержат обратные слэши. Проверить `docker compose config` до запуска.
4. **Записи в `/qa-runs`:** путь RUNS_DIR внутри контейнера не проверен на соответствие volume.
5. **Падение `test_board_static_whitelist_pattern`** (раздел 8.1). Не связано с QA, но в общем прогоне дашборда остаётся красным.
6. **Документ `qa-container.md` устарел:** пишет, что `Dockerfile.qa`, `compose-qa.patch` и скрипты «planned, not present» (строки 4-7), хотя все они есть. Скрипт retire в `qa_worker.py` тоже описан как «not yet in the workspace». Исправить вместе с релизом.
7. **Номер версии:** `ROADMAP.md` (строка 121) относит миссию к `0.11.0`, а правило из `README.md` (строка 761) для шага с новыми функциями даёт `0.10.0`. Решение за оператором.

## 10. Шаги оператора (все НЕ ЗАПУСК)

Выполнять по порядку на хосте, вывод каждого записать в отчёт миссии. Каждый шаг, меняющий `docker-compose.yml`, требует подтверждения.

1. НЕ ЗАПУСК, сборка: `docker compose build ahawr-qa`.
2. НЕ ЗАПУСК, старт: `docker compose up -d ahawr-qa`.
3. НЕ ЗАПУСК, проверка контейнера: `docker compose exec ahawr-qa python -m claude_runner.qa run --tag ui`, затем `docker compose exec ahawr-qa sh -c 'test ! -S /var/run/docker.sock && echo no-socket'`. Корень ФС должен быть read-only.
4. НЕ ЗАПУСК, паритет на хосте: зафиксировать список из 15 сценариев, затем прогнать их `python -m claude_runner.qa run --stack docker` и `--stack inprocess`; сравнить вердикты с контейнером.
5. НЕ ЗАПУСК, вывод хостового воркера из работы: `data-tables/ops/_retire_host_qa_worker.sh` (печатает команды, PID останавливается по PID).
6. НЕ ЗАПУСК, закрыть порт: применить `claude-runner/qa/compose-qa.patch` (`git apply`), затем `docker compose up -d claude-runner`. Перезапуск раннера, поэтому это действие оператора.
7. НЕ ЗАПУСК, откат: `docker compose stop ahawr-qa`; обратный патч `git apply -R claude-runner/qa/compose-qa.patch`; `docker compose up -d claude-runner`; `data-tables/ops/_rollback_host_qa_worker.sh` (печатает команды возврата хостового воркера).

## 11. Предложение для CHANGELOG (версия 0.9.0 -> 0.10.0)

Новые функции, значит средний номер +1, последний в 0. Запись в `CHANGELOG.md` на английском, как остальные записи файла. Дата выпуска ставится при выпуске.

```
## <release date> (0.10.0)

New functions of the ahawr-qa-container mission: QA runs in its own Linux container.

- **QA container** (mission ahawr-qa-container). `claude-runner/qa/Dockerfile.qa` (Chromium, pytest,
  ruff, mypy, node; non-root; no docker CLI, no Docker socket) and `claude-runner/qa/compose-qa.patch`
  (service `ahawr-qa`, read-only root, tmpfs, `qa-runs` volume, heartbeat folder, the runner port 8700
  is no longer published). Not built or started in the change: see `claude-runner/docs/qa-container.md`.
- **Worker in the repository** `claude_runner.qa_worker`; the host script `data-tables/ops/qa_worker.py`
  is retired (a note, not deleted).
- **Stack mode `inprocess`** (`qa_stack.py`, `qa.py --stack docker|inprocess`, `QA_STACK_MODE`).
- **Dashboard fixture** `DASHBOARD_QA_FIXTURE` (off by default; refused together with
  `DASHBOARD_BOARD_PG_DSN`); loader `qa_fixture.py`.
```

Строку «Version: **0.9.0** ...» в начале `CHANGELOG.md` тоже нужно поменять. Номер меняется в `claude-runner/pyproject.toml` (строка 7) и в `claude-runner/src/claude_runner/api.py` (строка 169).

## 12. Что не проверено

- Образ, контейнер, сценарии в контейнере и паритет двух режимов: НЕ ЗАПУСК (раздел 8.3).
- `docker compose config`, поведение монтирований в реальном Docker: НЕ ЗАПУСК.
- Базовое число ошибок mypy до изменений и падение whitelist-теста на чистом HEAD: не измерено.
- Результаты T001-T019 из таблицы состояния n8n: не прочитаны (раздел 2); id/заголовки/scope задач не найдены в репозитории (раздел 3).
