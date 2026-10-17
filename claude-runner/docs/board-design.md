# Board design

## Dashboard integration notes

This maps the existing dashboard so the mission board can plug in without breaking it.
Everything below is read-only research of the current code; no source file was modified.

## 1. FastAPI app and routes — `claude-runner/src/claude_runner/dashboard.py`

The whole page is served from one app built by `create_dashboard_app(settings, transport)`
(line 61), run under `main()` (line 341) with uvicorn on port 8701 (line 344).
Middleware: `TrustedHostMiddleware` on `DASHBOARD_HOSTS` (line 91). The page HTML is read
from the package resource at line 92 and returned verbatim by `GET /` (line 97) with
`Cache-Control: no-store` (line 99).

Route list (decorator, handler, line):

| Method | Path | Handler | Line |
| --- | --- | --- | --- |
| GET | `/` | `index` — returns `dashboard.html` | 97 |
| GET | `/healthz` | `healthz` — source names | 101 |
| GET | `/api/runs` | `list_runs` — multi-value filters, per-source fetch via `asyncio.gather` | 105 |
| GET | `/api/search` | `api_search` — full-text search over run events | 183 |
| GET | `/api/runs/{run_id}/events` | `run_events` — via `fetch_events` (line 166) | 225 |
| POST | `/api/runs/{run_id}/cancel` | `cancel_run` — requires `X-AHAWR-Dashboard: 1` header | 229 |
| GET | `/api/runs/{run_id}/export` | `export_run` — md/json download via `_download` (line 283) | 248 |
| GET | `/api/sessions/{session_id}/export` | `export_session` — whole-session download | 255 |

Supporting helpers: `sources(request)` (line 94) picks the configured sources; `_runs_match`
(line 313) post-filters runs (OR within a field, AND across fields); `_describe_error`
(line 333) maps transport errors to short strings.

## 2. `dashboard.html` structure (1009 lines, served as one file)

Layout: left `<aside id="side">` (header at lines 220-230, run list `<nav id="list">` line 231),
`<main id="main">` (line 233) which is filled dynamically with `#head`, `#overview`, `#ledger`,
`#insp` (see line 585).

- **Theme toggle**: an early `<script>` in `<head>` (lines 7-15) reads
  `localStorage["ahawr-dashboard-theme"]` before any CSS; CSS palettes for
  `:root[data-theme="light"]` (line 28), dark via `prefers-color-scheme` (line 31) and
  `:root[data-theme="dark"]` (line 42); the toggle button `#themebtn` is in the header
  (line 222), its IIFE script is lines 235-265 cycling auto/light/dark.
- **Mobile CSS**: `@media (max-width: 1100px)` (line 207, fixed `#insp`) and
  `@media (max-width: 760px)` (lines 208-215, column layout, collapsed run-list columns).
- **`esc()` helper**: defined at line 339 —
  `const esc = (s) => String(s ?? "").replace(/[&<>"']/g, ...)` — escape every interpolated
  string with it.
- **Hash routing**: the filter lives in the URL hash as `#field=value&...`
  (`urlFilter()` line 381, `writeUrl()` line 389, `parseFilter()` line 320); deep links
  append `#run=<id>` and `fromHash()` (line 996) on `hashchange` (line 997) selects the run.
- **Auto-refresh**: `setInterval(loadRuns, 3000)` (line 1005); the selected run polls its
  events every 1 s when active, 8 s when not (`poll()`, `S.timer = setTimeout(poll, ...)`
  at line 632, retry 4000 ms on error at line 626).

## 3. What `sources.py` polls (394 lines)

The dashboard has exactly two sources, held on `app.state` by the lifespan
(dashboard.py lines 66-86):

- **`RunnerSource`** (line 69, name `"claude-runner"`): polls claude-runner's own read-only
  API — `list_runs` (79, `GET /v1/runs`), `events` (95, `GET /v1/runs/{id}/events`),
  `cancel` (104, `POST /v1/runs/{id}/cancel`), `search` (114, `GET /v1/search`).
