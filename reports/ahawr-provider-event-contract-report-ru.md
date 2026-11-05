# Миссия `ahawr-provider-event-contract`: итоговый отчёт (T001A–T010)

Дата: 2026-11-05. Рабочая директория: `/d/n8n` (`D:\n8n`).
Задача T011: **код в этой задаче не изменён** — написан только этот файл
`reports/ahawr-provider-event-contract-report-ru.md`. Все ссылки на строки проверены
в текущем рабочем дереве (uncommitted-диффы).

## 0. Оговорка про RULES 1 и критерии приёмки в тексте задачи

`RULES 1` и критерии приёмки, приложенные к тексту этой задачи, описывают **другую
миссию** — библиотеку уроков: они требуют менять `board_lessons.py`,
`board_lessons_extract.py`, `board_lessons_ui.js`, `docs/lessons.md`,
`data-tables/ops/lessons_patch.py` и флаг `lessons_enabled`
(`data-tables/ops/_make_lessons_mission.py:29`, `:37-45`). Миссия `ahawr-provider-event-contract`
создана из того же шаблона (`SOURCE = "ahawr-lessons-library"`,
`data-tables/ops/_make_contract_mission.py:8`), поэтому эти пункты в её тексте остались
от исходной миссии. **Ничего из этих пунктов в T001A–T010 не реализовано и не должно
было реализовываться**: настоящие критерии приёмки этого контракта —
`data-tables/ops/_make_contract_mission.py:29-36`.

Точный список задач `T001A…T010` лежит только в базе доски (`curl` до
`http://127.0.0.1:8701/api/board/missions/ahawr-provider-event-contract` в этой среде
не дал вывода — дора unreachable). Ниже соответствие «файл ↔ задача» восстановлено из
диффов; это вывод, а не цитата из доски.

## 1. Что изменилось (file:line)

### Валидатор контракта (новый модуль)
- `claude-runner/src/claude_runner/contract_schema.py` — новый файл, 275 строк:
  - `:17` `FieldSpec = tuple[str, bool, str]` (поле, обязательность, тип);
  - `:19` `ENGINE_VALUES = ("claude", "codex", "hermes")`;
  - `:23` `PROVIDER_BLOCK_FIELDS` — 30 ключей провайдер-блока (из `config.py`, ничего
  обязательного нет: unknown-ключ игнорируется);
  - `:55` `EVENT_ENVELOPE_FIELDS` — `seq`, `t`, `kind` обязательны, `agent`, `step` опциональны;
  - `:64` `EVENT_KIND_FIELDS` — 20 видов событий (`prompt`, `init`, `user`, `thinking`,
  `text`, `tool_use`, `tool_result`, `step`, `retry`, `compact`, `result`, `end`,
  `git_snapshot`, `thinking_state`, `thinking_switch`, `context_window`, `qa_verdict`,
  `qa_scenario`, `mem_guard`, `notice`);
  - `:203` `TYPE_CHECKS`;
  - `:214` `_check_schema_version` — «отсутствует = версия 0», присутствует → должен быть int;
  - `:224` `_check_fields` — отсутствие обязательного поля и неверный тип; поля вне таблицы —
  не ошибка;
  - `:247` `validate_provider_block`;
  - `:261` `validate_run_event`, `:272` — сообщение `unknown event kind '…'`.

### Общий писатель событий
- `claude-runner/src/claude_runner/events.py:21` — импорт `validate_run_event`;
- `:27-28` — константа `EVENT_SCHEMA_VERSION = 1`;
- `:303` и `:307` — параметр `EventLog.__init__(..., event_schema_version: bool = False)`;
- `:348-349` — штамп `{"schema_version": 1}` первым ключом **только** при включённом флаге;
  при выключенном `stamp` пустой → строка ровно такая, как до миссии;
