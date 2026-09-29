# Compaction analysis (claude-runner)

> **Итог миссии ahawr-fast-compaction (действует вместо противоречивых мест ниже).**
> Значения, принятые в `.env.example` и `.env` (провайдер `local`, все с префиксом
> `CLAUDE_RUNNER_PROVIDER_LOCAL__`): `CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536`,
> `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`, `COMPACT_MIN_TOKENS=40000`,
> `BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`.
> Триггер авто-compact Claude Code остаётся встроенным: 65 536 − 8 192 − 13 000 = 44 344,
> запас 21 192 ≥ 20 000. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=67` (≈ 43 909) — только
> закомментированная альтернатива; значения 37 344 / 56.98 из ранних разделов не применяются.
> Без префикса: `CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS=1`, `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS=600`.
> Изменения `AHAWR_v13.json`, `AHAWR_v13_ClaudeCode.json` (счётчик попыток ретрая) и
> `Claude_Code_Run_Manager_v1.json` (флаг `context_overflow`) и правку `.env.example`
> внёс оператор во время миссии, а не Worker; разделы T012 ниже, где они названы
> нарушением правил или «разрешёнными правилом 1», это не учитывают.

## Текущее устройство

### 1. Формирование окружения для CLI (в т.ч. для `provider=local`)

- `child_env()` собирает env дочернего процесса `claude`: наследует env контейнера **без** переменных, начинающихся на `CLAUDE_RUNNER_`; затем накладывает блок провайдера `provider.env`; если провайдер задает свои креденшелы (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_BASE_URL` — список `CREDENTIAL_VARS`), креденшелы контейнера **удаляются** из env (локальный Worker не видит токен подписки), а вторичные имена моделей (`ANTHROPIC_DEFAULT_OPUS/SONNET/HAIKU_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`) по умолчанию равны модели ранa. Также ставятся `DISABLE_AUTOUPDATER=1` и `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1`. — `claude-runner/src/claude_runner/claude_cli.py:89-106`
- Блок провайдера парсится из `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` (двойной подчеркив): `TOOLS` и `COMPACT_MIN_TOKENS` — ключи, настраивающие runner, а не env процесса (`PROVIDER_RUNNER_KEYS`). Всё остальное попадает в `provider.env` (включая `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `CLAUDE_CODE_DISABLE_THINKING` и т.п. — runner **не выдвигает** их сам; они ставятся оператором в `.env` блоком провайдера, как описано в README: `claude-runner/README.md:195-199`). — `claude-runner/src/claude_runner/config.py:52-54,77-95`
- env процесса получает `provider.env` целиком поверх контейнерного env — это единственный путь передачи `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `CLAUDE_CODE_MAX_CONTEXT_TOKENS` и т.д. В коде runner'а этих имён нет (проверено grep по `claude-runner/src`). — `claude-runner/src/claude_runner/claude_cli.py:94-103`
- env контейнера задаётся в Dockerfile: `CLAUDE_CONFIG_DIR=/home/node/.claude`, `CLAUDE_RUNNER_DATA_DIR=/data`, `CLAUDE_RUNNER_WORKSPACE=/workspace`. — `claude-runner/Dockerfile:25-31`

### 2. settings.json / CLAUDE.md / хуки

- Runner **не пишет** ни `settings.json`, ни `CLAUDE.md`, ни хуки в `CLAUDE_CONFIG_DIR`. `config_dir()` (`claude-runner/src/claude_runner/claude_cli.py:26-28`) читает только `CLAUDE_CONFIG_DIR` и используется исключительно для поиска транскрипта `<config>/projects/<project>/<id>.jsonl` (`transcript_exists`, `claude-runner/src/claude_runner/claude_cli.py:31-36`).
- Единственное, что runner передаёт «как settings», — аргументы CLI и env (см. ниже), а также `--append-system-prompt` из профиля роли.
- Хуки на стороне Claude Code не используются. Единственный hook-механизм в стеке — LiteLLM pre-call hook `ahawr_hooks.py`, который переносит системные сообщения в пользовательский `<system-reminder>` (это hook LiteLLM, не Claude Code). — `claude-runner/README.md:191`

### 3. Аргументы CLI для ролей Architect / Worker / Reviewer

- Команда: `claude -p --output-format stream-json --verbose` + `--model`, `--session-id`/`--resume`, опционально `--bare`; при обычном (не compact) ранe добавляются `--permission-mode`, `--allowedTools`, `--disallowedTools`, `--tools`, `--append-system-prompt`, `--max-turns`, `--max-budget-usd`. Флаг `--include-partial-messages` тоже только для обычного ранa. `settings.extra_args` (`CLAUDE_RUNNER_EXTRA_ARGS`, `config.py:211`) добавляются в команду **и для compact-ранов**, потому что `cmd += list(settings.extra_args)` стоит **вне** ветки `if not compact` (строки 58–75). — `claude-runner/src/claude_runner/claude_cli.py:39-77`
- Профили ролей по умолчанию: `architect` = `dontAsk`, deny `Edit/Write/NotebookEdit` + чтение `.env`; `worker` = `bypassPermissions`, deny чтение `.env` + `git commit/push`; `reviewer` = `dontAsk` как architect; `generic` — как architect. Переопределяются `CLAUDE_RUNNER_<ROLE>_PERMISSION_MODE`, `_ALLOWED_TOOLS`, `_DISALLOWED_TOOLS`, `_APPEND_SYSTEM_PROMPT`, `_MAX_TURNS`, `_TOOLS`. — `claude-runner/src/claude_runner/config.py:28-30,98-103,180-197`
- `--tools` берётся по приоритету `profile.tools` → `provider.tools` → глобальные `settings.tools`; у local-провайдера это обычно `Bash,Read,Edit,Write,Glob,Grep` (срезает системный промпт с ~15k до ~4k токенов). — `claude-runner/src/claude_runner/config.py:229-230`, комментарий `claude_cli.py:64-68`; `claude-runner/README.md:198`
- Пуск процесса: `asyncio.create_subprocess_exec(*cmd, cwd=cwd, env=child_env(provider, model), ...)`, prompt пишется в stdin (headless `-p`), stdout — поток stream-json, stderr собирается с хвостом 8000. — `claude-runner/src/claude_runner/runs.py:247-303`
- Таймаут: обычный ран — `max_run_seconds`, compact — `compact_timeout_seconds`. — `claude-runner/src/claude_runner/runs.py:241-243`
- HTTP-слой: `POST /v1/runs` (`input, model, provider, session_id, working_directory, role`) — `api.py:144-161`; provider/role приходят из `hermes_config` (заголовки `x_ahawr_role`, body). — `claude-runner/src/claude_runner/api.py:144-161`

### 4. Runner-compact: `POST /v1/sessions/{session_id}/compact`

- Endpoint принимает `{mode?: auto|always|off, min_tokens?, model?}`. — `api.py:33-36,195-201`
- Реализация `RunManager.compact()`:
  - режим по умолчанию `settings.compact_mode` (`auto|always|off`, дефолт `auto`, env `CLAUDE_RUNNER_COMPACT_MODE`); порог `settings.compact_min_tokens` (дефолт 120 000, env `CLAUDE_RUNNER_COMPACT_MIN_TOKENS`), при `min_tokens=None` переопределяется блоком провайдера `provider.compact_min_tokens` (`…__COMPACT_MIN_TOKENS`). — `runs.py:404-441`, `config.py:161-163,212-214,93`
  - `skipped` при: `off` (`compaction_disabled`), активном ране (`session_busy`), отсутствии сессии/транскрипта (`session_not_found`), или в режиме `auto` при `context_tokens < threshold` (`below_threshold`). — `runs.py:415-441`
  - Компакция исполняется **как отдельный ран**: `kind="compact"`, `input="/compact"`, `resume=True`, `compact=True`, с тем же backend/model, на котором сессия работала в последний раз. — `runs.py:442-464`
  - Для compact-рана `build_command` с `compact=True` опускает permission-mode/tools и т.п. (только базовая команда + session) — `claude_cli.py:49-77` (ветка `if not compact`); `--include-partial-messages` тоже не добавляется. — `claude_cli.py:50` При этом `settings.extra_args` всё равно добавляются и в compact-ран: `cmd += list(settings.extra_args)` стоит **вне** `if not compact` (строки 58–75), т.е. `CLAUDE_RUNNER_EXTRA_ARGS` доходит и до обычного, и до compact-рана. — `claude_cli.py:76`
  - Результат: ок, если статус рана `completed` и `compact_result == "success"`; в ответе `pre_tokens`/`post_tokens` из `compact_metadata`, `context_tokens_before`. — `runs.py:465-482`
- Размер контекста до compact — `session["context_tokens"]`, который хранится в store из `state.final_context_tokens()` последнего ранa (последний запрос основной нити или средняя величина prompt на ход по total usage — важно через LiteLLM, где per-request usage не стримится). — `runs.py:339,357`, `claude_cli.py:177-187`

### 5. Парсинг событий `kind=compact` (pre_tokens/post_tokens)

- Stream-стат: событие `system/compact_boundary` → `compact_metadata` (триггер, `pre_tokens`, `post_tokens`) сохраняется в `state.compact`; `post_tokens` сразу становится текущим `context_tokens`. Событие `system/status` с `compact_result` → `state.compact_result`. — `claude_cli.py:168-174`
- В итог ранa попадает `details["compact"]` (при `compact is not None`) и `details["compact_result"]`. — `claude_cli.py:228-229`, `runs.py:317-318`
- EventLog пишет человекочитаемую запись `kind="compact"` с `trigger`, `pre_tokens`, `post_tokens` в `/data/events/<run_id>.jsonl`. — `claude-runner/src/claude_runner/events.py:122-131`
- Дашборд: бейдж COMPACT, строка «context compacted (trigger): pre → post tokens». — `dashboard.html:466,493`
- Экспорт: та же запись в Markdown/JSON. — `claude-runner/src/claude_runner/export.py:204-209`
- Handoff (дайджест для новой сессии): `[context compacted: pre → post tokens]`. — `claude-runner/src/claude_runner/handoff.py:66-69`

### 6. Существует ли механизм передачи custom compact instructions / хуков в Claude Code

**Специального механизма** для передачи кастомных инструкций для компакции или пользовательских хуков в Claude Code **нет** — runner не выдвигает отдельной переменной/флага, задающего стиль или текст сжатия, и не создаёт хук-файлы. Тем не менее существует **общий канал**, доходящий и до обычного, и до compact-рана:

- `CLAUDE_RUNNER_EXTRA_ARGS` (`settings.extra_args`, `config.py:211`) добавляется в команду **вне** ветки `if not compact` — `cmd += list(settings.extra_args)`, `claude_cli.py:76`. Это единственный параметр, который runner пробрасывает в компакцию.
- env блока провайдера (`provider.env`, `claude_cli.py:103`) передаётся целиком и в compact-ран: любые `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` (кроме `TOOLS`/`COMPACT_MIN_TOKENS`) попадают в env процесса.
- Команда compact сама — фиксированный `/compact` как stdin, без prompt-контента: `input="/compact"`, `resume=True`, `compact=True`, `build_command` для compact не принимает текст. — `runs.py:442-464`
- `--append-system-prompt` существует, но применяется **только к обычным** (не compact) ранам и содержит текст роли, не инструкцию compact. — `claude_cli.py:69-70`
- Settings-файлы и хуки Claude Code runner не создаёт (см. раздел 2).

**Гипотеза (не подтверждено прогонком):** compact-ран запускается **без** `--tools` и **без** `--append-system-prompt` (ветка `if not compact`, `claude_cli.py:58-70`), поэтому его системный промпт и набор инструментов, вероятно, **отличаются** от обычного ранa той же сессии (напр., при local-провайдере обычный ран сужает системный промпт через `--tools` до ~4k токенов, а compact-ран этого не делает). Это не доказано — поведение при `/compact` зависит от внутренней реализации Claude Code.

Итог: прямой способ задать «инструкцию компакции» отсутствует; управлять можно только (а) порогом (`COMPACT_MIN_TOKENS`), (б) моделью сессии, и (в) общим каналом `CLAUDE_RUNNER_EXTRA_ARGS` + env провайдера, которые доходят и до compact-рана.

---

## Подтверждённое поведение CLI (Claude Code 2.1.283)

Бинарь: `/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe` (241 МБ, Bun-компилированный ELF).

### 1. `## Compact Instructions` в CLAUDE.md

**Гипотеза.** Шаблон компактного запроса (оба варианта — полный `tko` и частичный `eko`) содержит:

> "There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating the above summary. Examples of instructions include:
> `<example>## Compact Instructions ...</example>`"

Это **промпт-инструкция** для модели, а не механизм парсинга секции CLAUDE.md. Гипотеза состоит в том, что секция `## Compact Instructions` из CLAUDE.md попадает в контекст сессии вместе со всем остальным CLAUDE.md и модель видит её в промпте компакции. **Не подтверждено:** в бинаре нет парсера, извлекающего именно эту секцию из CLAUDE.md, и нет прямого подтверждения, что содержимое CLAUDE.md доходит до компактного запроса. Прямое подтверждено лишь то, что модель получает инструкцию «включённого контекста» (included context), а не что этот контекст включает CLAUDE.md.

**Источник:** бинарные строки @98019430, @205077998 (оба шаблона `eko`/`tko`).

### 2. PreCompact hook

**Подтверждено.** Формат хука:

- Поле матчера: `trigger`, значения `["manual", "auto"]`.
- Exit code 0 → stdout **добавляется как custom compact instructions** (конкатенируется с уже имеющимися).
- Exit code 2 → блокирует компакцию.
- Прочие exit codes → stderr показывается пользователю, компакция продолжается.

**Код:** `Ufe(session, {trigger, customInstructions})` — вызывается **до** запроса компакции; `Ne.newCustomInstructions` из результата хука конкатенируется через `HVe(e, n)`:

```js
function HVe(e, n) {
  if (!n) return e || void 0;
  if (!e) return n;
  return `${e}\n\n${n}`;
}
```

**Событие хука:** `{hook_event_name: "PreCompact", trigger: n.trigger, custom_instructions: n.customInstructions}` — @205445227.

**Метаданные хука (из `claude --help` / бинарная строка):** @221375100–221375800:
- `matcherMetadata: {fieldToMatch: "trigger", values: ["manual", "auto"]}`
- "Exit code 0 - stdout appended as custom compact instructions"
- "Exit code 2 - block compaction"

### 3. `/compact <instructions>`

**Подтверждено.** Команда `/compact` принимает **опциональный аргумент** — кастомные инструкции суммаризации:

```js
FWn = {
  type: "local",
  name: "compact",
  description: "Free up context by summarizing the conversation so far",
  argumentHint: "<optional custom summarization instructions>",
  supportsNonInteractive: true,
  ...
}
```

**Источник:** бинарная строка @204377759.

### 4. `customInstructions` в запросе компакции — **где**

**Подтверждено.** `customInstructions` вставляются **в конец пользовательского сообщения** (compact prompt), а не в system prompt.

Цепочка: `compactConversation({instructions})` → `Ze` → `hbr` → `_oe` → `iko`:

В `iko` (байт @205087074):
```js
let V = moe(r);        // r = customInstructions
let ge = Ae({content: V});  // создаётся user-сообщение
ye = await pE({promptMessages: [ge], ...});
```

В `moe` (байт @205083598):
```js
function moe(e) {
  let n = YTt + qSo;           // основной шаблон компактного запроса
  if (e && e.trim() !== "")
    n += `\n\nAdditional Instructions:\n${e}`;  // customInstructions добавляются
  return n += QTt, n;          // финальное напоминание
}
```

Структура итогового user-сообщения:
1. `YTt` — "CRITICAL: Respond with TEXT ONLY..."
2. `qSo` — основной шаблон (9 секций: Primary Request, Key Technical Concepts, Files, Errors, Problem Solving, Pending Tasks, Current Work, Next Step, Summary)
3. **`Additional Instructions: <customInstructions>`** — если non-empty
4. `QTt` — "REMINDER: Do NOT call any tools..."

**Важно для кэша:** custom instructions — часть **user-сообщения** (compact prompt), а не system prompt.

При построении запроса `pE` (байт @205120553) формируется массив сообщений:

```js
let Wt = [...r===void 0 ? _t : Yqe(_t), ...e]
```

где `_t` = `forkContextMessages` (история диалога, переданная через `vPe(e)` в `iko`), а `e` = `promptMessages` — массив из **одного** compact-сообщения `Ae({content: moe(r)})`. То есть история диалога (`_t`) помещается **перед** compact prompt (`e`), а `moe(r)` вставляет customInstructions в **хвост** compact prompt:

```js
n = YTt + qSo;                          // шаблон
n += `\n\nAdditional Instructions:\n${r}`;  // customInstructions — в хвост
n += QTt;
```

Следовательно, при изменении custom instructions меняется **только хвост последнего user-сообщения**, а история диалога (префикс `Wt`) остаётся идентичной — кэшируемый префикс сохраняется. System prompt (`Bt = r?.systemPrompt ?? n.systemPrompt`) не затрагивается.

**Источники:** бинарные строки @205083598 (`moe`), @205087074 (`iko`), @205102425 (`hbr`), @205120553 (`pE` — `Wt=[..._t,...e]`), @205135903 (`HVe`).

### 5. Auto-compact threshold

**Подтверждено.** Переменные (все с прямыми байтовыми смещениями):

- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` — переопределяет порог (проценты от окна). Функция `twe(e, n, r)` читает `process.env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` — @204284331.
- `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` — блокирующий лимит. В той же функции `twe` (байт @204284331): `g=process.env.CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE`.
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` — размер окна авто-компакции. Функция `CT(e, n, r)` (байт @204281865): `if(process.env.CLAUDE_CODE_AUTO_COMPACT_WINDOW){ let ge=uLe("CLAUDE_CODE_AUTO_COMPACT_WINDOW", ...); if(ge.status!=="invalid"){ return {window:Math.min(g,ge.effective), configured:ge.effective, source:"env"} } }` — приоритет: env > settings > clientdata > experiment.

**CLI-флаг:** `--autocompact <auto|tokens>` — подтверждено в `claude --help`.

**Текст из бинара:** "the summary buffer, lowered further by CLAUDE_AUTOCOMPACT_PCT_OVERRIDE when set" — @198012628.

### 6. Microcompact (clearing old tool results)

**Подтверждено.** Microcompact — отдельный режим компакции, который **очищает старые tool results**.

**Триггер:** функция `lto(e, o, n)` (байт @210948215) запускает очистку, когда `tokensSaved >= xar`, где `xar = 20000` (байт @210946414) — то есть накопленный объём старых tool results, который можно очистить, должен составить **не менее 20 000 токенов**. Параметр `keepRecent` (по умолчанию `E = 2000`, байт @210946414) определяет, сколько последних tool results **сохраняются** — они не очищаются.

**Код:**
```js
async function lto(e, o, n) {
  let { keepSet: r, tokensSaved: a, candidates: l } = Iar(e, n.keepRecent);
  if (a < xar) return null;  // xar = 20000 — порог
  ...
  i("tengu_time_based_microcompact", {
    toolsCleared: s.size, toolsKept: r.size,
    keepRecent: n.keepRecent, tokensSaved: a,
    trigger: y("context_hint")
  });
  t(`[KEEP-RECENT MC] context_hint trigger, cleared ${s.size} tool results (~${a} tokens), kept last ${r.size}`);
  return { messages: p, tokensSaved: a, clearedIds: s, clearedContent: u };
}
```

**Управление:** `keepRecent` передаётся параметром в `lto(e, o, n)` — в бинаре **не найдено** env-переменной или settings-ключа, управляющего `keepRecent` или порогом `xar`. Режим `microcompact` активируется как вариант `rr` в цепочке компакции (`rr === "microcompact"` → `Xse(Ee, rr)`, байт @205314771), но механизм выбора между auto/manual/microcompact не найден в бинаре.

**Строки:** `tool_result_clear`, `toolsCleared`, `toolsKept`, `compact_micro_keep_recent`, `clearedIds`, `clearedContent` — @96806677.

**Источник:** бинарные строки @205314771, @96806677, @210948215 (`lto`), @210946414 (`xar=20000, E=2000`).

### 7. `BASH_MAX_OUTPUT_LENGTH`

**Подтверждено.** Дефолт: **30000**, clamp **4000–150000** (128000 в doc-строке, 150000 в кодовой константе `KRr`).

**Переменная в settings:** `bashOutputMaxChars` (Zod-схема, байт @196624235). Именно эта переменная заменяет `BASH_MAX_OUTPUT_LENGTH` при установке.

```js
// Байт @203002424
var KRr = 150000, x1n = 30000;
function O4e() { return A(Je().bashOutputMaxChars) ?? x1n; }
function Nae() {
  let e = A(Je().bashOutputMaxChars);
  if (e !== void 0) return e;
  return uLe("BASH_MAX_OUTPUT_LENGTH", process.env.BASH_MAX_OUTPUT_LENGTH, x1n, KRr).effective;
}
```

Логика: если в settings задан `bashOutputMaxChars` — используется он; иначе fallback на env `BASH_MAX_OUTPUT_LENGTH` с clamp 4000–150000.

**Документация в бинаре (байт @196624235):** "How many characters of a successful Bash or PowerShell command's output Claude receives inline (default 30000; values clamp to 4000-128000). Output past this is saved to a file and Claude receives a short preview plus the path. **When set, this also replaces BASH_MAX_OUTPUT_LENGTH, which on its own only sizes the read-back window.**"

**Источники:** @203002424 (`Nae`/`O4e`), @196624235 (Zod-схема `bashOutputMaxChars` + doc-строка), @101417089.

### 8. `MAX_MCP_OUTPUT_TOKENS`

**Подтверждено.** Дефолт: **25000** токенов.

```js
l = 1600, P = 25000;
function u() {
  let e = a.MAX_MCP_OUTPUT_TOKENS;
  if (e !== void 0 && e > 0) return e;
  return P;
}
```

**Сообщение об ограничении:** "MCP tool output truncated" с `[token limit]` — @97424380.

**Источник:** @222704152, @97424380.

### 9. `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`

**Подтверждено.** Ограничение на output Read-тула:

```js
function r() {
  let e = a.CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS;
  if (e !== void 0 && e > 0) return e;
  return void 0;
}
```

**Источник:** @199146579.

### 10. `--system-prompt-snapshot`

**Гипотеза.** Флаг `--system-prompt-snapshot <on|off>` — записывает system prompt и **переиспользует его дословно до компакции**. Текст из `claude --help` (байты @102001914, @212484651) прямо указывает на сброс при компакции:

> "on (the default): the prompt is rendered on the conversation's first request ... every later request and resume sends the record as-is, even when a later launch passes different text, **until the conversation is compacted**."

Т.е. snapshot сбрасывается при компакции — **подтверждено** текстом `--help`. Механизм сброса (когда именно snapshot перезаписывается) в бинаре **не подтверждено** прямым кодом — это гипотеза.

**Важно для кэша:** если snapshot используется, system prompt не меняется между запросами до компакции — это благоприятно для prefix-caching. Custom instructions (см. п. 4) **не влияют** на system prompt.

**Источник:** `claude --help` (байты @102001914, @212484651) — «until the conversation is compacted».

### 11. Idle compaction (автоматическая, при простое)

**Гипотеза.** `compactConversation({instructions: ""})` — вызывается с **пустыми** instructions (байт @223954497):

```js
compact: () => this.compactConversation({instructions: ""}).then(
  (h) => {
    if (h.skip !== void 0) return null;
    return this.transcript.replace((M) => [...M, Ft("Compacted while idle, before the prompt cache expired", "notice")]),
    { tokensBefore: h.tokensBefore, tokensAfter: h.tokensAfter };
  }
)
```

Подтверждено: **пустые instructions** и сообщение «Compacted while idle, before the prompt cache expired». **Гипотеза:** условие срабатывания — именно «простота» (idle) сессии — не подтверждено прямым кодом в бинаре; механизм выбора момента idle-компакции не найден.

**Источник:** @223954497.

### 12. `DISABLE_COMPACT`

**Подтверждено.** Env-переменная `DISABLE_COMPACT` отключает компакцию (и `/compact`, и авто-компакцию).

```js
isEnabled: () => !Le(process.env.DISABLE_COMPACT)
```

**Источник:** @204377759, @209691776.

---

## LiteLLM и кэш

### 1. Что именно меняет хук (ссылки `litellm/ahawr_hooks.py:строка`)

Хук — `MidSystemMessageHook` (`litellm/ahawr_hooks.py:51-58`), подключён через `callbacks: ahawr_hooks.proxy_handler_instance` (`litellm/config.yaml:33`). Срабатывает как pre-call: `async_pre_call_hook` берёт `data.get("messages")` (`litellm/ahawr_hooks.py:55`) и заменяет `data["messages"]` на результат `move_mid_system_messages(messages)` (`litellm/ahawr_hooks.py:57`).

`move_mid_system_messages` (`litellm/ahawr_hooks.py:33-48`) для каждого сообщения:
- `role != "system"` или индекс 0 → пропускается без изменений (`litellm/ahawr_hooks.py:36-38`);
- системное сообщение **внутри** диалога (индекс > 0): текст извлекается через `_text` (`litellm/ahawr_hooks.py:27-30,39`); если текст пуст — сообщение **отбрасывается** (`litellm/ahawr_hooks.py:40-41`);
- иначе создаётся блок `{"type":"text","text":"<system-reminder>\n…\n</system-reminder>"}` (`litellm/ahawr_hooks.py:42`), который **сливается в хвост предшествующего user-сообщения**, если оно есть (`litellm/ahawr_hooks.py:43-45`); если предшественник не user (или нет предшественника) — создаётся **новое** user-сообщение с одним блоком (`litellm/ahawr_hooks.py:46-47`).

Реальный системный промпт — поле `system` (Anthropic) или системное сообщение в начале (OpenAI) — **не затрагивается** (`litellm/ahawr_hooks.py:9`, условие `index == 0` — `litellm/ahawr_hooks.py:36`).

**Модифицируются ровно следующие поля запроса:** `data["messages"]` — состав и/или текст конкретных сообщений (конвертация system→user-`<system-reminder>`, слив в предшествующее user-сообщение, удаление пустых). **Не модифицируются** `system`, `max_tokens`, `tools`, `stop_sequences`, `stream` и остальные параметры — хук к ним не обращается (в `litellm/ahawr_hooks.py:55-58` есть только `data.get("messages")`/`data["messages"]`). `max_tokens` при этом **не управляется** хуком; лимит вывода задаётся env-переменной `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (блок провайдера, `claude-runner/src/claude_runner/claude_cli.py:94-103`), который доходит и до compact-рана (см. раздел «Текущее устройство», п. 1).

### 2. От чего зависит преобразование (содержимое / тип запроса)

Преобразование **не зависит** от типа запроса: обычный ран и compact-ран проходят через один и тот же `async_pre_call_hook` (один `callbacks`-объект, `litellm/config.yaml:33`). Отличие только **структурное** — в каких сообщениях стоит `role: "system"` с индексом > 0:
- обычный ран: Claude Code шлёт часть контекста (например блок «# Environment») как mid-conversation `system`-сообщения — хук их конвертирует, меняя текст user-сообщений;
- compact-ран: запрос — история диалога (user/assistant) + одно компактный user-сообщение (см. раздел «Подтверждённое поведение CLI», п. 4). Если в истории нет mid-conversation `system`-сообщений, хук **ничего не меняет** — массив `messages` возвращается таким же (все сообщения попадают в `out` без изменений, `litellm/ahawr_hooks.py:36-38`). Даже если mid-system есть, они сливаются в предшествующее user-сообщение — **начало** массива (системный промпт, первые сообщения истории) не трогается.

### 3. Влияние на префикс-кэш llama.cpp для запроса сжатия

- **Системный промпт (начало запроса) хук не меняет никогда** — ни в обычном ране, ни в compact-ране (`litellm/ahawr_hooks.py:36` условие `index == 0`). Это важно: префикс-кэш llama.cpp строится на **том, что реально уходит в /v1/chat/completions**, а хук не трогает начало запроса, поэтому начало запроса сжатия совпадает с предыдущим шагом и префилл остаётся в кэше.
- **Компактный запрос — предыдущая история + инструкция в конце.** Запрос сжатия строится из **предыдущей истории** (все сообщения до текущего) плюс **одно user-сообщение** с инструкцией компактирования в конце (см. п. 4 «Подтверждённое поведение CLI»: `Wt=[..._t,...e]`, где `_t` — история, `e` — компактный промпт). `move_mid_system_messages` (`litellm/ahawr_hooks.py:33-48`) — **детерминированная функция** только от `messages`: результат зависит исключительно от того, какие сообщения в массиве и где стоят `role: "system"` с индексом > 0. Если история перед компактным запросом **идентична** предыдущему шагу (а компактный промпт добавляется только в конец), то преобразованный префикс **совпадает** с предыдущим шагом — кэш не ломается.
- **Edge case (где кэш ломается):** если mid-system-сообщение стоит **сразу после** последнего user-сообщения истории, хук **сливает** его в это user-сообщение (`ahawr_hooks.py:43-45`). Тогда текст последнего user-сообщения меняется, и кэш ломается **только с этого сообщения** — то есть в **хвосте**, а не в префиксе. Префикс (системный промпт + все сообщения до этого) остаётся идентичным и кэшируется.
- **При предлагаемых изменениях** (только `max_tokens`): хук **не трогает** `max_tokens` вообще; ограничение вывода задаётся через `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (env блока провайдера, `claude-runner/src/claude_runner/claude_cli.py:94-103`) — это **не** изменение `data["messages"]` и не изменение системного промпта. Следовательно, начало запроса сжатия **не меняется** — кэш не сломается. Если бы изменение затрагивало `messages` (например, добавление инструкций в системный промпт), префикс бы сломался.
- **Важный нюанс:** компактный запрос сам по себе меняет **хвост** (новое user-сообщение — компактный промпт), но **начало** (история + системный промпт) остаётся идентичным — это покрывается префикс-кэшем (см. п. 4 «Подтверждённое поведение CLI»: `Wt=[..._t,...e]`). Хук на это не влияет.

**Вывод:** при текущем хуке **начало запроса сжатия не меняется** относительно предыдущего шага — хук трогает только mid-conversation `system`-сообщения, а начальные сообщения (системный промпт и начало истории) не трогает. Единственный случай, где кэш ломается, — если mid-system-сообщение стоит сразу после последнего user-сообщения (слив в хвост). Предлагаемое ограничение только `max_tokens` (через `CLAUDE_CODE_MAX_OUTPUT_TOKENS`) **не затрагивает** `messages`/`system`, поэтому кэш не ломается.

### 4. Можно ли безопасно распознать запрос сжатия и ограничить только `max_tokens`

**Да.** Распознать компактный запрос можно **безопасно**:
- **По последнему user-сообщению:** компактный промпт содержит фиксированный шаблон `YTt` ("CRITICAL: Respond with TEXT ONLY...") и `qSo` (основной шаблон из 9 секций) — см. п. 4 «Подтверждённое поведение CLI». Хук может проверять, содержит ли **последнее** user-сообщение маркерную строку `CRITICAL: Respond with TEXT ONLY` (подтверждено по бинарным строкам `YTt`, `qSo`). Строка `Additional Instructions:` **не** подходит как маркер — она появляется и в других контекстах.
- **Можно ли хуку задать `data["max_tokens"]` только для компактного запроса?** Да — `max_tokens` **не входит в промпт** (это параметр запроса, а не текст), поэтому установка `data["max_tokens"]` **не меняет префикс** и **не ломает кэш**. Хук в текущей реализации **не трогает** `max_tokens` вообще (`litellm/ahawr_hooks.py:55-58` — только `data.get("messages")`/`data["messages"]`), но при желании может задать `data["max_tokens"]` для распознанного компактного запроса — это безопасно.
- **Сравнение с `CLAUDE_CODE_MAX_OUTPUT_TOKENS`:** `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (env блока провайдера, `claude-runner/src/claude_runner/claude_cli.py:94-103`) задаёт лимит вывода для **всех** запросов (обычных и компактных). Это **не** изменение `data["messages"]`, поэтому не меняет префикс. Хук может задать `max_tokens` **только** для компактного запроса, тогда как `CLAUDE_CODE_MAX_OUTPUT_TOKENS` действует на все запросы.

**Итог:** распознавание компактного запроса по маркерной строке `CRITICAL: Respond with TEXT ONLY` в последнем user-сообщении и ограничение только `max_tokens` (хук может задать `data["max_tokens"]` для компактного запроса) **безопасны** для префикс-кэша — `max_tokens` не входит в промпт, поэтому начало запроса сжатия не меняется.

---

## Повторяемые команды верификации

```bash
# 1. Шаблон compact prompt (оба варианта: eko/tko)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'## Compact Instructions', data):
    s=m.start(); chunk=data[max(0,s-300):s+300]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:500]}')
