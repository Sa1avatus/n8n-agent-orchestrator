# Миссия `ahawr-qa-project-targets`: итоговый отчёт (T001–T012)

Рабочий каталог: `/d/n8n`. Источники фактов: заметки T001 (бейслайн, `/tmp/ahawr-qa-project-targets/baseline.md`), T002 (дизейм-заметка, `/tmp/ahawr-qa-project-targets/design.md`), финальные проверки T012 (`/tmp/ahawr-qa-project-targets/checks.md`), живой прогон T010 (`/tmp/ahawr-qa-project-targets/run-output.txt`), текущий diff рабочего дерева. Никаких секретов и сырых логов здесь нет: цитаты — только ключевые строки команд.

## 1. Что сделано (файл:строка)

### 1.1 Строгий блок `ui` в профиле проекта — `ahawr-runner/src/ahawr_runner/qa_regression.py`
- `qa_regression.py:190` — `OPTIONAL_PROFILE_KEYS = frozenset({"ui")}`; `ui` — единственный optional ключ профиля. Профиль без `ui` остаётся прежним: `qa_regression.py:273` — `ui=_ui(entry["ui"], where) if "ui" in entry else None`.
- `qa_regression.py:191-198` — `UI_KEYS`: шесть обязательных ключей (`start_command`, `env`, `port_env`, `health_path`, `ready_timeout_seconds`, `scenarios_dir`), defaults в блоке нет.
- `qa_regression.py:277-286` — `_ui` rejects unknown keys (`:281-283`) и missing keys (`:285-286`); `qa_regression.py:219` — `class UiConfig`.
- Границы значений: `qa_regression.py:201-202` — `READY_TIMEOUT_MIN_SECONDS = 1`, `READY_TIMEOUT_MAX_SECONDS = 300`; проверка `:343-346`.
- Матчин профилей (`find_profile`) не менялся: профиль проекта выбирается по `working_directory` (дизейм T002 §1).

### 1.2 Лаунчер приложения — новый модуль `ahawr-runner/src/ahawr_runner/qa_app.py` (150 строк)
- `qa_app.py:59` — `def app_up(profile: Profile, host: str = QA_LOOPBACK_HOST) -> AppHandle`; `qa_app.py:109` — `def app_down(handle: AppHandle) -> None`.
- Только loopback: `qa_app.py:42` — `ALLOWED_APP_HOSTS = frozenset({QA_LOOPBACK_HOST, "localhost"})`, проверка `:122-123`; порт — `_free_port()` (`qa_app.py:35`, `:69`).
- Очищенное окружение: `qa_app.py:44-46` — `OS_BASIC_ENV_NAMES = frozenset({"PATH", "PATHEXT", "SystemRoot", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE"})` плюс только `env` профиля и `port_env` (`qa_app.py:132` `_scrubbed_env`); docstring `qa_app.py:6`: "No Docker: the child is the argument list of the profile".
- Одноразовый data-каталог: `qa_app.py:47` `APP_DATA_PREFIX = f"{QA_PREFIX}app-"`, `:71` — `<system temp>/qa-app-<run_id>`, удаление на всех путях: `:82`, `:85` (при неудачном старте) и `:112` (в `app_down`).
- Ограниченный опрос здоровья: `qa_app.py:100` — `max_polls=_ready_polls(ui.ready_timeout_seconds)` (`_ready_polls` — `:127`); reusable `_wait_until` из `qa_stack.py` теперь принимает `max_polls` (`qa_stack.py:507`). Ошибка старта (нет бинарника / не healthy) — `QaStackError`, которая в вердикте превращается в `blocked`, не pass и не fail.

### 1.3 Сценарии и `target` — `ahawr-runner/src/ahawr_runner/qa_runner.py`
- `qa_runner.py:64` — в `TOP_LEVEL_KEYS` добавлен `"target"`; `qa_runner.py:136` — `target: str = DASHBOARD_TARGET  # dashboard: the QA stack; app: the handle passed to the runner` (дефолт сохраняет старые сценарии без изменений); `:160-164` — `_target` принимает только `dashboard|app`, иначе ошибка с перечислением разрешённых.
- `qa_runner.py:443-445` — `load_project_scenarios(scenarios_dir, base_dir=SCENARIO_DIR)` читает сценарии только из `qa/scenarios/<scenarios_dir>/`.
- `qa_runner.py:461` — `run_browser_scenario` принимает handle приложения; `qa_runner.py:481-483` — при `target: app` без handle — blocked: `"target app: no app handle was given"`.
- Новая шаговая лексика (каждое добавление — со своим тестом): `viewport` (`qa_runner.py:35`, разбор `:274-291`, границы `:73` `VIEWPORT_MIN_PX = 100`, выполнение `:670-672`), `color_scheme` (`:36`, `:357`, выполнение `:674` `page.emulate_media(color_scheme=...)`), ожидание `no_horizontal_scroll` (`:52`, `:388-389`, проверка `:843-850`: "horizontal scroll: scrollWidth {scroll_width} > clientWidth {client_width}").

