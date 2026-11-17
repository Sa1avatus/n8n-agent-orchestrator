# Инвентаризация миссии `ahawr-mission-dependencies` (части 1–3)

Инвентаризация перед частью 4. Код не менялся; все артефакты найдены `grep`/`ls` (чтение).

## Состояние перед частью 4

1. **Критерий 1 — `depends_on` хранится и читается через методы store, API показывает, при выключенном флаге ответы не меняются** — найдено: `grep -cE '^(async )?def test_' ahawr-runner/tests/test_mission_dependencies.py` → 79 тестов, ключевые строки `:254 async def test_flag_on_mission_answer_carries_dependency_state(` и `:33 FLAG_OFF_JSON = '{"mission_dependencies": {"enabled": false}}'` (пин флага OFF); `grep -c depends_on ahawr-runner/tests/test_board_pg.py` → 27 совпадений, `:129 if "UPDATE queue SET depends_on" in sql:`, `:149 if "SELECT depends_on FROM queue" in sql:`; флаг `grep -n "mission_dependencies" ahawr-runner/src/ahawr_runner/features_defaults.json` → `29:    "mission_dependencies": {`, `30:      "enabled": false`.
2. **Критерий 2 — `next_mission` пропускает entry с неудовлетворённой зависимостью, failed → blocked, циклы и неизвестные id отвергаются** — найдено: `grep -n "depend" ahawr-runner/tests/test_queue_board.py` → `:137 def _dependency_rule() -> ModuleType:`, `:139 rule: ModuleType = queue_board._shared_dependency_rule()`, `:157 def test_flag_on_skips_an_entry_with_unmet_dependency_and_picks_the_next_due_one()`; само правило — `grep -n "_shared_dependency_rule\|def next_mission" data-tables/ops/queue_board.py` → `:195 def _shared_dependency_rule()` и `:233 def next_mission(`; состояния — `grep -n "DependencyStateName" ahawr-runner/src/ahawr_runner/board_dependencies.py` → `:16 DependencyStateName = Literal["due", "waiting", "blocked"`, `:165 def classify_queue(`.
3. **Критерий 3 — патч split даёт части N зависимость от N-1, идемпотентен, синтаксис проверен на копии экспорта** — найдено: `grep -n "MARKER" data-tables/ops/split_dependencies_patch.py` → `:22 MARKER = "// split parts wait for the part before (split_dependencies_patch.py)"`, `:37 if MARKER in code:` (процедурная проверка идемпотентности); тесты `grep -cE '^(async )?def test_' ahawr-runner/tests/test_split_dependencies.py` → 9, `:107 def test_split_dependencies_part_chain_runs_the_patched_node_code()`, `:142 def test_split_dependencies_patch_applied_twice_gives_the_same_bytes`, `:120 def test_split_dependencies_patch_on_an_export_copy_adds_the_part_line`.
4. **Критерий 4 — очередь и kanban показывают `after: ids` и чипы, редактор валидирует, drop раньше зависимости отвергается, ключи EN/RU совпадают** — найдено: `grep -n "after: " ahawr-runner/tests/board_mission_dependencies.test.js` → `:51 test("the line renders 'after: a, b' for depends_on a,b", () => {`; паритет ключей — `:613 test("every t() key used by the module exists in both language tables", () => {` (`grep -c '^test(' ...` → 29); ключи `grep -n "q.dep" .../board_i18n_en.json` → `14:"q.dep":"after: {ids}","q.dep.waiting":"waiting for {ids}","q.dep.blocked":"blocked by {ids}: a dependency is not completed"`, `grep -n "q.dep" .../board_i18n_ru.json` → `14:"q.dep":"после: {ids}","q.dep.waiting":"ждёт: {ids}","q.dep.blocked":"заблокирована: {ids} не выполнена"`; в `BUNDLED_EN` модуля `board_i18n.js` — `grep -n "q.dep" .../board_i18n.js` → строки `:277`–`:282`.
5. **Критерий 5 — при включённом флаге в input Architect есть блок зависимостей (с ограничением) и правило, при выключенном промпт байт-в-байт прежний, линт отвергает план части с задачей из более поздней части** — найдено: `grep -n "MARKER" data-tables/ops/architect_prompt_dependency_block.py` → `:26 MARKER = "// architect_prompt_dependency_block.py"`, `:74 if MARKER in code:`; линт — `grep -n "architect_prompt_dependency_block" data-tables/ops/plan_dependency_lint_patch.py` → `:9 only when the flag is on (architect_prompt_dependency_block.py)`; тесты `grep -n '^def test_' ahawr-runner/tests/test_architect_dependencies.py` → `:44 def test_architect_dependencies_block_has_heading_ids_names_states_and_rule()` (`grep -cE '^(async )?def test_' ahawr-runner/tests/test_architect_dependencies.py` → 27).
6. **Критерий 6 — ruff и mypy без новых ошибок для тронутых файлов, `git diff --check` чист, CHANGELOG и ROADMAP обновлены** — найдено: `grep -n -i "depend" CHANGELOG.md` → `58:- \`docs/mission-dependencies.md\` is the final note for the mission dependency work: the \`depends_on\``; `grep -n -i "depend" ROADMAP.md` → `184:- [x] 1.1.300: bug fixes: the four QA scenarios that failed \`ahawr-mission-dependencies-p2\` (Kanban moves, Grid sort, Features panel); queue moves of a waiting mission without a queue row`; цифры проверок — `grep -n "Total on the six baseline files\|Target files unchanged" reports/ahawr-mission-dependencies-checks.txt` → `:58 Total on the six baseline files: 60 before / 60 now - unchanged.` и `:80 Target files unchanged: 70 before / 70 now.`; `git diff --check` в этой инвентаризации: 0 строк-ошибок (только предупреждения LF→CRLF на не тронутых файлах).

### Блокеры, найденные при инвентаризации