- `:397-413` — `_read_file` проверяет каждую прочитанную строку (`:408`
  `errors = validate_run_event(entry)`) и пишет warning `invalid run event in %s: %s`
  (`:410-412`), строку не отбрасывает (`:413 entries.append(entry)`);
- `:14` — `import logging` и `:30` `logger = logging.getLogger(__name__)` (новый, нужен
  только для warning).

### Где флаг читается
- `claude-runner/src/claude_runner/api.py:233-249` — `_event_schema_enabled(store)`;
- `:266-268` — `manager.events.event_schema_version = _event_schema_enabled(...)` в `lifespan`
  (до первого события);
- `:332` — версия приложения `1.0.0`;
- `claude-runner/pyproject.toml:7` — `version = "1.0.0"`;
- `claude-runner/src/claude_runner/features_config.py:16` — `"event_schema_version": {"enabled": False}`;
- `claude-runner/src/claude_runner/board_features.js:7` — `event_schema_version: { enabled: false }`
  (UI-раздел «Features» рисует переключатель по этой таблице);
- `claude-runner/docs/features-config.md:39` — строка таблицы `event_schema_version | enabled | off (0) | 0 | —`;
  `:41` — «read by the runner itself (`api.py`, at app start) and has no flattened constant:
  no workflow node reads it» (флаг не попадает в `constants.*` ворффлоу).

### Граница доверия для провайдер-блока
- `claude-runner/src/claude_runner/provider_registry.py:21` — импорт `validate_provider_block`;
- `:232-234` — `errors = validate_provider_block(row)` при чтении строки из базы; ошибка —
  только warning `invalid provider block %s: %s` (`:233`), строка всё равно загружается
  (закреплено тестом `tests/test_provider_registry.py:136 test_invalid_row_is_warned_and_still_loaded`).

### Документы контракта
- `docs/provider-block-schema.md` (новый, 188 строк): `:1` заголовок
  `# Provider block schema (schema_version 1)`; `:29` — «`schema_version` is a document-level
  marker, not a stored field … every block on disk is **version 1**»; `:38` — смена ключа →
  bump `schema_version 2`; `:37` — unknown-ключ попадает в `env` (`config.py:480`) и игнорируется.
- `docs/run-event-format.md` (новый, 334 строки): `:1` `# Run event format (schema_version 1)`;
  `:40-41` — absent = версия 0, 1 = эта документация; `:43-49` — таблица совместимости
  (новый optional-поле → без bump; rename/removed/type/смысл → bump); `:63` — строка envelope
  `schema_version` (писатель `events.py:348`, читатель `contract_schema.py:214-221`);
  `:305` — ссылка на `validate_run_event` (`contract_schema.py:261`); `:330` — правило bump.

### Тесты
- `claude-runner/tests/test_events.py` (новый файл): `:47` off-путь даёт ровно старые байты
  (`assert written == OFF_BYTES`), `:58` on-путь штампует каждое событие
  (`assert all(entry["schema_version"] == 1 …)`), `:77` разница — только штамп
  (`assert on_line.replace('{"schema_version": 1, ', "{") == off_line`), `:94` флаг по умолчанию off,
  `:98` чтения без штампа принимаются, `:28`/`:37` — валидная/невалидная строка.
- `claude-runner/tests/test_contract_schema.py` (новый): `:31-64` провайдер-блок
  (валидный, без версии, неверный тип называет поле, неизвестный ключ игнорируется,
  обязательных полей нет, плохая версия), `:71-124` событие (валидное, без версии,
  отсутствующее обязательное поле называется, unknown-поле игнорируется, unknown-вид).
- `claude-runner/tests/test_event_conformance.py` (новый): `:37`, `:46`, `:55`, `:66`, `:71`.
  Фиксуры: `tests/fixtures/events_claude_code.jsonl` (38 строк, excerpt живого лога
  `run_b9cc0264d28d41348c9e7714818ffa9b.jsonl` с обрезанными текстами, без секретов) и
  `tests/fixtures/events_hermes.jsonl` (7 строк, собраны из писателя `events.py:349` поверх
  `hermes_events.py:104-136`, живого Hermes-лога на хосте нет — `docs/run-event-format.md:309-311`).
  `:66` подтверждает, что в фиксурах нет ни одного Codex-события (Codex вне контракта этой миссии).
