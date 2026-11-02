## Facts: board store pattern

Template: the providers store (InMemory and Pg classes, with tests). Paths are relative to `claude-runner/`.

- Schema and DDL: `docs/board-schema.sql:7` creates schema `board` (`CREATE SCHEMA IF NOT EXISTS board`), all statements use `IF NOT EXISTS`. Provider tables: `board.providers` at `docs/board-schema.sql:127-143` (`updated_at` is the row stamp used as version), `board.provider_models` at `:147-161`.
- Grants: `docs/board-role.sql:20-22` creates role `board_operator` idempotently (`DO $$ ... IF NOT EXISTS (SELECT 1 FROM pg_roles ...)`); `:27` `GRANT USAGE ON SCHEMA board`; `:28-39` `GRANT SELECT, INSERT, UPDATE, DELETE ON board.<table>... TO board_operator`; `:41` sequence grant; `:48-` n8n data tables get `SELECT, INSERT, UPDATE` only. The provider tables are not in the `:28-39` list (`board.providers` / `board.provider_models` are not listed in the grep of that block, so the grant for them must be checked before a deploy).
- InMemory + Pg template module: `src/claude_runner/board.py` `InMemoryBoardStore` (class at `:614`), providers at `:1212-1262` (`get_provider` `:1212`, `create_provider` `:1218`, `update_provider` `:1247`); Pg counterpart in `src/claude_runner/board_pg.py` `PgBoardStore` (class at `:312`), providers at `:1328-1426` (`_provider_view` `:1351`, `_require_provider` `:1364`, `_lock_providers` `:1384`, `_check_version` `:1387`, `create_provider` `:1405`, `update_provider` `:1414`).
- Pg connection and transaction pattern: `board_pg.py:329` `__init__(dsn)`, `:333` sets `self._lock = threading.RLock()`; writes use `with self._lock, self._conn.transaction():` (`board_pg.py:1418`), then `_lock_providers()` (advisory lock `_PROVIDERS_LOCK`, `board_pg.py:67`, `:1385`).
- Version column: Pg `_provider_view` reads `updated_at` (`board_pg.py:1353`, select list `_PROVIDER_SELECT` at `:68`) and `_provider_dict` maps it to `"version"` (`board_pg.py:1708`, `:1720`). InMemory keeps `row["version"]` and sets it to `now()` on update (`board.py:1247-1262`).
- Header guard (middleware): `src/claude_runner/dashboard.py:205-215` `board_header_guard` returns 403 `{"error": "missing X-AHAWR-Dashboard header"}` for POST/PUT/DELETE on `/api/board/` and `/api/host-actions/` without `x-ahawr-dashboard: 1`; registered at `dashboard.py:217`.
- Header guard (per route): `src/claude_runner/board_api.py:124-126` `_require_dashboard_header` raises `GuardError`; called first in each provider mutation, e.g. `board_providers_api.py:408` (create), `:418` (update), `:438` (delete).
- Version check (store): `board.py:651-655` `InMemoryBoardStore._check_version` and `board_pg.py:1387-1390` `PgBoardStore._check_version` raise `ConflictError` when `expected is not None and expected != stored`. Route-level check: `board_providers_api.py:220-224` `_check_expected` raises `ConflictError`; `_expected` at `:177-180` makes `expected_version` required (`ValueError` when missing). `ConflictError` maps to HTTP 409 via `board_api.py:353-354` `_on_conflict`.
- Delete confirm: no confirm parameter for providers; provider delete refuses while a role uses the provider (`board_providers_api.py:434-446`, `ConflictError` on users, `_check_expected` before delete). The `confirm=true` pattern is in `board_api.py:216-219` `_confirm_needed` (raises `GuardError`, 403), used by mission delete at `board_api.py:684-697` (`Query(False)` at `:684`) and by remove-from-queue at `:1103-1111`. Mission delete is in-memory only (`board_api.py:690-691`).
- Store selection: `board.py:1445-1453` `get_board_store(dsn)` defers the import of `board_pg`; `board_pg.py:1735-1760` `get_board_store(dsn=None, driver_check=True)` reads `DASHBOARD_BOARD_PG_DSN` (`DSN_ENV`, `board_pg.py:43`), returns `_unavailable(...)` when unset or when `PgBoardStore(dsn)` fails, else `PgBoardStore(dsn)`. Route layer: `board_api.py:96-107` (`live_store`) sets `app.state.board_store = get_board_store()`; `board_api.py:111-119` `_store_guard` raises `RuntimeError` with the store's reason when unavailable, which `_on_runtime` (`board_api.py:373-374`) turns into 503; `add_board_routes` at `board_api.py:327-350` keeps a store already set on `app.state` (in-memory tests), otherwise uses `get_board_store()` (`:335-341`).
- Router mounting: board routes `board_api.py:350` `app.include_router(_build_router())`, error handlers at `:343-348`. Provider routes: `board_providers_api.py:382` `APIRouter(prefix="/api/board/providers")`, mounted by `add_provider_routes` at `board_providers_api.py:528-539` (`app.include_router` at `:539`). Both are called from `dashboard.py:220` (`add_board_routes`) and `dashboard.py:223` (`add_provider_routes`, only when `settings.providers_db`).
- Test fixture for Pg stores: `tests/test_board_providers_store.py:20-21` (`TEST_DSN_ENV = "AHAWR_TEST_BOARD_PG_DSN"`, `SCHEMA_FILE` = `docs/board-schema.sql`); `_pg_skip_reason` at `:25-35` skips unless the DSN is set and its database name contains "test"; `_fresh_pg_store` at `:38-49` applies the schema and TRUNCATEs the provider tables; `store` fixture `:52-63` is parametrized `["memory", "pg"]` and skips the pg case without a DSN. Stale-version test: `:103-107` `test_stale_version_is_a_conflict`.
- Fake Pg (no database): `tests/test_board_pg.py:38-55` `FakeCursor`, `:58-82` `FakeConnection` (records `execute`, canned rows by SQL marker, no-op `transaction()`), `:84-98` `_make_store(monkeypatch)` patches `psycopg.connect`. Also `tests/test_board_views_api.py:49` `FakeCursor` and `:60-94` `ScriptedConnection` (handlers matched by marker in the SQL, `transaction()` at `:86`).