- **Отчёты отдельных частей (p1–p3) не найдены.** Проверка: `ls -1 reports/ | grep -i 'ahawr-mission-dependencies'` → только `ahawr-mission-dependencies-baseline.txt`, `ahawr-mission-dependencies-checks.txt`, `ahawr-mission-dependencies-report-ru.md` (244 строки, итоговый сводный отчёт). Ни одного файла вида `...-p1-report-ru.md` / `p2` / `p3` в `reports/` нет — ничего не восстанавливается, факт зафиксирован здесь.
- **Проверка 5 (architect) не зелёная.** Из раздела 3 итогового отчёта: `python -m pytest tests/ -q --no-cov -k architect_dependencies` → exit 1, 32 совпало, 25 прошли, 7 FAILED — все из `tests/test_architect_dependencies.py`, потому что staged-экспорт уже пропатчен. Проверка сейчас: `grep -c "architect_prompt_dependency_block.py" data-tables/ops/wf_next.json` → 1 (маркер в экспорте есть). Для части 4 нужен свежий непропатченный экспорт живого workflow.
- **Флаг `mission_dependencies` ещё OFF** (`features_defaults.json:30 "enabled": false`): поведение зависимостей в живом интерфейсе не активно; патчи написаны, но в n8n не импортированы (раздел 6 итогового отчёта: шаги оператора ждут окна с приостановленной очередью).
- **Расхождение ops-файла с заметкой миссии** (из раздела 6 итогового отчёта): `data-tables/ops/launch_mission.sh` уже содержит команды этих патчей и импортирует `wf_next.json`; оператору нужно подтвердить состояние живого workflow перед частью 4.

## Что не менялось

Ни один файл кода, тестов, конфига или `data-tables/ops/` не тронут. Единственный созданный файл — этот отчёт: `reports/ahawr-mission-dependencies-p4-report-ru.md`.

## Проверки 1-2

Запущены из `ahawr-runner/` в venv `/tmp/venv` (ссылка на `/opt/ahawr-runner/venv`, pytest 9.2.0) с `PYTHONPATH=src`. Код не менялся.

1. `python -m pytest tests/test_board_pg.py tests/test_mission_dependencies.py -q --no-cov -k depend`
   - Итог: `92 passed, 43 deselected, 1 warning in 20.18s`, exit 0.
   - Падений нет. В `-q` режиме строка-счётчик не печатается: 92 точек без `F` (проверено `tail` лог-файла). Предупреждение единственное — StarletteDeprecationWarning про `httpx` в `fastapi/testclient.py`, не тестовое падение.

2. `python -m pytest tests/test_queue_board.py tests/test_mission_dependencies.py -q --no-cov`
   - Итог: `123 passed, 1 warning in 21.23s`, exit 0.
   - Падений нет (`падений нет`).

`known host failure` — ни одного: обе команды завершились с exit 0, POSIX-специфичных (`killpg`, фейковый CLI) тестов в этих двух файлах нет (`grep -n "killpg\|fake_cli\|subprocess" tests/test_queue_board.py tests/test_mission_dependencies.py` → 0 совпадений).

## Проверки 3 и 5

Запущены из `ahawr-runner/` (python = `/opt/ahawr-runner/venv/bin/python`, pytest 9.2.0). Код, тесты и патчи не менялись. В `-q` режиме pytest 9.2.0 не печатает строку-итог (`N passed, N deselected`), поэтому число отобранных тестов проверялось отдельно через `--collect-only`, а падения — по символам `F` и блокам `FAILED`.

3. `python -m pytest tests/ -q --no-cov -k split_dependencies`
   - Как записано: **тесты не найдены** (0 selected), exit 2 — сборка прервана, не тестовое падение:
   `ERROR collecting tests/test_qa_container_static.py ... ModuleNotFoundError: No module named 'yaml'` и `!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!`.
   Причина: `pyyaml` не установлен в venv (`python -c "import yaml"` → `ModuleNotFoundError: No module named 'yaml'`); `tests/test_qa_container_static.py` — единственный модуль, который импортирует `yaml`.
   - Повтор той же команды с `--ignore=tests/test_qa_container_static.py`: exit 0, отобрано 9 тестов (`--collect-only` → `9/3676 tests collected (3667 deselected) in 9.12s`), падений нет: `.........                                                                [100%]` (9 точек, ни одного `F`). Все 9 — из `tests/test_split_dependencies.py` (`grep -nE "^(async )?def test_.*split_dependencies" tests/*.py` → 9).

5. `python -m pytest tests/ -q --no-cov -k architect_dependencies`
   - Как записано: **тесты не найдены** (0 selected), exit 2 — тот же блокер `yaml` при сборке (`Interrupted: 1 error during collection`).
   - Повтор с `--ignore=tests/test_qa_container_static.py`: exit 1, отобрано 32 (`--collect-only` → `32/3676 tests collected (3644 deselected) in 8.27s`): 25 прошли, 7 FAILED — `........FF.....FFFFF............                                         [100%]`.
   - Состав отобранных 32: 27 из `tests/test_architect_dependencies.py` (5 линт-тестов `..._lint_*` — все прошли), 4 из `tests/test_board_dependencies.py` (`:161`, `:183`, `:189`, `:283`), 1 из `tests/test_mission_dependencies.py` (`:275 test_flag_on_dependency_context_architect_dependencies_block`).
   - 7 падающих тестов, у всех одна причина: staged-экспорт `data-tables/ops/wf_next.json` уже содержит блок зависимостей (`grep -c "architect_prompt_dependency_block.py" data-tables/ops/wf_next.json` → 1), поэтому патч идемпотентен и возвращает `False` («already patched»), а тесты ожидают первого применения:
     1. `tests/test_architect_dependencies.py::test_architect_dependencies_prompt_patch_applied_twice_leaves_the_same_bytes` — `assert 'architect dependency block: patched' in 'architect dependency block: already patched\n'`
     2. `::test_architect_dependencies_prompt_patch_never_writes_the_export_it_reads` — `assert ARCHITECT_PATCH.patch(workflow) is True` → `assert False is True`
     3. `::test_architect_dependencies_flag_off_prompt_is_byte_for_byte_the_unpatched_prompt` — `assert False is True`
     4. `::test_architect_dependencies_flag_on_plan_prompt_carries_the_block_and_the_rule` — `assert False is True`
     5. `::test_architect_dependencies_flag_on_split_prompt_carries_the_part_order_rule` — `assert False is True`
     6. `::test_architect_dependencies_board_fetch_failure_leaves_the_prompt_unchanged` — `assert False is True`
     7. `::test_architect_dependencies_flag_on_with_no_dependencies_adds_no_block` — `assert False is True`
   - Итог: проверка 5 не зелёная (7 падений из-за уже пропатченного staged-экспорта); для части 4 нужен свежий непропатченный экспорт живого workflow. Ни один тест или патч в этой проверке не менялся.

## Проверка 4

Команда запущена из `ahawr-runner/` (node v22.23.5), без изменений кода:

```
node --test tests/board_mission_dependencies.test.js tests/board_i18n.test.js
```

Строки TAP-сводки (exit 0):

```
# tests 30
# suites 0
# pass 30
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 323.693363
```

