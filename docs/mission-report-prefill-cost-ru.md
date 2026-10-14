# Итоговый отчёт миссии llamacpp-prefill-cost (холодный префил)

Отчёт составлен оператором (Claude Code) по `docs/cold-prefill.md` и файлам на диске: Worker три раза подряд вернул вместо отчёта фрагмент (ответ обрывался на лимите вывода). Числа и пути ниже взяты из `docs/cold-prefill.md` (разделы указаны) и из живых проверок 2026-10-14; всё, что не измерено, помечено.

## 1. Цель и итог

Цель: удешевить холодный префил (до миссии 142 мин из 535 мин работы сервера, 27%; пересчёт 94–100K токенов занимал 6–7 минут) тремя способами: A сохранение кэша на диск, B меньше вывода инструментов в контексте, C общий префикс промпта у сессий.

| Часть | Что сделано | До | После |
|---|---|---|---|
| A. Слоты на диск | `slot-save-path=/slots`, том `D:\llama-slots`, `slot_cache.py` (save, restore, loop), хуки в сторожа, apply-скрипты и очередь | холодный пересчёт 6–7 мин после перезапуска llama/Docker | ИЗМЕРЕНО вживую: восстановление 7475 токенов за 2,6 с (позже 1,7 и 3,1 с), 306 695 948 байт прочитано; в проде пересчёт префикса после перезапуска не нужен, если следующий промпт начинается с него |
| B. Вывод инструментов | лимиты вывода только для локального Worker, правила в промптах, search-first хук | tool_result 56 496 831 симв.: Read 62,9%, Bash 33,8%, Grep 2,4% (T004) | РАСЧЁТ по снимку: лимиты Bash 12 000 симв. и Read 16 000 токенов убирают 688 097 симв. (≈229 тыс. токенов, 1,218% суммы снимка). Живой эффект НЕ измерен, лимиты выключены по умолчанию |
| C. Общий префикс | переменные вставки (WD, RUN ID, ATTEMPT, DATE) перенесены в хвост первого сообщения | общий префикс Worker–Worker 1 271 ток. (одна миссия), 1 198 ток. (разные) | РАСЧЁТ по представительным промптам: 1 441 ток.; Reviewer–Reviewer 1 084 → 1 402 ток.; выигрыш +170…+318 ток. ≈ 0,7–1,4 с на новую сессию. Живое подтверждение через `cache_trace` НЕ сделано |

Честный вывод: главный эффект даёт часть A (секунды вместо минут при перезапуске). Части B и C дают малый расчётный выигрыш (доли процента контекста, около секунды на сессию) и требуют живой проверки.

## 2. Покрытие задач T001–T016

