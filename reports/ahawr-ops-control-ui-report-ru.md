# Отчёт T019: Control tab и host agent для операций (ahawr-ops-control-ui)

Дата: 2026-10-26. Ветка `main`, HEAD `2df39fa`. Отчёт написан чтением файлов и запуском тестов. Файлов кроме этого отчёта не менял. Агент не запущен, `DASHBOARD_OPS_TAB` не включён, SQL не применён, контейнеры и очередь не трогались.

Итог: агент и вкладка Control написаны, их тесты проходят. До запуска агента нужно исправить три риска из раздела 7: R1 (DRY пишет в базу), R2 (`host_snapshot` не пишется), R3 (пауза и таймаут 600 с). Без R2 кнопки «Pause at boundary» и «Stop mission» неактивны даже при работающей очереди.

## 1. Покрытие задач T001–T018

Пункт «каждая задача T001–T018» выполнен не полностью. Список задач с названиями в репозитории не записан: `grep -rn 'T0[01][0-9]' claude-runner docs reports data-tables` даёт одно совпадение в области миссии, `claude-runner/docs/board-ops-agent.md:147` («left for T003»). Список задач хранится в состоянии миссии в базе n8n (столбец `task_index` в `_accept_oc_t018.py` доходит до 20), но контейнер в этой задаче недоступен и состояние миссии не читалось. Покрытие дано по частям миссии из `data-tables/ops/_make_ops_control_ui_mission.py` (OBJECTIVE, DESIGN 1–7, PART 1–5, ACCEPT).

| Часть миссии | Что покрывает | Где | Статус |
|---|---|---|---|
| Дизайн-заметка | одна заметка, 598 строк | `claude-runner/docs/board-ops-agent.md` | написана; часть разделов устарела (раздел 6) |
| Агент и тесты агента | агент 716 строк, 35 тестов | `data-tables/ops/board_agent.py`, `claude-runner/tests/test_board_agent.py` | написан; два действия помечены неверно; DRY небезопасен (R1) |
| Вкладка Control | вид, DOM, стили, вставка за флагом, i18n | `board_control_view.js` (71 стр.), `board_control.js` (193), `board_control.css` (19), `dashboard.py:188-191`, `dashboard.html:1307-1366` | написана; статус-полоса зависит от `host_snapshot`, который не пишется (R2) |
| Тесты безопасности | неизвестное действие, аргумент вне списка, подтверждение, граница, второй `queue_watch`, DRY, флаг | `test_board_agent.py:177, 198, 213, 225, 250, 324, 364, 457`; `test_dashboard_ops_tab.py` (4 теста) | проходят; DRY-тест фиксирует запись `done` (R1) |
| Ничего живого не трогать | в `docker-compose.yml` нет строки флага | `grep -c OPS_TAB docker-compose.yml` даёт 0 | соблюдено для этой задачи |
| Приёмка T018 (оператор) | оператор закрыл T018 с записью проверок | `data-tables/ops/_accept_oc_t018.py:9-12` (task_index 20) | см. раздел 5 |

Конкретные задачи T001–T018 по одной не сопоставлены. Чтобы закрыть этот пункт, нужен список задач миссии с номерами.

## 2. Что построено (file:line)

