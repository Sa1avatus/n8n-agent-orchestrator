# Отчёт о миссии ahawr-features-config: features_json

Дата: 2026-10-22. Рабочая папка: `/d/n8n` (MSYS: `/d/n8n`).

Итог: `features_json` реализован как файлы и тесты. Патчи написаны, порядок проверен прогоном на копии
живого экспорта (тот, что есть в `_scratch`, не свежий, см. раздел 9). Живая проверка не выполнена: она
требует очереди и платных моделей, поэтому её делает оператор. Ничего не импортировано в n8n, `launch_mission.sh`
не изменён, контейнеры не трогались.

## 1. Что сделано

Части плана взяты из `data-tables/ops/_make_features_config_mission.py` (PART 1-6 и ACCEPT 1-7). Номеров T001-T024 в
файле миссии нет; в логах встречаются только T001, T014, T015, T020 и T023.

| Часть плана | Результат | Где |
|---|---|---|
| PART 1. Документ | `claude-runner/docs/features-config.md`: форма, таблица умолчаний, валидация, переопределение по миссии, flatten, порядок патчей, активация, откат, живая проверка, тексты Telegram | разделы: 10-94 форма и умолчания, 96-140 порядок, 142-154 активация, 156-163 откат, 165-175 не меняется, 177-230 живая проверка, 232-260 тексты Telegram |
| PART 2. Ядро и его двойники | Чистое ядро в JS и Python с общим набором кейсов | `claude-runner/src/claude_runner/board_features.js`: `FEATURE_DEFAULTS` :3, `PARAM_MINIMUMS` :8, `parseFeatures` :88, `flatten` :97, экспорт :113-120. `claude-runner/src/claude_runner/features_config.py`: `parse_features` :117, `validate_features_json` :130, `flatten` :137. Общий кейс-файл `claude-runner/tests/features_cases.json` (ключи `description`, `legacy_constants`, `parse`) |
| PART 2. Тесты ядра | Питон: эталонный случай пустого значения, нестандартный JSON | `claude-runner/tests/test_features_config.py`: `test_parse_case` :18, `test_golden_empty_or_missing_flattens_byte_for_byte_to_legacy` :36, `test_non_standard_json_constants_are_rejected` :45. Node: `claude-runner/tests/board_features.test.js:33` (golden) |
| PART 3. Патч Constants | `features_config_patch.py`: ядро между маркерами, якоря ровно один раз, идемпотентен | `data-tables/ops/features_config_patch.py`: маркеры `CORE_START`/`CORE_END` :22-23, `once()` :60-63, `core_block()` :67, `patch()` :76 |
| PART 3. Переписанный adaptive_split | Якорные правки пяти узлов вместо подмены всего `jsCode`. Полная подмена остаётся только у двух узлов, которые патч сам добавляет: `Apply Split Answer` и `Create Part Missions` | `data-tables/ops/adaptive_split_patch.py`: докстринг :1-14, `ANCHORED_NODES` :58, подмена для `ADDED` :806-819, якорные правки :824-842. Старые копии узлов `adaptive_split/nodes/` удалены (папки нет) |
| PART 3. Патч Telegram | `telegram_progress_patch.py`: позиция и номера в TASK STARTED, REVIEW, PLAN RECEIVED, FAILED, APPROVED | `data-tables/ops/telegram_progress_patch.py` докстринг :1-27; тесты `data-tables/ops/test_telegram_progress_patch.py`: `test_last_position_of_twenty_task_plan` :115, `test_part_two_of_three_with_overall_numbers` :121, `test_plain_mission_differs_only_in_position_wording` :128, `test_launch_order_adaptive_split_then_telegram_progress` :374 |
| PART 3. Запуск | Копия launch-скрипта с новыми строками; `launch_mission.sh` не тронут | `data-tables/ops/launch_mission.sh.new`: строки 40, 41, 43, 44, 45 (патчи), 47 (`ensure_live_state_row.py`). `launch_mission.sh`: дата изменения `Oct 21 22:39`, то есть до миссии |
| PART 3. Тест порядка | Применяет все патчи из launch-скрипта дважды к копии экспорта | `data-tables/ops/test_all_patches_order.py`: `MARKERS` :37-49, `test_patch_order_is_stable_and_marked` :141-158, `test_wholesale_overwrite_fails_marker_check` :161 |
| PART 4. Доска: API | Чтение и запись `features_json` (глобально) и `mission_features_json` (по миссии), с заголовком dashboard и проверкой версии | `claude-runner/src/claude_runner/board_api.py`: `GET /features` :1495, `PUT /features` :1501, `GET /missions/{id}/features` :1513, `PUT /missions/{id}/features` :1524. Тесты: `claude-runner/tests/test_board_features_api.py` :49-149 |
| PART 4. Доска: окно настроек | Раздел Features: строки с переключателем, параметры, поле JSON, сверка перед сохранением | `claude-runner/src/claude_runner/board_edit_win.js`: `FEAT_LABEL_KEYS` :52, строки `featFields` :321-331, `loadFeatures` :341. Тесты: `claude-runner/tests/board_features_ui.test.js`, `claude-runner/tests/board_edit_win_features.test.js`, `claude-runner/tests/board_features.test.js` |
| PART 4. QA-сценарии | Сценарий Features rows и сценарий сохранения с ошибкой | `claude-runner/qa/scenarios/board_features_rows.yaml`, `claude-runner/qa/scenarios/save_validation_error_then_persist.yaml` (новые, не в git) |
| PART 5. Живая проверка | Генератор пробной миссии и ожидаемые результаты; сама проверка не запускалась | `data-tables/ops/_make_split_trial_mission.py`; `data-tables/ops/_scratch/split_trial/` (`mission_ahawr-split-trial.csv`, `expected_outcomes.json`, `pkg/`); `docs/features-config.md` :177-230 |
| PART 6. Номера в Telegram | Позиция и общее число текущего плана; для частей плана - номер части и сквозной прогресс | `telegram_progress_patch.py` и тесты выше. Поля части пишет `Create Part Missions` в `adaptive_split_patch.py` (в логах не проверено отдельно) |

