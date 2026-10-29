# Отчёт миссии ahawr-queue-from-board (очередь из доски)

Дата проверки: 2026-10-26. Рабочая папка: `D:\n8n` (`/d/n8n`).

## 1. Что сделано

Очередь миссий теперь читается из `board.queue` (доска, Kanban). Оболочка очереди
спрашивает дашборд о следующей миссии перед каждым выбором. Старый статический список
`QUEUE` в `real_queue.sh` остался как запасной вариант. Новое поведение включено по
умолчанию и выключается переменной `QUEUE_FROM_BOARD=0`.

| Файл | Строки | Что это |
| --- | --- | --- |
| `data-tables/ops/queue_board.py` (новый) | 52-53 | `DEFAULT_DASHBOARD` и `DASHBOARD`, адрес дашборда, переменная `QUEUE_BOARD_URL` |
| | 69 | `RETRY_DELAY_SECONDS = 2.0`, задержка между тремя попытками чтения |
| | 138-152 | `read_board`: три попытки чтения, затем `BoardUnavailable` |
| | 155-177 | `next_mission`: чистая функция правил выбора |
| | 218-230 | `_send`: запись с заголовком `X-AHAWR-Dashboard: 1` |
| | 240-272 | `shell_entries`: список для `sync-from-shell` |
| | 286-315 | `_write`: `add`, `first`, `remove`, `move`, `sync-from-shell` |
| | 372-394 | `main`: коды выхода 0, 1, 3 |
| `data-tables/ops/real_queue_board.sh` (новый, 268 строк) | 169 | `MAX_PICKS=200`, предел цикла выбора |
| | 170-197 | DRY-ветка: `QUEUE_FROM_BOARD` не равно 0 — DRY-проверка доски |
| | 209-223 | `pick_next`: выбор через `queue_board.py next`, запасной статический список |
| | 224-231 | `remove_done`: удаление завершённой миссии с доски, ошибка = `ALERT` |
| | 237 | число попыток из доски |
| | 263 | вызов `remove_done` после `completed` |
| `data-tables/ops/queue_watch.py` (правка) | 124 | `queue_alive` ищет и `real_queue.sh`, и `real_queue_board.sh` |
| | 182-183 | `queue_script()`: `real_queue.sh` при `QUEUE_FROM_BOARD=0`, иначе `real_queue_board.sh` |
| | 188-190 | команда запуска использует `queue_script()` |
| `claude-runner/tests/test_queue_board.py` (новый) | | 24 теста правил, команд и кодов выхода |
| `claude-runner/tests/test_queue_board_contract.py` (новый) | | 2 теста: оболочка берёт порядок, заданный переносом в Kanban |
| `claude-runner/tests/test_queue_watch_flag.py` (новый) | | 4 теста флага и проверки живости |
| `claude-runner/tests/test_real_queue_board_dry.py` (новый) | | 3 теста DRY-прогона с фейковым сервером доски |
| `claude-runner/docs/queue-from-board.md` (новый, 182 строки) | | описание потока, правил, флага, таблица списков, миграция, откат |

## 2. Задания T001-T020

В репозитории нет списка заданий T001-T020 этой миссии. Проверено:

- `ahawr-search "where is the list of tasks T001 to T020 for the features-config mission, the task file or report that maps each T number to its result"` —
  в логах встречаются только T001, T014, T015, T020 и T023 (`reports/ahawr-features-config-report-ru.md:10-13`);
  `data-tables/ops/_make_qa_container_mission.py` задаёт миссию одной целью и шестью частями
  (Part 1-6), а не 19 задачами (`reports/ahawr-qa-container-report-ru.md:18-22`).
- `grep -rIn 'T001' /d/n8n/data-tables /d/n8n/reports` — посторонние вхождения
  (шаблоны `agent_prompts.csv`, отчёты других миссий, макеты доски); ни одно не относится
  к `ahawr-queue-from-board`.
