## 3. Часть B: размеры контекста (до/после)

### 3.1. Часть B.1: доля `tool_result` по инструментам (документ «Part B: measurements», §Runner sqlite)

Источник: событийный дамп claude-runner (`/data/events/<run_id>.jsonl`) и sqlite `runs` (1,497 записей), выгруженные в `docs/cold-prefill/` read-only; замер `tool_output_stats.py runner` (exit 0). Снимок 2026-10-13 08:56 UTC.

**Доля по инструментам (все роли/модели):**

| инструмент | кол-во | всего симв. | % tool_result | медиана | p95 |
|---|---:|---:|---:|---:|---:|
| Read | 6,806 | 35,546,344 | 62.917 | 2,824 | 20,559 |
| Bash | 15,278 | 19,074,891 | 33.763 | 524 | 5,289 |
| Grep | 1,521 | 1,367,947 | 2.421 | 333 | 3,727 |
| Edit | 2,135 | 275,727 | 0.488 | 102 | 187 |
| Glob | 180 | 148,327 | 0.263 | 133 | 6,228 |
| Write | 252 | 44,542 | 0.079 | 162 | 494 |
| Agent | 8 | 36,709 | 0.065 | 4,024 | 7,627 |
| Monitor | 6 | 1,811 | 0.003 | 302 | 302 |
| bash | 2 | 238 | 0.000 | 119 | 119 |
| ahawr-search | 2 | 152 | 0.000 | 76 | 76 |
| ToolSearch | 2 | 82 | 0.000 | 41 | 50 |
| ReportFindings | 3 | 61 | 0.000 | 21 | 21 |
| **итого** | 26,195 | 56,496,831 | 100.000 | | |
| (сохранено, обрезано) | | 53,651,766 | | | |

Истинные (необрезанные) символы: 56,496,831; сохранённые (обрезанные до 20 000): 53,651,766.

**Крупнейшие единичные результаты (топ-20)** — все `Read`, обрезаны до 20 000 симв. в хранилище:

| # | инструмент | ист. симв. | роль | модель | run_id | seq | ввод (намёк) |
|---:|---|---:|---|---|---|---:|---|
| 1 | Read | 61,749 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 29 | `arg.cpp` |
| 2 | Read | 60,332 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 31 | `arg.cpp` |
| 3 | Read | 59,132 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 33 | `arg.cpp` |
| 4 | Read | 51,233 | reviewer | opus | run_f81837dd96044f64a94399e102a4664c | 60 | `Hermes_Run_Manager_v5.json` |
| 5 | Read | 50,563 | worker | haiku | run_d9e745d90ac2404b8491e3296a58a294 | 22 | `pages.tsx` |
| 6 | Read | 48,979 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_49c6442c08934574a2327885be718e00 | 63 | `service.py` |
| 7 | Read | 48,979 | reviewer | opus | run_f81837dd96044f64a94399e102a4664c | 31 | `service.py` |
| 8 | Read | 47,365 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 32 | `llama-cpp-dcfr-research.patch` |
| 9 | Read | 44,659 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_b9643566b44147d3aa430d09460f8327 | 13 | `indexer.py` |
| 10 | Read | 44,499 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_77ac0c754b4d47bd9bdac9915dca9561 | 226 | `indexer.py` |
| 11 | Read | 44,489 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 36 | `llama-cpp-dcfr-research.patch` |
| 12 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 23 | `service.py` |
| 13 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 58 | `service.py` |
| 14 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 86 | `service.py` |
| 15 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 114 | `service.py` |
| 16 | Read | 44,123 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_ee0278788512411abe3f0797439a17f7 | 8 | `service.py` |
| 17 | Read | 43,562 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_2b2b6c118337408fb2e2bbb0611a6b80 | 21 | `indexer.py` |
| 18 | Read | 42,566 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 34 | `llama-cpp-dcfr-research.patch` |
| 19 | Read | 42,519 | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | run_003a0e2fb4d94d54b0ff834f9c120062 | 20 | `test_admin_routes.py` |
| 20 | Read | 42,519 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp-gpu96 | run_511c87933c9540dbb22a3b8634f7dfe9 | 17 | `test_admin_routes.py` |