### 1.4 Фай и проводка ui-слоя — `ahawr-runner/src/ahawr_runner/qa_worker.py`
- `qa_worker.py:53` — `from ahawr_runner.roles_config import qa_project_apps`.
- `qa_worker.py:360-383` — `_ui_app_target`: `qa_worker.py:363` — `if not qa_project_apps(options) or not working_directory: return None` (файл off — профиль даже не читается).
- `qa_worker.py:386-405` — `_app_ui_runner`: `:397` `handle = qa_app.app_up(profile)`; сбой старта (`QaStackError`, `ValueError`) — blocked-вердикт с причиной (`:398-400`); `finally: qa_app.app_down(handle)` (`:403-405`, комментарий: "the app started here is torn down on every path, blocked and raised alike").
- `qa_worker.py:410` — `_app_layer_verdicts`; `qa_worker.py:436-448` — `_layer_runners` выбирает app-раннер только при файле и `ui`-блоке, иначе — прежний `_layer_command("ui", ["run"], working_directory)`.
- Тест байт-в-байт: `tests/test_qa_worker.py:506` `test_ui_layer_flag_off_posts_the_verdict_bytes_recorded_before_the_change` (комментарий `:394`: "the flag-off path must post exactly these bytes").

### 1.5 Файл `project_apps` — `ahawr-runner/src/ahawr_runner/roles_config.py`
- `roles_config.py:17-24` — `QA_OPTION_KEYS = COMMON_OPTION_KEYS | {... "project_apps"}`.
- `roles_config.py:195-196` — не-boolean отвергается: `role 'qa': option 'project_apps' must be true or false`.
- `roles_config.py:240-241` — `def qa_project_apps(...): return bool(options.get("project_apps", False))` — по умолчанию OFF.
- `roles_cases.json` и `board_roles.js` НЕ изменены (их нет в `git status`): флага нет в `QA_SETABLE_OPTIONS` доски, оператор задаёт его в `roles_json` (дизейм T002 §6).

### 1.6 Профиль и сценарии mi-find-tracker
- `ahawr-runner/qa/projects.yaml:15-24` — профиль `mi-find-tracker` (`working_directory: /d/ClaudeProjects/mi-find-tracker`); `:25-49` — `ui`-блок: `:28` `start_command: ["python", "-m", "mifind"]`, env (`:36-45`) — только фиксированные значения и плейсхолдеры `{data_dir}` (плейсхолдерный `DEVICE_ID` `:39`, `LOCATE_ENABLED: "false"` `:43`, `POLL_SECONDS: "30"` `:44`, `GOOGLE_MAPS_API_KEY` не задаётся), `:46` `port_env: HOST_PORT`, `:47` `health_path: /health`, `:48` `ready_timeout_seconds: 30`, `:49` `scenarios_dir: mi-find-tracker`.
- `ahawr-runner/qa/scenarios/mi-find-tracker/` — четыре сценария, все с `target: app`:
  - `page_loads_map_container.yaml:11` — страница (`open: /` :13), тексты `text_visible` :15, vendored Leaflet-бандл 2xx :16, `no_console_error` :17 (shortcut-комментарий `:3-6`: контейнер `#map` без текста сам, карта доказывается бандлом — "upgrade when a point-seeding precondition is available").
  - `google_key_absent_leaflet_fallback.yaml:15` — без Google-ключа: local Leaflet served, Google loader отсутствует, `text_absent: ["Google Карты не подключены"]` (см. раздел 4 — блокатор).
  - `health_and_api_status_answer_2xx.yaml:11` — `/health` и `/api/status` 2xx, `api_get: path: /api/status` с извлечением `last_outcome`.
  - `narrow_375px_dark_scheme.yaml:10` — `viewport: width 375`, `no_horizontal_scroll`, `color_scheme: dark`, `no_console_error`.
- `tests/test_qa_regression.py:256` — ожидание списка профилей: `["ahawr-runner", "mi-find-tracker"]`.