- `claude-runner/tests/test_features_config.py:80-99` — флаг известен, по умолчанию off,
  не добавляет flattened-константу (`assert flatten(features) == LEGACY`), миссионный override
  принимается, плохие значения дают `'features_json: event_schema_version.enabled must be true
  or false, got "yes"'` и `'unknown parameter "max_tasks" in feature "event_schema_version"'`.
- `claude-runner/tests/test_board_features_api.py:156-175` — писатель берёт флаг из
  `features_json` доски: `false → False`, `true → True`, `"yes"` → off с логом ошибки.
- `CHANGELOG.md:8-24` — секция `## unreleased (0.14.0)` с описанием флага, двух документов
  и тестов.

Файлы `docs/hermes-engine.md`, `claude-runner/docs/board-ops-notes.md`,
`claude-runner/docs/lessons.md`, `think_profiles.py`, `board_common.py` и т. п. в
uncommitted-диффах принадлежат **другим** миссиям (thinking profiles, mem_guard,
fetch-хелперы) и этой миссией не менялись.

## 2. Версии и где валидатор используется

- Обе схемы — `schema_version 1` (`docs/provider-block-schema.md:1`, `docs/run-event-format.md:1`).
- События без штампа — версия 0, принимаются как есть (`contract_schema.py:214-221`).
- Валидатор применяется в двух местах границы доверия: события из файла
  (`events.py:21`, `:408`) и провайдер-блок из строки базы
  (`provider_registry.py:21`, `:232`). Ни один workflow-ноун валидатор не вызывает.

## 3. Флаг `event_schema_version`: как включить (инструкция оператора, ничего не применено)

В этой задаче **ничего не применялось, не перезапускалось и не отправлялось**. Оператор
делает сам, когда решение принято:

1. Доска → настройки запуска миссии → раздел «Features» → строка `event_schema_version`
   → переключатель «enabled» → save (diff-and-confirm, как у других настроек).
2. Или напрямую в строке `features_json` таблицы `hermes_config` (профиль `claude-code`,
   id 5 — `data-tables/ops/_make_features_config_mission.py:7`):
   ```json
   {"event_schema_version": {"enabled": true}}
   ```
   Сохранение через API требует заголовка `X-AHAWR-Dashboard: 1` и `expected_version`
   (`claude-runner/src/claude_runner/board_api.py:1612-1621`).
3. Значение по умолчанию — `false` (`features_config.py:16`, `board_features.js:7`).
   Пустой/отсутствующий `features_json` = всё выключено.
4. Флаг читается runner'ом **при старте приложения** (`api.py:268` внутри `lifespan`).
   Чтобы он заработал, процесс runner'а должен быть перезапущен после сохранения
   (перезапуск — решение оператора, команд здесь нет).
5. Проверить включение: новый запуск.run → в `events/<run>.jsonl` каждая строка начинается
   с `{"schema_version": 1, "seq": 1, …}`; старые файлы (без штампа) читаются без ошибок.

## 4. Откат

1. **Флаг обратно off**: `"event_schema_version": {"enabled": false}` (или убрать ключ —
   пустой `features_json` тоже off). Строки без штампа снова пишутся; уже записанные
   штампованные события принимаются читателями (версия 1, `contract_schema.py:214-221`).
   Поведение с off-флагом байт-в-байт совпадает с до-миссионным — тест
   `tests/test_events.py:47`.
