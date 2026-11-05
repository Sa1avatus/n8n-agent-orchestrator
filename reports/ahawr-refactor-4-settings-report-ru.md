# Отчёт миссии ahawr-refactor-4 — блок «настройки» (T015)

Дата: 2026-11-04, попытка 1/3. Источники: `reports/ahawr-refactor-4-candidates.json` (108 записей) и
`git diff` по изменённым файлам. сверка всех ссылок `фил:строка` выполнена по рабочему дереву (grep).

## Итог

| Шаг | Записей в worklist | Выполнено | Ложные срабатывания | Исключено |
|---|---|---|---|---|
| S1 qa_stack.py | 10 | 10 (константы) | 3 (`none`) | — |
| S2 config.py | 9 | 4 (3 настройки + 1 константа) | 3 | 2 (codex) |
| S3 board_ops_store*/store_pg | 16 | 16 | — | — |
| S4 dashboard.py | 8 | 8 | — | — |
| Всего в worklist | 108 | 38 правок в 10 файлах (954 вставок, 92 удаления) | 24 всего | 3 `none` + 2 codex |
| Следующая миссия | 62 | — | — | — |

Все правки — только в существующих файлах (`qa_stack.py`, `config.py`, `board_ops_store.py`,
`board_ops_store_pg.py`, `dashboard.py`, `.env.example`, четыре теста). Новый функционал не добавлен:
значения прежних литералов сохранены как значения констант/настроек по умолчанию, поведение не изменилось.

---

## Шаг S1 — `claude-runner/src/claude_runner/qa_stack.py` (10 записей, класс `constant`)

Две константы объявлены один раз:
- `qa_stack.py:29` — `QA_LOOPBACK_HOST = "127.0.0.1"` (адрес loopback, единица — host);
- `qa_stack.py:30` — `QA_CONTAINER_BIND_HOST = "0.0.0.0"` (bind внутри контейнера донора).

| audit_id | место (новая строка) | новое имя | значение по умолчанию |
|---|---|---|---|
| a076 | qa_stack.py:226 | `QA_LOOPBACK_HOST` | `127.0.0.1` (publish-маппинг Postgres `-p host:port:5432`) |
| a077 | qa_stack.py:238 | `QA_LOOPBACK_HOST` | `127.0.0.1` (цель `pg_isready -h`) |
| a078 | qa_stack.py:251 | `QA_LOOPBACK_HOST` | `127.0.0.1` (publish-маппинг донора-дашборда) |
| a079 | qa_stack.py:255 | `QA_CONTAINER_BIND_HOST` | `0.0.0.0` (`--host` внутри контейнера) |
| a080 | qa_stack.py:261 | `QA_LOOPBACK_HOST` | `127.0.0.1` (dashboard_url) |
| a081 | qa_stack.py:279 | `QA_LOOPBACK_HOST` | `127.0.0.1` (dashboard_url, in-process) |
| a082 | qa_stack.py:301 | `QA_LOOPBACK_HOST` | `127.0.0.1` (dashboard_url, postgres-режим) |
| a083 | qa_stack.py:353 | `QA_LOOPBACK_HOST` | `127.0.0.1` (spawn `--host` дашборда) |
| a085 | qa_stack.py:523 | `QA_LOOPBACK_HOST` | `127.0.0.1` (URL healthz-пробы) |
| a088 | qa_stack.py:569 | `QA_LOOPBACK_HOST` | `127.0.0.1` (`sock.bind` в `_free_port`) |