**Разбивка по ролям/моделям (топ по ист. символам):**

| инструмент | роль | модель | кол-во | всего симв. | % tool_result | медиана | p95 |
|---|---|---|---:|---:|---:|---:|---:|
| Read | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 4,419 | 26,490,087 | 46.888 | 3,183 | 23,043 |
| Bash | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 11,466 | 13,925,087 | 24.648 | 474 | 5,219 |
| Read | reviewer | opus | 809 | 3,396,388 | 6.012 | 2,454 | 12,530 |
| Read | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 796 | 2,602,741 | 4.607 | 1,920 | 10,351 |
| Read | worker | haiku | 324 | 1,043,092 | 1.846 | 2,220 | 10,309 |
| Bash | reviewer | opus | 588 | 1,013,493 | 1.794 | 727 | 5,634 |
| Grep | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 791 | 961,851 | 1.702 | 585 | 4,677 |
| Read | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | 205 | 844,093 | 1.494 | 2,263 | 14,456 |
| Bash | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 662 | 829,185 | 1.468 | 727 | 5,239 |
| Bash | worker | haiku | 757 | 807,784 | 1.430 | 285 | 4,825 |
| Bash | worker | opus | 315 | 696,084 | 1.232 | 933 | 8,917 |
| Bash | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | 635 | 651,286 | 1.153 | 290 | 5,107 |
| Read | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp-gpu96 | 91 | 449,098 | 0.795 | 2,877 | 14,456 |
| Bash | worker | sonnet | 221 | 415,602 | 0.736 | 480 | 10,039 |
| Grep | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 662 | 370,488 | 0.656 | 104 | 2,212 |
| Bash | architect | qwen3.8-27b-gsq-rco-iq2-s-mtp | 269 | 273,146 | 0.483 | 727 | 3,359 |

Команды экспорта (read-only):

```
docker exec claude-runner python -c "import sqlite3; s=sqlite3.connect('file:/data/runner.sqlite?mode=ro',uri=True); d=sqlite3.connect('/tmp/runner.sqlite'); s.backup(d)"
docker cp claude-runner:/tmp/runner.sqlite docs/cold-prefill/partB-runner.sqlite
docker cp claude-runner:/data/events/. docs/cold-prefill/partB-runner-events/
python data-tables/ops/tool_output_stats.py runner --events docs/cold-prefill/partB-runner-events --runs docs/cold-prefill/partB-runner.sqlite
```

Снимок трассы (1,271 записей, 50 разговоров, 196,280,392 симв. суммарно): `chars` — сериализованная длина всего списка сообщений (роли, JSON-кавычки, reasoning), поэтому из трассы **нельзя** выделить долю `tool_result`; именно поэтому замер доли — по данным runner (sqlite + event dump).

### 3.2. Часть B: env-переменные и лимиты вывода (документ «Part B: options»)

Поддерживаемые переменные (сканирование бинарного `claude.exe` v2.1.286):

