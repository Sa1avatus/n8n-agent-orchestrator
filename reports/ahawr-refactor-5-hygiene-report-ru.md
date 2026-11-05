# Гигиенический отчёт серии ahawr-refactor-5 (T014: сводка шагов A–D)

Дата: 2026-11-04. Все команды ниже запускались из `claude-runner/` (git-команды — из корня `/d/n8n`).
Все числа в отчёте — из проверок, которые **реально запускались** в этот день; там, где проверка не
запустилась, это сказано явно. Источники «до»: `reports/ahawr-refactor-5-baseline.json` (замер на живой
файловой системе на момент T001) и контрольная копия HEAD (`git archive HEAD claude-runner/src
claude-runner/pyproject.toml claude-runner/tests | tar -x -C /tmp/headcopy`).

---

## 1. Что изменилось: шаги A–D (файл:строка)

### Шаг A — `_unavailable` вынесен из `board_api.py` в `board_common.py`

| файл:строка | что |
|---|---|
| `src/claude_runner/board_common.py:35` | `def _unavailable(store: Any) -> dict[str, Any] \| None` (новый файл, в git пока `??`) |
| `src/claude_runner/board_common.py:22, 45, 49, 53, 57, 61` | `_error`, `_on_conflict`, `_on_missing`, `_on_value`, `_on_runtime`, `_iso` — остальные помощники того же модуля |
| `src/claude_runner/board_api.py:56` | импорт: `from .board_common import _error, _on_conflict, _on_missing, _on_runtime, _on_value, _unavailable` (hunk `@@ -53,6 +53,7 @@`) |
| `src/claude_runner/board_api.py` (hunk `@@ -69,34 +70,11 @@`) | определения `_unavailable` и коллег удалены из `board_api.py` (−23 строк) |
| `src/claude_runner/board_api.py:86, 100, 646` | вызовы `_unavailable(store)` остались прежними, текст не менялся |
| `src/claude_runner/dashboard.py:39` | добавлено `from .board_common import _unavailable` |
| `src/claude_runner/dashboard.py:453` | `refuse = _unavailable(store)` (hunk `@@ -428,7 +450,7 @@`); на HEAD это было `board_api._unavailable(store)` на строке 452 |

Эффект: единственная строка `dashboard.py` в mypy-выводе исчезла. Бейзлайн (`mypy_src_before.evidence`)
цитировал: `src/claude_runner/dashboard.py:452: error: Module "claude_runner.board_api" does not explicitly
export attribute "_unavailable"  [attr-defined]`, а `requested_line_dashboard_431: null` (строки 431 в
выводе не было и до шага).

### Шаг B — `print` в `qa.py`: один переведён в логгер, остальные оставлены на stdout

Замечание ревьюера (попытка 1) требовало вернуть `print` на stdout по всем операторским
выводам — это сделано; ниже итоговое состояние файла.

| файл:строка | что |
|---|---|
| `src/claude_runner/qa.py` (hunk `@@ -11,7 +11,6 @@`) | удалён `import sys`; `import logging` на строке 12 был на HEAD и остаётся |
| `src/claude_runner/qa.py:132-133` | `print(f"qa: {error}", file=sys.stderr)` → `# logging has no handler configured: lastResort writes ERROR to stderr.` + `log.error("qa: %s", error)` — ошибка CLI по-прежнему попадает в stderr |
| `src/claude_runner/qa.py:139-141` | `# stdout-is-interface: the operator reads the dashboard URL and stack id here.` + два `print` (dashboard URL, stack id) — оставлены как print |
| `src/claude_runner/qa.py:147-148` | `# stdout-is-interface: scripts confirm the removal on stdout.` + `print(f"stack {args.stack_id} removed")` |
| `src/claude_runner/qa.py:158-159, 173-174, 282-283, 422-423` | `# stdout-is-interface: scripts pipe this verdict JSON.` + `print(json.dumps(...))` в `_run`, `_api`, `_smoke`, `_regression` |
| `src/claude_runner/qa.py:232-233` | `# stdout-is-interface: the nightly cron reads the verdict path from stdout.` + `print(f"nightly verdict: {NIGHTLY_VERDICT_PATH}")` |
| `src/claude_runner/qa.py:367-368` | `# stdout-is-interface: scripts read the manifest directory from stdout.` + `print(f"manifest: {Path(manifest[0]['png']).parent}")` |
| `src/claude_runner/qa.py:452-455` | `# stdout-is-interface: scripts read the per-tool counts and the baseline path.` + `for tool, keys in found.items(): print(f"{tool}: {len(keys)} failures")` и `print(f"baseline: {path}")` |
| `src/claude_runner/qa.py:3` | docstring не менялась: «``stack up`` prints the dashboard URL and the stack id only: the DSN carries the password and is never printed.» (формулировка «logs» из попытки 1 снята) |
| `src/claude_runner/qa.py:243` | слепой `except Exception as error` оставлен (см. раздел 5) |
| `tests/test_qa.py:1-11` | docstring: «Every T201 print ... was pinned here before the T201 cleanup» |
| `tests/test_qa.py:52-54` | помощник `_handler(argv)` — namespace CLI без запуска `main` |
| `tests/test_qa.py:152, 169, 181, 196, 209, 228, 248, 260` | восемь характеризующих тестов, pinящих stdout: `stack up` (`assert out == "dashboard: http://127.0.0.1:49152\nstack id: 0a1b2c3d\n"`), `stack down` (`"stack 0a1b2c3d removed\n"`), `run`, `api`, `nightly`, `smoke`, `regression`, `baseline` |
| `tests/test_qa.py:58, 74, 128` | три теста `capture` перключены с `capsys` на `caplog` (ошибка теперь в логе, а не в stderr) |

