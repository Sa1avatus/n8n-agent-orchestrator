# Отчёт: ahawr-qa-smoke (универсальный smoke-обход QA)

Дата: 2026-10-23. Версия до миссии: 0.9.0. Предложение: 0.10.0 (раздел 7).
Все числа ниже сопровождаются именем файла-источника. Пути к сохранённым выводам
даны относительно `data-tables/ops/_scratch/ahawr-qa-smoke/` (далее `SCRATCH/`), если не сказано иное.

## 0. Покрытие задач T001–T013

План миссии с текстами задач хранится в таблице n8n и в этой сессии не читался
(`_accept_qs_t012.py` читает её только из оболочки оператора, а `n8nq.sh` вернул пустой
вывод). Точные тексты T001–T011 в сохранённых файлах отсутствуют — для них ниже
«нет сохранённых доказательств». Привязка по сохранённым файлам и по частям задания из
`data-tables/ops/_make_qa_smoke_mission.py` (строки 15–27, PART 1–6):

| Задача | Что сделано | Источник |
|---|---|---|
| T001 | нет сохранённых доказательств (план — в таблице n8n) | `data-tables/ops/_accept_qs_t012.py` (считывает план из таблицы; в этой сессии не читался) |
| T002 | нет сохранённых доказательств | то же |
| T003 | нет сохранённых доказательств | то же |
| T004 | нет сохранённых доказательств | то же |
| T005 | нет сохранённых доказательств | то же |
| T006 | нет сохранённых доказательств | то же |
| T007 | нет сохранённых доказательств | то же |
| T008 | нет сохранённых доказательств | то же |
| T009 | нет сохранённых доказательств | то же |
| T010 | нет сохранённых доказательств | то же |
| T011 | нет сохранённых доказательств (приёмка T012A/T012B отмечает переход на T013) | `data-tables/ops/_accept_qs_t012.py` |
| T012A | Четыре прогона краулера (default/empty × en/ru), вердикты и время; исправление `qa_smoke._controls` (один вызов `evaluate_all`) и `qa_smoke._mission_urls` | `SCRATCH/clean_*.txt`; `data-tables/ops/_t012a_crawls.sh`; заметка в `data-tables/ops/_accept_qs_t012.py` |
| T012B | Репликация трёх багов на копиров (replay a/b/c), `proposal.txt` | `SCRATCH/replay_a.txt`, `replay_b.txt`, `replay_c.txt`, `SCRATCH/proposal.txt`; `data-tables/ops/_t012b_replay.sh` |
| T013 | Проверки: pytest, ruff, mypy, node, diff, git status; исправления `qa_smoke.py` (`_controls`, `_mission_urls`) и `test_qa_smoke.py` | `SCRATCH/final/*.txt` (раздел 4); примечание к критерию T013 в `data-tables/ops/_accept_qs_t012.py` |
| T014 | Этот отчёт | `reports/ahawr-qa-smoke-report-ru.md` |

Раздел 1 перечисляет построенное по областям (PART 1–6 миссии), а не по номерам T.

Не запускались в этой сессии: docker, n8n, краулер, тесты. Использованы только сохранённые
выводы.

## 1. Что построено