- **`HermesSource`** (line 248, name `"hermes"`, id prefix `HERMES_PREFIX = "hermes:"` line 24):
  polls the Hermes Agent API — `list_runs` (319, `GET /api/sessions` or
  `GET /api/sessions/{id}`), `events` (358, messages via `GET /api/sessions/{id}/messages`),
  `search` (377 — returns a note, Hermes text is not searched).

Shared helpers: `_check` (49), `_rows` (59), `hermes_entries` (175), `SourceError` (45).
**Note:** `sources.py` does not poll LiteLLM or llama-server health — nothing in the dashboard
reaches them. LiteLLM/llama are only used as the model backend of `provider="local"` runs
(`litellm/Dockerfile`, `.env.example`); there is no health endpoint in scope for the board.

## 4. Reusable functions for time / cost / speed / cache / thinking, by list of run_id

- **`mission_stats.stats_for_runs(manager: RunManager, run_ids: list[str]) -> dict`**
  (`mission_stats.py:45`) — the single function that computes everything for a mission:
  `runs`, `worker_hours`, `reviewer_minutes`, `architect_minutes`, `cost_estimate_usd`,
  `cost_real_usd` (excludes `provider="local"`), `tok_per_s`, `cache_ratio`, `compactions`
  (lines 72-82). Internals: `_minutes` (line 18) and `_run_ids_with_compactions`
  (line 29, pulls in the sessions' `kind="compact"` runs).
- Exposed to callers via **`POST /v1/stats/runs`** in `run_views.py:86`
  (`run_stats`, body `StatsBody.run_ids` max 2000, line 54) — the board can call this
  instead of reading events itself.
- **`run_views.summary(run, manager) -> dict`** (`run_views.py:17`) — per-run summary dict
  (title, status, model, provider, `cost_usd`, `context_tokens`, token breakdown, `live`)
  returned by `GET /v1/runs` (line 61) and `GET /v1/runs/{run_id}/events` (line 72).

## 5. Reusable functions in `store.py`, `events.py`, `local_pricing.py`

`store.py` (class `Store`, SQLite; methods are instance methods, line 68):
- `list_runs(limit=50, session_id="", role="", status="")` — line 308, newest first.
- `get_run(run_id)` — line 144.
- `session_run_ids(session_id, since=None)` — line 246.
- `running_local_workers()` — line 191 (run id, session, prompt head, think-policy fields).
- `local_compaction_running()` — line 182.
- `session_seconds_since(session_id, since)` — line 228; `counts()` — line 335.

`events.py` (class `EventLog`, line 289):
- `read(run_id, after=0) -> (entries, live_block)` — line 368.
- `is_live(run_id)` — line 365; `add` — line 330; `feed` — line 341; `prune` — line 308.
- Free functions: `entries_from(event)` — line 60; `StepTracker.feed` — line 235.

`local_pricing.py`:
- `LocalPricing.from_env(env)` — line 56 (reads `CLAUDE_RUNNER_LOCAL_PRICE_*` settings).
- `LocalPricing.enabled` (property) — line 74.
- `cost_from_events(path, default_mode="on") -> dict` — line 78, returns
  `cost_usd`, `by_profile`, `by_mode` (thinking on/off split) for one run's event log.
- `parse_rates(text)` — line 32.

## 6. Test organization

- `tests/conftest.py` — shared fixtures: `fake_bin` (fake `claude` binary, line 26),
  `workspace` (line 34), plus the `calls`/`wait_for` helpers used by dashboard tests;
  the venv is forced to import the local `src` tree (lines 14-17).
- `tests/test_dashboard.py` — 36 tests: activity logging, live block, filters
  (single/multi-value/OR/AND), search merge, stop-a-run, source-error reporting, host guard
  (`test_dashboard_settings_and_host_guard`, line 806), theme switcher (line 323), escaping
  (line 418), data-attributes/handler (line 348).
- `tests/test_dashboard_js.py` — 2 node-based tests: extracts the pure filter functions
  between the marker comments `// === pure filter functions (no DOM) ===` (dashboard.html
  line 274) and `// === end pure filter functions ===` (line 336), runs them in node
  and asserts `toggleValue`/`removeValue`/`clearFilter`/`runMatches`/`filterToQuery`/
  `parseFilter`; skips with a manual procedure if node is absent.
- `tests/test_board_ui.py` — pytest wrapper for the pure board logic in
  `src/claude_runner/board_logic.js` (T017 part 1): runs `tests/board_logic.test.js`
  in node, asserts the module has no DOM references, and checks the exported
  functions `esc`/`reorder`/`dropPosition`/`mergeServerIntoForm`/`parseHash`/
  `buildHash` load. Skips when node is missing; the manual check steps are in
  the test file's docstring: load `board_logic.js` in a scratch file, run the
  assertions from `board_logic.test.js` by hand (reorder front/middle/end and
  clamping, dropPosition chained with reorder for before/after, drop onto
  self as a no-op, the running-mission refusal and the running target staying
  first, merge keeping local edits with the `server_changed` flag set/cleared,
  hash round-trip, `esc`), and confirm `all ok` in node.
- Other suites: `test_runs.py` (24), `test_local_pricing.py` (8), `test_think_switch.py`
  (23), `test_search*.py` (12/22/8), `test_config_cli.py` (18), `test_context_probe.py`
  (7), `test_providers.py` (7), `test_think_policy.py` (8), `test_handoff.py` (1),
  `test_mem_guard.py` (8).

## 7. Where the board ('Прогоны | Миссии') tabs and static files go

Current markup has **no tab bar**: the only `.tabs` CSS (lines 191-193) styles the
inspector's internal Summary/Details tabs (`S.tab = "summary"`, line 270; inspector
rendering from line 896). The top-level layout is `#side` (left, lines 219-232) +
`#main` (line 233, filled by the script at line 585).