| переменная | статус | ограничивает | значение по умолчанию/макс. |
|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` | supported | символы вывода Bash/PowerShell, возвращаемые в результат | default 30000, hard cap 150000 |
| `bashOutputMaxChars` (settings) | supported | то же, но как настройка; **заменяет** env при установке | default 30000, clamp 4000–128000 |
| `MAX_MCP_OUTPUT_TOKENS` | supported | токены ответов MCP-инструментов | default 25000; warning > 10,000 |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | supported | токены на `Read` файла | default 25,000 |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | supported | макс. токены ответа (и потолок для auto-compact) | зависит от модели; default 32,000, cap 128,000 |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | supported | окно auto-compact в токенах | 100,000–1,000,000, cap = контекст модели |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | supported | % окна, при котором срабатывает компакция | default ~95% (нельзя поднять выше) |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | supported | переопределяет контекст модели | n/a (зависит от модели) |
| `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` | supported | символы описания MCP-инструмента | default 2048 |
| `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` | unverified | вычищает env из subprocess | n/a |

Ограничение на провайдера: `child_env` (`claude_cli.py:186-203`) собирает env на каждый run: контейнерный env минус `CLAUDE_RUNNER_*`; если провайдер изолирует креды (`config.py:206-208`), удаляет `CREDENTIAL_VARS`/`SECONDARY_MODEL_VARS`; накладывается блок провайдера `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` (`config.py:291-320`); всегда добавляет `DISABLE_AUTOUPDATER=1` и `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1`. Поэтому `BASH_MAX_OUTPUT_LENGTH=12000` для локального провайдера попадает **только** в child env Worker-а на локальной модели; Anthropic-роли (Architect/Reviewer) не получают этот блок.

Рекомендованные пороги (замерены по T004):

| порог | значение | обоснование | экономия (замер, T004) |
|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` (локальный Worker) | **12,000** симв. | медиана Bash 524, p95 5,289; только 18 из 15,278 результатов > 12,000 | **307,130 симв.** = 1.610% от Bash = **0.544%** от всего = ~102k ток. |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` (локальный Worker) | **16,000** ток. (~48,000 симв.) | медиана Read 2,824; Read = 62.9% tool_result; крупные Read 44–61k симв.; 7 Read > 48,000 | **380,967 симв.** = 1.072% от Read = **0.674%** от всего = ~127k ток. |
| **вместе** | | | **688,097 симв. = 1.218%** от 56,496,831 (≈3.5% полного окна 65,536 ток.) |

Реализация (T011): `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS` (opt-in, по умолчанию выкл.); `Settings.local_tool_output_limits` читает переменную (default off); `child_env` добавляет оба лимита только когда `role == "worker"` и провайдер не None; `docker-compose.yml` передаёт `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS=${AHAWR_LOCAL_TOOL_OUTPUT_LIMITS:-0}`; `.env.example` документирует; `.env` не трогается. Тесты: `test_config_cli.py::test_local_tool_output_limits_apply_only_to_the_local_worker`, `test_runs.py::test_local_tool_output_limits_env_reaches_only_the_local_worker`.

Правила промпта Worker (T012, `data-tables/agent_prompts.csv`, worker,1,true, строки 113-115):
> Не читать большие файлы (более ~400 строк), логи или вывод команд целиком.
> Не выводить все diff/логи: использовать `grep -n ... | head -50`, `head`, `tail`, `--stat`.
> Ограничивать вывод команд: цитировать не более ~30 строк вывода в отчёте; остальное — ссылки file:line и количество совпадений (`grep -c`).

Reviewer/Architect не меняются. `search_hook.py` не трогается: `ahawr-search` — 2 результата, 152 симв. из 56,496,831 (0.000%), экономии нет.

### 3.3. Часть B: итоги «до/после»

До: Read 35,546,344 (62.9%), Bash 19,074,891 (33.8%), Grep 1,367,947 (2.4%); всего 56,496,831 симв.
После (опциональные лимиты + промпт-правила): Read лимит 48,000 симв. (16k ток.), Bash лимит 12,000 симв.; экономия 688,097 симв. = 1.218% (≈3.5% окна 65,536 ток.).## 4. Часть C: общий префикс промпта (BEFORE/AFTER)

Источник: `data-tables/ops/prefix_stats.py` и `data-tables/ops/prefix_stats_after.py` (1,321 run; worker 660, reviewer 621, architect 25). Оценка токенов: chars // 3.

### 4.1. BEFORE (до патча) — документ §5

| категория пар | пар | мин симв. (мин ток.) | макс симв. (макс ток.) | сред. симв. (сред. ток.) |
|---|---:|---:|---:|---:|
| Worker–Worker (полный ввод) | 217,470 | 0 (0) | 250,090 (83,363) | 3,022 (1,007) |
| Reviewer–Reviewer (полный ввод) | 192,510 | 0 (0) | 19,371 (6,457) | 2,342 (780) |
| Worker–Reviewer (одна WD) | 41,554 | 9 (3) | 9 (3) | 9 (3) |
| Worker–compact (fresh-session-retry) | 45 | 5,031 (1,677) | 29,147 (9,715) | 5,607 (1,869) |
| Worker–resume (`RESUME TASK`) | 703 | 14 (4) | 948 (316) | 48 (16) |
| Reviewer–resume (`The previous attempt`) | 120 | 65 (21) | 335 (111) | 199 (66) |

`--same-task` (worker full-input по (task id, working directory)): 138 групп ≥2, 1,336 пар внутри групп; мин 1,190 (396), медиана 4,956 (1,652), макс 7,948 (2,649), сред. 3,982 (1,327).

### 4.2. AFTER (после патча, T015) — документ §7.1

Патч переносит переменные данные (WD-блок, RUN ID, ATTEMPT, DATE) из **головы** в **хвост** первого user-сообщения.

| категория пар | BEFORE same (симв./ток.) | AFTER (симв./ток.) | BEFORE live (симв./ток.) | точка расхождения (AFTER) |
|---|---:|---:|---:|---|
| Worker–Worker, one mission | 3,813 / 1,271 | 4,325 / 1,441 | 3,982 / 1,327 (сред. same-task) | id запроса RETRIEVED CONTEXT |
| Worker–Worker, cross-mission | 3,595 / 1,198 | 4,325 / 1,441 | 4,818 / 1,606 (изм. пара) | блок WORKING DIRECTORY (в хвосте) |
| Reviewer–Reviewer, one mission | 3,252 / 1,084 | 4,208 / 1,402 | 2,342 / 780 (сред. по всем) | блок WORKING DIRECTORY (в хвосте) |
| Worker–Reviewer (одна WD) | 9 / 3 | 9 / 3 | 9 / 3 | фраза роли (симв. 9) |
| Worker–compact, feedback различается | 558 / 186 | 558 / 186 | 5,607 / 1,869 (сред.) | digest-контент |
| Worker–compact, same feedback | 558 / 186 | 558 / 186 | 5,607 / 1,869 (сред.) | digest-контент |
| Worker–resume (одна задача) | 653 / 217 | 666 / 222 | 48 / 16 (сред.) | RUN ID / ATTEMPT / DATE (в хвосте) |
| Reviewer–resume | 65 / 21 | 65 / 21 | 199 / 66 (сред.) | текст ошибки |

### 4.3. Экономия prefill на новую сессию (документ §7.2)

Пропуск llama-server 220–260 tok/s; каждый сэкономленный токен — не требует повторного prefill.

| категория | выигрыш токенов | время (сек) |
|---|---:|---:|
| Worker–Worker, one mission | +170 | **0.7–0.8 s** |
| Worker–Worker, cross-mission | +243 | **0.9–1.1 s** |
| Reviewer–Reviewer, one mission | +318 | **1.2–1.4 s** |
| Worker–compact | 0 | 0 s |
| Worker–resume | +5 | 0.0 s |
| Worker–Reviewer | 0 | 0 s |
| Reviewer–resume | 0 | 0 s |

Это оценки по репрезентативным промптам; live-подтверждение требует запуска патченного workflow и чтения `cache_trace.jsonl` (operator step, unverified).

### 4.4. Часть C: итоги «до/после»

До: общий префикс Worker–Worker one mission 3,813 симв. (1,271 ток.), cross-mission 3,595 (1,198), Reviewer–Reviewer 3,252 (1,084).
После: 4,325 (1,441), 4,325 (1,441), 4,208 (1,402) соответственно; выигрыш 0.7–1.4 s на новую сессию при 220–260 tok/s.## 5. Что включено по умолчанию и что opt-in

**По умолчанию (активно без env/флагов):**
- `slot-save-path = /slots` в пресете `qwen3.8-27b-gsq-rco-iq2-s-mtp` (`docker-entrypoint.sh:680`); bind-том `D:\llama-slots:/slots` (`docker-compose.yml:29`)
- Stable-prefix workflow patch, зашит в `AHAWR_v13_ClaudeCode.json` (3 патченых узла) — без runtime-флага

**Opt-in (нужна явная установка):**
- `AHAWR_STABLE_PREFIX=1` — перенос mid-conversation переменных (total tokens, date, run id, attempt, working directory) в хвост первого user-сообщения
- `AHAWR_CACHE_TRACE=1` — append в `litellm/trace/cache_trace.jsonl` (default off)
- Tool-output caps в `claude_cli.py` (env на роль; default unset)
- Slot save loop (`slot_cache.py loop`) — запускается только если запущен (отдельный процесс или через `queue_watch.py.new`); soft-fail, никогда не останавливает очередь
- Watchdog post-restart slot restore — только если установлен патченный `docker_watchdog.ps1` (шаг 3 Part A §9b)## 6. Действия оператора

1. **Перезапуск llama с новым пресетом.** `docker restart llama-cpp-server` (или обычный apply-скрипт). Пресет теперь несёт `slot-save-path = /slots` (`/d/OpenAIProjects/llama.cpp/docker-entrypoint.sh:680`); bind-том `D:\llama-slots` → `/slots` (`/d/OpenAIProjects/llama.cpp/docker-compose.yml:29`). Перед первым использованием создать `D:\llama-slots` на хосте.
2. **Пересборка и перезапуск runner.** В контейнере `claude-runner`: `pip install -e .` (или rebuild образа) и restart. Это устанавливает env-cap и stable-prefix изменения в `claude_cli.py` / `config.py` / `runs.py`.
3. **Применение `.new` файлов** (Part A §9b, шаги 1–6):
   - watchdog: бэкап, затем `docker_watchdog.ps1.new` → `docker_watchdog.ps1`; запуск как обычно. После каждого перезапуска Docker Desktop опрашивает `http://localhost:8033/health` каждые 10 с до 5 минут, и только тогда выполняет restore.
   - apply-скрипты: бэкап, затем каждый из `apply_cms.new.sh`, `apply_llama_template.new.sh`, `apply_no_diag.new.sh` поверх live-имён; запуск как обычно.
   - queue watcher (опционально): бэкап, `queue_watch.py.new` → `queue_watch.py`, перезапуск watcher — перед каждым перезапуском очереди запускает `slot_cache.py loop --base-url http://localhost:8033 --save-dir D:\llama-slots`; пропускает, если loop уже работает. Либо отдельный процесс (шаг 4 §9b).