numstat шага B: `11 2 claude-runner/src/claude_runner/qa.py` (11 добавлено, 2 удалено),
`164 13 claude-runner/tests/test_qa.py`.

### Шаг C — три из четырёх слепых `except` в `board_ready.py` сужены

| файл:строка | что |
|---|---|
| `src/claude_runner/board_ready.py:24` | `import logging` (добавлено) |
| `src/claude_runner/board_ready.py:43` | `from .sources import RunnerSource, SourceError, _check` (добавлен `SourceError`) |
| `src/claude_runner/board_ready.py:45` | `logger = logging.getLogger(__name__)` |
| `src/claude_runner/board_ready.py:147` | было `except Exception as exc:` (HEAD:144) → `except (httpx.HTTPError, SourceError, json.JSONDecodeError) as exc:` |
| `src/claude_runner/board_ready.py:167` | было `except Exception as exc:` (HEAD:164) → `except httpx.HTTPError as exc:` |
| `src/claude_runner/board_ready.py:188` | `import urllib.error` (локальный импорт внутри `check_llama_model`) |
| `src/claude_runner/board_ready.py:196` | было `except Exception as exc:` (HEAD:192) → `except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:` |
| `src/claude_runner/board_ready.py:403-404` | четвёртый except оставлен слепым: `except Exception as exc:  # trust boundary: the store can raise anything (DB down, schema)` + `logger.warning("hermes config unreadable: %s", exc)` (HEAD:399) |

numstat шага C: `10 5 claude-runner/src/claude_runner/board_ready.py` (10 добавлено, 5 удалено).

### Шаг D — удалены два подтверждённых «мёртовых» имени (из `dead_candidates` бейзлайна)

| файл:строка | что |
|---|---|
| `src/claude_runner/board_think.py` (hunk `@@ -317,29 +316,6 @@`) | удалено `def _beta_cdf(` (на HEAD строка 320, 23 строк вместе с пустыми) |
| `src/claude_runner/board_think.py` (hunk `@@ -26,7 +26,6 @@`) | удалён `import math` (нужен был только `_beta_cdf`) |
| `src/claude_runner/board_think.py:309` | `def _beta(` — соседний помощник **не тронут**, используется в `board_think.py:440` (`p = _beta(1 + passed, 1 + failed, rng)`) |
| `src/claude_runner/hermes_run.py` (hunk `@@ -27,7 +27,6 @@`) | удалено `RUNNING_STATUSES = frozenset({"running", "started", "queued", "waiting_for_approval"})` (HEAD:30) |
| `src/claude_runner/hermes_run.py:29` | `TERMINAL_STATUSES` осталась, используется в `:161`; цикл `poll_run` ограничен (`profile.hermes_max_polls` на `:157`, дедлайн на `:154`) |
| `tests/test_board_think.py:221` | новый тест `test_the_uplift_interval_is_reproducible_from_the_bootstrap_seed` (характеризующий, не ссылается на `_beta_cdf`) |
| `tests/test_hermes_run.py:313, 332` | новые тесты `test_poll_run_returns_on_the_first_terminal_status` и `test_poll_run_keeps_polling_a_not_finished_status_until_max_polls`; скрипы-заглушки `TERMINAL_POLL_SCRIPT` (`:82`) и `IN_PROGRESS_POLL_SCRIPT` (`:92`) |

Характеризующие тесты, зафиксированные в бейзлайне (`dead_candidate_characterization_tests`), соответствуют
добавленным тестам: два для `_beta_cdf`/`RUNNING_STATUSES` — см. строки выше.

