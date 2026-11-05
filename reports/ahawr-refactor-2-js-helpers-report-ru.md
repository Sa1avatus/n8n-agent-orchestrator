# Отчёт миссии `ahawr-refactor-2-js-helpers` (T001–T015)

Отчёт — единственный в миссии. Факты: рабочий diff (`git diff HEAD -- claude-runner/src/claude_runner/board_*.js`), запуски `node --test`, `reports/audit-frontend.json` (HEAD и рабочая версия) и `reports/review-baseline.json`. Идентификаторы, пути и команды — дословно; пути относительные к репозиторию.

Оговорка о нумерации: текст плана миссии в репозиторий не попадает (`data-tables/ops/` не в git; `curl -s -m 10 -H "X-AHAWR-Dashboard: 1" http://127.0.0.1:8701/api/board/missions/ahawr-refactor-2-js-helpers` — пустой ответ; `ahawr-search` → «retrieval unavailable: the service cannot be reached»). В коде маркеров `T012`, `T013`, `T014` нет; шаги привязаны к фактическому diff и к маркерам `T001`, `T008`, `T009`, `T011` в тестах и комментариях.

## 1. Что изменилось по шагам (file:line)

### T001 — `esc`: одна каноническая функция
- Каноническая функция уже была в HEAD: `claude-runner/src/claude_runner/board_logic.js:23` (`function esc(value)`) — сама `board_logic.js` этой миссией не менялась (`git diff HEAD --stat` по ней пустой).
- Локальные копии заменены на делегирование `(typeof BoardLogic !== "undefined" ? BoardLogic : require("./board_logic.js")).esc`:
  `board_dnd.js:38`, `board_mission.js:35`, `board_mission_save.js:31`, `board_plan_edit.js:40`, `board_qa.js:28`, `board_queue.js:32`, `board_task.js:35`, `board_tasks.js:36`;
  алиасы (имя сохранено, чтобы не менять вызовы внутри файла): `board_claude_md.js:29` (`claudeMdEsc`), `board_history_ui.js:41` (`historyEsc`), `board_ready_ui.js:43` (`readyEsc`), `board_think_ui.js:64` (`thinkEsc`).
- `board_actions.js:28` (`function esc(v)`) оставлена намеренно — см. раздел 4.
- Тест: `claude-runner/tests/board_esc.test.js:1` («the T001 list»).

### T002 — `fetchJson`: одна каноническая функция
- Каноническая: `claude-runner/src/claude_runner/board_actions.js:49` (`async function fetchJson(url, options)`); добавлена в экспорт-блок `BoardActions` (`board_actions.js:458`).
- Делегирование на каноническую: `board_history_ui.js:70-71`, `board_mission.js:854-855`, `board_mission_save.js:594-595`, `board_plan_edit.js:196-197`, `board_ready_ui.js:204-205`.
- Сохранённое исключение: `board_dnd.js:50` (`async function fetchJson(url)`) — другая обработка ошибки (текст из `body.error`, без `.message`).
- Тест: `claude-runner/tests/fetch_json.test.js:1` («Characterization tests for the fetchJson helper in board*.js (T001): the single definition in board_actions.js, the kept exception in board_dnd.js»).

### T003–T007 — делегирование по отдельным модулям
Шаги T001/T002 применены к файлам по одному; следы в diff по объёму удалённых копий: `board_claude_md.js` (18 строк), `board_dnd.js` (15), `board_history_ui.js` (33), `board_mission.js` (42), `board_mission_save.js` (34), `board_plan_edit.js` (33), `board_qa.js` (15), `board_queue.js` (16), `board_ready_ui.js` (33), `board_task.js` (16), `board_tasks.js` (16), `board_think_ui.js` (18). Маркеров шага в коде нет — привязка по файлу и символу.

