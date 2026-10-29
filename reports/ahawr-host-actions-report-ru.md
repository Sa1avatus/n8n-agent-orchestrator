# Миссия ahawr-host-actions: отчёт

Статус: код и тесты готовы, исполнитель не запускался, активация НЕ ВЫПОЛНЕНА. Запрос на перезапуск контейнера
проходит очередь, но с текущим кодом ни одно действие не дойдёт до `done`, пока не закрыт риск R1 (раздел 8).

## 1. Что построено

| Часть | Файл:строка | Что там |
|---|---|---|
| Модель запроса, переходы, белый список, решение | `claude-runner/src/claude_runner/host_actions.py:38` | `HOST_TRANSITIONS` (таблица переходов) |
| | `host_actions.py:79` | `host_actions_enabled`: флаг `HOST_ACTIONS`, выключен по умолчанию |
| | `host_actions.py:103` | `DEFAULT_WHITELIST_DATA`: действия и сервисы с уровнями |
| | `host_actions.py:132` | `parse_whitelist`: проверка белого списка с понятными ошибками |
| | `host_actions.py:218` | `decide`: проверки перед claim (активные запуски, граница задачи, интервал) |
| | `host_actions.py:259` | `HostActionStore`: файл `host_actions.json` с лимитами |
| Маршруты runner (порт 8700) | `claude-runner/src/claude_runner/api.py:180` | `_send_confirm_notice`: уведомление Telegram |
| | `api.py:275-280` | `authorize_operator`: отдельный ключ только для решения оператора |
| | `api.py:486-535` | маршруты `/v1/host-actions` (создание, список, чтение, claim, result, operator) |
| | `api.py:501` | вызов уведомления для уровня CONFIRM |
| Маршруты dashboard (порт 8701) | `claude-runner/src/claude_runner/dashboard.py:200` | заголовок `X-AHAWR-Dashboard` для `/api/host-actions/` |
| | `dashboard.py:288` | `GET /api/host-actions` (список, прокси к runner) |
| | `dashboard.py:303` | `POST /api/host-actions/{id}/confirm` |
| | `dashboard.py:319` | `POST /api/host-actions/{id}/reject` |
| Исполнитель (хост, вне git) | `data-tables/ops/host_actions_worker.py:38` | `DRY_RUN_ENV = "HOST_ACTIONS_DRY"` |
| | `host_actions_worker.py:94` | печать `DRY RUN, not executed: [argv]` |
| | `host_actions_worker.py:173-174` | `queue_at_boundary=False`, `queue_paused=False` (см. R1) |
| Вид панели | `claude-runner/src/claude_runner/board_guards_view.js:127` | `hostActionsModel`: кнопки Confirm и Reject |
| Тексты интерфейса | `claude-runner/src/claude_runner/board_i18n_en.json:49` | ключи `guards.host.*` (title, confirm, reject, ask.confirm, ask.reject, failed) |
| | `board_i18n_ru.json` | те же ключи `guards.host.*` на русском |
| | `board_i18n.js` | `BUNDLED_EN` (12 упоминаний host) |
| Конфигурация | `claude-runner/src/claude_runner/config.py:397` | поле `host_actions_operator_key` |
| | `config.py:504-505` | чтение `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` |
| Документ | `claude-runner/docs/host-actions.md` (511 строк) | проблема, уровни, жизненный цикл, маршруты, Telegram, активация, откат |
| Правила для агентов | `CLAUDE.md:67-70` | запрос через `POST /v1/host-actions`, без `docker` в задаче |
| Пример переменных | `.env.example:154-158` | `HOST_ACTIONS=` и `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY=` (пустые) |
| Тесты | `claude-runner/tests/test_host_actions*.py`, `test_dashboard_host_actions.py`, `test_host_actions_worker.py`, `board_host_actions_view.test.js` | 74 функции `def test_` в Python, 18 тестов node (два файла) |

Соответствие задач. Список задач T001-T023 в рабочем каталоге не найден: `data-tables/ops/_make_host_actions_mission.py`
содержит только текст миссии (части PART 1-5) и правила идентификаторов. Поэтому соответствие дано по частям миссии:
PART 1 (документ), PART 2 (runner, `host_actions.py`, `api.py`), PART 3 (исполнитель и его тест), PART 4 (панель и
dashboard), PART 5 (ограничение: без правки живой системы, compose не менялся, предложения только в тексте).

## 2. Жизненный цикл и таблица состояний

Путь: Worker `POST /v1/host-actions` -> `pending`. Для уровня AUTO runner сразу переводит в `confirmed`. Для CONFIRM
запрос ждёт оператора на 8701. Исполнитель делает claim (`running`), применяет команду из белого списка и отправляет
result (`done` или `failed`). Worker читает результат в следующем запуске через `GET /v1/host-actions/<id>`.