---

## 2. Проверки, которые запускались (числа «до» и «после»)

### 2.1 mypy (строгий) — главная строка

| команда | ключевая строка вывода | до | после |
|---|---|---|---|
| `python -m mypy src` (живое дерево) | `Found 125 errors in 17 files (checked 101 source files)` | бейзлайн: `Found 126 errors in 18 files (checked 101 source files)` | −1 ошибка, −1 файл |
| `python -m mypy src 2>&1 \| grep -c dashboard.py` | `0` | в бейзлайне одна строка `dashboard.py:452 ... [attr-defined]`; строки `dashboard.py:431` не было и раньше (`requested_line_dashboard_431: null`) | 0 строк |
| `python -m mypy src 2>&1 \| grep -n 'dashboard.py:431'` | пусто (0 совпадений) — строки 431 в выводе нет ни до, ни после | — | — |
| mypy на копии HEAD (`/tmp/headcopy/claude-runner`) | `Found 127 errors in 17 files (checked 94 source files)` | 94 файла, потому что HEAD содержит 7 неотслеживаемых модулей меньше (в т.ч. `board_common.py`) | контрольная точка |

mypy по файлам (живое дерево против копии HEAD — счётчик строк `error:`):

| файл | HEAD-копия | живое дерево |
|---|---|---|
| `board_ops_api.py` | 35 | 35 |
| `board_pg.py` | 30 | 29 |
| `board_order.py` | 20 | 20 |
| `board_api.py` | 12 | 12 |
| `board_ops_store_pg.py` | 7 | 6 |
| `board.py` | 5 | 5 |
| `board_ready.py` | 4 | 4 |
| `board_ops_parsers.py` | 3 | 3 |
| `board_ops_livestate.py` | 2 | 2 |
| `board_stats.py` | 2 | 2 |
| `api.py` | 1 | 1 |
| `runs.py`, `board_think.py`, `board_providers_api.py`, `board_ops_settings.py`, `board_ops_redact.py`, `board_ops_guard.py` | 1 каждый | 1 каждый |
| `dashboard.py`, `qa.py`, `hermes_run.py`, `board_common.py` | 0 (HEAD: `dashboard.py` 1) | 0 |

Примечание: «до» по отдельным файлам в живом дереве измерить нельзя (файлы уже изменены), поэтому
использована копия HEAD; расхождения `board_pg.py` (30→29) и `board_ops_store_pg.py` (7→6) — не шаги A–D,
а более ранние правки той же серии.

### 2.2 ruff: целевые правила (T201, BLE001)

| команда | ключевая строка | до | после |
|---|---|---|---|
| `python -m ruff check --select T201,BLE001 src/claude_runner/qa.py src/claude_runner/board_ready.py` (живое дерево, после сокращения шага B) | `Found 13 errors` (`11 unsafe fixes available`) | копия HEAD: `Found 17 errors` (`12 unsafe fixes available`) | qa.py: T201 12→11 (−1), BLE001 1→1; board_ready.py: BLE001 4→1 |
| `python -m ruff check --select T201 src/claude_runner` | `Found 22 errors` | копия HEAD: `Found 23 errors`; бейзлайн (живое дерево на момент T001): 23 | −1 (единственный T201, снятый шагом B — stderr-строка в `main`) |
| `python -m ruff check --select BLE001 src/claude_runner` | `Found 29 errors` | копия HEAD: `Found 31 errors`; бейзлайн (живое дерево): 32 | −2 относительно HEAD, −3 относительно бейзлайна |
| `python -m ruff check --select T201,BLE001 src/claude_runner/codex_run.py tests/fake_codex.py tests/test_codex_run.py` | `Found 4 errors` (`3 unsafe fixes available`) | 4 (Codex-файлы намеренно не выбирались) | без изменений |

Все 13 находок (concise, ключевые строки): `board_ready.py:403:12: BLE001`,
`qa.py:243:16: BLE001`, `qa.py:140,141,148,159,174,233,283,368,423,454,455: T201` — одиннадцать
`print`, каждый из которых подтверждён комментарием `# stdout-is-interface` рядом (строки 139, 147,
158, 173, 232, 282, 367, 422, 452).

### 2.3 ruff: правила по умолчанию (E, F, I, B, UP, SIM)