| Файл | Строки | Что внутри |
|---|---|---|
| `claude-runner/src/claude_runner/qa_smoke.py` (539 строк, новый) | 56–68 | константы границ: `MAX_PAGES = 40`, `MISSION_PAGES = 3`, `MAX_CLICKS_PER_PAGE = 25`, `MAX_MINUTES = 10`, пути seeds, локальные хосты, селекторы |
| то же | 142, 193, 217, 222 | `load_seeds`, `check_host` (отказ не-локальным хостам), `destructive_match`, `run_smoke` |
| то же | 261, 278, 317, 350, 401 | `crawl`, `_mission_urls`, `_visit`, `_click_once`, `_controls` (один вызов `evaluate_all`, исправление T012A) |
| то же | 497, 519 | `build_smoke_verdict`, `_blocked_verdict` |
| `claude-runner/src/claude_runner/qa_smoke_detect.py` (235 строк, новый) | 85–222 | чистые детекторы: console-error (85), pageerror (93), client-error (97), null-id (107), missing-header (115), asset-failed (129), bad-text (139), error-visible/скрытое сообщение (149), overflow (175), непереведённые ключи (214), no-effect (222) |
| `claude-runner/qa/smoke_seeds.yaml` (31 строка, новый) | — | стартовые пути, пути миссий, список деструктивных подписей |
| `claude-runner/src/claude_runner/qa.py` | 83–107 | подкоманда `smoke`: `--fixture`, `--lang`, `--url`, `--allow-host`, `--max-pages`, `--max-clicks`, `--max-minutes` |
| то же | 185–209 | `_nightly`: краулер запускается после сценариев, если включён `smoke` |
| то же | 239, 265, 309 | `_smoke`, `_smoke_crawl`, `_merged_smoke_verdict` |
| `claude-runner/src/claude_runner/roles_config.py` | 17, 164–165 | `QA_OPTION_KEYS` содержит `smoke`; нелогическое значение отклоняется с причиной `option 'smoke' must be true or false` |
| `claude-runner/src/claude_runner/qa_worker.py` | 57, 413–414, 135, 161 | `SMOKE_MODULE`, `smoke_enabled` (по умолчанию выключено), `changed_files` (используется для проверки UI-файлов) |
| `claude-runner/src/claude_runner/board_edit_win.js` | 37, 41, 180 | строка QA в окне настроек: поле `smoke`, подпись `ew.qa.smoke` |
| `claude-runner/src/claude_runner/board_i18n_en.json`, `board_i18n_ru.json` | 50 | ключ `ew.qa.smoke` ("Smoke crawl" / "Смоук-обход") |
| `claude-runner/src/claude_runner/board_i18n.js` | 449 | ключ `ew.qa.smoke` в `BUNDLED_EN` |
| `data-tables/ops/qa_workflow_patch.py` | 20–25, 353–363 | ключ `smoke` в патче рабочего процесса: при `false` тело QA-запроса не меняется |
| `claude-runner/docs/qa-smoke.md` (161 строка, новый) | 119–133 (активация), 143–161 (шесть находок) | проектная документация |
| `claude-runner/tests/test_qa_smoke.py` (375 строк) | — | 20 функций `def test_` (подсчёт `grep -c`) |
| `claude-runner/tests/test_qa_smoke_detect.py` (246 строк) | — | 24 функции `def test_` |
| `claude-runner/tests/test_qa_smoke_integration.py` (54 строки) | — | 1 функция `def test_` (с пометкой интеграционного теста) |

Изменённые файлы `qa_runner.py`, `qa_stack.py`, `test_qa_cli.py`, `test_qa_runner.py`,
`test_qa_stack.py`, `test_qa_workflow_patch.py` видны в `git status` как изменённые.

Известный разрыв: `claude-runner/docs/qa-smoke.md:6` всё ещё говорит
"Status: design only. Nothing in this document is implemented yet." (файл `qa-smoke.md`, строка 6).

## 2. Шесть реальных находок и результат репликации

Таблица находок и колонки «найдёт ли обход» взяты из `claude-runner/docs/qa-smoke.md:150–157`.
Результат репликации — из `SCRATCH/replay_a.txt`, `replay_b.txt`, `replay_c.txt` (подсчёт строк
`"detector"` по каждому файлу) и из `SCRATCH/proposal.txt`.

| № | Находка | Детектор по дизайну | Реплицирована? | Результат |
|---|---|---|---|---|
| 1 | Пустой URL: сценарии открывали `/board#...` | нет | нет (плохой адрес был в файле сценария, не в ссылке приложения) | не реплицирована; в сохранённых выводах её нет |
| 2 | Нет таблиц n8n: 503 на каждый API-запрос | `server-error` | нет | в сохранённых выводах нет 503 и нет детектора `server-error` (поиск по `SCRATCH/*.txt` дал 0 совпадений) |
| 3 | Пустой URL настроек: `GET /api/board/missions/null/roles` | `null-id` | да (replay a) | 0 находок `null-id` в `replay_a.txt`: `proposal.txt` объясняет, что краулер до этого шага не посещал страницы миссий (исправление `_mission_urls`), а текущий `board_edit_win.js` отбрасывает пустой id до запроса |
| 4 | Скрытое сообщение `#boardstate` о конфликте Kanban | `error-visible` | да (replay b: удалён `showNotice`) | 0 находок `error-visible` в `replay_b.txt` |
| 5 | Первое сохранение 409 "plan version is required" | нет (только предупреждение `conflict`) | нет | детектора `conflict` в выводах нет |
| 6 | Тайминг порядка карточек | нет | нет | не реплицирована |

Замечание: replay c (удалён заголовок `X-AHAWR-Dashboard` в `board_dnd.js`,
`SCRATCH/proposal.txt`, пункт (c)) не относится к шести находкам. В `replay_c.txt` 0 находок
`missing-header`.

Итог репликации: во всех трёх прогонах `verdict` = `fail`, но ни одна находка не совпала с
ожидаемым детектором. Во всех трёх прогонах повторяются одни и те же ошибки 400 (см. раздел 3).