- Файл миссии `data-tables/ops/_make_queue_from_board_mission.py` описывает части
  PART 1-PART 5 и шесть критериев приёмки (строки 13-23, `ACCEPT` строки 36-43),
  без номеров T.
- Вывод задания T020 в доступных файлах не найден. Файл
  `data-tables/ops/_accept_fc_t020.py` записывает приёмку T020 в таблицу состояния n8n
  (строки 9-15), но это приёмка миссии `ahawr-features-config`, а не этой миссии.

Поэтому соответствие отдельных T-id частям не установлено. Ниже — части PART 1-5 и
артефакты, которые они породили (проверены по файлам). Все пять частей покрыты этим
перечнем; таблица по отдельным T-id не приводится.

| Часть | Артефакты | Состояние |
| --- | --- | --- |
| PART 1 (дизайн-документ) | `claude-runner/docs/queue-from-board.md` | создан |
| PART 2 (хелпер + тесты) | `queue_board.py`, `test_queue_board.py` | созданы |
| PART 3 (оболочка) | `real_queue_board.sh`, `test_real_queue_board_dry.py` | созданы |
| PART 4 (сторож + скрипты) | `queue_watch.py`, `queue_watch_flag.py` | созданы |
| PART 5 (доска) | нет новых файлов (проверено, раздел 3.5) | проверено |

## 3. Старый и новый поток

**Старый поток.** Порядок миссий задан списком `QUEUE` в `real_queue.sh` (строки 16-63).
Правила для каждой миссии заданы списками `RETRY_FAILED`, `ATTEMPTS5`, `ATTEMPTS10`,
`MAXTASKS30`, `CLOUD_WORKER`, `NEEDS_FILE` (строки 64-69). Список читается один раз при
старте. Изменение требует остановки очереди и её перезапуска. Доска `board.queue` при
этом не используется для выбора.

**Новый поток** (`docs/queue-from-board.md`, раздел "New flow", строки 32-46):

1. Миссия стартует или завершается. Оболочка пишет `started` или `ended`.
2. Оболочка вызывает `python queue_board.py next --skip ...`.
3. `queue_board.py` читает `GET /api/board/queue` и `GET /api/board/live-states`
   (`queue_board.py:131`).
4. `next_mission` выбирает миссию по правилам (`queue_board.py:155-177`) и печатает
   `<mission_id> <attempts> <retry_failed>`.
5. Оболочка запускает миссию. Пустые поля берут значения по умолчанию.
6. После `completed` оболочка удаляет запись: `DELETE /api/board/queue/<mission_id>`
   (`real_queue_board.sh:229`). Ошибка даёт строку `ALERT`.
7. Цикл возвращается к шагу 1.

Правила выбора (`queue_board.py:155-177`): сначала `active` (прерванная миссия),
затем первая включённая запись, не `completed`. Запись `failed` выбирается только с
`retry_failed`. Миссии без строки живого состояния пропускаются.

## 3.5. PART 5 (доска) — проверка

Проверено grep-ом ключей `queue`, `live` и `live_order` в трёх файлах
`board_i18n_en.json`, `board_i18n_ru.json` и `board_i18n.js` (`BUNDLED_EN`,
строка 19). Ключа для заметки «порядок живой» в Kanban нет: ключи `queue.*`
(строки 24-26) описывают группы `running`/`queued`/`completed`, `task_of`,
`stats.title`, `stats`, `up.title`, `down.title`; ключи `control.*`
(строка 51) — управление. Новых текстов PART 5 в этой сессии не добавлено:
доска уже пишет `board.queue` через API, а оболочка читает тот же порядок,
пропасть не найдена.

## 4. Сравнение DRY