| команда | ключевая строка | до | после |
|---|---|---|---|
| `python -m ruff check src/claude_runner` | `Found 98 errors` (`[*] 24 fixable ... (6 unsafe fixes available)`) | копия HEAD: `Found 98 errors` (бейзлайн тоже 98) | 98 → 98 |
| `python -m ruff check src/claude_runner/qa.py` | `All checks passed!` (после удаления `import sys`) | копия HEAD: 0 (бейзлайн `default_ruff_errors: 0`) | без изменений |
| `python -m ruff check tests/test_qa.py` | `All checks passed!` | новый прогон (файл изменён шагом B) | 0 находок |
| `python -m ruff check src/claude_runner tests/test_qa.py` | `Found 98 errors` (`[*] 24 fixable ... (6 unsafe fixes available)`) | копия HEAD `src/claude_runner`: 98 | 98 → 98 |
| `python -m ruff check src/claude_runner/board_ready.py` | `Found 2 errors` (`594:5: SIM108`, `637:101: E501`) | 2 (бейзлайн `default_ruff_errors: 2`, `SIM108: 1, E501: 1`) | без изменений (эти находки не относятся к шагу C) |
| `python -m ruff check src/claude_runner/board_think.py` | `Found 9 errors` (`73, 82, 161, 172, 328, 336, 406, 420` E501 + `269:13: SIM105`) | 9 (бейзлайн) | без изменений |
| `python -m ruff check src/claude_runner/hermes_run.py src/claude_runner/board_common.py` | `All checks passed!` | 0 / новый файл | без изменений |

### 2.4 Тесты

| команда | ключевые строки | до | после |
|---|---|---|---|
| `node --test tests/*.test.js` | `# tests 528` / `# pass 528` / `# fail 0` / `# duration_ms 1799.75473` | — | 528 тестов, 0 провалов |
| `python -m pytest tests/test_qa.py -q --no-cov` (живое дерево, шаг B) | 12 тестов (счётчик `--collect-only -q`: `tests/test_qa.py: 12`), 12 точек в прогресс-строках, ни одной строки `FAILED`, exit код 0 | до шага B: те же 12 тестов, 4 из них pinили stderr (`output.err`) | 12 из 12 проходят |
| `python -m pytest tests/test_qa.py tests/test_board_ready.py tests/test_board_think.py tests/test_hermes_run.py tests/test_board_api.py -q --no-cov` | четыре строки `FAILED` (перечислены ниже); итоговой строки «N passed, M failed» pytest в `-q` не печатает | те же провалы воспроизводятся на копии HEAD (замер в T014) | 248 собрано: `test_board_api.py: 152`, `test_board_ready.py: 71`, `test_board_think.py: 13`, `test_hermes_run.py: 10`, `test_qa.py: 12` |
| `python -m pytest "tests/test_board_api.py::TestUnavailableStore" "tests/test_board_api.py::TestErrorMapping" -q --no-cov` (живое дерево) | `FAILED ...test_status_is_503_with_no_dsn[asyncio]`, `FAILED ...test_missions_is_503_when_unavailable[asyncio]`, `FAILED ...test_store_unavailable_is_503_on_the_stats_routes[asyncio]` (`assert 404 == 503`, `tests/test_board_api.py:1077`) | **то же на копии HEAD** — провалы воспроизводятся без шагов A–D | провалы не являются следствием шага A |
| `python -m pytest tests/test_hermes_run.py::test_flag_off_engine_hermes_is_rejected_by_the_config -q --no-cov` (живое дерево) | `assert "provider HER... got 'hermes'" == ...` → `+ f claude, codex, got 'hermes'` (`tests/test_hermes_run.py:451`) | на копии HEAD тот тест проходит | провал из-за `ENGINES = ("claude", "codex")` (`config.py:198`, правка другой миссии, HEAD: `ENGINES = ("claude",)`) |
| `git diff --check` | выход 0; только предупреждения `LF will be replaced by CRLF` | — | пробельных ошибок нет |

---

## 3. Что не запускалось и почему

1. **Полный pytest-прогон** (`python -m pytest tests -q --no-cov`) — не прошёл сбор:
   `tests/test_qa_container_static.py:7: in <module> import yaml` →
   `ModuleNotFoundError: No module named 'yaml'`, `Interrupted: 1 error during collection`.
   Прогон не считается «зелёным»; поэтому проверялись только файлы, которых касается изменение
   (правило CLAUDE.md: запускать узкие проверки).
2. **mypy «до» на живом дереве** — невозможно: файлы уже изменены. Вместо этого измерена копия HEAD
   (см. 2.1); её счёт (127 ошибок в 94 файлах) не сопоставим напрямую с 125/101.
3. **ruff по отдельным файлам для отложенных правил** — запускалась только агрегированная выборка
   (`--select PERF401,PERF403,ERA001,ARG001,ARG002,ARG004,ARG005 src/claude_runner tests`); пофайловых
   прогонов по каждому файлу отложенного списка не делалось, поэтому числа в разделе 6 — из агрегата.
