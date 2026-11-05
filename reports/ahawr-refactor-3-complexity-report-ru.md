# Миссия `ahawr-refactor-3-complexity` — итоговый отчёт (T014, попытка 3)

Отчёт написан 2026-11-04 по рабочему дереву. Изменения в git не закоммичены (коммиты запрещены
правилами репозитория), поэтому «до» — это `HEAD`, «после» — рабочее дерево. Все числа получены
фактическим запуском проверок, а не из плановых файлов.

## 1. Что изменилось

### 1.1 Первая группа маршрутов вынесена из `_build_router` (шаги T002–T003)

| Что | Где сейчас |
|---|---|
| Новая функция `_register_status_and_missions_routes(router: APIRouter) -> None` | `claude-runner/src/claude_runner/board_api.py:765` |
| Восемь обработчиков на уровне модуля | `board_api.py:644` (`status`), `:652` (`list_missions`), `:660` (`get_mission`), `:676` (`mission_qa`), `:688` (`qa_screenshot`), `:697` (`create`), `:713` (`update_mission`), `:732` (`delete_mission`) |
| Регистрация маршрутов внутри новой функции | `board_api.py:774`, `board_api.py:777–783` |
| Вызов группы из `_build_router` | `board_api.py:796` (`_register_status_and_missions_routes(router)`) |

Диф по `board_api.py`: два hunk'а этого шага — `@@ -681,0 +644,142` (добавлены обработчики и
функция регистрации) и `@@ -691,124 +795,2` (из тела `_build_router` удалены 124 строки первой
группы, на их месте вызов новой функции). Тела обработчиков перенесены дословно; новый помощник не
писался — ошибки envelope берутся из `board_common.py`, а импорт — `board_api.py:56`
(модуль `board_common.py` создан миссией refactor-1, эту миссию он не создаёт).

### 1.2 Три цикла получили границу, задержку и выход (T011–T013 и два цикла сверх плана)

| Цикл | Граница (константа) | Петля | Задержка | Выход (лог) |
|---|---|---|---|---|
| `MemGuard.run` | `mem_guard.py:36` `MAX_GUARD_SCANS = 86_400` | `mem_guard.py:204` `for _ in range(MAX_GUARD_SCANS):` | `mem_guard.py:209` | `mem_guard.py:210` |
| `ThinkSwitch.run` | `think_switch.py:43` `RUN_MAX_TICKS = 4_320` | `think_switch.py:526` `for _ in range(RUN_MAX_TICKS):` | `think_switch.py:531` | `think_switch.py:532–534` |
| `RunManager._watchdog_loop` | `runs.py:100` `WATCHDOG_MAX_SCANS = 10_000  # scans` | `runs.py:895` `while scans < WATCHDOG_MAX_SCANS:` | `runs.py:901` | `runs.py:902–904` |

Якорь Т012 из плана подтверждён: в `HEAD` `while True` в mem_guard действительно на строке 202.
Якорь `think_switch.py:523` — в `HEAD` `while True` на 523. Якорь `runs.py:886` не подтверждается:
в `HEAD` `while True` в `_watchdog_loop` на строке 874 (строка 886 в `reports/audit-python.json` —
тот же `_watchdog_loop` в снимке файла, который аудитовали).

### 1.3 Тесты, фиксирующие поведение до изменения

- `claude-runner/tests/test_board_api.py:2036` — инвентарь `_BOARD_ROUTE_ORDER` (90 маршрутов),
  проверка порядка `:2130–2141` (`assert registered == _BOARD_ROUTE_ORDER`), класс
  `TestFirstRouteGroup` `:2144` (маппинг ошибок 409/404/400/503 для вынесенной группы).
- `claude-runner/tests/test_mem_guard.py:201` — `test_mem_guard_loop_stops_at_max_scans`.
- `claude-runner/tests/test_think_switch.py:979` — `test_run_stops_at_max_ticks`.
- `claude-runner/tests/test_runs.py:846` — `test_watchdog_loop_stops_at_max_scans`.

Итог дифа по файлам миссии: `8 files changed, 1106 insertions(+), 176 deletions(-)`
(`board_api.py` 308, `mem_guard.py` 8, `runs.py` 46, `think_switch.py` 10; тесты
`test_board_api.py` 329, `test_mem_guard.py` 109, `test_runs.py` 318, `test_think_switch.py` 154).

## 2. Какие проверки выполнены