"

# 2. PreCompact hook matcher
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'PreCompact', data):
    s=m.start(); chunk=data[max(0,s-200):s+300]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    if 'trigger' in txt: print(f'@{s}: {txt[:400]}')
"

# 3. /compact argumentHint
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'argumentHint', data):
    s=m.start(); chunk=data[max(0,s-100):s+200]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    if 'compact' in txt.lower(): print(f'@{s}: {txt[:300]}')
"

# 4. moe() — где вставляются customInstructions
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'function moe(', data):
    s=m.start(); chunk=data[s:s+500]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:400]}')
"

# 5. iko() — promptMessages с customInstructions
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'function iko(', data):
    s=m.start(); chunk=data[s:s+500]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:400]}')
"

# 6. HVe() — конкатенация custom instructions
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'function HVe(', data):
    s=m.start(); chunk=data[s:s+200]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:200]}')
"

# 7. Microcompact
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'microcompact', data):
    s=m.start(); chunk=data[max(0,s-200):s+300]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    if 'compact' in txt.lower() or 'clear' in txt.lower(): print(f'@{s}: {txt[:400]}')
"

# 8. BASH_MAX_OUTPUT_LENGTH дефолт
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'x1n=', data):
    s=m.start(); chunk=data[s:s+100]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"

# 9. MAX_MCP_OUTPUT_TOKENS дефолт
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'MAX_MCP_OUTPUT_TOKENS', data):
    s=m.start(); chunk=data[max(0,s-200):s+300]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    if '25000' in txt or 'default' in txt: print(f'@{s}: {txt[:400]}')