### T008–T009 — таймеры: остановка при уходе со страницы
- `claude-runner/src/claude_runner/board_actions.js`: `let refreshTimer = null;` :46; `startAutoRefresh()` :390-393 (защита :391, `setInterval(tick, 10000)` :392, `wireTeardown()` :393); `stopAutoRefresh()` :398-401; комментарий `T009` :405-408; wiring :409-422 (`pagehide` :413, `hashchange` :414: перезапуск при возврате на экран очереди, остановка при уходе).
- `claude-runner/src/claude_runner/board_views.js`: `let tickTimer = null;` :28, `let teardownWired = false;` :30; `if (!tickTimer) tickTimer = setInterval(tick, REFRESH_MS);` :393 (в HEAD был «голый» `setInterval(tick, REFRESH_MS)` на :387 без остановки); комментарий `T009` :397-399; wiring :400-407 (`pagehide` :402, `hashchange` :403); `stopTick()` :412-417.
- Тест: `claude-runner/tests/board_tick_timers.test.js:1` («the "tick" polling timers (T008 of the review mission) plus the teardown added by T009»).

### T010 — заглушка `window` для тестов
- `claude-runner/tests/board_actions.test.js:91-93` (+3 строки): `addEventListener` на стабе `window` + `const windowListeners = {};` с комментарием «T009 teardown listeners (board_actions.js wireTeardown) register on this stub.».

### T011 — чистка неиспользуемых экспортов
- `board_mission.js`: экспорт-блок `const BoardMissionView = {` начинается с :857; из него убраны `presetButtons`, `fmtLines`, `fmtJson`, `greyNote`, `attachPresetButtons`, `FORM_ROWS`, `bindTasksView` — все они нужны внутри файла: `fmtLines` вызывается :183, `greyNote` :187, `presetButtons` :195, `bindTasksView` :691.
- `board_think_ui.js`: экспорт-блок :350-352 оставляет `render`, `renderHtml`, `renderInto`, `esc`, `MIN_ARM`; убраны `policyText` (:87), `policyFromText` (:98), `notEnoughText` (:112), `previewLayout` (:267), `loadStats` (:292), `thinkPost` (:67), `LAYOUT_PARALLEL` (:46) — внутренние вызовы остались: `policyText` :334, `previewLayout` :250, `loadStats` :258.
- `board_mission_save.js`: экспорт-блок :628 (строка блока — :631) не изменён: `ensurePanel` вызывается внутри :279, :290, :327, :429; `showBanner`, `attachControls`, `copyIntoForm`, `fetchServerData` тоже используются в том же файле (см. раздел 4).
- Тесты: `claude-runner/tests/board_mission_exports_stay.test.js:2` и `claude-runner/tests/board_think_ui_exports_stay.test.js:2` («T011, step 1»).

### T012–T014 — пересчёт аудита и закрепление результатов
- `reports/audit-frontend.json` перезаписан живым прогоном: `generated_at` `2026-11-03T22:15:11.726728+00:00`, `summary.total` 35 (в HEAD было 53, `2026-11-03T13:49:38.603912+00:00`).
- Новые закрепляющие тесты: `tests/board_esc.test.js`, `tests/fetch_json.test.js`, `tests/board_tick_timers.test.js`, `tests/board_mission_exports_stay.test.js`, `tests/board_think_ui_exports_stay.test.js` (все — untracked, созданы миссией).
- Маркеров `T012`/`T013`/`T014` в коде нет; привязка — по diff и по обновлённому аудиту.

## 2. Проверки, которые реально запускались

Из `claude-runner/`:

| Команда | Ключевые строки вывода |
|---|---|
| `node --test tests/board_esc.test.js tests/fetch_json.test.js tests/board_tick_timers.test.js tests/board_mission_exports_stay.test.js tests/board_think_ui_exports_stay.test.js` | `# tests 21` / `# pass 21` / `# fail 0` |
| `node --test tests/board_views.test.js` | `# tests 8` / `# pass 8` / `# fail 0` |
| `node --test tests/board_actions.test.js` | `# tests 1` / `# pass 1` / `# fail 0` |
| `node --test tests/*.test.js` (весь набор) | `# tests 528` / `# pass 528` / `# fail 0` |
| `python -m ruff check --output-format concise src/claude_runner` | `Found 98 errors.` |

ruff/mypy по `reports/review-baseline.json`: **до** 98 и 128; **после** 98 (тот же прогон выше) и 128 — mypy не перезапускался (см. раздел 3), поэтому счётчик остался базовым. В `ruff_by_file` нет ни одного ключа `.js`: миссия меняла только `*.js`, поэтому количество ошибок ruff не изменилось (крупнейшие файлы — `board_api.py` 28, `board_pg.py` 26, `remote_manager.py` 12, `board_think.py` 9 — все не тронуты).