Тесты, закрепляющие каждое значение (`claude-runner/tests/test_qa_stack.py`, функции с номера 462):
- `test_qa_stack_postgres_publish_map_is_loopback_by_default` (462): `assert publish == "127.0.0.1:45000:5432"` (473);
- `test_qa_stack_pg_isready_probe_targets_loopback_by_default` (476): `assert probe[probe.index("-h") + 1] == "127.0.0.1"` (485);
- `test_qa_stack_dashboard_publish_map_is_loopback_by_default` (488) — publish-маппинг донора loopback (497);
- `test_qa_stack_dashboard_container_binds_on_all_interfaces_by_default` (500) — `--host 0.0.0.0` внутри контейнера;
- `test_qa_stack_dashboard_url_is_loopback_by_default` (512), `test_qa_stack_inprocess_dashboard_url_is_loopback_by_default` (524),
`test_qa_stack_postgres_mode_dashboard_url_is_loopback_by_default` (536): `assert dashboard_url == "http://127.0.0.1:..."` (521/533/555);
- `test_qa_stack_spawn_dashboard_host_arg_is_loopback_by_default` (558): `assert cmd[cmd.index("--host") + 1] == "127.0.0.1"` (572);
- `test_qa_stack_health_probe_url_is_loopback_by_default` (586): healthz URL на `127.0.0.1` (597);
- `test_qa_stack_free_port_binds_to_loopback_by_default` (600): `assert bound == [("127.0.0.1", 0)]` (631).

Откат шага S1: hunks `@@ -25,6 +25,9 @@` (объявление констант, qa_stack.py:29-30) и hunks замен
`@@ -220,7 +223,7 @@`, `@@ -231,7 +234,7 @@`, `@@ -244,10 +248,10 @@` (в нём строки 251 и 255),
`@@ -272,7 +276,7 @@`, `@@ -294,7 +298,7 @@`, `@@ -346,7 +350,7 @@`, `@@ -516,7 +520,7 @@`,
`@@ -562,5 +566,5 @@`; в `tests/test_qa_stack.py` — hunks `@@ -3,6 +3,7 @@` (import констант) и
`@@ -436,3 +437,196 @@` (10 новых тестов).

---

## Шаг S2 — `claude-runner/src/claude_runner/config.py` (9 записей)

| audit_id | место (новая строка) | новое имя | единица | default |
|---|---|---|---|---|
| a043 | config.py:220 `DEFAULT_COMPACT_TIMEOUT_SECONDS = 600` | настройка `compact_timeout_seconds` | seconds | 600 |
| a046 | config.py:221 `DEFAULT_WATCHDOG_INTERVAL_SECONDS = 60` | настройка `watchdog_interval_seconds` | seconds | 60 |
| a045 | config.py:222 `DEFAULT_THINK_PROXY_URL = "http://127.0.0.1:8700"` | настройка `think_proxy_url` | URL | loopback:8700 |
| a048 | config.py:21 `GENERIC_PROFILE_NAME = "generic"` | константа имени роли | имя роли | `generic` |

Использование: `Settings.compact_timeout_seconds` (config.py:667) и `from_env` (config.py:817);
`Settings.think_proxy_url` (678, from_env 828); `Settings.watchdog_interval_seconds` (695);
`GENERIC_PROFILE_NAME` в `ROLES` (config.py:22), в `DEFAULT_PROFILES` (config.py:544) и в
fallback `Settings.profile` (config.py:897).

Env-имена: для compact timeout `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS` уже был в `.env.example`
(строка 131) — новых имён не требуется; новые — `CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS`
(`.env.example:133`) и `CLAUDE_RUNNER_THINK_PROXY_URL` (`.env.example:135`), оба закомментированы
(значения берутся из defaults).

Тесты (`claude-runner/tests/test_config.py`):
- `test_compact_timeout_seconds_defaults_to_six_hundred` (230): `assert Settings.from_env({}).compact_timeout_seconds == 600` (232);
- `test_compact_timeout_seconds_below_one_is_rejected` (235) — проверка, что floor `max(1, ...)` не становится настройкой;
- `test_watchdog_interval_seconds_defaults_to_sixty` (241): `... watchdog_interval_seconds == 60` (243);
- `test_watchdog_interval_seconds_below_one_is_rejected` (246);
- `test_think_proxy_url_defaults_to_loopback_8700` (252): `... think_proxy_url == "http://127.0.0.1:8700"` (254);
- `test_generic_profile_is_the_fallback_for_unknown_roles` (268): `assert "generic" in ROLES` и
`s.profile("no-such-role") == s.profiles["generic"]`.