Коротко по ядру: пустой `features_json` или отсутствующий `features_json` даёт все выключенные функции и умолчания.
`max_tasks` берётся из столбца `max_tasks`, если в `features_json` нет параметра. Переопределение по миссии применяется
по функциям; неизвестные ключи, нецелые и малые числа и `enabled` не логическое значение отклоняются с текстом
причины.

## 2. Порядок патчей и почему ни один не отменяет другой

Текущий порядок в `data-tables/ops/launch_mission.sh.new` (файл от 2026-10-22 07:26):

```
40  features_config_patch.py     (features_json -> constants)
41  adaptive_split_patch.py      (пишет в adaptive_split/scratch/)
42  mv ... wf_next.json          (возвращает результат на место)
43  retry_patch.py
44  telegram_progress_patch.py
45  qa_workflow_patch.py         (режим QA из roles_json)
47  ensure_live_state_row.py     (не патч для wf_next.json)
```

Строки 33-39 (notice_split, telegram_model, telegram_mission, telegram_gate, blind_guard, think_config,
roles_config) стоят перед ними. В `launch_mission.sh.new` retry_patch стоит на строке 43, после adaptive_split_patch,
как в `docs/features-config.md` :96-116.

Обоснование (`docs/features-config.md` :118-140, коротко):
- Патч 1 переписывает весь текст TASK STARTED; патч 2 добавляет строку модели в тексты, идущие за ним.
- Патч 3 привязывается к строке `📌` в тексте, который создают 1 и 2.
- Патч 4 (`telegram_gate`) меняет только условия `If` (`item` -> `first()`); патч 5 (`blind_guard`) работает с узлом `Retry Current Task`. Текстов Telegram они не трогают.
- Патч `features_config` добавляет константы в тот же узел `Constants`, что и `think`/`roles`; идёт после них и
  сохраняет их якоря.
- `adaptive_split` использует якоря, которые должны совпадать с выводом 1-8. Его `--out`/`mv` (строки 41-42)
  пишет в scratch и возвращает файл; остальные узлы остаются прежними.
- `retry` идёт после `adaptive_split`, потому что покрывает Telegram-узлы, которые тот создаёт (комментарий строки 43).
- `telegram_progress` идёт после `adaptive_split`: использует поля части, которые пишет `Create Part Missions`; без
  узла падает, если в workflow включён адаптивный раздел.
- `qa_workflow` (строка 45) добавляет узлы QA; его якоря не пересекаются с предыдущими.

