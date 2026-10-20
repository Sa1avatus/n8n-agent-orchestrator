# Board ops notes (first mission)

Conventions the operations part must follow, recorded from the first-mission board
modules (`claude-runner/src/claude_runner/board*.py`, `board*.js/html/css`,
`dashboard.py`). All paths are relative to the repository root.

## Board conventions

### Store interface
- File: `claude-runner/src/claude_runner/board.py`
- `class BoardStore(ABC)` — board.py:157. Every read returns a copy; every write
  takes `expected_version` (ISO-8601 `updatedAt` of the row as read by the
  caller); `None` disables the check; a mismatch raises `ConflictError`. Every
  write saves the full previous row to the changelog first (board.py:157-164).
- Abstract reads: `list_missions` (168), `get_mission` (172), `list_live_states`
  (176), `get_live_state` (180), `events` (184), `hermes_config` (192), `queue`
  (196), `mission_config` (200), `task_overrides` (204), `templates` (208),
  `flags` (212), `preflight` (216), `changelog` (216, newest first, default
  limit 200).
- Derived views: `running_missions` (220), `mission_groups` (229),
  `task_views` (260).
- `audit_rollback` (base) — board.py:417: the rollback's own changelog row
  (`action` "rollback"), written after the write that applies the restored
  value; the Pg store writes it inside the same transaction (board.py:417-430).
- Helpers: `now()` — board.py:41 (ISO-8601 with microseconds so successive
  writes never stamp the same version), `ConflictError` — board.py:48 (mapped
  to HTTP 409, "план изменился, обновите"), `GuardError` — board.py:60 (HTTP 403),
  `get_board_store` factory — board.py:1096 (deferred import of `board_pg`).

### In-memory store
- File: `claude-runner/src/claude_runner/board.py`
- `class InMemoryBoardStore(BoardStore)` — board.py:532. Thread-safe
  (`threading.RLock`, board.py:540); versions are ISO-8601 `updatedAt` stamps;
  every write saves the full previous row to the changelog, then applies the
  change and stamps a new version (board.py:532-537).
- Version check: `_check_version` — board.py:562-566
  (raises `ConflictError` when `expected` is not None and differs from the
  stored stamp).
- Audit write: `_audit` — board.py:568-590 (appends a `ChangelogEntry` to
  `self._changelog` with a sequential id; `at=now()`).
- Example write order (audit before change): `update_mission` — board.py:678
  (version check 685, audit 694, replace row 697).
- `audit_rollback` — board.py:658-675.
- `changelog(limit)` — board.py:654-656 (newest first).
- Running-mission guard: `_guard_running` — board.py:592-597.
- Factory: `UnavailableBoardStore` (Pg module, board_pg.py:86) with
  `status()` ("unavailable", reason) and `reason` never carrying the DSN
  (board_pg.py:100-114); `get_board_store` in board.py:1096.

### Postgres store
- File: `claude-runner/src/claude_runner/board_pg.py`
- `class PgBoardStore(BoardStore)` — board_pg.py:270. One connection, no
  autocommit; every write runs in a transaction in the audit order:
  `SELECT ... FOR UPDATE` (lock), compare the version, write the changelog row
  first, then apply the change (board_pg.py:270-284, 572).
- Table-name constants (from `docs/board-schema.sql`): `_MISSIONS_TABLE`,
  `_LIVE_STATE_TABLE`, `_HERMES_TABLE` (quoted mixed-case physical names)
  — board_pg.py:44-47; `_QUEUE_TABLE` — board_pg.py:50; `_CHANGELOG_TABLE` —
  board_pg.py:51.
- Parameterized SQL: all values go through `%s`/named placeholders, never
  concatenated (board_pg.py:270-284); e.g. the update at board_pg.py:637-641.
- Changelog insert: `_audit` — board_pg.py:300-322
  (`INSERT INTO changelog (at, who, action, mission_id, task_key, before_json,
  after_json) VALUES (now(), %s, %s, %s, %s, %s, %s)`); rollback audit row
  `audit_rollback` — board_pg.py:577-599.
- Queue version: `_queue_version` — board_pg.py:324-335 (latest `board.changelog`
  stamp; the in-memory store keeps a single stamp `_queue_version`).
- Connection: `__init__` — board_pg.py:290-297 (`psycopg.connect`,
  `SET search_path TO board, public`).
- Factory: `get_board_store` — board_pg.py:1295 (DSN env `DASHBOARD_BOARD_PG_DSN`,
  board_pg.py:40; unavailable stub when unset).

### Optimistic lock (version / updated_at check and conflict response)
- In-memory: `_check_version` — board.py:562-566; called in every write, e.g.
  board.py:685, 706-707, 859.
- Postgres: `_conflict` — board_pg.py:351-352 (raises
  `ConflictError(f"{kind} {key!r}: expected version {expected}, got {stored}")`);
  check after the `FOR UPDATE` lock, e.g. board_pg.py:614-615 (`mission`),
  655-656 (`live state`), 739 (`hermes_config`), 793/831/867/899/1043
  (queue/config).
- HTTP mapping: `ConflictError` → 409 via `_on_conflict` —
  board_api.py:163-164 (envelope `{"status":"failed", "error":{"code":"conflict",
  "message":...}, "code":"conflict", "status_code":409}`, board_api.py:48-58);
  exception handler registered in `add_board_routes` — board_api.py:153.
- Version stamps: `now()` — board.py:41 (microsecond precision); data-model
  reference: the `updatedAt` column as the optimistic-locking version —
  `claude-runner/docs/board-data-model.md:508-518`.

### Changelog write / rollback
- Interface: `BoardStore.changelog` — board.py:215-217; in-memory read —
  board.py:654-656; Pg read — board_pg.py:553-567 (`SELECT ... ORDER BY at DESC
  LIMIT %s`, `_read_json` for before/after).
- Write order: the full previous row is saved to the changelog **before** the
  change lands (board.py:157-164; in-memory `_audit` board.py:568-590; Pg
  `_audit` board_pg.py:300-322 inside the write transaction).
