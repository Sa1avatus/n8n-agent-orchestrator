# Отчёт о миссии ahawr-features-one-source: один источник умолчаний (T001–T019)

Дата: 2026-11-13. Рабочая папка: `/d/n8n` (MSYS: `/d/n8n`), runner — `/d/n8n/ahawr-runner`.
Доказательная база: `reports/ahawr-features-one-source-evidence/baseline.txt` (T001, T002, T003),
`pinned-runs.txt` (T004–T013), `final-checks.txt` (T018, попытка 2). Файл этого отчёта — единственное,
что создала задача T019; больше файлов она не меняет.

Итог: умолчания и минимумы параметров функций живут в одном файле
`ahawr-runner/src/ahawr_runner/features_defaults.json`; его читают `features_config.py`, маршрут
`GET /api/board/features`, тесты JS и патч `data-tables/ops/features_config_patch.py`. Поведение
не изменилось: все 7 функций по умолчанию выключены, pinned-фиксстуры байт-в-байт совпадают с
до реформы. Патч написан и проверен на копии экспорта, но в живой workflow не применён —
применение делает оператор в паузе.

## 1. Где один источник и кто его читает

`ahawr-runner/src/ahawr_runner/features_defaults.json` (новый, 37 строк, `git status --short`
показывает его как `?? ahawr-runner/src/ahawr_runner/features_defaults.json`):

- секция `features` — 7 функций (`plan_no_truncate`, `adaptive_split`, `run_hygiene`,
  `event_schema_version`, `think_profiles`, `stats_popup`, `stats_engine_sampler`), у каждой
  `enabled: false`;
- секция `param_minimums` — 5 ключей (`max_tasks`, `max_tasks_hard`, `split_steps`, `max_splits`,
  `max_total_tasks`).

Единственные строки с числами (`grep -rn "split_steps" src/ahawr_runner/features_defaults.json`):

```
src/ahawr_runner/features_defaults.json:10:      "split_steps": 150,
src/ahawr_runner/features_defaults.json:33:    "split_steps": 1,
```

`150` — default для `adaptive_split`, `1` — минимум. Читатели файла:

| Кто | Где | Как |
|---|---|---|
| Python-ядро | `src/ahawr_runner/features_config.py:8` (`DEFAULTS_PATH = Path(__file__).with_name("features_defaults.json")`), `:21` `_load_defaults()`, `:59` `FEATURE_DEFAULTS, PARAM_MINIMUMS = _load_defaults()` | один импорт значений, в коде литералов нет |
| Маршрут доски | `src/ahawr_runner/board_api.py:1943-1944` (`"feature_defaults": features_config.FEATURE_DEFAULTS`, `"param_minimums": features_config.PARAM_MINIMUMS`) | ответ `GET /api/board/features` отдаёт умолчания панели |
| JS-ядро | `src/ahawr_runner/board_features.js:15` `setFeatureDefaults(answer)`, комментарий `:3-5`, экспорт `:138` | берёт defaults только из ответа маршрута; `grep -c "FEATURE_DEFAULTS" src/ahawr_runner/board_features.js` = 0 |
| Патч Constants | `data-tables/ops/features_config_patch.py:22` (путь к `features_defaults.json` через `parents[2]`), чтение при патче, причины отказа `:180`, `:204` | пишет значения в `Constants` из JSON |
| Тесты | `tests/board_features.test.js:14`, `tests/test_features_config.py:47`, `tests/test_features_config_patch.py:31-32` | проверяют, что файл — единственный источник |

## 2. Что изменено (файл:строка)

`git diff --stat` по всему репозиторию: 16 файлов, 506 вставок, 75 удалений (в эту сумму
входит и чужой `ahawr-runner/qa/contracts/audio-dub-ui-redesign/contract.md`, +8; по файлам
миссии — 15 файлов, 498/75). Новые файлы из `git status --short`: 81 запись сейчас, включая
этот отчёт (80 до него; `final-checks.txt:167` фиксировал 79).

