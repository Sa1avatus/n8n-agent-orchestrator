# Board ops — design

Design of the operations part of the mission board, built on the conventions
recorded in `claude-runner/docs/board-ops-notes.md` (T001–T007): one store
(`BoardStore`, optimistic locking), one FastAPI app (the dashboard on port
8701), host-side scripts run detached via `Win32_Process.Create`, no external
packages, parameterized SQL only.

## Architecture

Two components with a strict split of duties:

- **Dashboard (writer)** — the `claude-runner` FastAPI app (`dashboard.py`)
  gains the ops routes under `/api/board` using the existing `add_board_routes`
  mounting pattern (board_api.py:137-160) and the existing static-file serving
  pattern (`GET /static/{name}`, `Cache-Control: no-store`, dashboard.py:125-144).
  The browser only *writes* commands (after confirmation for dangerous ones)
  and *reads* results, settings, the agent heartbeat and the published
  watcher/environment data. The dashboard container has no docker and no host
  processes, so it never executes anything.
- **Host agent (executor)** — `data-tables/ops/board_agent.py`: plain Python,
  no external packages, started detached exactly like `queue_watch.py`
  (CIM `Win32_Process.Create` of `D:\Python312\python.exe
  D:\n8n\data-tables\ops\board_agent.py`, per queue_watch.py:170-179). It polls
  `board.commands` every few seconds, executes **only** whitelisted actions,
  writes the `board.agent` heartbeat row, publishes the tail of
  `real_queue.log` into `board.queue_log` and a parsed watcher/environment
  snapshot into `board.host_snapshot`.

Shared rules:

- Commands are rows in `board.commands`; the dashboard writes them, the agent
  executes them. There is no other execution path.
- **No arbitrary shell commands.** Every argv the agent runs is built from
  fixed dictionaries inside the agent (service names, kill patterns, script
  paths, preset names, notification script). Unknown actions, or arguments
  outside the allowed values, are rejected with `status = rejected` and a
  reason recorded in `result_json`.
- **No argument carries a path or a container name** — the allowed values
  (services, scripts, presets) come from the dictionaries in the agent
  itself, not from the request.
- **One command runs at a time**; stale (running past the timeout) commands
  are marked `failed` (see "Command protocol").
- Secrets (`.env` values, keys, tokens, DSN, chat ids) are never read into a
  command result or shown in the UI; results are truncated and scrubbed of
  anything that looks like a credential.

## Tables

All in the `board` schema (per `claude-runner/docs/board-schema.sql:7`
`CREATE SCHEMA IF NOT EXISTS board;`), created idempotently the same way the
existing seven tables are. Version columns follow the existing convention:
ISO-8601 / timestamptz stamps compared as the optimistic-locking version.

### board.commands

One row per command. Columns:

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | sequence |
| `action` | text | whitelisted action name |
| `args_json` | jsonb | arguments, validated against the whitelist |
| `status` | text | `new` → `running` → `done` / `failed` / `rejected` |
| `requested_by` | text | dashboard user name |
| `requested_at` | timestamptz | when the dashboard wrote the row |
| `started_at` | timestamptz | when the agent claimed it |
| `finished_at` | timestamptz | terminal state |
| `result_json` | jsonb | result or rejection reason (scrubbed, truncated) |
| `confirm_token` | text | set on dangerous commands at confirmation; kept for the audit trail |

Every write of `status` goes through the same store layer as the rest of the
board (`BoardStore` abstract methods + `InMemoryBoardStore` + `PgBoardStore`,
parameterized SQL, `SELECT ... FOR UPDATE` + version check in the Pg store).

### board.agent

One row per heartbeat, newest first:

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | sequence |
| `heartbeat_at` | timestamptz | last agent poll timestamp |
| `version` | text | agent script version string |
| `host_info_json` | jsonb | host facts (python version, disk free, uptime) |

The UI disables action buttons when `heartbeat_at` is older than **30 s**
(banner "host agent not responding").

### board.settings

Key/value table for operator-tunable values:

| Column | Type | Meaning |
|---|---|---|
| `key` | text PK | settings key |
| `value_json` | jsonb | validated value |
| `updated_at` | timestamptz | version for optimistic locking |

Keys and defaults (defaults equal the current values in `real_queue.sh`):