Все pytest-запуски — из `claude-runner/` с `--no-cov` (иначе не проходит гейд покрытия).

Инвентарь маршрутов и тест первой группы (`tests/test_board_api.py -k "route_order or TestFirstRouteGroup"`):

```
..............                                                           [100%]
```

14 точек — 14 выбранных тестов, 138 deselected (полная выборка файла: 152).

Порядок регистрации HEAD против рабочего дерева (рег `router.(get|post|put|delete|patch)("` по всему файлу):

```
head: 90 current: 90 identical: True
```

Проверка живого роутера (построенный `_build_router()`):

```
routes: 90 unique: 90
```

Граничащие тесты трёх циклов (`tests/test_mem_guard.py tests/test_think_switch.py tests/test_runs.py -k "max_scans or max_ticks"`):

```
...                                                                      [100%]
```

То есть `test_mem_guard_loop_stops_at_max_scans`, `test_run_stops_at_max_ticks` и
`test_watchdog_loop_stops_at_max_scans` прошли (3 теста).

C901 и длины/вложенность — меряются модулем самого репозитория, `qa_complexity.py`
(`function_length_lines`, `function_nesting_depth`, `check_c901(..., max_complexity=1)`), четыре
файла миссии, HEAD против рабочего дерева:

```
HEAD verdict FAIL violations 128
current verdict FAIL violations 127
{'head_board_api.py': 77, 'head_mem_guard.py': 7, 'head_runs.py': 30, 'head_think_switch.py': 14} total 128
{'board_api.py': 76, 'mem_guard.py': 7, 'runs.py': 30, 'think_switch.py': 14} total 127
```

ruff по всему `src` (сравнение с `reports/review-baseline.json` от 2026-11-03,
`ruff_total 98`):

```
Found 98 errors.
```

mypy по всему `src`:

```
Found 126 errors in 18 files (checked 101 source files)
```

baseline `mypy_total 128` → сейчас 126 (−2). По тронутым файлам: `board_api.py` 12 (baseline 12),
`runs.py` 1 (baseline 1), `mem_guard.py` 0, `think_switch.py` 0, `board_common.py` 0.

`git diff --check`: код 0, предупреждения только о CRLF (`warning: in the working copy of ...
LF will be replaced by CRLF`), реальных ошибок trail-whitespace нет.

**Не запускались:** `node --test` (JS в этой миссии не менялся), полный pytest-сейт, и сравнение
списка падений pytest на `HEAD` с списком падений в рабочем дереве (намеренно: baseline-файл
`review-baseline.json` содержит только `pytest_collected 2991`, а не список падений).

Падения, которые я увидел при полных запусках файлов и которые **не** относятся к этому шагу
(проверяются другие ветки — store-fallback из других миссий):

```
tests/test_board_api.py: 3 failed, 149 passed in 36.48s
tests/test_runs.py: 1 failed, 42 passed in 31.05s
```

Это `TestUnavailableStore` (status/missions 503) и `TestErrorMapping` (stats 503) с
`assert 200 == 503` (`tests/test_board_api.py:69`), плюс `test_unknown_run_and_restart_recovery`
(`assert resumed["session_created"] is False`). Тесты порядка маршрутов и тесты границ при этом
проходят, тела перенесённых обработчиков не изменены, поэтому к вынесению группы эти падения
отношения не имеют. Я не проверял, были ли эти три падения на `HEAD` — этого запуска не делал.

## 3. Новые числа `_build_router` и вынесенной функции

Методика — та же, что у QA-гейда: `function_length_lines`, `function_nesting_depth` и
`check_c901` из `claude-runner/src/claude_runner/qa_complexity.py`.

| Функция | length_lines HEAD | length_lines сейчас | nesting_depth | C901 |
|---|---|---|---|---|
| `_build_router` | 1201 | 1079 (−122) | 4 → 4 | 181 → 161 (−20) |
| `_register_status_and_missions_routes` | — (её не было) | 19 | 0 | не выдаётся (C901 ≤ порога) |
| 8 перенесённых обработчиков | внутри `_build_router` | на уровне модуля | суммарно −20 в `_build_router` | их C901 остался прежним |

Сравнение с `reports/audit-python.json` (rank 1, `_build_router`): аудит фиксирует
181/1201/4 — после изменения 161/1079/4, то есть длина и сложность уменьшились, глубина
вложенности осталась 4 (ветвления внутри оставшихся групп).