- `# pass 30`, `# fail 0`.
- Падающих тестов нет, поэтому перечень имений падений пуст: ни один тест не провалился, ничего не чинилось.
- 29 тестов — из `board_mission_dependencies.test.js` (`grep -c '^test(' tests/board_mission_dependencies.test.js` → 29). `board_i18n.test.js` — самопроверяющийся скрипт без `test(...)` (`grep -n "test(" tests/board_i18n.test.js` → 0 совпадений): он напечатал `# board_i18n.test.js: all checks passed` и был посчитан node как один тест (`ok 1 - tests/board_i18n.test.js`).
- Рамки проверок паритета внутри этих файлов прошли: `tests/board_mission_dependencies.test.js:613 test("every t() key used by the module exists in both language tables")` → `ok 30`; `tests/board_i18n.test.js:140 check("bundled table matches the JSON file", ...)` → в сводке падения нет, значит `BUNDLED_EN` в `board_i18n.js` равен `board_i18n_en.json`.

Ключи зависимостей, grep по трём местам (`board_i18n_en.json`, `board_i18n_ru.json`, `BUNDLED_EN` в `board_i18n.js`, объект открывается на `board_i18n.js:19`):

| Ключ | EN | RU | BUNDLED_EN (`board_i18n.js`) |
|---|---|---|---|
| `after:` — `q.dep` | `:14` | `:14` | `:277` |
| `waiting` — `q.dep.waiting` | `:14` | `:14` | `:279` |
| `blocked` — `q.dep.blocked` | `:14` | `:14` | `:280` |
| отказ drop — `dep.order` | `:14` | `:14` | `:287` |
| `move them too` — `dep.group`, `btn.dep.group` | `:14` | `:14` | `:289`, `:291` |
| `run anyway` — `dep.anyway`, `btn.dep.anyway` | `:14` | `:14` | `:290`, `:293` |

Ключевые строки grep (по одному совпадению в каждом файле, все шесть ключей в одной строке):

```
board_i18n_en.json:14 "q.dep":"after: {ids}","q.dep.waiting":"waiting for {ids}","q.dep.blocked":"blocked by {ids}: a dependency is not completed","dep.order":"The move is refused: {id} must run after {ids}","dep.group":"Do you want to move them too? {ids} ({n}) depend on it.","dep.anyway":"The dependencies are not completed: run anyway?","btn.dep.group":"Move them too","btn.dep.anyway":"Run anyway"
board_i18n_ru.json:14 "q.dep":"после: {ids}","q.dep.waiting":"ждёт: {ids}","q.dep.blocked":"заблокирована: {ids} не выполнена","dep.order":"Перемещение отклонено: {id} должна идти после {ids}","dep.group":"Переместить и зависящие от неё: {ids} ({n})?","dep.anyway":"Зависимости не выполнены: запустить всё равно?","btn.dep.group":"Переместить и их","btn.dep.anyway":"Запустить всё равно"
board_i18n.js:277   "q.dep": "after: {ids}",
board_i18n.js:279   "q.dep.waiting": "waiting for {ids}",
board_i18n.js:280   "q.dep.blocked": "blocked by {ids}: a dependency is not completed",
board_i18n.js:287   "dep.order": "The move is refused: {id} must run after {ids}",
board_i18n.js:289   "dep.group": "Do you want to move them too? {ids} ({n}) depend on it.",
board_i18n.js:290   "dep.anyway": "The dependencies are not completed: run anyway?",
board_i18n.js:291   "btn.dep.group": "Move them too",
board_i18n.js:293   "btn.dep.anyway": "Run anyway",
```

- Ключей, отсутствующих хотя бы в одном из трёх мест, не найдено: 0. Дополнительно те же три файла содержат `q.dep.none`, `q.dep.waiting.title`, `q.dep.blocked.title`, `m.dep`, `m.dep.hint`, `dep.missing`, `dep.cycle`, `dep.start`, `btn.dep.only` — тоже во всех трёх местах (`grep -nE '"(dep\.|btn\.dep|m\.dep)' src/ahawr_runner/board_i18n.js` → 12 совпадений, строки 283–293 и 324; `dep.add` — `en=1 ru=1 js=1`).
- Ни один файл кода или тестов в этой проверке не изменён; изменён только этот отчёт (добавлен раздел «Проверка 4»).

## Исправления Python

В «Проверках 1-2» падений нет. В «Проверках 3 и 5» записаны две группы падений, но ни одна не исправляется в разрешённых для этой задачи файлах (`ahawr-runner/src/ahawr_runner/board*.py` или `queue_board.py` либо их тесты; единственный разрешённый тестовый файл — `ahawr-runner/tests/test_mission_dependencies.py`): причины обеих групп лежат вне этих путей — в окружении venv и в staged-экспорте `data-tables/ops/`. Поэтому ни один файл `ahawr-runner/` не изменён (из двух разрешённых — 0 правок); ни один тест не удалён, не пропущен и не ослаблен.

Повторная проверка в текущем venv (`/tmp/venv` → `/opt/ahawr-runner/venv`, pytest 9.2.0, `PYTHONPATH=src`, из `ahawr-runner/`):
- 7 падющих id тестов из проверки 5: `python -m pytest <7 id> -q --no-cov` → exit 1, `short test summary info` содержит ровно те же `FAILED tests/test_architect_dependencies.py::...` (7 строк) — падение воспроизводится;
- команда проверки 3 без `--ignore`: exit 2, сборка прервана — `ERROR collecting tests/test_qa_container_static.py ... ModuleNotFoundError: No module named 'yaml'`;
- с `--ignore=tests/test_qa_container_static.py`: `-k split_dependencies` → exit 0 (9 тестов, ни одного `F`); проверки 1-2 (`-k depend` и полный `test_queue_board.py`+`test_mission_dependencies.py`) → exit 0, 0 символов `F`.

### Открытые риски