Доказательство по логам (`data-tables/ops/_scratch/all_patches.log`):
- Первый проход: `notice_split_patch: rc=0 notice split: updated`; `features_config_patch: rc=0 live_export.json: patched Constants`;
  `retry_patch: rc=0 retry added: ['Cancel Previous Run', 'Read Queue Position', 'Create Part Mission']`.
- Маркеры, по одному разу в узле: `notice_split_patch: 'AHAWR_NOTICE_SPLIT' in 1 node(s), once each`;
  `think_config_patch: "think_policy: String($('Constants')" in 1 node(s), once each`;
  `adaptive_split_patch: 'max_tasks_hard: 30 },' in 1 node(s), once each`.
- Второй проход: `second pass: features_config_patch: rc=0 live_export.json: already patched`;
  `second pass: retry_patch: rc=0 retry added: []`;
  `second pass: telegram_progress_patch: rc=0 task position and numbers: ['Split Task Notice']`.
- Итог: `2 passed in 16.86s`.

Что доказывает тест: хэш результата после первого прохода равен хэшу после второго (`assert _sha256(workflow) == once_sha256`),
то есть второй проход ничего не меняет. Маркеры каждого патча найдены в нужном числе узлов. Каждый `Code`-узел
проходит `node --check`.

Ограничения доказательства (важно):
- В `MARKERS` (`test_all_patches_order.py` :37-49) нет маркеров `features_config_patch`, `telegram_progress_patch` и
  `qa_workflow_patch`. Тест проверяет их только по стабильности и синтаксису. Фраза «каждый маркер на месте» в
  `docs/features-config.md` :138-140 утверждает больше, чем проверяет тест.
- Тест брал `data-tables/ops/_scratch/live_export.json`, который старше миссии (см. раздел 9), а не свежий экспорт.
- Строка `notice_split_patch: rc=0 notice split: updated` выводится и при втором проходе. Это метка, а не
  доказательство: доказывает хэш.
- `launch_mission.diff` в `_scratch` сделан с `launch_mission.sh.new` от 06:32. Порядок в нём другой (telegram_progress
  стоит до adaptive_split, retry нет). Текущий файл от 07:26 имеет порядок, описанный выше; тест читает именно его.

## 3. Тесты и проверки (что запускалось и что видно в логах)

Источник: `data-tables/ops/_scratch/baseline.txt` (до миссии), `final_checks.txt` (T023, попытка 2), `all_patches.log`.

| Проверка | Строки из лога | Итог |
|---|---|---|
| node, claude-runner/tests | baseline: `result:  # tests 205 / # pass 205 / # fail 0 / NODE_EXIT=0`; final: `-> # tests 256 / # pass 256 / # fail 0` | 205 -> 256, без падений |
| node, board_roles и board_mission_save | `-> # pass 88 / # fail 0` | прошло |
| pytest, claude-runner/tests, полный | baseline: `summary: 1141 passed, 1 skipped, 10 failed (exit 1); 1202 tests collected`; final: `progress: 1307 dots, 9 F, 1 s = 1317 symbols` | 10 -> 9 падений, новых нет |
| падения pytest | список из 9 тестов совпадает с базой; `test_edit_window_keys_exist_in_both_translation_tables` теперь проходит | новых нет |
| ruff, файлы из списка | `HEAD copies: Found 63 errors. Working tree after fix: Found 63 errors.` | не хуже |
| ruff, board_api.py | `python -m ruff check src/claude_runner/board_api.py -> "Found 28 errors"`; baseline `board_api.py: 28 errors` | как до миссии |
| mypy --strict, по файлам | `api.py 0/0, board.py 4/4, board_api.py 13/13, board_pg.py 21/21,` (первое число - сейчас, второе - HEAD) | не хуже на каждом файле |
| git diff --check | `exit 0` (только предупреждения CRLF) | прошло |
| all_patches (2 теста) | `2 passed in 16.86s` | прошло |

Не запускалось или нет лога:
- `data-tables/ops/test_telegram_progress_patch.py` (golden-тесты Telegram): в логах нет вывода. Наличие тестов
  подтверждено только по именам; прохождение не заявляю.
- `data-tables/ops/adaptive_split/plan_gate.test.js` и `task_split.test.js` после удаления `adaptive_split/nodes/`:
  в логах этой миссии нет прогона. Прогон из 2026-10-20 относится к прошлой миссии.
- pytest только по файлам features (`test_features_config.py`, `test_board_features_api.py`) по отдельности: в логах
  есть только общий итог.