Откат шага S2: hunks `@@ -15,7 +15,11 @@` (GENERIC_PROFILE_NAME), `@@ -483,7 +541,7 @@`
(DEFAULT_PROFILES), `@@ -202,6 +214,13 @@` (три DEFAULT_*), `@@ -566,7 +574,7 @@`, `@@ -577,7 +585,7 @@`,
`@@ -594,7 +602,7 @@` (defaults в Settings), `@@ -715,7 +813,9 @@`, `@@ -724,13 +824,17 @@`
(from_env), `@@ -790,7 +894,7 @@` (fallback профиля); `.env.example` hunk `@@ -129,6 +129,10 @@`
(строки 133, 135); `tests/test_config.py` hunks `@@ -1,31 +1,70 @@` и `@@ -182,3 +214,98 @@` (тесты 230-272).

---

## Шаг S3 — `board_ops_store.py` и `board_ops_store_pg.py` (16 записей, класс `setting`)

Единый источник — config.py:598-608, все четыре значения читаются через `_int(dict(os.environ), …, 1)`
(минимум 1, единица — строки/rows):
- `OPS_COMMANDS_DEFAULT_ROWS` (config.py:598-599) = 200, env `CLAUDE_RUNNER_OPS_COMMANDS_DEFAULT_ROWS`;
- `OPS_CHANGELOG_DEFAULT_ROWS` (config.py:601-602) = 200, env `CLAUDE_RUNNER_CHANGELOG_DEFAULT_ROWS`
(имя без `OPS_` — так было названо в `.env.example:255` и config.py:602; несоответствиеprefix'а намеренно сохранено);
- `OPS_QUEUE_LOG_MAX_ROWS` (config.py:604-605) = 1000, env `CLAUDE_RUNNER_OPS_QUEUE_LOG_MAX_ROWS`;
- `OPS_SNAPSHOT_MAX_ROWS` (config.py:607-608) = 100, env `CLAUDE_RUNNER_OPS_SNAPSHOT_MAX_ROWS`.

| audit_id | файл:строка | имя | default | метод |
|---|---|---|---|---|
| a010 | board_ops_store.py:134 | `OPS_COMMANDS_DEFAULT_ROWS` | 200 | `list_commands` (OpsStore) |
| a011 | board_ops_store.py:170 | `OPS_CHANGELOG_DEFAULT_ROWS` | 200 | `changelog` |
| a012 | board_ops_store.py:175 | `OPS_QUEUE_LOG_MAX_ROWS` | 1000 | `get_queue_log` |
| a013 | board_ops_store.py:180 | `OPS_SNAPSHOT_MAX_ROWS` | 100 | `get_snapshot` |
| a014 | board_ops_store.py:367 | `OPS_COMMANDS_DEFAULT_ROWS` | 200 | `list_commands` (InMemory) |
| a015 | board_ops_store.py:444 | `OPS_CHANGELOG_DEFAULT_ROWS` | 200 | `changelog` (Unavailable) |
| a016 | board_ops_store.py:451 | `OPS_QUEUE_LOG_MAX_ROWS` | 1000 | `get_queue_log` (Unavailable) |
| a017 | board_ops_store.py:455 | `OPS_SNAPSHOT_MAX_ROWS` | 100 | `get_snapshot` (Unavailable) |
| a018 | board_ops_store_pg.py:117 | `OPS_COMMANDS_DEFAULT_ROWS` | 200 | `list_commands` (Pg) |
| a019 | board_ops_store_pg.py:134 | `OPS_QUEUE_LOG_MAX_ROWS` | 1000 | `get_queue_log` (Pg) |
| a020 | board_ops_store_pg.py:138 | `OPS_SNAPSHOT_MAX_ROWS` | 100 | `get_snapshot` (Pg) |
| a021 | board_ops_store_pg.py:183 | `OPS_CHANGELOG_DEFAULT_ROWS` | 200 | `changelog` (Pg) |
| a022 | board_ops_store_pg.py:436 | `OPS_COMMANDS_DEFAULT_ROWS` | 200 | pg-вариант `list_commands` |
| a023 | board_ops_store_pg.py:586 | `OPS_CHANGELOG_DEFAULT_ROWS` | 200 | pg `changelog` |
| a024 | board_ops_store_pg.py:608 | `OPS_QUEUE_LOG_MAX_ROWS` | 1000 | pg `get_queue_log` |
| a025 | board_ops_store_pg.py:618 | `OPS_SNAPSHOT_MAX_ROWS` | 100 | pg `get_snapshot` |

(Импортные hunks: board_ops_store.py:33-36, board_ops_store_pg.py:38-41.)

Тесты (`claude-runner/tests/test_board_ops_api.py`, с 485):
- `test_ops_store_list_commands_default_page_limit_is_200_rows` (485): `assert len(rows) == 200`;
- `test_ops_store_changelog_default_rows_is_200_newest_first` (497): `assert len(entries) == 200`;
- `test_ops_store_queue_log_default_tail_is_1000_rows` (508): `assert len(tail) == 1000`;
- `test_ops_store_snapshot_default_tail_is_100_rows` (520): `assert len(rows) == 100`;
- `test_unavailable_ops_store_read_defaults_keep_the_shared_caps` (531): `assert defaults == {200, 200, 1000, 100}`;
- `test_pg_ops_list_commands_default_limit_is_200_rows` (546), `test_pg_ops_changelog_default_limit_is_200_rows` (553),
`test_pg_ops_queue_log_default_limit_is_1000_rows` (560), `test_pg_ops_snapshot_default_limit_is_100_rows` (567).

Также (`claude-runner/tests/test_config.py:275`):
`test_ops_store_row_caps_default_to_the_rows_they_used_to_be`:
`assert config.OPS_COMMANDS_DEFAULT_ROWS == 200`, `... OPS_CHANGELOG_DEFAULT_ROWS == 200`,
`... OPS_QUEUE_LOG_MAX_ROWS == 1000`, `... OPS_SNAPSHOT_MAX_ROWS == 100`; и
`test_ops_store_row_caps_env_override_is_read_at_import` (283): `assert reloaded.OPS_QUEUE_LOG_MAX_ROWS == 2500`.

Откат шага S3: hunks `@@ -29,6 +29,12 @@` (import) в board_ops_store.py, `@@ -125,7 +131,7 @@`,
`@@ -161,17 +167,17 @@`, `@@ -358,7 +364,7 @@`, `@@ -435,17 +441,17 @@`; в board_ops_store_pg.py
`@@ -26,6 +26,7 @@`, `@@ -33,6 +34,12 @@`, `@@ -107,7 +114,7 @@`, `@@ -124,11 +131,11 @@`,
`@@ -173,7 +180,7 @@`, `@@ -426,7 +433,7 @@`, `@@ -576,7 +583,7 @@`, `@@ -598,7 +605,7 @@`,
`@@ -608,7 +615,7 @@`, `@@ -620,15 +627,6 @@`; в config.py часть блока `@@ -533,6 +591,46 @@`
(строки 596-608); `.env.example` часть hunks `@@ -244,6 +250,18 @@` (строки 254-257);
`tests/test_board_ops_api.py` hunks `@@ -6,15 +6,28 @@` и `@@ -350,3 +363,209 @@`.

---

## Шаг S4 — `claude-runner/src/claude_runner/dashboard.py` (8 записей, класс `setting`)

Источник — config.py:613-630 (блок `DASHBOARD_*`), импорт dashboard.py:44-50:
- `DASHBOARD_PORT` (config.py:616-617) = 8701, env `CLAUDE_RUNNER_DASHBOARD_PORT`, единица — порт;
- `DASHBOARD_BIND_HOST` (config.py:619) = `0.0.0.0`, env НЕ введён: комментарий config.py:613-615
объясняет, что `AHAWR_DASHBOARD_BIND` (dashboard_auth_config.py:41) задывает публикуемый адрес,
а не bind внутри контейнера — новый env вводить нельзя (пересечение с auth-настройкой);
- `RUNS_LIST_DEFAULT_ROWS` (config.py:620-621) = 100, env `CLAUDE_RUNNER_RUNS_LIST_DEFAULT_ROWS`;
- `RUNS_LIST_MAX_ROWS` (config.py:623-624) = 500, env `CLAUDE_RUNNER_RUNS_LIST_MAX_ROWS`;
- `SEARCH_DEFAULT_ROWS` (config.py:626-627) = 50, env `CLAUDE_RUNNER_SEARCH_DEFAULT_ROWS`;
- `SEARCH_MAX_ROWS` (config.py:629-630) = 200, env `CLAUDE_RUNNER_SEARCH_MAX_ROWS`;
- `LOOPBACK_BIND_HOST = "127.0.0.1"` — локальная константа fallback, dashboard.py:83.

| audit_id | место (новая строка) | имя | default |
|---|---|---|---|
| a049 | dashboard.py:438 | `RUNS_LIST_DEFAULT_ROWS` / `RUNS_LIST_MAX_ROWS` | 100 / 500 rows |
| a050 | dashboard.py:438 | `RUNS_LIST_MAX_ROWS` (le= в Query) | 500 rows |
| a051 | dashboard.py:480 | `RUNS_LIST_MAX_ROWS` (fetch до фильтра) | 500 rows |
| a052 | dashboard.py:528 | `SEARCH_DEFAULT_ROWS` / `SEARCH_MAX_ROWS` | 50 / 200 rows |
| a053 | dashboard.py:528 | `SEARCH_MAX_ROWS` (le=) | 200 rows |
| a054 | dashboard.py:1024 | `DASHBOARD_BIND_HOST` (`--host` argparse) | 0.0.0.0 |
| a055 | dashboard.py:1025 | `DASHBOARD_PORT` (`--port` argparse) | 8701 |
| a056 | dashboard.py:1039 | `LOOPBACK_BIND_HOST` (fallback bind) | 127.0.0.1 |

Тесты (`claude-runner/tests/test_dashboard.py`, с 1248):
- `test_runs_list_default_limit_is_100_rows` (1248): `assert len(body["runs"]) == 100`;
- `test_runs_list_limit_max_is_500_rows` (1258): `... params={"limit": 500}).status_code == 200` и
`params={"limit": 501}).status_code == 422`;
- `test_runs_filter_fetch_is_capped_at_500_rows_before_the_limit` (1266): `... params["limit"] == "500"`;
- `test_search_default_limit_is_50_rows` (1277): `assert len(body["runs"]) == 50`;
- `test_search_limit_max_is_200_rows` (1286): `limit=200 → 200`, `limit=201 → 422`;
- `test_dashboard_main_default_host_is_0_0_0_0` (1294): `assert seen["host"] == "0.0.0.0"`;
- `test_dashboard_main_default_port_is_8701` (1308): `assert seen["port"] == 8701`;
- `test_dashboard_main_bind_fallback_is_loopback_when_env_unset` (1322+): fallback `127.0.0.1`.

И (`tests/test_config.py:293`): `test_dashboard_defaults_match_the_literals_they_used_to_be` —
`config.DASHBOARD_PORT == 8701`, `config.DASHBOARD_BIND_HOST == "0.0.0.0"`,
`config.RUNS_LIST_DEFAULT_ROWS == 100`, `config.RUNS_LIST_MAX_ROWS == 500`,
`config.SEARCH_DEFAULT_ROWS == 50`, `config.SEARCH_MAX_ROWS == 200`; и
`test_dashboard_row_caps_env_override_is_read_at_import` (304): `assert reloaded.SEARCH_MAX_ROWS == 300`.

Откат шага S4: hunks dashboard.py `@@ -41,12 +41,26 @@` (import), `@@ -65,6 +79,8 @@`
(`LOOPBACK_BIND_HOST`, строка 83), `@@ -414,7 +435,7 @@`, `@@ -456,7 +477,7 @@`, `@@ -504,7 +525,7 @@`,
`@@ -965,8 +1021,8 @@`, `@@ -980,7 +1036,7 @@`; config.py часть блока `@@ -533,6 +591,46 @@`
(строки 613-630); `.env.example` часть hunks `@@ -244,6 +250,18 @@` (строки 260-264);
`tests/test_dashboard.py` hunk `@@ -1219,3 +1219,127 @@`.

---

## Новые имена в `.env.example` (все закомментированы — defaults сохраняют прежнее поведение)

- `.env.example:133` `CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS`
- `.env.example:135` `CLAUDE_RUNNER_THINK_PROXY_URL`
- `.env.example:254` `CLAUDE_RUNNER_OPS_COMMANDS_DEFAULT_ROWS`
- `.env.example:255` `CLAUDE_RUNNER_CHANGELOG_DEFAULT_ROWS`
- `.env.example:256` `CLAUDE_RUNNER_OPS_QUEUE_LOG_MAX_ROWS`
- `.env.example:257` `CLAUDE_RUNNER_OPS_SNAPSHOT_MAX_ROWS`
- `.env.example:260` `CLAUDE_RUNNER_DASHBOARD_PORT`
- `.env.example:261` `CLAUDE_RUNNER_RUNS_LIST_DEFAULT_ROWS`
- `.env.example:262` `CLAUDE_RUNNER_RUNS_LIST_MAX_ROWS`
- `.env.example:263` `CLAUDE_RUNNER_SEARCH_DEFAULT_ROWS`
- `.env.example:264` `CLAUDE_RUNNER_SEARCH_MAX_ROWS`

---

## Ложные срабатывания (24 записи, изменять нельзя)

Внутри шагов (3, шаг `none`, файл `qa_stack.py`): a084, a086, a087 — артефакт regex-а аудита
(«timeout»+цифра в `returncode == 0` / `args[0]`); настоящие QA-таймауты уже названы
`DOCKER_TIMEOUT_SECONDS` и `HEALTH_REQUEST_SECONDS` — дублировать нельзя.

S2 (3): a042, a044, a047 — `max(1, ...)` и минимальный аргумент `_int(..., 1)` — это floor-валидации,
не конфигурируемые значения.

Перенесённые в «следующую миссию» (18): a096, a098, a099, a100 — перевод MiB в байты
(единица выводится из другой величины); a103 — `* 1000` для миллисекундной метки времени;
a037 — sentinel `timeout_seconds = 0` («нет таймаута»); a038 — код HTTP 408; a062 — clamp
`max(..., 0.0)`; a065-a067 — деление на 1000 (ms → s); a034 — `round(..., 1)` для отображения;
a068, a069 — `/1_000_000` в ценообразовании (pricing); a073 — текст сообщения `ValueError`;
a059 — сравнение `delay > 0.0`; a071 — `/60` перевод секунд в минуты; a075 — текст
`HostRefusedError`.

## Что не тронуто, кроме ложных срабатываний

- Шаг `none`: a084, a086, a087 (см. выше, regex-артефакты; `test_file` в worklist —
  `test_qa_stack.py`, правок не требовалось).
- Codex-исключения по правилу миссии: a040, a041 (config.py:371 — проверка схемы для codex;
  в worklist помечены как `excluded`/codex, менять нельзя).
- 62 записей — блок «следующая миссия» (см. ниже); они сознательно не редактируются в T015.

## Перенесено в следующую миссию (62 записей, не изменялись)

- `runs.py`: a101 `kill_check_interval_seconds` (0.05 s), a094/a095 `PROMPT_JOIN_CHARS` (4 chars),
  a097 `subprocess_pipe_limit_bytes` (64 KiB).
- `board_pg.py`: a026 `hermes_profile_name` ("claude-code"), a027/a028 `changelog_default_rows` (200),
  a029/a030 `DSN_REDACTION_HOST_TOKENS`.
- `sources.py`: a102 `cancel_request_timeout_seconds` (60 s), a104 `probe_message_rows` (5 rows),
  a105 `session_list_max_rows` (200 rows).
- Overflow (файлы вне четырёх шагов T015): `board_ops_api.py` a005-a007, `board_ready.py`
  a031-a033 `ready_check_timeout_seconds` (5.0 s), `claude_cli.py` a036 `search_tool_timeout_seconds`
  (10 s), `guards.py` a063 `TELEGRAM_API_URL_PREFIX`, a064 `telegram_request_timeout_seconds` (30 s),
  `hermes_run.py` a065-a067 (ms→s, FP), `board.py` a001-a002, `board_api.py` a003-a004,
  `board_think.py` a035 `MIN_SAMPLE_DRAWS` (5), `dashboard_auth_config.py` a057-a058,
  `remote_manager.py` a089-a090, `run_views.py` a092-a093, `think_switch.py` a107-a108
  `transcript_tail_bytes` (262144 bytes), `board_ops_guard.py` a008, `board_ops_livestate.py` a009,
  `export.py` a060 `FENCE_BASE_CHARS` (2; `test_file` — none, тестов нет), `guard_queue.py` a061,
  `mem_guard.py` a070, `qa_agent_prompt.py` a074, `roles_config.py` a091, `store.py` a106,
  плюс FP a037, a038, a059, a062, a065-a067, a068-a069, a071, a073, a075, a096, a098-a100, a103.

---

## Проверки (что реально выполнялось)

Команды запускались из `claude-runner/` (кроме `git diff --check` из корня `/d/n8n`):

- `python -m pytest tests/test_qa_stack.py -q --no-cov` → exit 0, `58 passed` (было 48 passed).
- `python -m pytest tests/test_config.py -q --no-cov` → exit 0, `40 passed` (было 29).
- `python -m pytest tests/test_board_ops_api.py -q --no-cov` → exit 0, `32 passed` (было 23).
- `python -m pytest tests/test_dashboard.py -q --no-cov` → exit 1, `56 passed, 1 failed`;
  failure — `test_board_static_whitelist_pattern` (pre-existing pin устаревшей markup-разметки:
  `tests/test_dashboard.py:1132` против `dashboard.html:1428`/`:1512`); он существовал и до правок
  (baseline_before в worklist фиксирует тот же провал).
- `python -m ruff check src/claude_runner/qa_stack.py src/claude_runner/config.py
  src/claude_runner/board_ops_store.py src/claude_runner/board_ops_store_pg.py src/claude_runner/dashboard.py`
  → qa_stack/config/dashboard: `All checks passed!`; board_ops_store: `Found 3 errors.` (без изменений);
  board_ops_store_pg: `All checks passed!`.
- `python -m mypy src/claude_runner/qa_stack.py src/claude_runner/config.py` → `Success`;
  `mypy src/claude_runner/board_ops_store.py` → 0 errors в самом файле (34 errors в импортируемом
  `board_pg.py`); `mypy src/claude_runner/board_ops_store_pg.py` → 6 errors, строки 286, 291, 312,
  467, 521, 583; `mypy src/claude_runner/dashboard.py` → 1 error, строка 452.
- `git diff --check` (корень) → exit 0 (только предупреждения LF/CRLF, никаких trailing whitespace).

Не запускались: полный pytest src (не нужен для четырёх шагов), `node --test tests/*.test.js`
(JS-модули в этих шагах не менялись), `cProfile`/`py-spy` (оптимизации не делались).

## ruff/mypy до и после (сверка с `reports/review-baseline.json`)

| Файл | baseline (review-baseline.json) | после |
|---|---|---|
| `board_ops_store.py` ruff | 3 (F401, UP035, E501) | 3 — без изменений |
| `board_ops_store_pg.py` mypy | 7 | 6 в узком проходе (286, 291, 312, 467, 521, 583) |
| `qa_stack.py` ruff/mypy | 0 / 0 | 0 / 0 |
| `config.py` ruff/mypy | 0 / 0 | 0 / 0 |
| `dashboard.py` mypy | 1 (line 431) | 1 (line 452) — та же ошибка, сдвинулась из-за добавленного блока импорта |
| combined mypy 5 src-файлов | 128 (всего по src) | `Found 83 errors in 12 files (checked 5 source files)` — ни одного нового error в файлах шагов |
| combined ruff по src | 98 | в touched-файлах новых error нет |

`tests/test_board_ops_api.py` ruff даёт 2 E501 (`:70`, `:74`) — обе строки добавлены НЕ нами
(это строки HEAD, проверено diff-строками: в новых строках тестов этих литералов нет).

## Открытые риски

1. Провал `test_board_static_whitelist_pattern` — существующий дрейф markup-пин'а; к нашим правкам
   не относится, но чинить его в T015 нельзя (файл `dashboard.html` вне миссии).
2. Имя env `CLAUDE_RUNNER_CHANGELOG_DEFAULT_ROWS` без `OPS_`-prefix'а — сохранено как было в
   `.env.example`/config.py; при следующем аудите может быть названо иначе (риск несоответствия).
3. `DASHBOARD_BIND_HOST` намеренно без env-переменной: если оператор захотит менять bind из env,
   понадобится отдельное решение (пересечение с `AHAWR_DASHBOARD_BIND`).
</content>