Пересчёт аудита — `reports/audit-frontend.json`, HEAD против рабочего дерева:

| Показатель | HEAD (13:49:38) | рабочий прогон (22:15:11) |
|---|---|---|
| всего находок | 53 | 35 |
| `duplicate_helper` (записей) | 12 | 12 |
| мест дублирования | 51 | 38 |
| `unused_export` | 38 | 22 |
| `timer_without_clear` | 3 | 1 |
| `esc` (мест) | 12 | 4 |
| `fetchJson` (мест) | 8 | 3 |
| `L` (мест) | 9 | 9 |
| таймеры | `board_actions.js:392`, `board_views.js:387`, `dashboard.html:1483` | только `dashboard.html:1483` |

## 3. Проверки, которые не запускались, и почему

- `python -m mypy` — Python-файлы миссии не меняла; строго-прогон по не затронутым модулям (`src/claude_runner/board_api.py` и т. п.) дольше 100 секунд и вне области этой миссии. Базовые 128 ошибок взяты из `reports/review-baseline.json`.
- `pytest tests/test_board_js_static.py` — не даётся из-за предсуществующего провала: `claude-runner/tests/test_board_js_static.py:85` — `pytest.fail(f"unescaped interpolation {frag} in a tag string")`, нарушитель `claude-runner/src/claude_runner/board_guards.js:174` (файл этой миссией не менялся).
- `node --test tests/board_dnd.test.js` — такого файла нет: тестов для `board_dnd.js` в `claude-runner/tests/` не существует (есть только `board_esc.test.js`, `fetch_json.test.js`, `board_views.test.js`, `board_actions.test.js`, `board_tick_timers.test.js`, два `*_exports_stay.test.js`).
- Тесты паритета i18n и проверки заголовка `X-AHAWR-Dashboard: 1` не повторялись: переводы и заголовки миссия не меняла.
- Эндпоинд плана миссии не отвечает (команда выше), поэтому сверить формулировки T012–T014 по плану нечем.

## 4. Что не тронуто и почему

- `claude-runner/src/claude_runner/board_dnd.js:50` — своя копия `fetchJson`: отличается обработка ошибки (`body.error` без `.message`); объединение изменило бы текст сообщения пользователю.
- `claude-runner/src/claude_runner/board_actions.js:28` — копия `esc` оставлена: `board_actions.js` сама является носителем общих helper'ов (`fetchJson`, `postJson`, `loadBoardData`), делегирование на `board_logic.js` дало бы встречную зависимость.
- `claude-runner/src/claude_runner/dashboard.html:496` (`esc`) и `dashboard.html:1492` (`fetchJson`) — инлайн-копии страницы dashboard: `board_logic.js` на этой странице не загружается (`grep -n "board_logic.js" claude-runner/src/claude_runner/dashboard.html` — 0 совпадений), делегировать некуда.
- `claude-runner/src/claude_runner/dashboard.html:1483` — `setInterval(loadRuns, 3000)`: таймер страницы dashboard, общего teardown-хоста у неё нет, в node-тестах не проверяется.
- Экспорты, которые остались: `BoardMissionSave.ensurePanel` (вызовы `board_mission_save.js:279`, `:290`, `:327`, `:429`), `showBanner`, `attachControls`, `copyIntoForm`, `fetchServerData` (употребления в том же файле и в `board_history_ui.js`, `board_ready_ui.js`); `BoardDnd.applyMove`/`moveByStep`/`moveInData`/`postMoveQueue`, `BoardEditWin.closeWin`, `BoardHistoryUI.rollbackRequest`, `BoardRoles.validateRoles`, `BoardViews.DEFAULT_VIEW`/`SUMMARY_URL` — используются другими модулями и страницами, удаление сломало бы UI.
- `claude-runner/src/claude_runner/board_lessons_ui.js:34` (алиас `esc`) — файл миссии lessons, к этой чистке не относится.

## 5. Откат каждого шага

Коммитов миссия не делала (правила репозитория запрещают `git commit`), поэтому откат — возврат рабочего дерева:

- T001: `git checkout HEAD -- claude-runner/src/claude_runner/board_dnd.js claude-runner/src/claude_runner/board_mission.js claude-runner/src/claude_runner/board_mission_save.js claude-runner/src/claude_runner/board_plan_edit.js claude-runner/src/claude_runner/board_qa.js claude-runner/src/claude_runner/board_queue.js claude-runner/src/claude_runner/board_task.js claude-runner/src/claude_runner/board_tasks.js claude-runner/src/claude_runner/board_claude_md.js claude-runner/src/claude_runner/board_history_ui.js claude-runner/src/claude_runner/board_ready_ui.js claude-runner/src/claude_runner/board_think_ui.js`
- T002: `git checkout HEAD -- claude-runner/src/claude_runner/board_actions.js` (вернётся и экспорт `fetchJson` на :458) плюс `git checkout HEAD -- claude-runner/src/claude_runner/board_history_ui.js claude-runner/src/claude_runner/board_mission.js claude-runner/src/claude_runner/board_mission_save.js claude-runner/src/claude_runner/board_plan_edit.js claude-runner/src/claude_runner/board_ready_ui.js`
- T003–T007: тот же возврат файлов из T001/T002.
- T008–T009: `git checkout HEAD -- claude-runner/src/claude_runner/board_actions.js claude-runner/src/claude_runner/board_views.js`
- T010: `git checkout HEAD -- claude-runner/tests/board_actions.test.js`
- T011: `git checkout HEAD -- claude-runner/src/claude_runner/board_mission.js claude-runner/src/claude_runner/board_think_ui.js`
- T012–T014: `rm claude-runner/tests/board_esc.test.js claude-runner/tests/fetch_json.test.js claude-runner/tests/board_tick_timers.test.js claude-runner/tests/board_mission_exports_stay.test.js claude-runner/tests/board_think_ui_exports_stay.test.js` и `git checkout HEAD -- reports/audit-frontend.json`
- T015: `rm reports/ahawr-refactor-2-js-helpers-report-ru.md`

## 6. Список следующей миссии (по убыванию ранга из `reports/audit-frontend.json`)

**`duplicate_helper` — 12 записей, 38 мест:**
1. `L` (rank −9, 9 мест): `board_edit_win.js:109`, `board_history_ui.js:29`, `board_plan_edit.js:28`, `board_providers_view.js:23`, `board_qa.js:17`, `board_ready_ui.js:31`, `board_task.js:23`, `board_tasks.js:24`, `board_think_ui.js:30`.
2. `esc` (−4): `board_actions.js:28`, `board_lessons_ui.js:34`, `board_logic.js:23` (каноническая), `dashboard.html:496`.
3. `fetchJson` (−3): `board_actions.js:49` (каноническая), `board_dnd.js:50`, `dashboard.html:1492`.
4. `postJson` (−3): `board_actions.js:75`, `board_mission.js:536`, `board_mission_save.js:600`.
5. `isPlainObject` (−3): `board_features.js:16`, `board_overrides.js:32`, `board_retrieval.js:29`.
6. `stateOf` (−3): `board_view_grid.js:25`, `board_view_kanban.js:36`, `board_view_projects.js:18`.
7. `taskCounts` (−3): `board_view_grid.js:29`, `board_view_kanban.js:40`, `board_view_projects.js:41`.
8. `loadBoardData` (−2): `board_actions.js:63`, `board_dnd.js:63`.
9. `fmtTokens` (−2): `board_queue.js:44`, `board_tasks.js:39`.
10. `fmtPercent` (−2): `board_queue.js:70`, `board_tasks.js:54`.
11. `numberOf` (−2): `board_view_grid.js:35`, `board_view_sprint.js:22`.
12. `projectName` (−2): `board_view_grid.js:57`, `board_view_sprint.js:32`.

**`unused_export` — 22 (строка — линия экспорт-блока файла):**
`BoardActions.confirmActions`, `BoardActions.createMissionForm` (`board_actions.js:458`); `BoardClaudeMdView.storageLine`, `BoardClaudeMdView.editorHtml` (`board_claude_md.js:200`); `BoardDnd.applyMove`, `BoardDnd.moveByStep`, `BoardDnd.moveInData`, `BoardDnd.postMoveQueue` (`board_dnd.js:404`); `BoardEditWin.closeWin` (`board_edit_win.js:1309`); `BoardGuardsView.STRIP_LENGTH`, `BoardGuardsView.FLOW` (`board_guards_view.js:156`); `BoardHistoryUI.rollbackRequest` (`board_history_ui.js:211`); `BoardMissionSave.ensurePanel`, `showBanner`, `attachControls`, `copyIntoForm`, `fetchServerData` (`board_mission_save.js:628`); `BoardReadyUI.startRequest` (`board_ready_ui.js:208`); `BoardRoles.validateRoles` (`board_roles.js:652`); `BoardViewLanes.boardTasks` (`board_view_lanes.js:360`); `BoardViews.DEFAULT_VIEW`, `BoardViews.SUMMARY_URL` (`board_views.js:419`).