- **Агент** `data-tables/ops/board_agent.py` (вне git). Константы: `POLL_SECONDS = 10` (:22), `QUEUE_LOG_TAIL_PUBLISH_SECONDS = 10` (:25), `SUBPROCESS_TIMEOUT_SECONDS = 60` (:29), `MAX_OUTPUT_CHARS = 20000` (:32), `PAUSE_DEADLINE_SECONDS = 8 * 60 * 60` (:97). Белые списки: `KNOWN_ACTIONS` (:113), `DANGEROUS_ACTIONS` (:129), `IMPLEMENTED_ACTIONS` (:143). Шаблоны argv: `SCRIPT_PATHS` (:58), `KILL_PATTERNS` (:64), `PROCESS_LIST_ARGV` (:69), `QUEUE_CHAIN_COMMAND_LINE` (:101). `validate_command` (:184). `run_argv` без shell, с таймаутом (:211). `CLAIM_SQL` (:240). `PgdbDb` с `docker exec` (:318). `BoardClient` (:361). `stop_mission` (:577), `pause_at_boundary` (:585), `resume` (:596), `restart_queue` (:603), `execute` (:616), `STOP_FILE` (:628), `_handle_command` (:644), `run_tick` (:659), `main` (:687).
- **Тесты агента** `claude-runner/tests/test_board_agent.py`: 35 тестов. Агент грузится по пути, база, список процессов и runner подменяются фейками.
- **Вкладка Control, чистый вид** `claude-runner/src/claude_runner/board_control_view.js`: `ACTIONS` (:8), `agentModel` (:16), `buttonsModel` (:27), `controlModel` (:52), экспорт (:65).
- **Вкладка Control, DOM** `claude-runner/src/claude_runner/board_control.js` (193 строки): `POLL_MS = 5000` (:10), `MAX_POLLS = 120` (:11), `post` с заголовком `X-AHAWR-Dashboard: 1` (:105-111), `onAction` с запросом prepare (:113), `onConfirm` (:137), `mount` с `pagehide` (:175-182), `unmount` с очисткой таймера (:184-190).
- **Стили** `claude-runner/src/claude_runner/board_control.css` (19 строк).
- **Флаг** `claude-runner/src/claude_runner/dashboard.py:188-191`: при `DASHBOARD_OPS_TAB` включённом убираются маркеры `<!--ops-tab-->`, при выключенном вырезается блок целиком.
- **Вставка на странице** `claude-runner/src/claude_runner/dashboard.html`: маркер (:1307), css (:1308), `showControl` (:1335), загрузка скриптов (:1356-1357), закрывающий маркер (:1366).
- **i18n**: ключи `control.*` в `board_i18n_en.json`, `board_i18n_ru.json` и `BUNDLED_EN` в `board_i18n.js`. Паритет проверяет `claude-runner/tests/board_i18n.test.js`.
- **Тесты UI**: `claude-runner/tests/board_control_view.test.js` (9 тестов) с общими случаями `control_view_cases.json`; `claude-runner/tests/test_dashboard_ops_tab.py` (4 теста флага, включая `test_ops_tab_off_leaves_the_page_unchanged`).
- **SQL, только для оператора**: `claude-runner/docs/board-ops-agent-schema.sql` (70 строк, четыре `CREATE TABLE IF NOT EXISTS`, частичный уникальный индекс `commands_one_running_idx` в строках 39-41; GRANT, ALTER и DROP нет).
- **Флаг в `.env.example`**: строка `DASHBOARD_OPS_TAB=` без значения (`.env.example:207`).

## 3. Таблица действий

Колонка «Граница» — требование `requires_boundary` в определении действия. Проверка агента — `_at_task_boundary` (`board_agent.py:569`); серверная проверка — `board_ops_state.check_boundary` (`board_ops_state.py:181`), вызывается в prepare и confirm (`board_ops_api.py:384, :440`).

| Действие | Что запускает агент | Опасно | Граница | Статус |
|---|---|---|---|---|
| `stop_mission` | если есть running worker-run: `POST localhost:8701/api/runs/<run_id>/cancel` через curl; затем PowerShell `taskkill /T /F` по шаблону `[r]eal_queue\.sh\|[w]ait_mission_end\|[l]aunch_mission` (`_kill_chain`, :510) | да | нет | реализовано (:577) |
| `pause_at_boundary` | ждёт границу задачи (`_at_task_boundary`, :569) с дедлайном 8 ч (:97); затем тот же kill цепочки | да | да | реализовано (:585) |
| `resume` | проверяет число процессов `queue_watch` (`PROCESS_LIST_ARGV`, :69); если 0, CIM `Create` для `D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py` (`_start_detached`, :501) | нет | нет | реализовано (:596) |
| `restart_queue` | kill цепочки, затем CIM `Create` для `bash real_queue.sh` с фиксированной строкой (`QUEUE_CHAIN_COMMAND_LINE`, :101) | да | нет | реализовано (:603) |
| `restart_queue_watch` | — | да | нет | rejected: not implemented in the agent |
| `restart_docker_watchdog` | — | да | нет | rejected: not implemented in the agent |
| `restart_tg_relay` | — | да | нет | rejected: not implemented in the agent; тест :457 |
| `rebuild_service` | — | да | да (по серверу) | rejected: not implemented in the agent |
| `docker_update_memory` | — | да | нет | rejected: not implemented in the agent |
| `set_llama_preset` | — | да | да (по серверу) | rejected: not implemented in the agent |
| `switch_model_pair` | — | да | нет | rejected: not implemented in the agent (в дизайн-заметке ошибочно «implemented») |
| `send_test_notification` | — | нет | нет | rejected: not implemented in the agent (в дизайн-заметке ошибочно «implemented») |
| неизвестное имя | — | — | — | rejected: unknown action (:185-186) |