Notes:
- `claude-runner/docs/qa-acceptance.md:396-424` cites `dashboard.py:111-121` and `:123` for the header guard; the code is now at `dashboard.py:205-217`.

## Facts: live workflow

Status: NO FRESH EXPORT. The export is `docker exec` based (`data-tables/ops/launch_mission.sh:20`), and no task may run docker, so none was taken and nothing was written to a scratchpad. Everything below is checked against the newest local snapshot, `data-tables/ops/wf_before_ahawr-lessons-library.json` (a prior launch export, dated 2026-10-27 08:40, not fresh). Re-check every anchor on a fresh export before a patch relies on it. Facts marked UNVERIFIED were not found in the snapshot.

Export method (for the operator): `launch_mission.sh` runs `docker exec n8n-autonomous-agents n8n export:workflow --id=7oMrsFqTiYMy0JJN --output=/tmp/wf_current.json` (`:20`), `docker cp` to `data-tables/ops/wf_before_<mission>.json` (`:21`); the Run Manager export is `rm_live.json` (`:49-50`, workflow id from `data_table_user_1S21PsMqT0FpR7eD` row 5, column `run_manager_workflow_id`).

Patch scripts called by `launch_mission.sh` (in order): `notice_split_patch.py` (`:33`), `telegram_model_patch.py` (`:34`), `telegram_mission_patch.py` (`:35`), `telegram_progress_patch.py` (`:36`), `report_pack_patch.py` (`:37`), `retry_patch.py` (`:38`), `telegram_gate_patch.py` (`:39`), `blind_guard_patch.py` (`:40`), `think_config_patch.py` (`:41`, and on the Run Manager `:51`), `roles_config_patch.py` (`:42`), `qa_workflow_patch.py` (`:43`), `ensure_live_state_row.py` (`:45`). `cloud_first_patch.py` is NOT called by `launch_mission.sh`.

Node names (snapshot, each name occurs once): `Build Worker Run Input`, `Build Reviewer Run Input`, `Constants`, `Retry Current Task`, `Parse Plan`, `Restore Saved Task State`, `Review Passed?`, `Prepare Current Task`, `Split Task Notice`. `Normalize Request` is a Run Manager node, not in the main workflow.

Worker input text: `Build Worker Run Input` builds `fullInput` (about line 386) and returns the node output at `return { json: {` (line 487, node-scoped count 1; the raw file has 3 such strings). The `input:` field is line 491: `  input: __fresh ? __input : (unfinished ? resumeText : __input),` (count 1 in the node).

Reviewer input: `Build Reviewer Run Input`; the prompt is `Prompt Templates` `reviewer_prompt` plus the previous review feedback (lines 415-425), with `resume_input` at line 439.