| Key | Type / range | Default | Used by |
|---|---|---|---|
| `loop_no_progress_runs` | int 1–10 | 3 | real_queue.sh patch |
| `loop_max_runs_per_mission` | int 1–100 | 15 | real_queue.sh patch |
| `loop_max_consecutive_failures` | int 1–10 | 2 | real_queue.sh patch |
| `loop_docker_wait_minutes` | int 10–600 | 120 | real_queue.sh patch |
| `loop_max_failed_launches` | int 1–20 | 6 | real_queue.sh patch |
| `rate_limit_commands_per_minute` | int 1–30 | 5 | command creation |
| `stale_command_timeout_seconds` | int 60–3600 | 600 | stale reaper |
| `model_pairs` | list of `{name, worker_model, worker_provider, reviewer_model, reviewer_provider}` | (the currently approved pairs from `hermes_config`) | `switch_model_pair` |
| `notify_events` | list from {mission_end, mission_failed, alert, docker} | [mission_end, mission_failed] | `send_test_notification` / agent notices |

The dashboard form validates ranges and writes with optimistic locking
(`expected_version` = the row's `updated_at`, ConflictError → 409 like the
board writes); every write lands in `board.changelog` with the previous row.

### board.queue_log

Published tail of `real_queue.log`:

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | sequence |
| `at` | timestamptz | line timestamp (parsed from the line prefix) |
| `line` | text | the log line (verbatim, no secrets in it) |

The agent reads incrementally, the same way `queue_watch.py:new_lines`
tracks byte offsets; it keeps the newest 1000 rows and prunes older ones.
The mission card shows the loop-protection counters from these lines
("no progress in N runs", "giving up", "two failures in a row").

### board.host_snapshot

Published watcher/environment data, one row per agent poll:

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | sequence |
| `at` | timestamptz | snapshot time |
| `data_json` | jsonb | parsed data |

`data_json` contents (all parsed, read-only, no action):

- Docker VM: `avail_mb`, `swap_used_mb`, PSI `mem_some10/mem_full10/io_some10/io_full10` (from the `docker_watchdog.ps1` probe of `/proc/meminfo` + `/proc/pressure/*`, docker_watchdog.ps1:97-103) — the VM memory graph.
- Watchdog: last action, restart count per day, last incident (from `docker_watchdog.csv` / `docker_incidents.csv` tails).
- Queue: alive or not, current mission/task, age of the last event, protection counters (from `real_queue.log` head/tail).
- `queue_watch`: last sent notices (from `queue_watch.state.json` / `real_queue.log`), notices/hour.
- `tg_relay`: alive, last update id (from `tg_relay.offset`), last log line.
- n8n container: running (process count of `n8n-autonomous-agents` via `docker ps`).
- Whitelisted service health: the four `health` booleans (per the health-check table below).
- `hermes_config`: current worker/reviewer/architect model+provider (read-only).
- Memory limits: current `mem_limit`/`memswap_limit` of claude-runner and ahawr-retrieval, `CLAUDE_RUNNER_PROC_DATA_LIMIT_MB`, `MEM_GUARD_*` values.
- Hardware (GPU power limit, Afterburner curve, GPU governor): **display only**, never changed — shown from `gpu_power_governor.log` head, display-only per the mission rule.

## Command protocol

Lifecycle: `new → running → done | failed`, with `rejected` possible at
admission time (dashboard validation or agent validation).

**Write (new).** `POST /api/board/commands` with `{action, args, requested_by}`:

1. Server validates the action against the whitelist and the args against the
   allowed values (same dictionary the agent uses; unknown action/arg → 400).
2. Rate limit: reject with 429 if the number of `board.commands` rows with
   `requested_at` in the last 60 s is already `rate_limit_commands_per_minute`.
3. Dangerous actions require a `confirm_token` (see below); without it the
   request gets a pending token, not a row.
4. Otherwise insert a row `status = 'new'` (parameterized INSERT, same
   optimistic-lock store layer as the other board tables).

**Claim (running).** The agent, each poll, in one transaction:

```sql
BEGIN;
SELECT id FROM board.commands WHERE status = 'new'
 ORDER BY requested_at LIMIT 1 FOR UPDATE;
SELECT count(*) FROM board.commands WHERE status = 'running';
-- if the count above is > 0: ROLLBACK and skip this poll
UPDATE board.commands SET status = 'running', started_at = now()
 WHERE id = <claimed id> AND status = 'new';
COMMIT;
```

The `UPDATE ... WHERE status = 'new'` makes the claim atomic; the
"single running command" invariant is the `count(*)` check in the same
transaction (a second concurrent claim rolls back).

**Execute.** The agent runs the whitelisted argv (see the whitelist). Each
step's output is captured; any step that fails aborts the remaining steps.