Порядок проверки в `validate_command` (:184-204): неизвестное имя, затем «не реализовано», затем подтверждение для опасных (:190-191), затем аргументы из белых списков. Аргумент строки команды в argv не попадает: argv собирается только из констант.

Отвергнутые действия, для которых в `test_board_agent.py` нет отдельного теста: все, кроме `restart_tg_relay` (:457) и `send_test_notification` (параметризация того же теста). Проверка для них та же, что и для остальных отвергнутых, но их поведение не покрыто тестом.

## 4. Цикл агента

1. `main` (:687) на каждом такте проверяет файл остановки `board_agent.stop` (:628), вызывает `run_tick` (:659) и спит `POLL_SECONDS` = 10 с.
2. `run_tick` (:659) пишет heartbeat (`write_heartbeat`, :416, `INSERT INTO board.agent`).
3. Если ничего не ждёт, захватывает одну команду (`claim_next`, :374): `CLAIM_SQL` (:240) в одной транзакции ставит `running` только при отсутствии других `running`.
4. `_handle_command` (:644) отвергает через `reject_command` (:405) или выполняет `execute` (:616). Результат пишется через `write_result` (:390) как `done` или `failed`. Пауза, которая ещё ждёт границу, остаётся в состоянии ожидания.
5. Публикация хвоста `real_queue.log` в `board.queue_log` идёт по интервалу (`publish_queue_log`, :425).
6. Снимок `board.host_snapshot` через `publish_snapshot` (:459) на этом пути не вызывается: `main` не передаёт сборщик, в `run_tick` стоит пометка `shortcut` (:681). См. R2.

## 5. Проверки

Запущены в этой задаче (команды и точные строки вывода, повторённые при написании отчёта):

| Команда | Ключевая строка вывода | Результат |
|---|---|---|
| `python -m pytest tests/test_board_agent.py tests/test_dashboard_ops_tab.py tests/test_board_ops_api.py -q --no-cov -p no:cacheprovider` | `57 passed, 1 warning in 7.08s` | exit 0 |
| `node --test tests/board_control_view.test.js tests/board_i18n.test.js` | `# tests 10` / `# pass 10` / `# fail 0` | exit 0 |
| `python -m pytest tests/test_board_agent.py --no-cov -p no:warnings -p no:cacheprovider` | `35 passed in 1.26s` | exit 0 |
| `python -m pytest tests/test_dashboard_ops_tab.py --no-cov -p no:warnings -p no:cacheprovider` | `4 passed in 5.07s` | exit 0 |
| `python -m pytest tests/test_board_ops_api.py --no-cov -p no:warnings -p no:cacheprovider` | `18 passed in 1.38s` | exit 0 |
| `node --test tests/board_control_view.test.js` | `# tests 9` / `# pass 9` / `# fail 0` | exit 0 |
| `node --test tests/board_i18n.test.js` (паритет) | `# tests 1` / `# pass 1` / `# fail 0` | exit 0 |
| `python -m ruff check /d/n8n/data-tables/ops/board_agent.py` (из `claude-runner`) | `All checks passed!` | exit 0 |

Числа по отдельным файлам совпадают с записью оператора в `_accept_oc_t018.py` (35, 4, 18, 9 с 0 падений).

Записи оператора, не повторённые здесь. Файл `data-tables/ops/_accept_oc_t018.py:9-12` содержит такие строки: «mypy gives 35 errors there against 38 for the HEAD version (not worse); test_board_ops_api.py has no mypy errors.» и «Checks run by the operator: pytest test_board_ops_api, test_board_agent, test_dashboard_ops_tab all pass; node board_control_view.test.js 9 pass 0 fail». Числа pytest и node совпадают с моими прогонами (18, 35, 4 и 9 с 0 падений). Цифры mypy в этой задаче не перепроверялись.

Не запущено:

- **mypy** по изменённым файлам: не запускался в этой задаче. Цифры 35 и 38 приведены только по записи оператора.
- **Полный прогон pytest и node**: не запускался, по правилам задачи.
- **Браузер**: вкладка Control не открывалась. Тесты флага проверяют вставку, но не отрисовку и не нажатия.
- **DRY-прогон агента на базе** и **сквозной тест с фейковой базой** с полным циклом: нет. Есть только модульные тесты.
- **Docker, QA-стек, запуск агента и дашборда**: нет (запрещено правилами задачи).
- **Проверка живых таблиц `board.*`**: NOT RUN, см. раздел 8.

## 6. Расхождения документа `board-ops-agent.md` с кодом

Код считается верным. Документ нужно поправить, но в этой задаче его не правят.