- Точный лог команды `python -m claude_runner.qa` не сохранён, только вывод (раздел 4).

Замечания по базе (baseline): ruff и mypy в базе сравнивались только по файлам, которые миссия меняла; общая строка
pytest в базе (1202 собранных) и в финале (1317) не сопоставима, потому что финальный запуск выполнен на другом наборе тестов и без
итоговой строки (`pipe-close RuntimeError`, см. `final_checks.txt` :7-9).

## 4. QA-прогон

Источник: `data-tables/ops/_scratch/qa_run.log` (263 строки, JSON-список из 17 сценариев).

- Полный прогон: 16 `"verdict": "pass"`, 1 `"verdict": "fail"`, код выхода `exit 1`.
- Упавший сценарий: `"scenario": "mission save blocks a bad JSON policy, then a valid model change persists",` на шаге 7.
  Причина: `Locator.click: Timeout 30000ms exceeded` для `Next ›`. На странице Review кнопка называется `Save changes`.
  Ошибка была в сценарии, не в приложении.
- Исправлен `claude-runner/qa/scenarios/save_validation_error_then_persist.yaml`; сценарий перезапущен один:
  строка лога `verdict: pass (qa-7b4e028c). Result: 17 of 17 scenarios pass (the directory holds 17 files, not 15).`
- Сценарий Features: `"scenario": "launch settings Features rows validate a bad JSON key, then a valid switch persists",` - `"verdict": "pass"`.

Что это значит: полного прогона на 17 сценариев после исправления не было. 17 из 17 - это 16 из полного прогона
плюс исправленный сценарий, перезапущенный один. Прежние 14 сценариев (14 файлов в git) в полном прогоне прошли.
Постановка миссии просила «полный прогон, 14 старых сценариев проходят»: это выполнено по сути, но без одного полного
прогона после правки.

`docker ps` с удалёнными `qa-*` объектами в логах нет. Заявление «не осталось qa-* объектов» не подтверждено.

## 5. Что не сделано: живая проверка

Не выполнено и не может быть выполнено без оператора (очередь, платные модели, n8n):
- ни один патч не применён к живому workflow; ничего не импортировано в n8n;
- пробная миссия `ahawr-split-trial` не запускалась; `expected_outcomes.json` не проверен на реальном прогоне;
- события разбиения (имена событий runner), текст уведомления о разбиении и вид `Task N of M, part P of C` в живом
  прогоне не подтверждены (`features-config.md` :212-223);
- `mission_features_json` не добавлен в таблицу миссий; читатели его терпят, но пробный прогон без колонки молча не
  включит разбиение (`features-config.md` :185-187);
- выполнено только в копии `_scratch/live_export.json` (раздел 9), не в свежем экспорте.

## 6. Шаги активации оператора

Источник: `claude-runner/docs/features-config.md` :142-154 и :184-230. Ничего из этого миссия не сделала.

1. Остановить очередь перед запуском с патчами (`/pause <причина>` в Telegram-боте, `docs/telegram-bot.md`); дождаться,
   пока не выполняется ни одна задача AHAWR (запуск откажется, пока что-то идёт).
2. Проверить `data-tables/ops/launch_mission.sh.new` и заменить им `launch_mission.sh` (diff, решение оператора; сейчас
   `launch_mission.sh` не тронут).
3. Добавить колонку `mission_features_json` в таблицу миссий в интерфейсе n8n. Это изменение схемы: нужно одобрение
   оператора; колонки не добавляются через raw SQL. Если колонки нет, пробный прогон без неё не разбивается.
4. Проверить значение `features_json` профиля `claude-code` (`hermes_config`, id 5) против нужных функций. Миссия
   его не сбрасывает.
5. Сгенерировать пробную миссию: `cd data-tables/ops && python _make_split_trial_mission.py` (пишет только в scratch).
   Строку миссии с `mission_features_json` `{"adaptive_split":{"enabled":true,"split_steps":25}}` занести в таблицу.
   Маршрут CSV в таблицу не проверен (`features-config.md` :201-202).