| Состояние | Кто переводит | Следующие состояния |
|---|---|---|
| `pending` | Worker (создание) | `confirmed`, `rejected`, `expired` |
| `confirmed` | runner (AUTO) или оператор на 8701 (CONFIRM) | `running`, `expired`, `rejected` |
| `running` | исполнитель через claim | `done`, `failed` |
| `done` | исполнитель, result с `ok: true` | нет (конечное) |
| `failed` | исполнитель, `ok: false`, или истечение `running` | нет (конечное) |
| `rejected` | оператор | нет (конечное) |
| `expired` | runner при чтении | нет (конечное) |

Недопустимые переходы отвечают 409 (`HOST_TRANSITIONS`, `host_actions.py:38`). В коде истекают только `pending`
(по `MAX_PENDING_HOURS = 1`) и `running` (по `CLAIM_TIMEOUT_SECONDS = 30 * 60`, `host_actions.py:186-200`). Истечение
`confirmed` в коде нет; в документе это записано как состояние таблицы (см. R3).

## 3. Сервисы, уровень и причина

| Сервис | Контейнер | Уровень | Причина (из `docs/host-actions.md`, раздел «Services and tiers») |
|---|---|---|---|
| ahawr-postgres | ahawr-postgres | NOT WHITELISTED | Хранит данные n8n и доски: перезапуск или пересоздание может их повредить |
| n8n | n8n-autonomous-agents | CONFIRM | Запускает рабочие процессы и очередь: перезапуск прерывает миссии |
| ahawr-retrieval | ahawr-retrieval | NOT WHITELISTED | Хранит индекс в томе `ahawr-retrieval-data` |
| claude-runner | claude-runner | CONFIRM | Запускает Worker, Reviewer, Architect: перезапуск прерывает активный запуск |
| ahawr-qa | ahawr-qa | AUTO | Побочный контейнер QA, своего состояния не хранит (только том `qa-runs`) |
| ahawr-dashboard | ahawr-dashboard | AUTO | Только чтение: перезапуск не влияет на запуски |
| litellm | ahawr-litellm | CONFIRM | Шлюз моделей к llama-серверу: перезапуск прерывает вызовы моделей |

Действия: `recreate` (`docker compose up -d --no-deps <service>`) и `restart` (`docker restart <container>`).

## 4. Telegram: уведомление, не команда

Решение: NOTICE ONLY. Подтверждение и отказ выполняются только на dashboard (8701, `127.0.0.1`).

Причины (из `docs/host-actions.md`, раздел «Telegram»):

- `guard_relay.py` не разбирает команды: он пересылает каждое обновление как есть (`_forward`, `guard_relay.py:233-242`).
  Текст `confirm <id>` попал бы в n8n, а не в логику relay.
- У relay нет проверки отправителя: `grep -c chat claude-runner/src/claude_runner/guard_relay.py` вернул `0`.
  Любой, кто может написать боту, проходит relay.
- Relay не хранит ключ dashboard. Для подтверждения через Telegram нужен `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY`,
  то есть новый секрет в n8n или relay и новый маршрут. Это вне задачи.

Уведомление уже подключено: `_send_confirm_notice` (`api.py:180`) вызывается при создании запроса уровня CONFIRM
(`api.py:501`). Текст: id, сервис, действие и причина, пропущенные через `scrub`.

## 5. Проверки, которые выполнены (повторно, в этой сессии)

Команды запускались из `claude-runner/` с `--no-cov`. Набор соответствует правилам `CLAUDE.md` (целевой pytest,
node, ruff, mypy, `git diff --check`). Список команд T022 в рабочем каталоге не найден, поэтому набор восстановлен по
правилам, а не скопирован из T022.

pytest, пять файлов host-actions (`test_host_actions.py`, `test_host_actions_api.py`, `test_host_actions_dashboard.py`,
`test_dashboard_host_actions.py`, `test_host_actions_worker.py`):

```
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
PYTEST_EXIT 0
```

Строка итога pytest не напечатана в этой среде, поэтому число тестов установлено по отметкам: 72 + 72 + 11 = 155,
код выхода 0.

node, два файла (`board_host_actions_view.test.js`, `board_control_view.test.js`):

```
# tests 18
# suites 0
# pass 18
# fail 0
# cancelled 0
# skipped 0
# todo 0
```

ruff, девять файлов (host_actions, api, dashboard, config и пять тестов):

```
All checks passed!
RUFF_EXIT 0
```

mypy, `host_actions.py`:

```
Success: no issues found in 1 source file
MYPY_EXIT 0
```

`git diff --check` (из корня):

```
warning: in the working copy of 'claude-runner/src/claude_runner/board_guards_view.js', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'claude-runner/src/claude_runner/board_ops_api.py', LF will be replaced by CRLF the next time Git touches it
DIFF_CHECK_EXIT 0
```

Предупреждения касаются CRLF в чужих незакоммиченных файлах; ошибок пробелов нет.

Не запускались: mypy по `api.py`, `dashboard.py`, `config.py` (в них есть старые ошибки, сравнение до и после не делалось);
полный набор тестов; тесты `test_board_agent.py` и `test_dashboard_ops_tab.py`, которые не относятся к этой части.

