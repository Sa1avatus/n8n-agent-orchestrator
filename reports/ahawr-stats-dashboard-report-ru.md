# Отчёт миссии ahawr-stats-dashboard (T001–T014) — popup «Статистика» и бенч-метрики

Рабочее дерево: main, HEAD 9bf0266e; все изменения миссии незакомментированы в git (коммит — оператор). Все цифры проверок приведены дословно из reports/ahawr-stats-dashboard-checks.txt (T014A, attempt 2; рабочее дерево после правок в attempt 2 — только аннотации mypy в четырёх тестовых файлах). Новые проверки не запускались.

## 1. Что сделано по задачам T001–T013 (file:line)

### T001 — исследование (без изменений файлов)
Фиксированные базовые значения подтверждены проверками T014A: ruff board_api.py 28 до/после (CHECK 3), mypy board_api.py 16/16, board_stats.py 2/2, tests/test_board_stats.py 22/22, tests/test_mission_stats.py 3/3 (CHECK 4); git status 67 записей (CHECK 6). Файлы оператора data-tables/ops/engine_log_snapshot_20261108.log и data-tables/ops/bench_kv_series_segments.txt都存在 — они названы предусловием миссии и присутствуют в data-tables/ops (проверка наличия — критерий T001).

### T002 — флаги stats_popup и stats_engine_sampler
- ahawr-runner/src/ahawr_runner/features_config.py:18-19 — `"stats_popup": {"enabled": False}`, `"stats_engine_sampler": {"enabled": False}` (по умолчанию выключены).
- ahawr-runner/src/ahawr_runner/board_features.js:8-9 — те же дефолты в UI.
- ahawr-runner/tests/features_cases.json:94-104 — новые кейсы (оба флага включены; кейс `non_boolean_enabled_stats_popup` с ошибкой `"features_json: stats_popup.enabled must be true or false, got \"yes\""`).
- ahawr-runner/tests/test_board_stats_detail.py:161 — `test_flags_default_off`.

### T003 — чистый модуль метрик (по задаче и миссии)
- ahawr-runner/src/ahawr_runner/stats_figures.py: `sonnet_rates_usd` (:36), `run_figures` (:78), `mission_figures` (:190), вспомогательные `none_with`/`ratio`/`average` (:93-102), `_task_rows` (:176).
- ahawr-runner/tests/test_stats_figures.py:111 `test_mission_figures_match_stats_for_runs` (числа миссии совпадают с mission_stats), :129 `...full_table`, :148 `...per_task_rows`, :159 `test_task_figures_with_per_attempt_rows`, :181 `test_uncomputable_ratios_are_none_with_reasons`, :203 `test_missing_review_score_is_no_score_not_zero`.
- Крючок board_stats.py: hunk-блоки в board_stats.py (строки 57-73, 262-269, 345-355, 386-409, 429-433, 460).

### T004 — проектная область (группировка миссий в проект)
- ahawr-runner/src/ahawr_runner/stats_figures.py:200 `project_figures` (агрегат по миссиям проекта, разбивка по миссиям), :257 `task_figures`.
- ahawr-runner/tests/test_stats_figures.py:300 `test_project_figures_of_two_missions`, :324 `test_project_figures_group_by_the_stored_rule` (группировка по правилу хранилища, без нового правила).
- Маршрут GET `/api/board/projects/{project}/stats`: board_api.py:1707-1725 (гейт флагом `stats_popup`: строки 1717-1718; движок — только при `stats_engine_sampler`: 1725).

### T005 — парсер журнала движка
- ahawr-runner/src/ahawr_runner/engine_log.py:20-26 — regexes, дословно взятые из `engine_segment` bench_series_table.py (`GENERATION_RE`, `DRAFTS_RE`, `EXPERT_CACHE_RE`, `VRAM_FREE_RE`); `_tok_s_or_none` (:67), `parse_line` (:75); несовпадающая строка игнорируется, не ошибка.
- ahawr-runner/tests/test_engine_log.py:62 `test_parse_line_table(line: str, expected: list[Sample]) -> None` (табличные тесты на реальных формах строк), :67 `test_parse_line_ne_raises_on_garbage`.

