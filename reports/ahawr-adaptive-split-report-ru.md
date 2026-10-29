# Отчёт по миссии ahawr-adaptive-split

Составлен оператором вручную 2026-10-20: задача T020 (отчёт) не прошла ревью на попытке 1 (Haiku, 45): Worker не нашёл список задач и описал работу по шести «частям» из текста миссии, а не по задачам; попытка 2 (локальная модель) за 26 минут не дала результата. T019 принята оператором вручную после двух попыток (55 на первой). Цифры ниже взяты из базы миссии, файлов репозитория и повторных запусков на хосте, источник указан.

## Итог

Миссия выполнена по T001-T018: все приняты Reviewer, оценки 78-88. T019 закрыта оператором (оценка 55 стоит за отсутствием результата полного прогона, который оператор получил сам). T020 написан оператором. Новое поведение workflow (шлюз размера плана, разбиение без обрезки, разбиение задачи при таймауте и раздувании, гигиена запусков) реализовано как файлы и патч, в живой workflow n8n НЕ применено, все флаги по умолчанию выключены.

## Задачи миссии

Источник: `plan_json` и таблица событий миссии (`review_parsed`), попытки и последняя оценка.

| Задача | Что сделано | Попыток | Оценка |
|---|---|---|---|
| T001 | Исследование: узлы workflow, шаблон патча, бюджет вывода Architect (`data-tables/ops/adaptive_split/NOTES.md`) | 2 | 88 |
| T002 | Исследование: сводка запуска runner, порождение процессов, очередь, разбор id задач | 1 | 88 |
| T003 | Runner: в сводку запуска добавлены `compactions`, `steps`, `edits`, `files_changed` | 1 | 88 |
| T004 | Runner: при cancel, interrupt и timeout убивается вся группа процессов | 1 | 80 |
| T005 | Runner: вытесненные (superseded) запуски очереди не стартуют и показываются как cancelled | 1 | 78 |
| T006 | Runner: watchdog зависших потомков и сирот | 2 | 88 |
| T007 | Разборщики id принимают буквенный суффикс (T013A) | 3 | 88 |
| T008 | Чистый JS: шлюз размера плана и разбиение без обрезки на части (`plan_gate.js`) | 1 | 88 |
| T009 | Тесты `plan_gate.js` (формулировки T013 и T015, разбиение) | 1 | 88 |
| T010 | Чистый JS: триггер разбиения, выдача id, переписывание зависимостей (`task_split.js`) | 2 | 88 |
| T011 | Тесты `task_split.js` и таблицы решений триггера | 1 | 88 |
| T012 | Узлы: Constants и Evaluate Architect (шлюз, один повтор, части) | 2 | 88 |
| T013 | Узлы: промпт Architect (весь план, фазы, режим разбиения) | 1 | 88 |
| T014 | Узлы: разбиение перед RESUME, уведомления, миссии-части, гигиена запусков | 4 | 84 |
| T015 | Идемпотентный патч `data-tables/ops/adaptive_split_patch.py` | 4 | 84 |
| T016 | Тесты патча | 1 | 88 |
| T017 | `data-tables/ops/mission_lint.py` с тестами | 1 | 88 |
| T018 | Документы: `docs/adaptive-split.md`, `docs/mission-authoring.md`, раздел в `claude-runner/README.md` | 2 | 88 |
| T019 | Запись CHANGELOG и итоговая проверка | 2, принята оператором | 55 |
| T020 | Этот отчёт, написан оператором | 1 (45), попытка 2 не завершилась | - |

Всего 20 позиций; ни одна не разбивалась (идентификаторов с буквенным суффиксом в плане нет).

## Что реализовано (по частям миссии)

1. **Шлюз плана и план без обрезки** (`adaptive_split/plan_gate.js`, 116 строк; узлы `Evaluate_Architect.js`, `Create_Part_Missions.js`). Широкая задача: больше 3 файлов в области, цель длиннее 1500 символов, слова «весь/целиком/whole/entire» рядом с модулем или файлом, больше 4 результатов. План до `max_tasks` (15) идёт целиком; больший делится по границам фаз на 2-5 последовательных миссий (`<id>`, `<id>-p2`...), фаза может растянуть часть до `max_tasks_hard` (30); план, которому нужно больше 5 частей, отклоняется, а не обрезается. Источник: `docs/adaptive-split.md`, раздел Parts.
2. **Разбиение во время работы** (`adaptive_split/task_split.js`, 124 строки; `Apply_Split_Answer.js`, `Build_Worker_Run_Input.js`). Триггеры: таймаут, 2 и более компакции, больше 150 шагов. Задача заменяется 2-4 задачами с буквенными суффиксами (T013 -> T013A, T013B...), зависимости переписываются, поля сессии Worker сбрасываются. Ограничения: глубина 1, не больше 6 разбиений и 40 задач. Таблица решений: `docs/adaptive-split.md`.
3. **Сводка запуска runner** (`runs.py`, `run_views.py`, `store.py`, `tests/test_run_summary_fields.py`): новые поля обратно совместимы.
4. **Линтер миссий** (`data-tables/ops/mission_lint.py`, 71 строка, тесты `tests/test_mission_lint.py`) и `docs/mission-authoring.md`. Линтер предупреждает на реальной миссии доски; точное число задач её плана не зафиксировано в скрипте (Architect создаёт план при запуске), поэтому документ отсылает к CHANGELOG за 2026-10-17 (30 задач).
5. **Патч workflow** (`data-tables/ops/adaptive_split_patch.py`, 348 строк): идемпотентный, работает на копии выгрузки, повторный запуск ничего не меняет. В n8n не импортирован. Команды применения, включения и отката: `docs/adaptive-split.md`.
6. **Гигиена запусков** (runner): убийство группы процессов (`claude-runner/tests/test_process_group_kill.py`, `test_run_process_cleanup.py`), отмена вытесненных запусков в очереди (`test_queue_supersede.py`), watchdog зависших потомков и сирот с кодами ошибок `child_hung` и `orphan` (`test_watchdog.py`, `test_process_routes.py`, `test_run_process_listing.py`); отмена предыдущего `worker_run_id` при перезапуске — в патче workflow, за флагом `run_hygiene`.