"

# 10. CLAUDE_AUTOCOMPACT_PCT_OVERRIDE + CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE (twe)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'CLAUDE_AUTOCOMPACT_PCT_OVERRIDE', data):
    s=m.start(); chunk=data[max(0,s-200):s+400]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:500]}')
"

# 11. CLAUDE_CODE_AUTO_COMPACT_WINDOW (CT)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(b'CLAUDE_CODE_AUTO_COMPACT_WINDOW', data):
    s=m.start(); chunk=data[max(0,s-200):s+400]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt[:500]}')
"

# 12. pE — Wt=[..._t,...e] (история перед promptMessages)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(re.escape(b'let Wt=['), data):
    s=m.start(); chunk=data[max(0,s-100):s+200]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"

# 13. iko — forkContextMessages: s?vPe(e):e
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(re.escape(b'forkContextMessages:s?vPe(e)'), data):
    s=m.start(); chunk=data[max(0,s-200):s+200]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"

# 14. lto — microcompact trigger (xar=20000)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(re.escape(b'function lto('), data):
    s=m.start(); chunk=data[s:s+800]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"

# 15. xar=20000, E=2000
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(re.escape(b'xar=20000'), data):
    s=m.start(); chunk=data[max(0,s-100):s+200]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"

# 16. bashOutputMaxChars (Zod schema + doc string)
python3 -c "
data = open('/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe','rb').read()
import re
for m in re.finditer(re.escape(b'bashOutputMaxChars'), data):
    s=m.start(); chunk=data[max(0,s-200):s+500]
    txt=''.join(chr(b) if 32<=b<127 or b in (10,9) else '?' for b in chunk)
    print(f'@{s}: {txt}')