1. **Сборка прервана (проверки 3 и 5, exit 2).** `tests/test_qa_container_static.py:7 import yaml` — в venv нет `pyyaml` (`python -c "import yaml"` → `ModuleNotFoundError`). Причина падения — окружение, а не код `ahawr_runner`. Исправление вне рамок задачи: установка `pyyaml` в venv (новая runtime-зависимость — требуется оператор) или правка `tests/test_qa_container_static.py`, которая не входит в разрешённые файлы. Сами 9 тестов `tests/test_split_dependencies.py` зелёные (exit 0).
2. **7 падений проверки 5** — `tests/test_architect_dependencies.py::test_architect_dependencies_prompt_patch_applied_twice_leaves_the_same_bytes`, `::test_architect_dependencies_prompt_patch_never_writes_the_export_it_reads`, `::test_architect_dependencies_flag_off_prompt_is_byte_for_byte_the_unpatched_prompt`, `::test_architect_dependencies_flag_on_plan_prompt_carries_the_block_and_the_rule`, `::test_architect_dependencies_flag_on_split_prompt_carries_the_part_order_rule`, `::test_architect_dependencies_board_fetch_failure_leaves_the_prompt_unchanged`, `::test_architect_dependencies_flag_on_with_no_dependencies_adds_no_block`. Общая причина: staged-экспорт `data-tables/ops/wf_next.json` уже содержит блок зависимостей (`grep -c "architect_prompt_dependency_block.py" data-tables/ops/wf_next.json` → 1), поэтому `ARCHITECT_PATCH.patch(workflow)` возвращает `False` («already patched») при первом применении, а тесты ожидают `True`. Исправление требует регенерации свежего непропатченного экспорта живого workflow (`data-tables/ops/wf_next.json`) — это вне разрешённых путей (запрещено трогать живой workflow); нужен шаг оператора: stage свежего экспорта до запуска проверки. Код `ahawr_runner` и тесты не при чём: grep по `src/ahawr_runner/` не содержит ни `wf_next`, ни `EXPORT_COPY` (единственное совпадение — `data-tables/ops/architect_prompt_dependency_block.py:97`, где корректно напечатан статус идемпотентности).
3. **Версия для `mission_dependencies` не назначена (открытый бамс).** Запись о зависимостиях в
   `CHANGELOG.md` дополнена под уже существующим заголовком `## 2026-11-14 (1.1.310)` — тем же
   заголовком, под которым части 1–3 записали заметку `docs/mission-dependencies.md`; новой версии
   для функции нет: `ahawr-runner/pyproject.toml` и `ahawr_runner/src/ahawr_runner/api.py` остаются на
   `1.2.020` (правка версии в эту задачу не входит). Строка в `ROADMAP.md` стоит без номера версии —
   как строки других миссий. Риск: `ROADMAP.md` генерируется из `docs/roadmap.json`
   (`scripts/make_roadmap.py`), а `docs/roadmap.json` вне SCOPE этой задачи, поэтому при следующем
   запуске генератора новая строка может пропасть, пока её не добавят в JSON. Бамс версии должен
   поставить оператор или следующая часть миссии.
4. **Блокер: guard on run-next.** `POST /api/board/missions/{mission_id}/run-next` has no
   dependency guard (board_api.py:1808-1822); a blocked mission moves to the front without
   a run anyway confirm; this violates mission rule (5) and needs a code task outside this
   QA scope.

Проверки этой задачи: `git diff --check` — 0 строк-ошибок (только штатные предупреждения LF→CRLF на не тронутых файлах); `python -m ruff check ahawr-runner/tests/test_architect_dependencies.py ahawr-runner/tests/test_mission_dependencies.py ahawr-runner/tests/test_qa_container_static.py` → `All checks passed!` (файлы не менялись, ruff запущен на файлах, названных падающими тестами); pytest-проверки цитированы выше. Файлов с правками нет — file:line не приводится.

## Исправления UI

**Исправления UI: не требуются.**

- Повтор команды «Проверки 4» (строки 63-116 этого отчёта) из `ahawr-runner/` (node v22.23.5) даёт ту же зелёную сводку: `# tests 30`, `# pass 30`, `# fail 0`, `# skipped 0`, exit 0 — имений падений нет, чинить нечего.
- Пропущенных ключей зависимостей нет: ключи `q.dep*`, `dep.*`, `btn.dep.*`, `m.dep*` присутствуют во всех трёх местах — 18 в `board_i18n_en.json`, 18 в `board_i18n_ru.json`, 18 в `BUNDLED_EN` (`ahawr-runner/src/ahawr_runner/board_i18n.js`, объект открывается на `:19`); полные наборы EN и RU идентичны (`set(en) == set(ru)` → True), расхождений `BUNDLED_EN` против `board_i18n_en.json` — 0 в обе стороны.
- Файлов с правками нет: `board_mission_dependencies.test.js`/`board_i18n.test.js` не запускались с изменениями, `board*.js`, `board_i18n_en.json`, `board_i18n_ru.json` не редактировались (из двух разрешённых — 0 правок). Ни один тест не удалён, не пропущен и не ослаблен.
- Требований по `esc()` и заголовку `X-AHAWR-Dashboard: 1` не возникало: UI-падений нет, тест `every move and start POST carries the X-AHAWR-Dashboard header` → `ok 29`, тест паритета ключей `every t() key used by the module exists in both language tables` → `ok 30`.
- Открытые риски по UI/i18n-проверкам: нет (в «Проверке 4» падений не зафиксировано).

## Флаг выключен

Два пина «при выключенном флаге `mission_dependencies`» проверены `grep` и запуском. Правка — одна: добавлен тест в `ahawr-runner/tests/test_mission_dependencies.py`; ни один файл под `ahawr-runner/src/` этой задачей не изменён.

### Пин (a): очередь и порядок при выключенном флаге

Что нашлось до правки (grep):

| id теста | место | что закрепляет |
|---|---|---|
| `test_flag_off_depend_queue_answer_bytes` | `tests/test_mission_dependencies.py:73` | байтовый ответ `GET /api/board/queue` при флаге OFF (`queue.content == EXPECTED_QUEUE_BYTES`, константа `:36-41`) |
| `test_flag_off_move_with_stored_dependencies_is_not_guarded` | `:1094` | зависимость m2→m1 лежит в store, флаг OFF: `/queue/move` отвечает старыми байтами (`:1107-1112`), порядок `["m2","m1"]` (`:1114`) |
| `test_next_mission_order_unchanged_flag_off` | `tests/test_queue_board.py:104` | порядок выбора `next_mission` при флаге OFF: `["m_active","m_wait1","m_wait2","m_wait3"]` (`:134`) |
| `test_flag_on_failed_dependency_is_blocked_and_listed_with_the_reason` | `tests/test_queue_board.py:206` | при флаге OFF запись с `depends_on` выбирается как раньше: `:230-231` |
| `test_active_mission_resumes_first_with_and_without_the_rule` | `tests/test_queue_board.py:320` (parametrize ids `flag-off`/`flag-on`, `:319`) | та же очередь с `{"depends_on": ["m_queued"]}` (`:323`) даёт тот же выбор (`:328`) |
| `test_blocked_entries_listed_on_stderr_stdout_keeps_the_parsed_line` | `tests/test_queue_board.py:331` | stdout флага OFF равен stdout флага ON: `captured_on.out == out_off == "m_after 3 0\n"` (`:354`) |
| `test_flag_off_create_with_depends_on_returns_the_same_bytes_as_without_it` | `tests/test_mission_dependencies.py:784` | тело create с `depends_on` байт-в-байт равна телу без него |
| `test_flag_off_ignores_a_sent_dependency` | `:835` | присланный `depends_on` при флаге OFF игнорируется, ответ = `EXPECTED_MISSION_BYTES` |
| `test_flag_off_answer_has_no_dependency_context` | `:318` | ни `dependency_context`, ни `dependency_state` в ответе при флаге OFF |