### T006 — DDL board.engine_samples, хранилище сэмплов и окно движка
- ahawr-runner/docs/board-schema.sql:167-185 — `CREATE TABLE IF NOT EXISTS board.engine_samples` (columns `sampled_at`, `engine_run`, `generated_tokens`, `tok_per_s`, `drafts_accepted`, `drafts_total`, `expert_slots`, `vram_free_mib`, `low_vram`, `source`, `sample_key`), уникальный индекс `engine_samples_sample_key_idx` и индекс `engine_samples_sampled_at_idx`; nothing применено — только файл.
- ahawr-runner/src/ahawr_runner/engine_samples.py:30 `MIN_GENERATED_TOKENS = 50`, :31 `NO_SAMPLES_REASON = "no samples"`, :49 `insert_sample` (идемпотентен по `sample_key`), :83 `engine_window`, :95 `engine_window_figures` — пустое окно даёт `None` с причиной `no samples`, никогда не нули.
- Крючок в API: board_api.py:131-141 (engine-блок = `{"engine": None, "engine_reason": "no samples"}` при пустом окне).
- Тесты: tests/test_engine_samples.py:79,96,101,123,142,158,185 (7 тестов, включая `test_empty_window_is_none_with_no_samples_reason` и `test_schema_ddl_for_engine_samples_is_only_if_not_exists`).

### T007 — инкрементальный коллектор с ограниченным циклом
- ahawr-runner/src/ahawr_runner/engine_collector.py:80-83 (`poll_seconds`, `max_batch_lines`, `max_db_retries`, `backoff_seconds` — именованные константы-дефолты), :86-109 `from_env` (путь журнала из `DASHBOARD_ENGINE_LOG_PATH`, а не литерал в цикле), :148-165 (`EngineCollector`, байтовый offset в собственном состоянии), :169-187 `run()` — `while not self.stop_event.is_set()` (:172) с выходом и `sleep(poll_seconds)` (:187), :194 `_read_lines` (детект усечения/перезаписки, маркер `engine started:` инкрементирует `engine_run`), :241-263 `_write_batch` — retry с `backoff_seconds`, `sample_key` защищает от дублей.
- Тесты на временных файлах: tests/test_engine_collector.py:125 (`append_partial_line_and_restart_marker`), :150 (truncation restarts from zero), :173, :184 (max batch caps a poll), :200, :216 (retry без двойного учёта engine_run), :233 (retries exhausted exits the loop), :248 (stop event ends the loop), :271, :295, :313.

### T008 — read-only detail API (GET-маршруты) и byte-for-byte проверка
- ahawr-runner/src/ahawr_runner/board_api.py:1660 `GET /missions/{mission_id}/stats/detail`, :1681 `GET /missions/{mission_id}/tasks/{task_key}/stats/detail`, :1707 `GET /projects/{project}/stats` — только GET; гейт флагом: :1669-1670, :1694-1695, :1717-1718; движок-блок только при `stats_engine_sampler`: :1673, :1698, :1725. Валидация пути: `_TASK_ID_RE` (:602) применяется :694 и :1689.
- Тесты: tests/test_board_stats_detail.py:170,177 `..._bytes_with_flags_off` (сравнение байтов существующих маршрутов), :209,218,227 — отказ при выключенном флаге, :236 отказ при недоступном хранилище, :264 неизвестная миссия/задача → 404, :281 `test_invalid_path_values_are_rejected`, :293,317,334,352 — цифры detail-маршрутов.
- Кэш и повторные чтения фигур (добавлены этой миссией): tests/test_board_stats.py:555-557, 570-572, 594-596 — три теста (`test_failed_figures_read_keeps_the_last_good_figures`, `test_failed_figures_read_is_retried_soon_not_after_the_ttl`, `test_expired_task_list_is_answered_from_the_cache_and_recomputed_in_background`); hunk в diff `@@ -544,3 +545,86 @@` (CHECK 4).

### T009 — подключение коллектора к запуску и документ для оператора
- ahawr-runner/src/ahawr_runner/board_api.py:518 `_run_engine_collector`, :527-539 `start_engine_collector` (запускается только при `stats_engine_sampler`: :539).
- ahawr-runner/src/ahawr_runner/dashboard.py:217-219 — guard-style watcher: старт только при флаге; :223-224 — `engine_stop.set()` при shutdown (ограниченный цикл завершается).
- ahawr-runner/docs/stats-collector.md:49,55 — патч `docs/stats-collector-compose.patch` (read-only mount `docker-live_strata-data:/strata-data`, `patch -p1 --dry-run`... затем `patch -p1`), rollback :38 и :61; env-таблица :68. Сам патч — файл docs/stats-collector-compose.patch (diff против live docker-compose.yml; ничего не применено).
- Тесты подключения: tests/test_engine_collector.py:357,371,396,408 (флаг off — нет потока; off — нет базы — нет; переживает плохие флаги); tests/test_engine_collector_wiring.py:75,89,103,131 (off-флаг при реальном старте не создаёт поток; unreachable database не роняет dashboard; failing writes выходят из коллектора, dashboard жив; shutdown ставит stop event).