4. **Сохранение слота и замер размера.** Запустить промпт известной длины N (например, 32K ток.) на слоте, затем `curl -X POST "http://localhost:8033/slots/0?action=save"` (API key). Записать `n_tokens`, `n_bytes` из ответа и `stat -c %s <file>` в контейнере; сравнить с формулой `18,440·N + 52,985,856 + 44` байт (Part A §9). Свободный диск 187 GiB (2026-10-13) покрывает ~3.55 GiB для трёх файлов с запасом.
5. **Проверка restore на холодном старте — `prompt eval time` в логе llama.**
   a. Перезапустить llama cold — слот пуст.
   b. Выполнить restore: автоматически после каждого перезапуска Docker Desktop при установленном `docker_watchdog.ps1` (опрашивает health и затем restore, см. `docker_watchdog.log`); либо вручную: `python3 data-tables/ops/slot_cache.py restore --base-url http://localhost:8033 --save-dir D:\llama-slots`.
   c. Отправить первый промпт, общий с сохранённым префиксом.
   d. Грeпнуть лог llama по времени prefill: `docker logs llama-cpp-server | grep "prompt eval time"`. Малое время (пропорциональное расходившемуся хвосту, а не полному префиксу) — restore сработал; значение близко к полному cold-prefill (~6–7 мин) — restore не помог (расхождение до восстановленного префикса или ошибка restore: `slot restore failed (ignored): …` в `docker_watchdog.log`).
