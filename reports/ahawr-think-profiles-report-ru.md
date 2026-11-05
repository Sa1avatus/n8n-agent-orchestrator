# Отчёт миссии ahawr-think-profiles (T012)

Отчёт написан 2026-11-05. Задачи T001–T011 уже выполнены в рабочей ветке: этот отчёт — единственный файл, созданный T012; код, конфиги и патчи больше не изменены. Все ссылки ниже сверены с файлами рабочей директории; команды и идентификаторы приведены дословно.

## 1. Как think_policy передавался до этой миссии (итоги T001)

- Цепочка передачи: строка 5 таблицы `hermes_config` (`worker_think_policy`) → узел `Constants` (`jsCode` строка 81) → узел `Build Worker Run Input` (`jsCode` строка 556) кладёт в запрос к runner'у поле `think_policy`:
  `think_policy: String($('Constants').first().json.worker_think_policy ?? '')` — это зафиксировано в `docs/config-thinking.md:28` (строка 81) и `docs/config-thinking.md:32` (строка 556).
- Политика читается один раз на миссию (сессию), не по задаче: значение пишет `data-tables/ops/set_think_policy.py` (`docs/config-thinking.md:49`), и всем задачам миссии уходит одно и то же. Поле `think_policy_from_task` (`docs/config-thinking.md:29`, `docs/config-thinking.md:33`) влияет только на шаговый сдвиг ThinkSwitch, а не на выбор профиля.
- Поведение ThinkSwitch до починки было дефектным: `_json_policy` возвращала `self._json_cache[text] or self._last_valid_json`, то есть запуска с невалидным текстом политики получал политику предыдущего запуска (это видно в `git diff` по `claude-runner/src/claude_runner/think_switch.py`: удалена строка `self._last_valid_json = policy` и `return self._json_cache[text] or self._last_valid_json`).
- `board_stats.py` группировки по `think_variant` не делает (grep по `think_variant` в `board_stats.py` — совпадений нет), это открытый пункт измерения, см. раздел 6.

## 2. Что изменилось (file:line)

| Файл | Строки | Что там |
|---|---|---|
| `claude-runner/src/claude_runner/think_profiles.py` (новый) | `:22` | `ALLOWED_PROFILE_NAMES = ("none", "plan", "plan+pulse", "heavy")` |
| | `:27` | `def parse_profiles(thinking_json: str \| None) -> dict[str, tuple[Policy, int]]` |
| | `:69` | `def resolve_profile(...)`; `:75` — неизвестное или отсутствующее имя возвращает `None`, `:78` — политика применяется с `cap_steps` |
| | `:81`, `:116` | `_capped` / `_cap_timeline`: `cap_steps` ограничивает суммарное число think-вызовов в таймлайне |
| `claude-runner/src/claude_runner/think_policy.py` | `:163` | `def render_policy(policy: Policy) -> str` — текстовая политика для ThinkSwitch |
| `claude-runner/src/claude_runner/runs.py` | `:39` | импорт `parse_profiles`, `resolve_profile` |
| | `:194–195` | поля `StartRequest`: `think_profile: str = ""`, `features_json: str = ""` |
| | `:335` | `def _profile_policy(self, request: StartRequest) -> tuple[str, str]`: флаг выключен → глобальная политика из `hermes_config` и пустой вариант; `:342` — проверка `features["think_profiles"]["enabled"]` |
| | `:407` | `self.store.update_run(run_id, think_variant=profile_name)` — запись профиля в `runs` |
| `claude-runner/src/claude_runner/api.py` | `:172` | `RunBody` принимает `think_profile: str = ""` |
| `claude-runner/src/claude_runner/think_switch.py` | `:43` | `RUN_MAX_TICKS = 4_320` — предел тиков `run()` (24 ч при задержке 20 с) |
| | `:138–141` | `self._default_parsed` — политика switch'а разбирается один раз вместо `_last_valid_json` |
| | `:213–230` | `_json_policy`: невалидный текст → default switch'а; «A run never inherits the policy of the run that ran before it» |
| `claude-runner/src/claude_runner/thinking_config.py` | `:21–23` | `CATALOG_KEYS = ("profiles",)` — ключ каталога не попадает в настройки Worker |
| `claude-runner/src/claude_runner/features_config.py` | `:17` | `"think_profiles": {"enabled": False}` — флаг по умолчанию выключен |
| `claude-runner/src/claude_runner/store.py` | `:63`, `:97` | колонка `think_variant TEXT` в `runs` |
| `CHANGELOG.md` | `:28–40` | запись под `## unreleased (0.14.0)` про `think_profiles` |
| `docs/hermes-engine.md` | `:220–247` | раздел «Thinking profiles» (заголовок 220): пример каталога, флаг OFF, `runs.think_variant`, правило выбора профиля Architect'ом |