Не хватало теста, который закрепляет сразу **и** байтовый ответ очереди, **и** порядок, когда записи несут `depends_on`. Добавлен:

`tests/test_mission_dependencies.py:1161 test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order` (25 строк, вставка между `:1159` и `QUEUE_IDS` на `:1186`; тестов в файле стало 80 — `grep -cE '^(async )?def test_` → 80, было 79). Фиксатуры те же, что в файле: `pinned_clock` (`:61`), `dependency_store` (`:128`: m1 позиция 0, m2 позиция 1, оба `queued`), `dependency_client` (`:172`). Ключевые строки (`:1171-1182`):

```
dependency_store.set_dependencies("m2", ["m1"], None)
dependency_store.update_hermes_config(None, features_json=FLAG_OFF_JSON)
assert queue.content == (
    b'{"queue":[{"position":0,"mission_id":"m1","enabled":true,"attempts_per_task":null,'
    b'"retry_failed":false,"note":null},{"position":1,"mission_id":"m2","enabled":true,'
    b'"attempts_per_task":null,"retry_failed":false,"note":null}],'
    b'"version":null}'  # the dependency fixture never stamps a queue version
)
assert [row["mission_id"] for row in rows] == ["m1", "m2"]
assert not any("depends_on" in row or "dependency_state" in row for row in rows)
```

`"version":null` закреплена как есть: store этой фиксатуры не ставит штамп очереди, его пишут только маршруты-записи (`src/ahawr_runner/board_api.py:344-351`). Путь кода, который держит пин: `src/ahawr_runner/board_api.py:1881-1884` — при выключенном флаге строки идут из `entry.model_dump()` без полей зависимостей; в ops-файле `data-tables/ops/queue_board.py:539-540` — `flag_on = any("dependency_state" in entry for entry in entries)`, `rule = _shared_dependency_rule() if flag_on else None`, поэтому `next_mission` (`:233`) работает по-старому.

**Как пин падает без проверки флага** (критерий 2). Временно в `src/ahawr_runner/board_api.py:1881`
`if _feature_enabled(store, None, "mission_dependencies"):` → `if True:  # T043 temporary demo: the flag-off gate removed, reverted right after`.
Запуск нового теста: exit 1, `F  ... [100%]`,
`FAILED tests/test_mission_dependencies.py::test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order[asyncio]`,
деталь `At index 114 diff: b',' != b'}'` — в строке появились поля зависимостей. Правка отменена точно (файл возвращён к исходному виду): `git diff -- src/ahawr_runner/board_api.py` → пусто ( только предупреждение LF→CRLF). После возврата тест снова зелёный: exit 0, `.` `[100%]`.

### Пин (b): input Architect

Он существует, добавлять не потребовалось:
- `tests/test_architect_dependencies.py:588 test_architect_dependencies_flag_off_prompt_is_byte_for_byte_the_unpatched_prompt` — `assert patched["input"] == unpatched["input"]`, `assert patched == unpatched`, `assert "Missions this one depends on" not in patched["input"]`, `assert "PART ORDER OF THE SPLIT:" not in patched["input"]`.
- `:176 test_architect_dependencies_flag_off_route_has_no_dependency_block` (проходит): при сохранённой m2→m1 и выключенном флаге ответ маршрута даёт `dependency_context == ""` и `dependency_state == ""` — форма ответа с `depends_on` и без неё совпадает, то есть промпт байт-в-байт тот же.
- Эквивалентность «с `depends_on` и без него» на стороне runner закреплена тестами `:784` и `:835` (см. таблицу выше).
- Статус пина: красный. `python -m pytest tests/test_architect_dependencies.py -q --no-cov -k architect_dependencies` → exit 1, `........FF.....FFFFF....... [100%]`, 7 FAILED. Причина — та же, что в разделе «Проверки 3 и 5» (строки 49-61): staged-экспорт `data-tables/ops/wf_next.json` уже содержит блок зависимостей, поэтому `ARCHITECT_PATCH.patch(workflow)` → `False` («already patched»), а тесты ждут первого применения. Править эти тесты в этой задаче нельзя: нужен свежий непропатченный экспорт живого workflow (шаг оператора).

### Проверки этой задачи

Из `ahawr-runner/` (python `/opt/ahawr-runner/venv/bin/python`, pytest 9.2.0):
1. `python -m pytest tests/test_mission_dependencies.py -q --no-cov -k flag` → exit 0, `................................................... [100%]` — 51 точка, ни одного `F` (в `-q` режиме pytest 9.2.0 строку-итог не печатает; до добавления теста было 50 точек).
2. `python -m pytest tests/test_mission_dependencies.py -q --no-cov -k test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order` → exit 0, `.` `[100%]`.
3. `python -m ruff check tests/test_mission_dependencies.py` → `All checks passed!`.
4. `python -m mypy tests/test_mission_dependencies.py` → 0 ошибок, приписанных этому файлу (`grep -c "tests/test_mission_dependencies.py:"` → 0); 157 ошибок — в импортируемом `tests/test_board_api.py`, он не тронут.
5. `git diff --check` → 0 строк-ошибок (только штатные предупреждения LF→CRLF).
6. `git diff --stat` (полный): `ahawr-runner/src/ahawr_runner/features_defaults.json | 4 ++--`, `ahawr-runner/src/ahawr_runner/roles_config.py | 2 +-`, `ahawr-runner/tests/test_mission_dependencies.py | 25 ++++++++++++++++++++++`, `ahawr-runner/tests/test_queue_board.py | 3 ++-`. Правки этой задачи — только `test_mission_dependencies.py` (+25) и этот отчёт; `features_defaults.json`, `roles_config.py` и `test_queue_board.py` были изменены до запуска задачи (видно в снимке `git status` в начале работы), я их не трогал. `reports/` в `git diff` не появляется: файл отчёта не отслеживается git.

Не запускалось: `node --test` (JS в этой задаче не трогают), полный `python -m pytest tests/` (тот же блокер — `tests/test_qa_container_static.py:7 import yaml`, в venv нет `pyyaml`), `-k architect_dependencies` по всему `tests/` (тот же блокер) — вместо этого приведён запуск по одному файлу выше.