2. **Полный возврат файлов миссии**: удалить `claude-runner/src/claude_runner/contract_schema.py`,
   `claude-runner/tests/test_contract_schema.py`, `claude-runner/tests/test_event_conformance.py`,
   `claude-runner/tests/fixtures/events_claude_code.jsonl`,
   `claude-runner/tests/fixtures/events_hermes.jsonl`, `docs/provider-block-schema.md`,
   `docs/run-event-format.md`; вернуть в `events.py` (строки 21, 27-28, 303/307, 348-349, 398-412),
   в `api.py` (233-249, 266-268), в `features_config.py:16`, в `board_features.js:7`,
   в `provider_registry.py:21,232-234` исходный вид; убрать `CHANGELOG.md:8-24`;
   в `pyproject.toml:7` вернуть `0.13.100` (а `api.py:332` — `0.13.100`).
   Пересоздания контейнеров для отката не требуется: достаточно выключенного флага.

## 5. Проверки, которые **выполнились** (ключевые строки)

1. `python -m pytest tests/test_contract_schema.py tests/test_event_conformance.py tests/test_events.py
   tests/test_features_config.py tests/test_board_features_api.py tests/test_provider_registry.py -q --no-cov`
   (из `claude-runner/`):
   ```
   ......................................................................   [100%]
   ```
   70 тестов, ни одной `F`, exit 0.
2. `python -m ruff check src/claude_runner/contract_schema.py src/claude_runner/events.py
   src/claude_runner/api.py src/claude_runner/features_config.py src/claude_runner/provider_registry.py
   tests/test_contract_schema.py tests/test_event_conformance.py tests/test_events.py
   tests/test_features_config.py tests/test_board_features_api.py tests/test_provider_registry.py`
   ```
   All checks passed!
   ruff exit=0
   ```
   В baseline (`reports/review-baseline.json:70 "ruff_total": 98`) ни у одного из этих
   файлов ошибок нет (`ruff_by_file` их не содержит) → `ruff_total 98` не вырос.
3. `python -m mypy src/claude_runner/contract_schema.py src/claude_runner/events.py
   src/claude_runner/api.py src/claude_runner/features_config.py src/claude_runner/provider_registry.py`
   ```
   Found 76 errors in 10 files (checked 5 source files)
   ```
   Разбивка по файлам (`awk … | uniq -c`):
   ```
   29 src/claude_runner/board_pg.py     20 src/claude_runner/board_order.py
   12 src/claude_runner/board_api.py     7 src/claude_runner/board_ready.py
    5 src/claude_runner/board.py         2 src/claude_runner/board_stats.py
    1 src/claude_runner/runs.py          1 src/claude_runner/board_think.py
    1 src/claude_runner/board_ops_redact.py
    1 src/claude_runner/api.py
   ```
   Из трёх изменённых миссией модулей ошибок нет: `contract_schema.py`, `events.py`,
   `features_config.py`, `provider_registry.py` — 0. `api.py` — 1 (`src/claude_runner/api.py:278:
   error: Unused "type: ignore" comment`); в baseline для `api.py` 2 (`review-baseline.json:72`),
   то есть не выше; сама строка `# type: ignore[arg-type]` в дифф этой миссии не входит
   (`git diff claude-runner/src/claude_runner/api.py | grep -c "type: ignore"` → `0`).
4. `git diff --check` (из `/d/n8n`): exit 0, `grep -c "trailing"` → `0`;
   предупреждения только про `LF will be replaced by CRLF` (существовавшие до миссии).
5. `node --test tests/board_features.test.js` → `# pass 11`, `# fail 0`;
   `node --test tests/board_features_ui.test.js` → `# pass 9`, `# fail 0`.
   Эти JS-тесты нового ключа `event_schema_version` не касаются (в них нет строки
   `event_schema_version`) — они подтверждают, что таблица `FEATURET_DEFAULTS`
   не сломала парсинг/валидацию `features_json`.

Всего процитировано ключевых строк: 24 (≤ 30).

## 6. Проверки, которые **не** запускались, и почему