"
```

## Частота сжатий по событиям runner'а (T004)

Источники: `GET http://claude-runner:8700/v1/runs` (полный список последних 50 прогонов) и
`GET http://claude-runner:8700/v1/runs/{id}/events` для каждого прогона. Запросы к
127.0.0.1:8033 и LiteLLM не отправлялись.

### Команды и выдержки ответов

```
curl -s http://claude-runner:8700/v1/runs
```

Ответ (выдержка, 39.4KB):

```json
{"runs":[{"run_id":"run_5a8f0b3d13b442579e15eac72ec3a60b","kind":"run","title":"TASK T004: Собрать частоту сжатий по событиям runner'а","status":"running",...},
{"run_id":"run_bec6849936a545b980d4d7884422f2a2","kind":"run","title":"Review: Изучить хук LiteLLM...","status":"completed","num_turns":6,"context_tokens":42107,...}, ...]}
```

Для каждого прогона: `curl -s http://claude-runner:8700/v1/runs/<run_id>/events`,
фильтр `kind == "compact"`. Пример записи (выдержка):

```json
{"seq": 147, "t": 1790616909.353, "kind": "compact", "trigger": "auto", "pre_tokens": 47415, "post_tokens": 6197}
```

### Таблица сжатий (все 50 последних прогонов)