### T010 — popup-модуль, стили и i18n
- ahawr-runner/src/ahawr_runner/board_stats_popup.js (новый): :10 `BoardStatsPopup.attach(host, scope)`; :28-36 — переиспользование общего `esc()` (board_logic.js) и `fetchJson` (board_actions.js), второй копии нет; :67,189-192,203-213 — тексты через `L(key, "English")`; :161-… GET-URL; :308-314 — `clearTimeout(loadTimer)` при закрытии (таймер снимается); :364 — Esc закрывает; :413-431 — возврат фокуса на кнопку; :460-484 — экспорт модуля.
- ahawr-runner/src/ahawr_runner/board.css:514-528 — стили `.sp-btn`, `.sp-win`, `.sp-tabs`, `.sp-tab`, `.sp-table` (15 строк).
- ahawr-runner/src/ahawr_runner/board.html:51 — `<script src="/static/board_stats_popup.js"></script>` (фиксированный /static-паттерн).
- Ключи `sp.*` добавлены в ahawr-runner/src/ahawr_runner/board_i18n_en.json (hunk `@@ -95 +95,58`) и board_i18n_ru.json (`@@ -95 +95,58`) и в BUNDLED_EN ahawr-runner/src/ahawr_runner/board_i18n.js:699-755 (57 строк).

### T011 — кнопки и GET-only тесты
- ahawr-runner/src/ahawr_runner/board_mission.js:338-340 — кнопка на карточке миссии (`BoardStatsPopup.attach(box, { kind: "mission", ... })`).
- ahawr-runner/src/ahawr_runner/board_task.js:104, 113, 198-203 — кнопка на строке задачи (`kind: "task"`), со `shortcut: ...` комментарием для empty/error-состояний.
- ahawr-runner/src/ahawr_runner/board_view_projects.js:164-174 — кнопки на карточках проектов (`kind: "project"`).
- ahawr-runner/tests/board_stats_popup.test.js — 17 node-тестов; ключевые: `test("clicking the attached button opens the popup of its scope")` (:287), `test("the popup issues no POST, PUT or DELETE")` (:305), `test("every translated key ... English, Russian and bundled tables")` (:325), `test("the popup reuses the shared esc and fetchJson ...")` (:342).
- ahawr-runner/tests/test_board_stats_detail.py:146 `test_stats_popup_bundle_is_served_through_the_static_route`.

### T012 — одноразовый идемпотентный backfill
- ahawr-runner/src/ahawr_runner/engine_backfill.py:45-46 `SOURCE_SEGMENT = "backfill-segment"`, `SOURCE_MATCHED = "backfill-matched"`; :18-19 — «once is ambiguous and writes nothing, so only unique matches become rows»; :93,101 — ключи сэмплов; :108 `load_segment_map`, :122 `read_samples`, :174 `load_runs`, :207 `setting_of`, :220 `pair_index`, :229 `false_match_risk` (счёт неопределённых пар), :235 `build_rows` (:243 «unique pair ... write nothing»), :288 `coverage_table`, :313 `setting_coverage`, :331-332 `apply_rows` (через `engine_samples.insert_sample`, идемпотентно), :366 `--dry-run`, :371 `main`.
- Тесты: tests/test_engine_backfill.py:140,156,162 (`test_only_unique_pair_matches_write_rows`), :172,186 (`test_ambiguous_pairs_are_counted_as_false_match_risk`), :192,203,210 (ключи стабильны при повторном чтении), :218 (`test_rerunning_the_backfill_adds_no_rows`), :228 (`test_coverage_table_reports_each_mission_with_its_source`), :248 (`test_dry_run_writes_nothing_and_reports_the_counts`), :270 (`test_missing_input_stops_with_the_checked_path`).
- Команда для оператора — в ahawr-runner/docs/stats-popup.md:122-123:
  `python -m ahawr_runner.engine_backfill --dry-run --log <snapshot> --segments <map> [--runs <export>]` и
  `python -m ahawr_runner.engine_backfill --apply --dsn <postgres dsn> --log <snapshot> --segments <map> --runs <export>`.
  apply_rows — «the unique sample_key makes a retried insert a no-op» (engine_backfill.py:332-333).