**`timer_without_clear` — 1:**
`claude-runner/src/claude_runner/dashboard.html:1483` — `setInterval(loadRuns, 3000)`, `missing_clear: "clearInterval(loadRuns)"`, `page_teardown_wired: false`, proposed fix — `clearInterval(loadRuns)` на `pagehide`.

## 7. Конфликт области (scope conflict)

`RULES 1` и `ACCEPTANCE CRITERIA 1-7` этой задачи описывают **lessons-функцию**, а не чистку JS: в них требуются `board_lessons.py`, `data-tables/ops/lessons_patch.py` и `claude-runner/docs/lessons.md` (правила извлечения, K-отбор, красные лиметы, флаги). Причина — наследование правил: `data-tables/ops/_make_refactor_missions.py:10` — `SOURCE = "ahawr-lessons-library"  # a finished AHAWR mission: name, rules, criteria and folder are replaced below`, то есть RULES/ACCEPT скопированы из `data-tables/ops/_make_lessons_mission.py` (`RULES = [` :28, `ACCEPT = [` :37, пункты про lessons — :38, :39, :42).

Миссия `ahawr-refactor-2-js-helpers` делала только чистку: общий `esc`/`fetchJson`, teardown таймеров, обрезка неиспользуемых экспортов, пересчёт аудита. Lessons **не строились**: артефакты `claude-runner/src/claude_runner/board_lessons.py`, `board_lessons_extract.py`, `board_lessons_ui.js`, `claude-runner/docs/lessons.md`, `data-tables/ops/lessons_patch.py` уже существуют в репозитории (импорты — `claude-runner/src/claude_runner/board_api.py:57-58`), и в diff этой миссии их нет.

Предложение: вынести lessons-часть в отдельную миссию (`ahawr-lessons-*`) с собственными RULES/ACCEPT, а для `ahawr-refactor-2-js-helpers` исправить RULES и ACCEPTANCE CRITERIA на фактическую область (shared helpers / unused exports / timers).

## Итоговая проверка этой задачи

- `ls reports/ahawr-refactor-2-js-helpers-report-ru.md` — файл существует.
- `git diff --check` — чисто (exit 0; только предупреждения о CRLF для файлов, которых эта задача не касалась).
- `git status --short` — отчёт появляется как единственное добавление этой задачи: `?? reports/ahawr-refactor-2-js-helpers-report-ru.md` (остальные ` M `-строки — работа T001–T014 и чужие правки).
- Spot-чек трёх file:line: `claude-runner/src/claude_runner/board_tasks.js:36` (делегирование `esc`), `claude-runner/src/claude_runner/board_actions.js:49` и `board_dnd.js:50` (определения `fetchJson`), `claude-runner/src/claude_runner/dashboard.html:1483` (`setInterval(loadRuns, 3000)`); дополнительно `board_mission.js:536` и `board_mission_save.js:600` (`postJson`).
- Секретов, значений окружения и абсолютных путей хоста в отчёте нет: `grep -c "/d/n8n"` — 0; проверка на `.env`, `printenv`, `/home/`, `C:\` даёт единственное совпадение — строку 139, где сама проверка процитирована.

**Не сделано / риски:** привязка T012–T014 опирается на diff (маркеров в коде нет, план недоступен); `board_guards.js:174` и таймер `dashboard.html:1483` остаются без тестового покрытия; mypy не перезапускался (см. раздел 3); список раздела 6 взят из `reports/audit-frontend.json`: все 38 мест `duplicate_helper` сверены grep'ом по определениям (`L`, `isPlainObject`, `stateOf`, `taskCounts`, `numberOf`, `projectName`, `fmtTokens`, `fmtPercent`, `loadBoardData`, `postJson` совпали с указанными строками), 11 строк `unused_export` — это строки экспорт-блоков файлов (`const BoardX = {`), таймер подтверждён строкой 1483.