6. Запустить `bash data-tables/ops/launch_mission.sh ahawr-split-trial`; ожидаемая строка `started ahawr-split-trial`.
7. Наблюдать за прогоном по `_scratch/split_trial/expected_outcomes.json` и записать фактические результаты.
8. Откат: сбросить `mission_features_json` пробной миссии или `adaptive_split.enabled` в false. Для удаления патчей
   запуска импортировать `data-tables/ops/wf_before_ahawr-split-trial.json` при паузе. Файл отсутствует в ops (есть только
   `wf_before_ahawr-features-config.json`, его содержимое не сверено с живым экспортом); перед импортом проверить.

## 7. Версия и CHANGELOG (предложение)

Факты: `CHANGELOG.md` :8 - верхняя запись `## 2026-10-21 (0.9.0)` (QA-роль, guards в дашборде). `claude-runner/pyproject.toml`
:7 `version = "0.9.0"`. `claude-runner/src/claude_runner/api.py` :169 `version="0.9.0"`. Правило (CHANGELOG.md :6):
новые функции поднимают средний номер на 1.

Предложение: **0.10.0** (новые функции: `features_json`, Features в окне настроек, номера Telegram).
- `CHANGELOG.md` :6: строка `Version: **0.9.0**` -> `Version: **0.10.0** (claude-runner `0.9.0` → `0.10.0`, 2026-10-22; ...)`
  с прежней цепочкой дальше.
- Новая запись наверху:

```
## 2026-10-22 (0.10.0)

features_json: one JSON setting for the workflow features (adaptive split, plan without truncation, run hygiene),
read by the Constants node through the features patch; a Features section in the launch settings window; per-mission
override mission_features_json (optional column, readers tolerate its absence); the patches of the split code are
anchored edits, and the Telegram notices show the position of the task and of the part. Every feature defaults to
off; with the switches off behaviour is unchanged (golden tests).
```

- `claude-runner/pyproject.toml` :7 и `api.py` :169 - `0.10.0`. Эти правки в миссии не сделаны (правило версии
  относится к пушу, см. CHANGELOG.md :6).

## 8. Расхождения с постановкой и открытые риски

1. Номера Telegram: критерий ACCEPT 6 и PART 6 (4) требуют, чтобы у плоского плана менялась только позиция. По
   `features-config.md` :249-255 у плоского плана также меняется REVIEW и число «Completed tasks» (`4/5` -> `5/5`
   для пяти выполненных задач). Это исправление, а не только позиция. Решение оператора: оставить и записать в
   CHANGELOG или сделать отдельно.
2. Тест порядка не проверяет маркеры трёх патчей (раздел 2). Документ утверждает иное.
3. Тест порядка брал старый экспорт `_scratch/live_export.json` от 2026-10-21 12:33 (`baseline.txt` :9-10). Свежий
   экспорт не получен: в этой среде нет доступа к контейнеру n8n (`baseline.txt` :3-6). Документ в :138-139 говорит
   «fresh», это неверно для запуска.
4. `adaptive_split_patch.py` подменяет `jsCode` целиком только у двух узлов, которые сам добавляет (`ADDED`, :806-819).
   Документ говорит «без полной подмены», это верно только для узлов, которые патч не создаёт.
5. Нет логов: golden-тесты Telegram, node-тесты `adaptive_split` после удаления копий, `docker ps` после QA.
6. Полного QA-прогона после правки сценария нет (раздел 4).
7. `docs/features-config.md` :7-8 пишет «Status: design ... until then no reader uses this document's rules». Статус
   устарел: ядро и патчи готовы. :89 пишет, что три значения `max_tasks` (30/20/15) «must be settled before the
   golden test is written»: тест уже написан; вопрос не закрыт в документе.
8. `features_cases.json` разбирается и в node (`board_features.test.js`), и в pytest (`test_features_config.py`).
   Общий прогон по файлам в логах не сохранён.
9. Ruff E501 и mypy в файлах, которые миссия не меняла, остаются прежними (`board_api.py` 28 E501, `board_pg.py` 21 mypy).

## 9. Вывод

Что проверено и видно в логах: ядро, порядок патчей на старом экспорте, node-тесты (256 из 256), pytest (9 старых
падений без новых), ruff и mypy не хуже базы, QA 16 из 17 в полном прогоне и один упавший сценарий, исправленный и проходящий отдельно (см. раздел 4). Что не проверено: живой прогон, свежий экспорт, golden-тесты Telegram, маркеры трёх патчей в
тесте порядка, `docker ps` после QA.

Этот отчёт - единственный записанный файл миссии: `reports/ahawr-features-config-report-ru.md`.