- **Tab bar insertion point**: add a `<nav class="tabs">` with two buttons
  (`Прогоны` = existing run view, `Миссии` = board) at the top of `<main>`, right after
  the `<main id="main">` open tag (line 233) and before the dynamic `#empty`/sections
  content — i.e. between line 233 and the first script insertion at line 585. The
  existing `.tabs` CSS (lines 191-193) can be reused for styling; the new tab state
  belongs in the `S` object (line 270). Hash routing: keep the `#run=...` deep link
  (`fromHash`, line 996) and add a `#view=...` (or similar) fragment handled in `urlFilter`/
  `writeUrl` (lines 381-394) so deep links stay shareable.
- **Static file serving**: currently there is none — `dashboard.html` is inlined via
  `importlib.resources` at dashboard.py line 92 and only `GET /` serves it (line 97).
  To serve board static assets (css/js), add an explicit route in `create_dashboard_app`,
  e.g. `@app.get("/static/{name}")` registered after `index` (after line 99), serving files
  from a new package resource folder with the same `no-store` headers. No change to
  `GET /` or to the existing API routes is required.
- **Board data source**: reuse `POST /v1/stats/runs` (`run_views.py:86`,
  `stats_for_runs` `mission_stats.py:45`) for per-mission time/cost/speed/cache, and
  `GET /api/runs` with `session_id=` filters (`dashboard.py:105`) or `store.session_run_ids`
  (`store.py:246`) to resolve a mission's run ids; thinking-mode split comes from
  `LocalPricing.cost_from_events` (`local_pricing.py:78`) per run's event log.

## 8. Environment and driver decision

Checked 2026-10-15 in the test venv (`python3 -m venv /tmp/venv`;
`/tmp/venv/bin/pip install -e 'claude-runner[dev]'`).

**Installed driver packages (absence):**
- `psycopg` — not installed.
- `psycopg2` — not installed.
- `asyncpg` — not installed.

Confirmed by `/tmp/venv/bin/pip list | grep -i -E 'psycopg|asyncpg'` → no output (exit 1).
`claude-runner/pyproject.toml` declares no Postgres dependency at all (only
`pydantic`, `fastapi`, `uvicorn`, `httpx`; `[dev]` adds `pytest`, `pytest-cov`,
`ruff`, `mypy`). The Dockerfile for `ahawr-dashboard` (built from the same
`claude-runner/Dockerfile`) installs only `python3 python3-venv git ca-certificates curl
ripgrep procps` via apt plus `uv` + `.[dev]` into a Python 3.12 venv — no `libpq`
and no `psycopg`/`psycopg2`/`asyncpg` anywhere. So there is **no Postgres driver
available in the dashboard image today**.