`DRY=1 bash real_queue.sh` и `DRY=1 bash real_queue_board.sh` с `QUEUE_FROM_BOARD=0`
НЕ ВЫПОЛНЕНО: ветка DRY вызывает `check_env` (строка 171), который использует
`docker inspect` (строки 116, 124) и `docker exec` (строка 117) — без `docker` DRY
не запустить. Вместо неё проверено тестом
`test_flag_off_board_shell_prints_the_same_decision_lines_as_the_static_shell`
(`claude-runner/tests/test_real_queue_board_dry.py:95`), который прошёл (раздел 6).

Различия файлов, `diff data-tables/ops/real_queue.sh data-tables/ops/real_queue_board.sh`:
54 строки с `<` или `>`, пять блоков изменений:

```
168a169
171a173,186
190c205,232
192,195c234,237
221c263
```

Строка 221 старого файла (`if [ "$st" = completed ]; then fails=0; else`) заменена на
строку 263 нового (`... remove_done "$m"; else`). Остальные блоки добавляют пункт
доски, выбор и удаление. Строки списков (1-69) совпадают.

Контрольная сумма `real_queue.sh` (не изменён в этой миссии):

```
eaefbbea51aece68fdc0973160f70c80cd320b098da481a81c97097aea07b53a  data-tables/ops/real_queue.sh
```

Время изменения `real_queue.sh`: 2026-10-24 03:24:56. Файлы миссии изменены 2026-10-25
и 2026-10-26. Значение до изменения не записывалось, поэтому сравнить до и после
нельзя. Сравнивается только дата изменения.

## 5. Таблица списков `real_queue.sh`

Источник: `claude-runner/docs/queue-from-board.md`, строки 110-122.

| Список | Смысл | Новое место |
| --- | --- | --- |
| `QUEUE` (строки 16-63) | порядок миссий | строки `board.queue`: `position`, `mission_id`; запасной вариант |
| `ATTEMPTS5` (68) | 5 попыток на задачу | `attempts_per_task` = 5; запасной вариант |
| `ATTEMPTS10` (67) | 10 попыток на задачу | `attempts_per_task` = 10; запасной вариант |
| `MAXTASKS30` (66) | лимит задач 30 вместо 20 | нет поля `QueueEntry`; остаётся в оболочке |
| `CLOUD_WORKER` (65) | Worker на облачной модели | нет поля `QueueEntry`; остаётся в оболочке |
| `RETRY_FAILED` (64) | повтор проваленной миссии | `retry_failed` = true; запасной вариант |
| `NEEDS_FILE` (69) | пропуск без файла данных | нет поля `QueueEntry`; остаётся в оболочке |

Поле `enabled` и поле `note` не имеют списка в `real_queue.sh`.

## 6. Устаревшие скрипты (ничего не удалено)

Источник: `claude-runner/docs/queue-from-board.md`, строки 129-141. Все перечисленные
файлы существуют в `data-tables/ops/` (проверено).

| Скрипт | Замена |
| --- | --- |
| `sync_board_queue.py` | `python queue_board.py sync-from-shell` |
| `_queue_append_qa_gate.sh` | `queue_board.py add ahawr-qa-gate --after ahawr-features-config` |
| `_queue_append_qa_layers.sh` | `queue_board.py add ahawr-qa-layers --after ahawr-features-config` |
| `_queue_append_qa_smoke.sh` | `queue_board.py add ahawr-qa-smoke --after ahawr-guards-migration` |
| `_queue_append_report_pack.sh` | `queue_board.py add ahawr-report-pack --after ahawr-guards-migration` |
| `_queue_append_two.sh` | два вызова `queue_board.py add` |
| `_queue_insert_ops.sh` | `queue_board.py add ahawr-dashboard-ops-control --after ahawr-dashboard-mission-board` |
| `_queue_reorder.sh` | `queue_board.py move <mission_id> --after <other_id>` |
| `_queue_run_bench_hour.sh` | `queue_board.py add bench-rag-api-local-r2 --after ahawr-guards-migration` |
| `_queue_resume_after_commit.sh` | не нужна: оболочка читает доску при каждом выборе |
| `_restart_queue_at_end.sh` | не нужна: изменение доски действует при следующем выборе |

