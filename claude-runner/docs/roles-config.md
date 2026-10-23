# Roles config (roles_json)

`roles_json` chooses the model for each role and attempt. It exists in two places:

- globally, as a column of the `hermes_config` row (the `claude-code` profile);
- per mission, in `mission_config.config_json` under the key `roles_json` (table `board.mission_config`, one row per `mission_id`).

Readers tolerate the `roles_json` column being absent: a missing column or key is read as "no roles", and the
next precedence level applies. No reader may fail because the column does not exist.

## Format

### Model

A model is one string `provider/model`, split at the first slash:

- `anthropic/haiku` -> provider `anthropic`, model `haiku`;
- `openrouter/deepseek/deepseek-chat` -> provider `openrouter`, model `deepseek/deepseek-chat`.

### Role

A role is either a string (one model for every attempt) or a map from attempt number to model string:

- keys are attempt numbers as strings: the first key is `"1"`, keys are strictly increasing integers;
- the value for a key holds from that attempt up to the next key;
- the last key holds for every later attempt.

Example from the mission, `{"1": "A/x", "3": "B/y", "4": "C/z"}`:

| Attempt | Model |
|---|---|
| 1 | `A/x` |
| 2 | `A/x` (key `"1"` holds up to the next key) |
| 3 | `B/y` |
| 4 and later | `C/z` (last key holds for every later attempt) |

#### Examples

- `{"worker": {"1": "anthropic/haiku", "2": "local/qwen3.8-27b-gsq-rco-iq2-s-mtp", "3": "openrouter/deepseek/deepseek-chat", "4": "anthropic/sonnet"}}` —
  attempt 1 on `anthropic/haiku`, attempt 2 on `local/qwen3.8-27b-gsq-rco-iq2-s-mtp`,
  attempt 3 on `openrouter/deepseek/deepseek-chat` (provider `openrouter`, model `deepseek/deepseek-chat`),
  attempts 4 and later on `anthropic/sonnet` (the last key holds for every later attempt).
- `{"worker": {"1": "anthropic/haiku", "2": "local/qwen3.8-27b-gsq-rco-iq2-s-mtp"}}` — cloud first, retries local;
  this reproduces `cloud_first_patch.py` and the `cloud_first.on` switch: attempt 1 on `anthropic/haiku`,
  attempts 2 and later on `local/qwen3.8-27b-gsq-rco-iq2-s-mtp`.
- `{"report": {"1": "anthropic/haiku", "3": "anthropic/sonnet"}}` — attempts 1 and 2 on `anthropic/haiku`
  (key `"1"` holds up to the next key), attempts 3 and later on `anthropic/sonnet`.
- `{"reviewer": "anthropic/sonnet"}` — `anthropic/sonnet` on every attempt.
- `{"1": "anthropic/haiku", "2": "local/qwen3.8-27b-gsq-rco-iq2-s-mtp", "options": {"fresh": "auto", "think": "off"}}` —
  a full role object: attempts 1 and 2 on the models above, and the reserved key `options`
  carries per-role settings instead of a model for an attempt.

Migration example from `cloud_first_patch.py` and the `cloud_first.on` switch (a cloud model on the first attempt,
the local model on the retry):

```json
{"1": "anthropic/haiku", "2": "local/<model>"}
```

Here `<model>` is the local model name configured for the local profile; attempt 2 and later use it.

### Escalation block `<role>_escalate`

A role may have a second key, `<role>_escalate` (today used for `worker`). It moves a task to a stronger
model after the task has used many steps. The attempt chain above stays plain: the block only overrides
the model of the chain when its condition holds.

```json
{"worker": {"1": "anthropic/claude-haiku-5-5", "2": "local/qwen3.8-27b-gsq-rco-iq2-s-mtp", "4": "anthropic/sonnet"},
 "worker_escalate": {"model": "anthropic/sonnet", "above_steps": 100, "below_score": 50}}
```

- `model`: the model used when the block fires;
- `above_steps`: the step limit (a whole number, at least 1);
- `below_score`: the review score under which a retry escalates (above 0, up to 100); optional, without it
  the steps alone decide.

The block is read when an attempt starts and fires in two cases, never in the middle of a run (stopping a
run is the job of the adaptive split):

1. **Retry of the same task.** The previous run used more than `above_steps` steps and the review score is
   below `below_score`. A run that ended at 110 steps with score 40 sends the next retry to `model`; the same
   run with score 70, or a run of 60 steps, does not.
2. **Split part.** The task is a part made by an adaptive split that was forced by more than `above_steps`
   steps (the split writes `split_steps` into the part). The whole part runs on `model`, from its first
   attempt to its last, and the score is not checked.

Facts come from the workflow: the score is `last_review_score` of the Live State row, the steps are
`num_turns` of the previous run read from the runner (`GET /v1/runs/<id>`), and `split_steps` is a field of the
task. A fact that is missing (the runner does not answer, no previous run) counts as unknown and the block does
nothing. `fresh: auto` starts a fresh session whenever the model changes.

### Reserved key `options`

A role map may contain the key `options`, which is not an attempt number. It holds per-role settings:

- all roles: `fresh`, `think`, `max_polls`, `poll_seconds`, `max_retries`;
- `qa` only: also `mode`, `max_minutes`, `max_cost_usd`.