- `board-ops-agent.md:298-299` и `:486-491` говорят, что агента и вкладки нет. Оба уже есть.
- `board-ops-agent.md:200-201`: `switch_model_pair` и `send_test_notification` помечены «implemented». В коде их нет в `IMPLEMENTED_ACTIONS` (`board_agent.py:143-150`), они отвергаются (:185-191).
- `board-ops-agent.md` (раздел про DRY): «no writes to the database beyond the heartbeat». В коде DRY не отключает `claim_next` и `write_result` (R1).
- `board-ops-agent.md:155-161`: агент использует `data-tables/ops/pgdb.py`. В коде свой `PgdbDb` с `docker exec ... psql` (`board_agent.py:263-276, 318`), `pgdb` не импортируется.
- `board-ops-agent.md` (раздел про снимок): снимок публикуется на каждом такте. В коде нет (R2).
- `board-ops-agent.md:259-263`: heartbeat пишет строку аудита `changelog`. В коде только `INSERT` (`board_agent.py:416`).
- `board-ops-agent.md:315-320, 455-462`: агент сам снимает зависшие команды. В `board_agent.py` нет `mark_stale`, `reap` и `stale` (`grep` даёт 0 совпадений) (R7).
- `board-ops-agent.md:392-394`: вкладка опрашивает каждые 10 с. В коде `POLL_MS = 5000` (`board_control.js:10`).
- `board-ops-agent.md:292`: строка флага в compose названа так. Фактически `docker-compose.yml:295`.

## 7. Риски

- **R1 (высокий): DRY-режим пишет в базу.** `claim_next` (`board_agent.py:374`) и `write_result` (:390) выполняются и при `BOARD_AGENT_DRY=1`; DRY отключает только запуск argv (`run_argv`, :211). Тест `test_dry_run_resume_writes_done_without_spawning` (`test_board_agent.py:250`) проверяет статус `done`. На живой базе DRY съедает команды очереди и помечает их выполненными без запуска. Приёмка «the agent does nothing in DRY mode» не выполнена. До исправления DRY на живой базе не запускать.
- **R2 (высокий): `host_snapshot` не пишется.** `main` вызывает `run_tick` без сборщика (`board_agent.py:681` shortcut). Единственный `INSERT` в `host_snapshot` — `board_agent.py:466`, и его никто не вызывает (`grep publish_snapshot` в `data-tables/ops` даёт только определение). `process_panel` берёт `queue.alive` из снимка (`board_ops_state.py:136, 145`). Вкладка считает `queue.running = !!queue.alive` (`board_control_view.js:55`). При `false` кнопки «Pause at boundary» и «Stop mission» отключаются с причиной «The queue is not running» (`board_control_view.js:36-37`). То есть при работающей очереди остановить миссию из UI нельзя. Приёмка «writes ... the snapshot» не выполнена.
- **R3 (высокий): пауза и таймаут 600 с.** Команда `pause_at_boundary` остаётся `running`. `_expire_stale` (`board_ops_api.py:615`) вызывается при каждом чтении (`:366, :421, :475, :495`) и помечает `running` старше `command_timeout_s` = 600 с (`board_ops_settings.py:108`) как `failed`. Агент продолжает ждать границу до 8 ч (`PAUSE_DEADLINE_SECONDS`, :97) и на границе выполняет kill. Результат уже не записывается, потому что строка не `running`. Итог: UI показывает `failed`, а цепочка очереди всё равно останавливается. Пока пауза висит, новые команды не захватываются, в том числе `stop_mission`.
- **R4 (средний): обрыв задачи.** `stop_mission` (:577) и `restart_queue` (:603) убивают цепочку без проверки границы. По дизайну, но это обрыв задачи на середине.
- **R5 (средний): рост таблиц.** `board.agent` получает строку на каждом такте: при интервале 10 с это 8 640 строк в сутки. Очистки нет. `board.queue_log` растёт вместе с логом.
- **R6 (средний): двойной запуск.** У агента нет блокировки от второго экземпляра. Инструкция оператору «do not start a second one» есть только в тексте баннера (`board_control_view.js:23`).
- **R7 (средний): агент не снимает зависшие команды.** Если агент упал во время `running`, строка снимается только дашбордом через 600 с (R3).
- **R8 (средний): `docker` из агента.** `PSQL_ARGV` (`board_agent.py:263`) вызывает `docker exec -i ahawr-postgres psql`. Правило миссии, что у агента нет docker в PATH и любая docker-команда показывается оператору, для этого пути не выполняется. Решение оператора.
- **R9 (средний): агент не в git.** `board_agent.py` не версионируется, резервной копии в репозитории нет.
- **R10 (низкий): UI не проверен в браузере** (см. раздел 5).
- **R11 (низкий): устаревшая документация** (раздел 6).