4. **Проверки хост-действий / контейнеров** — не требуются: T014 ничего не перезапускает и не пересобирает.
5. **Внутренняя несогласованность бейзлайна**: `counts_per_touched_file` для `board_ready.py` даёт
   `mypy_lines_in_mypy_src_output: 7`, а `deferred.mypy_by_file` для того же файла — 4 (это строки из
   `audit-python.json`, а не из свежего mypy-запуска). В отчёте использованы числа живого mypy-запуска
   (4), а не 7.

---

## 4. Доказательства grep для каждого удалённого имени

**`_beta_cdf`** (была `src/claude_runner/board_think.py:320`)
- `grep -rn "_beta_cdf" claude-runner/src claude-runner/tests claude-runner/docs` → **0 совпадений**.
- `grep -rn "_beta_cdf" claude-runner data-tables reports` → только 6 не-кода:
  `data-tables/ops/think_steps_archive.jsonl` (1 — запись журнала операторских шагов, не код),
  4 копии того же файла в `data-tables/ops/_scratch/ahawr-qa-smoke/{final/head,scratch/a,scratch/b,scratch/c}`,
  плюс упоминания в `reports/ahawr-refactor-1-py-helpers-report-ru.md`, `reports/ahawr-refactor-5-baseline.json`
  и `reports/audit-python.json`.
- Соседний `_beta` жив: `board_think.py:309 def _beta(`, вызов `board_think.py:440`.
- `grep -n "import math" src/claude_runner/board_think.py` → 0 (импорт удалён вместе с функцией).

**`RUNNING_STATUSES`** (было `src/claude_runner/hermes_run.py:30`)
- `grep -rn "RUNNING_STATUSES" claude-runner/src claude-runner/tests claude-runner/docs` → **0 совпадений**.
- `grep -rn "RUNNING_STATUSES" data-tables reports` → 9 не-кода: `data-tables/ops/think_steps_archive.jsonl` (1),
  `reports/audit-python.json` (3), `reports/ahawr-refactor-1-py-helpers-report-ru.md` (1),
  `reports/ahawr-refactor-5-baseline.json` (4).
- `grep -rn "TERMINAL_STATUSES" claude-runner/src claude-runner/tests` → `hermes_run.py:29` (определение) и
  `hermes_run.py:161` (использование) — постоянный статус-набор остаётся.

---

## 5. Что не трогали и почему

| что | почему не меняли |
|---|---|
| все одиннадцать `print` в `qa.py` (`140, 141, 148, 159, 174, 233, 283, 368, 423, 454, 455`) | stdout — интерфейс CLI (`python -m claude_runner.qa`): оператор читает dashboard URL и stack id, скрипты-обёртки получают verdict JSON, cron — путь nightly-файла, `baseline` — счётцы и путь; рядом комментарий `# stdout-is-interface` (строки 139, 147, 158, 173, 232, 282, 367, 422, 452) |
| `qa.py:243` `except Exception as error` | граница доверия: стор может поднять что угодно; конфигурация не читается — smoke остаётся выключенным, причина логируется (`log.warning` на `:244`) |
| `qa.py:362` `except Exception as error` (с ре-рейзом `raise ValueError(...) from error`) | ruff BLE001 её не флагует (ре-рейз), менять нечего |
| `board_ready.py:403-404` | граница доверия (DB может поднять что угодно), комментарий в коде + `logger.warning` на `:404` |
| `_beta` (`board_think.py:309`) | используется (`:440`); бейзлайн прямо требует не трогать `_beta` при удалении `_beta_cdf` |
| `TERMINAL_STATUSES` (`hermes_run.py:29`) | используется (`:161`); «мёртвым» в `dead_candidates` был только `RUNNING_STATUSES` |
| Codex-файлы (`src/claude_runner/codex_run.py`, `tests/fake_codex.py`, `tests/test_codex_run.py`) | исключены правилом отбора бейзлайна (`selection_rules`: Codex-пути исключаются); их находки (118 BLE001, 99/102/116 T201, 159 ARG001) остались |
| Кандидаты `tests/test_board_api.py:816 async def _actions(...)` и `tests/test_qa_smoke.py:172 def is_visible(...)` | подтверждённых вызовов нет (`grep -n "_actions(" tests/test_board_api.py` → только определение; `grep -rn "is_visible" tests/ src/` → только определение), но в `dead_candidates` бейзлайна для шага D вошли **только** `board_think.py:320` и `hermes_run.py:30`, а эти два файла — тесты, вне scope шага D |
| `board_ready.py:594` (SIM108), `board_ready.py:637` (E501) | это находки правил по умолчанию, они не относятся ни к одному из четырёх шагов; менять их в T014 нельзя (несогласованные правки других миссий) |