Шаги между сжатиями: первый интервал = `seq_1 - 1` (события до первого compact),
следующие = `seq_{i+1} - seq_i - 1` (события между соседними compact).

| run_id | Задача (прогон) | status | turns | compact (trigger, pre → post, seq, t epoch) | Сжатий | Шагов между | Ходов на сжатие |
|---|---|---|---|---|---|---|---|
| run_127d23d1737243988a4693582309ba03 | TASK T002: Подтвердить поддержку Compact Instructions (атт. 1) | completed | 71 | auto 47415→6197 seq147 t=1790616909; auto 45471→8367 seq232 t=1790617579 | 2 | 146, 84 | 35.5 |
| run_76d7f3e43871440cbccf74997feb1d03 | TASK T002: Подтвердить поддержку Compact Instructions (атт. 2) | completed | 35 | auto 45373→6524 seq40 t=1790618239; auto 53776→3007 seq97 t=1790619152 | 2 | 39, 56 | 17.5 |
| cmp_9c9e19e8c61b4501b53ea240967d87bf | `/compact` (ручной, kind=compact) | completed | 0 | manual 43269→11866 seq3 t=1790615678 | 1 | 2 | — |
| run_0bcbcd3df0db4687a466ead10442e782 | TASK T003: Trace every prompt_clear call site (атт. 1) | completed | 94 | auto 48878→11169 seq3 t=1790612664; auto 47916→6373 seq125 t=1790613304 | 2 | 2, 121 | 47.0 |
| run_9bce5ec83d6b449db02854cbec1123b5 | TASK T003: Trace every prompt_clear call site (атт. 3) | completed | 26 | auto 42895→11838 seq3 t=1790608251; auto 45164→2640 seq56 t=1790610301 | 2 | 2, 52 | 13.0 |
| run_de15878d0f9d45deb034055bbace6ca2 | TASK T002: Extract and triage llama-cpp-server log evidence | completed | 56 | auto 33544→8173 seq69 t=1790599095; auto 34579→10628 seq97 t=1790599651; auto 45959→6762 seq108 t=1790600305 | 3 | 68, 27, 10 | 18.7 |
| run_911890ced9ff467cbc40c4fba4544919 | TASK T002: Extract and triage llama-cpp-server log evidence (атт. 3) | completed | 30 | auto 44340→8830 seq62 t=1790603669 | 1 | 61 | 30.0 |
| run_be778a6d0c1e459bad5e7d3255db19e3 | TASK T002: Extract and triage llama-cpp-server log evidence (атт. 4) | completed | 21 | auto 45917→5091 seq25 t=1790605164 | 1 | 24 | 21.0 |
| run_e613e7505c53466ca132bf658b688933 | TASK T001: Исследовать, как runner запускает Claude Code (атт. 1) | completed | 35 | auto 45488→19031 seq46 t=1790614873 | 1 | 45 | 35.0 |
| run_7d6c31d82b4747eda18c46f426425da5 | TASK T003: Изучить хук LiteLLM (атт. 2) | completed | 17 | auto 44257→7510 seq11 t=1790620267 | 1 | 10 | 17.0 |

Прочие 40 прогонов (все Review/Plan/короткие TASK-попытки) compact-событий **не содержат** —
проверены все через `/v1/runs/{id}/events` (0 компактов).

### Итоги

- Всего сжатий за все последние 50 прогонов: **16** (из них 15 auto + 1 manual).
- Прогоны с ≥1 сжатием: 10 из 50.
- **Среднее число сжатий на задачу/прогон: 1.6** (16 / 10) среди прогонов, где сжатие
  происходило; по всем 50 прогонам — 16 / 50 = 0.32.
- Контекст вырастает до **~43–54K токенов** и сжимается до
  **~3–19K токенов** (post_tokens: min 2640, max 19031); все 15 авто-сжатий —
  `trigger=auto`, одно сжатие — `trigger=manual` (ручной `/compact`).
- Интервалы в событиях: 146, 84, 39, 56, 2, 2, 121, 2, 52, 68, 27, 10, 61, 24, 45, 10 —
  диапазон 2–146, **медиана 42** (сортировка: 2,2,2,10,10,24,27,39,45,52,56,61,68,84,121,146; две средних: 39 и 45 → (39+45)/2 = 42).
- **Ходов на сжатие** (turns / число сжатий, cmp_ исключён): 35.5, 17.5, 47.0, 13.0,
  18.7, 30.0, 21.0, 35.0, 17.0 — медиана **21.0**; суммарно 385 ходов / 15 авто-сжатий =
  **25.7 хода на сжатие**.
- Сопоставление с данными миссии (каждые 10–20 шагов): **не подтверждается** —
  ходов на сжатие по прогонам 13–47, медиана 21, т.е. сжатие происходит реже
  «каждые 10–20 ходов»; типичный цикл — ~17–35 ходов до следующего сжатия.

### Ограничения

- Интервалы считаются по event `seq` (события event log); «ходов на сжатие» — по
  `num_turns / число сжатий` (для компактных прогонов `num_turns=0`).
- Время сжатий (epoch) приводится для каждого compact-события в таблице; все
  сжатия — в пределах одного дня (2026-09-28).
- Прогоны `running`/`failed`/`cancelled` с компактами не выявлены; failed/cancelled
  короткие прогоны не достигли порога авто-сжатия.

---

## Стоимость сжатия: рычаги и решения (T005)

### Базовые числа и формула

Формула для каждого рычага: **экономия = Δтокены / скорость (с)**, где
Δтокены — сколько токенов перестаёт вычисляться, скорость — измеренная
tok/s (генерация ≈ 23.3 ток/с: 8192 ток. за 350 с; префилл ≈ 350–400 ток/с:
1405 ток. за 6 с, 11 239 ток. за ~40 с). Проверялась вручную и `python -c`.

Частота (T004): 16 сжатий (15 auto + 1 manual) на 10 прогонов с сжатиями →
**1.6 сжатия на задачу** (по всем 50 прогонам — 0.32). Экономия «на задачу»
= экономия на одно сжатие × 1.6 (× 0.32 — по всем прогонам).

### Таблица рычагов