Acceptance and exhaustion: `Review Passed?` routes true to `Advance Task` and false to `Retry Current Task`. In `Retry Current Task` the exhaustion anchor is `if (currentAttempt >= maxAttempts) {` (count 1 in the node; the blind guard `AHAWR_BLIND_GUARD` is inserted before it). The limit is `Constants.max_attempts_per_task`; the `limit` variant `if (currentAttempt >= limit) {` is absent (0).

Events table: `Persist Parsed Review` writes data table `Autonomous Agent Task Attempts` with columns `task_index`, `task_attempt`, `review_status`, `review_score`, `review_reason`, `review_next_task`, `reviewer_output`, `event_type`, `state_key`, `created_at` (and run ids). `review_next_task` is the fix instruction: `Retry Current Task` sets `review_feedback: reviewNext || reviewReason`. The task state table is `Autonomous Agent Task State` (`Persist Retry State`), with `task_attempt`, `task_index`, `last_review_score`, `last_review_reason`, `review_feedback`, `status`. The `escalation_to_architect` marker is absent (0).

Flag reading pattern: `Constants` takes the `hermes_config` row where `profile_id === 'claude-code'` and `enabled` is true (`Constants` lines 1-3, from `$('Load Config')`). Each flag is one `row.<column>` field with normalisation: strings `String(row.x ?? '').trim().toLowerCase()`, numbers `Number(row.x ?? 0) || 0`, and unset as the -1 sentinel (`Constants` lines 80-83). Downstream nodes read `$('Constants').first().json.<name>` (`Build Worker Run Input`, lines 507-510). Telegram control flags from the Live State row: UNVERIFIED (not found in the snapshot).

Patch helpers (`data-tables/ops`): `sub(code, old, new, what)` asserts the anchor occurs exactly once (`cloud_first_patch.py:22-24`, `think_config_patch.py:30-32`). `node(w, name)` finds a node by name (`think_config_patch.py:25-27`). The code is edited at `n["parameters"]["jsCode"]`. The idempotency marker is tested with `MARK in code` before patching: `cloud_first_switch`, `think_policy`, `AHAWR_BLIND_GUARD`. Writes go to a temp file, then `os.replace` (`think_config_patch.py:171-173`).

Anchor status against the snapshot: `cloud_first_patch.py` anchors `  model: ($('Constants').first().json.worker_model),`, `  provider: ...`, `  input: unfinished ? resumeText : fullInput,`, `  session_id: (String(s.worker_session_id ?? '')),` and `freshRetry` all count 0; `cloud_first_switch` is absent, so it would fail its assert on this export. `think_config_patch.py` `CONSTANTS_OLD` is absent because `Constants` already has `worker_think_policy` (count 4).

Syntax check: `node data-tables/ops/js_syntax_check.js <workflow.json> [...]`, output `checked N bad M`, exit 1 if any node fails. Run on the snapshot (read-only): `checked 43 bad 0`, exit 0. `launch_mission.sh:55` runs it on the Run Manager export before import.

## Facts: statistics, redaction, UI

Sources: `docs/thinking-control.md`, `claude-runner/src/claude_runner/board_stats.py`, `board_think.py`, `think_switch.py`, `board_mission.js`, `board_mission_save.js`, `board_task.js`, `board_i18n.js`, `dashboard.py`, `board.html`. Line numbers checked with grep -n.

Alternation (policy variants by task number):
- `board_think.py:216-217` `_layout`: each variant repeated `weight` times, in list order. `board_think.py:207-224` `variant_for_task(policy, task_number, from_task=None)`: task n (1-based) uses `layout[(n - 1) % sum(weights)]`; `None` before `from_task`. Route: `board_api.py:1440-1442` (`{"variant": ...}`).
- Simpler `|`-separated form: `think_switch.py:57-60` `variants(policy)` splits on `|`; `think_switch.py:63-65` `variant_for(policy, task_number)` = `parts[(n - 1) % count]`.
- Documented in `docs/thinking-control.md:44-51` (JSON policy, read from `hermes_config.worker_think_policy`), `:69` (`weight`), `:71` (`from_task`), `:76` (older text forms).

Per-task statistics (the function to extend):
- `board_stats.py:400-` `task_stats(manager, board, state_key, task_id)`: runs via `_task_run_ids` (`:126`), attempts against the cap (`:460`), `attempt_seconds` (`:462-469`), `runs` per attempt (`:471`). Route: `board_api.py:1365` `/missions/{mission_id}/tasks/{task_key}/stats`.
- Aggregation: `board_stats.py:27` imports `stats_for_runs` from `mission_stats.py` (reuse only). Run ids come from `worker_run_id` / `reviewer_run_id` event rows and `_run_id` fields (docstring `:3-5`).
- Attempt cap: `board_stats.py:180-190` `_max_attempts`: the queue entry's `attempts_per_task` first, else `hermes_config` `max_attempts_per_task` (`:187`).
- Mission card: `board_stats.py:224` `mission_card_stats`; route `board_api.py:1348`.