6. **Проверка `cache_trace` после live-запуска.** Установить `AHAWR_CACHE_TRACE=1` в env LiteLLM, запустить Worker и Reviewer на одной задаче, затем посмотреть `litellm/trace/cache_trace.jsonl`: `first_diff` offset должен находиться в переменной части хвоста (RUN ID / ATTEMPT / DATE / WD-блок), а не в WD-блоке в голове (Part C §7.3).## 7. Откат

- **Slot save/restore (llama.cpp):** восстановить два бэкапа
  ```
  cp /d/OpenAIProjects/llama.cpp/docker-entrypoint.sh.bak-2026-10-13 /d/OpenAIProjects/llama.cpp/docker-entrypoint.sh
  cp /d/OpenAIProjects/llama.cpp/docker-compose.yml.bak-2026-10-13 /d/OpenAIProjects/llama.cpp/docker-compose.yml
  ```
  затем перезапустить llama. Slot-эндпоинты снова отклоняются (нет `--slot-save-path`); сохранённые файлы в `D:\llama-slots` становятся инертными.
- **Watchdog / apply-скрипты / queue watcher (Part A §9b):** восстановить копии `.bak-T010` поверх live-имён (watchdog, три apply-скрипта, `queue_watch.py`), перезапустить процессы, остановить любой процесс `slot_cache.py loop` (Ctrl-C или `Get-Process python | Where-Object CommandLine -match 'slot_cache.py' | Stop-Process -Force`). `real_queue.sh`, `queue_watch.py`, `docker_watchdog.ps1` никогда не модифицировались на месте; `slot_cache.py` неизменён.
- **Stable-prefix workflow patch:** до-патч-файл для Claude Code workflow: `AHAWR_v13_ClaudeCode.json.bak` (110 узлов, без маркеров `stable-prefix`; сверено с патченным — 3 маркера). Патч-скрипт содержит revert: `python3 data-tables/ops/stable_prefix_patch.py --revert AHAWR_v13_ClaudeCode.json.bak` (без git checkout). Основной workflow `AHAWR_v13.json` не трогается `stable_prefix_patch.py` (скрипт патчит только `AHAWR_v13_ClaudeCode.json`); изменения в working tree — только control-flag patch, до-патч-источник = HEAD (`git show HEAD:AHAWR_v13.json`, commit 3508408, сверено без маркеров). Патч-скрипты идемпотентны: повторный запуск на патченном файте выводит "already patched, skipping" / "already applied", файл byte-идентичен.
- **Runner env caps / thinking switch:** сбросить соответствующие env-переменные в контейнере `claude-runner` и перезапустить. Для caps кодовый rollback не нужен (opt-in env); default thinking switch off.
- **Cache trace:** установить `AHAWR_CACHE_TRACE=0` (или unset) в env LiteLLM и перезапустить; hook перестаёт append-ить. Существующие строки `cache_trace.jsonl` остаются.
- **agent_prompts.csv:** правка не применялась (Part C §6), откатывать нечего.## 8. Список изменённых файлов (T001–T015, по `git status --short`)