## 8. Операторские шаги (все NOT RUN)

Все шаги ниже не выполнялись. Шаг 0 выполняется первым. Агент не запускать до исправления R1–R3.

- **Шаг 0, NOT RUN. Проверить таблицы:**
  ```
  SELECT to_regclass('board.commands') AS commands, to_regclass('board.agent') AS agent,
         to_regclass('board.queue_log') AS queue_log, to_regclass('board.host_snapshot') AS host_snapshot;
  ```
  Выполнить на базе через `docker exec -i ahawr-postgres psql -U n8n -d n8n` (имя базы и роль как в `board_agent.py:263`; сверить с `.env` оператором, сам `.env` не читать).
- **Шаг 1, NOT RUN. Только если шаг 0 дал NULL.** Применить идемпотентный SQL: `docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 < claude-runner/docs/board-ops-agent-schema.sql`
- **Шаг 2, NOT RUN. Строка в compose.** В `docker-compose.yml`, сервис `ahawr-dashboard`, блок `environment`, после строки 295 (`DASHBOARD_GUARDS_TAB`) добавить `      - DASHBOARD_OPS_TAB=${DASHBOARD_OPS_TAB:-0}`. Без неё значение из `.env` в контейнер не попадёт: строки в `docker-compose.yml` сейчас нет (`grep -c OPS_TAB` = 0).
- **Шаг 3, NOT RUN. Флаг.** В `.env` поставить `DASHBOARD_OPS_TAB=1` (в `.env.example:207` значение пустое). Файл `.env` не читать и не копировать.
- **Шаг 4, NOT RUN. Перезапуск только дашборда** из `/d/n8n`: `docker compose up -d ahawr-dashboard`. После этого вкладка видна, но статус очереди показывает «stopped» до исправления R2.
- **Шаг 5, NOT RUN. Запуск агента, один раз, отдельно** (`claude-runner/docs/board-ops-agent.md:436`): `powershell.exe -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\board_agent.py'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"`. Не использовать `BOARD_AGENT_DRY=1` на живой базе (R1).
- **Шаг 6, NOT RUN. Проверка heartbeat:** `SELECT max(heartbeat_at) FROM board.agent;` Значение должно быть моложе 30 с.

## 9. Откат

- **NOT RUN. Остановить агента.** Мягко: создать файл `D:\n8n\data-tables\ops\board_agent.stop` (`board_agent.py:628`), агент выйдет на следующем такте. Затем файл удалить, иначе следующий запуск выйдет сразу. Жёстко: команда `taskkill` по `[b]oard_agent\.py` из `board-ops-agent.md:475`. Строка `running`, если была, снимется дашбордом через 600 с (R3).
- **NOT RUN. Выключить флаг:** `DASHBOARD_OPS_TAB=0` или удалить строку в `.env`, затем `docker compose up -d ahawr-dashboard`. Страница возвращается к исходной (`dashboard.py:188-191`, тест `test_ops_tab_off_leaves_the_page_unchanged`).
- **Таблицы не удалять.** Строки `board.commands` остаются, таблицы идемпотентны. Удаление таблиц не рекомендуется и не автоматизируется.

## 10. Предложение записи в CHANGELOG (не записано)

Текущая версия: `claude-runner/pyproject.toml:7` и `claude-runner/src/claude_runner/api.py:254` содержат `0.10.100`. Предлагается поднять средний номер до `0.11.0`, так как добавлены новые функции. Запись в `CHANGELOG.md` писать на английском, как остальные. Текст для вставки:

```
## 2026-10-26 (0.11.0)

Operations control: the host agent `data-tables/ops/board_agent.py` (outside git) runs the confirmed
commands of the Control tab; the Control tab sits behind `DASHBOARD_OPS_TAB`, OFF by default.
New functions, so the middle number goes up. Not run in production; the operator steps are in
`reports/ahawr-ops-control-ui-report-ru.md`.
```

Версию поднимать только после исправления R1–R3 и только в `pyproject.toml` и `api.py` одновременно.

## 11. Что не проверено

- Отрисовка и нажатия вкладки в браузере.
- Работа агента на живой базе и на реальной очереди.
- mypy по изменённым файлам.
- Полный набор тестов.
- Серверная проверка границы (`requires_boundary` в `board_ops_api.py` не найден; как сервер проверяет границу, в этой задаче не выяснялось).
- Список задач T001–T018 с номерами (раздел 1).
