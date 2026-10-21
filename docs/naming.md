# Naming conventions

How names look in this repository. Applies to new code now and to the cleanup that the code review and
refactoring mission will do before 1.0. A name says WHAT the thing is, in the words of the vocabulary below; it
does not say how it is stored, and it is not shortened to save letters.

## 1. Casing by language

| Thing | Style | Example |
|---|---|---|
| Python module, function, variable, method | `snake_case` | `build_worker_input`, `task_attempt` |
| Python class, exception | `PascalCase` | `GuardSupervisor`, `ConflictError` |
| Python module constant | `UPPER_SNAKE` | `DEFAULT_INTERVAL_S` |
| Python private (module or class) | one leading `_` | `_read_json` |
| JS function, variable, method | `camelCase` | `renderMissionCard` |
| JS module constant | `UPPER_SNAKE` | `BUNDLED_EN` |
| JS class / constructor | `PascalCase` | `BoardStore` |
| File: Python / JS / CSS / HTML | `snake_case` | `board_view_grid.js` |
| JSON field, API field, DB column | `snake_case` | `review_score`, `started_at` |
| API route | `kebab-case`, plural nouns for collections | `/api/board/missions`, `/queue/set-enabled` |
| Environment variable | `UPPER_SNAKE`, prefix per component | `CLAUDE_RUNNER_CLOUD_THINK`, `DASHBOARD_GUARDS` |
| CSS class | `kebab-case`, a short prefix per widget | `kcard`, `bv-note`, `ew-win` |
| i18n key | dotted lowercase, group first | `st.q`, `h.num`, `ew.architect` |
| Mission id, profile name | `kebab-case` | `ahawr-qa-acceptance` |
| Task id | `T` + number, letter suffix for a split | `T013`, `T013A` |
| Run id | `run_` + hex | `run_c308914b…` |

One casing per layer: a Python function never uses camelCase and a JS function never uses snake_case, even when
it mirrors the other side. Names of fields that cross the API stay `snake_case` in JS objects too
(`mission.started_at`), and are mapped to camelCase only in local variables.

## 2. Vocabulary (one word per idea)

Use these words, and only these, for these ideas. A new word for an old idea is a naming bug.

- **mission** - a unit of work with an objective, rules and acceptance criteria; **mission_id** is its human id.
- **state_key** - the key of a mission's rows in the Live State and event tables (today equal to the mission's
  `state_namespace`). Code that reads state says `state_key`; `state_namespace` appears only where the missions table
  column itself is meant.
- **task** - one step of a mission plan; **task_id** (`T013`), **task_index** (0-based position in the plan),
  **task_attempt** (1-based). `task_number` is not used.
- **attempt** - one Worker try at a task. **run** - one runner process (a role call). A task has attempts, an attempt
  has a Worker run and a Reviewer run.
- **role** - architect, worker, reviewer, service, qa, report. **provider** and **model** are a pair; as one string
  `provider/model`.
- **profile** - a row of `hermes_config` (`default`, `test`, `claude-code` ...). Not "config" and not "preset".
- **review** - the Reviewer's verdict: `review_status` (`pass`/`needs_changes`), `review_score` (0-100),
  `review_reason`, `review_next_task`. **score** alone is never used.
- **queue** - the ordered list of missions to run (`board.queue`); **position** is a place in it.
- **flag** - an on/off setting; **setting** - any configurable value; **option** - a setting that belongs to a role.
- **guard** - a watcher that acts or notices (`guards.py`); **notice** - a message to the operator.
- **board** - the mission board UI; **view** - one of its pages (Projects, Kanban, Lanes, Sprint, Grid).

## 3. What a name must say

- Units in the name when the type does not carry them: `timeout_seconds`, `poll_seconds`, `duration_ms`,
  `cost_usd`, `size_bytes`, `max_chars`. Never a bare `timeout` or `delay`.
- Booleans read as a statement: `is_running`, `has_plan`, `enabled`, `dry_run`; never `flag`, `status` or `check`.
- Collections are plural (`missions`, `task_views`), a mapping says what it maps: `mission_by_id`, `times_by_state_key`.
- A function is a verb phrase and says what it returns or does: `build_*` returns a new value, `load_*` reads from
  storage, `save_*` writes, `parse_*` text to data, `render_*` data to DOM or text, `resolve_*` picks among sources,
  `is_*`/`has_*` returns a bool, `ensure_*` creates if missing and is safe to repeat.
- A class is a noun; an exception ends with `Error`; a test is `test_<behaviour>` and reads as a sentence.
- A loop variable or helper may be one short word only when its scope is a few lines and its meaning is plain
  (`for task in tasks`); never `tmp`, `res`, `val`, `obj`, `data2`, `x`, `foo` outside a scope of a few lines.
- No type or storage in the name (`mission_list`, `task_dict`, `str_name`); no negations (`not_found_flag`);
  no mixed words for one idea (`mission` / `job` / `project` as synonyms).
- Abbreviations only from this list: `id`, `ms`, `url`, `api`, `db`, `dsn`, `ui`, `i18n`; `ctx` only for a UI render context object;
  `cfg` is not allowed (write `config`). Write `attempt`, not `att`; `mission`, not `mis`.

## 4. Files and scripts

- Repository modules: `<area>_<thing>.py|js` with a shared prefix per area (`board_`, `guard_`, `cloud_`). One
  area prefix, one responsibility per file; a test file mirrors the module: `test_<module>.py`, `<module>.test.js`.
- Operator scripts in `data-tables/ops`: a kept script has a plain verb-noun name (`sync_missions_db.py`,
  `launch_mission.sh`); a one-off script starts with `_` and is deleted when its job is done (the cleanup mission
  removes the rest). A patch of the live workflow ends in `_patch.py`; a mission generator is `_make_<topic>_mission.py`.
- Reports: `<mission_id>-report-ru.md`; docs: lowercase words with hyphens (`roles-config.md`).

## 5. Where the current code differs (the cleanup list)

The review mission starts from this list; it is a first sample, not complete.

- Generic or shortened names: `tmp`, `res`, `val`, `cfg`, `obj`, `ctx` outside render code; one-letter SQL aliases in
  scripts. Rename inside the module when the scope is local.
- Duplicate helpers with the same name in several files (`esc`, `fetchJson`, `postJson`, `L`, `_iso`, `_read_json`):
  move to one shared module before renaming anything else.
- Same idea, several words: `state_key` / `state_namespace` / `namespace`; `profile` / `profile_id` / `config row`;
  `score` / `review_score` / `last_review_score`.
- Hard-coded values inside functions that should be named parameters (`_HERMES_PROFILE`, port 8701, local model name).
- Underscore-prefixed one-off scripts in `data-tables/ops`.

Renames that cross a boundary (a DB column, an API field, an environment variable, a public route, a table of
n8n) are NOT done by a refactoring step alone: they need a compatibility alias for one version, a changelog line and
the operator's approval. Inside one module a rename is free when tests pass and the diff stays reviewable.