| Файл | Строки | Что |
|---|---|---|
| `ahawr-runner/src/ahawr_runner/features_defaults.json` | 1-37 (новый) | `features` + `param_minimums`, все `enabled: false` |
| `ahawr-runner/src/ahawr_runner/features_config.py` | `:8`, `:21-38`, `:59` | загрузка вместо inline-таблиц; `64 ++++---` |
| `ahawr-runner/src/ahawr_runner/board_api.py` | `:1943-1944` | два новых поля ответа маршрута (`4 +-`) |
| `ahawr-runner/src/ahawr_runner/board_features.js` | `:1-5`, `:15`, `:128`, `:138` | копий `FEATURE_DEFAULTS` больше нет; `66 ++++---` |
| `ahawr-runner/src/ahawr_runner/api.py` | `:362` | только версия `1.1.140` → `1.1.150` (`2 +-`) |
| `ahawr-runner/pyproject.toml` | `:7` | `version = "1.1.150"` (`2 +-`) |
| `ahawr-runner/tests/test_features_config.py` | `:47` (`PINNED_FIXTURE`), `:66`, `:73`, `:81`, `:94`, `:219-262` | pinned-фикстура и тесты «литералов нет», «все off» (`200 +++++++`) |
| `ahawr-runner/tests/board_features.test.js` | `:14`, `:63-64`, `:101`, `:118` | тесты получают defaults из JSON (`87 +++++++-`) |
| `ahawr-runner/tests/test_board_features_api.py` | `:70-72`, `:83-88`, `:100` | сверка полей маршрута с файлом (`49 +++++`) |
| `ahawr-runner/tests/test_features_config_patch.py` | новый, 287 строк; `:119`, `:127`, `:150`, `:172`, `:201`, `:219`, `:248`, `:270` | 8 тестов патча, идемпотентность, синтаксис, abort-пути |
| `ahawr-runner/tests/fixtures/features_pinned.json` | новый | pinned разрешённых значений Python |
| `ahawr-runner/tests/fixtures/board_features_pinned.json` | новый | pinned той же формы для JS |
| `ahawr-runner/tests/fixtures/features_constants_pinned.txt` | новый, 217 строк | эталон текста `Constants` (10966 байт) |
| `data-tables/ops/features_config_patch.py` | `:7`, `:11`, `:22`, `:127`, `:154`, `:180`, `:200-204` | чтение defaults из JSON при патче; `split_steps` только как имя, не значение |
| `ahawr-runner/docs/features-config.md` | `:7`, `:30`, `:41`, `:46`, `:66`, `:77` | «один источник; новая функция — одна запись в `features_defaults.json`» (`62 +++++--`) |
| `docs/adaptive-split.md` | `:55-60` | ссылки на старый источник заменены на JSON (`9 +-`) |
| `docs/config-overrides.md` | `:54-55` | номера строк патча `:29`/`:36` → `:137`/`:144` (`4 +-`) |
| `docs/config-thinking.md` | `:151`, `:156` | ссылки на якоря патча (`4 +-`) |
| `CHANGELOG.md` | `:6`, `:8-13` | запись `1.1.150` (`15 +-`) |
| `README.md` | `:15` | версия `1.1.150` (`2 +-`) |
| `ROADMAP.md` | `:15`, `:182` | версия и закрытый пункт (`3 +-`) |
| `ahawr-runner/qa/scenarios/board_features_panel.yaml` | новый (не в git) | QA-сценарий панели Features |

Файлы чужих миссий в `git status` (`ahawr-runner/qa/contracts/audio-dub-ui-redesign/contract.md`,
`docs/naming.md`, `docs/guards.md`, `docs/codex-executor.md`, `docs/dashboard-lan-access.md`,
`docs/retrieval/*`, `evals/worker-suite/*`, `litellm/*`, `scripts/*`) этой задачей не тронуты —
это незакоммиченная работа других миссий (зафиксировано в `baseline.txt`, раздел 6).

## 3. Pin прогонов: строки «до» и «после»

`python -m pytest tests/test_features_config.py` (число тестов растёт, ни одного провала):

