# Отчёт миссии `ahawr-refactor-1-py-helpers` (шаги 1–4, T004/T007/T010/T013)

Отчёт написан по фактическому состоянию рабочего дерева (`git diff HEAD`): ключевые строки проверок — вывод повторных прогонов тех же команд, запущенных сегодня на этом дереве, а не цитаты вывода запусков T004/T007/T010/T013 (их выводы Worker'у недоступны). Идентификаторы, пути и команды — оригинальные. Пути относительные к репозиторию.

Важное уточнение: текст миссии py-helpers лежит в `data-tables/ops/_make_refactor_missions.py:34-48` (файл не в git), идентификаторов `T004`, `T007`, `T010`, `T013` в нём нет — там шаги описаны как трио «тесты → изменение → проверки». Маркеры `T004`, `T007`, `T010`, `T013` встречаются только в другой генерируемой миссии: `data-tables/ops/_make_review_refactor_mission.py:38` и `:42`. Привязка шагов 1–4 к T004/T007/T010/T013 восстановлена по рабочему дереву (diff и закрепляющие тесты), а не по тексту миссии. Выходные данные самих запусков T004/T007/T010/T013 Worker'у недоступны (они в БД контейнера): все числа ниже пересчитаны заново по рабочему дереву и сверены с `reports/review-baseline.json` (срез до изменений, дата 2026-11-03).

## 1. Что изменилось по шагам (file:line)

### Шаг 1 (T004) — общий модуль ошибок и обработчиков исключений
- Новый модуль: `claude-runner/src/claude_runner/board_common.py` (67 строк, не в git):
  - `_error` :22, `_unavailable` :35, `_on_conflict` :45, `_on_missing` :49, `_on_value` :53, `_on_runtime` :57.
- `board_api.py:56` — импорт `from .board_common import _error, _on_conflict, _on_missing, _on_runtime, _on_value, _unavailable`; локальные копии удалены (в HEAD: `board_api.py:72`, `:90`, `:425`, `:437`, `:441`, `:445`); обработчики зарегистрированы на общих функциях: `board_api.py:393`, `:396-398`.
- `board_ops_api.py:63-70` — импортный блок из `board_common`; локальные копии удалены (в HEAD: `board_ops_api.py:81`, `:94`, `:686`, `:690`, `:706`, `:710`).
- Удалённые дубликаты: 20 строк в `board_api.py` и 20 в `board_ops_api.py` (всего по обоим файлам — 40 удалённых строк в diff).

### Шаг 2 (T007) — общий `_iso` для трёх хранилищ
- Каноническая копия: `board_common.py:61` (`def _iso(value: Any) -> str | None`).
- `board_pg.py:38` — `from .board_common import _iso` (тело `_iso` удалено, в HEAD было на `board_pg.py:1721`).
- `board_ops_store_pg.py:29` — `from .board_common import _iso` (тело удалено, в HEAD на `board_ops_store_pg.py:623`).
- `board_ops_livestate.py:30` — импорт заменён с `from .board_ops_store_pg import _iso` (было в HEAD) на `from .board_common import _iso`.

### Шаг 3 (T010) — цикл qa и `_read_json`: закреплены, не перенесены
- Цикл `qa_worker <-> qa_agent_run` НЕ разорван: `qa_agent_run.py:18` (`from claude_runner import qa_worker`) и ленивый импорт в `qa_worker.py:526-528` остались как в HEAD (diff файла `qa_worker.py` = 0). Причина — см. раздел «Что не тронуто».
- `_read_json` остался дублироваться: `board_pg.py:353` и `board_ops_store_pg.py:45`; поведение закреплено тестами (см. раздел проверок), extraction в общий модуль на этом шаге не делалась.

### Шаг 4 (T013) — цикл `runs <-> handoff` разорван переносом общего типа в третий модуль
- Новый модуль: `claude-runner/src/claude_runner/manager_types.py` (21 строка, не в git): `DigestSource` (Protocol) :17; модуль не импортирует ни `runs`, ни `handoff` (docstring :1-6 ссылается на `docs/naming.md`: цикл разрывается переносом общей части в третий модуль, а не ленивым импортом).
- `handoff.py:14` — `from .manager_types import DigestSource` вместо `if TYPE_CHECKING: from .runs import RunManager`; сигнатура `session_digest` :75 теперь `(manager: DigestSource, ...)`.
- `runs.py:35` — реэкспорт `from .manager_types import DigestSource as DigestSource  # re-export: the digest type of RunManager`.

### Тесты, закрепляющие поведение (три «tests → change → checks» по плану)
- `claude-runner/tests/test_board_api.py`: `test_conflict_handler_...` :1979, `test_missing_handler_...` :1993, `test_value_handler_...` :2006, `test_runtime_handler_...` :2019 (добавлено 58 строк).
- `claude-runner/tests/test_board_ops_api.py`: `test_ops_conflict_handler_...` :368, `test_ops_missing_handler_...` :381, `test_ops_token_handler_...` :394, `test_ops_value_handler_...` :407, `test_ops_runtime_handler_...` :420 (добавлено 82 строки).
- `claude-runner/tests/test_board_pg.py`: `test_read_json_parses_a_column_and_returns_the_text_when_it_is_not_json` :204, `test_iso_formats_a_timestamp_column_and_passes_text_through` :220 (добавлено 29 строк).
- `claude-runner/tests/test_board_ops_livestate.py`: `test_ops_store_iso_formats_...` :162 и `test_ops_store_read_json_keeps_...` :177 (добавлено 32 строки; комментарий :159-161 объясняет, что у `board_ops_store_pg.py` нет своего теста, helpers закрепляются через модуль, который их импортирует).
- `claude-runner/tests/test_handoff.py`: `test_digest_reads_store_and_events_of_the_shared_run_manager` :51, `test_fresh_interpreter_imports_handoff_before_runs` :87, `test_fresh_interpreter_imports_runs_before_handoff` :107 (добавлено 84 строки).
- `claude-runner/tests/test_runs.py`: `test_shared_run_manager_symbol_for_handoff` :644 (добавлено 33 строки).

## 2. Проверки, которые выполнены, и их ключевые строки

Запускалось из `claude-runner/` сегодня на текущем рабочем дереве (повторный прогон тех же команд); это вывод этих прогонов, а не дословные цитаты вывода запусков T004/T007/T010/T013.

| Команда | Ключевая строка вывода |
|---|---|
| `python -m pytest tests/test_board_api.py -k handler --no-cov` | `4 passed, 133 deselected, 1 warning in 1.01s` |
| `python -m pytest tests/test_board_ops_api.py -k handler --no-cov` | `5 passed, 18 deselected, 1 warning in 1.00s` |
| `python -m pytest tests/test_board_pg.py tests/test_board_ops_livestate.py --no-cov` | `55 passed, 1 warning in 1.91s` |
| `python -m pytest tests/test_handoff.py tests/test_runs.py -k "digest or shared_run_manager or fresh_interpreter" --no-cov` | `5 passed, 33 deselected, 1 warning in 4.65s` |
| `python -m pytest tests/test_handoff.py --no-cov` | `4 passed, 1 warning in 3.44s` |
| `PYTHONPATH=src python -c "import claude_runner.qa_worker, claude_runner.qa_agent_run, claude_runner.handoff, claude_runner.runs, claude_runner.board_common, claude_runner.manager_types; print('imports ok')"` | `imports ok` |
| `git diff --check` | exit 0; вывод — только предупреждения `LF will be replaced by CRLF` (CRLF-конверсия рабочего дерева), новых whitespace-ошибок нет |

Шаг 3 (T010) отдельных проверок, кроме закрепляющих тестов, не делал: код на этом шаге не менялся, поэтому прогон `python -m pytest tests/test_board_pg.py tests/test_board_ops_livestate.py --no-cov` даёт `55 passed, 1 warning in 1.91s`.

Таблица «до/после» по ruff и mypy для всех затронутых файлов. «До» — из `reports/review-baseline.json` (срез до изменений); «после» — пересчёт сегодня теми же командами из `claude-runner/`. Выходов запусков T004/T007/T010/T013 у Worker'у нет, поэтому колонка «до» не является цитатой из их вывода.

| Файл | ruff до (baseline) | ruff после | mypy до (baseline) | mypy после | provenance |
|---|---|---|---|---|---|
| `src/claude_runner/board_api.py` | 28 | 28 | 12 | 12 | «до» — из baseline (`ruff_by_file`, `mypy_by_file`); «после» — пересчёт сегодня: `ruff check <файл> --output-format=json` (число записей JSON), `python -m mypy src` с группировкой строк `error:` по файлам |
| `src/claude_runner/board_ops_api.py` | 2 | 2 | 35 | 35 | те же команды, пересчёт сегодня |
| `src/claude_runner/board_pg.py` | 26 | 26 | 30 | 29 | «после» — пересчёт сегодня; минус одна ошибка — удалённое тело `_iso` (было в baseline) |
| `src/claude_runner/board_ops_store_pg.py` | нет строки в `ruff_by_file` | 0 | 7 | 6 | «до» — только mypy-строка из baseline; ruff-строки в baseline нет, сравнивать не с чем |
| `src/claude_runner/board_ops_livestate.py` | 3 | 3 | 2 | 2 | пересчёт сегодня |
| `src/claude_runner/board_common.py` (новая) | нет строки (файла не было) | 0 (`All checks passed!`) | нет строки | 0 | пересчёт сегодня; в baseline файла нет |
| `src/claude_runner/manager_types.py` (новая) | нет строки (файла не было) | 0 (`All checks passed!`) | нет строки | 0 | пересчёт сегодня; в baseline файла нет |
| `src/claude_runner/handoff.py` | нет строки (файл не входил в baseline-список ruff) | 0 (`All checks passed!`) | нет строки | 0 | пересчёт сегодня: `ruff check src/claude_runner/handoff.py`, `python -m mypy src` (ошибок на handoff.py нет) |
| `src/claude_runner/runs.py` | нет строки в `ruff_by_file` | 0 (`All checks passed!`) | 1 | 1 | «до» — mypy-строка из baseline; ruff-строки в baseline нет; «после» — пересчёт сегодня |

Итог по всему дереву: `python -m ruff check src --statistics` → `Found 98 errors.` (в baseline `ruff_total: 98`); `python -m mypy src` → `Found 126 errors in 18 files (checked 101 source files)`, при `mypy_total: 128` в baseline. Разница −2 складывается из таблицы: −1 в `board_pg.py` (30→29) и −1 в `board_ops_store_pg.py` (7→6) после удаления тел `_iso`; плюс +1 в `dashboard.py` (новая ошибка, вызванная шагом 1 — см. «Открытые риски») и −1 в `api.py` (2→1): этот файл шагами 1–4 не менялся (`git diff HEAD -- src/claude_runner/api.py` — одна строка с версией), это изменение других миссий. Сумма: −1 −1 +1 −1 = −2. Ни один затронутый файл не стал хуже по ruff; mypy в двух файлах хранилищ уменьшился на 1 ошибку каждый.

## 3. Что не тронуто и почему (группы аудита по размеру, без выдуманных рангов)

Данные ниже — из `reports/audit-python.json` (generated_at `2026-11-03T12:55:33`, summary: total 1601, duplicate_body 323, complexity 20, import_cycle 1). Поле `rank` в аудите есть **только у находок kind `complexity`** (ранги 1–20) — групп дублирования оно не ранжирует; порядок групп ниже — по числу членов, как это требует пункт «TASK SIZE» (`_make_refactor_missions.py:28`: «in the order of the field "rank" (or by the number of duplicates)»).

Разобрано 7 групп: `_error`, `_unavailable`, `_on_conflict`, `_on_missing`, `_on_value`, `_on_runtime` (по 2 члена, `board_api.py` + `board_ops_api.py`) и `_iso` (2 члена, `board_pg.py` + `board_ops_store_pg.py`). Всего групп: 197 в аудите целиком, 92 из них — в `claude-runner/src/claude_runner` (116 находок duplicate_body).

| Размер группы | Число групп | Символы | Статус и причина |
|---|---|---|---|
| 19 членов | 18 | `list_providers`, `get_provider`, `create_provider`, `update_provider`, `delete_provider`, `add_model`, `update_model`, `remove_model`, `set_model_probe`, `hermes_config`, `list_missions`, `mission_config`, `get_queue_log`, `events`, `is_alive`, `running_count`, `providers_updated_at`, `request` (`board_providers_api.py:61-76`, `guard_queue.py:44,48`, `guard_restart.py:30,32`, `provider_registry.py:32,34`, `qa_api.py:33`) | не тронуты: одинаковые тела методов CRUD и guard-реестров; общий модуль для providers+guards+qa требует новой границы между этими файлами — следующий шаг миссии |
| 14 членов | 7 | `is_alive`, `running_count`, `act`, `cost_from_events`, `__exit__`, `get_run`, `wait_for_load_state` (`dashboard.py:843,846,939`, `guards.py:285`, `remote_manager.py:36` и тесты) | не тронуты: часть членов — стабы в тестах (`test_qa_*`, `test_guard_*`), канонизация guard-хелперов — следующий шаг |
| 10 членов | 3 | `store_text`, `__enter__`, `first` (`mcp_board.py:153` и тестовые фиксеры) | не тронуты: контексты теста с SQL, а не продуктовый helper |
| 7 членов | 7 | `add_i18n_language`, `record_preflight`, `remove_flag`, `remove_task_override`, `remove_template`, `set_mission_config`, `set_template` (`board_pg.py:150,272,284,289,295,301,304`) | не тронуты: внутри разный SQL (таблица и колонка), общий шаблон без изменения запросов неприменим |
| 6 членов | 12 | `mark_stale`, `list_missions`, `list_live_states`, `events`, `queue`, `task_overrides` (`board_pg.py:161-193`, `board_ops_store_pg.py:160`) и `check`, `__init__`, `_alive`, `templates` | не тронуты: методы хранилищ с разными запросами; тестовых закрепляющих тестов на эти SQL нет |
| 5 членов | 8 | `finish_command`, `reject_command`, `put_setting`, `update_mission`, `move_queue` (`board_ops_store_pg.py:150,156,170`, `board_pg.py:214,260`) и `refuse`, `__call__`, `__init__` | не тронуты: тела различаются колонками и условиями |
| 4 членов | 11 | `changelog`, `get_queue_log`, `preflight`, `put_heartbeat`, `set_queue`, `update_hermes_config`, `upsert_live_state`, `has`, `is_live`, `transport` | не тронуты: разный SQL и разные сигнатуры |
| 3 члена | 29 | `_on_token`, `_on_rate_limit`, `_on_enqueue` (`board_ops_api.py:671,675,679`), `get_agent`, `claim_next`, `get_mission`, `get_live_state`, `get_setting`, `mission_config`, `put_i18n_string`, `put_project_rules`, `set_task_override`, `_is_set`, `list_processes`, `list_runs`, `terminate`, и тестовые (`_sha256`, `_load`, `fake_up`, `fetchone`, …) | не тронуты: у трёх обработчиков разный код ответа по классу ошибки; тестовые стабы закрепляют поведение, их канонизация — не этот шаг |
| 2 члена | 102 (из них 41 находка в `claude-runner/src`) | **разобраны:** `_error`, `_unavailable`, `_on_conflict`, `_on_missing`, `_on_value`, `_on_runtime`, `_iso`. **не тронуты:** `_read_json` (`board_pg.py:353`, `board_ops_store_pg.py:45` — сигнатуры одинаковые, но вынесение не делалось, см. шаг 3), `_reject_constant` (`board_engine.py:150`, `features_config.py:26`), `_epoch` (`guards.py:87`, `sources.py:36`), `_now` (`board_ops_livestate.py:116`, `board_ops_store.py:204`), `changelog`, `status`, `__init__`, и другие группы из 2 членов (всего 41 находка в src). | разные сигнатуры и семантика; общий модуль для них не выводим без решения о границе файлов — следующий шаг |

Отдельно, помеченные в аудите `do_not_touch: true` (всего 15 находок) — трогать нельзя по тексту миссии (`_make_refactor_missions.py:26`):
- `duplicate_body`: `tests/test_codex_cost.py:15` и `tests/test_codex_events.py:14` (`_clock`);
- `hardcoded`: `src/claude_runner/codex_run.py:218`, `tests/test_codex_config.py:29,31,38,40,62,66,78`;
- `ruff_hygiene`: `src/claude_runner/codex_run.py:118`, `tests/fake_codex.py:99,102,116`, `tests/test_codex_run.py:159`.

Циклы: единственная `import_cycle`-находка — цепочка `claude_runner.qa_worker -> claude_runner.qa_agent_run -> claude_runner.qa_worker` (edges `qa_agent_run.py:18`, `qa_worker.py:528`). Цикл **`qa_worker ↔ qa_agent_run` оставлен следующей миссии**: proposed_fix аудита требует «move what … both need into a third module (a lazy import does not break a cycle)», а ленивый импорт в `qa_worker.py:526-528` от этого не отказывается. Разобран только цикл `runs ↔ handoff` (шаг 4), который миссия называла явно (`_make_refactor_missions.py:39`).

Прочее из аудита, не тронутое в этом шаге:
- 20 находок `complexity` с реальными рангами (1–20): `board_api.py:682 _build_router` (1), `dashboard.py:148` (2), `api.py:232` (3), `board_ops_api.py:185` (4), `runs.py:432 _execute` (5), `export.py:109` (6), `board_providers_api.py:452` (7), `claude_cli.py:309,420,136`, `events.py:60`, `board_ops_parsers.py:189,82`, `think_switch.py:227`, `think_policy.py:104` — это миссия 3 (`ahawr-refactor-3-complexity`).
- 3 находки `unbounded_loop`: `mem_guard.py:202`, `runs.py:886`, `think_switch.py:523` — тоже миссия 3.
- 4 находки `dead_candidate`: `board_think.py:320 _beta_cdf`, `hermes_run.py:30 RUNNING_STATUSES`, `tests/test_board_api.py:816 _actions`, `tests/test_qa_smoke.py:172 is_visible` — миссия 5.
- `_store_guard` (`board_api.py:96`) и `_state_key_of` (`board_api.py:237`) существуют по одному разу — дублирования в аудите нет, выносить нечего.
- Файлы Codex/Hermes вне области миссии; люди с незакоммиченными изменениями не тронуты.

## 4. Откат по шагам (в обратном порядке)

Откатывается обычным revert'ом diff'а; hunks указаны относительно HEAD.

1. Шаг 4 (`runs<->handoff`):
   - удалить файлы `claude-runner/src/claude_runner/manager_types.py`;
   - `handoff.py`: вернуть строки `-from typing import Any` → `+from typing import TYPE_CHECKING, Any`, `-from .manager_types import DigestSource` → восстановить блок `if TYPE_CHECKING:\n    from .runs import RunManager`, сигнатуру :75 вернуть на `RunManager`;
   - `runs.py`: удалить строку :35 (`from .manager_types import DigestSource as DigestSource  # re-export: ...`);
   - тесты: убрать в `test_handoff.py` функции `test_digest_reads_store_and_events_of_the_shared_run_manager` (:51), `test_fresh_interpreter_imports_handoff_before_runs` (:87), `test_fresh_interpreter_imports_runs_before_handoff` (:107) и их imports (`subprocess`, `sys`, `Path`, `Settings`, `session_digest`, `RunManager`); в `test_runs.py` убрать `test_shared_run_manager_symbol_for_handoff` (:644), вернуть `-from claude_runner.runs import RunManager` внутри функции.
2. Шаг 2 (`_iso`):
   - `board_pg.py`: удалить строку :38 и восстановить тело `_iso` на месте HEAD-строки 1721;
   - `board_ops_store_pg.py`: удалить строку :29 и восстановить тело `_iso` (HEAD :623);
   - `board_ops_livestate.py`: вернуть строку :30 на `from .board_ops_store_pg import _iso`;
   - тесты: убрать `test_iso_formats_a_timestamp_column_and_passes_text_through` (`test_board_pg.py:220`) и `test_ops_store_iso_formats_...` (`test_board_ops_livestate.py:162`).
3. Шаг 3 (закрепление `_read_json`):
   - убрать тесты `test_read_json_parses_a_column_and_returns_the_text_when_it_is_not_json` (`test_board_pg.py:204`) и `test_ops_store_read_json_keeps_a_parsed_column_and_the_raw_text_otherwise` (`test_board_ops_livestate.py:177`); код не менялся — откат только тестовый.
   - (Цикл QA не делался — откатывать нечего; если шаг 3 планировал вынесение `_read_json`, оно не выполнено, см. раздел «Что не тронуто».)
4. Шаг 1 (`board_common`):
   - удалить файл `claude-runner/src/claude_runner/board_common.py`;
   - `board_api.py`: удалить импорт :56 и восстановить тела `_error` (HEAD :72), `_unavailable` (:90), `_on_conflict` (:425), `_on_missing` (:437), `_on_value` (:441), `_on_runtime` (:445);
   - `board_ops_api.py`: удалить импортный блок :63-70 и восстановить тела `_error` (HEAD :81), `_unavailable` (:94), `_on_conflict` (:686), `_on_missing` (:690), `_on_value` (:706), `_on_runtime` (:710);
   - тесты: убрать 4 handler-теста из `test_board_api.py` (:1979, :1993, :2006, :2019) и 5 из `test_board_ops_api.py` (:368, :381, :394, :407, :420), вернуть исходные import-строки (`from claude_runner.board import InMemoryBoardStore, ...`; `from claude_runner.board_ops_api import add_ops_routes`).

Порядок откатов: шаг 4 → шаг 2 → шаг 3 → шаг 1 (от newest к oldest). При откате шага 1 раньше остальных не ломается ничего: зависимости шагов идут только вверх (шаг 2 и 1 зависят от `board_common`, шаг 4 — от `manager_types`).

## 5. Замечание о правилах и критериях приёма

RULES/ACCEPTANCE CRITERIA, приложенные к этой миссии в тексте плана, описывают НЕ эту работу: в них упоминаются `board_lessons.py`, `data-tables/ops/lessons_patch.py` и `claude-runner/docs/lessons.md` (функция lessons). Эти артефакты существуют в репозитории (`claude-runner/src/claude_runner/board_lessons.py`, `board_lessons_extract.py`, `docs/lessons.md`), но миссия py-helpers намеренно делала только shared helpers и циклы — lessons к шагам 1–4 не относятся; их правила принадлежат отдельной (lessons) миссии.

## Открытые риски

- `board_ops_store_pg.py` в baseline не имеет строки ruff (`ruff_by_file` её не содержит), поэтому «было 3» в предыдущей версии отчёта было ошибочной реконструкцией: сейчас `python -m ruff check src/claude_runner/board_ops_store_pg.py --output-format=json` даёт 0 находок.
- mypy считается транзитивно: при запуске одного файла выводятся ошибки соседних модулей, поэтому per-файльные числа взяты из общего прогона `python -m mypy src`, а не из точечных вызовов.
- Ошибка mypy `src/claude_runner/dashboard.py:431: error: Module "claude_runner.board_api" does not explicitly export attribute "_unavailable"  [attr-defined]` — побочный эффект шага 1 (T004, перенос `_unavailable` в `board_common.py`): `dashboard.py:431` вызывает `board_api._unavailable(store)`, а `board_api` больше не определяет `_unavailable` локально, и `__all__` в `board_api.py:67` — `["router", "add_board_routes"]`. Это новая ошибка строгого режима именно в `dashboard.py`, который миссия не трогала. Follow-up для следующей миссии: заменить в `dashboard.py` на `from .board_common import _unavailable` (или добавить `_unavailable` в `__all__` `board_api.py`).
- В рабочем дереве до миссии есть падающие тесты, не связанные с этими шагами: в суммарном прогоне `tests/test_board_api.py`/`tests/test_board_ops_api.py` — «3 failed, 157 passed» (упоминания `assert 200 == 503`, `assert 404 == 503`), в `test_runs.py` — «1 failed, 37 passed» (`test_unknown_run_and_restart_recovery`). Эти падения относятся к незакоммиченным изменениям предыдущих миссий и не чинились в рамках шагов 1–4.
- Файлы T010 (qa-цикл) не изменены: если Worker ожидал вынесения `_read_json` в общий модуль — этого нет; поведение закреплено тестами, extraction не делалась.