**Finish (done / failed).** On success: `status = 'done'`, `finished_at = now()`,
`result_json` = per-step summary (truncated, scrubbed). On failure:
`status = 'failed'`, `result_json` = error summary. For actions with a
health check after (rebuild_service), success means the health probe
passed; a failed probe is `failed`, not `done`.

**Rejected.** A command is written `status = 'rejected'` (not `new`) when the
agent finds, at claim time, an argument or precondition it does not accept —
e.g. `rebuild_service` while a mission is running, `restart_tg_relay` when
the relay is already alive or disabled. The reason goes into `result_json`.

**Stale timeout.** A `running` command older than
`stale_command_timeout_seconds` (default 600 s) is marked `failed` with reason
"stale (running longer than timeout)". The agent reaps its own rows; when the
agent heartbeat is itself stale (> 30 s), the dashboard reaper applies the same
rule so a dead agent can never leave a command stuck in `running`.

**Health check per whitelisted service** (read-only reference for the
post-action checks and for the snapshot):

| Service | Health URL / command |
|---|---|
| claude-runner | `docker exec claude-runner sh -c 'curl -s -m 8 -H "Authorization: Bearer $CLAUDE_RUNNER_API_KEY" localhost:8700/health'` |
| ahawr-dashboard | `http://localhost:8701/healthz` |
| ahawr-retrieval | `docker compose exec ahawr-retrieval python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8500/health').read().decode())"` |
| ahawr-litellm | `docker compose exec litellm python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness', timeout=3)"` |

Task-boundary check (for `rebuild_service`, `pause_at_boundary`,
`set_llama_preset`): the agent polls `GET localhost:8701/api/runs`
(header `X-AHAWR-Dashboard: 1`) the way `_deploy_memguard.sh:8-24` does —
among the first 8 runs take the `role == 'worker'`, `status == 'running'`
one; no such run = no running mission. `pause_at_boundary` waits until the
task id in that run's title changes, with an 8 h deadline.

## Confirmation and rate limit

**confirm_token — 60 s, single use, bound to action and args.**

For a dangerous action the dashboard does not create the command row
immediately:

1. `POST /api/board/commands` with a dangerous action: the server validates
   the action and args, then returns a pending confirmation object
   `{confirm_token, expires_at = issued_at + 60 s, description}` describing
   what will be interrupted/changed (e.g. which run is canceled, which service
   is rebuilt, which memory value is set). No row is written yet.
2. The token is `HMAC-SHA256(server secret, action + args_json + nonce)`
   (first 32 hex chars); the server keeps pending tokens in memory
   `{token: (action, args_json, issued_at)}` and prunes entries older than
   60 s.
3. The user confirms in the UI; the dashboard re-sends
   `POST /api/board/commands/confirm` with `{action, args, confirm_token}`.
   The server checks: the token is present, unexpired, and its binding
   `(action, args_json)` matches the request; the token is then **consumed**
   (removed before the row is inserted), so it is single-use. Only then is
   the row written: `status = 'new'`, `confirm_token` stored in the row for
   the audit trail.
4. A replay of the same token, a token bound to different args, or an
   expired token → 400 ("token expired / already used / does not match").
   The pending-token store is in-memory: a server restart discards unconfirmed
   tokens, which is acceptable because a token is only valid 60 s.

**Rate limit.** At most `rate_limit_commands_per_minute` (default 5) command
rows may be created per 60 s, counting all rows (including rejected) whose
`requested_at` falls in the window. Exceeding it → 429 with the current count.
The limit is itself a `board.settings` key so the operator can tune it.

**One command at a time.** Guaranteed by the claim transaction above: a new
claim is refused while any row is `running`.

## Action whitelist

Fixed dictionary in `board_agent.py`: `action → executor function` with an
argument validator per action. Any action outside this table, or any argument
value outside the allowed values, is rejected with a reason. All argv is
taken from the agent's dictionaries (`SERVICES`, `KILL_PATTERNS`,
`SCRIPTS`, `PRESETS`, `PAIRS`) — the request only supplies dictionary keys.