```
before (baseline.txt:111):  18 passed, 1 warning in 1.43s
before (T004, attempt 3):   19 passed, 1 warning in 1.51s
after (T007, :61):          20 passed, 1 warning in 1.38s
after (T018, :14):          24 passed, 1 warning in 1.43s
```

`node --test tests/board_features.test.js`:

```
before (baseline.txt:113-116):  # tests 13  # pass 13  # fail 0
after (T012, :77-79):           # tests 46  # pass 46  # fail 0
```

Хешей pinned-фикстур не менялся (pinned-runs.txt и final-checks.txt, повторено в T019):

```
d98d2158a21ca6ee02b37982f2639fb6e585a7de6ecdf4aef4ea9e47a0236d04  tests/fixtures/features_pinned.json
25e94a56760bc1dd68f7ef0dc86fa58fb872150ace2eb058aa21b02a1fd00ff  tests/fixtures/board_features_pinned.json
efc931564af32447cad446f922dcc9d32bba148110fbada0e2c2daa05654554b  tests/fixtures/features_constants_pinned.txt
```

Текст `Constants` патча и эталон совпадают (`pinned-runs.txt:110`):
`byte_identical_with_fixture: True  len_code: 10966 len_pinned: 10966  diff_lines: none`;
второй прогон — `copy.json: already patched`, хеш копии после первого и второго прогона одинаковый
(`40e587d3dbcd498e0a113cce0be7305ae122d0479a2deb4aa7d5d0062977594a`).

## 4. Проверки, которые реально выполнены

Все команды — из `/d/n8n/ahawr-runner`, если путь не абсолютный.

| Команда | Ключевая строка |
|---|---|
| `python -m pytest tests/test_features_config.py --no-cov` (T019) | `24 passed, 1 warning in 1.10s`, exit 0 |
| `node --test tests/board_features.test.js` (T019) | `# tests 46` / `# pass 46` / `# fail 0` |
| `python -m pytest tests/test_features_config_patch.py --no-cov` (T019) | `8 passed, 1 warning in 1.72s` |
| `python -m pytest tests/test_board_features_api.py --no-cov` (T019) | `17 passed, 1 warning in 5.74s` |
| `python -m pytest tests/test_features_config_patch.py --no-cov -k pinned` (T018) | `1 passed, 7 deselected, 1 warning in 1.22s` |
| `python -m pytest tests/ -q --no-cov -k features_defaults --ignore=tests/test_qa_container_static.py` (T018) | `14 passed, 2 skipped, 3429 deselected, 1 warning in 10.35s` |
| `python -m ruff check src/ahawr_runner/features_config.py src/ahawr_runner/api.py tests/test_features_config.py tests/test_board_features_api.py tests/test_features_config_patch.py` (T019) | `All checks passed!` |
| `python -m ruff check src/ahawr_runner/board_api.py` (T019) | 29 ошибок, `grep -c '^E501'` = 29 — ровно как в baseline.txt:130 |
| `python -m ruff check ../data-tables/ops/features_config_patch.py` (T018) | `Found 2 errors.` (E501 на `:237` и `:238`, старые комментарии) |
| `python -m mypy src/ahawr_runner/features_config.py` (T019) | `Success: no issues found in 1 source file` |
| `python -m mypy ../data-tables/ops/features_config_patch.py` (T019) | `Success: no issues found in 1 source file` |
| `python -m mypy src/ahawr_runner/board_api.py` (T018) | `Found 79 errors in 9 files`, 16 в `board_api.py` — те же, что до миссии |
| `node --test tests/board_i18n.test.js` (T019) | `# tests 1` / `# pass 1` / `# fail 0` (ключи i18n не трогались) |
| `git diff --check -- CHANGELOG.md README.md ROADMAP.md ahawr-runner/pyproject.toml ahawr-runner/docs/features-config.md ahawr-runner/src/ahawr_runner/{features_config,api,board_api}.py ahawr-runner/src/ahawr_runner/board_features.js ahawr-runner/tests/{board_features.test.js,test_features_config.py,test_board_features_api.py}` (T019) | exit 0, вывода нет |
| `git diff --check` (T019, целиком) | exit 2, одна находка: `ahawr-runner/qa/contracts/audio-dub-ui-redesign/contract.md:69: new blank line at EOF.` — EOF чужого файла другой миссии, не нашего |
| `grep -c "setFeatureDefaults" data-tables/ops/wf_next.json` (T019) | `0` — нового ядра в staged-экспорте нет, старой копии с literals там хватает (риск 1) |
| `ls -l data-tables/ops/_scratch/live_export.json` (T019) | `221093 Oct 21 12:33` — копия экспорта не записывалась |