| Задача | Результат (источник) |
|---|---|
| T001 исследование слотов | KV и рекуррентное состояние GDN сохраняются и восстанавливаются (`llama-memory-hybrid.cpp:190-195`, `llama-kv-cache.cpp:1969-2037`, `llama-memory-recurrent.cpp:743-822`); контрольные точки НЕ сохраняются, restore их очищает (`server-context.cpp:2523`) (док. Part A §6) |
| T002 размер слота | формула документа 18 440·N + 52 985 856 байт (§9), свободно 187 ГиБ; ЖИВОЙ замер выше (306,7 МБ на 7475 токенов) больше формулы: считать ≈18 КБ на токен + ≈170 МБ, формулу §9 считать нижней оценкой |
| T003 состав контекста | снимок `docs/cold-prefill/partB-cache-trace-snapshot.jsonl`, агрегаты в Part B |
| T004 доля tool_result | цифры в таблице п.1, источник `partB-runner.sqlite` и события runner |
| T005 лимиты вывода | `BASH_MAX_OUTPUT_LENGTH`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` (Part B §1–2) |
| T006 расхождение префиксов | точки расхождения и префикс «до» (Part C §1–5) |
| T007 конфиг llama | `slot-save-path=/slots` (`docker-entrypoint.sh:684`), том `D:\llama-slots:/slots` (`docker-compose.yml:29`) |
| T008 `slot_cache.py` | save, restore, loop; затем доработан под режим router (model в запросах, загрузка модели перед restore, пути `/d/...`) |
| T009 тесты | `test_slot_cache.py`: 31 тест проходит |
| T010 хуки | сторож Docker, три apply-скрипта, наблюдатель очереди (файлы `.new`, откат `.bak-T010`) |
| T011 лимиты вывода | `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS` (`config.py`, `claude_cli.py`, `runs.py:318`), только Worker на локальной модели, по умолчанию выключено |
| T012 search-first | хук и правила Worker в `agent_prompts.csv` (риски для Reviewer/Architect описаны, Part B §6) |
| T013 стабильный префикс | `AHAWR_STABLE_PREFIX=1` в `litellm/ahawr_hooks.py`, трассировка `AHAWR_CACHE_TRACE=1`; по умолчанию выключено |
| T014 патч workflow | `stable_prefix_patch.py`, идемпотентный; `agent_prompts.csv` не менялся (Part C §6) |
| T015 замер после | Part C §7: см. таблицу п.1 |
| T016 проверка | см. п.6 |

## 3. Что включено по умолчанию

Включено: `slot-save-path` в пресете `qwen3.8-27b-gsq-rco-iq2-s-mtp`; патч стабильного префикса вшит в `AHAWR_v13_ClaudeCode.json` (3 узла). Выключено, включается явно: `AHAWR_STABLE_PREFIX`, `AHAWR_CACHE_TRACE`, лимиты вывода (`AHAWR_LOCAL_TOOL_OUTPUT_LIMITS`), цикл `slot_cache.py loop`, восстановление после рестарта в сторожe.

## 4. Действия оператора

1. Перезапустить llama с новым пресетом (`docker compose up -d llama-server`), каталог `D:\llama-slots` должен существовать.
2. Пересобрать runner: `docker compose up -d --build claude-runner`.
3. Применить `.new`-файлы (сторож, `apply_cms.new.sh`, `apply_llama_template.new.sh`, `apply_no_diag.new.sh`, `queue_watch.py.new`), предварительно сохранив копии.
4. Сохранить слот и сверить размер с живым замером (≈18 КБ на токен + ≈170 МБ).
5. Проверка холодного старта: перезапустить llama, выполнить `python slot_cache.py --save-dir D:/llama-slots restore`, отправить промпт с тем же префиксом и посмотреть `prompt eval time` в `docker logs llama-cpp-server`: малое значение (пропорционально хвосту) значит, что восстановление сработало.
6. Для частей B и C: включить флаги, прогнать Worker и Reviewer на одной задаче и сравнить `first_diff` в `litellm/trace/cache_trace.jsonl` (должен лежать в переменном хвосте).

## 5. Откат

- Слоты: вернуть `docker-entrypoint.sh.bak-2026-10-13` и `docker-compose.yml.bak-2026-10-13` (llama.cpp), перезапустить llama; сохранённые файлы в `D:\llama-slots` становятся инертными.
- Сторож, apply-скрипты, наблюдатель: вернуть `.bak-T010`, остановить процесс `slot_cache.py loop`.
- Стабильный префикс: `python data-tables/ops/stable_prefix_patch.py --revert AHAWR_v13_ClaudeCode.json.bak`; флаг `AHAWR_STABLE_PREFIX` убрать.
- Лимиты вывода: `AHAWR_LOCAL_TOOL_OUTPUT_LIMITS=0`. Трассировка: `AHAWR_CACHE_TRACE=0`.

## 6. Изменённые файлы и проверки

Изменены: `AHAWR_v13.json`, `AHAWR_v13_ClaudeCode.json`, `AHAWR_Status_Bot.json`, `claude-runner/src/claude_runner/{claude_cli,config,runs}.py` и тесты, `litellm/ahawr_hooks.py` и тест, `litellm/runtime/local_thinking`, `retrieval-service` (хранилище PG, к этой миссии отношения не имеет), `scripts/patch_*`, `docker-compose.yml`, `.env.example`, `.gitignore`. Новые: `docs/cold-prefill.md`, `docs/cold-prefill/`, `litellm/runtime/local_thinking.auto`, `AHAWR_v13_ClaudeCode.json.bak`. Скрипты в `data-tables/ops` (вне git): `slot_cache.py`, `test_slot_cache.py`, `*.new*`, `stable_prefix_patch.py`.

Проверки (Part T016 §5, все с кодом 0): `test_slot_cache.py` 31 passed; тесты runner 143 passed; litellm hooks 19 passed; retrieval 57 passed; синтаксис JS узлов workflow 15/45/48 без ошибок; `check_workflows.py` ok; идемпотентность патчей подтверждена (файл байт-в-байт тот же); `bash -n` для apply-скриптов и `real_queue.sh`; CSV и JSON валидны; git не менялся (HEAD `3508408`, коммитов нет). НЕ запускалось: `docker compose config --quiet` (в окружении Worker нет docker CLI; заменено разбором YAML).

## 7. Открыто и не проверено

1. Живой эффект частей B и C не измерен (оба флага выключены по умолчанию); оценка «≈3,5% окна» для лимитов вывода получена из суммы по снимку и как оценка на один запрос ненадёжна, на неё полагаться нельзя.
2. Контрольные точки не сохраняются на диск: после восстановления слота промпт, расходящийся с сохранённым префиксом, пересчитывается с нуля (`server-context.cpp:3263-3268`); реализация сохранения точек (Part A §7, п.4) не сделана.
3. Размер слота: формула §9 занижает; нужен замер на 32K токенов.
4. Часть мер вне миссии сделана оператором позже: `--cache-ram` снижен до 1024, `ctx-checkpoints` 8, `slot_cache.py` доработан под router-режим; их влияние на память Docker VM требует отдельного замера.