Открытые риски: пин (b) остаётся красным из-за уже пропатченного staged-экспорта (требуется шаг оператора — свежий экспорт); новый пин (a) закрепляет ответ runner, но не байты самого промпта Architect — это по-прежнему проверяет `test_architect_dependencies.py:588`, и пока он не зелёный, связка «ответ очереди → промпт» закреплена только на уровне маршрута (`:176`).

## Статические проверки (часть 4)

Тронутые миссией `.py`-файлы: `git diff --name-only d4f7c17 -- ahawr-runner/src ahawr-runner/tests | grep '\.py$'` → 34 файла (14 в `src/ahawr_runner/`, 20 в `tests/`). `features_defaults.json` — JSON, ruff/mypy его не проверяют. `board_think.py` (9 ruff: 8 E501 + SIM105), `board_stats.py` (2 mypy), `board_order.py` (20 mypy) миссией не тронуты (последние правки — `2da35ff`, `c97523e`); их старые ошибки оставлены как есть.

### ruff

Запуски из `ahawr-runner/` (python `/opt/ahawr-runner/venv/bin/python`, `line-length = 100`, select `E,F,I,B,UP,SIM` — `pyproject.toml:35,39`, не менялся):

- все 34 тронутых файла: `Found 66 errors.` (перед правкой этой задачи — `Found 68 errors.`);
- копии тех же файлов из `d4f7c17` (`git show d4f7c17:<путь>` → `--config pyproject.toml`): `Found 68 errors.` — это «до».

| файл | ruff до | ruff после | mypy до | mypy после |
|---|---|---|---|---|
| `src/ahawr_runner/board_api.py` | 29 | **27** | 16 | 16 |
| `src/ahawr_runner/board_pg.py` | 26 | 26 | 29 | 29 |
| `src/ahawr_runner/board.py` | 5 | 5 | 5 | 5 |
| `src/ahawr_runner/board_ready.py` | 2 | 2 | 4 | 4 |
| `src/ahawr_runner/api.py` | 0 | 0 | 1 | 1 |
| `src/ahawr_runner/runs.py` | 0 | 0 | 1 | 1 |
| `src/ahawr_runner/roles_config.py` | 0 | 0 | 0 | 0 |
| `tests/test_board_pg.py` | 4 | 4 | 83 (запись из частей 1-3) | 81, из них 9 на строках миссии |
| `tests/test_board_ready.py` | 0 | 0 | 121 | 121 |
| `tests/test_qa_runner.py` | 2 | 2 | 9 | 9 |
| `tests/test_qa_fixture.py` | 0 | 0 | 5 | 5 |
| `tests/test_queue_board.py` | 0 | 0 | 3 | 3 |
| остальные 22 тронутых файла (`board_dependencies.py`, `board_run_mission.py`, `dashboard.py`, `features_config.py`, `qa_fixture.py`, `qa_runner.py`, `run_limits.py`, `test_architect_dependencies.py`, `test_architect_fallback_patch.py`, `test_board_dependencies.py`, `test_board_features_api.py`, `test_board_run_mission.py`, `test_dashboard_ops_tab.py`, `test_dashboard_security.py`, `test_fallback_option_patch.py`, `test_features_config.py`, `test_features_config_patch.py`, `test_mission_dependencies.py`, `test_roles_config.py`, `test_run_limits.py`, `test_split_dependencies.py`, `test_split_inherit_settings.py`) | 0 | 0 | 0 | 0 |

### Что исправлено (1 файл, 2 строки)

- `ahawr-runner/src/ahawr_runner/board_api.py:1769-1772` — условие `_feature_enabled(...) and not (body or {}).get("run_anyway")` (104 символа) разбито на 4 строки с скобками;
- `ahawr-runner/src/ahawr_runner/board_api.py:1930-1932` — вызов `store.move_queue(body.get("mission_id", ""), int(body.get("to_position", -1)), expected)` (110 символов) разбит на 3 строки.

Смысл не изменился: те же аргументы, тот же порядок проверок; pytest-тесты, которые ходят через эти маршруты, прошли (см. ниже).

Оба E501 добавила миссия (`git blame` — `66cc181`): `git diff -U0 d4f7c17 HEAD -- .../board_api.py` даёт ровно `+`-строк длиннее 100 символов: 2, `-`-строк: 2 (-net по файлу 29 → 29, как записано в частях 1-3). После правки `git diff -U0 d4f7c17 -- ahawr-runner/src ahawr-runner/tests | awk '/^\+/ && length($0) > 101'` по `.py`-файлам не даёт ни одной строки (остались только `.js`, `.json`, `.html`, `.md`, где ruff не применяется).

Пересечение диапазонов миссии с выводами проверок (скрипт-разбор `git diff -U0` по хункам + вывод ruff): `ruff errors on mission-added lines: 0`; mypy — 9, все в `tests/test_board_pg.py` (см. риски).

### mypy

- `python -m mypy <34 тронутых файлов>` → `Found 614 errors in 25 files (checked 34 source files)`. Из них на тронутых миссией файлах — 275; 339 ошибок в файлах, которых задача не касала (`tests/test_board_api.py` 157, `tests/test_dashboard.py` 60, `tests/test_runs.py` 49, `src/ahawr_runner/board_ops_api.py` 35, `src/ahawr_runner/board_order.py` 20 и т. д., они идут транзитивно через импорты).
- «До» для файлов без записанного числа считается по правилу задачи: текущее число минус ошибки на строках, добавленных миссией. На `src`-файлах миссии mypy не дал ни одной новой ошибки (0 попаданий в диапазоны миссии).

### Проверки этой части

1. `python -m ruff check <34 файлов>` → `Found 66 errors.`; отдельно `src/ahawr_runner/board_api.py` → `Found 27 errors.` (все 27 — E501, ни одна строка не принадлежит миссии).
2. `python -m mypy src/ahawr_runner/board_api.py` → 16 ошибок в этом файле, итог запуска `Found 614 errors in 25 files (checked 34 source files)`; новых ошибок на строках правки нет.
3. `python -m pytest tests/test_queue_board.py tests/test_board_pg.py -q --no-cov` → exit 0, последний прогресс-маркер `[100%]`, ни одного `F` (в `-q` pytest 9.2.0 строку-итог не печатает; подсчёт `F` в выводе — 0).
4. `python -m pytest tests/test_mission_dependencies.py -q --no-cov` → exit 0 (51 точка).
5. `git diff --check` (из корня `/d/n8n`) → exit 0; ошибок whitespace нет, только штатные предупреждения: `warning: in the working copy of 'ahawr-runner/src/ahawr_runner/board_api.py', LF will be replaced by CRLF the next time Git touches it`.
6. `git diff | grep -n 'noqa\|type: ignore'` → exit 1, ни одного совпадения (в diff нет ни `noqa`, ни `type: ignore`). `git diff -- ahawr-runner/pyproject.toml ahawr-runner/ruff.toml` — пуст: конфиг проверок не ослаблен.