### Attempt meaning per role

- `worker`: a task attempt;
- `reviewer` and `qa`: an attempt of the Worker output judged;
- `architect`: a planning retry;
- `report`: an attempt of the report task.

The role names are `architect`, `worker`, `reviewer`, `service`, `qa` and `report`; more can be added
without a schema change.

### Session rule for `fresh`

`options.fresh` takes `auto` (the default), `false` or `true`:

- `auto`: a fresh runner session when the model or provider differs from the previous attempt of the
  same role; otherwise the retry continues the session, and the retry input already contains the review
  feedback;
- `false`: always continue the session of the previous attempt;
- `true`: always start a fresh session.

## Precedence

For a role on a given attempt, the first level that defines the role wins:

1. the mission override (`board.mission_config` under the key `roles_json`) replaces the role as a whole;
   keys are not merged with the global role;
2. the global `roles_json` in `hermes_config`;
3. the legacy columns of every role: `architect_*`, `worker_*`, `reviewer_*`, `service_*` provider and model,
   plus the per-role `*_max_polls`, `*_poll_seconds` and `*_max_retries` as the fallback for options not set
   in `roles_json`.

Fallbacks by role: `qa` falls back to `reviewer`; `report` falls back to `worker`.
`max_tasks`, `max_attempts_per_task`, retrieval and Telegram stay outside roles. An empty `roles_json`
or an absent column falls back to the legacy columns.

## Validation

A `roles_json` value is rejected (the write fails, nothing is stored) when:

- it is not a JSON object, or a role value is not a string or an object of attempt keys to strings;
- an array, a range (`"1-2"`, `"4+"`) or an object is used as a model value;
- a provider does not exist in the runner's provider list;
- the model after the first slash is empty;
- an attempt key is not a plain positive integer as a string: the key `"0"` is invalid, the first key
  must be `"1"`, keys must be strictly increasing, and duplicates are invalid;
- an `options` key is unknown; the known keys are `fresh`, `think`, `max_polls`, `poll_seconds` and
  `max_retries`, and `qa` also allows `mode`, `max_minutes` and `max_cost_usd`;
- a `qa` option has a bad value: `mode` is not one of `off`, `final`, `ui_tasks`, `nightly`, or a limit
  is not a positive finite number.

The role map may have at most 10 keys (`options` counts as one). Warnings (not rejections): an unknown
role name, and a config that uses more than 8 distinct models. Every message names the role and the
element it reports on.

## JS helper contract

The pure core of `claude-runner/src/claude_runner/board_roles.js` (the block between `// === pure roles
core` and `// === end pure roles core ===`) has no DOM, fetch or globals. `data-tables/ops/roles_config_patch.py`
copies that block into the run input nodes of the workflow, so the board UI and the workflow share one
resolver.

- Inputs: the `roles_json` text from `hermes_config`, the `mission_roles_json` text from the Mission (both
  parsed with `JSON.parse`; empty is `{}`), the role name (`architect`, `worker`, `reviewer`), the attempt
  (1-based) and the legacy `*_provider` / `*_model` values of the same role.
- Outputs of `resolveRole(roles, role, attempt, legacy)`: `{ provider, model, think, fresh, options }`.
  `mergeRoles(global, mission)` builds the `roles` argument; a mission role replaces the global role as a whole.
- Errors: an unknown key, a bad `fresh` value, or no model for the role and attempt throws. The node fails
  instead of guessing a model.
- Attempts in the workflow: Worker uses the task attempt; Reviewer uses the attempt of the output it judges
  (`Reviewer State.task_attempt`); Architect is fixed at 1 because the workflow has no planning retry counter.
- `fresh` (`"auto"`, `true` or `false`): `"auto"` is true when the model differs from the previous attempt.
  The Worker uses it to start a new session instead of resuming the old one. Architect and Reviewer do not
  use `fresh` or `think` yet: the runner has no field for them, and their sessions are resumed after a poll.
- Fallbacks: a `qa` role without its own entry uses `reviewer`, and `report` uses `worker`. An empty
  `roles_json` falls back to the legacy columns.
- QA and report are not wired by this patch. Their run inputs will call `resolveRole(roles, "qa", attempt,
  legacy)` or `"report"` and get the reviewer or worker model through the fallbacks above.
- Each run input node defines `const __roleConfig` and calls it once with its role and attempt. Re-running the
  patch on a patched node changes nothing.

## QA

The `qa` role judges the Worker output with a QA run (see `qa-acceptance.md`). Its options go under the reserved
key `options`:

```json
{"qa": {"1": "anthropic/sonnet", "options": {"mode": "off", "max_minutes": 20, "max_cost_usd": 1}}}
```

- Model: the `qa` role's own model. If `qa` is not set, the `reviewer` role is used, then the legacy
  `reviewer_*` columns. Only when none of them is set does the model default to `anthropic/sonnet`.
- `mode`: `off` (default), `final`, `ui_tasks` or `nightly`; any other value is rejected.
- `max_minutes`: default `20`; `max_cost_usd`: default `1`. Both must be positive numbers.
- With `roles_json` absent or without a `qa` entry, the role resolves to the defaults above and the mode is `off`.
- `mode` and the limits are accepted only under `qa`; on any other role they are unknown options.

## Roles added by other missions

_To be filled in by the missions that add roles._