| Action | Args (allowed values) | Dangerous | Task boundary required | Effect |
|---|---|---|---|---|
| `stop_mission` | none | yes | no | Cancel the running Worker run via the runner API, then kill the queue chain; mission state untouched, a later resume continues from the current task |
| `pause_at_boundary` | none | yes | yes (waits for the boundary itself) | Wait until the worker task id changes (8 h deadline), then stop the queue chain and stop the n8n container |
| `resume` | none | no | no | Start `queue_watch`, which starts the queue itself |
| `restart_queue` | none | no | no | Kill the queue chain, relaunch `real_queue.sh` detached |
| `restart_queue_watch` | none | no | no | Kill `queue_watch`, relaunch it |
| `restart_docker_watchdog` | none | no | no | Relaunch `docker_watchdog.ps1` |
| `restart_tg_relay` | none | no | no | Relaunch `tg_relay` (only when the liveness count is 0 and the `tg_relay.enabled` marker exists; otherwise rejected) |
| `rebuild_service` | `service` ∈ {claude-runner, ahawr-dashboard, ahawr-retrieval, ahawr-litellm} | yes | yes (only at a task boundary, or with no mission running) | `docker compose up -d --build <service>`, then the service's health probe must pass |
| `docker_update_memory` | `service` ∈ {claude-runner, ahawr-retrieval}; `gb` ∈ {2,3,4,5,6,7,8} | yes | no | `docker update --memory=<gb>g --memory-swap=<gb>g <service>` |
| `set_llama_preset` | `preset` ∈ {`enable_thinking`, `ensure_prod_preset`} | yes | yes | Run the existing `enable_thinking.sh` / `ensure_prod_preset.sh` unmodified, wait up to 450 s for a healthy llama-server |
| `switch_model_pair` | `pair` = a key of `board.settings.model_pairs` | no | no | Parameterized update of `hermes_config` (`worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`) with optimistic locking; effective from the next run |
| `send_test_notification` | none | no | no | Send one test message through the host notification path |

**Explicit exclusions.** There are **no actions for GPU/hardware settings**
(GPU power limit, Afterburner curve, GPU governor — display only), and **no
actions for `llama-cpp-server` or `ahawr-postgres`** (llama-cpp-server lives in
a separate compose stack with its own apply scripts; ahawr-postgres is never
started or stopped by the ops scripts and is reached only via
`docker exec ahawr-postgres psql`). No argument in any action carries a path
or a container name; the only service names an argument may take are the
whitelisted ones above.

**argv per action (all from agent dictionaries):**

- `stop_mission`:
  1. `curl -s -m 20 -X POST -H 'X-AHAWR-Dashboard: 1' localhost:8701/api/runs/{run_id}/cancel` — `run_id` is resolved by the agent from `GET /api/runs` (first worker/running run, per the task-boundary check), never taken from the request.
  2. `powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '[r]eal_queue\.sh|[w]ait_mission_end|[l]aunch_mission' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"` (bracketed patterns so powershell never matches itself).
- `pause_at_boundary`: task-boundary wait (poll `GET /api/runs`, 8 h deadline), then the queue-chain kill above plus `docker stop -t 30 n8n-autonomous-agents`.
- `resume`: `powershell.exe -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"`.
- `restart_queue`: the queue-chain kill above, then
  `powershell.exe -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='C:\Program Files\Git\usr\bin\bash.exe -c \"export PATH=/usr/bin:/mingw64/bin:$PATH; cd /d/n8n/data-tables/ops && NOWAIT=1 /usr/bin/bash real_queue.sh >> real_queue.out 2>&1\"'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"`.
- `restart_queue_watch`: kill pattern `[q]ueue_watch\.py` (taskkill tree), then the CIM Create of `D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py`.
- `restart_docker_watchdog`: `powershell -NoProfile -ExecutionPolicy Bypass -File D:\n8n\data-tables\ops\docker_watchdog.ps1`.
- `restart_tg_relay`: liveness count of `python.exe` matching the two-piece pattern `tg_relay`+`.py` must be 0 and marker `tg_relay.enabled` must exist, then CIM Create of `D:\Python312\python.exe D:\n8n\data-tables\ops\tg_relay.py`.
- `rebuild_service`: `docker compose up -d --build <service>` (run from `/d/n8n`), then the service's health probe from the table above must pass.
- `docker_update_memory`: `docker update --memory=<gb>g --memory-swap=<gb>g <service>`.
- `set_llama_preset`: `bash D:\n8n\data-tables\ops\enable_thinking.sh` or `bash D:\n8n\data-tables\ops\ensure_prod_preset.sh` (paths from the `SCRIPTS` dictionary; argv: none), then wait up to 450 s for the llama-server health probe.
- `switch_model_pair`: parameterized `UPDATE "data_table_user_1S21PsMqT0FpR7eD" SET ... WHERE "updatedAt" = %s` via `pgdb.x()` — values from the `model_pairs` setting, never from a free-text field.
- `send_test_notification`: run the host notification script (`notify.py` path from the `SCRIPTS` dictionary) with the fixed test message; bot token stays in `.env` and is never read into logs or results.