Не запускалось: `node --test` (JS в этой части не трогают), полный `python -m pytest tests/` (блокер `tests/test_qa_container_static.py:7 import yaml` — в venv нет `pyyaml`), mypy на «до»-копии `src` (не требуется: «до» считается по строчному методу; копия `board_api.py` из `d4f7c17` через mypy не проверяется — `No parent module -- cannot perform relative import`).

### Открытые риски статических проверок

- 9 mypy-ошибок на строках, добавленных миссией, в `tests/test_board_pg.py:76 (x2), 332, 340, 348, 353, 361, 377, 383` — все `no-untyped-def` («Function is missing a type annotation»/«missing a return type annotation»). Это 9 строк в одном тестовом файле: не проходит под лимит задачи «исправление одной строки в не более чем 2 файлах», и тесты вне SCOPE этой части (SCOPE — `src/ahawr_runner/`). Файл в целом стал чище (83 → 81), новых ошибок на строках `src`-правок нет.
- Старые ошибки нетронутых файлов не правятся по правилу задачи: `board_think.py` 9 ruff (8 E501 + SIM105), `board_stats.py` 2 mypy, `board_order.py` 20 mypy, `board_ops_*` 49 mypy (транзитивно).

## Итоговые проверки

Повторный запуск всех пяти команд миссии из `ahawr-runner/` после всех правок части (python `/opt/ahawr-runner/venv/bin/python`, pytest 9.2.0, node v22.23.5); код и тесты этой задачей не менялись. В `-q` pytest 9.2.0 строку-итог не печатает, поэтому цитируются прогресс-маркеры и exit-код; символ `F` в выводе отсутствует.

| № | критерий | команда | итоговая строка | вердикт |
|---|---|---|---|---|
| 1 | `depends_on` хранится/читается, API показывает, при OFF ответы прежние | `python -m pytest tests/test_board_pg.py tests/test_mission_dependencies.py -q --no-cov -k depend` | exit 0; `........................................................................ [ 77%]` + `..................... [100%]` — 93 точки, 0 `F` | выполнено |
| 2 | `next_mission` пропускает неудовлетворённую зависимость, blocked/циклы отвергаются | `python -m pytest tests/test_queue_board.py tests/test_mission_dependencies.py -q --no-cov` | exit 0; `........................................................................ [ 58%]` + `.................................................... [100%]` — 124 точки, 0 `F` | выполнено |
| 3 | split-патч идемпотентен, синтаксис проверен на копии экспорта | `python -m pytest tests/ -q --no-cov -k split_dependencies` | exit 2; `______________ ERROR collecting tests/test_qa_container_static.py ______________` и `!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!` (`ModuleNotFoundError: No module named 'yaml'`) — отобрано 0 тестов | не выполнено (команда не смогла собрать тесты: в venv нет `pyyaml` — блокер окружения, записанный в открытых рисках; 0 отобранных тестов, поэтому вердикт не может быть «выполнено») |
| 4 | очередь/kanban показывают `after: ids`, drop раньше зависимости отвергается, ключи EN/RU совпадают | `node --test tests/board_mission_dependencies.test.js tests/board_i18n.test.js` | exit 0; `# tests 30`, `# pass 30`, `# fail 0`, `# duration_ms 785.983612` | выполнено |
| 5 | в input Architect блок зависимостей и правило, при OFF промпт байт-в-байт прежний, линт отвергает позднюю часть | `python -m pytest tests/ -q --no-cov -k architect_dependencies` | exit 2; тот же `ERROR collecting tests/test_qa_container_static.py` и `Interrupted: 1 error during collection` (`No module named 'yaml'`) — отобрано 0 тестов | не выполнено (команда прервана на сборке из-за отсутствия `pyyaml` в venv, тесты не запускались) |
| 6 | ruff и mypy без новых ошибок для тронутых файлов, `git diff --check` чист, CHANGELOG и ROADMAP обновлены | данные берутся из раздела «Статические проверки» (повторно не запускалось): `python -m ruff check <34 файлов>` → `Found 66 errors.` (было 68; на строках миссии — 0); `python -m mypy` → `Found 614 errors in 25 files` (новых ошибок на строках `src`-правок нет; 9 новых на строках миссии в `tests/test_board_pg.py`); `git diff --check` → exit 0; в diff нет `noqa`/`type: ignore`; `CHANGELOG.md:58` и `ROADMAP.md:184` содержат записи о зависимостях | не выполнено (критерий требует «без новых ошибок»: 9 новых mypy-ошибок `no-untyped-def` на строках миссии в тронутом `tests/test_board_pg.py:76 (x2), 332, 340, 348, 353, 361, 377, 383` остались незакрытыми — открытый риск раздела) |

Команды 1–5 были запущены именно в этой задаче (выходы: `exit1=0 exit2=0 exit3=2 exit4=0 exit5=2`); команд, которые «не запускалось», кроме статических для критерия 6 (они не повторяются по условию задачи), нет.

## Итог части 4

### Что сделано (задачи T037–T048)

- **Инвентаризация** (разделы «Состояние перед частью 4» и «Что не менялось»): все шесть критериев
  найдены в коде и тестах; ни один файл кода, тестов, конфига или `data-tables/ops/` не тронут.
  Отдельных отчётов p1–p3 в `reports/` нет — это зафиксировано как блокер.
- **«Исправления Python»: 0 правок.** Падений в «Проверках 1-2» нет; обе группы падений
  (7 падений проверки 5 и exit 2 сборки проверок 3 и 5) лежат вне разрешённых путей — в venv
  и в staged-экспорте `data-tables/ops/`. Ни один тест не удалён, не пропущен и не ослаблен.
- **«Исправления UI»: не требуются.** Повтор команды проверки 4 даёт ту же сводку (`# tests 30`,
  `# pass 30`, `# fail 0`, exit 0); 18 ключей `q.dep*`/`dep.*`/`btn.dep.*`/`m.dep*` присутствуют
  во всех трёх местах, `set(en) == set(ru)` → True, расхождений `BUNDLED_EN` против JSON — 0.