## 6. Что не запускалось и почему

- Docker: ни одна команда `docker` не выполнялась (правило: Worker не трогает контейнеры).
- Исполнитель `host_actions_worker.py`: не запускался ни в каком режиме, в том числе в `HOST_ACTIONS_DRY=1`.
- Dashboard: не запускался, панель в браузере не проверена. Проверка только тестами `.test.js` и Python.
- Живой runner: маршруты проверены тестами с тестовым клиентом, не на порту 8700.
- Активация (раздел 7): все шаги NOT RUN.

## 7. Активация оператором и откат

Все шаги помечены NOT RUN. Выполняет оператор, Worker ничего из этого не делает (`docs/host-actions.md`, раздел «Activation»).

NOT RUN, активация:

1. `HOST_ACTIONS=1` в `.env`. Пропуска этой переменной в compose нет (open item, требует одобрения).
2. Применить предложенный compose-diff для `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` (только после одобрения).
3. `docker compose up -d --no-deps claude-runner` и `... ahawr-dashboard`.
4. Запуск исполнителя в режиме DRY: `HOST_ACTIONS=1 HOST_ACTIONS_DRY=1 python data-tables/ops/host_actions_worker.py`.
5. Тестовый запрос `restart` для `ahawr-dashboard` (уровень AUTO) и чтение записи до `done`. С текущим кодом запись
   останется `confirmed` (см. R1).
6. Исполнитель без DRY; тот же запрос; запись до `done`.

NOT RUN, откат:

1. `HOST_ACTIONS` пустой в `.env`, пересоздать `claude-runner`.
2. Остановить исполнитель (Ctrl+C) и проверить, что `data-tables/ops/heartbeat/host_actions_worker.json` перестал меняться.
3. Файл `host_actions.json` не удаляется. Запросы в `pending`, `confirmed` не выполняются, пока флаг выключен.

## 8. Риски

- R1, блокер: claim всегда отказывает. `decide` (`host_actions.py:237`) отказывает, пока не выполнено
  `queue_at_boundary or queue_paused`. Исполнитель передаёт оба значения как `False` (`host_actions_worker.py:173-174`,
  с пометкой `shortcut`). Значит, с текущим кодом ни одно действие не дойдёт до `done`. Нужен маршрут или чтение
  состояния очереди. До этого активация не имеет смысла.
- R2: `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` не передаётся в compose. Пока её нет, `/operator` отвечает 401
  (`.env.example:156-157`), и подтверждение с dashboard не работает.
- R3: расхождение документа и кода. Документ называет `HOST_ACTION_CLAIM_MINUTES = 30` и `HOST_ACTION_RUN_MINUTES = 15`
  и пишет, что их нет в коде. В коде есть только `CLAIM_TIMEOUT_SECONDS = 30 * 60` (`host_actions.py:30`), и он
  применяется к `running` (`host_actions.py:196`). Срок 15 минут для запуска отсутствует. Истечение `confirmed` не
  реализовано.
- R4: `host_actions_worker` не добавлен в `HEARTBEAT_NAMES` (`guard_heartbeat.py:23`; сейчас только
  `docker_watchdog` и `gpu_power_governor`). Dashboard не покажет жизнь исполнителя.
- R5: исполнитель и `host_actions_worker.py` вне git и не проверяются CI. Тест импортирует его по пути.
- R6: у relay нет проверки отправителя (раздел 4). Опасно только при появлении команд в Telegram.
- R7: `docs/host-actions.md` в разделе Telegram указывает на `guard_relay.py:24-27` и `:79-88`. Эти номера не сверялись
  с файлом в этой задаче.
- R8: `CHANGELOG.md` и `pyproject.toml` не обновлены (версия `0.10.100`).

## 9. Предложение для CHANGELOG

Новая функция, поэтому средний номер растёт на 1: `0.10.100` -> `0.11.0`. Текст записи (английский, как в файле):

```
## 2026-10-25 (0.11.0)

Host actions: a Worker asks the runner to recreate or restart a service through a whitelisted request, the
operator confirms the CONFIRM tier on the dashboard, and a host executor applies the command. Behind the flag
HOST_ACTIONS, OFF by default; the executor and the activation are not run yet.

- New `host_actions.py` (request model, store, whitelist, `decide`), routes `/v1/host-actions` on the runner and
  `/api/host-actions` on the dashboard, the Host actions panel in the Guards tab.
- Design `docs/host-actions.md`; `CLAUDE.md` tells tasks to request a restart instead of running `docker`.
- Known gaps: the executor never gets a claim (`queue_at_boundary` and `queue_paused` are fixed to False);
  the executor heartbeat name is not registered; `confirmed` does not expire.
```

Версию в `pyproject.toml` и `api.py` менять только при выпуске; это решение оператора.

## 10. Итог

Код, тесты и документ созданы; проверки pytest, node, ruff, mypy и `git diff --check` прошли (раздел 5). Активация,
Docker и исполнитель не запускались. До активации нужно закрыть R1 и R2.