Тесты, которые это закрепляют: `tests/test_think_profiles.py` (10 тестов: известное/неизвестное/отсутствующее имя → `None`; невалидные варианты и `cap` пропускаются; `cap` применяется; повторяющийся таймлайн останавливается на лимите; `plan+pulse` равен сохранённой глобальной политике), `tests/test_think_profiles_patch.py` (поле добавляется ровно один раз; повторный запуск ничего не меняет; отсутствующий или повторяющийся якорь даёт ошибку; отсутствующий или дублирующийся узел даёт ошибку), `tests/test_runs.py:538–627` (при флаге off запрос слово-в-слово прежний, `think_variant` остаётся `None`; при флаге on даёт разрешённую политику и `think_variant`), `tests/test_think_switch.py` (`RUN_MAX_TICKS`, default-политика switch'а).

## 3. Проверки T011: что подтверждено, ключевые строки

Запись T011 в плане (`data-tables/ops/restore_task_state_before_think_profiles_retry.json`) зафиксирована так: полный прогон pytest дал `511 passed, 26 skipped, 33 failed, 13 subtests passed`, ruff/mypy чистые на изменённых файлах.

Повтор оператора 2026-11-05 (этот запуск):

- `python -m pytest tests/test_think_profiles.py tests/test_think_profiles_patch.py tests/test_think_policy.py tests/test_think_switch.py tests/test_runs.py -q --no-cov` → собрано 120 тестов, 119 прошёл, один упал:
  `FAILED tests/test_runs.py::test_unknown_run_and_restart_recovery - assert True is False` (`tests/test_runs.py:478`); это тест восстановления сессии на фиктивном CLI, он входит в 33 известных host-специфичных падения T011 и к `think_profiles` не относится.
- `python -m ruff check src/claude_runner/think_profiles.py ...` → `All checks passed!`
- `python -m mypy` на 7 изменённых модулях → `Found 76 errors in 10 files (checked 7 source files)`. Все 76 — старые ошибки в модулях, которых миссия не трогает (`board_api.py:899, 906, 911`, `api.py:281` — `Unused "type: ignore"`, `runs.py:141` — аргумент `read_sample`). Сами модули миссии чисты: `Success: no issues found in 2 source files` (для `think_profiles.py`/`think_policy.py`, аналогично для `think_switch.py`/`thinking_config.py` и `features_config.py`).
- `git diff --check` → предупреждения только про LF→CRLF в файлах чужих миссий (`board-actions.js` и др.), exit 0.
- `git status`: `.env` не изменён; не закомиченные Codex-файлы (`codex_cli.py`, `codex_config.py`, `codex_run.py`, `tests/test_codex_*`, `docs/codex-executor.md`) относятся к другой миссии и к профилям мышления не имеют отношения.
- Дополнительно (вне критериев T011): патч проверен на копии живой выгрузки `data-tables/ops/_scratch/chain_adaptive.json` (workflow `AHAWR v13 — Claude Code`): первый запуск `added think_profile spread`, второй — `already patched`, в jsCode узла появился `think_profile: __thinking`. Идемпотентность подтверждена.

Что не запускалось и почему:

- полный pytest (`tests/` целиком) не повторялся: он больше 100 секунд и его результат уже зафиксирован в плане T011; повторно прогнана только связка тестов, относящаяся к миссии;
- патч не проверялся на копии `data-tables/ops/_scratch/live_export.json`: в ней узел содержит `const __fresh = __cfg.fresh` (старая выгрузка до адаптивного сплита), и скрипт резонно поднял якорь: `anchor for task lookup occurs 0 times, expected 1`. Это не баг патча — копия устарела; проверка сделана на свежей копии `chain_adaptive.json`.

## 4. Порядок активации (шаги T008, по порядку)

1. **Каталог** — `python data-tables/ops/set_think_profiles.py think_profiles_catalog.json --check` → `ok: profiles none, plan, plan+pulse, heavy`; тот же скрипт без `--check` пишет каталог в `thinking_json` строки 5 `hermes_config` (`stored ... characters in thinking_json: none, plan, plan+pulse, heavy`). Перед первой правкой текущие `thinking_json` и `features_json` сохраняются один раз в `data-tables/ops/think_profiles_before_activation.json`.
2. **Патч живого workflow (оператор)** — `python data-tables/ops/think_profiles_patch.py --src <копия живой выгрузки> --out <черновая копия>`; скрипт сам ничего в n8n не импортирует; оператор проверяет на копии (`added think_profile spread`, повторный запуск — `already patched`) и вносит её в n8n. Без шага 2 поле в запросе просто отсутствует.
3. **Правило Architect'а** — `python data-tables/ops/architect_prompt_think_profiles.py`: сначала сохраняется `data-tables/ops/architect_prompt_before_think_profiles_rule.txt`; правило с маркером `THINKING PROFILES - PICK ONE PER TASK:` добавляется один раз после якоря `The top-level "tasks" array MUST be non-empty.`; повторный запуск — `the rule is already in the Architect prompt; nothing changed`.
4. **Флаг** — `python data-tables/ops/set_think_profiles.py --flag on` → `think_profiles on in features_json (... characters)`. Флаг включает разрешение только на runner'е: без флага добавленное патчем поле игнорируется.
Проверка состояния: `python data-tables/ops/set_think_profiles.py --show`. Шаги 2 и 3 можно менять местами; флаг — последний шаг.

## 5. Откат (rollback)

- Флаг: `python data-tables/ops/set_think_profiles.py --flag off` — поведение runner'а слово-в-слово прежнее (поле `think_profile` игнорируется, берётся глобальная политика).
- Prompt Architect'а: восстановить текст из `data-tables/ops/architect_prompt_before_think_profiles_rule.txt` в строке `architect` таблицы `agent_prompts` (оператор сам, через update той же таблицы).
- Патч: вернуть в n8n копию выгрузки до патча; строго обязательного возврата нет — при выключенном флаге добавленное поле игнорируется runner'ом.
- Полный возврат к состоянию до активации (каталог и флаг вместе): `python data-tables/ops/set_think_profiles.py --rollback` — скрипт восстановит `thinking_json` и `features_json` из `think_profiles_before_activation.json`.

## 6. План измерения (группировка по `runs.think_variant`)

`runs.think_variant` (`store.py:63`, запись `runs.py:407`) заполняется только когда профиль разрешён; при пустом поле или выключенном флаге вариант остаётся пустым — это глобальная политика.

- Базис (до активации): все прогоны с пустым `think_variant`; по ним уже есть метрики QA (accept/needs_changes, число повторов).
- Группы после активации: `none`, `plan`, `plan+pulse`, `heavy` — по каждой считать: число прогонов, долю миссий, принятых без переделки, среднее число переделок, среднее число think-шагов в прогоне (считается ThinkSwitch'ем), среднее время и стоимость.
- Ожидания: группа `none` (`cap_steps 1`) должна давать заметно меньше think-шагов, чем базис; `heavy` (`cap_steps 20`) — больше; доля принятых без переделки не должна быть ниже базиса. Сравнение — между группами и с базисом пустых `think_variant`.
- Открытый пункт: группировки по `think_variant` в `board_stats.py` пока нет (grep — пустой); оператор считает её SQL-выборкой по `runs` (`select think_variant, count(*), ... group by think_variant`), пока view не добавлен.

## 7. Версия

Запись CHANGELOG лежит под заголовком `## unreleased (0.14.0)` (`CHANGELOG.md:28` и далее). Предлагаемая версия стека: **0.14.0** (правило CHANGELOG: новая функция поднимает среднее число — `0.13.100` → `0.14.0`). Версию пакета runner'а (`claude-runner/pyproject.toml:7` = `1.0.0`) эта миссия не двигает; выпуск 0.14.0 фиксировать при активации флага и снятии измерений раздела 6.

## 8. Открытый пункт: несоответствие правил миссии

В плане миссии (`data-tables/ops/restore_task_state_before_think_profiles_retry.json`) зафиксировано дословно:

> RULES 1, 4 and 5 and ACCEPTANCE CRITERIA 1-7 come from a different mission (lessons: board_lessons.py, lessons_patch.py, lessons_enabled, docs/lessons.md). They do not match OBJECTIVE and DESIGN. This plan follows OBJECTIVE and DESIGN. The scope is limited to the think-profile files named in each task. No lessons files are created. The general rules are still in force: no docker, no git commit/push/stash/checkout/reset, no .env, no writes to n8n, hermes_config or data tables, Codex files untouched, targeted tests only, read large files in parts. The final report must state this conflict as an open point.

То есть RULES 1, 4, 5 и ACCEPTANCE CRITERIA 1–7 формулируют функцию «lessons» (lessons-библиотека), которая к Objective и Design этой миссии не относится и не выполнялась: ни `board_lessons.py`, ни `lessons_patch.py`, ни `lessons_enabled`, ни `docs/lessons.md` этой миссией не создавались. План и все 12 задач следуют OBJECTIVE и DESIGN (профили мышления). Это единственное расхождение, которое нужно разрешить перед релизом: либо снять формулировки о lessons из правил, либо вынести их в отдельную миссию.

## Риски

- `board_stats.py` не группирует по `think_variant` — до этого измерения по группам доступны только операторской SQL-выборкой.
- Фактическая активация разделом 4 меняет `hermes_config` и prompt Architect'а; пока оператор не включил флаг, поведение стека не меняется (тест `test_runs.py` фиксирует инертность при `--flag off`).

## Решение оператора (2026-11-05) по пункту 8

Правила 1, 4, 5 и критерии 1–7 были скопированы из миссии `ahawr-lessons-library` (она завершена, релиз 0.11.200, библиотека уже в коде) и к профилям мышления не относятся. Правила этой миссии не правятся, миссия завершена. Что осталось от lessons: флаг `lessons_enabled` выключен и `lessons_scope.sql` не применён; это записано в бэклог ROADMAP.