Mypy-счёт по `api.py` не изменился по существу: в combined-прогоне `Found 80 errors in 10 files`
= 79 старых ошибок импортируемых модулей + `api.py:285: Unused "type: ignore" comment`,
которая стоит с коммита `e70a18515` (2026-10-27), до миссии. В `pyproject.toml` нет `mypy_path`,
поэтому `mypy tests/test_features_config.py` в одиночку даёт 10 `attr-defined`: он видит
установленный в venv `ahawr_runner-1.1.110` вместо исходников. С `MYPYPATH=src` — `Success: no issues found in 1 source file`. Дефекта в коде нет.

## 5. Проверки, которые не выполнялись

- **Полная тестовая suite** — не запускалась: задача меняет только этот отчёт, CLAUDE.md требует
  запускать только те тесты, на которые влияет изменение.
- **`tests/test_qa_container_static.py`** — не запускается: `import yaml` даёт
  `ModuleNotFoundError: No module named 'yaml'`; PyYAML нет в venv, ставить её нельзя (новая
  runtime-зависимость запрещена).
- **Запуск приложения/дашборда (живая smoke-проверка)** — не делалась: в `pinned-runs.txt` нет
  записи T017, поэтому сравнивать «до» не с чем; применяется оператором в паузе.
- **ruff/mypy на `board_features.js` и `features_defaults.json`** — неприменимы (JS и JSON).
- **docker, очередь, контейнеры** — не запускались никогда, по границам репозитория.
- **Патч в живой workflow** — не проверялся применением (см. раздел 6, риск 1).

## 6. Открытые риски

1. **Патч написан, но не применён.** `data-tables/ops/features_config_patch.py` изменён
   (mtime `Nov 13 19:03`, 11048 байт). `rules_json` прямо запрещает Worker'у менять workflow
   («Не менять workflow n8n … (читать можно)»), пункт 2: «Применение делает оператор в паузе».
   Проверено на staged-экпорте `data-tables/ops/wf_next.json` (mtime `Nov 13 20:09`, 373862 байт):
   `grep -c "setFeatureDefaults" = 0`, `grep -c "FEATURE_DEFAULTS" = 1`, а маркер нового ядра
   `setFeatureDefaults` в `board_features.js` отсутствует — то есть `Constants` в workflow
   по-прежнему несёт старую копию с литералами:

   ```
   FEATURE_DEFAULTS = {\n  plan_no_truncate: { enabled: false, max_tasks: 15, max_tasks_hard: 30 },
   grep -c "split_steps: 150" data-tables/ops/wf_next.json  -> 1
   grep -c "pure features core" data-tables/ops/wf_next.json -> 1
   grep -c "pure features core" data-tables/ops/_scratch/live_export.json -> 0
   ```

   Копия `_scratch/live_export.json`, на которой тесты патча работают, — вообще без маркера ядра,
   поэтому применение патча на свежем экспорте никто не выполнял.
2. **Конфликт нумерованных RULES и OBJECTIVE.** `rules_json` миссии
   (`data-tables/missions/mission_ahawr-features-one-source.csv`) разрешает править только
   `claude-runner/src/claude_runner (board*.py …)`, `claude-runner/tests`, `claude-runner/docs`,
   `claude-runner/README.md`, `CHANGELOG.md`, `docker-compose.yml` и
   `D:\n8n\data-tables\ops\board_agent.py, board_settings.py и их тесты` — это белый список
   миссии про `board_agent` и устаревшие пути (переименование `claude-runner` → `ahawr-runner`
   зафиксировано в `CHANGELOG.md:190`). OBJECTIVE называет
   `ahawr-runner/src/ahawr_runner/features_defaults.json`, `board_features.js`,
   `data-tables/ops/features_config_patch.py`. Разрешение выполнено по OBJECTIVE: файлов по
   «claude-runner/…» в репозитории нет; `board_agent.py` и `board_settings.py` не тронуты.