**Node:** available, v22.23.3 (`which node` → `/usr/local/bin/node`; `node --version`
→ `v22.23.3`). This is the same major line as the runner image base
(`node:22-bookworm-slim`), so the two node-based dashboard tests
(`tests/test_dashboard_js.py`) run in the venv rather than skipping.

**Networks of `ahawr-dashboard` and `ahawr-postgres`:**
docker-compose.yml declares **no explicit `networks:` block**, so both services sit on
the compose default bridge network (one shared stack network). `ahawr-postgres`
publishes **no port** to the host; inside the stack it is reachable at
`ahawr-postgres:5432`. `ahawr-dashboard` publishes only
`127.0.0.1:${AHAWR_DASHBOARD_PORT:-8701}:8701` (loopback-only) and reaches its
sources at `http://claude-runner:8700` and the Hermes API via `host.docker.internal`.
The network route from the dashboard to Postgres **already exists** (default network,
host `ahawr-postgres:5432`); what is missing is only the driver and the DSN. Note:
`RETRIEVAL_DATABASE_URL` (docker-compose.yml:90) belongs to the `ahawr-retrieval`
service, not to claude-runner. The dashboard's environment today carries no DSN
setting at all (`DASHBOARD_RUNNER_URL`, `DASHBOARD_RUNNER_API_KEY`,
`DASHBOARD_HERMES_URL`, `DASHBOARD_HERMES_API_KEY`, `DASHBOARD_HERMES_SOURCE` only).

**Dashboard tests pass in /tmp/venv:**
Command: `cd claude-runner && PYTHONPATH=src /tmp/venv/bin/pytest tests -k dashboard --no-cov -q`
Result: **38 passed, 169 deselected, 1 warning** (exit 0). The 38 = 36 from
`test_dashboard.py` + 2 from `test_dashboard_js.py` (the node-based ones, since
node is present).

**Windows-only known failures:** none recorded. The only platform-conditional test
is `tests/test_dashboard_js.py`, which skips with a manual procedure **if node is
absent** — node is present here, so both of those tests ran and passed. No other
test is documented as Windows-only.

**Chosen approach — direct `psycopg[binary]` connection from `ahawr-dashboard` via
`DASHBOARD_BOARD_PG_DSN`.**

Justification:
- The board needs **reads and writes** with **optimistic locking** and a
  **transaction** for the changelog; a helper HTTP endpoint would itself need a
  driver, a DSN and a new service, and would still have to talk to Postgres from
  somewhere. `psycopg[binary]` is the one dependency the mission allows, so the
  dashboard connects directly.
- The role is **least-privilege** (board-role.sql).
- `ahawr-dashboard` and `ahawr-postgres` already share the compose default network
  (route exists: `ahawr-postgres:5432`), so **only the env variable**
  (`DASHBOARD_BOARD_PG_DSN`, referenced in docker-compose.yml without a value) is
  needed there; no published port and no new network.