## 4. Что не тронуто и почему

### 4.1 Вторая группа маршрутов (T005–T007) — не выполнена

Единственная `_register_*_routes` во всём `src` — `board_api.py:765`. Следовательно, второй
группы (10 маршрутов) как отдельной функции нет. Остальные 82 маршрута остаются inline внутри
`_build_router` (`:786–1864`):

| Группа | Строки | Маршрутов |
|---|---|---|
| live-states | `799`, `804`, `812` | 3 |
| plan/tasks | `825–1051` | 10 |
| readiness | `1077–1117` | 4 |
| rollback | `1129` | 1 |
| templates | `1135–1148` | 3 |
| queue | `1157–1255` | 10 |
| config/flags/preflight | `1279–1353` | 7 |
| claudemd | `1363`, `1385` | 2 |
| changelog | `1425` | 1 |
| stats/timeline/summary | `1431–1470` | 4 |
| thinking/roles/features/retrieval/engine/i18n | `1517–1728` | 24 |
| lessons | `1742–1852` | 13 |

### 4.2 `create_dashboard_app` (T008–T010) — не выполнена

Функция не менялась: `claude-runner/src/claude_runner/dashboard.py:154` (в аудите якорь
`dashboard.py:148`; смещение на 6 строк — правки других миссий), 455 строк, nesting 4,
C901 78. Причина: в тексте задачи (попытка 3) заявлены только первая группа маршрутов и три
границы циклов; расщепление dashboard в план остаётся неисполненным.

### 4.3 Остальные 14 сложностей из аудита — список следующей миссии

Ранги по `reports/audit-python.json`; `has_tests` — из аудита; рядом — где сейчас `def`
(смещение относительно аудита отмечено).

| Ранг | Функция | Якорь аудита | Сейчас | C901 | length | nesting | has_tests |
|---|---|---|---|---|---|---|---|
| 2 | `create_dashboard_app` | `dashboard.py:148` | `:154` | 78 | 455 | 4 | True |
| 3 | `create_app` | `api.py:232` | `:232` | 59 | 427 | 4 | True |
| 4 | `_build_router` (ops) | `board_ops_api.py:185` | `:170` | 42 | 378 | 4 | False |
| 5 | `_execute` | `runs.py:432` | `:437` | 30 | 245 | 5 | True |
| 6 | `run_markdown` | `export.py:109` | `:109` | 24 | 128 | 11 | True |
| 7 | `_build_router` (providers) | `board_providers_api.py:452` | `:452` | 22 | 167 | 2 | False |
| 10 | `feed` | `claude_cli.py:309` | `:309` | 21 | 63 | 6 | True |
| 13 | `entries_from` | `events.py:60` | `:60` | 19 | 95 | 6 | True |
| 15 | `classify` | `claude_cli.py:420` | `:420` | 17 | 98 | 6 | True |
| 16 | `parse_queue_watch_state` | `board_ops_parsers.py:189` | `:189` | 17 | 71 | 6 | True |
| 17 | `parse_watchdog_csv` | `board_ops_parsers.py:82` | `:82` | 17 | 63 | 6 | True |
| 18 | `_policy_tick` | `think_switch.py:227` | `:230` | 15 | 86 | 4 | False |
| 19 | `build_command` | `claude_cli.py:136` | `:136` | 15 | 75 | 2 | True |
| 20 | `parse` | `think_policy.py:104` | `:104` | 15 | 54 | 4 | True |

Ранги 8, 9, 11, 12, 14 в аудите — это e2e- и fake-CLI тестовые файлы, а ранг 1 — уже
разобранная `_build_router` (её якорь в аудите — `board_api.py:682`, в `HEAD` def на 682, в
рабочем дереве — на 786).

### 4.4 Что ещё осталось незакрытым

- `claude-runner/docs/board-ops-notes.md:540` описывает только `MAX_GUARD_SCANS`;
  `RUN_MAX_TICKS` и `WATCHDOG_MAX_SCANS` в документации не появились (документ в задачу не
  входил — менять её без указания в задаче я не стал).
- Границы в `runs.py` и `think_switch.py` выходят за план T011–T013 (план называет только
  `mem_guard.py:202`), но они названы в самой формулировке задачи («три границы циклов»);
  записаны как отдельные hunk'и, чтобы их можно было откатить независимо.

## 5. Откат по шагам