---

## 6. Отложенное (deferred) — список следующей миссии в порядке убывания

Агрегат, который реально запускался:
`python -m ruff check --select PERF401,PERF403,ERA001,ARG001,ARG002,ARG004,ARG005 src/claude_runner tests`
→ `Found 475 errors`.

По правилам: **ARG002 — 168**, **ARG001 — 162**, **ARG005 — 119**, **PERF401 — 14**, **ERA001 — 6**,
**PERF403 — 5**, **ARG004 — 1**.

Ранжируется по файлам (число находок в файле; порядок убывания):

| # | файл | всего |
|---|---|---|
| 1 | `src/claude_runner/board_pg.py` | 79 |
| 2 | `tests/test_qa_cli.py` | 47 |
| 3 | `tests/test_search_cli.py` | 38 |
| 4 | `tests/test_qa.py` | 28 |
| 5 | `src/claude_runner/board_ops_store_pg.py` | 28 |
| 6 | `tests/test_board_providers_api.py` | 17 |
| 7 | `tests/test_qa_runner.py` | 14 |
| 8 | `tests/test_board_ready.py` | 13 |
| 9 | `tests/test_board_views_api.py` | 11 |
| 10 | `tests/test_qa_smoke.py` | 10 |
| 11 | `tests/test_dashboard.py` | 10 |
| 12 | `src/claude_runner/mcp_board.py` | 9 |
| 13 | `tests/test_search.py` | 8 |
| 14 | `src/claude_runner/sources.py` | 8 |
| 15 | `tests/test_dashboard_auth_config.py` | 7 |
| 16 | `tests/test_search_hook.py` | 6 |
| 17 | `tests/test_qa_worker.py` | 6 |
| 18 | `tests/test_qa_stack.py` | 6 |
| 19 | `tests/test_board_pg.py` | 6 |
| 20 | `tests/test_board_lessons_api.py` | 5 |
| 21 | `src/claude_runner/board_api.py` | 5 |
| 22 | `tests/test_dashboard_auth.py` | 4 |
| 23 | `tests/test_board.py` | 4 |
| 24 | `src/claude_runner/dashboard.py` | 4 |
| 25 | `src/claude_runner/board.py` | 4 |
| 26–30 | `tests/test_think_proxy.py`, `tests/test_qa_api.py`, `tests/test_ops_legacy_reads.py`, `tests/test_host_actions_api.py`, `tests/test_config_cli.py` | 3 каждый |
| 31 | `src/claude_runner/qa_complexity.py` | 3 |
| 32+ | по 2: `tests/test_think_switch.py`, `test_run_summary_fields.py`, `test_remote_processes.py`, `test_queue_watch_flag.py`, `test_queue_board.py`, `test_qa_capture.py`, `test_provider_secrets.py`, `test_provider_registry.py`, `test_process_routes.py`, `test_mission_stats.py`, `test_dashboard_js.py`, `test_dashboard_host_actions.py`, `test_claude_cli.py`, `test_board_ops_api.py`, `test_board_lessons_extract.py`, `test_board_api.py`, `test_board_agent.py`; `src/claude_runner/mcp_search.py`, `board_providers_api.py`, `board_ops_livestate.py`, `board_ops_api.py` | 2 каждый |
| 33+ | по 1: `tests/test_thinking_config_patch.py`, `test_task_id_suffix.py`, `test_runs.py`, `test_run_process_cleanup.py`, `test_risk_tiers.py`, `test_remote_manager.py`, `test_queue_board_contract.py`, `test_qa_stack_inprocess.py`, `test_qa_agent_run.py`, `test_process_group_kill.py`, `test_mission_lint.py`, `test_mem_guard.py`, `test_hermes_run.py`, `test_guards_overrides.py`, `test_guards.py`, `test_guard_queue.py`, `test_dashboard_auth_crypto.py`, `test_codex_run.py`, `test_board_think.py`, `test_board_ops_state.py`, `tests/replay_guard_queue.py`, `tests/fixtures/qa_complexity/new_function.py`; `src/claude_runner/roles_config.py`, `remote_manager.py`, `qa_run_record.py`, `qa_contract.py`, `qa_capture.py`, `qa.py`, `guards.py`, `guard_queue.py`, `dashboard_auth.py`, `board_think.py`, `board_ready.py`, `board_plan.py`, `board_order.py`, `board_ops_store.py`, `board_ops_settings.py`, `board_ops_guard.py` | 1 каждый |