## 3. Время и стоимость

Время измерено командой `time` в `data-tables/ops/_t012a_crawls.sh` и записано в файлы `SCRATCH/clean_*.txt`.
Стоимость — поле `cost_usd` в вердикте каждого прогона.

| Фикстура | Язык | Время (`real`) | `minutes` в вердикте | `cost_usd` | Источник |
|---|---|---|---|---|---|
| default | en | 2m15.166s | 0.58 | 0.0 | `clean_default_en.txt` (строка `real`: 643) |
| default | ru | 0m59.125s | 0.49 | 0.0 | `clean_default_ru.txt` (строка 473) |
| empty | en | 5m3.758s | 4.6 | 0.0 | `clean_empty_en.txt` (строка 484) |
| empty | ru | 5m17.678s | 4.34 | 0.0 | `clean_empty_ru.txt` (строка 508) |
| default (replay a, b, c) | en | не записано | 1.23 / 1.23 / 1.24 | 0.0 / 0.0 / 0.0 | `replay_a.txt`, `replay_b.txt`, `replay_c.txt` |

Стоимость: 0 USD. Поле `"cost_usd": 0.0` есть во всех семи вердиктах (`clean_*.txt`, `replay_*.txt`).
По дизайну (`claude-runner/docs/qa-smoke.md`) краулер модель не вызывает; это не проверено кодом отдельно.
Разница между `real` и `minutes` (например, около 1 мин 40 с у default/en) в выводах не разложена.
Вероятно, это подъём и остановка стека, но этого не измерено. В empty/en `clean_empty_en.txt` есть девять
записей `click-failed` с `Locator.click: Timeout 15000ms exceeded`, то есть около 135 с ожидания таймаутов.

Сохранённые вердикты, все `"verdict": "fail"`: `clean_default_en.txt` 62 находки (4 severity `error`),
`clean_default_ru.txt` 45 (3), `clean_empty_en.txt` 46 (13), `clean_empty_ru.txt` 48 (15).

## 4. Результаты проверок (цитаты из сохранённых выводов)

Тесты, все изменённые файлы (`SCRATCH/final/pytest_all_touched.txt`):

```
FAILED tests/test_board.py::TestUnavailableStoreApi::test_status_is_503_with_a_readable_reason
FAILED tests/test_dashboard.py::test_dashboard_merges_claude_code_and_hermes
FAILED tests/test_dashboard.py::test_board_static_whitelist_pattern - assert ...
3 failed, 869 passed, 2 skipped, 1 warning in 226.64s (0:03:46)
```

Те же падения на HEAD (`SCRATCH/final/bis10_head_board_api_dashboard.txt`, строка 35):

```
FAILED tests/test_dashboard.py::test_dashboard_merges_claude_code_and_hermes
```

Тесты smoke (`SCRATCH/final/pytest_smoke.txt`): строка с точками и `exit=0`. Строки-итога с
числом тестов в файле нет, поэтому число тестов из этого файла не берётся.

Mypy сравнение (`SCRATCH/final/mypy_compare.txt`):

```
Totals: working 214 errors in 20 files; HEAD 229 errors in 22 files.
src/claude_runner/qa_smoke.py                1        —      +1 new
```

Mypy для новых тестов (`SCRATCH/final/mypy_new_tests.txt`):

```
test_qa_smoke | 0
test_qa_smoke_detect | 0
test_qa_smoke_integration | 0
test_qa_requests_api | 3
```

Проверка `git diff --check` (`SCRATCH/final/diff_check.txt`): `exit=0` (только предупреждения CRLF).

Node (`SCRATCH/final/node_review.txt`, строки 630–631):

```
# pass 104
# fail 0
```

Docker после прогонов (`docker ps -a --filter name=qa-` и сети/тома `qa-*`, команда в `data-tables/ops/_t012a_crawls.sh`; вывод в `SCRATCH/docker_ps_after.txt`, `SCRATCH/docker_ps_after_replay.txt` такой же):

```
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
NETWORK ID   NAME      DRIVER    SCOPE
DRIVER    VOLUME NAME
```

Контейнеров, сетей и томов `qa-*` не осталось: в выводе нет строк с данными.

Git после репликации (`SCRATCH/git_status_diff.txt`): `git status identical`.