- Rollback: `rollback(store, changelog_id, who)` — board_ready.py:398-447.
  Honoured only while the current row still equals the entry's `after_json`
  (checked field-by-field, version stamps ignored — `_same_values`
  board_ready.py:387-395); otherwise `ConflictError` ("план изменился,
  обновите") — board_ready.py:431-434. The rollback writes its own changelog
  row via `store.audit_rollback` (board_ready.py:441-445), whose backup row is
  the state just before the rollback.
- Route: `POST /api/board/changelog/{changelog_id}/rollback` — board_api.py:527-530.
- Tests of the contract: `claude-runner/tests/test_board_ready.py:365-425`
  (backup row saved before the change; rollback restores the previous value;
  refused when the row changed after the entry).

### Router mounting
- File: `claude-runner/src/claude_runner/board_api.py`
- `add_board_routes(app, settings)` — board_api.py:137-160: keeps a store set
  before lifespan (in-memory in tests), else creates it via
  `get_board_store()` from `DASHBOARD_BOARD_PG_DSN` (board_api.py:142-149);
  registers exception handlers (ConflictError 409, GuardError 403,
  MissionIdError 400, KeyError 404, ValueError 400, RuntimeError 503) at
  board_api.py:153-158; mounts the `APIRouter(prefix="/api/board", tags=["board"])`
  built by `_build_router` (board_api.py:187-883) via `app.include_router`
  (board_api.py:160). Module-level `router = _build_router()` — board_api.py:886.
- Mounted from the dashboard: `board_api.add_board_routes(app, settings)` —
  dashboard.py:109; `X-AHAWR-Dashboard` header guard for POST/PUT/DELETE under
  `/api/board` — `board_header_guard` dashboard.py:98-108.

### Static file serving (board.html / board.js / board.css)
- Served by `dashboard.py` at `GET /static/{name}` — dashboard.py:132-144:
  `_board_static = re.compile(r"^board[a-z_]*[.](js|css|html)$")` (dashboard.py:125),
  media types from `_board_media` (dashboard.py:126-130), read via
  `resources.files("claude_runner")` (dashboard.py:139), `Cache-Control: no-store`
  (dashboard.py:143); anything else or a missing file is 404 (dashboard.py:135-141).
- Page load: `board.html` — `claude-runner/src/claude_runner/board.html:1-42`:
  links `/static/board.css` (board.html:26) and loads
  `board_logic.js`, `board_queue.js`, `board_dnd.js`, `board_actions.js`,
  `board_mission.js`, `board_mission_save.js`, `board_claude_md.js`, `board.js`
  (board.html:33-40), in that order.
- Embedded in the dashboard: iframe `#boardframe` — dashboard.html:242, src
  `/static/board.html#...` — dashboard.html:1030-1046.

### esc() helper
- `function esc(value)` — `claude-runner/src/claude_runner/board_logic.js:23-31`
  (the shared esc; escapes `& < > " '` to entities, `null`/`undefined` → "").
- Local fallback copies: `board_queue.js:32-42` (prefers `BoardLogic.esc` when
  available) and `board_actions.js:28-38`; `board.js:20-28` (`localEsc` /
  `escValue`, used only for shell error messages).
- Contract test: `claude-runner/tests/test_dashboard_js.py:164-209`
  (`test_esc_function`, runs the dashboard's `const esc` arrow in node).

### Theme switching
- `board.css` — `claude-runner/src/claude_runner/board.css:1-27`: `:root`
  light variables (board.css:3-10), `:root[data-theme="light"]` (11),
  `@media (prefers-color-scheme: dark)` auto (12-20), `:root[data-theme="dark"]`
  (21-27). Same variable set as `dashboard.html` (dark/light blocks at
  dashboard.html:28-42).
- Theme bootstrap in `board.html:7-25`: reads `localStorage["ahawr-dashboard-theme"]`,
  sets `data-theme="light"/"dark"` on `<html>`, removes it for Auto; also a
  `storage` event listener (board.html:15-23).
- Dashboard-side control: `#themebtn` — dashboard.html:228; theme persistence
  via the same `ahawr-dashboard-theme` key — dashboard.html:252-279
  (`apply()` mirrors `data-theme` onto the same-origin board iframe,
  dashboard.html:266-274).

### Polling
- Auto-refresh — `board_actions.js:346-349`: `startAutoRefresh()` sets
  `setInterval(tick, 10000)` (10 s); `tick()` (board_actions.js:359-366) skips
  while a drag is active, while the creation form is dirty, and while the tab
  is hidden, then calls `refreshBoard()` (board_actions.js:114-141: loads
  `/api/board/missions` + `/api/board/queue` plus per-mission detail and stats,
  re-renders `#boardqueue`, re-attaches DnD and card actions). `stopAutoRefresh`
  — board_actions.js:351-357; `hookUp()` starts the timer first — board_actions.js:371-372.
- The dashboard runs page polls separately: `setInterval(loadRuns, 3000)` —
  dashboard.html:1078.
- Fetch helper: `fetchJson` — board_actions.js:49-60 (cache `no-store`,
  `body.error.message` on non-OK); `loadBoardData` — board_actions.js:63-69.

### Reference docs
- `claude-runner/docs/board-data-model.md` (566 lines): table layouts
  (`missions` :8, Live State :43, event log :92, `plan_json` :133, task status
  :191, `hermes_config` :427) and the optimistic-locking section :508-518.
- `claude-runner/docs/board-schema.sql` (91 lines): `CREATE SCHEMA IF NOT EXISTS
  board;` (:7) and the seven idempotent tables — `templates` (:12), `queue`
  (:27), `mission_config` (:41), `task_overrides` (:50), `changelog` (:65),
  `flags` (:80), `preflight` (:89).

## Host script conventions

Recorded 2026-10-17 from the ops host scripts `data-tables/ops/pgdb.py`,
`data-tables/ops/queue_watch.py` (launcher + liveness parts only) and the
sibling `data-tables/ops/_setup_board_db.py`. Note: `data-tables/ops/board_queue.py`,
`board_apply_config.py`, `board_preflight.py` and `board_backup.py` are **not present**
in the working tree (they do not exist in git history either); the conventions below
are drawn from the scripts that actually live in `data-tables/ops/`.

### DB access method — `pgdb.py` (shared helper, no external packages)
- File: `data-tables/ops/pgdb.py`. No `psycopg`/`asyncpg`/driver imports — the
  helper talks to `psql` inside the `ahawr-postgres` container via `subprocess`.
- The three public functions the host scripts use (`from pgdb import q, x, T`):
  - `q(sql, params=None)` — pgdb.py:52-54. Runs a `select coalesce(json_agg(t),
    '[]'::json) from (...) t` wrapper and returns a **list of dicts**
    (JSON rows). Reads are always through `q`.
  - `x(sql, params=None)` — pgdb.py:57-61. Runs one statement and returns the
    **number of rows changed**, parsed from the command tag
    (`UPDATE n`, `INSERT n`, `DELETE n`).
  - `T(name)` — pgdb.py:15-17. Returns the data-table name wrapped in double
    quotes (`'"' + name.replace('"','') + '"'`), because the n8n data-table
    physical names are mixed-case and must be quoted in SQL.
- Placeholder filling: `_fill(sql, params)` — pgdb.py:33-41. Splits on `%s`,
  errors if the placeholder/value counts differ; each value is passed through
  `lit()` (pgdb.py:20-30) which quotes strings and doubles single quotes,
  renders `None`→`NULL`, `bool`→`true`/`false`, numbers via `repr`, and rejects
  NUL bytes. So there is no driver-side parameter binding; values are literal-
  escaped into the SQL string.
- Credentials are **not taken from the environment at call time**: `_psql`
  (pgdb.py:44-49) shells out to
  `docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 -At`
  with the script on stdin. The username/database literals (`n8n`/`n8n`) are
  baked into that command; no password is passed, and `.env` is never opened by
  the helper. On a non-zero exit it raises
  `RuntimeError("psql failed: " + (stderr or stdout)[-600:])`.
- Env for the subprocess: `ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}`
  (pgdb.py:12) — MSYS path-conversion disabled so `docker`/`psql` paths are not
  mangled under Git Bash.
- The same `docker exec psql -U n8n -d n8n` pattern (no driver, no password
  passed) is used verbatim by the sibling `data-tables/ops/_setup_board_db.py`
  (`def psql` at :13-18). That script is the only place a role password is
  generated (`secrets.token_urlsafe`) and a DSN is **written to `.env`**
  (`DASHBOARD_BOARD_PG_DSN`) — it is never printed, and the notes here do not
  record any value.

### Logging style
- `queue_watch.py` keeps a plain append log, not `logging`: `log(m)` —
  queue_watch.py:37-39 — opens `WATCH_LOG` (`queue_watch.log`) in append mode
  and writes `time.strftime("%Y-%m-%d %H:%M:%S ") + m + "\n"`. Every state
  change ("started", "sent"/"NOT sent", "queue started again (rc=...)",
  "error: ...") goes through this one function.
- Notice output is capped: `notify()` — queue_watch.py:42-52 keeps a rolling
  1-hour window of `sent_times`, drops notices beyond **20 per hour**, and
  parks un-sendable ones in a `pending` list (max 10) that `retry_pending()`
  (queue_watch.py:55-66) re-sends with a "(с опозданием, ...)" prefix, dropping
  items older than 6 h.
- State (log byte offsets) is persisted to `queue_watch.state.json` via
  `load_state`/`save_state` (queue_watch.py:69-77); `new_lines`
  (queue_watch.py:80-97) tracks per-file offsets so only new lines are
  re-read each minute.

### Win32_Process.Create launch command pattern
- Launches are done from Python via `subprocess.run(["powershell", "-NoProfile",
  "-Command", cmd], ...)` calling the **`Win32_Process.Create`** CIM method
  (`Invoke-CimMethod -ClassName Win32_Process -MethodName Create`), not a
  plain `powershell` `Start-Process`.
- The `real_queue.sh` restart is `start_queue()` — queue_watch.py:182-187:
  - `CommandLine` is `"C:\Program Files\Git\usr\bin\bash.exe" -c "export
    PATH=/usr/bin:/mingw64/bin:$PATH; cd /d/n8n/data-tables/ops && NOWAIT=1
    /usr/bin/bash real_queue.sh >> real_queue.out 2>&1"`.
  - The queue script is launched **detached** by piping its stdout into
    `real_queue.out`; `NOWAIT=1` keeps it from blocking.
  - The CIM call is piped `| Select-Object -ExpandProperty ReturnValue`;
    `start_queue` returns that PID string, which the main loop logs as
    `queue started again (rc={rc})` (queue_watch.py:237).
- The same `Win32_Process -MethodName Create` pattern launches the Telegram
  relay: `ensure_relay()` — queue_watch.py:170-179 (a `python.exe` running
  `tg_relay.py`). The restart is only attempted when the liveness count is 0 and
  the `tg_relay.enabled` marker file exists.

### Process liveness check
- `queue_alive()` — queue_watch.py:122-126: runs
  `powershell -NoProfile -Command "(Get-CimInstance Win32_Process | Where-Object
  { $_.Name -eq 'bash.exe' -and $_.CommandLine -match 'real_queue.sh' } |
  Measure-Object).Count"` and returns `True` when the trimmed output is a
  number > 0. In other words, liveness = "a `bash.exe` process whose command
  line matches `real_queue.sh` is running", detected by counting CIM
  `Win32_Process` instances.
- The same count pattern is used for the slot loop (`slot_loop_running()`
  queue_watch.py:149-155, pattern `'slot_cache'+'.py loop'`) and the relay
  (`ensure_relay` queue_watch.py:170-175, pattern `'tg_relay'+'.py'`). For those
  the match pattern is **split into two pieces** so the watcher's own
  powershell command line does not match itself.
- Restart policy: the queue is re-launched only when it is gone, did not finish
  by itself, `executions_running() == 0` (no `AHAWR v13*` n8n execution running)
  and there have been **< 4 restarts in the last hour** (queue_watch.py:227-241).
- Ancillary checks use `docker exec`: `runner_running()` — queue_watch.py:138-146
  hits the claude-runner `/health` endpoint over `docker exec claude-runner sh -c
  "curl ..."` (counting `running` + `queued` runs); `executions_running()` —
  queue_watch.py:129-135 counts `execution_entity` rows in Postgres via `q`.

## Blockers

- `docs/board-data-model.md` (the task-specified path) is missing; the file
  exists only at `claude-runner/docs/board-data-model.md`.
- `docs/board-schema.sql` (the task-specified path) is missing; the file
  exists only at `claude-runner/docs/board-schema.sql`.
- No other listed artifact is missing: `board.py`, `board_pg.py`, `board_api.py`,
  `board.html`, `board.js`, `board.css`, `board*.js` companions, `dashboard.py`
  are all present under `claude-runner/src/claude_runner/`.

## Loop protection

Parameters in `real_queue.sh` that guard against runaway or stalled missions.

### Parameters

| # | Parameter | Variable / expression | Line | Current value | Proposed board.settings key |
|---|-----------|----------------------|------|---------------|------------------------------|
| 1 | Consecutive runs without new events (no-progress) | `$same -ge 3` (alert "no progress in 4 runs") | 183 | 3 (i.e. 4 runs total) | `loop_no_progress_runs` |
| 2 | Maximum runs per mission | `$run -ge 15` (alert "15 runs, giving up") | 184 | 15 | `loop_max_runs_per_mission` |
| 3 | Consecutive mission failures that stop the queue | `$fails -ge 2` (alert "two failures in a row") | 191 | 2 | `loop_max_consecutive_failures` |
| 4 | Docker wait in minutes | `lim=${1:-120}` inside `docker_wait()` | 54 | 120 | `loop_docker_wait_minutes` |
| 5 | Maximum failed launches | `for try in 1 2 3 4 5 6` (alert "6 tries") | 171 | 6 | `loop_max_failed_launches` |

### Log line formats (verbatim samples)

- **Run start** (real_queue.sh:177):
  `2026-10-17 14:23:44 started ahawr-dashboard-ops-control (run 1, attempts per task: 5)`

- **No-new-events retry / interrupted relaunch** (real_queue.sh:185):
  `2026-10-17 06:58:34 execution of ahawr-dashboard-mission-board was interrupted (state active, 153 events): relaunching`

  The no-progress alert (real_queue.sh:183) is written to `real_queue.alert` (not the log):
  `ALERT: mission $m: no progress in 4 runs, giving up`

- **Mission failure** (real_queue.sh:190):
  `2026-10-17 14:23:18 MISSION NOT COMPLETED: llamacpp-prefill-kernels (failed)`

- **Mission done** (real_queue.sh:188):
  `2026-10-17 13:51:51 ended ahawr-dashboard-mission-board: completed`

- **Task change** (launch_mission.sh:29, written to `real_queue.log` via `tee -a "$LOG"` at real_queue.sh:178):
  `Get Mission: llamacpp-prefill-kernels -> ahawr-dashboard-ops-control`

## Process control

Recorded 2026-10-17 from `data-tables/ops/_deploy_memguard.sh`, `data-tables/ops/_rerun_prefill.sh`,
`data-tables/ops/launch_mission.sh`, `data-tables/ops/queue_watch.py`,
`data-tables/ops/docker_watchdog.ps1`, `claude-runner/src/claude_runner/api.py`,
`run_views.py`, `dashboard.py` and `docker-compose.yml`. The whitelisted services are
`claude-runner`, `ahawr-dashboard`, `ahawr-retrieval`, `ahawr-litellm`; **`llama-cpp-server`
and `ahawr-postgres` are excluded** from this section: llama-cpp-server lives in a separate
compose stack (`D:\OpenAIProjects\llama.cpp`, port 8033) and is managed by its own apply
scripts, and `ahawr-postgres` is never started or stopped by the ops scripts — it is reached
only through `docker exec ahawr-postgres psql ...` (no published port). No script was
modified or executed for this note.

### Task boundary detection

Two reference ways, both read-only:

1. **Dashboard run list** (`_deploy_memguard.sh:8-24`): poll
   `curl -s -m 20 -H 'X-AHAWR-Dashboard: 1' localhost:8701/api/runs` every 15 s. Among the
   first 8 runs take the one with `role == 'worker'` and `status == 'running'` and extract the
   task id `T\d+` from its `title`. The first such id is recorded as `first`; the boundary is
   the poll where the id **changes** ("task changed to …: pausing"). If the worker list stays
   empty until the 8 h deadline (28800 s) the script logs `timeout` and exits 1.
2. **Queue log line** (`_rerun_prefill.sh:8-11`): record `n0=$(wc -l < real_queue.log)`, then
   wait (3 s poll) until a new line — `tail -n +$((n0+1)) real_queue.log` — contains
   `ended <mission>:` (there: `ended llamacpp-speed-tuning:`). The boundary is the
   `ended <mission>:` line in `data-tables/ops/real_queue.log`.

Preconditions before a mission start (`launch_mission.sh:13-18`):
- DB field: the count of `execution_entity` rows joined to `workflow_entity` where the
  workflow name matches `AHAWR v13%` and `status in ('running', 'new', 'waiting')` must be **0**
  (queried through `n8nq.sh` → `docker exec ahawr-postgres psql ...`).
- If the mission's `worker_provider` (row id 5 of `data_table_user_1S21PsMqT0FpR7eD`) is
  `local`, `docker inspect -f '{{.State.Health.Status}}' llama-cpp-server` must be `healthy`.

### Exact argv of each process action

`kill_tree` (`_deploy_memguard.sh:7`, `_rerun_prefill.sh:7`) is
`powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '<pattern>' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"`.

- **Stop queue_watch** — pattern `[q]ueue_watch\.py`:
  `powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '[q]ueue_watch\.py' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"`
- **Stop queue + wait/launch chain** — pattern `[r]eal_queue\.sh|[w]ait_mission_end|[l]aunch_mission`:
  `powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '[r]eal_queue\.sh|[w]ait_mission_end|[l]aunch_mission' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"`
  (bracketed character classes so the powershell process itself never matches its own command line)
- **Stop n8n** (`_deploy_memguard.sh:29`): `docker stop -t 30 n8n-autonomous-agents`
- **Start n8n** (`_deploy_memguard.sh:34`): `docker start n8n-autonomous-agents`
- **Start queue_watch** (`_deploy_memguard.sh:35`, `_rerun_prefill.sh:39`):
  `powershell.exe -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"`
  (queue_watch then starts the queue itself; its queue relaunch is the CIM `Create` of
  `bash.exe -c "… NOWAIT=1 /usr/bin/bash real_queue.sh >> real_queue.out 2>&1"` — `queue_watch.py:182-187`)
- **Restart docker_watchdog** (`docker_watchdog.ps1:3`):
  `powershell -NoProfile -ExecutionPolicy Bypass -File D:\n8n\data-tables\ops\docker_watchdog.ps1`
- **Restart tg_relay** (`queue_watch.py:170-179`; only when the liveness count is 0 and the
  marker file `tg_relay.enabled` exists):
  `powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\tg_relay.py'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"`
- **Rebuild and restart a compose service** (`_deploy_memguard.sh:31`, run from `/d/n8n`):
  `docker compose up -d --build <service>` (e.g. `docker compose up -d --build claude-runner`),
  then wait for readiness with `curl -s -m 5 localhost:8701/api/runs >/dev/null 2>&1`
  (`_deploy_memguard.sh:32`).

### Runner cancel and active-runs endpoints

- **Cancel a running Worker run** — claude-runner `api.py:230-232`:
  `POST /v1/runs/{run_id}/cancel` (Bearer `CLAUDE_RUNNER_API_KEY`). Host-side form used by the
  scripts (dashboard proxy, `dashboard.py:274-291`):
  `curl -s -m 20 -X POST -H 'X-AHAWR-Dashboard: 1' localhost:8701/api/runs/{run_id}/cancel`
  (`_deploy_memguard.sh:30`, `_rerun_prefill.sh:19`).
- **Active runs list** — claude-runner `run_views.py:61` (mounted at `/v1`, `api.py:222-224`):
  `GET /v1/runs?limit=…&session_id=…&role=…&status=…` (Bearer auth; each summary carries
  `status`, `role`, `live`). Host-side form:
  `curl -s -m 20 -H 'X-AHAWR-Dashboard: 1' localhost:8701/api/runs`
  (`_deploy_memguard.sh:8`, `_rerun_prefill.sh:15`).

### Health check per whitelisted service

No port of these containers is published to the host, so claude-runner/retrieval/litellm are
checked from inside via `docker exec`; dashboard is reachable on `127.0.0.1`.

| Service | Health URL / command |
|---|---|
| claude-runner | `docker exec claude-runner sh -c 'curl -s -m 8 -H "Authorization: Bearer $CLAUDE_RUNNER_API_KEY" localhost:8700/health'` (`queue_watch.py:138-146`; `/health` is open — `api.py:165-196`) |
| ahawr-dashboard | `http://localhost:8701/healthz` — probed every 20 s by `docker_watchdog.ps1:198`; compose healthcheck `python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8701/healthz', timeout=3)"` |
| ahawr-retrieval | from inside: `docker compose exec ahawr-retrieval python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8500/health').read().decode())"` (`docs/retrieval/INTEGRATION.md:33`); stack-internal URL `http://ahawr-retrieval:8500/health` |
| ahawr-litellm | from inside: `python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=3)"` (`litellm/Dockerfile` healthcheck; same check in `board_ready.py:145-161`); stack-internal URL `http://litellm:4000/health/liveliness` |

## Environment

Recorded 2026-10-17 (T007), read-only, from `claude-runner/src/claude_runner/board_pg.py`,
`board.py`, `api.py`, `runs.py`, `config.py`, `mem_guard.py`, `claude_cli.py`,
`docker-compose.yml`, `.env.example`, `data-tables/ops/enable_thinking.sh`,
`ensure_prod_preset.sh`, `data-tables/ops/docker_watchdog.ps1`,
`data-tables/ops/gpu_power_governor.py` and `/d/OpenAIProjects/llama.cpp/docker-entrypoint.sh`.

### hermes_config — location and fields

- **Location**: a PostgreSQL table inside `ahawr-postgres`, physical name
  `"data_table_user_1S21PsMqT0FpR7eD"` (quoted, mixed case) —
  `board_pg.py:47` (`_HERMES_TABLE`). Exactly one row: the `claude-code` profile
  (`board.py:188-189`). Reads: `SELECT * FROM ... LIMIT 1` (`board_pg.py:466-472`);
  in-memory twin: `InMemoryBoardStore._hermes` (`board.py:620-622`).
- **Model/provider fields** of that row (`known_hermes_cols`, `board_pg.py:745-760`):
  `worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`,
  `architect_model`, `architect_provider`, plus `service_model`/`service_provider`,
  the poll/retry fields (`*_poll_seconds`, `*_max_polls`, `*_max_retries`,
  `retry_delays_json`), the retrieval fields (`retrieval_enabled`, `retrieval_url`,
  `retrieval_corpora_json`, `retrieval_*_max_tokens`, `retrieval_timeout_ms`,
  `retrieval_label`), `runner_url`, `run_manager_workflow_id`,
  `worker_think_policy`, `worker_think_policy_from_task`,
  `worker_think_off_after_seconds`, `worker_think_off_after_steps`, `enabled`,
  `max_tasks`, `max_attempts_per_task`, and the fixed `profile_id`.
- **Switch mechanism**: the Hermes/n8n workflow sends the run's settings per run in the
  `POST /v1/runs` body (`RunBody`, `api.py:30-42`: `model`, `provider`, `role`, the
  think-policy fields — comment "thinking policy of the local Worker from hermes_config")
  and in the headers `X-ahawr-role`, `X-hermes-session-id`,
  `X-hermes-working-directory` (`api.py:198-219`). The runner starts the CLI with that
  model/provider (`runs.py:320-329`); the provider block is isolated and all of Claude
  Code's secondary model names (`ANTHROPIC_DEFAULT_OPUS_MODEL`, … `CLAUDE_CODE_SUBAGENT_MODEL`)
  default to the run's model, so `hermes_config.*_model` alone decides the model
  (`claude_cli.py:213-214, 221-229`). The row itself is written by the board with optimistic
  locking: `update_hermes_config(expected_version, **fields)`, `profile_id` immutable,
  `updatedAt` bumped (`board_pg.py:726-769`, `board.py:307-311`); the dashboard form edits
  the same fields (`board_mission.js:88-93`).
- **Cloud runs**: the policy is forwarded for Anthropic Workers too, but the runner applies it
  only to local runs unless `CLAUDE_RUNNER_CLOUD_THINK=1` is set; see
  [`docs/cloud-thinking.md`](../../docs/cloud-thinking.md).
- `board_ready.py:279-295` reads `worker_model` from the row to probe
  llama-server's `/v1/models` and its `--ctx-size`.

### llama thinking mode and presets

- **Preset source**: `/d/OpenAIProjects/llama.cpp/docker-entrypoint.sh` — the preset
  file is regenerated from the script at every container start ("Generate initial
  preset", `docker-entrypoint.sh:596-607`); sections other than `[*]` are "manual" and
  are not auto-modified (`docker-entrypoint.sh:599-601, 768-789`).
- **List of presets** (section names, `docker-entrypoint.sh:610, 622, 625, 688, 723`):
  - `[*]` — global defaults (ctx-size 65536, n-gpu-layers 99, q8_0 KV cache, …)
  - `[gemma4-12b]` — `parallel = 10` only
  - `[qwen3.8-27b-gsq-rco-iq2-s-mtp]` — the **prod** preset (ctx 131072, MTP n-max 4,
    thinking on, `slot-save-path = /slots`)
  - `[qwen3.8-27b-gsq-rco-iq2-s-mtp-gpu96]` — bench variant (96K, same GGUF)
  - `[qwen3.8-27b-gsq-rco-iq3-xxs-mtp]` — thinking on, MTP n-max 8
  - `[$MODEL_NAME]` — auto-generated per model (MTP detection), appended by the scan loop
- **Thinking mode**: the per-preset keys `reasoning = on`, `reasoning-budget = 4096`,
  `reasoning-format = deepseek` (`docker-entrypoint.sh:666-668, 717-719, 751-753`);
  the same-prefix jinja template (`qwen38-same-prefix.jinja`, `docker-entrypoint.sh:672`)
  keeps the prompt cache when `enable_thinking` switches per request. Per-request
  switching: the runner's `CLAUDE_RUNNER_THINK_FLAG_FILE=/app/runtime/local_thinking`
  (on/off flag read by LiteLLM on every request, no restart — `docker-compose.yml:152-159`)
  and the dashboard API passes `enable_thinking` / `reasoning_effort` as
  `chat_template_kwargs` (`/d/OpenAIProjects/llama.cpp/dashboard/backend/main.py:1894-1902`).
  The dashboard exposes the model config as `GET/PUT /api/models/{model_id}/config`,
  `effective-config`, `reload`/`load` (`main.py:1529-1793`); it does not read or write
  the entrypoint preset file.
- **`data-tables/ops/enable_thinking.sh`** (read-only; operator 2026-10-11):
  - argv: none (no arguments).
  - Does: backs up `docker-entrypoint.sh` to `.bak-$STAMP-thinkon`, then a Python pass
    over the preset that, in the sections
    `qwen3.8-27b-gsq-rco-iq2-s-mtp` and `qwen3.8-27b-gsq-rco-iq3-xxs-mtp`, replaces
    `reasoning = off` with `reasoning = on`, `reasoning-budget = 4096`,
    `reasoning-format = deepseek`; removes the
    `CLAUDE_RUNNER_PROVIDER_(ANTHROPIC|LOCAL)__CLAUDE_CODE_DISABLE_THINKING=` lines from
    `/d/n8n/.env`; restarts `llama-cpp-server` only when a preset line changed and waits
    up to 450 s for healthy. Idempotent; logs to `real_queue.log`.
- **`data-tables/ops/ensure_prod_preset.sh`** (read-only):
  - argv: none (no arguments).
  - Does: ensures the prod preset `[qwen3.8-27b-gsq-rco-iq2-s-mtp]` has
    `spec-draft-n-max = 4`; if it differs, backs up the entrypoint (`.bak-ensure`,
    renamed to `.bak-$STAMP-ndepth`), rewrites the line, restarts `llama-cpp-server`,
    waits for healthy, then prints the live `--spec-draft-n-max` from
    `GET /v1/models` (Bearer `LLAMA_API_KEY`) into `real_queue.log`. Idempotent.

### Memory variables and defaults

- **`CLAUDE_RUNNER_PROC_DATA_LIMIT_MB`** — default 4096 MB
  (`config.py:409, 496`); the data-segment limit (`RLIMIT_DATA`) of the `claude`
  process and everything the Worker starts: the command runs under
  `prlimit --data=<N MiB>` (`runs.py:439-447`); 0 = off; an over-allocation gets
  `MemoryError` (traceback) instead of an OOM kill; the Worker's system prompt carries
  the MEMORY RULE when the limit is set (`runs.py:330`, `claude_cli.py:106-107, 154`).
- **`CLAUDE_RUNNER_MEM_GUARD_PROC_MB`** — default 5000 (`config.py:405, 495`): the memory
  guard kills the biggest Worker process holding more than this much anonymous memory
  (`mem_guard.py:121-124`); 0 = off.
- **`CLAUDE_RUNNER_MEM_GUARD_CGROUP_PERCENT`** — default 85 (`config.py:410, 497`): kill
  the biggest process when the container's anonymous memory passes this share of its
  limit (`mem_guard.py:125-135`).
- The guard is started when `mem_guard_proc_mb > 0` (`api.py:119-128`).
- **Not in `docker-compose.yml`** (verified): no `CLAUDE_RUNNER_MEM_GUARD_*` or
  `CLAUDE_RUNNER_PROC_DATA_LIMIT_MB` entries; the defaults live in code
  (`config.py:405-410, 495-497`) and are documented in `.env.example:92-96`
  (commented-out reference values).
- **Container caps** (`docker-compose.yml`):
  - `ahawr-retrieval`: `mem_limit: 4g`, `memswap_limit: 4g` (line 99-100) — 1536m was too
    small (OOM kill on first search of every start, 122 restarts); 2560m→4g history in the
    comment.
  - `claude-runner`: `mem_limit: 6g`, `memswap_limit: 6g` (line 131-132) — set 2026-10-15
    so a Worker script reading the 9 GB GGUF into RAM dies inside the container instead
    of taking the Docker VM down.
- The ops mission allows changing them only via `docker update --memory`, 2-8 GB, with
  confirmation (`data-tables/missions/mission_ahawr-dashboard-ops-control.csv:12`).

### mem_guard events in run logs

- Event appended to the run's event log for every running local Worker
  (`mem_guard.py:182-202`, `MemGuard._report`): JSON line
  `{"kind": "mem_guard", "pid": <pid>, "process": <name>, "memory_mb": <anon_mb>,
  "reason": <…>, "command": <first 300 chars of the process cmdline>}`.
  The reason is either `holds <N> MB of memory (limit <M> MB per process)`
  (`mem_guard.py:123`) or `the container uses <A> of <L> MB; this is the biggest process
  (<B> MB)` (`mem_guard.py:133-134`).
- The runner's own log line: `memory guard killed pid <pid> (<name>): <reason>`
  (`mem_guard.py:175`). The Worker itself sees exit code 137 / "Killed".
- Never killed: pid 1 (the runner), the guard's own process, the `claude` CLI
  (`mem_guard.py:34, 113-117`); the guard scans every second (`interval=1.0`).

### Docker VM memory

- The memory source is the Docker Desktop WSL2 VM itself (its size is set by
  Docker Desktop / `wslconfig`, not by any file in this repo).
- Measured by `docker_watchdog.ps1`: `wsl.exe -d docker-desktop -- sh -c "grep -E
  '^(MemAvailable|SwapTotal|SwapFree)' /proc/meminfo; cat /proc/pressure/memory
  /proc/pressure/io"` (`docker_watchdog.ps1:97-103`) → one line per engine-alive probe
  in `docker_vm.csv`: `time, avail_mb, swap_used_mb, mem_some10, mem_full10,
  io_some10, io_full10`; under-memory warning when `avail_mb < 400` or
  `mem_full10 > 20` (once per 15 min) goes to the text log and `notify.py`
  (board-ops-notes.md "docker_watchdog.ps1 — output CSVs", lines 428-435).

### Hardware — display only (never changed by the board)

- **GPU power limit / GPU governor**: `data-tables/ops/gpu_power_governor.py` — dry run by
  default (logs `gpu_power_governor.log` + `.jsonl`); with `--apply` it switches
  `nvidia-smi -pl 170` / `-pl 110.5` through elevated scheduled tasks
  `schtasks /run /tn GPU_PL_HIGH|GPU_PL_LOW` (lines 12-13, 188-189). Display only.
- **Afterburner curve**: no readable source in this repo (no script touches it); the
  mission doc lists it with the other hardware settings as display-only
  (`data-tables/missions/mission_ahawr-dashboard-ops-control.csv:12` — "Аппаратные
  настройки (лимит мощности GPU, кривая Afterburner, GPU governor) НЕ менять: только
  показывать").

## Watchers

Recorded 2026-10-17 from `docker_watchdog.ps1`, `queue_watch.py`, `tg_relay.py`, the live output
files in `data-tables/ops/` (heads/tails only) and the state file.

### docker_watchdog.ps1 — output CSVs

`docker_watchdog.csv` — one line per 20 s probe. Columns:
`time, engine, engine_ms, n8n, dashboard, llama, free_ram_gb`
- `time` — probe timestamp, `yyyy-MM-dd HH:mm:ss`
- `engine` — `1`/`0`: the Docker engine answered `docker version` (12 s hard limit) or not
- `engine_ms` — probe duration in ms (`12000` on timeout)
- `n8n`, `dashboard`, `llama` — HTTP status of `http://localhost:5678/healthz`,
  `http://localhost:8701/healthz`, `http://localhost:8033/health`; `0` = unreachable
- `free_ram_gb` — host free RAM in GB, dot decimal (locale-safe so the column never splits)

`docker_vm.csv` — one line per engine-alive probe (memory inside the docker-desktop WSL VM).
Columns:
`time, avail_mb, swap_used_mb, mem_some10, mem_full10, io_some10, io_full10`
- `avail_mb` — `MemAvailable` in MB; `swap_used_mb` — swap used in MB
- `mem_some10`, `mem_full10` — PSI memory `some`/`full` avg10;
  `io_some10`, `io_full10` — PSI io `some`/`full` avg10
- written only when the engine answers; an under-memory warning (avail < 400 MB or
  `mem_full10` > 20, once per 15 min) goes to the text log and to `notify.py`

Related outputs: `docker_watchdog.log` (text log, `{time} {message}` lines),
`docker_incidents.csv` (`start, end, duration_s, kind, action, note`),
`docker_snapshots/*.txt` (one snapshot per failed-probe episode), and
`real_queue.alert` when the watchdog gives up restarting (3 restarts/hour cap).

### queue_watch.state.json

Keys: `q` — byte offset read so far in `real_queue.log`; `d` — byte offset read so far in
`docker_watchdog.log`. Sample: `{"q": 108253, "d": 15135}`. First run starts from the end of
each file; a truncated file restarts from 0.

### tg_relay — how it runs, heartbeat

- Runs forever as a host process: `python tg_relay.py` (started detached; the watcher
  relaunches it when it is gone, see below). Long-polls `getUpdates` (timeout 50 s,
  limit 50, `allowed_updates: message, callback_query`) and POSTs every update to the
  n8n webhook `http://localhost:5678/webhook/<AHAWR_TG_WEBHOOK_PATH>`.
- The bot token is read from `.env` (`TELEGRAM_BOT_TOKEN`) via the `env()` helper; it is
  never written to any log file (error messages replace it with `<token>`).
- Files it writes: `tg_relay.offset` — `update_id + 1`, written **only after** n8n
  accepted the update (so nothing is lost while n8n is down); `tg_relay.log` —
  `relay started, offset N`, `setMyCommands: ok=…, N commands`,
  `forwarded update N (message|callback)`, `error #N: …`.
- **No heartbeat file or heartbeat line.** Liveness is checked by `queue_watch.py`
  `ensure_relay()` (queue_watch.py:170-179): a CIM `Win32_Process` count of `python.exe`
  processes whose command line matches the two-piece pattern `tg_relay`+`.py`. When the
  count is 0 **and** the marker file `tg_relay.enabled` exists, the relay is started via
  CIM `Win32_Process.Create` (`python.exe tg_relay.py`). On any error it retries with
  `sleep(min(60, 3*fails))`; errors 1-3 and every 20th are logged.

### Notifications queue_watch sends

Sent via `notify.py`, capped at 20 notices/hour; unsent notices are retried for up to 6 h
(prefix `(с опозданием, событие в HH:MM)`). Trigger lines and notice types:
- `MISSION NOT COMPLETED: <mission> (<state>)` — ❌ mission not completed, with the last
  review from the events table (`last_review()`)
- `ended <mission>: completed` — ✅ mission completed
- `REAL QUEUE STOPPED: <reason>` (not containing `ALERT`) — ⛔ queue stopped, the watcher
  will restart it when n8n is free
- `ALERT: <text>` — ⚠️ alert; skipped for `DISABLE_THINKING` env lines,
  "new missions were not inserted" and REAL QUEUE STOPPED lines
- `=== real queue done` — 🏁 all missions finished or skipped
- new `docker_watchdog.log` lines: "restarting Docker Desktop" — 🐳 Docker hung, restart
  in progress; "containers are back" — 🐳 Docker back; "not restarting again" — 🐳
  too frequent hangs, manual intervention needed
- queue relaunch — 🔁 queue started again; nothing running for 20 minutes while the queue
  is alive — 💤 idle notice

## Live State

Physical table: `data_table_user_innMqY8MMiUE9Lrf` (n8n data table "Autonomous Agent
Live State"). Row key: `state_key` (string, PK), matched against the Config node value
`state_namespace` (e.g. `"llama.cpp-dashboard-v1"`).

**Database access note:** No live SELECT was run from this environment against the
real database. `pgdb.q()` shells out to `docker exec ahawr-postgres psql -U n8n -d
n8n` (`pgdb.py:44-49`), but the `docker` binary is not available in this container,
`psql` is not installed, no `DASHBOARD_BOARD_PG_DSN` / `PGPASSWORD` / `PGUSER` is
set, and a raw SCRAM-SHA-256 TCP probe to `ahawr-postgres:5432` fails at the
authentication stage (no password available). All column names and types below are
therefore **UNVERIFIED (from docs)** and are taken from the sources cited here.

### Telegram control flags

| Column | Type (UNVERIFIED) | Meaning |
|--------|------|---------|
| `state_key` | string (PK) | Filter key = Config.state_namespace. |
| `workflow_status` | string | Set by `/stop` (`"stopped"`) or `/restart` (`"starting"`). |
| `owner` | string | Run owner chat ID (set from `p.chat_id`). |
| `current_run_id` | string | Carried over on stop/restart. |
| `paused` | boolean | Set by `/pause [reason \| off]`; true pauses, false resumes. |
| `pause_reason` | string | Reason text, or `''` when `/pause off`. |
| `steer_note` | string | Appended operator note from `/steer` (after existing `operator_feedback`). |
| `operator_feedback` | string | Prior feedback text, read as input for `/steer`. |
| `last_control` | string | Set for resume/retry/accept/start verbs. |
| `queue` | array (mission IDs) | Managed by `/queue [add <mission>\|list\|rm N\|move A B\|clear]`. |
| `updated_at` | string (ISO-8601) | Set on every mutation; the optimistic-locking version for this table. |

**`retry_requested` and `accept_requested` are NOT confirmed as existing columns.**
`scripts/patch_status_bot_control.py:52-53` maps the `/retry` and `/accept` verbs to
those flag names, and line 803 writes them as columns on "Autonomous Agent Live
State" via the Apply Mutation node. However, `docs/telegram/control-bot-findings.md`
(§ Defect 2, line 269-275) lists them under "New columns needed (not present in the
documented schema and absent from both files)" — i.e. they may not yet exist in the
actual table. Treat them as UNVERIFIED; do not present them as confirmed.

### Two distinct Live State tables — do not mix

- **Control-flag table** (this section): `data_table_user_innMqY8MMiUE9Lrf`
  ("Autonomous Agent Live State"). Used by `ensure_live_state_row.py:11-15` (only
  queries `state_key`) and by the n8n status-bot workflow. Its version column is
  `updated_at` (text, ISO-8601 per docs).
- **Board mission table**: `data_table_user_Yn9lOhVhQ9gFymNs`. Used by
  `board_pg.py:45` (`_LIVE_STATE_TABLE`). Contains mission plan/status columns
  (`worker_session_id`, `plan_json`, `task_index`, `task_attempt`, `status`,
  `review_feedback`, `last_review_score`, `last_review_reason`,
  `reviewer_session_id`, `reviewer_run_id`, `reviewer_status`,
  `worker_run_id`, `worker_status`, `architect_run_id`, `architect_status`,
  `architect_session_id`) and **no control-flag columns** (`paused`, `steer_note`,
  `queue`, etc.). Its version column is `"updatedAt"` (timestamptz, per
  `board-data-model.md:70`).

The two tables serve different purposes and their schemas must not be conflated:
the control-flag table has no mission-plan columns, and the board table has no
control-flag columns.

### Version column for optimistic locking

For the control-flag table (`data_table_user_innMqY8MMiUE9Lrf`), the version column
is **`updated_at`** (string, ISO-8601) per `docs/telegram/control-bot-findings.md:191`.
A writer reads the current `updated_at`, performs the update, and writes a new
`updated_at`; if the stored `updated_at` changed since the read, the update is a
conflict and should be retried. This is UNVERIFIED — no live SELECT was run to
confirm the column exists in the actual table.

For the board table (`data_table_user_Yn9lOhVhQ9gFymNs`), the version column is
**`"updatedAt"`** (timestamptz) per `board-data-model.md:70` and used by
`board_pg.py:650-656` (`SELECT "updatedAt" ... FOR UPDATE` then compare). Note
`board_pg.py:657-664` `known_live_cols` includes `updated_at` (text) as a writable
data column but uses `"updatedAt"` for the version check.

### Exact parameterized SELECT/UPDATE statements

Control-flag table (`data_table_user_innMqY8MMiUE9Lrf`):

```sql
-- Read one row by state_key (UNVERIFIED column list, from docs)
SELECT "state_key", "workflow_status", "owner", "current_run_id",
       "paused", "pause_reason", "steer_note", "operator_feedback",
       "last_control", "queue", "updated_at"
  FROM "data_table_user_innMqY8MMiUE9Lrf"
 WHERE state_key = %s;

-- Optimistic-lock UPDATE: set a flag and bump updated_at
-- (UNVERIFIED: assumes updated_at exists in this table)
UPDATE "data_table_user_innMqY8MMiUE9Lrf"
   SET paused = %s, pause_reason = %s, "updated_at" = %s
 WHERE state_key = %s AND "updated_at" = %s;
```

Board table (`data_table_user_Yn9lOhVhQ9gFymNs`) — version check pattern from
`board_pg.py:650-656`:

```sql
SELECT "updatedAt"
  FROM "data_table_user_Yn9lOhVhQ9gFymNs"
 WHERE state_key = %s FOR UPDATE;
```

The `%s` placeholders are bound by `pgdb.lit()` (see `data-tables/ops/pgdb.py:20-30`).
The `AND "updated_at" = %s` clause in the control-flag UPDATE is the optimistic-lock
guard: the row is only updated if its `updated_at` still equals the version the
caller read. For the board table, `board_pg.py` uses `"updatedAt"` as the guard in
`WHERE state_key = %s` after the `FOR UPDATE` read (the version comparison is done
in application code, not in the SQL).