**Modified (tracked):**
- `AHAWR_v13.json` — main workflow; control flags + stable-prefix patch
- `AHAWR_v13_ClaudeCode.json` — Claude Code workflow; те же патчи
- `AHAWR_Status_Bot.json` — status bot workflow (control commands)
- `claude-runner/src/claude_runner/{claude_cli.py, config.py, runs.py}` — env caps, thinking switch, stable prefix
- `claude-runner/tests/{conftest.py, fake_claude.py, test_config_cli.py, test_context_probe.py, test_runs.py}` — test updates
- `litellm/ahawr_hooks.py` — hooks (incl. `AHAWR_CACHE_TRACE`, stable prefix, think-leak guard)
- `litellm/runtime/local_thinking` — runtime thinking config
- `litellm/test_ahawr_hooks.py` — hook tests
- `retrieval-service/{Dockerfile, pyproject.toml, src/ahawr_retrieval/{config.py, service.py}, tests/{conftest.py, test_retrieval.py}}` — PG store wiring, parity check
- `scripts/{patch_main_control_flags.py, patch_status_bot_control.py}` — patch scripts
- `docker-compose.yml`, `.env.example`, `.gitignore`, `docs/mission-report-T013-ru.md` — infra/docs updates

**Untracked new files:**
- `docs/cold-prefill.md` и `docs/cold-prefill/` (Part A/B/C, snapshot)
- `litellm/runtime/local_thinking.auto`
- `retrieval-service/scripts/`, `retrieval-service/src/ahawr_retrieval/pg_store.py`, `retrieval-service/tests/parity_check.py`
- `AHAWR_v13_ClaudeCode.json.bak`

