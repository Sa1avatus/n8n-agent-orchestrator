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

## 2c. Step policy (JSON) and variants for tuning

Measured on 326 attempts (`data-tables/ops/think_report.py`): without thinking an attempt that passes does so early (67% under 5 min, 26% at 30-60 min, about 20% after an hour,
28% after 100 steps), thinking helps most in the 15-60 minute range and not at all after an hour, and as a whole first attempt it is slower (4x) and not better. The pass share of
attempts that end within a window of calls falls below 50% at about 22-25 calls (31 for first attempts, 15 for retries). So thinking is used in short stretches.

`hermes_config.worker_think_policy` (also `.env` `AHAWR_THINK_POLICY`) holds a JSON object (the runner reads it at the start of every Worker run; the workflow lower-cases it, so keep names and
modes lower-case). Every variant is a timeline of segments counted in model calls of ONE attempt:

```json
{
  "variants": [
    {"name": "plan+pulse", "weight": 2,
     "first": [["think", 8], ["off", 25], ["think", 10], ["off", null]],
     "retry": [["think", 8], ["off", 25], ["think", 10], ["off", null]]},
    {"name": "control", "weight": 1, "first": [["off", null]], "retry": [["off", null]]}
  ],
  "from_task": 1
}
```

- A segment is `[mode, calls]`: `think` or `off`; `null` calls = until the attempt ends (only the last segment). The example: the first 8 calls think (the plan of the task, or the remarks of the
  review on a retry), 25 calls without thinking, a 10-call burst, then without.
- `first` = the first attempt at a task, `retry` = an attempt after a review (a resume after a failed run belongs to the same attempt). One of them missing = a copy of the other; both missing = never thinks.
- `repeat: true` repeats the timeline when it is over (every segment then needs a number of calls).
- `weight` (1..20, default 1): the share of tasks. Task n uses the variant at position (n - 1) mod (sum of weights) of the variants laid out by weight, so one mission compares the variants. Tasks differ in
  difficulty: read the comparison over many tasks and missions.
- `from_task`: the policy applies from this task number on (default 0 = the workflow's `worker_think_policy_from_task`).
- Calls are counted per attempt from the end of the last completed Worker run (the last review) or from the start of the first attempt. The flag is written when the runner sees the count
  (every 20 s), so a segment can be a call longer or shorter. The prompt prefix stays the same (section 3), so a switch costs only the tail.
- The value is checked before it is stored: `python data-tables/ops/set_think_policy.py policy.json` (`--check` only validates, `--show` prints the stored one). An invalid value that gets in anyway is
  reported in the runner log and the last valid policy keeps working.
- The older text forms still work: `off_first` (section 2b) and `pulse:O:T:P[:fN][:rN]` with `|`-separated variants (O calls without thinking, T with, P pulses, 0 = for ever; `fN` / `rN` = N calls of
  thinking at the start of a first attempt / a retry).
- What to tune: the lengths of the segments (when to start thinking after the opening stretch, how long the burst is, whether the opening stretch is the same for first attempts and retries) and the weights.

### Tuning tools (`data-tables/ops`)

- `think_archive.py` keeps the step records of every local Worker run in `think_steps_archive.jsonl` beyond the runner's 14-day event retention (called by `queue_watch.py` every 6 hours);
  a record has, per model call, the tokens, time, whether the model thought, the tools called (name, short key, error, edit size, pytest result).
- `think_report.py`: statistics by mode, attempt, duration, era and policy variant. `think_model.py`: can the first K calls predict a failure (leave-one-mission-out; on 316 attempts
  AUC 0.6-0.67 at 10-30 calls and no skill after 50, so no model is deployed). `think_uplift.py`: pulse variants against the `off` control (bootstrap intervals; a T-learner
  once every arm has 100 attempts). `think_bandit.py`: Thompson sampling over the variants (passes per hour) and a policy string with matching weights; `--apply` writes it to `hermes_config`.

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
