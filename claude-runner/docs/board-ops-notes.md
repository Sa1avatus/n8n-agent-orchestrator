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