`data-tables/ops/*.new*` (apply-скрипты, watchdog, queue watcher) находятся вне git и были скопированы, но не закоммичены.

**Примечание про `.new`-файлы:** файлы `docker_watchdog.ps1.new`, `apply_cms.new.sh`, `apply_llama_template.new.sh`, `apply_no_diag.new.sh`, `queue_watch.py.new` **не применены** к live-копиям в этой сессии; live-оригиналы неизменны (git diff пуст).## 9. Проведённые проверки и их результаты

| проверка | команда | результат |
|---|---|---|
| slot_cache tests | `python3 data-tables/ops/test_slot_cache.py` | 31 passed, OK |
| claude-runner (runner + search_hook) | `PYTHONPATH=/d/n8n/claude-runner/src /opt/claude-runner/venv/bin/python -m pytest claude-runner/tests/ --no-cov` | **143 passed** (документ §16) |
| litellm hooks | `python3 -m pytest litellm/test_ahawr_hooks.py -q` | 19 passed |
| retrieval service | `cd retrieval-service && PYTHONPATH=src python3 -m pytest tests/test_retrieval.py -q --no-cov` | 57 passed |
| js syntax (workflow jsCode) | `node data-tables/ops/js_syntax_check.js AHAWR_Status_Bot.json AHAWR_v13.json AHAWR_v13_ClaudeCode.json` | 15/45/48 checked, 0 bad |
| workflow structure | `python3 scripts/check_workflows.py` | 3 files OK, no token/key |
| patch idempotency | `cp AHAWR_v13.json /tmp/t016_v13.json && python3 scripts/patch_main_control_flags.py /tmp/t016_v13.json && cmp AHAWR_v13.json /tmp/t016_v13.json` (аналогично для ClaudeCode) | "already patched, skipping"; файл byte-идентичен |
| bash syntax | `bash -n data-tables/ops/{apply_cms.new.sh, apply_llama_template.new.sh, apply_no_diag.new.sh, real_queue.sh}` | все exit 0 |
| CSV validity | `python3 -c "import csv; ..."` на `data-tables/agent_prompts.csv` | 4 rows × 5 cols parsed |
| JSON validity | `python3 -m json.tool` на 3 workflow-файла; построчный `json.loads` на `litellm/trace/cache_trace.jsonl` | все OK (2,958 строк) |
| compose config | `docker compose config --quiet` | **не выполнялась** — нет docker CLI в этой WSL-среде; YAML-парс `docker-compose.yml` пройден как замена (services: ahawr-dashboard, ahawr-postgres, ahawr-retrieval, claude-runner, litellm, n8n) |
| forbidden files | `git status --short .env data/ data-tables/ops/real_queue.sh data-tables/ops/queue_watch.py data-tables/ops/docker_watchdog.ps1` | пусто (неизменны) |
| no git mutations | `git log --oneline -5` | нет новых коммитов; HEAD по-прежнему `3508408`; stash/checkout/reset не выполнялись |