`_restart_haiku.sh` и `_restart_queue_once.sh` не редактируют список очереди. Они
остаются без изменений.

`sync_board_queue.py` НЕ РЕТИРОВАН. Файл не удалён, но `queue_board.py` его импортирует:
`queue_board.py:242` (`importlib.import_module("sync_board_queue")`) и `queue_board.py:276`.
Без этого файла `sync-from-shell` не работает. Строка в документе
(`queue-from-board.md:30`, "It is removed once the shell reads the board") не совпадает с
кодом.

## 7. Проверки

Запуск выполнен из `claude-runner/`.

**Запущено и прошло:**

- `python -m pytest tests/test_queue_board.py tests/test_queue_board_contract.py tests/test_queue_watch_flag.py tests/test_real_queue_board_dry.py -q --no-cov -p no:cacheprovider`
  Код выхода 0. Строка прогресса: `.........................................` + `[100%]`
  (41 = 24 + 2 + 4 + 3 тестовых функций с параметризацией). Сводная строка
  `41 passed` не появилась в выводе `-q` (она в stderr); подтверждено
  `grep -c PASSED` = 41 в выводе `-rA` (файл `/tmp/queue_tests_rA2.txt`).
- `python -m ruff check` для `queue_board.py`, `tests/test_queue_board.py`,
  `tests/test_queue_board_contract.py`, `tests/test_queue_watch_flag.py`,
  `tests/test_real_queue_board_dry.py`: строка `All checks passed!` для каждого файла.
- `python -m mypy ../data-tables/ops/queue_board.py`: строка
  `Success: no issues found in 1 source file`.

**Ruff для `queue_watch.py`:** строка `Found 39 errors.` для текущего файла. Для
`queue_watch.py.bak-T010` строка `Found 28 errors.`. Разница 11 ошибок: в текущем
файле добавлены функции `slot_loop_running`, `archive_think_steps`, `ensure_relay`
и блок `ensure_relay`/`archive_think_steps` в цикле (не относятся к этой миссии).
Ошибка E501 в строках 187-191 относится к блоку запуска, в котором правка
строки 189 сделана для миссии.

**НЕ ЗАПУЩЕНО:**

- `DRY=1 bash real_queue.sh` и `DRY=1 bash real_queue_board.sh` на настоящих данных:
  нет `docker` в оболочке агента, и скрипт обращается к контейнерам и базе.
- Полный набор тестов `claude-runner`: по правилам запускаются только связанные тесты.
- `mypy` для `queue_watch.py`: не запускался.
- Ruff для `real_queue_board.sh`: не Python-файл.
- Часть 5 миссии (проверка Kanban, `board_queue.js`, `board_dnd.js`, тексты
  `L(key, ...)`): проверена grep-ом ключей `queue`, `live`, `live_order` в трёх
  файлах i18n (раздел 3.5). Новых текстов PART 5 не добавлено: пропасть не найдена.

## 8. Шаги оператора (НЕ ВЫПОЛНЕНО)

Все шаги ниже НЕ ВЫПОЛНЕНЫ. Их не делал агент, это план для оператора.

**Замена файла на паузе очереди** (`docs/queue-from-board.md`, строки 147-162):

1. НЕ ВЫПОЛНЕНО. Посеять доску из оболочки: `python queue_board.py sync-from-shell`.
   Вызов заменяет весь `board.queue` и требует версию с дашборда.
2. НЕ ВЫПОЛНЕНО. Проверить: `python queue_board.py list`. Порядок и
   `attempts_per_task` сравнить со строками `QUEUE` и `ATTEMPTS5`.