Откат — это «снять hunk'и данного шага», порядок снизу вверх. Ни один шаг не трогает файлы
других миссий (hunk'и `@@ -16,7`, `@@ -30,7`, `@@ -42,6`, `@@ -331,7`, `@@ -471,6`, `@@ -988,7`,
`@@ -1012,7`, `@@ -1039,7` в `runs.py` и hunk'и `@@ -55,0`, `@@ -72,13`, `@@ -90,10`, `@@ -425,4`,
`@@ -437,12` в `board_api.py` принадлежат refactor-1 и не относятся к этой миссии).

| Шаг | Что откатывать | Файлы и hunk'и |
|---|---|---|
| T013 | тест, что граница завершает цикл mem_guard | `tests/test_mem_guard.py` hunk'и `@@ -1,6 +1,11` (импорты) и `@@ -104,3 +109,107` (новые тесты) |
| T012 | границу mem_guard | `src/claude_runner/mem_guard.py` hunk'и `@@ -32,6 +32,8` (`MAX_GUARD_SCANS`) и `@@ -199,9 +201,11` (`while True` → `for _ in range(...)`, лог выхода на `:210`) |
| T011 | характеризационные тесты mem_guard-цикла | `tests/test_mem_guard.py` `@@ -104,3 +109,107` |
| — | границу watchdog (сверх плана) | `src/claude_runner/runs.py` hunk'и `@@ -87,6 +94,10` (`WATCHDOG_MAX_SCANS`) и `@@ -871,12 +891,18` (`_watchdog_loop`) плюс тест `tests/test_runs.py` `@@ -637,3 +644,310` (часть hunk'а, содержащая `test_watchdog_loop_stops_at_max_scans`) |
| — | границу think switch (сверх плана) | `src/claude_runner/think_switch.py` hunk'и `@@ -39,6 +39,9` (`RUN_MAX_TICKS`) и `@@ -520,9 +523,12`; тест `tests/test_think_switch.py` `@@ -847,3 +850,154` (часть с `test_run_stops_at_max_ticks`) |
| T004 | узкие проверки (ruff/mypy/C901) — это замеры, а не код; откатывать нечего, кроме вывода отчёта | — |
| T003 | вынесение первой группы | `src/claude_runner/board_api.py` hunk'и `@@ -681,0 +644,142` (удалить 142 добавленных строк: 8 обработчиков + `_register_status_and_missions_routes`) и `@@ -691,124 +795,2` (вернуть 124 inline-строк вместо вызова на `:796`) |
| T002 | инвентарь маршрутов и тесты группы | `tests/test_board_api.py` hunk'и `@@ -20,7 +20,7` (импорт) и `@@ -1971,3 +1971,330` (`_BOARD_ROUTE_ORDER`, тест порядка, `TestFirstRouteGroup`) |
| T001 | baseline и карту групп | `reports/review-baseline.json`, `reports/audit-python.json`, `reports/audit-frontend.json` (последний в `git status` помечен как изменённый) |

Если откатывать только T003, не трогая T002: тест `_BOARD_ROUTE_ORDER` продолжит проходить,
потому что порядок регистрации не зависит от способа group'ировки (90 → 90, identical — см.
раздел 2).

---

## Открытые риски

1. Фан-риск нумерации: план миссии (`plan_json` в стейме задачи) называет отчёт шагом **T015**,
   а регрессионную проверку — **T014**; формулировка запуска («T014 (попытка 3)») требует отчёта
   на T014. Числа и ссылки в отчёте от этого не меняются.
2. `reports/audit-python.json` ссылается на `runs.py:886` как на `while True` в `_watchdog_loop` —
   в HEAD это строка 874. Разница ±12 строк — следствие того, что аудит снимался с другого
   состояния дерева.
3. Полный pytest-сейт не запускался; три падения в `tests/test_board_api.py` и одно в
   `tests/test_runs.py` не сверены с HEAD-списком (см. раздел 2).
4. Границы `MAX_GUARD_SCANS = 86_400` (сутки сканов), `RUN_MAX_TICKS = 4_320` (2 часа тиков),
   `WATCHDOG_MAX_SCANS = 10_000` (≈7 дней при 60 с) — если контейнер должен жить дольше, границу
   надо увеличить; это зафиксировано комментарием в `runs.py:98–99`.
5. `create_dashboard_app` (455 строк, C901 78) остаётся самой большой функцией runner-а: без
   T008–T010 рефакторинг считается незавершённым.