Redaction (an existing helper exists; reuse it):
- `board_ops_redact.py:53` `scrub(text: str) -> str` (token patterns, e.g. `:30` Telegram token); `:105` `sanitize_result(obj, max_chars, max_lines)`.
- `report_facts.py:284` `redact(text)` with rules `:270-280` (bearer, URL credentials, `sk-`/`ghp_`/`xox` tokens, `KEY=value`).
- `provider_secrets.py:41` `redact(text, secrets)` (replaces known secret values); `:108` `log_filter`.
- DSN: `board_pg.py:1761` `_redact_dsn(message, dsn)`.
- Grep used: `grep -rn -i "def .*redact\|def .*mask\|def .*scrub\|def .*sanitize" claude-runner/src` and `grep -rn -i "redact\|secret\|token" claude-runner/src --include=*.py | grep -i "def \|re.compile"`. No mask helper found.

Settings read:
- Interface: `board.py:216-218` abstract `BoardStore.hermes_config()`; `board.py:718-720` in-memory; `board_pg.py:529-538` Pg (profile `claude-code`, falls back to the first row); `board_pg.py:169-171` unavailable store returns `{}`.
- Route reads: `board_api.py:434` `row = store.hermes_config() or {}`; `:424-428` the allowed field list (`max_attempts_per_task`, `worker_think_policy`, `worker_think_policy_from_task`, ...).
- Writes: `board_api.py:1492` `store.update_hermes_config(body.get("expected_version"), roles_json=text)`; `:1537` same with `features_json`.
- `service_model`: found only in field lists (`board_mission.js:54`, `board_plan.py:80`, `board_pg.py:819`); no `hermes_config` read of it in `board_api.py`.

UI settings panel (pattern to copy):
- `board_mission_save.js:10-20` header comment (diff, confirm, 409 banner). `:30` `esc`. `:43-48` save state (`data`, `lastDiff`).
- Version + header: `board_mission_save.js:219-236` `featuresSavePlan`: one request per changed field, `expected_version` = version read at load (`:221-224`), header `X-AHAWR-Dashboard: 1` (`:233`).
- Diff then confirm: `board_mission_save.js:224-231` (no change -> banner); `:324` `window.confirm(t("save.confirm"))`; 409 handling `:368`.
- Mission config: `board_mission_save.js:361` and `:365` send `expected_version`; 409 at `board_mission_save.js:368`.
- Reorder with version: `board_mission.js:560-566` `saveOrder` (`fresh.version` re-read, then POST).
- Task view: `board_task.js:23` `L(key, en, params)`, `:35` `esc`, `:158` `renderBoardTask`, `:184` `BoardTask`.

Fetch helpers (four `fetchJson` copies already exist; do not add a fifth):
- `board_mission.js:522-525` `postJson(url, body)` sets `"X-AHAWR-Dashboard": "1"` at `:525`.
- `board_mission.js:705-716` `fetchJson(url)`: no options; throws `err.status`, `err.body`.
- `board_actions.js:49-60` `fetchJson(url, options)`: accepts options (use this shape for POST).
- `board_dnd.js:59-69` `fetchJson(url)`: different error text shape.
- `board_i18n.js:646-650` `fetchJSON(url)` (capital JSON, separate helper).

i18n:
- `board_i18n.js:19` `BUNDLED_EN`, closes `:595`; `:613` `t(key, params)`; `:635` `setString`.
- Keys: flat, in `board_i18n_en.json` (example `"save.confirm":"Save the changes?"`) and `board_i18n_ru.json`.
- Parity test: `claude-runner/tests/board_i18n.test.js:139-141` checks `Object.keys(EN_JSON)` against `I.t(k)`; header item 5 says bundled table equals `board_i18n_en.json`. The RU file is not checked by this test (not verified further).
- Also `tests/board_i18n_ui.test.js` exists (not read).

Static files and script tags:
- `dashboard.py:239` regex `^board[a-z0-9_]*[.](js|css|html)$`; `:246-258` serves `resources.files("claude_runner")` files with `Cache-Control: no-store`, 404 when absent. A new `board_<name>.js` is served with no change to `dashboard.py`.
- Loading it in the board page needs `<script src="/static/<name>.js"></script>` in `board.html` (existing tags `:36` i18n, `:40` mission, `:42` task, `:48` mission_save).
- `dashboard.html` only needs a change for panes it loads itself with `loadScript` (`dashboard.html:1248-1274`, `:1356-1357`).