### 1.7 Тесты
- Новые файлы: `ahawr-runner/tests/test_qa_app.py` (7 тестов, парсер launcher'а на фейковой команде: свободный порт, ready, timeout → blocked, kill дерева, удаление data-каталога, очищенное окружение; `test_qa_app.py:229` — `pytest.importorskip("yaml")`), `ahawr-runner/tests/test_qa_profile_ui.py` (15 тестов: хороший блок, unknown key, shell-строка, границы, плейсхолдеры, профиль/сценарии mi-find-tracker).
- `tests/test_qa_runner.py` — новые тесты target/viewport/scheme/scroll (например `:123` `test_target_accepts_dashboard_and_app`, `:1014` `test_shipped_scenarios_parse_identically_with_the_default_target`); `tests/test_qa_worker.py` (diff: 387 добавленных строк, 3 удалённых; тест off-байтов `:506`); `tests/test_roles_config.py` (+25 строк, `:216-221` — отсутствие ключа читается как False).

### 1.8 Документы
- `ahawr-runner/docs/qa-layers.md`: строка ui-слоя `:16` ("the QA stack dashboard, or the project's own app on `127.0.0.1` when `options.project_apps` is true"); описание флага `:91-92`; секция `The \`ui\` block of a project profile` (`:121`); секция `Project apps: the \`project_apps\` flag` (`:197-224`) с rollback (`:224`) и таблицей лимитов (`:233-236`: loopback only, no Docker, scrubbed environment, throwaway data dir).
- `ahawr-runner/docs/qa-gate.md`: упоминания флага там, где изменились утверждения гейта — `:19`, `:42`, `:145` ("Add `\"project_apps\": true` only for the projects whose own app should run the `ui` layer; the key is absent by default").

## 2. Какие проверки реально выполнялись (все цитаты — вывод T012, `/tmp/ahawr-qa-project-targets/checks.md`)

**pytest.** Команда: `python -m pytest tests/test_qa_profile_ui.py tests/test_qa_app.py tests/test_qa_runner.py tests/test_qa_worker.py tests/test_qa_regression.py tests/test_roles_config.py -q --no-cov` (exit 1). Ключевая строка: "265 dots passed, 100 F failed, 1 s skipped". Причины падений: "99 tracebacks \"ModuleNotFoundError: No module named 'yaml'\" (PyYAML missing in this environment), 1 genuine \"Failed: DID NOT RAISE QaStackError\" (test_qa_runner.py::test_run_is_blocked_without_playwright_and_starts_no_stack) — the same single genuine failure as the T001 baseline". Сравнение с бейслайном T001: "all 58 T001 failing names still fail (comm -23 base vs now = empty); 42 new failing names (20 test_qa_profile_ui + 22 test_qa_runner), all yaml-caused." То есть новых падений по сравнению с бейслайном нет, кроме 42, вызванных отсутствием PyYAML в среде (не кодом миссии).

**ruff.** "src modules (T001 §9 command re-run now): \"All checks passed!\" — qa_stack, qa_runner, qa_worker, qa_regression, roles_config: 0 before, 0 after. qa_app.py (new module): All checks passed! (0)." Тестовые файлы: "tests/test_qa_runner.py: 2 errors before (HEAD blob, repo rules) -> 2 after. Both are E501 (958:101 104>100, 967:101 101>100); ... they are pre-existing lint debt, not introduced by this mission." `test_qa_worker.py 0 -> 0; test_qa_regression.py 0 -> 0; test_roles_config.py 0 -> 0; test_qa_app.py (new) 0; test_qa_profile_ui.py (new) 0."

**mypy (strict).** До: "Found 1 error in 1 file" — `src/ahawr_runner/board_ops_redact.py:124: error: Incompatible types in assignment [assignment]`; 5 целевых модулей — 0. После (6 модулей, включая `qa_app.py`): "Found 1 error in 1 file (checked 6 source files)" — та же ошибка `board_ops_redact.py:124`; "the 6 target modules 0 errors". Тестовые файлы mypy не проверял (объём задачи — модули).

**git status --short / git diff --check.** Перечень изменённых tracked-файлов: только `ahawr-runner/docs/qa-gate.md`, `ahawr-runner/docs/qa-layers.md`, `ahawr-runner/qa/projects.yaml`, `qa_regression.py`, `qa_runner.py`, `qa_stack.py`, `qa_worker.py`, `roles_config.py` и четыре тест-файла; новые untracked внутри разрешённых путей: `ahawr-runner/qa/scenarios/mi-find-tracker/`, `ahawr-runner/src/ahawr_runner/qa_app.py`, `ahawr-runner/tests/test_qa_app.py`, `ahawr-runner/tests/test_qa_profile_ui.py`. "git diff --check: 9 lines, all \"warning: in the working copy ... LF will be replaced by CRLF\"; no trailing-whitespace/conflict-marker errors; exit 0".

**Присутствие Playwright и worker'а (T012 §6, перецитировано).** `python -c "import playwright; print(playwright.__version__)"` -> `ModuleNotFoundError: No module named 'playwright'` (exit 1). `ps -ef | grep -i "qa_worker.py" | grep -v "grep -i"` -> 0 lines; "No qa_worker.py process is running; the runner (ahawr-runner --host 0.0.0.0 --port 8700) and hermes gateway are running, as in T001 §8". `data-tables/ops/qa_worker.py` на месте: `-rwxrwxrwx 1 root root 514 Nov 5 07:29`. `python -c "import yaml"` -> `ModuleNotFoundError: No module named 'yaml'`.

**Живой прогон приложения (T010, `/tmp/ahawr-qa-project-targets/run-output.txt`).** Реальный запуск `python -m mifind` через `qa_app.app_up` из профиля `projects.yaml` (venv runner'а не имеет PyYAML, поэтому скрипт использовал staged под `PYYAML_VENDOR = Path("/tmp/pyyaml-vendored")` — shortcut в скрипте). Ключевые строки: `INFO:     Uvicorn running on http://127.0.0.1:56917`, `GET /health -> 200 body: {"status":"ok"}`, `GET /api/status -> 200 body: {"last_outcome":"error","last_reason":"request failed: CurlFileError",...`, `scenarios: skipped: playwright missing`, `app_down: returncode=-9 data_dir exists=False`, `ps lines matching 'qa-app-': none`. То есть старт, health и status проверены в реальном процессе; браузерные сценарии пропущены по предусловии (Playwright отсутствует).

**node-тесты.** "Node test (*.test.js): none modified by the mission (git status has no test.js entry), so no node run."

**Проверка acceptance после написания этого отчёта.** `git status --short` в `/d/n8n` показывает ровно тот же набор изменённых файлов, что T012 §5, плюс единственную новую строку `?? reports/ahawr-qa-project-targets-report-ru.md` (этот отчёт). Никаких других изменённых или новых файлов нет.

## 3. Какие проверки не выполнялись и почему

- **Исполнение браузерных сценариев** — не выполнялось: Playwright отсутствует в среде (цитата выше), сценарии пропущены и в живом прогоне T010 ("scenarios: skipped: playwright missing"). Существующий паттерн `importorskip` сохранён (`test_qa_app.py:229`).
- **Парсинг shipped-профиля и shipped-сценариев mi-find-tracker** в pytest не запускался: 42 новых падения — все `ModuleNotFoundError: No module named 'yaml'` (PyYAML не установлен; правила задачи запрещают `pip install`). Реальный парсинг профиля подтверждён живым прогоном T010 (app_up прочитал `projects.yaml` через vendored PyYAML и поднял приложение); парсинг самих сценариев в браузере не подтверждён — он подтверждён только тем, что `load_project_scenarios` — существующий путь (`qa_runner.py:443-445`), а сценарии написаны в том же vocabulary, что board-сценарии.
- **mypy на тестовых файлах** — не запускался (объём задачи — src-модули).
- **Полный прогон pytest** (все tests) не запускался: только шесть целевых файлов (правила задачи).
- **Проверка flag-off "байт-в-байт"** в pytest прошла (единственное падение `test_qa_worker.py` — baseline-тест из бейслайна T001), но отдельного запуска именно этого теста в T012 не было: он вошёл в 265 passed.

## 4. Блокатор по mi-find-tracker

Приложений-блокатора по старту нет: T001 §0 ("`/d/ClaudeProjects/mi-find-tracker/src/mifind/app.py` — exists"), живой прогон T010 поднял приложение без секретов (профиль задаёт `DEVICE_ID: qa-placeholder-device` и curl-дампы `{data_dir}/no-status-dump.txt` / `{data_dir}/no-locate-dump.txt` — несуществующие файлы, поэтому poller пишет `CurlFileError` и не обращается к Xiaomi; `LOCATE_ENABLED: false`).

Есть один текстовый блокатор, зафиксированный в самом сценарии (`qa/scenarios/mi-find-tracker/google_key_absent_leaflet_fallback.yaml`, комментарий `blocker:`): "The profile never sets GOOGLE_MAPS_API_KEY ... blocker: the task asks for the note \"Google Карты не подключены\"; that text is not in the app. The project asserts it is absent: tests/test_page.py:30 defines NOTE = \"Google Карты не подключены: задайте GOOGLE_MAPS_API_KEY\" and tests/test_page.py:103 asserts \"the missing-key note was removed: no key is a normal setup\" ... The operator must add the note in the app before it can be asserted as visible." Факт проверен: `grep -rn "Карты" src tests` в проекте даёт только `tests/test_page.py:30` (`NOTE = "Google Карты не подключены: задайте GOOGLE_MAPS_API_KEY"`) и `tests/test_tiles.py:57` (`assert "Google Карты не подключены" not in html`); в `src/mifind/app.py` ни `Карты`, ни `KEY_NOTE`/`NOTE` не встречается (0 совпадений). Поэтому сценарий проверяет fallback как присутствие vendored Leaflet-бандла (`request_2xx: [/static/vendor/leaflet.js, /static/vendor/leaflet.css]`) и `text_absent` заметки. Проект не редактировался (read-only).

## 5. Шаги оператора (записаны, НЕ выполнены)

1. `pip install playwright` — в среде сейчас: `python -c "import playwright; print(playwright.__version__)"` -> `ModuleNotFoundError: No module named 'playwright'` (T012 §6).
2. `playwright install chromium` — после шага 1.
3. Запустить host-worker `data-tables/ops/qa_worker.py` (существующая watcher-строка). Файл на месте (`-rwxrwxrwx 1 root root 514 Nov 5 07:29`), процесс не запущен: "ps -ef | grep -i \"qa_worker.py\" ... -> 0 lines".
4. В `roles_json` для роли `qa` задать `options.project_apps: true` (по умолчанию ключ отсутствует — OFF; `roles_config.py:240-241`).
Ничего не устанавливалось и не запускалось миссией (правила задачи).

## 6. Rollback

Из `docs/qa-layers.md:224-227`: "**Rollback:** set `\"project_apps\": false`, or drop the key, in the `qa` options. With the flag off `_ui_app_target` returns `None` at `qa_worker.py:363` before any profile is read, so the `ui` layer takes the qa child path. No restart or container change is involved, and the `ui` layer behaves as it did before the flag existed." Отдельного откатного скрипта не требуется: код `qa_app.py` без флага не вызывается ни с какого пути.

## 7. Открытые риски

1. **Файл baseline для mi-find-tracker отсутствует**: `qa/baselines/` содержит только `ahawr-runner.json`; `projects.yaml:24` ссылается на `qa/baselines/mi-find-tracker.json`. Regression-слой для этого проекта заработает только после создания baseline-файла.
2. **Браузерная часть не проверена исполнением**: пока не установлены Playwright+Chromium на host, `ui`-слой mi-find-tracker будет давать `blocked` (по дизайну — не fail миссии).
3. **42 падения из-за PyYAML**: тесты парсера `ui`-блока и парсинга shipped-сценариев в этой среде не запускались; их нужно перезапустить после `pip install pyyaml` (qa extra) на хосте.
4. **Заметка "Google Карты не подключены" отсутствует в приложении** (раздел 4): до её добавления оператором сценарий проверяет её как `text_absent` — проверка "note visible" из плана миссии не выполнена.
5. **Флаг недоступен из UI доски** (`board_roles.js` QA_SETABLE_OPTIONS не менялся): оператор должен редактировать `roles_json` вручную.
6. **Живой прогон убил приложение сигналом**: `app_down: returncode=-9` — `_kill_tree` SIGKILL'яет дерево (штатный путь), graceful shutdown uvicorn не подтверждён.
7. **Линт-долг HEAD не изменился**: 2 E501 в `tests/test_qa_runner.py` (строки 958, 967) остаются — посчитано "2 before -> 2 after".
8. `env` профиля использует плейсхолдеры (`DEVICE_ID`, curl-дампы): poller приложения в QA-прогоне фиксирует `CurlFileError` — данные /api/status в сценариях могут быть error-состоянием.

## Что не проверялось и остаётся риском

- Исполнение четырёх mi-find-tracker сценариев в реальном браузере (нет Playwright — раздел 3); проверены только старт приложения и 2xx ответов health/status (T010).
- Парсинг `ui`-блока и сценариев pytest-тестами в этой среде (PyYAML отсутствует) — подтверждён живой прогон T010 для профиля и чтение сценариев `load_project_scenarios` без запуска шагов.
- mypy на тестовых файлах и полный pytest не запускались по правилам задачи.
- `roles_cases.json` не дополнен кейсом `project_apps` (файл не менялся; ключ валидируется `roles_config.py:195-196` и тестами `test_roles_config.py`).