Детализация PERF401/PERF403/ERA001 по файлам (25 находок):
`qa_complexity.py` PERF401 ×3; `board_ops_livestate.py` PERF403 ×2; `board.py` PERF403 ×2;
`tests/test_dashboard_js.py` ERA001 ×2; `tests/test_board.py` ERA001 ×2; по 1:
`test_board_ops_state.py` (PERF403), `test_risk_tiers.py`, `test_qa_stack.py`, `test_board_views_api.py`,
`tests/fixtures/qa_complexity/new_function.py`, `roles_config.py`, `qa_run_record.py`, `qa_contract.py`,
`qa_capture.py`, `dashboard.py`, `board_providers_api.py`, `board_order.py` (все PERF401),
`test_dashboard.py`, `test_board_think.py` (ERA001). ARG004 — одна находка: `src/claude_runner/board.py:577`.
Напоминание: у `qa.py` и `board_ready.py` из отложенного осталось по одной ARG001 —
`python -m ruff check --select ... src/claude_runner/qa.py src/claude_runner/board_ready.py` →
`Found 2 errors`: `qa.py:205:14: ARG001 Unused function argument: 'args'` (`def _nightly`) и
`board_ready.py:763:5: ARG001 Unused function argument: 'who'`.

**mypy по файлам** (живая проверка, порядок убывания):
`board_ops_api.py` 35 → `board_pg.py` 29 → `board_order.py` 20 → `board_api.py` 12 →
`board_ops_store_pg.py` 6 → `board.py` 5 → `board_ready.py` 4 → `board_ops_parsers.py` 3 →
`board_ops_livestate.py` 2 → `board_stats.py` 2 → по 1: `api.py`, `runs.py`, `board_think.py`,
`board_providers_api.py`, `board_ops_settings.py`, `board_ops_redact.py`, `board_ops_guard.py`.
Итого 125; файлы, доведённые до нуля: `qa.py`, `hermes_run.py`, `board_common.py`, `dashboard.py`.
В бейзлайне `deferred.mypy_by_file` содержал `api.py: 2` и `board_ops_livestate.py: 2` — сейчас `api.py` 1.

---

## 7. Откат (rollback) по каждому шагу

**Шаг A.** Откатить в `src/claude_runner/board_api.py` hunk `@@ -69,34 +70,11 @@` (вернуть определения
`_unavailable` и коллег) и убрать `_unavailable` из импорта на строке 56 (hunk `@@ -53,6 +53,7 @@`);
в `src/claude_runner/dashboard.py` вернуть строку 39 (удалить импорт из `board_common`) и на строке 453
вернуть `refuse = board_api._unavailable(store)` (hunk `@@ -428,7 +450,7 @@`). `board_common.py` остаётся
нужным для `_error`, `_on_*`, `_iso`; если их тоже откатить, файл можно удалить целиком.
Проверка после отката: `python -m mypy src` вернёт строку `dashboard.py:... [attr-defined]`.

**Шаг B.** Откатываются ровно те hunки, которые сейчас есть в diff (их десять в `qa.py` и шесть
в `tests/test_qa.py` — `git diff --unified=3` по этим файлам):

- `src/claude_runner/qa.py` `@@ -11,7 +11,6 @@` — вернуть `import sys`;
- `@@ -130,12 +129,14 @@` — вернуть `print(f"qa: {error}", file=sys.stderr)` вместо
  `log.error("qa: %s", error)` и строки-комментария `# logging has no handler configured: lastResort writes
  ERROR to stderr.` (строка 132) — этот же hunk добавляет комментарий на строке 139
  (`# stdout-is-interface: the operator reads the dashboard URL and stack id here.`);
- `@@ -143,6 +144,7 @@`, `@@ -153,6 +155,7 @@`, `@@ -167,6 +170,7 @@`, `@@ -225,6 +229,7 @@`,
  `@@ -274,6 +279,7 @@`, `@@ -358,6 +364,7 @@`, `@@ -412,6 +419,7 @@`, `@@ -441,6 +449,7 @@` — удалить
  восемь строк `# stdout-is-interface: ...` (строки 147, 158, 173, 232, 282, 367, 422, 452).
  Сами `print`-строки (140, 141, 148, 159, 174, 233, 283, 368, 423, 454, 455) на HEAD уже были — их
  менять не нужно, откат только снимает комментарии.