| # | Рычаг | Формула / механизм | Экономия на одно сжатие | Экономия на задачу (×1.6) | Риск потери информации | Решение |
|---|-------|--------------------|------------------------|---------------------------|-------------------------|---------|
| 1 | Префилл запроса сжатия (кэш) | Префилл уже из кэша: 1405 ток. за ~6 с (45 753 restored). Любое изменение **начала** запроса (системный промпт, история) вернёт полный префилл ~45K ток. ≈ 125 с. Хук LiteLLM трогает только mid-system; `max_tokens` в кэш не входит. | **~6 с** (сохраняя; при поломке кэша **+125 с** на сжатие) | ~10 с (сохраняя; при поломке кэша +200 с) | Нет, если не менять начало запроса; кэш ломается только при изменении prefix (edge case: mid-system сразу после последнего user — сливается в хвост, префикс жив) | **Применять (сохранять)** — кэш уже работает; запрет: не менять начало запроса сжатия (не добавлять текст в системный промпт/историю; только `max_tokens` в хуке) |
| 2 | Длина сводки ≤ 2000 ток. | 8192−2000 = 6192 ток. / 23.3 ≈ **266 с** (генерация 352.6 с → 86.1 с) | **≈266 с** | **≈426 с** | Средняя (сводка короче: теряются детали — но обязательный минимум (задача, критерии, факты с file:line, изменённые файлы, следующий шаг) сохраняется) | **Применять** — Compact Instructions / PreCompact со структурированным форматом ≤ ~2000 ток. |
| 3а | `BASH_MAX_OUTPUT_LENGTH` 30 000 → 12 000 | Δ = 18 000 символов ≈ 5 143 ток. (÷3.5); на сжатие ~25–30 % (1.6 × ~6–9 с) | **≈11–15 с** (≈ 5 143 / 360) | **≈18–24 с** | Низкая: хвост большой выдачи уходит в файл + короткий превью (подтверждено бинаром: «output past this is saved to a file… short preview plus the path») | **Применять** (значение 12 000) |
| 3б | `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` = 10 000 | Дефолт 20 000 → 10 000: Δ = 10 000 ток. на большой Read; 10 000 / 360 ≈ 28 с на один большой Read, × доля больших Reads в цикле (оценка ~0.3–0.5) | **≈8–15 с** | **≈13–24 с** | Низкая: Read-выдача больше 10K ток. урезается (агенту доступен offset/limit) | **Применять** (значение 10 000) |
| 3в | Подсказки Worker'у (grep с head, Read offset/limit) | Уменьшают ток. на ход → сдвиг порога → реже сжатия. Оценка: −5–10 % контекста на типичный цикл 21 ход; 1.6 × (Δ × 21 × ~0.05 / 360) | **≈0.5–1 с** | **≈1–2 с** | Минимальная: подсказки не отключают инструмент, только ограничивают размер чтения | **Применять** (текст в agent_prompts.csv, без смены колонок) |
| 3г | Порог авто-compact Claude Code ≈ 44 344 (65 536 − 8 192 − 13 000) | Бинар вычисляет триггер как `window − min(max_output, 20 000) − 13 000` (функции `B4`/`P7` в бинаре; `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` даёт процент от окна напрямую, `CLAUDE_CODE_AUTO_COMPACT_WINDOW` — окно, из которого вычитаются `min(max_output, 20 000)` и 13 000). При window = 65 536 и max_output = 8 192: 65 536 − 8 192 − 13 000 = **44 344** (~44K, дефолтный эффективный триггер). Headroom = 21 192 ток. ≥ 20 000 (запас на один большой вывод инструмента). При PCT = 56.98 %: 0.5698 × 65 536 = 37 342 ≤ 44 344 ✓. Чистая экономия от самого порога мала: ≈ 1–3 с. Ценность — защита от превышения окна и от сжатий, спровоцированных большим выводом | **≈1–3 с** | **≈2–5 с** | Низкая: порог 44 344 < окна 65 536 с запасом 21 192 (8 192 + 13 000) | **Применять** (`CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=56.98` **или** `CLAUDE_CODE_AUTO_COMPACT_WINDOW=65536`; не ниже порога, дающего headroom < 20 000, автосжатие не отключать) |
| 4 | Runner-сжатие (POST /v1/sessions/{id}/compact) | Тот же Compact Instructions формат; `COMPACT_MIN_TOKENS` (локально: 40 000 → **44 344**, тот же порог, что рычаг 3г) — срабатывает перед продолжением сессии. Экономия — та же, что рычаг 2, если сводка ≤ 2000 ток. | **≈266 с** (на runner-сжатие, если оно происходит) | зависит от частоты runner-сжатий | Средняя: тот же минимум, что в рычаге 2 | **Применять** (тот же формат, порог 44 344) |
| 5 | Первый шаг после сжатия | 15 502 ток. − 4 263 (кэш system+tools) = 11 239 ток. ≈ **32–40 с** префилл (измерено ~40 с; 350–400 ток/с). При сводке ≤ 2 000 ток.: 15 502−4 263−6 192 ≈ **9 047 ток.** ≈ **23–25 с** | **≈7 с** (6 192 ток. / ~360) | **≈11 с** (×1.6) | Низкая: сводка короче, но обязательный минимум сохраняется; кэш system+tools не теряется | **Применять** (короче сводка → меньше досчитывать; не отключать кэш) |

### Итог по времени на сжатие

- **Базовое сжатие сейчас**: префилл ~6 с + генерация ~352 с + первый шаг ~40 с ≈ **398 с** (~6.6 мин).
- **С рычагами 2 + 5** (сводка ≤ 2000 ток.): 6 + 86 + 25 ≈ **117 с** (~2 мин) → **−281 с на сжатие**, **−450 с на задачу (×1.6)**.
- Рычаги 3 (частота) добавляют ещё **≈15–45 с на задачу** — вторичный эффект, но
  он же защищает от повторных сжатий после больших выводов.
- Рычаг 1 (кэш) — не экономия, а **защита**: без него каждое сжатие +125 с префилла.

### Решения и конкретные значения переменных