### T013 — документация
- ahawr-runner/docs/stats-popup.md:10-22 — флаги и rollback (флаги выключаются в `features_json` — строка 19; rollback движка — `DROP TABLE` строки 22); :70-74 — лимит хранения runs (шаговые цифры читаются из activity-логов, которые чистятся после `CLAUDE_RUNNER_EVENT_RETENTION_DAYS`, дефолт 14); :105 — у журнала движка нет тайстмэдов, единственный источник времени — часы коллектора; :108-110 — у прошлых миссий нет engine-блока, пока не запущен backfill; источник помечается `sampled`/`backfill-segment`/`backfill-matched`.
- ahawr-runner/docs/stats-collector.md — инструкции оператора (mount, DDL, env, rollback).

## 2. Проверки, которые реально запускались (цифры из checks.txt)

- CHECK 1 — целевой pytest 9 файлов миссии (test_stats_figures, test_engine_log, test_engine_samples, test_engine_collector, test_engine_collector_wiring, test_engine_backfill, test_board_stats_detail, test_board_stats, test_mission_stats), `-q --no-cov`: `exit code: 0` («EXIT_CODE=0»); 110 тестов (72 точки ` [ 65%]` + 38 ` [100%]`), маркеров `F` нет; `grep -c "FAILED" /tmp/final_pytest.txt = 0`. Предупреждение из установленного венка: «StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated» — не код проекта. В teardown (не провал теста): «summary warm-up failed, retrying in 60.0 s» … «RuntimeError: cannot schedule new futures after interpreter shutdown».
- CHECK 2 — node --test tests/board_stats_popup.test.js: «1..17», «# tests 17», «# pass 17», «# fail 0», «# skipped 0», «# duration_ms 555.968483».
- CHECK 3 — ruff before/after по 18 файлам: новые файлы (stats_figures, engine_log, engine_samples, engine_collector, engine_backfill и 7 тестовых) — 0 ошибок; board_api.py 28/28 (unchanged), test_mission_stats.py 4/4 (unchanged); сводная команда: exit 1, «Found 32 errors.» — «32 before, 32 after: no new ruff errors» (28 x E501 в board_api.py, 4 x F401 в test_mission_stats.py, все уже в HEAD; кросс-чек с QA-базойline 85 qa/baselines/ahawr-runner.json: «src/ahawr_runner/board_api.py:E501»).
- CHECK 4 — mypy (strict) 18 файлов после аннотаций attempt 2: exit 1, «Found 112 errors in 14 files (checked 18 source files)»; attempt 1 был «Found 143 errors in 17 files»; удалены ровно 31 error (6+22+2+1). tests/test_board_stats.py 22/22 (unchanged, все ≤ строки 547, совпадают с baseline); все 5 новых модулей источника и 7 новых тестовых файлов — 0 mypy-ошибок. Единственная разница с HEAD-базисом (111): «src/ahawr_runner/api.py:284: error: Unused "type: ignore" comment [unused-ignore]» — api.py миссией не меняется, попал в проверку из-за импорта в tests/test_board_stats_detail.py:23.
- CHECK 5 — `git diff --check`: exit 0, находок нет, 22 предупреждения LF→CRLF (Windows-хост); повтор после правок attempt 2 — тот же результат.
- CHECK 6 — `git status --short`: 67 записей; docker-compose.yml и data-tables/ops не тронуты (`git status --short -- docker-compose.yml data-tables/ops` → пусто).
- CHECK 7 — повтор после аннотаций: pytest exit 0, 110 точек, `FAILED|ERROR` = 0; mypy «112 errors in 14 files»; ruff на четырёх отредактированных тестовых файлах: exit 0, «All checks passed!»; node не перезапускался (JS/i18n не трогались) — результат CHECK 2 в силе; git diff --check exit 0.

## 3. Проверки, которые не запускались (и почему)

- Полный pytest без --no-cov: не запускался — T014A называет только 9 файлов, CLAUDE.md требует --no-cov для targeted-прогонов.
- node-тесты board_mission/board_task/board_view_projects: не запускались — T014A их не называет (файлы менялись другими задачами).
- Коллектор и backfill не запускались против реальной базы данных и реального журнала движка: контейнерные inputs Worker недоступны (предусловие миссии); тесты работают только на временных файлах.
- Ни один контейнер, база или n8n-таблица не тронуты, ничего не применено.
- mypy/ruff не запускались на board_stats_popup.js и i18n JSON (Python-only инструменты; JS проверяется node-тестом CHECK 2).
- `mypy src` отдельно (команда QA-гейта) не запускалась: 18-файльная команда CHECK 4 — супсет для файлов миссии.