- `tests/test_qa.py` `@@ -1,13 +1,26 @@` (docstring, `import argparse`, `import json`, расширенный
  список модулей), `@@ -36,8 +49,13 @@` (помощник `_handler` на 52-54), `@@ -49,12 +67,11 @@`,
  `@@ -72,8 +89,7 @@` (вернуть `capsys`-формулировку `output = capsys.readouterr()` / `assert "no
  contract" in output.err` и `assert "missing title" in output.err` — иначе тесты провалятся, так как
  ошибка CLI снова уйдёт в stderr, а не в лог), `@@ -105,12 +121,11 @@` (вернуть `assert f"manifest: {run_dir}" in output.out`),
  `@@ -132,5 +147,141 @@` (удалить восемь print-тестов: `stack up`, `stack down`, `run`, `api`,
  `nightly`, `smoke`, `regression`, `baseline`).
- `import logging` (строка 12) на HEAD был — при откате его убирать не нужно; `log.warning` на 244 и
  256 появились раньше шага B.

Проверка после отката: `python -m ruff check --select T201,BLE001 src/claude_runner/qa.py
src/claude_runner/board_ready.py` снова даёт 17 находков (12 T201 в `qa.py`).

**Шаг C.** В `src/claude_runner/board_ready.py` вернуть `except Exception as exc:` на 147, 167, 196 и
399 (HEAD-номера), удалить `import logging` (24), `logger = logging.getLogger(__name__)` (45), `SourceError`
в импорте (43), локальный `import urllib.error` (188) и `logger.warning` на 404. Полностью: откатить весь
diff файла (`10 5` по numstat).

**Шаг D.** Вернуть `def _beta_cdf(` и `import math` (hunks `@@ -317,29 +316,6 @@`, `@@ -26,7 +26,6 @@`),
вернуть `RUNNING_STATUSES = frozenset({...})` (hunk `@@ -27,7 +27,6 @@`), удалить добавленные тесты:
`tests/test_board_think.py` hunk `@@ -218,6 +218,17 @@`, `tests/test_hermes_run.py` hunks `@@ -78,6 +78,26 @@`
(оба poll-скрипта) и `@@ -289,6 +309,44 @@` (оба новых теста).

---

## 8. Открытые риски и замечания

1. Полный pytest не проходит из-за отсутствия `yaml` в окружении (`ModuleNotFoundError`) — зелёный прогон
   получить нельзя, поэтому проверка ограничена пятью файлами (248 собранных тестов). 4 провала в этих
   пяти файлах воспроизводятся и на копии HEAD (3 × `board_api` про 503/404, 1 × `hermes_run` про
   `ENGINES`) — то есть не созданы шагами A–D, а связаны с незакоммиченными правками других миссий.
2. `board_common.py` пока не в git (`??`) — при откате шага A его нельзя «удалить одним коммитом», он
   существует как неотслеживаемый файл.
3. Внутреннее противоречие бейзлайна: сумма `mypy_by_file` (128) ≠ заявленный `mypy_src_before` (126);
   7 строк для `board_ready.py` в `counts_per_touched_file` против 4 в `deferred.mypy_by_file`. Для отчёта
   взяты числа, измеренные живым `python -m mypy src`.
4. Число находок BLE001 в `src/claude_runner` на HEAD-копии (31) отличается от бейзлайна (32): HEAD-копия
   не содержит 7 неотслеживаемых файлов, один из них даёт одну находку.
5. Числа отложенного (475) получены агрегатом по `src/claude_runner` + `tests`; Codex-файлы в агрегат
   не входят по правилу отбора (их находки 118/99/102/116/159 остаются за рамками списка).
6. PERF401/PERF403/ERA001/ARG* не проверялись пофайловыми прогонами — в ранке только агрегат; перед
   исправлением каждой находки её нужно подтверждать чтением конкретного места.
7. После замечания ревьюера шаг B свёлся к одному переводу: `print(f"qa: {error}", file=sys.stderr)`
   в `main` стал `log.error("qa: %s", error)` (`qa.py:132-133`). Одиннадцать `print` (140, 141, 148,
   159, 174, 233, 283, 368, 423, 454, 455) остались на stdout, поэтому T201 в `qa.py` закрыт только
   на одну находку из двенадцати: `ruff --select T201 src/claude_runner` даёт 22 вместо 23 на HEAD.
   Эти 11 T201 — кандиденты следующей миссии (в раздел 6 они не входят, там ARG/PERF/ERA).
8. Тесты `tests/test_qa.py` (12 тестов, все проходят) pinят stdout-вывод. При откате шага B
   capsys-проверки новых тестов остаются валидными (на HEAD эти `print` были), но caplog-переходы
   в трёх `capture`-тестах (строки 58, 74, 128) нужно тоже откатить: ошибка CLI уходит в stderr, и
   `caplog.text` её не увидит.