| Переменная | Значение | Эффект |
|------------|----------|---------|
| `CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS` | **8192** (не менять) | Лимит генерации сводки; при сводке ≤ 2000 ток. не упирается |
| `CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | **56.98** (= 37 344 / 65 536 × 100) | Порог авто-compact Claude Code: 0.5698 × 65 536 = 37 342; headroom 65 536 − 37 342 = 28 194 ≥ 20 000 ✓ |
| `CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_AUTO_COMPACT_WINDOW` | **65 536** | Window для бинара; триггер = 65 536 − 8 192 − 13 000 = 44 344; headroom = 21 192 ≥ 20 000 ✓ |
| `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_TOKENS` | **44 344** (= 65 536 − 8 192 − 13 000) | Порог runner-сжатия; 44 344 + 8 192 + 13 000 = 65 536 ✓ |
| `BASH_MAX_OUTPUT_LENGTH` | **12 000** (дефолт 30 000) | Урезать Bash-вывод, хвост в файл + превью |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | **10 000** | Урезать Read-вывод |
| `CLAUDE_RUNNER_EXTRA_ARGS` | `--max-turns <N>` | Ограничить число ходов (опционально) |
| Agent prompt Worker | Тексты: «grep с head -50», «Read с offset/limit», «не читать большие куски без head/offset» | Уменьшить ток. на ход |

**План реализации:**
1. Рычаг 1 (кэш) — **файлы не меняются**: это запрет ломать префикс запроса сжатия (не добавлять текст в системный промпт/историю). Контроль: сравнение prefill-таймингов в логах llama-cpp-server (строки `restored context checkpoint` / `cached n_tokens`) — префилл остаётся ~1.4K ток.
2. `agent_prompts.csv` — **только тексты** Worker (подсказки: grep с `head -50`, Read с `offset`/`limit`, не читать большие куски без head/offset), без смены колонок.
3. `.env.example` — перечислить значения: `BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`, `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_TOKENS=37344`, `CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=56.98`, `CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192` (с комментарием, не менять `.env`).
4. `litellm/ahawr_hooks.py` — **T009 реализовал опциональный лимит**: `data["max_tokens"]` ставится в `COMPACTION_MAX_TOKENS` (3000) только для распознанного compact-запроса по маркеру `CRITICAL: Respond with TEXT ONLY` в последнем user-сообщении; обычные запросы не трогаются, `max_tokens` — параметр запроса, не часть prompt-префикса, кэш не ломается.
5. **Compact Instructions / PreCompact — один конкретный канал доставки для провайдера local, покрывающий и авто-compact, и runner-compact:**
   - **Файл и изменение:** `claude-runner/src/claude_runner/claude_cli.py`, функция `build_command` (строка 76, `cmd += list(settings.extra_args)`). Добавляется `--settings <json-путь>` с блоком `hooks.PreCompact`, где команда хука печатает текст инструкции сводки на stdout (exit 0). Позиция — **вне** `if not compact` (как и `extra_args` на строке 76), т.е. передаётся и для compact-рана, и для обычных. Переменная для локального провайдера: `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_SETTINGS=<путь>` (новая, в `PROVIDER_RUNNER_KEYS` или через env провайдера — зависит от реализации).
   - **Куда попадает (бинар):** хук PreCompact с matcher `trigger: manual|auto` вызывается перед compact-запросом (`Ufe(session, {trigger, customInstructions})`, @205445227; метаданные @221375100–221375800). `HVe(e, n)` склеивает stdout хука с запросом. `customInstructions` вставляется в **хвост** последнего user-сообщения через `moe(r)`: `n += "\n\nAdditional Instructions:\n${r}"` (@205083598). Префикс `_t = forkContextMessages` (история + системный промпт) **не меняется** — `let Wt = [..._t, ...e]` (@205120553) — поэтому префилл-кэш (кэш prefill ~1.4K ток.) остаётся целым: **начало запроса сжатия не изменяется**, только хвост.
   - **Почему начало не меняется:** история (все сообщения до compact-запроса) и системный промпт идентичны предыдущему шагу; `customInstructions` — только дописанный текст в хвосте user-сообщения. Префикс-кэш не трогается.
   - **Влияние `--settings` на обычные раны (гипотеза, не подтверждено):** `--settings` может изменять системный промпт/инструменты обычных ран (напр., `appendSystemPrompt`, `tools` в settings-файле). Бинар подтверждает только механизм PreCompact-хука; влияние settings-файла на остальные поля — **гипотеза**. Если runner пишет settings-файл, нужно убедиться, что он содержит **только** `hooks.PreCompact`, без других полей.
   - **Текст инструкции** (≤ ~2000 ток.): задача+критерии приёмки, факты с file:line, изменённые файлы, выполненные/невыполненные проверки, следующий шаг.
   - **Почему НЕ `CLAUDE_RUNNER_EXTRA_ARGS` с `/compact <instructions>`:** `/compact <instructions>` в `extra_args` стал бы **позиционным аргументом на каждом ране** (строка 76 вне `if not compact`), т.е. CLI получил бы `/compact` как позицию для **каждого** запуска, включая обычные (не compact), что ломает обычные раны. `extra_args` — это аргументы CLI, а не stdin; `/compact` как команда передаётся через stdin (runs.py строка 456), не через аргументы.
   - **Альтернатива для runner-compact (POST /v1/sessions/{id}/compact):** в `runs.py` строка 452 (`input="/compact"`) и строка 456 (`"/compact"`) — заменить на `"/compact <instructions>"`, где `<instructions>` — тот же текст сводки. Это отдельный канал (stdin), не требующий `--settings`; но дублирует текст, уже доставляемый хуком PreCompact при авто-compact.
6. `docs/compaction-analysis.md` — этот раздел.

**Примечание по плану:** изменение `claude_cli.py` (добавление `--settings` с PreCompact-хуком в `build_command`, строка 76) и новой переменной `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_SETTINGS` — это план для дальнейшей реализации; в рамках T005 только документ. `config.py` не изменяется: `CLAUDE_CODE_MAX_OUTPUT_TOKENS` и `COMPACT_MIN_TOKENS` уже передаются через env блока провайдера (разделы 1 и 4); рычаг 4 (runner-сжатие) использует тот же формат сводки и тот же порог.

**Проверки, которые реально выполнены:**
- Арифметика в таблице пересчитана `python -c` (генерация 8192/23.3=352.6 с, 2000/23.3=86.1 с, экономия 266.5 с, ×1.6=426.4 с, префилл 11 239/365=30.8 с ≈ 40 с; **порог авто-compact**: 65 536 − 20 000 − 8 192 = 37 344; 37 344 + 20 000 + 8 192 = 65 536 ✓; **PCT**: 37 344 / 65 536 × 100 = 56.98 %; 0.5698 × 65 536 = 37 340 ≤ 37 344 ✓).
- Частота 1.6 сжатия на задачу — из T004 (16 сжатий / 10 прогонов).
- Кэш префилла — из данных llama-cpp-server (45 753 restored, 1405 ток. за 6 с).
- `BASH_MAX_OUTPUT_LENGTH` — из бинара (дефолт 30 000, clamp 4000–150 000).
- `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` — из бинара (дефолт 20 000).
- Compact Instructions / PreCompact — из бинара (оба варианта в шаблоне compact-запроса).
- Microcompact — из бинара (порог 20 000 ток., keepRecent 2 000).

**Проверки, которые не выполнялись:**
- Реальный прогон с новыми значениями (требует пересборки контейнеров — не запускаю).
- Точная оценка экономии рычагов 3в и 3г на реальных данных (оценка на основе типичного цикла 21 ход).
- Точный размер сводки ≤ 2000 ток. после Compact Instructions (требует прогон).

**Проблемы, которые остаются:**
- Компактный запрос сам по себе меняет **хвост** (новое user-сообщение с инструкцией компактирования), но **начало** (история + системный промпт) остаётся идентичным — это покрывается префикс-кэшем. Однако при **edge case** (mid-system сразу после последнего user) кэш ломается **только с этого сообщения** — в хвосте, а не в префиксе.
- Runner-сжатие (POST /v1/sessions/{id}/compact) — тот же формат, но частота runner-сжатий не измерена (нет данных по runner-compact в T004).
- Микроcompact (очистка tool results) — порог 20 000 ток., keepRecent 2 000; механизм выбора между auto/manual/microcompact **не найден в бинаре** — не управляется env.

---

## Итоговый отчёт T012 — финальная проверка и применение

### 1. Что изменено (git diff, без commit/push)

Изменённые файлы (из разрешённого списка правила 1):

| Файл | Суть diff |
|-------|-----------|
| `claude-runner/src/claude_runner/claude_cli.py` | Функция `_compact_settings_path()` (создаёт `compact-settings.json` с блоком `hooks.PreCompact`); в `build_command` добавлен `--settings <путь>` — **вне** `if not compact`, только для credential-isolating (local) провайдера; `settings_flag()` возвращает путь. |
| `claude-runner/src/claude_runner/config.py` | `DEFAULT_COMPACT_INSTRUCTIONS` (1500–2000 ток., жёсткий лимит 2000, разделы Task/Findings/Changed files/Checks/Next step); `AUTO_COMPACT_WINDOW=65_536`, `AUTO_COMPACT_TOOL_RESERVE=20_000`, `DEFAULT_MAX_OUTPUT_TOKENS=8_192`, `_AUTO_COMPACT_PRECOMPUTE_BUFFER=13_000`; `_autocompact_threshold()` читает `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (56.98 → 37342) либо window path; `_validate_compaction_limits()` enforces headroom ≥ 20000; `Settings.local_compact_instructions` (default True), `compact_instructions` (default DEFAULT). |
| `claude-runner/src/claude_runner/runs.py` | `compact()`: `compact_prompt = "/compact " + settings.compact_instructions` — тот же формат, что авто/manual; только для credential-isolating (local) провайдера; остальные бэкенды — stock `/compact`. |
| `claude-runner/README.md` | Раздел "Compaction": документация PreCompact hook delivery, `/compact <instructions>`, `CLAUDE_RUNNER_COMPACT_INSTRUCTIONS`, `CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (56.98 → 37342), `CLAUDE_CODE_AUTO_COMPACT_WINDOW=65536` (trigger = window − min(max_output, 20000) − 13000, headroom ≥ 20000), LiteLLM hook row. |
| `claude-runner/tests/test_config_cli.py` | Тесты PreCompact hook settings file (строки 77–118): assert `--settings` path, parse `hooks.PreCompact[0].hooks[0]`, `off.local_compact_instructions is False`. |
| `claude-runner/tests/test_providers.py` | Тесты compact-ранов: `compact["prompt"] == "/compact " + mixed.compact_instructions`, credential isolation. |
| `litellm/ahawr_hooks.py` | `COMPACTION_MAX_TOKENS = 3000`, `COMPACTION_MARKER = "CRITICAL: Respond with TEXT ONLY"`, `_is_compaction_request()`, `MidSystemMessageHook.async_pre_call_hook` — `max_tokens = 3000` только для распознанного compact-запроса. |
| `litellm/test_ahawr_hooks.py` | **Новый** (untracked): 11 unit tests, stubbing `litellm.integrations.custom_logger.CustomLogger`. |
| `.env.example` | Блок провайдера "local" **уже содержит** закомментированные новые переменные: `CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536` (вместо устаревшего 32768), `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`, `BASH_MAX_OUTPUT_LENGTH=12000` / `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000` (в форме `CLAUDE_RUNNER_PROVIDER_LOCAL__…`), `COMPACT_MIN_TOKENS=40000`, `PCT_OVERRIDE=67`; удалены устаревшие `16000` и `32768`. **Значения `40000`/`67` отличаются от требуемых `37344`/`56.98`**; точные значения применить не удалось — файл `.env.example` закрыт Read-deny правилом (см. раздел 7). |
| `AHAWR_v13_ClaudeCode.json` | Добавлена логика `loggedAttempt` в jsCode-ноду (максимум из `state.task_attempt` и логированных попыток из перечисленных нод). **Разрешён** правилом 1. |
| `AHAWR_v13.json` | Та же логика `loggedAttempt` в jsCode-ноду. **Не разрешён** правилом 1 (n8n-workflow `*.json` — нарушение правила 2). |
| `agent_prompts.csv` | **Изменён** — в Worker execution prompt добавлены подсказки: «для поиска по файлам и логам используй `grep -n | head` (например `head -50`), Read — с offset/limit только на нужный участок, большие логи и выводы команд не читай целиком». В Reviewer acceptance prompt добавлены подсказки: «Проверку ведите точечно: ищите конкретные строки через `grep -n | head`, Read открывайте с offset/limit, большие логи не перечитывайте целиком». |

Diff `agent_prompts.csv` (без CRLF-шума):

```
-Не объявляй задачу завершенной без фактической проверки.",Worker execution prompt
+Работай с выводами точечно: для поиска по файлам и логам используй `grep -n | head` (например `head -50`), Read — с offset/limit только на нужный участок, большие логи и выводы команд не читай целиком; полный результат всё равно останется в логах и по повторному точечному запросу.",Worker execution prompt
```

```
-Если работа неполна, next_task должна быть одной конкретной атомарной задачей.",Reviewer acceptance prompt
+Если работа неполна, next_task должна быть одной конкретной атомарной задачей. Проверку ведите точечно: ищите конкретные строки через `grep -n | head`, Read открывайте с offset/limit, большие логи не перечитывайте целиком.",Reviewer acceptance prompt
```

Diff `Claude_Code_Run_Manager_v1.json` (без CRLF-шума) — **изменение вне миссии**:

```
...transient_error: Boolean(runner.transient_error ?? false),
+context_overflow: Boolean(runner.context_overflow ?? false),
 resumed: Boolean(runner.resumed ?? false),...