## 4. Покрытие backfill по миссиям и риск ложных совпадений (T012)

Числовое покрытие миссий в checks.txt отсутствует, и это не пропущенная проверка, а отсутствие запуска:
- В checks.txt зафиксировано: «The collector was not executed against a real database or a real engine log ... only the pytest files that exercise it on temporary files were run» и «No container, database or n8n data table was touched; nothing was applied» (CHECKS NOT RUN).
- Таблица покрытия генерируется скриптом только при наличии `--runs` — экспорта хранимых Worker-рудов: engine_backfill.py:406 («no --runs export: the per-mission coverage needs the stored Worker runs»); stage-файлы оператора (snapshot-лог и segment map) предусловием подготовлены, но dry-run по ним T014A не фиксировал.
- Риск ложных совпадений измерен на фиксированных тестах: `false_match_risk()` (engine_backfill.py:229) считает неопределённые пары; тесты test_engine_backfill.py:162 (только уникальные пары пишут строки) и :186 (ambiguous pairs counted as false match risk) показывают, что ambiguous-пара не пишется, а учитывается в риске. Цифр по реальному snapshot в отчёте нет — оператор получит их, запустив `--dry-run` (stats-popup.md:122) после создания таблицы и экспорта runs.
- Run-based метрики старых миссий не требуют backfill: считаются из stored runs (stats-popup.md:70-74 — лимит: activity-логи чистятся после `CLAUDE_RUNNER_EVENT_RETENTION_DAYS`, дефолт 14).

## 5. Шаги оператора (всё подготовлено файлами, ничего не применено)

1. Применить патч compose: `ahawr-runner/docs/stats-collector-compose.patch` — read-only mount `docker-live_strata-data:/strata-data` в сервисе ahawr-dashboard; проверка перед применением: `patch -p1 --dry-run < ahawr-runner/docs/stats-collector-compose.patch` (stats-collector.md:49,55). Rollback: строки 38 и 61.
2. Создать таблицу: DDL `CREATE TABLE IF NOT EXISTS board.engine_samples` в `ahawr-runner/docs/board-schema.sql:167-185` (идемпотентно, индексы sample_key и sampled_at): `docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 < ahawr-runner/docs/board-schema.sql` (stats-collector.md:31-35).
3. Включить два флага: `stats_popup` и `stats_engine_sampler` (по умолчанию false, features_config.py:18-19) в настройках доски (`features_json`); rollback — выключить флаги (stats-popup.md:19).
4. Запустить backfill: `python -m ahawr_runner.engine_backfill --dry-run --log <snapshot> --segments <map> [--runs <export>]` затем `--apply --dsn <dsn> ...` (stats-popup.md:122-123); скрипт идемпотентен (unique `sample_key`), строки помечаются `backfill-segment`/`backfill-matched`.

## 6. Открытые риски

- Коллектор не проверялся на реальной базе/журнале: первый реальный запуск после применения патча и таблицы может выявить проблемы mount/offset (checks.txt, CHECKS NOT RUN).
- У журнала движка нет тайстмэдов: время сэмплов — часы коллектора; метрики движка есть только за периоды, когда коллектор работал; у прошлых миссий engine-блок `null` с причиной `no samples` (stats-popup.md:105-108) до backfill.
- Ошибки mypy/ruff, предшествующие миссии, остаются: 22 в tests/test_board_stats.py (≤ строки 547), 16 в src/ahawr_runner/board_api.py, 2 в board_stats.py, 3 в tests/test_mission_stats.py и транзитивные в board_pg/board_order/board_ready/board_ops_store_pg/board/runs/board_think/board_providers_api/board_ops_redact/api.py (checks.txt, REMAINING PROBLEMS 1-2).
- Один `# type: ignore[arg-type]` в tests/test_board_stats.py:634 добавлен миссией и strict mypy в attempt 2 его не требует (checks.txt, REMAINING PROBLEMS 3).
- В git status 67 записей, включая qa/ и .claude/ от других миссий: критерий «только разрешённые пути» рабочее дерево целиком не выполняет; T014A меняла только checks.txt и четыре тестовых файла в ahawr-runner/tests (CHECK 6).
- В teardown тестов естьstderr-предупреждение «summary warm-up failed ... RuntimeError: cannot schedule new futures after interpreter shutdown» — это не провал теста (CHECK 1), но сигнал, что warm-поток не завершается идеально при shutdown.
- Числов покрытия backfill по конкретным миссиям нет до запуска dry-run оператором (см. раздел 4).
