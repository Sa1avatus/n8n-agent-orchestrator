# Итоговый отчёт миссии `ahawr-mission-dependencies` (зависимости миссий)

Дата: 2026-11-16. Версия runner: `1.2.020` (`ahawr-runner/pyproject.toml:7`,
`ahawr-runner/src/ahawr_runner/api.py:362`).
Источники: разделы отчёта части 4 `reports/ahawr-mission-dependencies-p4-report-ru.md`
(задачи T037–T048), `reports/ahawr-mission-dependencies-baseline.txt`,
`reports/ahawr-mission-dependencies-checks.txt`, `docs/mission-dependencies.md` и
`git status --short` (79 записей в дереве). Отдельных отчётов p1–p3 в `reports/` нет —
факт зафиксирован в отчёте части 4 («Блокеры, найденные при инвентаризации»); содержательная
часть частей 1–3 восстановлена по коду и итоговому тексту.
Все ссылки на строки сверены `grep -n` с текущим рабочим деревом: после правок части 4
номерация в `board_api.py` сместилась (guard зависимостей — `:1769-1772`).

## 1. Что сделано по пунктам миссии (1)–(8)

### (1) Данные: `depends_on` в очереди, только через методы store
- `ahawr-runner/src/ahawr_runner/board_pg.py:97` — в `_DDL`: `"depends_on jsonb NOT NULL DEFAULT '[]')"`;
- `board_pg.py:416` — вызов `self._ensure_queue_columns()` при создании store; определение —
  `board_pg.py:422`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS depends_on jsonb NOT NULL DEFAULT '[]'`
  (`board_pg.py:440`). SQL руками не выполняется, сырых UPDATE нет.
- Методы store: `board_pg.py:273` `list_dependencies` и `board_pg.py:277` `set_dependencies`
  (базовый stub), реализация в Pg-сторе — `board_pg.py:625` и `board_pg.py:1107`; запись из API —
  `board_api.py:632` (`setter = getattr(store, "set_dependencies", None)`).
- API отдаёт поля: `board_api.py:427` `_dependency_fields` (`depends_on`, `dependency_state`,
  `dependency_reason` — `:432`), добавление в ответы — `board_api.py:452`, `:1244`, `:1353`, `:1388`.

### (2) Правило next_mission: due / waiting / blocked, проверка id и циклов
- Один источник правила — `ahawr-runner/src/ahawr_runner/board_dependencies.py` (чистый модуль,
  без БД и сети): `build_dependencies` `:58`, `validate_dependencies` `:134`, `classify_queue` `:165`,
  `plan_move_order` `:275`, `find_move_problems` `:323`, `find_move_blockers` `:357`,
  `find_first_due` `:376`, `find_part_order_problems` `:400`.
- Шелковый queue использует то же правило по пути, не импортируя пакет runner:
  `data-tables/ops/queue_board.py:89` (`DEPENDENCY_RULE_PATH`), загрузка `:195-198`,
  `_blocked_lines` `:207`, `classify_queue` `:225`, выбор `find_first_due` — `:267`
  (маршрут `next_mission` — `:233`).
- В API правило вызывают guard-маршруты: `board_api.py:533` (`validate_dependencies`), `:597`
  (`find_move_problems`), `:605` (`find_move_blockers`), `:609` (`plan_move_order`).

### (3) Adaptive split: часть N зависит от части N-1
- Патч `data-tables/ops/split_dependencies_patch.py`: якорь `ANCHOR = "    plan: partPlan(part, k),\n"`
  (`:23`), проверка «якорь ровно один раз» — `:35`, идемпотентность (`already patched`) — `:53`.
- Часть 1 зависит ни от чего; части, созданные до патча, остаются с пустым списком
  (`docs/mission-dependencies.md`).
- Тесты: `ahawr-runner/tests/test_split_dependencies.py` (9 тестов: цепочка частей — `:107`,
  применение к копии экспорта — `:120`, двойное применение даёт те же байты — `:142`).

### (4) Dashboard: строка `after: <ids>`, чипы, редактор, drag-and-drop
- Рендер (только отображение, пересчёта в JS нет):
  `ahawr-runner/src/ahawr_runner/board_mission_dependencies.js:30` (классы `dep-chip q-dep-waiting`,
  `q-dep-blocked`), `:37-38` (`q.dep` / `q.dep.none`), `:49-52` (waiting / blocked, титулы).
- Подключение в карточках: `board_queue.js:71`, `:206`; `board_view_kanban.js:71`; CSS — `board.css:274-277`.
- Drag-and-drop: POST `/api/board/queue/move` — `board_dnd.js:85`, «to the top» — `:141`; обработка
  отказа по зависимости `handleDependencyRefusal` — `board_actions.js:290`, повтор с
  `move_dependents: true` — `:310` (вызов — `:366`).
- Поле редактора «After missions»: `board_mission.js:200-202`; сохранение и разбор поля —
  `board_mission_save.js:116`, `:119`, `:384-387`, `:409`, `:511`.
- Тексты через `L(key, ...)` во всех трёх копиях: 16 ключей `q.dep*`, `dep.*`, `btn.dep.*`, `m.dep*` —
  `board_i18n_en.json:14`, `board_i18n_ru.json:14`, `BUNDLED_EN` в `board_i18n.js` (`:277-293`);
  паритет проверяет тест `tests/board_mission_dependencies.test.js:613`.
- QA-контракт с этими проверками: `ahawr-runner/qa/contracts/ahawr-mission-dependencies-p2/contract.md` (C1–C16).

### (5) Кнопки оператора: «Run next» / «Run now» и перемещение по очереди
- Guard старта: `board_api.py:1769-1772` — при включённом флаге `mission_dependencies` и без
  `run_anyway: true` миссия с незавершёнными зависимостями не запускается (часть 4 разбила эту
  строку на 4, смысл не менялся); текст отказа формируется из `dependency_reason` —
  `board_api.py:575`.
- Выбор next_mission в runner-модуле `board_run_mission.py`: описание правила — `:25-28`; общий
  модуль правила — `board_dependencies.find_first_due` (`:376`).

### (6) Флаг `mission_dependencies`, OFF по умолчанию
- `ahawr-runner/src/ahawr_runner/features_defaults.json:29-31` — `"mission_dependencies": {"enabled": false}`;
  чтение через `_feature_enabled` (`board_api.py:89`) в маршрутах `:1770`, `:1884`, `:1929`, `:1982`,
  `:2003` — при выключенном флаге ключей `depends_on` / `dependency_*` в ответах нет, поведение прежнее.
- Контракт «флаг выключен» закрепляет `ahawr-runner/tests/test_mission_dependencies.py`
  (`FLAG_OFF_JSON` — `:33`, байтовый ответ очереди — `:73`, create без `depends_on` — `:784`, `:835`,
  отсутствие контекста — `:318`).
- Часть 4 (пин флага OFF): добавлен тест
  `ahawr-runner/tests/test_mission_dependencies.py:1161 test_flag_off_queue_with_stored_dependencies_keeps_the_old_answer_and_order`
  (+25 строк, тестов в файле стало 80); пин (b) про промпт Architect существовал ранее —
  `tests/test_architect_dependencies.py:588`.

### (7) Architect: блок зависимостей и линт плана
- Лимиты и текст: `board_dependencies.py:21` (`DEPENDENCY_REPORT_MAX_CHARS = 700`), `:23`
  (`ARCHITECT_BLOCK_MAX_CHARS = 4000`), `:25` (`ARCHITECT_DEPENDENCY_RULE`), `:35` (`TRUNCATION_MARK`),
  сборка контекста — `:462` (`build_dependency_context`).
- Подача в API как additive-поле `dependency_context`: helper `board_api.py:492` (вызов `:523`),
  добавление в ответы — `board_api.py:1245-1247` и `:1496-1498`, только при флаге и наличии зависимостей.
- Патч промпта: `data-tables/ops/architect_prompt_dependency_block.py` — маркер `:26`, fetch с
  `DEPENDENCY_FETCH_TIMEOUT_MS = 30000` (`:31`, `:43`), `SPLIT_ORDER_RULE` (`:55`), добавление только
  при флаге (`:66`), идемпотентность (`:97`).
- Линт плана: `data-tables/ops/plan_dependency_lint_patch.py` — `GATE_RETRY_ROUTED = dependencyFlagOn`
  (`:65`), `LINT_RETRY_LIMIT = 1` (`:74`); зеркало Python-функции `find_part_order_problems`
  (`board_dependencies.py:400`), тесты — `ahawr-runner/tests/test_architect_dependencies.py` (27).
- Порядок применения: линт требует `data-tables/ops/plan_gate_retry_patch.py` (файл в `data-tables/ops` есть).

### (8) Документы
- `docs/mission-dependencies.md` — окончательная заметка (правило, API, UI, split, Architect, флаг,
  раздел «Operator steps» — `:92`); упоминание в CHANGELOG: `CHANGELOG.md:58-66`;
  `docs/adaptive-split.md` — абзац про `depends_on` частей. ROADMAP: `docs/roadmap.json:582` →
  `ROADMAP.md:184` («1.1.300: bug fixes: the four QA scenarios that failed `ahawr-mission-dependencies-p2` …»).

## 2. Изменённые и новые файлы

Работа миссии закоммичена (`ca7d7b0` 1.1.200, `634d87e` 1.1.300, `66cc181` 1.2.0, `33f34cd` 1.2.010,
`0be2cd6` 1.2.020 поверх `d4f7c17`); `git diff --name-only d4f7c17 HEAD | wc -l` → 98 файлов
(по `reports/ahawr-mission-dependencies-checks.txt:138-146`).
Python: `api.py`, `board.py`, `board_api.py`, `board_dependencies.py`, `board_pg.py`, `board_ready.py`,
`board_run_mission.py`, `dashboard.py`, `features_config.py`, `qa_fixture.py`, `qa_runner.py`,
`roles_config.py`, `run_limits.py`, `runs.py` + 20 тестовых модулей. JS/i18n/фикстуры:
`board_actions.js`, `board_dnd.js`, `board_edit_win.js`, `board_features.js`, `board_i18n.js`,
`board_i18n_en.json`, `board_i18n_ru.json`, `board_mission.js`, `board_mission_dependencies.js`,
`board_mission_save.js`, `board_queue.js`, `board_readonly.js`, `board_ready_ui.js`, `board_roles.js`,
`board_run_mission.js`, `board_stats_popup.js`, `board_view_kanban.js`, `board.css`, `board.html`,
`tests/fixtures/*pinned*`.
Новые тестовые модули: `test_board_dependencies.py`, `test_mission_dependencies.py`,
`test_split_dependencies.py`, `test_architect_dependencies.py`, `test_board_run_mission.py`,
`test_run_limits.py`, `test_features_config.py`, `test_features_config_patch.py`,
`test_fallback_option_patch.py`, `test_architect_fallback_patch.py`, `board_mission_dependencies.test.js`.

Незакоммиченный «хвост» миссии (`git status --short`, 79 записей; `git diff --stat` по файлам
миссии): `CHANGELOG.md` (36 строк), `ROADMAP.md` (1), `ahawr-runner/src/ahawr_runner/board_api.py` (9),
`ahawr-runner/src/ahawr_runner/features_defaults.json` (4), `ahawr-runner/src/ahawr_runner/roles_config.py` (2),
`ahawr-runner/tests/test_mission_dependencies.py` (25), `ahawr-runner/tests/test_queue_board.py` (3),
`docs/mission-dependencies.md` (175).
Не отслеживаются git и лежат в дереве: `ahawr-runner/qa/contracts/ahawr-mission-dependencies/` и
`ahawr-runner/qa/contracts/ahawr-mission-dependencies-p2/`,
`ahawr-runner/qa/fixtures/mission_dependencies.json`, 7 сценариев
`ahawr-runner/qa/scenarios/mission_dependencies_*.yaml` (create_with_dependency,
drop_before_dependency_refused, editor_cycle, editor_unknown_id, kanban_blocked, queue_waiting,
run_next_run_anyway), `reports/ahawr-mission-dependencies-baseline.txt`,
`reports/ahawr-mission-dependencies-checks.txt`, отчёты этой миссии.
Прочие `M`-файлы в `git status` (`evals/worker-suite/…`, `litellm/…`, `docs/retrieval/…`,
`docs/codex-executor.md`, `docs/guards.md`, `docs/naming.md`, `scripts/…`) — другие миссии;
к зависимостям миссий отношения не имеют.

## 3. Проверки по критериям приёмки 1–6 (команды и результаты)

Результаты взяты дословно из раздела «Итоговые проверки» отчёта части 4
(`reports/ahawr-mission-dependencies-p4-report-ru.md:287-294`): повторный запуск всех пяти команд
миссии из `ahawr-runner/` после всех правок части (python `/opt/ahawr-runner/venv/bin/python`,
pytest 9.2.0, node v22.23.5); код и тесты этой задачей не менялись. В `-q` pytest 9.2.0 строку-итог
не печатает, поэтому цитируются прогресс-маркеры и exit-код; символ `F` в выводе отсутствует.
Выходы команд: `exit1=0 exit2=0 exit3=2 exit4=0 exit5=2`.

| № | критерий | команда | итоговая строка | вердикт |
|---|---|---|---|---|
| 1 | `depends_on` хранится/читается, API показывает, при OFF ответы прежние | `python -m pytest tests/test_board_pg.py tests/test_mission_dependencies.py -q --no-cov -k depend` | exit 0; `........................................................................ [ 77%]` + `..................... [100%]` — 93 точки, 0 `F` | выполнено |
| 2 | `next_mission` пропускает неудовлетворённую зависимость, blocked/циклы отвергаются | `python -m pytest tests/test_queue_board.py tests/test_mission_dependencies.py -q --no-cov` | exit 0; `........................................................................ [ 58%]` + `.................................................... [100%]` — 124 точки, 0 `F` | выполнено |
| 3 | split-патч идемпотентен, синтаксис проверен на копии экспорта | `python -m pytest tests/ -q --no-cov -k split_dependencies` | exit 2; `______________ ERROR collecting tests/test_qa_container_static.py ______________` и `!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!` (`ModuleNotFoundError: No module named 'yaml'`) — отобрано 0 тестов | не выполнено (команда не смогла собрать тесты: в venv нет `pyyaml` — блокер окружения, записанный в открытых рисках; 0 отобранных тестов, поэтому вердикт не может быть «выполнено») |
| 4 | очередь/kanban показывают `after: ids`, drop раньше зависимости отвергается, ключи EN/RU совпадают | `node --test tests/board_mission_dependencies.test.js tests/board_i18n.test.js` | exit 0; `# tests 30`, `# pass 30`, `# fail 0`, `# duration_ms 785.983612` | выполнено |
| 5 | в input Architect блок зависимостей и правило, при OFF промпт байт-в-байт прежний, линт отвергает позднюю часть | `python -m pytest tests/ -q --no-cov -k architect_dependencies` | exit 2; тот же `ERROR collecting tests/test_qa_container_static.py` и `Interrupted: 1 error during collection` (`No module named 'yaml'`) — отобрано 0 тестов | не выполнено (команда прервана на сборке из-за отсутствия `pyyaml` в venv, тесты не запускались) |
| 6 | ruff и mypy без новых ошибок для тронутых файлов, `git diff --check` чист, CHANGELOG и ROADMAP обновлены | данные берутся из раздела «Статические проверки» (повторно не запускалось): `python -m ruff check <34 файлов>` → `Found 66 errors.` (было 68; на строках миссии — 0); `python -m mypy` → `Found 614 errors in 25 files` (новых ошибок на строках `src`-правок нет; 9 новых на строках миссии в `tests/test_board_pg.py`); `git diff --check` → exit 0; в diff нет `noqa`/`type: ignore`; `CHANGELOG.md:58` и `ROADMAP.md:184` содержат записи о зависимостях | не выполнено (критерий требует «без новых ошибок»: 9 новых mypy-ошибок `no-untyped-def` на строках миссии в тронутом `tests/test_board_pg.py:76 (x2), 332, 340, 348, 353, 361, 377, 383` остались незакрытыми — открытый риск раздела) |

Статические данные критерия 6 — из раздела «Статические проверки (часть 4)» того же отчёта
(34 тронутых миссией `.py`-файла, ruff 68 → 66 после разбиения двух строк E501 в `board_api.py`).
Дополнительные прогоны других затронутых модулей зафиксированы в
`reports/ahawr-mission-dependencies-checks.txt` и в разделах «Проверки 1-2», «Проверки 3 и 5»,
«Проверка 4» отчёта части 4 — они не повторялись в «Итоговых проверках».

## 4. Проверки, которые не запускались (и почему)

- **Статические ruff/mypy в «Итоговых проверках» не повторялись** — по условию задачи повтор
  не требовался; результаты взяты из раздела «Статические проверки (часть 4)»
 (`reports/ahawr-mission-dependencies-p4-report-ru.md:296`: «команд 1–5 … команд, которые
  «не запускалось», кроме статических для критерия 6 … нет»).
- **Полный `python -m pytest tests/`** не запускался: блокер сборки
  `tests/test_qa_container_static.py:7 import yaml` — в venv нет `pyyaml`
  (`python -c "import yaml"` → `ModuleNotFoundError`).
- **QA-сценарии** `ahawr-runner/qa/scenarios/mission_dependencies_*.yaml` и валидатор контракта
  `ahawr-mission-dependencies-p2` не проверялись: нет `pyyaml`, и нет playwright/chromium для
  съёма референс-изображения (`contract.md:2-8`).
- **`node --test tests/` целиком** не делался: правило CLAUDE.md — запускать только тесты,
  затронутые изменением; для отчёта достаточно проверки 4.
- **Применение патчей к живому workflow, миграция БД, включение флага и `POST /v1/host-actions`**
  намеренно не выполнялись (граница миссии: ничего не применять к живому workflow и живой БД).
- **Проверка живого дашборда** (`/static/board.html`) не делалась: контейнер не перезапускался,
  флаг в живом `features_json` не включён.

## 5. Что оператору применить в окне (не применено)

По `docs/mission-dependencies.md:92-…` ничего не применено к живому workflow; требуется одно
окно с приостановленной очередью (`/pause <reason>`):

1. **Миграция хранилища** — руками ничего не выполняется: `_ensure_queue_columns`
   (`ahawr-runner/src/ahawr_runner/board_pg.py:422`) добавит колонку при создании store. Шаг —
   пересборка runner в окне через `POST /v1/host-actions` на runner (беллист runner решает
   сервис и tier). Не выполнено.
2. **Свежий экспорт живого workflow** в `data-tables/ops` (не `AHAWR_Workflow.json`) — не делалось.
3. **Патчи копии в порядке**: `data-tables/ops/split_dependencies_patch.py`,
   `data-tables/ops/architect_prompt_dependency_block.py`,
   `data-tables/ops/plan_dependency_lint_patch.py` (после `plan_gate_retry_patch.py`), затем
   `node data-tables/ops/js_syntax_check.js <copy.json>` → `bad 0`. Файлы-патчи написаны; импорт
   в n8n не выполнялся.
4. **Имспорт пропатченной копии в интерфейсе n8n** при паузе очереди, затем `/pause off` — не сделано.
5. **Включение флага** `mission_dependencies.enabled = true` в `features_json` (или в
   `mission_features_json` одной миссии) — сейчас `features_defaults.json:29-31` даёт
   `enabled: false`. Откат: вернуть флаг false — guard, блок Architect и линт сразу отключаются
   без пересмотра импорта.

Важное расхождение факта: `data-tables/ops/launch_mission.sh:56, 58, 59` уже содержат команды этих
трёх патчей, а `launch_mission.sh:100` импортирует `wf_next.json`; staged-экспорт
`data-tables/ops/wf_next.json` уже содержит маркер `// architect_prompt_dependency_block.py`
(`grep -c ... → 1`). То есть утверждение заметки «launch_mission.sh does not run them yet» расходится
с текущим ops-файлом; оператору нужно подтвердить, что импорт живого состояния действительно
требует отдельного окна.

## 6. Открытые риски

- **Критерии 3 и 5 не подтверждены прогоном**: команды прерываются на сборке из-за отсутствия
  `pyyaml` в venv (exit 2). Причина — окружение, а не код миссии; установка `pyyaml` — новая
  runtime-зависимость, её ставит оператор.
- **Staged-экспорт `data-tables/ops/wf_next.json` уже пропатчен** маркером Architect-блока,
  поэтому тесты `tests/test_architect_dependencies.py`, ожидающие первого применения патча,
  красные (раздел «Исправления Python» и «Пин (b)» отчёта части 4). Нужен свежий непропатченный
  экспорт живого workflow — шаг оператора.
- **9 новых mypy-ошибок `no-untyped-def`** на строках миссии в `tests/test_board_pg.py:76 (x2),
  332, 340, 348, 353, 361, 377, 383` — не закрыты: лимит части (одна строка в не более чем 2
  файлах) и SCOPE (`src/ahawr_runner/`) их не покрывают. Файл в целом стал чище (83 → 81).
- **Guard на run-next отсутствует**: маршрут `POST /api/board/missions/{mission_id}/run-next`
  (`ahawr-runner/src/ahawr_runner/board_api.py:1811`) не проверяет зависимости — заблокированная
  миссия уходит в начало без подтверждения run-anyway; нужен отдельный код-тест вне QA-части.
- **Версия для `mission_dependencies` не назначена**: `ahawr-runner/pyproject.toml:7` и
  `ahawr-runner/src/ahawr_runner/api.py:362` остаются на `1.2.020`; запись в `CHANGELOG.md`
  дополнена под заголовком `## 2026-11-14 (1.1.310)`, строка в `ROADMAP.md:184` — без номера
  версии. `ROADMAP.md` генерируется из `docs/roadmap.json` (`scripts/make_roadmap.py`), а
  `docs/roadmap.json` вне SCOPE — при следующем запуске генератора строка может пропасть.
- **Старые ошибки нетронутых файлов не правятся** по правилу задачи: `board_think.py` 9 ruff
  (8 E501 + SIM105), `board_stats.py` 2 mypy, `board_order.py` 20 mypy, `board_ops_*` 49 mypy
  (транзитивно через импорты).
- **QA-контракты и сценарии миссии не закоммичены** (`git status --short`): без них воспроизводимость
  приёмки теряется; в контракте `ahawr-mission-dependencies-p2` нет референс-изображения
  `mission-dependencies-reference.png` (нужен playwright/chromium) — единственный открытый блокер
  контракта (`contract.md:2-8`).
- **Линт плана ограничен** `LINT_RETRY_LIMIT = 1` (`plan_dependency_lint_patch.py:74`) и зависит от
  порядка патчей: без `plan_gate_retry_patch.py` патч выходит с сообщением об отсутствии якоря.
- **Чужие незакоммиченные правки** `features_defaults.json` (`adaptive_split`,
  `run_mission_from_card`) портят прогон чужих тестов (`tests/board_features.test.js`,
  `test_features_config*.py`), но к этой миссии отношения не имеют.
- **Конфликт RULES item 1**: правила миссии в `data-tables/missions/mission_ahawr-mission-dependencies.csv`
  ссылаются на старое имя `claude-runner/...`; вся работа сделана в фактических путях
  `ahawr-runner/`. Правила нужно привести к `ahawr-runner` или снять вопрос у оператора.