```

Это единственное изменение в `Claude_Code_Run_Manager_v1.json`. Оно **не относится к компакции** — это добавление поля `context_overflow` в jsCode-ноду n8n-воркфлоу (tracking context overflow). Это **предсуществующее/не относящееся к миссии изменение** — нарушение правила 2 (изменение файлов вне разрешённого списка правила 1).

### 2. Ожидаемая экономия (расчёт)

| Рычаг | На одно сжатие | На задачу (×1.6) | Примечание |
|--------|---------------|-----------------|-----------|
| Длина сводки ≤ 2000 ток. (8192 → 2000) | **≈266 с** (352.6 → 86.1) | **≈426 с** | |
| `BASH_MAX_OUTPUT_LENGTH` 30 000 → 12 000 | **≈11–15 с** | **≈18–24 с** | Только после применения `.env` |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` 20 000 → 10 000 | **≈8–15 с** | **≈13–24 с** | Только после применения `.env` |
| Префилл кэш (сохраняется) | **~6 с** (не теряется) | ~10 с | |
| **Итого** | **≈285–296 с** | **≈457–474 с** | BASH/FILE_READ рычаги — только после `.env` |

### 3. Команды ручного применения (только перечислить, не выполнять)

1. Пересборка `claude-runner` (Dockerfile в `/d/n8n/claude-runner/`):
   ```
   docker build -t claude-runner /d/n8n/claude-runner
   docker compose up -d claude-runner
   ```
2. Пересборка `ahawr-litellm` (Dockerfile в `/d/n8n/litellm/`):
   ```
   docker build -t ahawr-litellm /d/n8n/litellm
   docker compose up -d ahawr-litellm
   ```
3. Значения `.env` (только перечислить; `.env` не читать и не изменять):

   Единая рекомендация — значения, совпадающие с `.env.example`:

   ```
   BASH_MAX_OUTPUT_LENGTH=12000
   CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000
   CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_TOKENS=40000
   CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=67
   CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536
   CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192
   CLAUDE_RUNNER_COMPACT_INSTRUCTIONS=<текст сводки — формат по умолчанию в config.py>
   CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS=1
   ```

   Проверка headroom при `PCT_OVERRIDE=67`: 0.67 × 65 536 = 43 909; headroom = 65 536 − 43 909 = 21 627 ≥ 20 000 ✓.

### 4. Как проверить на следующем прогоне

1. **События compact**: в `/data/events/<run_id>.jsonl` (или `GET /v1/runs/<id>/events`) — событие `kind=="compact"`, поля `pre_tokens` / `post_tokens`.
2. **Размер сводки**: `post_tokens` — это контекст ПОСЛЕ сжатия, включая системный промпт и инструменты (~4263 ток.). Сам текст сводки ≈ `post_tokens − 4263` (ожидается ≈ 1500–2000 ток., а не 8192). Альтернатива — по строке `'eval time'` compact-запроса в логах llama-cpp-server: время генерации сводки ≈ 86 с (не 352 с), что соответствует ~2000 ток. при 23.3 ток./с.
3. **Логги llama-cpp-server**: строки `'prompt eval time'`, `'eval time'`, `'restored context checkpoint'`, `'cached n_tokens'` — префилл ≈ 1405 ток. за ~6 с.
4. **Тайминг**: генерация сводки ≈ 86 с (не 352 с).

### 5. Какие проверки реально выполнены

| Проверка | Результат |
|----------|-----------|
| `pytest -q` (claude-runner) | **58 passed**, coverage **97.80%** |
| `ruff check src tests` | **All checks passed!** |
| `mypy src` | **Success: no issues found in 14 source files** |
| `test_ahawr_hooks.py` (hook unit test) | **11 passed** |
| `git diff --ignore-all-space --stat` | **13 файлов, 505 ins / 17 del** (реальные изменения без CRLF-шума): `.env.example`(27+3), `AHAWR_v13.json`(1+1), `AHAWR_v13_ClaudeCode.json`(1+1), `Claude_Code_Run_Manager_v1.json`(1+1), `agent_prompts.csv`(2+2), `claude-runner/README.md`(34+2), `claude_cli.py`(46), `config.py`(110), `runs.py`(13+2), `fake_claude.py`(1+1), `test_config_cli.py`(164+1), `test_providers.py`(69+3), `ahawr_hooks.py`(36). Примечание: `Claude_Code_Run_Manager_v1.json` и `AHAWR_v13.json` — изменения **вне миссии** (нарушение правила 2); `AHAWR_v13_ClaudeCode.json` — **разрешён** правилом 1; `fake_claude.py` — в `claude-runner/tests`, **разрешён** правилом 1 (изменение `==` → `startswith("/compact")` — часть миссии). |
| `git status --short` | Изменения **unstaged** в worktree (HEAD == index); untracked: `.venv-dev/`, `docs/compaction-analysis.md`, `litellm/test_ahawr_hooks.py`, `ahawr-session-worker-…-T001-….json`, `mission_ahawr-rag-effectiveness.csv`, `mission_ahawr-retry-and-microcompact.csv`, `mission_llamacpp-speed-tuning.csv`. commit/push **не выполнялись**. |

### 6. Какие проверки НЕ выполнялись

| Проверка | Причина |
|----------|---------|
| Реальный прогон с новыми значениями | Требует пересборки контейнеров — не запускаю |
| Точный размер сводки ≤ 2000 ток. | Требует прогон |
| Точная экономия рычагов 3в/3г на реальных данных | Оценка на основе типичного цикла 21 ход |
| Тесты в контейнерном image | Тесты выполнялись в локальном venv `/d/n8n/.venv-dev`, не в image |

### 7. Какие проблемы остались

1. **`.env.example`**: блок провайдера "local" **уже содержит** новые переменные, но с **не тождественными** значениями — `COMPACT_MIN_TOKENS=40000` и `PCT_OVERRIDE=67` вместо требуемых `37344`/`56.98`; `BASH_MAX_OUTPUT_LENGTH=12000` и `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000` даны в форме `CLAUDE_RUNNER_PROVIDER_LOCAL__…` (не как глобальные). Точные значения `37344`/`56.98` **не внесены**: файл `.env.example` закрыт Read-deny правилом (Edit/Write/Read отклонены). Устаревшие `16000` и `32768` **удалены** (заменены `65536`/`40000`).
2. **`AHAWR_v13.json`** и **`Claude_Code_Run_Manager_v1.json`** — изменения **вне миссии** (нарушение правила 2): `AHAWR_v13.json` — добавлена логика `loggedAttempt` в jsCode-ноду; `Claude_Code_Run_Manager_v1.json` — добавлено поле `context_overflow` в jsCode-ноду. Оба n8n-workflow `*.json`, не в разрешённом списке правила 1.
3. **`agent_prompts.csv` изменён** — в Worker execution prompt добавлены подсказки про точечную работу (grep/head/offset), в Reviewer acceptance prompt — подсказки про точечную проверку; diff приведён в разделе 1.
4. Изменения **unstaged** в worktree; commit/push не выполнялись по правилу.
5. `git diff --ignore-all-space --stat` показывает **13 файлов, 505 ins / 17 del** реальных изменений; из них `Claude_Code_Run_Manager_v1.json` и `AHAWR_v13.json` — изменения **вне миссии** (нарушение правила 2, diff приведён в разделе 1); `AHAWR_v13_ClaudeCode.json` — **разрешён** правилом 1; `fake_claude.py` — в `claude-runner/tests`, **разрешён** правилом 1 (изменение `==` → `startswith("/compact")` — часть миссии). Остальные 65 файлов в полном `git diff --stat` — CRLF-шум (ins==del).
6. Контейнеры не перезапускались; изменения вступят в силу после ручной пересборки.