- The `psycopg[binary]` wheel **ships its own libpq**, so **no apt package is
  required** (the Dockerfile's apt line stays as it is: `python3 python3-venv git
  ca-certificates curl ripgrep procps`, plus `uv` + `.[dev]` into the 3.12 venv,
  Dockerfile lines 11 and 27-30).

## 9. Host scripts (`data-tables/ops`, read-only study T005)

### `real_queue.sh` (194 lines)

- `QUEUE` — line 16 (22 mission ids, lines 16-38; telegram first, then the
  llama speed-up missions, then the rest). Companion lists: `RETRY_FAILED`
  line 39, `ATTEMPTS10` line 40, `ATTEMPTS5` line 41, `NEEDS_FILE` line 42,
  `JSA` path line 43.
- Helpers: `running()` line 47 (count of AHAWR v13 executions in
  running/new/waiting), `status_of()` line 48 (mission state from the Live
  State table), `progress_of()` line 49 (event count so far), `alert()` line 50.
  `docker_wait()` line 53, `n8n_up()` line 62, `ensure_stack()` line 72
  (llama-cpp-server, claude-runner, ahawr-litellm + n8n health, up to 20 min).
- `check_env` — line 87: llama-cpp-server healthy and serving the prod model
  `qwen3.8-27b-gsq-rco-iq2-s-mtp` with `--ctx-size 131072` and
  `override-tensor` (lines 89-95), claude-runner and ahawr-litellm healthy
  (line 97); returns 1 on any problem, problems go to stdout.
- `db_window` — line 102 (`db_window <mission_id>|- [attempts]`): waits for
  zero running executions (line 105), dumps the four data tables to
  `/d/n8n/data/pg-backups` (line 109), then a Python heredoc via `pgdb`
  updates the hermes_config row `id=5`: `worker_model=<prod local model>`,
  `worker_provider='local'`, `reviewer_model='sonnet'`, `architect_model='opus'`,
  `max_attempts_per_task=<att>` (lines 110-122) and enables the mission in the
  missions table when `mid != "-"` (line 120). The first window (`mid="-"`,
  lines 123-131) additionally runs `sync_missions_db.py`, `enable_thinking.sh`,
  `ensure_prod_preset.sh` and recreates claude-runner; `n8n_up` at line 133.
- DRY mode: lines 137-150 — `check_env` (line 139), per-mission enabled/state
  report (lines 140-148), exit 0.
- Real start: wait for the bench marker in `model_bench.log` (line 152),
  `check_env` to `.real_env.txt` (line 154), first `db_window - 3` (line 155).
- Mission loop: `fails=0` line 157; `for m in $QUEUE` line 158:
  skip `completed` (line 160), skip `failed` unless in `RETRY_FAILED` (line
  161), skip if its `NEEDS_FILE` is absent (line 162), attempt count 3/5/10
  (line 163), `db_window "$m" "$att"` (line 164), optional publish export
  (line 165). Inner run loop (lines 168-187): up to 6 `launch_mission.sh`
  tries (lines 171-175), then `wait_mission_end.sh` (line 178); if the state
  is still `active` the mission is relaunched, with "no progress in 4 runs"
  (lines 181-183), "15 runs" (line 184) and 60 s delay (line 186) exits.
  Per mission end: `ended $m: $st` (line 188); on success `fails=0`, otherwise
  `fails++` and "two failures in a row" stops the queue (lines 189-192).
  Final log line 194.
- **Where a pause flag could be checked between missions** (no such check
  exists today — the loop only skips, it does not stop): between the start of
  iteration and `db_window` at line 164, i.e. after line 163 — e.g.
  `if [ "$PAUSED" = 1 ]; then sleep <interval>; continue; fi` inserted before
  line 164. An alternative location is at the top of the loop, after line
  158, before the status checks. Either spot is per-mission, so a pause stops
  the queue without disturbing an already running mission.

### `launch_mission.sh` (60 lines)

Pre-flight: no AHAWR execution running (line 14), no A/B/C test container on
8033 (line 15), prod llama healthy when the Worker is local (line 18). It
exports the workflow `7oMrsFqTiYMy0JJN`, re-points the `Get Mission` filter to
the mission id (lines 20-32), runs the idempotent patches (notice split,
telegram model/mission, retry, blind guard, `think_config_patch.py` lines
33-38), ensures the mission's Live State row exists (line 40), patches and
re-imports the Run Manager workflow (lines 42-52), validates the Telegram
expressions and imports the patched workflow (lines 53-56), then starts the
execution detached via `n8n-exec-dt.js` (lines 58-59).

### `sync_missions_db.py` (27 lines)

Updates or inserts mission rows in the missions table from
`data-tables/missions/mission_*.csv`: version, enabled, name, objective,
rules_json, acceptance_criteria_json, state_namespace, working_directory
(lines 20-26); asserts no `\n8n` in `working_directory`+`objective` (line 19).

### `pgdb.py` (61 lines)

Postgres helper (no deps): `T(name)` quotes a table name (line 15), `lit(v)`
escapes a value (lines 20-30), `_psql` runs psql inside `ahawr-postgres`
(lines 44-49), `q(sql, params)` returns a list of dicts via
`json_agg` (lines 52-54), `x(sql, params)` returns the number of rows changed
(lines 57-61).

### `queue_watch.py` (266 lines, notifications only)

Every 60 s: notices for new `real_queue.log` lines — mission ended/not
completed (with the last review via `last_review`, line 100), ALERT lines,
queue stopped/done (lines 203-217); `docker_watchdog.log` notices (lines
218-224); restarts the queue when its bash process is gone, at most 4 times
per hour and only when no AHAWR execution runs (`queue_alive` line 122,
`executions_running` line 129, `start_queue` line 182, restart block lines
230-241); one idle notice after 20 minutes (lines 243-251); notices capped
at 20/hour (lines 42-52, pending retries lines 55-66); every 6 h calls
`think_archive.py` (`archive_think_steps`, lines 161-167); keeps `tg_relay.py`
alive (`ensure_relay`, lines 170-179).

## 10. Thinking statistics formulas (for reimplementation in `board_think.py`
without importing ops)

Shared source — `think_data.load_attempts` (think_data.py lines 31-64),
`load_attempts(real_only=True, refresh=False)`:
- join the event table `data_table_user_ceJ7PFzVJvPN0LsV` rows with
  `event_type in ('worker_completed','review_parsed')`, keyed by
  (state_key, task_index, task_attempt); an attempt needs both events and the
  worker run to exist in the step archive.
- for the attempt's worker run, `mine` = all runs in the same session with
  `finished_at <= worker_completed time + 2 min`; if there was a previous
  attempt, additionally `started_at >= previous review time - 2 min`.
- `steps` = concatenation of the per-run step records; attempts with fewer
  than 3 steps are dropped.
- per attempt: `pass` = `review_status == 'pass'`; `score` =
  `review_score` (0 if none); `secs` = sum of (finished_at - started_at) over
  `mine`; `prev_score` = score of attempt-1's review (0 if none); `policy` =
  the last non-empty `think_policy` among `mine`; `variant` = the variant name
  for `task` (1-based) under that policy; `mode` = "think" if
  `think_share >= 0.3`, "nothink" if `<= 0.05`, else "mixed", where
  `think_share` = fraction of steps with `think` true. `task` is 1-based
  (`task_index + 1`).

Policy JSON format (think_policy.py, also the docstring of
`claude-runner/src/claude_runner/think_policy.py`):
- a policy is a JSON object: `{"variants": [...], "from_task": N}`.
- a variant: `{"name", "weight", "first", "retry", "repeat"}`;
  `first`/`retry` are timelines of segments `[mode, calls]`, mode in
  `{"think","off"}`, `calls` a whole number >= 1 or `null` (null = until the
  attempt ends; only the last segment may be null).
- `first` = the first attempt at a task; `retry` = an attempt after a review;
  one missing = a copy of the other; both missing = never thinks (`[["off",null]]`).
- `repeat: true` repeats the timeline from the start when it is over (then
  every segment needs a number of calls).
- `weight` 1..20 (default 1): the share of tasks; task n uses the variant at
  position `(n - 1) mod (sum of weights)` of the weights laid out in order
  (expand each variant `weight` times; the runner's `Policy.variant_for`,
  think_policy.py line 73).
- `from_task`: the policy applies from this task number on (default 0 = the
  workflow's setting).
- the text is lower-cased on its way from the workflow.
- legacy form: `pulse:O:T:P[:fN][:rN]` with `|`-separated variants; for a
  string policy, `variant_name` returns the part at
  `(task_number - 1) % count` (think_policy.py lines 169-177).

Mode bucket: `mode = "think" if share >= 0.3 else ("nothink" if share <= 0.05
else "mixed")` (think_data.py line 61, think_report.py line 67), where
`share = sum(1 for s in steps if s["think"]) / len(steps)`.

Uplift — `think_uplift.py` (lines 1-110):
- filter to attempts whose policy contains `|`, starts with `pulse`, or equals
  `off`; group by variant (lines 24-30).
- `tasks_of(items)` (lines 34-47): per (mission, task), attempts needed to
  finish = attempts up to the first passing one (or the last one if none);
  `mins` = their total seconds / 60; `first` = whether attempt 1 passed.
- bootstrap 90% intervals (lines 50-58): 2000 resamples with replacement (seed
  1), each sample = one draw per item; interval = the 5th and 95th percentiles
  of `mean(sample_x) - mean(sample_y)`.
- per variant vs the control variant (default `off`): pass-rate difference in
  percentage points with a bootstrap interval (line 79-80); time to finish a
  task (minutes) and attempts per task with bootstrap intervals when both arms
  have at least 5 tasks (lines 81-86).
- minimum-data threshold: `MIN_ARM = 100` (line 21); a T-learner runs only
  when both arms have at least `MIN_ARM` attempts (lines 87-90), otherwise it
  reports how many more attempts are missing.
- T-learner (lines 91-102): features known at call 10 of an attempt —
  `attempt_ge2 = 1 if attempt >= 2 else 0`, `prev_score`, `task` — standardized
  (`StandardScaler`) over both arms; one `LogisticRegression(C=0.5)` per arm
  on `pass`; uplift = `P_arm1(pass) - P_arm0(pass)` on all attempts; reported
  mean uplift and share of attempts with uplift > 0.
- retries only (attempt >= 2) per variant (lines 104-110).

Bandit — `think_bandit.py` (lines 1-64):
- reward of a variant = passes per hour of Worker time. Per variant:
  `passes` = number of passing attempts; pass probability ~
  `Beta(1 + passes, 1 + fails)` (line 37); time per attempt resampled
  (bootstrap) from its attempts, at least 5 draws, mean in hours (line 38);
  `rate = p / hours` (line 39).
- 10 000 Monte-Carlo draws (line 32, seed 7, line 30): per draw, sample one
  Beta draw per variant, take the best rate, count wins; `P(best) = wins / N`
  (line 45).
- proposed policy: each variant gets `max(1, round(8 * wins/N))` slots (line
  46), trimmed to at most 8 total (lines 47-48). If the current policy is JSON
  (starts with `{`), set each variant's `weight` to its slot count and keep the
  JSON (lines 51-58); otherwise build a string policy `variant|variant|...` in
  descending slot order (line 60). `--apply` writes it to
  `hermes_config.worker_think_policy` (row id 5, line 63).

Features — `think_features.features(steps, k, attempt=1, task=1, prev_score=0.0)`
(think_features.py lines 11-44), pure Python, over the first `k` steps:
- from the tools of those steps (each tool: `name`, `key`, `err`, step index i):
  `tools_per_step`, `bash_share`/`read_share`/`grep_share` (Grep+Glob)/
  `edit_share` (Edit/Write/NotebookEdit/MultiEdit), `err_count`,
  `err_rate`, `err_last10` (errors in the last 10 tool calls), `edits`,
  `files_edited` (distinct tool keys), `stall` = steps since the last edit
  (or all steps if none), `max_repeat` = most frequent (name,key) pair,
  `repeat_ratio` = 1 - distinct_pairs/tools, `bash_repeat` = most frequent Bash
  command key.
- tests (TEST_WORDS list, line 8): `tests`, `tests_err`, `tests_err_rate`,
  `tests_passed_last`/`tests_failed_last` (from the last tool call with
  `tests`), `test_runs_with_result`, `tests_best_passed`.
- timing: `elapsed_min`, `sec_per_step`, `tok_s` (out tokens per second of
  generation), `out_mean`.
- context: `ctx_last`, `ctx_growth`.
- thinking: `think_share`.
- identity: `k`, `attempt_ge2`, `attempt`, `task`, `prev_score`.
- `denied` = Bash/Read tools with an error (line 43).

Report — `think_report.py` (lines 1-231), sections: era (before policy /
`off_first` / `pulse`) x mode x first-attempt vs retry (lines 101-108); mode x
attempt number (lines 110-117); mode x duration buckets and x steps (lines
119-131); retry after a failed attempt, with vs without thinking, and score
change (lines 133-157); finishing a task: time/attempts by the mode of the
first attempt and era (lines 159-193); per mission (lines 195-204); time lost
in long failed attempts (lines 206-216); variants of the pulse policy (lines
219-231). `era` = the run's `think_policy` (`off_first` if that exact text,
`pulse` if any other non-empty policy, else "before"); `variant` only in the
pulse era (lines 63-65).