- **Полный pytest** (`--collect-only` даёт 2991 тест) — задача T011 требует узких проверок;
  кроме того в baseline зафиксирован collect-error `tests/test_qa_container_static.py:
  ModuleNotFoundError: No module named 'yaml'` (`review-baseline.json:52`), который я не
  должен чинить.
- **mypy «до/после» на копии HEAD** — нельзя делать `git checkout`/`reset` (запрещено);
  сравнение сделано только с `reports/review-baseline.json`, как описано выше.
- **mypy поодиношке на изменённых тестовых файлах** — первый запуск разрешал `claude_runner`
  из установленной копии `/opt/claude-runner/venv/lib/python3.12/site-packages/claude_runner/`
  (в ней нет `contract_schema.py`: `grep -c "event_schema_version" …/features_config.py` → `0`).
  Это артефакт окружения, а не код; объединённый запуск выше (резолв из `src/`) его перекрывает.
- **`python -m claude_runner.qa`** (QA-сценарии доски) — нужен живой контейнер и провайдеры;
  у Worker нет docker, и T011 этого не требует.
- **Live-проверка включённого флага в базе/на проде** — не делалась: применение флага
  (сохранение `features_json`) выходит за границы задачи («nothing was applied»).
- **`node --test tests/*.test.js` целиком** — запускались только два файла, относящиеся к
  `features`; остальные не затронуты изменением.

## 7. Открытые риски

1. **Флаг читается только при старте приложения** (`api.py:268` в `lifespan`): изменение
   `features_json` без перезапуска runner'а на уже запущенный процесс не влияет.
2. **`shortcut`-оговорка в коде**: читается только глобальный `features_json`,
   миссионный `mission_features_json` писателю событий не передаётся
   (`api.py:247-248` — «shortcut: … the per-mission override reaches the writer once runs
   carry the mission's features»). Апгрейд — когда runs начнут нести features миссии.
3. **`_read_file` предупреждает, но не отбрасывает** невалидные строки
   (`events.py:408-412`): статистика и UI считают их обычными событиями. Это текущее
   поведение, закрепленное тестом `tests/test_events.py:37`.
4. **Провлайдер-блок не rejecting**: `validate_provider_block` даёт только warning
   (`provider_registry.py:232-234`); плохой ключ всё равно уходит в `env` и используется
   CLI. Оператор должен смотреть лог `invalid provider block …`.
5. **Документы — ручная копия таблиц кода**: правило bump (`docs/run-event-format.md:330`)
   не machine-verifiable; при расхождении таблиц и документов тесты не падают.
6. **Codex-исполнитель вне контракта**: `ENGINE_VALUES` содержит `codex`
   (`contract_schema.py:19`), но видов событий Codex в `EVENT_KIND_FIELDS` нет;
   conformance-тест лишь подтверждает, что Codex-событий в фиксурах нет
   (`tests/test_event_conformance.py:66`). Для Codex — отдельная миссия
   (`docs/codex-executor.md`).
7. **Старая установленная копия пакета** в `/opt/claude-runner/venv/.../site-packages/claude_runner`
   без `contract_schema.py`: если runner исполняется из неё, флаг и валидатор не будут
   работать. Перед включением флага оператор должен переустановить/пересобрать пакет
   (проверить из Worker нельзя — нет docker).
8. **Релиз не собран**: запись `CHANGELOG.md:8` помечена как `unreleased (0.14.0)`,
   при этом `pyproject.toml:7` и `api.py:332` уже показывают `1.0.0` — несоответствие
   версий до релиза остаётся.
9. **Hermes-фиксура сконструирована, а не взята из живого лога** (живого Hermes-лога на хосте
   нет, `docs/run-event-format.md:309-311`): соответствие Hermes-движка контракту
   доказано только писателем `events.py:349`, а не реальными данными.
10. mypy-ошибка `api.py:278` (`Unused "type: ignore"`) осталась как есть — она не из этого
    диффа, и по правилу задачи чужие ошибки не чинятся.