Ruff: `SCRATCH/final/ruff_working.txt` — список всего дерева (`Found 70 errors.`,
`[*] 19 fixable with the `--fix` option.`); в нём нет ни одной строки для `qa_smoke.py`,
`qa_smoke_detect.py`, `qa.py` или `roles_config.py` (проверено `grep -c`, 0 совпадений),
т. е. в момент этого прогона ошибки в этих файлах не были. `ruff_head.txt` (70 errors) —
та же картина для HEAD. Итоговый прогон ruff по четырём файлам после последних правок
тестов в сохранённых выводах отсутствует: по этим четырём файлам ruff «не запущен»
в сохранённых выводах; это не противоречит `Found 70 errors.` — все 70 ошибок лежат в
других файлах (board*.py, test_board*.py, test_dashboard.py и т.п.).

## 5. Что не сделано и риски

- Критерий «чистый прогон на исправленном дереве» не выполнен: все четыре прогона `fail` (раздел 3).
- Три из шести находок не реплицированы (строки 2, 5, 6 таблицы раздела 2), поэтому по ним нет
  результата ни «найдено», ни «не найдено».
- Ожидаемые детекторы не сработали ни в одной из трёх репликаций (раздел 2). Детектор
  `missing-header` не входит в шесть находок; replay c проверяет его отдельно и находок не даёт.
- Краулер отправляет `POST /api/board/missions/create`, который отвечает 400 (`clean_default_en.txt`,
  `client-error`). Это побочный эффект на throwaway-стеке, он не разобран.
- Ошибки 400 на `/api/board/i18n/languages` (`clean_empty_en.txt`, `clean_empty_ru.txt`)
  не классифицированы.
- Mypy: `qa_smoke.py` даёт +1 (`Library stubs not installed for yaml`, `SCRATCH/final/mypy_compare.txt`).
  Ранний прогон `SCRATCH/final/mypy_now.txt` (09:02) показывал ошибки в тестах смоука, позднее
  прогон `mypy_new_tests.txt` (12:27) даёт 0. Прогон после правок тестов сохранён, поэтому берём позднее.
- Проверка, что воркер запускает смоук только при изменённых UI-файлах, в этом отчёте не выполнена.
  Есть `changed_files` (`qa_worker.py:135`, вызов `:161`), но полный путь не прогонялся.
- Не сделано по предложению `proposal.txt`: действие-затем-видимый-результат, принудительные
  отказы 409, проверка порядка по времени. Это предложение, код не написан.
- Документ `qa-smoke.md:6` устарел.
- Не измерено время репликаций (в `replay_*.txt` нет строки `real`).

## 6. Команды для оператора (не запускались)

```
cd /d/n8n/claude-runner && PYTHONPATH=src python -m claude_runner.qa smoke --fixture default --lang en
```
Поднимает throwaway-стек `qa-*`. Запускать только оператору.

Включение в роли (`roles_json`, `claude-runner/docs/qa-smoke.md:124`):
```
{"qa": {"1": "anthropic/sonnet", "options": {"mode": "final", "smoke": true}}}
```

Патч рабочего процесса (`data-tables/ops/qa_workflow_patch.py:20`), результат в файл, не импорт в n8n:
```
python data-tables/ops/qa_workflow_patch.py <qa_mode> <workflow.json> [layers_json] [smoke]
```

## 7. Версия и CHANGELOG (предложение)

- Сейчас: `claude-runner/pyproject.toml:7` = `0.9.0`; `claude-runner/src/claude_runner/api.py:181` = `0.9.0`;
  `CHANGELOG.md:6` (строка `Version: **0.9.0**`).
- Правило `CHANGELOG.md:6`: новые функции поднимают среднее число. Значит, `0.9.0` → `0.10.0`.
- `ROADMAP.md:113` уже называет релиз `0.10.0`, согласовано. Файлы не меняются в этом отчёте.

Предлагаемый блок для `CHANGELOG.md` (английский, как остальные записи файла):

```
## 2026-10-23 (0.10.0)

New function: the QA smoke crawl, a third QA layer that needs no per-mission scenarios.
Off by default (`roles_json` qa option `smoke`, default false).

- **Smoke crawl** (`python -m claude_runner.qa smoke`): opens the board, follows its links and
  clicks each control once within bounds (40 pages, 25 clicks per page, 10 minutes); refuses
  hosts other than 127.0.0.1, localhost and qa- containers; never clicks the destructive list in
  `claude-runner/qa/smoke_seeds.yaml`.
- Detectors: console and page errors, responses >= 400, null or undefined in URLs, missing
  dashboard header, bad text, hidden messages, overflow, raw i18n keys, no-effect clicks.
- Replay on copies: the null roles URL, the hidden message and the missing header were not
  reported by the crawl (see `reports/ahawr-qa-smoke-report-ru.md`).
```

Текст блока — предложение: оператор решает, включать ли его в релиз.

Открытые пункты (раздел 5) не закрываются этим релизом.