## Файлы

Новые: `docs/adaptive-split.md`, `docs/mission-authoring.md`, `data-tables/ops/adaptive_split_patch.py`, `mission_lint.py`, каталог `data-tables/ops/adaptive_split/` (JS-узлы, `plan_gate.js`, `task_split.js`, тесты, `NOTES.md`), `claude-runner/tests/`: `test_run_summary_fields.py`, `test_task_id_suffix.py`, `test_queue_supersede.py`, `test_watchdog.py`, `test_process_group_kill.py`, `test_process_routes.py`, `test_run_process_cleanup.py`, `test_run_process_listing.py`.
Изменённые (по `git status`): `claude-runner/src/claude_runner/runs.py`, `run_views.py`, `store.py`, `config.py`, `claude_cli.py`, `api.py`, `claude-runner/tests/test_runs.py`, `claude-runner/README.md`, `CHANGELOG.md`. Часть изменений в `runs.py`, `api.py`, `config.py`, `claude_cli.py` относится и к другим миссиям (guards, cloud-thinking): раздельно по миссиям их не посчитать, потому что коммита между миссиями не было.

Не менялись (проверено `git diff --quiet HEAD`): `AHAWR_v13_ClaudeCode.json`, `Claude_Code_Run_Manager_v1.json`.

## Тесты и проверки

- `data-tables/ops`: `python -m pytest adaptive_split/tests tests/test_mission_lint.py -q --no-cov` -> `24 passed in 4.04s` (повтор оператора).
- `node --test` для `plan_gate.test.js` и `task_split.test.js` -> `# tests 2`, `# pass 2`, `# fail 0` (повтор оператора).
- Ruff по пяти изменённым файлам runner (`runs.py`, `run_views.py`, `store.py`, `config.py`, `claude_cli.py`) -> `All checks passed!` (повтор оператора). В заметках Worker: ruff по шести файлам `ops` -> `All checks passed!`.
- Runner, файлы `test_run_summary_fields.py`, `test_task_id_suffix.py`, `test_queue_supersede.py`: проходят на хосте (`[100%]`, без падений).
- Runner, файлы `test_watchdog.py`, `test_process_group_kill.py`, `test_process_routes.py`, `test_run_process_cleanup.py`, `test_run_process_listing.py`: на Windows-хосте падают по POSIX-причинам (`killpg`, поддельный CLI, `/proc`); на Linux-контейнере Worker их запускал. Это согласуется с правилом из `CLAUDE.md`, но по этим пяти файлам оператор итогового числа не фиксировал.
- Полный набор runner на хосте (оператор, `python -m pytest -q --no-cov -p no:cacheprovider`, около 6 минут): `EXIT 1`, 98 строк `FAILED`, среди них `test_search_cli.py` и `test_watchdog.py`; строки итога со счётчиком в логе найти не удалось (`claude-runner/.pytest_full_host.log`). Сравнения с падениями до миссии нет: это не проверено.
- Первая попытка Worker запускать полный набор закончилась тайм-аутом 1500 с (`EXIT 124`, 57%) из-за утёкших процессов-«крутилок» от тестов (`spin.py`, `while True: sum(range(...))`); оператор убил их по PID (в контейнере claude-runner, 12 процессов, затем ещё 6).

## Что не проверено

- Патч workflow не применялся к живому n8n и не прогонялся на нём; работа проверена только на копии выгрузки и тестами.
- Ни один из флагов (`adaptive_split`, `plan_no_truncate`, `run_hygiene`) не включался; поведение разбиения во время реальной миссии не наблюдалось.
- Падения 98 тестов на хосте не сопоставлены с падениями до миссии.
- Архитектор: оценка бюджета вывода в `NOTES.md` раздел 6 — оценка, а не измерение.

## Что нашла эта миссия в самой системе

- Тесты runner оставляют зависшие дочерние процессы (`test_busy_child_without_events`, таймаутные тесты): они съедают CPU контейнера до 600%, замедляют соседние запуски и один раз довели Worker до тайм-аута. PID 1 контейнера (`claude-runner`) не собирает зомби. Исправление теста и `init: true` в compose вынесены в отдельные работы.
- Задачи-отчёты проваливаются систематически (T015 прошлой миссии, T017 cloud-thinking, T020 здесь) по одной причине: у Worker нет списка задач и фактов. Это решает миссия `ahawr-report-pack`.

## Команды для оператора (не запускались)

Применение, включение флагов и откат описаны в `docs/adaptive-split.md`; перед включением флагов проверить патч на свежей выгрузке workflow.

## Версия и CHANGELOG

Предложение: включить в единую запись 0.8.0 (новые функции: шлюз плана, разбиение, гигиена запусков) вместе с cloud-thinking и guards; запись «0.9.0» из T019 заменить, версию в `pyproject.toml` и `api.py` поднять до 0.8.0. Коммит и пуш — по плану оператора после этой миссии.