Harness2 (arXiv 2610.14224): no offline copy found. Searched `grep -rli "2610.14224\|harness2\|harness 2" --include=*.md --include=*.txt --include=*.html --include=*.json .` (no hit), `find . -iname "*harness*" -o -iname "*2610.14224*"` (only `./harness.log` and `data-tables/missions/mission_ahawr-deepseek-harness-executor.csv`, both unrelated, not read), `find . -iname "*.pdf"` (none). Only the facts in the mission text will be used.

## Design: lessons library

Status: the library code exists (`board_lessons.py`, `board_lessons_extract.py`, routes in `board_api.py`) and
is OFF by default (`lessons_enabled`). The table DDL in `board-lessons.sql` is NOT APPLIED; applying it needs the
database owner's go-ahead.

A lesson is one short, checked note from an earlier task of the same mission or project: what went wrong in a
task's first attempts, and what the accepted attempt did differently. The next task's first attempt may get up
to `LESSONS_K` of them as data.

### Entry

Storage: the Pg board schema, table `board.lessons` (`claude-runner/docs/board-lessons.sql`). Limits are counted in
Unicode characters after redaction. A text value over its limit is cut and ends with `ELLIPSIS` (`...`,
`board_lessons.py:20`). A value that is not text (evidence) over its limit is refused, not cut.

| Field | Type | Hard limit and rule |
|---|---|---|
| `id` | bigint, identity | assigned by the database |
| `mission` | text | the mission `state_key`, at most `MISSION_MAX_CHARS` = 64 characters |
| `task_id` | text | the task id as in the plan (for example `T004`), at most `TASK_ID_MAX_CHARS` = 16 characters |
| `task_index` | integer | 1-based position in the mission plan, 1 to `TASK_INDEX_MAX` = 999 (code and table use 1-based; `docs/naming.md` says 0-based, see Open points) |
| `kind` | enum | `fixed-after-retry` or `gave-up` only |
| `scope` | enum | `mission` or `project` only; default `mission`. New field: see Scopes and promotion. Added as a column in `board-lessons.sql` before that file is applied |
| `situation` | text | at most `SITUATION_MAX_CHARS` = 400 characters: what the failing attempts did, in one sentence |
| `lesson` | text | at most `LESSON_MAX_CHARS` = 300 characters: the rule to follow, in one sentence |
| `evidence` | jsonb | at most `EVIDENCE_MAX_CHARS` = 1200 characters serialised; over the limit the write is refused |
| `created_at` | timestamptz | set when the row is written |
| `status` | enum | `active` or `retired` only |
| `times_shown` | integer | count of tasks whose first attempt received the lesson; starts at 0 |
| `times_first_attempt_passed_after` | integer | count of shown tasks that passed on their first attempt; at most `times_shown` |
| `times_seen` | integer | count of writes that matched this entry as a duplicate; starts at 1 |
| `version` | integer | entry format version, `1`; not the optimistic-lock stamp (`updated_at`) |

`evidence` is a JSON object with these keys only:

- `failed_review_reason` and `accepted_review_reason`: Reviewer reason excerpts, each at most `REASON_MAX_CHARS` = 200
  characters (for `gave-up`, `accepted_review_reason` repeats the last failed reason).
- `failed_score` and `accepted_score`: stored Reviewer scores as integers, or `null`.
- `failed_attempt` and `accepted_attempt`: 1-based attempt numbers; `accepted_attempt` is `null` for `gave-up`.
- `touched_paths`: at most `TOUCHED_PATHS_MAX` = 5 repository-relative paths, each at most `PATH_MAX_CHARS` = 120 characters.

The lesson text is the failing review's `review_next_task`, or its `review_reason` when that is empty.

### Write rule

Only two cases write a row, both decided at the review outcome:

- `fixed-after-retry`: a review with `review_status = pass` follows at least one failed attempt. The accepted attempt
  is that first pass; the failure is the attempt before it.
- `gave-up`: the attempt cap (`max_attempts_per_task`) is reached with no pass.

A task whose first attempt passes writes NOTHING: no row, no counter, no evidence. The write endpoint returns
nothing when `lessons_enabled` is off (`board_api.py:1820`).

Dedup, in `decide` (`board_lessons_extract.py:183`), after normalising the text (lowercase, whitespace collapsed,
punctuation removed) and comparing with `difflib.SequenceMatcher` at `LESSONS_DEDUP_RATIO` = 0.9:

1. `drop` when a sentence of the mission objective matches the lesson text at or above the ratio (the mission
   already says it).
2. `bump` when an active lesson of the same mission matches the situation plus lesson text at or above the ratio:
   `times_seen` rises by one on that row. This is a design choice: the existing counter rises instead of a new row.
3. `add` otherwise.

### Redaction

Redaction runs on every text field before the length limits and before the write. The existing function
`redact` (`board_lessons_extract.py:58`) runs these, and this section is its contract:

| Content | Replacement |
|---|---|
| Tokens, bearer values, `sk-`/`ghp_`/`xox` tokens, `KEY=value` pairs | removed by `board_ops_redact.scrub` and `report_facts.redact`; the helper's own marker stays |
| Known secret values | removed by `provider_secrets.redact(text, secrets)`, when the caller passes the secrets |
| DSNs and connection strings | removed by `board_pg._redact_dsn` |
| Email addresses | replaced by `<email>` (new pattern in `redact`; no existing helper covers emails) |
| Absolute personal paths (`C:\Users\<name>\`, `/d/Users/<name>/`, `/home/<name>/`, `/Users/<name>/`) | the home prefix is removed (existing pattern `_PERSONAL_HOME`) |

Never stored, in any field: raw logs, prompts (Worker or Reviewer), full Reviewer output, full diffs, Worker
output text, environment variable names or values, tokens, keys, DSNs, `.env` contents. `touched_paths` are
repository-relative by construction.

### Selection

Runs only when `lessons_enabled` is on, and only for the first attempt of a task that the alternation shows
(see Measurement). No model, no embedding, no network call.

1. Candidates: `status = active` AND either `scope = mission` with the same `mission`, or `scope = project` from any
   mission. Mission and project lessons share one pool and one cap. The optional `lessons_same_workdir` flag (OFF,
   not read by code yet) would widen mission candidates to missions with the same working directory.
2. Score for the current task, using its file list and its task words (lowercased words of the task title and
   objective, stop words and words under `SCOPE_WORD_MIN_CHARS` = 4 characters removed; `_scope_words` in code):
   - +1 per file path equal to one of the lesson's `evidence.touched_paths`;
   - +1 per task word that also appears in the lesson's `situation`;
   - +1 when `kind` is `fixed-after-retry`.
   Lessons with score 0 are not selected.
3. Order: score, highest first; ties go to the newest `created_at`.
4. Take at most `LESSONS_K` = 5 lessons. Add them in that order while the rendered block stays within
   `LESSONS_BLOCK_MAX_CHARS` = 1500 characters. A lesson that does not fit is skipped, not cut.

The block heading and the note that follows it are the constants `BLOCK_HEADING` and `BLOCK_NOTE`
(`board_lessons_extract.py:37-43`). The block goes into the Worker input only; it is not stored in the task state.

### Scopes and promotion

- `mission`: the lesson is a candidate only in its own mission. This is what the workflow writes.
- `project`: the lesson is a candidate in every mission of the runner. Promotion is the only way to reach it.
- Automatic promotion: when a new lesson matches an active lesson of another mission at or above `LESSONS_DEDUP_RATIO`,
  and the matching lessons span at least `lessons_promote_min_missions` distinct missions (proposed default 3),
  the copy with the highest `times_shown` becomes `scope = project`. The other copies are set to `retired`
  (kept, not deleted). The check runs in `decide`, after the dedup step.
- Operator promotion: the operator sets `scope` to `project` (or back to `mission`) with the existing edit route
  (`PUT`, `board_api.py:1790-1797`), with `expected_version` and the `X-AHAWR-Dashboard: 1` header.
- A project lesson follows the same retire rule as a mission lesson.
- Open point: "project" here means the whole runner, not one working directory. The operator confirms this before
  promotion is enabled; the narrower option is `lessons_same_workdir`.

### Measurement

- Alternation by task number. With `lessons_holdout_enabled` ON in the mission config (`board_stats.py:512`), a task is
  shown when `(task_index - 1) % lessons_holdout_every == 0`; with the default period 2, odd task numbers get the
  block and even ones are the control. With the holdout OFF, every task is shown when `lessons_enabled` is on.
- The period is fixed at 2 in code (`board_stats.py:514`, shortcut: `lessons_holdout_every` is not read; upgrade when a
  different period is needed).
- Effect on the next task's first attempt: the selection runs once, before the first attempt. Each lesson in the
  block gets `times_shown` plus one. If that first attempt passes, each lesson in the block gets
  `times_first_attempt_passed_after` plus one. Retries of the task do not select again and do not count again, so the
  first-attempt pass rate is the only outcome counted. UNKNOWN whether the workflow calls `POST /lessons/select` on
  retries; the design requires that it does not.
- The comparison reads the existing events table (`Autonomous Agent Task Attempts`, `task_attempt`, `review_status`).
  No extra run is made. The statistics and sample sizes are in "Measurement plan" below.

### Retirement

- Rule: `status` becomes `retired` when `times_shown >= LESSONS_RETIRE_MIN_SHOWN` (10) and
  `times_first_attempt_passed_after / times_shown < LESSONS_RETIRE_MIN_PASS_RATE` (0.5). The two values are
  named constants in code (`board_lessons.py:21-22`); the setting names `lessons_retire_min_shown` and
  `lessons_retire_min_pass_rate` are reserved for when they become settings.
- The rule runs in the outcome path (`POST /lessons/outcome`, `board_api.py:1861`), so only with `lessons_enabled` on.
- A retired row is never selected, is not deleted, and keeps its counters. Restore: `board_api.py:1805-1809`.
  Delete is possible only with `confirm` (`board_api.py:1811-1816`).

### Flag

Read from the `hermes_config` row of profile `claude-code`, the same way as `max_attempts_per_task`
(`board_api.py:434`; allowed fields at `board_api.py:424-428`).

| Name | Default | Meaning |
|---|---|---|
| `lessons_enabled` | OFF | master flag, read by `_lessons_enabled` (`board_api.py:210`) and by `Constants` in the workflow (`lessons_patch.py`) |
| `lessons_holdout_enabled` | OFF | mission config; turns on the alternation (`board_stats.py:512`) |
| `lessons_same_workdir` | OFF | candidates may come from other missions with the same working directory; not read by code yet |
| `lessons_promote_min_missions` | 3 (proposed) | distinct missions needed before automatic promotion to `project`; not read by code yet |
| `lessons_rewrite_enabled` | OFF | optional rewrite of a lesson text by the service model; calls a paid provider, so turning it on asks the operator first; not read by code yet |

`lessons_enabled` OFF means byte-for-byte unchanged behaviour: no block is added to any Worker input, no select, write,
outcome or retire call is made to the runner, and the mission's inputs are the same as before the library existed.
Required test: with the flag off, the Worker input equals the input without the patch.

### Deliberate omissions

- No extra executions per task: the comparison uses the attempts the mission already makes.
- No model call by default: selection, dedup, promotion and retirement are deterministic. The only model use is
  `lessons_rewrite_enabled`, OFF, which needs the operator's go-ahead because it contacts a paid provider.
- No Redis, no vector store, no embedding index: candidates are read from the Pg table with a plain query.
- No new service and no new runtime dependency: `difflib` is standard library; the table lives in the existing Pg board.
- No Harness2 propose-probe-compose runs: no offline copy of the method exists in the repository (see the note in
  the facts above).
- Why: the library must not change the cost, timing or results of a mission it did not ask for, and a deterministic
  rule can be checked by a test and read by an operator.

### Open points

- `task_index` is 1-based in code and in the table, while `docs/naming.md` defines it as 0-based. Renaming or
  re-basing is a boundary change (a DB column), so it needs the operator's approval.
- "Task words" are called `_scope_words` in code; the word `scope` now also names the new field. Rename inside
  `board_lessons_extract.py` when the module is next changed.
- Whether the workflow calls `POST /lessons/select` on retries: UNKNOWN in the repository (see Measurement).
- `lessons_patch.py` anchors the Worker input only; the Reviewer input is UNKNOWN (see the facts above).

## Activation (operator)

Nothing in this section has been run. The Worker does not run these commands: the operator runs them on the host, from the repository root, after reading the output of each step. The container name `n8n-autonomous-agents` and the workflow id `7oMrsFqTiYMy0JJN` are the ones `data-tables/ops/launch_mission.sh` uses (`:20`); check them against the live stack before you run anything.

1. Deploy a runner build that has the `/api/board/lessons` routes (`docker compose up -d --build claude-runner`), so the workflow's calls have a target.
2. Apply the table. The file is idempotent and needs the database owner's go-ahead (it is DDL):
   ```
   psql "$DSN" -v ON_ERROR_STOP=1 -f claude-runner/docs/board-lessons.sql
   ```
   `$DSN` is your own connection string to the n8n database; do not paste it into a file or a report.
3. Export the live workflow and copy it out:
   ```
   docker exec n8n-autonomous-agents n8n export:workflow --id=7oMrsFqTiYMy0JJN --output=/tmp/wf_lessons_export.json
   docker cp n8n-autonomous-agents:/tmp/wf_lessons_export.json data-tables/ops/wf_lessons_export.json
   cp data-tables/ops/wf_lessons_export.json data-tables/ops/wf_lessons_next.json
   ```
4. Patch the copy, not the export. The patch is idempotent and asserts each anchor exactly once. It adds the flag to `Constants` and the block to `Build Worker Run Input` only; `Build Reviewer Run Input` is not changed. Expect `patched: ...` the first time and `already patched` on a second run:
   ```
   PYTHONIOENCODING=utf-8 python data-tables/ops/lessons_patch.py data-tables/ops/wf_lessons_next.json
   ```
   The patch does not write lessons. Writes come from the outcome posts in `Advance Task` and `Retry Current Task` (`lessons_outcome_switch`), which an earlier patch added. Check that the export still has them (`grep -c lessons_outcome_switch`); without them no lesson is ever stored.
5. Syntax-check the patched copy. Expect `checked <N> bad 0` and exit code 0; any other result means do not import:
   ```
   node data-tables/ops/js_syntax_check.js data-tables/ops/wf_lessons_next.json
   ```
6. Import (the operator's step; Claude does not import):
   ```
   docker cp data-tables/ops/wf_lessons_next.json n8n-autonomous-agents:/tmp/wf_lessons_next.json
   docker exec n8n-autonomous-agents n8n import:workflow --input=/tmp/wf_lessons_next.json
   ```
7. Turn the library on. Both switches are OFF by default and the workflow reads `lessons_enabled` only from the hermes row:
   - hermes row: `lessons_enabled` in the n8n data table `data_table_user_1S21PsMqT0FpR7eD`, the row with `profile_id = claude-code`. Add the boolean column in the n8n interface if it is missing (never with SQL), then set it to `true`.
   - Optional alternation: in the mission form, tick "Alternation of the lessons block (holdout)" (`lessons_holdout_enabled` in the mission config). This is a mission setting; it does not change other missions.

Turn it off again by setting `lessons_enabled` back to `false`. The stored lessons stay in the table; with the flag off no block is added and no call is made to the runner.

### Switches in use

Only two switches are read by the code today. The other names in the table above are design text and are not read anywhere yet (checked with grep over `claude-runner/src` and `data-tables/ops`):

- `lessons_enabled` (hermes row, default OFF): `board_api.py` `_lessons_enabled`, and `Constants` in the workflow via `lessons_patch.py`.
- `lessons_holdout_enabled` (mission config, default OFF): `board_stats.py` `lesson_block_shown`.
- Not read: `lessons_same_workdir`, `lessons_rewrite_enabled`, `lessons_holdout_every`. The alternation period is fixed at 2 (`board_stats.py:493` shortcut).
- The retire thresholds are constants, not settings: `LESSONS_RETIRE_MIN_SHOWN = 10` and `LESSONS_RETIRE_MIN_PASS_RATE = 0.5` (`board_lessons.py:21-22`).

### Measurement plan

The figures come from `lessons_stats` (`board_stats.py`): for each mission, the first-attempt pass rate and task count of the tasks that got a block (`with_lessons`) and of those that did not (`without_lessons`), and the mean attempts per task before and after the first task with a block.

1. Baseline first. Measure the first-attempt pass rate `p1` of the missions with the flag off. The sample size depends on it.
2. Sample size per variant, two-sided alpha 0.05, power 0.8, standard two-proportion formula
   `n = (1.96*sqrt(2*pbar*(1-pbar)) + 0.84*sqrt(p1*(1-p1) + p2*(1-p2)))^2 / (p1 - p2)^2`, where `pbar` is the mean of the two rates. Computed here (not measured):
   - `p1` 0.60 to `p2` 0.80: about 82 tasks per variant;
   - `p1` 0.60 to `p2` 0.70: about 356 tasks per variant;
   - `p1` 0.50 to `p2` 0.65: about 170 tasks per variant.
   Tasks of one mission are correlated, so count missions as well as tasks.
3. Read the result after each mission with the holdout on. Caveat: `with_lessons` counts only tasks that actually received a block, and `without_lessons` also holds tasks that had no candidate lesson. For a clean control, count the odd tasks (shown) and the even tasks (withheld) by number, and compare only those.
4. Decision rule: keep `lessons_enabled` on only when the lift in first-attempt pass rate is positive at the planned count and the attempts per task do not rise. If the count is not reached, report the result as inconclusive and do not extend the claim.