Примечания:
- claude-runner suite должен запускаться интерпретатором venv (`/opt/claude-runner/venv/bin/python`) с `PYTHONPATH=/d/n8n/claude-runner/src`: системный `python3` (3.12.15) имеет устаревшую non-editable установку `claude_runner`, которая заслоняет working tree и ломает `test_search_hook.py` (запускает `sys.executable -m claude_runner.search_hook`). С правильным интерпретатором все 143 теста проходят, включая exit-code тест search-hook.
- `docker compose config --quiet` — единственная проверка, которую нельзя выполнить здесь (нет docker CLI). YAML-парс покрывает валидацию compose-файла в этом репозитории; compose-файл llama.cpp валидирован diff'ом против `.bak` (добавлены только две строки слота).
- Отдельные замерные прогоны (не в документе §16): `151 passed` (позднее повторное измерение claude-runner suite) и `3,072 строки` (поздний пересчёт JSON-валидации) — это **поздние повторные замеры**, не совпадающие с цифрами документа.## 10. Что осталось открытым или не подтверждено

1. **Размер файла слота (qwen3.8-27b-gsq-rco-iq2-s-mtp)** — формула `18,440·T + 52,985,856 + 44` байт рассчитана, но **не подтверждена** реальным сохранением: нужен оператор (шаг 4 раздела 6) — запустить промпт известной длины, сохранить слот, сравнить `n_bytes` с формулой.
2. **End-to-end restore на гибридной модели (KV+GDN)** — unit-тест использует небольшую attention-only модель; нужна проверка, что гибридный слот восстанавливается и продолжается с `timings.prompt_n`, равным длине расходившегося хвоста.
3. **Checkpoint-free divergence** — подтвердить, что после restore промпт, расходившийся раньше восстановленного префикса, действительно пересчитывается с токена 0 (`tools/server/server-context.cpp:3263-3268`); ожидается `prompt_n` = полная длина нового промпта.
4. **Restore failure mode с полным KV-кэшем** — «No available space in KV cache» должен воспроизводиться сохранением промпта рядом с `n_ctx` и восстановлением в слот с меньшим остатком; проверить, что слот остаётся пустым и ошибка `ERROR_TYPE_INVALID_REQUEST`.
5. **Device-side save path** (`LLAMA_STATE_SEQ_FLAGS_ON_DEVICE`) не используется slot-эндпоинтами (flags 0); в multi-GPU конфигурации проверить, что host-side копия не доминирует во времени save.
6. **Live confirmation stable-prefix** — запустить патченный workflow, включить `AHAWR_CACHE_TRACE=1`, проверить, что `first_diff` offset в `cache_trace.jsonl` находится в переменной части хвоста, а не в WD-блоке в голове (Part C §7.3).
7. **Re-Read поведение при превышении лимитов** — события runner фиксируют только сохранённый + обрезанный текст, не пишут ли Claude Code хвост в файл; для Read и Anthropic-ролей это unverified.

Ни один из пунктов не был закрыт в ходе T017 (задача — только отчёт, без изменений файлов).
## 11. Итоговый вывод

Общая экономия по частям A, B и C:

- **Часть A**: slot-save/restore позволяет восстановить KV + рекуррентное состояние после перезапуска llama. Формула размера файла `18,440·T + 52,985,856 + 44` байт (не подтверждена реальным сохранением; операторский шаг 4 в разделе 6).
- **Часть B**: опциональные лимиты (Read 48,000 симв. / 16k ток., Bash 12,000 симв.) плюс промпт-правила дают экономию **688 097 симв. = 1,218 % tool_result** (≈3.5 % полного окна 65 536 ток.).
- **Часть C**: stable-prefix patch переносит переменные данные в хвост; выигрыш по общим префиксам: Worker–Worker one mission +170 ток. (0.7–0.8 с), cross-mission +243 ток. (0.9–1.1 с), Reviewer–Reviewer +318 ток. (1.2–1.4 с) при пропуске 220–260 tok/s.

Все изменения T001–T015 перечислены в разделе 8; проверки и результаты — в разделе 9; что осталось открытым — в разделе 10. В ходе T017 файлов не изменялось.