## Modules

### Python modules (dashboard side)

All under `claude-runner/src/claude_runner/`, following the existing store/route
patterns of `board.py`, `board_pg.py`, `board_api.py`:

- **`board.py`** — extend the `BoardStore` ABC with the new abstract reads
  (`commands`, `settings`, `agent_heartbeat`, `queue_log`, `host_snapshot`)
  and the status transitions (`claim_command`, `finish_command`,
  `reject_command`, `reap_stale`, `upsert_setting`, `append_queue_log`,
  `insert_host_snapshot`); implement them in `InMemoryBoardStore` with the
  same `_check_version` / `_audit` order as the existing writes.
- **`board_pg.py`** — implement the same methods on `PgBoardStore` with
  parameterized SQL, `SELECT ... FOR UPDATE` + version check in one
  transaction (the claim transaction from "Command protocol"), table-name
  constants for the five new tables, and the changelog audit row for settings
  and status writes.
- **`board_commands.py`** — the command protocol server-side: whitelist
  dictionary shared with the agent (action → allowed args), dangerous-action
  list, `POST /api/board/commands` (validation, rate limit, pending confirm
  token), `POST /api/board/commands/confirm` (token consume + row insert),
  `GET /api/board/commands` (filterable journal), `GET /api/board/commands/{id}`,
  the stale reaper (runs on each dashboard poll when the agent heartbeat is
  stale), result scrubbing (truncate, strip credential-like strings).
- **`board_api.py`** — mount the new routers from `_build_router` /
  `add_board_routes` exactly like the existing board routes.
- **`data-tables/ops/board_settings.py`** — host helper used by the patched
  `real_queue.sh`: `get_setting(key, default)` via `pgdb.q()` with the default
  fallback when the database is unreachable (no external packages).

### Host agent

- **`data-tables/ops/board_agent.py`** — the executor: poll loop (every few
  seconds), whitelist dictionary with per-action argument validators and
  executors (all argv from fixed dictionaries), atomic claim, heartbeat write
  (`board.agent`), `queue_log` tail publication (incremental offset tracking
  like `queue_watch.py:new_lines`), `host_snapshot` publication (parsed
  `real_queue.log` head/tail, `queue_watch.state.json`, `docker_watchdog.csv` /
  `docker_vm.csv` tails, `tg_relay.offset`, `docker ps` for n8n, the four
  health probes), rejection and stale reaping. Execution calls
  (`docker`, `powershell.exe`, `curl`) are isolated behind a small
  `run(argv)` seam so tests substitute a fake executor — no real
  docker/powershell/taskkill in tests.

### Static files

- **`board_ops.html`** — the "Операции" page served at `GET /static/board_ops.html`
  by the existing `_board_static` pattern (dashboard.py:125-144), linked from
  the board tab bar next to "Прогоны | Миссии"; loads
  `board_ops.js` and `board_ops.css`.
- **`board_ops.js`** — panels: processes (queue/watchers/n8n/worker state),
  watchers (VM memory graph, last notices, relay), environment (model pairs,
  presets, memory limits, mem_guard events, hardware display-only), action
  journal with filter and "repeat safe action"; two-step confirmation dialog
  showing the consequences before the token is sent; "host agent not
  responding" banner (heartbeat > 30 s) disabling all action buttons;
  auto-refresh that does not clobber dirty forms; all values through
  `esc()` (board_logic.js:23-31).
- **`board_ops.css`** — light/dark variables and `prefers-color-scheme` block,
  same variable set as `board.css` / `dashboard.html`.

### real_queue.sh patch (described, not applied by the mission)

Five parameter lines of `real_queue.sh` are replaced by reads from
`board.settings` via `board_settings.py` (defaults = current values, so a
database outage keeps the script working):

| real_queue.sh line | Current | Patched source |
|---|---|---|
| 183 `$same -ge 3` | no-progress after 4 runs | `loop_no_progress_runs` |
| 184 `$run -ge 15` | max runs per mission | `loop_max_runs_per_mission` |
| 191 `$fails -ge 2` | consecutive failures stop the queue | `loop_max_consecutive_failures` |
| 54 `lim=${1:-120}` | docker wait minutes | `loop_docker_wait_minutes` |
| 171 `for try in 1 2 3 4 5 6` | max failed launches | `loop_max_failed_launches` |

`real_queue.sh` itself is not modified by the mission; the patch text is
documented here for the operator to apply in a pause.