3. НЕ ВЫПОЛНЕНО. При паузе очереди, без запущенной миссии:
   `cp data-tables/ops/real_queue.sh data-tables/ops/real_queue.sh.bak-board-migration`
   и затем `cp data-tables/ops/real_queue_board.sh data-tables/ops/real_queue.sh`.
4. НЕ ВЫПОЛНЕНО. Запустить очередь. В журнале должна быть строка `queue_board.py next`
   при каждом выборе.

Замечание по шагу 3: `queue_watch.py` при старте запускает `real_queue_board.sh`, если
`QUEUE_FROM_BOARD` не равно 0 (`queue_watch.py:183`). Значит, без замены файла очередь
уже читает доску. Замена нужна, только если очередь запускается вручную с
`real_queue.sh`.

**Откат** (`docs/queue-from-board.md`, строки 164-171):

- НЕ ВЫПОЛНЕНО. Флаг: `export QUEUE_FROM_BOARD=0` в оболочке, которая запускает
  `queue_watch.py`, затем перезапуск очереди. Файл менять не нужно.
- НЕ ВЫПОЛНЕНО. Старый файл: `cp data-tables/ops/real_queue.sh.bak-board-migration data-tables/ops/real_queue.sh`
  при остановленной очереди, затем перезапуск.

## 9. Риски и открытые пункты

1. **Пустая доска останавливает очередь.** При `queue_board.py next` с кодом 1 оболочка
   выходит из цикла и статический `QUEUE` не используется (`real_queue_board.sh:216-218`).
   Код 3 (доска недоступна) даёт запасной вариант. Поэтому перед включением доску надо
   засеять (шаг 1 раздела 8).
2. **Документ расходится с кодом.** `queue-from-board.md:69` называет переменную
   `QUEUE_FETCH_DELAY_SECONDS`. В коде такой переменной нет: задержка задана константой
   `RETRY_DELAY_SECONDS = 2.0` (`queue_board.py:69`). Документ не исправлен, потому что
   по правилам миссии менять можно только этот отчёт.
3. **Документ не упоминает `live-states`.** Шаг 3 в `queue-from-board.md:37-38` говорит
   только о `GET /api/board/queue`. Код читает и `live-states` (`queue_board.py:131`).
4. **`sync_board_queue.py` не удалён и нужен** (раздел 6).
5. **Широкий перехват исключений** в `queue_board.py:392` (`except Exception` с
   `noqa: BLE001`). Ошибка печатается и даёт код 3, но перехват широкий.
6. **Удаление без версии.** `DELETE /queue/{id}` не проверяет версию. Параллельная
   правка в Kanban не обнаруживается (`queue_board.py:291-294`,
   `real_queue_board.sh:228`). Это записано как `shortcut` в коде.
7. **Явная тройка в доске** читается как значение по умолчанию (`real_queue_board.sh:237`,
   `shortcut`).
8. **Настройки значений по умолчанию.** `queue_watch.py` читает флаг из своей среды.
   Установка `QUEUE_FROM_BOARD=0` только в оболочке оператора не действует на запуск из
   `queue_watch.py`.
9. **Нет настоящего DRY-сравнения.** Сравнение выполнено только тестом с фейковым
   сервером (раздел 4).

## 10. Предложение для CHANGELOG (не записано в файл)

По правилу `CHANGELOG.md` (строка 6), новая функция поднимает среднее число версии.
Текущая версия `0.10.100` (`CHANGELOG.md:6`, `claude-runner/pyproject.toml:7`).
Предлагаемая версия: `0.11.0`.

```
## 2026-10-26 (0.11.0)

New function: the mission queue is read from the board (board.queue). The queue shell asks
queue_board.py for the next mission; the static QUEUE list is the fallback after three
failed reads. Controlled by QUEUE_FROM_BOARD (on by default, 0 turns it off).
queue_watch.py starts real_queue_board.sh unless QUEUE_FROM_BOARD=0. The one-off queue
scripts are replaced by queue_board.py commands; none is deleted.
```