3. **Какая копия живого экспорта использована.** Тесты патча работают только на копии
   `/d/n8n/data-tables/ops/_scratch/live_export.json` — 221093 байт, mtime `Oct 21 12:33`
   (2026-10-21), то есть несвежей. `AHAWR_Workflow.json` (252278 байт, `Nov 9 15:01`) как источник
   фактов не использовался. Якоря патча проверены именно на этой копии; на свежем экспорте их
   никто не проверял.
4. **Фикстура `features_constants_pinned.txt` не в git.** В первой попытке T018 её не было;
   в попытке 2 её восстановили байт-в-байт из дампа `/tmp/t013run/constants_code.txt`
   (10966 байт, тот же хеш). `git log --all` по файлу пуст. Если `/tmp` очистят, тест
   `test_features_config_patch.py:201` просто пропустится (`skipif` на `:199-200`), а не провалится.
5. **Охранный паттерн не покрывает новый JSON.** В `config.py:254-262`
   `DEFAULT_RISK_TIER_PATTERNS` не совпадает с `features_defaults.json` (`baseline.txt`, раздел 7.3),
   поэтому правка файла умолчаний не попадает ни в один tier guards.
6. **NOT RUN (список):** полная suite, `test_qa_container_static.py` (нет PyYAML), живая
   smoke-проверка дашборда, применение патча к workflow. Их перечисляет и `final-checks.txt` (раздел 6).
7. **Версия 1.1.150** поднята в `pyproject.toml:7` и `api.py:362`; в работающем стеке релиз не
   проверялся (контейнеры не трогались).

## 7. Что сделала задача T019

Создан только этот файл: `reports/ahawr-features-one-source-report-ru.md`.
Прочитаны `baseline.txt`, `pinned-runs.txt`, `final-checks.txt`,
`git status --short` (81 запись с этим отчётом) и `git diff --stat` (16 файлов, 506/75).

Spot-check пяти утверждений отчёта (команды выполнены в T019):

- `grep -n "split_steps" src/ahawr_runner/features_config.py src/ahawr_runner/board_features.js ../data-tables/ops/features_config_patch.py`
  → четыре совпадения (`features_config.py:171`, `board_features.js:128`,
  `features_config_patch.py:127` и `:154`), нигде нет значения — только имена;
- `grep -c "FEATURE_DEFAULTS" src/ahawr_runner/board_features.js` → `0`;
- `sha256sum tests/fixtures/features_pinned.json` → `d98d2158…36d04`, тот же, что в
  `pinned-runs.txt:6,13,24,63` и `final-checks.txt:27`;
- `ls -l data-tables/ops/_scratch/live_export.json` → `221093 Oct 21 12:33` (2026-10-21);
- `git diff --check` → exit 2, одна находка `ahawr-runner/qa/contracts/audio-dub-ui-redesign/contract.md:69: new blank line at EOF.` —
  чужой файл; по файлам миссии `git diff --check` exit 0.

## Что не проверено и риски в конце

- Применение патча к живой workflow и перезапуск runner — не делались (границы репозитория);
  без этого `Constants` в n8n остаётся со старой копией (риск 1).
- Совпадение `features_defaults.json` с `HEAD` проверялось в T018 через `ast.literal_eval`
  (`features match: True`, `param_minimums match: True`, `feature count HEAD/JSON: 7 7`);
  в T019 я это не повторял, опираюсь на `final-checks.txt:79-86`.
- Полная suite и `test_qa_container_static.py` не запускались (нет PyYAML).
- Открытые риски: патч не применён; конфликт RULES (`claude-runner/…`) и OBJECTIVE; тесты
  опираются на несвежую копию экспорта (2026-10-21); фикстура `features_constants_pinned.txt`
  не в git и восстановлена из `/tmp`; новый JSON не покрыт паттернами guards.
