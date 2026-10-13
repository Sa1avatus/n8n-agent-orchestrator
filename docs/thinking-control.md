# Thinking of the local model: switches and prompt cache

The local Worker model (Qwen 3.8, served by llama.cpp through LiteLLM) can think or not. Three pieces work together; all of
them are optional and the stack runs without them.

## 1. Per-request switch (no restart): `litellm/runtime/local_thinking`

- File content `on` (or no file) = the llama preset decides (reasoning on, budget 4096, format deepseek).
- File content `off` = LiteLLM adds `chat_template_kwargs: {"enable_thinking": false}` to every request for a non-Claude model
  (`litellm/ahawr_hooks.py`, read on each request, at most 2 s old). Claude models are never touched.
- Set it by writing the file. `docker-compose.yml` mounts `./litellm/runtime` read-only into LiteLLM and read-write into
  `claude-runner`.

## 2. Automatic switch for a long task: `claude_runner/think_switch.py`

- `AHAWR_THINK_OFF_AFTER_SECONDS` (default 3600, `0` = never; see `.env.example`).
- A Worker session is one task. When the runs of a local Worker session add up to more than the limit and the task is still
  running, the runner writes `off` to the flag file (and a marker `local_thinking.auto` next to it); the next model call of the
  same run already goes without thinking. A `thinking_switch` event is added to the run.
- It writes `on` back only when a local Worker run of another, shorter session starts (the next task), and only if the runner
  itself wrote `off`. A manual `off` is never touched. Between two attempts at the same task the flag stays as it is.

## 2b. Policy for the local Worker: first attempt without thinking, retry with it

- `off_first`: the first attempt at a task runs without thinking; when the Reviewer sends the task back (`needs_changes`) the retry runs
  with thinking, for at most the hour limit counted over the retries only; after that it goes off again. A resume after a failed run is
  still the first attempt. A compaction run never counts as an attempt.
- The runner recognises the task by the prompt (`TASK T<n>` / `RESUME TASK T<n>`) and the attempt by "has a Worker run of this session
  already completed" (a completed run went to the review).
- Settings, strongest first: the columns of the `hermes_config` row (`worker_think_policy` = `off_first` or empty,
  `worker_think_policy_from_task` = first task number the policy applies to, `worker_think_off_after_seconds` = the hour limit; empty / 0 /
  -1 = not set) are read by the workflow (`Constants`), passed through the Run Manager to `POST /v1/runs` (`think_policy`,
  `think_policy_from_task`, `think_off_after_seconds`) and stored with the run; then the defaults in `.env`
  (`AHAWR_THINK_POLICY`, `AHAWR_THINK_POLICY_FROM_TASK`, `AHAWR_THINK_OFF_AFTER_SECONDS`).
- Step limit next to the time limit: `hermes_config.worker_think_off_after_steps` (run field `think_off_after_steps`, default
  `AHAWR_THINK_OFF_AFTER_STEPS`; 0 = none, empty / -1 = use the default). A step is one model call; the runner counts the `step` entries of
  the run event logs of the task's session (under `off_first` only the retries, like the time limit). Whichever limit is reached first
  turns thinking off (reason `retry over N steps` / `retry over the time limit` in the `thinking_switch` event). Measured on 2026-10-13: a
  retry with thinking takes about 15-25 steps, so 40 leaves room but stops circling.
- `data-tables/ops/think_config_patch.py` patches the workflows (Constants, Build Worker Run Input in AHAWR v13; Normalize Request and the
  three request nodes in the Claude Code Run Manager); `launch_mission.sh` applies it on every start. After adding the three columns to the
  `hermes_config` table (`hermes_config.example.csv` has them) the values take effect for the next Worker run after a workflow restart.

## 3. Same prompt prefix with thinking on and off (keeps the llama.cpp prompt cache)

The stock Qwen 3.8 chat template adds the system paragraph "Reasoning effort is set to xhigh..." only when thinking is on. The
prompt then differs from the very first characters, so every switch inside one session made llama.cpp evaluate the whole
context again (52,217 tokens = 215 s measured on 2026-10-12): the state of every token depends on all tokens before it, and the
model's recurrent layers cannot be cut in the middle.

`llama/templates/qwen38-same-prefix.jinja` is the stock template with one change: the condition
`{%- if enable_thinking is undefined or enable_thinking is true %}` became `{%- if true %}` (the effort paragraph is always
emitted). `qwen38-original.jinja` is the stock template as the server reported it (`GET /props`). With the changed template
the prompts of both modes are identical up to the generation prompt (`<think>\n` against `<think>\n\n</think>\n\n`), so a
switch costs only the tail.

Setup (llama.cpp project: `docker-compose.yml` and the preset file):

```yaml
    volumes:
      - <path to this repo>/llama/templates:/templates:ro
```

```ini
[your-qwen3.8-preset]
jinja = true
chat-template-file = /templates/qwen38-same-prefix.jinja
```

The mount needs the container to be recreated (`docker compose up -d <service>`), not just restarted. Check with
`POST /apply-template?model=<id>` and `chat_template_kwargs.enable_thinking` true and false: the two prompts must share
everything except the end. No change to llama.cpp itself is needed (`--chat-template-file` is a standard option).

Downside: with thinking off the system text still says "think carefully"; the empty `<think></think>` block in the generation
prompt is what stops the model, so the behaviour should be watched on a few steps.

## 4. Prompt-cache diagnostics: `litellm/cache_trace.py`

`AHAWR_CACHE_TRACE=1` (default in `docker-compose.yml`) makes the LiteLLM hook compare every request to a local model with the
previous request of the same conversation and append one JSON line to `litellm/trace/cache_trace.jsonl` (git-ignored, rotated
at 50 MB): `first_diff` is the index of the first message that differs (`null` = the request only extends the previous one),
with its kind, the text around the first differing character (`old`/`new`), `redo_tokens_est` (what llama.cpp has to evaluate
again, about a third of the characters from there to the end), `tools_changed` and `thinking_off`. The request itself is never
changed. `cache_control` markers (they move from request to request and are not part of the prompt) and one text block against
a plain string are not counted as differences. Use it to find what breaks the cache: reminders inserted in the middle of the
history, results of old tools being trimmed, thinking dropped from earlier steps, a changed tool list.

---

## Кратко по-русски

Размышление локальной модели: (1) файл `litellm/runtime/local_thinking` (`on`/`off`) читается LiteLLM на каждом запросе, без
перезапуска; (2) runner сам пишет `off`, когда задача (сессия Worker) идёт дольше `AHAWR_THINK_OFF_AFTER_SECONDS` (по
умолчанию 3600), и возвращает `on` при старте следующей задачи; (3) шаблон `llama/templates/qwen38-same-prefix.jinja` делает
начало промпта одинаковым при включённом и выключенном размышлении, поэтому переключение не сбрасывает кэш llama.cpp (без
него: полный пересчёт контекста, 215 с на 52k токенов). Подключение: монтаж `templates` в контейнер llama и строка
`chat-template-file = /templates/qwen38-same-prefix.jinja` в пресете; контейнер пересоздаётся, а не перезапускается.