- **«Флаг выключен»: одна правка.** Добавлен пин
  `ahawr-runner/tests/test_mission_dependencies.py:1161 test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order`
  (+25 строк; тестов в файле 80, было 79). Пин (b) существовал ранее —
  `tests/test_architect_dependencies.py:588`. Доказательство, что пин падает без gate: временная
  правка `src/ahawr_runner/board_api.py:1881` (`if _feature_enabled(...)` → `if True:`) даёт
  exit 1 и `At index 114 diff: b',' != b'}'`; после возврата файла
  `git diff -- ahawr-runner/src/ahawr_runner/board_api.py` пуст, тест снова зелёный.
- **«Статические проверки (часть 4)»**: две строки, добавленные миссией и нарушавшие E501, разбиты —
  `ahawr-runner/src/ahawr_runner/board_api.py:1769-1772` и `:1930-1932`; ruff по 34 тронутым файлам
  68 → 66, на строках миссии — 0 ошибок; mypy на `src`-правок новых ошибок не дал (9 новых —
  в `tests/test_board_pg.py`, см. риски).
- **«Итоговые проверки»**: пять команд миссии повторно, выходы `exit1=0 exit2=0 exit3=2 exit4=0 exit5=2`.

### Изменённые файлы части 4

- `ahawr-runner/src/ahawr_runner/board_api.py` — 9 строк в `git diff --stat` (две правки E501);
- `ahawr-runner/tests/test_mission_dependencies.py` — 25 строк (новый тест);
- `reports/ahawr-mission-dependencies-p4-report-ru.md` — этот отчёт (файл не отслеживается git).
- Изменены **до** задач части 4 и этими задачами не тронуты: `features_defaults.json` (4),
  `roles_config.py` (2), `tests/test_queue_board.py` (3), `CHANGELOG.md` (36), `ROADMAP.md` (1),
  `docs/mission-dependencies.md` (175) — видно по `git diff --stat` и снимку `git status` в начале работы.

### Проверки (результаты — из раздела «Итоговые проверки»)

- критерии 1, 2, 4 — **выполнено**: 93 точки (exit 0), 124 точки (exit 0),
  `# tests 30` / `# pass 30` / `# fail 0` / `# duration_ms 785.983612` (exit 0);
- критерии 3 и 5 — **не выполнено**: exit 2, сборка прервана
  (`ModuleNotFoundError: No module named 'yaml'`), отобрано 0 тестов;
- критерий 6 — **не выполнено**: 9 новых mypy-ошибок `no-untyped-def` на строках миссии
  в `tests/test_board_pg.py`.
Локальные прогоны задач части: `pytest tests/test_mission_dependencies.py -q --no-cov -k flag` →
exit 0, 51 точка; `pytest tests/test_mission_dependencies.py -q --no-cov -k test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order` → exit 0;
`pytest tests/ -q --no-cov -k split_dependencies --ignore=tests/test_qa_container_static.py` → exit 0, 9 точек;
`ruff check tests/test_mission_dependencies.py` → `All checks passed!`; `git diff --check` → exit 0
(штатные предупреждения LF→CRLF).

### Что не запускалось (и почему)

- полный `python -m pytest tests/` и `-k architect_dependencies`/`-k split_dependencies` без
  `--ignore`: блокер `tests/test_qa_container_static.py:7 import yaml` — в venv нет `pyyaml`;
- `node --test tests/` целиком: правило задачи — запускать только затронутые тесты (проверка 4
  покрыла их);
- QA-сценарии `ahawr-runner/qa/scenarios/mission_dependencies_*.yaml` и валидатор контракта
  `ahawr-mission-dependencies-p2`: нет `pyyaml`, нет playwright/chromium для референс-изображения;
- импорт патчей в n8n, миграция БД и включение флага: граница миссии — живое состояние не меняется;
- mypy на «до»-копию `src`: не требуется («до» считается по строчному методу); копия `board_api.py`
  из `d4f7c17` через mypy не проверяется — `No parent module -- cannot perform relative import`.

### Что оператору применить в окне (не применено)

По `docs/mission-dependencies.md:92` — одно окно с приостановленной очередью:
1. миграция store (`board_pg.py:422`) через пересборку runner, заказанную `POST /v1/host-actions`;
2. свежий экспорт живого workflow в `data-tables/ops` (не `AHAWR_Workflow.json`);
3. патчи в порядке: `split_dependencies_patch.py` → `architect_prompt_dependency_block.py` →
   `plan_dependency_lint_patch.py` (после `plan_gate_retry_patch.py`), затем
   `node data-tables/ops/js_syntax_check.js <copy.json>`;
4. импорт пропатченной копии в интерфейсе n8n при паузе, затем `/pause off`;
5. флаг `mission_dependencies.enabled = true` в `features_json` (сейчас
   `features_defaults.json:29-31` даёт `enabled: false`).
Расхождение факта: `data-tables/ops/launch_mission.sh:56, 58, 59` уже содержит команды этих патчей и
`:100` импортирует `wf_next.json`, а в `data-tables/ops/wf_next.json` маркер
`architect_prompt_dependency_block.py` уже есть (`grep -c ... → 1`).

### Открытые риски части 4

- 9 mypy-ошибок `no-untyped-def` на строках миссии в `tests/test_board_pg.py:76 (x2), 332, 340,
  348, 353, 361, 377, 383` — вне лимита «одна строка в не более чем 2 файлах» и вне SCOPE.
- Пин (b) красный: 7 тестов `tests/test_architect_dependencies.py` падают из-за уже пропатченного
  staged-экспорта; нужен свежий непропатченный экспорт (шаг оператора).
- Нет `pyyaml` в venv — проверки 3 и 5 не зелёные; установка — новая runtime-зависимость, её решает оператор.
- Guard на `run-next` отсутствует: маршрут `ahawr-runner/src/ahawr_runner/board_api.py:1811`
  зависимости не проверяет (нарушает правило (5) миссии; нужен отдельный код-тест).
- Версия для `mission_dependencies` не назначена: `pyproject.toml:7` и `api.py:362` — `1.2.020`;
  строка в `ROADMAP.md:184` без номера версии, а `ROADMAP.md` генерируется из `docs/roadmap.json`
  (`scripts/make_roadmap.py`), который вне SCOPE.
- QA-контракты и сценарии миссии не закоммичены (`git status --short`), в контракте p2 нет
  референс-изображения (нужен playwright/chromium).
- Старые ошибки нетронутых файлов не правятся: `board_think.py` 9 ruff, `board_stats.py` 2 mypy,
  `board_order.py` 20 mypy, `board_ops_*` 49 mypy (транзитивно).
- Правила миссии в `data-tables/missions/mission_ahawr-mission-dependencies.csv` ссылаются на старое
  имя `claude-runner/...` вместо `ahawr-runner/` — вопрос к оператору.
