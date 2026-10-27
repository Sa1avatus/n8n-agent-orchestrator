# Board ops — host agent: what exists, what is missing, and the server half

Design note for `data-tables/ops/board_agent.py` (the host agent), researched from
`docs/guards.md`, `docs/naming.md`, `claude-runner/docs/board-ops.md`,
`claude-runner/docs/board-ops-notes.md`, `claude-runner/docs/board-ops-schema.sql`
and the runner source. Read-only research; nothing was written to any database.

## What exists

- **Ops tables DDL (the design).** `claude-runner/docs/board-ops-schema.sql` is
  idempotent Postgres: `board.commands` (board-ops-schema.sql:37), `board.agent`
  (:68), `board.settings` (:80), `board.queue_log` (:88), `board.host_snapshot`
  (:97), plus the placeholder roles and grants (:15–:33, :105–:140). The header
  says the file "only adds the operations tables" on top of the board schema
  created by `board-schema.sql` (board-ops-schema.sql:5–6).
- **Ops store.** `claude-runner/src/claude_runner/board_ops_store_pg.py` implements
  all five tables against Postgres. The header says the fixed identifiers "come
  from `docs/board-ops-schema.sql`" (board_ops_store_pg.py:15) and are never
  built from values. The claim transaction is the atomic `board.commands`
  protocol (board_ops_store_pg.py:181–212, docstring at :189–205).
- **Ops store interface and in-memory store.** `board_ops_store.py` defines the
  row types (`Command`, `AgentHeartbeat`, `Setting`) and the `OpsStore` ABC with
  the reads `get_queue_log` / `get_snapshot` (board_ops_store.py:44, :63, :175).
  `InMemoryOpsStore` is used by the QA fixture path
  (dashboard.py:583, `_ops_store`).
- **Ops API routes.** `board_ops_api.py` mounts `GET/POST` routes under
  `/api/board/ops` (board_ops_api.py:13, :186): `/status`, `/watchers`,
  `/environment`, `/settings` (GET/PUT), `/commands` (prepare, confirm, list,
  repeat), `/livestate/{mission}/flags`. The newest `board.host_snapshot`
  `data_json` backs the panels via `_latest_snapshot` (board_ops_api.py:115).
- **Guards on the ops store.** `dashboard.py` builds the guards from the ops
  store (`_build_guards`, dashboard.py:489): `IdleGuard` (dashboard.py:504) and
  `MissionNoticeGuard` (dashboard.py:514). Both read the published rows through
  `_queue_alive` / `_queue_running` (dashboard.py:618, :632), which read the
  newest `board.host_snapshot` `data_json` via `ops_store.get_snapshot(
  "host_snapshot", limit=1)` (dashboard.py:611).
- **Guard implementation.** `guard_queue.py` holds `MissionNoticeGuard`
  ("mission_notice", guard_queue.py:163) and `IdleGuard` ("idle",
  guard_queue.py:261). `MissionNoticeGuard.check` reads
  `queue_log.get_queue_log(QUEUE_LOG_LIMIT)` (guard_queue.py:174) and
  `_notices_for` parses the `line` text (guard_queue.py:215–228); `_last_review`
  reads the `review_parsed` event (`task_index`, `task_attempt`, `review_score`,
  `review_reason`, guard_queue.py:230–240). `IdleGuard.check` reads the
  `running` count and the `alive` flag (guard_queue.py:272–286).
- **Board schema DDL in code.** `board_pg.py` carries a `_DDL` list for the
  existing board tables (`board.templates`, `board.queue`, `board.mission_config`,
  `board.task_overrides`, `board.changelog`, `board.flags`, `board.preflight`,
  board_pg.py:63–88) and fixed table-name constants (board_pg.py:51–61).
  `board_pg.py:310` states the table names come from `docs/board-schema.sql`.
- **QA stack applies the board schema.** `qa_stack.py` reads `board-schema.sql`
  (BOARD_SCHEMA_PATH, qa_stack.py:38) and applies it plus the fixture seed to a
  throwaway Postgres (qa_stack.py:122). It does not apply `board-ops-schema.sql`.
- **Static serving pattern.** The "Операции" page will be served at
  `GET /static/board_ops.html` via the existing `_board_static` pattern
  (dashboard.py:125–144) and loads `board_ops.js` / `board_ops.css`
  (board-ops.md:357–372).

## Command lifecycle

The lifecycle is `new → running → done | failed`, with `rejected` possible at
admission or claim time (board-ops.md:156–159).

**Write (new).** The dashboard is the only writer. A dangerous action goes
through two steps: `POST /api/board/ops/commands/prepare` answers with a
pending confirmation object — `confirm_token` (the real 32-hex credential,
32 chars, board_ops_guard.py:100), `expires_at`, `consequences` and
`command_preview` — and writes no row (board_ops_api.py:388–410);
`POST /api/board/ops/commands/confirm` then consumes the client-held
`token` (400 when missing, expired, unknown, reused or mismatched —
`TokenError`, code "token", board_ops_guard.py:32–36), re-checks the rate
limit (`RateLimiter`, `store.count_recent(60)`, board_ops_guard.py:142–150)
and the single-running check (`check_can_enqueue`, board_ops_guard.py:153–167),
and inserts the row with `status = 'new'` and the consumed token stored in
`confirm_token` (board_ops_api.py:453–457). A safe action is enqueued
directly after the rate-limit and single-running checks
(board_ops_api.py:412–415).

**Claim (running).** The agent calls the store method `claim_next`
(`OpsStore.claim_next`, board_ops_store.py:101–104; the Pg implementation is
`PgOpsStore.claim_next`, board_ops_store_pg.py:295–323): in one transaction
it locks the oldest `new` row (`SELECT ... ORDER BY requested_at LIMIT 1 FOR
UPDATE SKIP LOCKED`), counts the `running` rows and rolls back when the count
is > 0 (single-running-command invariant), then
`UPDATE commands SET status = 'running', started_at = now() WHERE id = ... AND
status = 'new'`. It returns the claimed `Command` or `None` (nothing to
claim, or one is already running). There is no separate claim route: the agent
claims through the store.

**Result write (done / failed).** The agent calls `finish_command(command_id,
status, result)` (`OpsStore.finish_command`, board_ops_store.py:106–110;
`PgOpsStore.finish_command`, board_ops_store_pg.py:325–357): only a
`running` row is accepted, `status` must be `done` or `failed`
(ValueError otherwise), and the row gets `finished_at = now()` and
`result_json = result` (per-step summary, truncated and scrubbed by
`sanitize_result` on read). A command the agent refuses at claim time — a bad
argument or precondition — goes through `reject_command(command_id, reason)`
(`OpsStore.reject_command`, board_ops_store.py:112–114; `PgOpsStore.
reject_command`, board_ops_store_pg.py:359–388), which sets
`status = 'rejected'`, `finished_at = now()` and
`result_json = {"reason": reason}`.

**Stale reaping.** `mark_stale(timeout_s)` (`OpsStore.mark_stale`,
board_ops_store.py:116–119; `PgOpsStore.mark_stale`, board_ops_store_pg.py:390–421)
marks `running` commands older than `timeout_s` as `failed` with
`result_json = {"reason": "timeout"}` and returns them. The dashboard runs
it on every read through `_expire_stale` (board_ops_api.py:609–619),
with the timeout from the `command_timeout_s` setting
(board_ops_settings.py:108–114). The agent's own reap while alive makes its
rows self-healing.

**Row fields.** The claim/update methods read and write the exact
`Command` fields (board_ops_store.py:43–59): `id`, `action`, `args_json`,
`status` (`new`, `running`, `done`, `failed`, `rejected`), `requested_by`,
`requested_at`, `started_at`, `finished_at`, `result_json`,
`confirm_token`.

**Heartbeat.** The agent appends one `board.agent` row per poll with
`put_heartbeat(version, host_info)` (`OpsStore.put_heartbeat`,
board_ops_store.py:143–147; `PgOpsStore.put_heartbeat`,
board_ops_store_pg.py:477–515); the newest row is read by `get_agent`
(board_ops_store.py:139–141, board_ops_store_pg.py:463–475). A heartbeat
older than 30 s (`STALE_AGENT_S`, board_ops_state.py:54, :84–89) makes
`agent_status` report `stale`, which refuses every prepare/confirm/repeat
with 503 (`_stale_agent_error`, board_ops_api.py:622–638) and is the
`actions_enabled` flag in `GET /status` (board_ops_api.py:212–222).

**Confirm state.** A confirmation token lives in memory on the app
(`board_ops_confirm_tokens`, board_ops_api.py:664), is bound to
`action`/`args`/`user`, is single-use and expires after `TOKEN_TTL_S = 60.0`
s (board_ops_guard.py:29); it is stored in the command row's `confirm_token`
column for the audit trail but never appears in any other response
(board_ops_api.py:8–11, :396–410).

## Whitelist

`ACTIONS` (board_ops_actions.py:103–233) is the dashboard-side mirror of the
agent whitelist; `validate_action` (board_ops_actions.py:288–329) rejects an
unknown action, an unknown or missing argument, a value outside its allowed
set (services, presets) or range (memory 2–8 GB), a pair id that is not a
key of the `verified_model_pairs` setting, and any string value carrying a
path separator or a shell metacharacter. The allowed values live in the fixed
dictionaries: `REBUILD_SERVICES` (board_ops_actions.py:36–41),
`MEMORY_SERVICES` (:43), `MEMORY_GB` (:46), `PRESETS` (:49); the `pair`
argument's allowed keys come from the setting's `verified_model_pairs` value
(board_ops_actions.py:213, :262–274). The `consequences` string (Russian,
for the confirmation dialog) and `command_preview` per action are in the
same entries. The "what the agent runs" column is left for T003.

The agent's own fixed dictionaries (script paths, process-list argv, kill patterns, allowed values), the
`validate_command` gate and the bounded `run_argv` runner are in `data-tables/ops/board_agent.py`; an action
outside `IMPLEMENTED_ACTIONS` is refused with "not implemented in the agent".

### Agent database access

The agent talks to Postgres directly through the stdlib helper `pgdb`
(`data-tables/ops/pgdb.py`): `q(sql, params)` returns a list of dict rows
(pgdb.py:52–54), `x(sql, params)` runs one statement and returns the number
of rows changed (pgdb.py:57–61), `T(name)` quotes a table name (pgdb.py:15–17);
`_psql` shells out to `docker exec -i ahawr-postgres psql -U n8n -d n8n`
(pgdb.py:44–49) and fills each `%s` with a literal-escaped value via
`lit()` (pgdb.py:20–30, `_fill` pgdb.py:33–41). No driver is imported.
This is the path the design already chose: the host agent is "plain Python,
no external packages" (board-ops.md:21–22) and the host settings helper reads
settings "via `pgdb.q()` … (no external packages)" (board-ops.md:340–341);
`board-ops-notes.md:190–191` records the same method as "shared helper, no
external packages". The dashboard HTTP (urllib) is the fallback, not the
design: `PgOpsStore` imports `psycopg` (board_ops_store_pg.py:214), a
third-party package, so a stdlib-only agent cannot import
`board_ops_store_pg` at all.

| Action | Allowed args | Dangerous | Needs boundary | Evidence |
|---|---|---|---|---|
| `stop_mission` | none | yes | no | board_ops_actions.py:104–115 |
| `pause_at_boundary` | none | yes | yes (waits for the boundary itself, 8 h deadline) | board_ops_actions.py:116–129 |
| `resume` | none | no | no | board_ops_actions.py:130–139 |
| `restart_queue` | none | yes | no | board_ops_actions.py:140–149 |
| `restart_queue_watch` | none | yes | no | board_ops_actions.py:150–159 |
| `restart_docker_watchdog` | none | yes | no | board_ops_actions.py:160–169 |
| `restart_tg_relay` | none (runs only when the liveness count is 0 and the `tg_relay.enabled` marker exists; otherwise rejected) | yes | no | board_ops_actions.py:170–180 |
| `rebuild_service` | `service` ∈ `REBUILD_SERVICES` = {claude-runner, ahawr-dashboard, ahawr-retrieval, ahawr-litellm} | yes | yes (only at a task boundary, or with no mission running) | board_ops_actions.py:181–190 |
| `docker_update_memory` | `service` ∈ `MEMORY_SERVICES` = {claude-runner, ahawr-retrieval}; `gb` ∈ `MEMORY_GB` = {2, 3, 4, 5, 6, 7, 8} | yes | no | board_ops_actions.py:191–201 |
| `set_llama_preset` | `preset` ∈ `PRESETS` = {enable_thinking, ensure_prod_preset} | yes | yes | board_ops_actions.py:202–211 |
| `switch_model_pair` | `pair` = a key of the `verified_model_pairs` setting | yes | no | board_ops_actions.py:212–222 |
| `send_test_notification` | none | no | no | board_ops_actions.py:223–232 |

### What the agent runs

| Action | What the agent runs |
|---|---|
| `stop_mission` | `["curl","-s","-m","20","-X","POST","-H","X-AHAWR-Dashboard: 1","localhost:8701/api/runs/{run_id}/cancel"]` (run_id from `GET /api/runs`, first worker/running run per boundary check, board-ops.md:203–224); then `["powershell.exe","-NoProfile","-Command","Get-CimInstance Win32_Process \| Where-Object { $_.CommandLine -match '[r]eal_queue\.sh\|[w]ait_mission_end\|[l]aunch_mission' } \| ForEach-Object { taskkill /PID $_.ProcessId /T /F \| Out-Null }"]` (bracketed patterns prevent self-match, board-ops.md:262–283). |
| `pause_at_boundary` | **implemented: small and safe** — poll `["curl","-s","-m","20","-H","X-AHAWR-Dashboard: 1","localhost:8701/api/runs"]` until the worker task id in the running run's title changes (8 h deadline, board-ops.md:203–224); then the queue-chain kill above (board-ops.md:262–283). The n8n container stop is NOT done by the agent; it is shown to the operator as a docker command (`["docker","stop","-t","30","n8n-autonomous-agents"]`), docker being out of scope for this mission. |
| `resume` | Pre-check: `["powershell.exe","-NoProfile","-Command","$p='[q]ueue_watch'+'.py'; (Get-CimInstance Win32_Process \| Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match $p } \| Measure-Object).Count"]` — if count > 0, do not start; report in `result_json` ("queue_watch already running", board-ops.md:262–283). Start: `["powershell.exe","-NoProfile","-Command","Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py'; CurrentDirectory='D:\n8n\data-tables\ops'} \| Out-Null"]` (board-ops.md:262–283). Note: queue_watch.py has no self-start code; the CIM Create is the only launch path. |
| `restart_queue` | `["powershell.exe","-NoProfile","-Command","Get-CimInstance Win32_Process \| Where-Object { $_.CommandLine -match '[r]eal_queue\.sh\|[w]ait_mission_end\|[l]aunch_mission' } \| ForEach-Object { taskkill /PID $_.ProcessId /T /F \| Out-Null }"]` (queue-chain kill, board-ops.md:262–283); then `["powershell.exe","-NoProfile","-Command","Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\"C:\\Program Files\\Git\\usr\\bin\\bash.exe\" -c \"export PATH=/usr/bin:/mingw64/bin:$PATH; cd /d/n8n/data-tables/ops && NOWAIT=1 /usr/bin/bash real_queue.sh >> real_queue.out 2>&1\"'; CurrentDirectory='D:\n8n\data-tables\ops'} \| Out-Null"]` (queue_watch.py:182–187, board-ops.md:262–283). |
| `restart_queue_watch` | `["powershell.exe","-NoProfile","-Command","Get-CimInstance Win32_Process \| Where-Object { $_.CommandLine -match '[q]ueue_watch\.py' } \| ForEach-Object { taskkill /PID $_.ProcessId /T /F \| Out-Null }"]`; then `["powershell.exe","-NoProfile","-Command","Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py'; CurrentDirectory='D:\n8n\data-tables\ops'} \| Out-Null"]` (board-ops.md:262–283). |
| `restart_docker_watchdog` | **rejected: not implemented in the agent** — docker/host-script actions are out of scope for this mission; the command is shown to the operator, never run by the agent. The design intent is `["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File","D:\n8n\data-tables\ops\docker_watchdog.ps1"]` (board-ops.md:262–283). |
| `restart_tg_relay` | **rejected: not implemented in the agent** — it needs the `tg_relay.enabled` marker and the CIM liveness logic that are out of scope for this mission. Design intent: CIM liveness count 0 + `tg_relay.enabled` marker, then CIM Create of `D:\Python312\python.exe D:\n8n\data-tables\ops\tg_relay.py` (queue_watch.py:170–179, board-ops.md:262–283). |
| `rebuild_service` | **rejected: not implemented in the agent** — docker/host-script actions are out of scope for this mission; the command is shown to the operator, never run by the agent. Design intent: `["docker","compose","up","-d","--build","<service>"]` (run from `/d/n8n`), then the service's health probe must pass (board-ops.md:203–224). |
| `docker_update_memory` | **rejected: not implemented in the agent** — docker/host-script actions are out of scope for this mission; the command is shown to the operator, never run by the agent. Design intent: `["docker","update","--memory=<gb>g","--memory-swap=<gb>g","<service>"]` (board-ops.md:262–283). |
| `set_llama_preset` | **rejected: not implemented in the agent** — docker/host-script actions are out of scope for this mission; the command is shown to the operator, never run by the agent. Design intent: `["bash","D:\n8n\data-tables\ops\<preset>.sh"]`, then wait up to 450 s for the llama-server health probe (board-ops.md:262–283). |
| `switch_model_pair` | **implemented: small and safe** — parameterized update of `hermes_config` (`worker_model`, `worker_provider`, `reviewer_model`, `reviewer_provider`) with optimistic locking via `pgdb.x()` (board-ops.md:262–283, pgdb.py:52–61); effective from the next run; no subprocess. |
| `send_test_notification` | **implemented: small and safe** — calls the existing `notify.send()` host notification path (Telegram or log fallback, queue_watch.py:42–52); no subprocess. |

**Process-list check for a live `real_queue.sh` (queue_alive):** `queue_watch.py:122–126` — `["powershell","-NoProfile","-Command","(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'bash.exe' -and $_.CommandLine -match 'real_queue.sh' } | Measure-Object).Count"]`; alive when count > 0.

**Process-list check for an existing `queue_watch.py`:** `["powershell.exe","-NoProfile","-Command","$p='[q]ueue_watch'+'.py'; (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match $p } | Measure-Object).Count"]` (pattern split so the check does not match itself, board-ops.md:262–283); if count > 0, do not start — report in `result_json`.

**Task boundary detection rule:** `board_ops_state.py:160–178` (`at_task_boundary`) — True when no mission is running, or when the newest queue-log line matching `ended <mission>:` or `started <mission>` is an `ended` line (current task finished, next not started). While the queue is alive, a newest `started` line (or no matching line) means mid-run (conservatively False). The agent polls `GET localhost:8701/api/runs` (header `X-AHAWR-Dashboard: 1`), among the first 8 runs takes the `role == 'worker'`, `status == 'running'` one; no such run = no running mission. `pause_at_boundary` waits until the task id in that run's title changes, 8 h deadline (board-ops.md:203–224).

`requires_boundary` is enforced by `board_ops_state.check_boundary`
(board_ops_api.py:384, :440), which reads the published snapshot plus the
`queue_log` tail (`_boundary_state`, board_ops_api.py:641–646) and refuses a
boundary action while a task is mid-run (`at_task_boundary`,
board_ops_state.py:160–179).

## Heartbeat and publishing

| Quantity | Constant name | Value | Unit | Evidence |
|---|---|---|---|---|
| Agent poll interval | `POLL_SECONDS` | 10 | seconds | One agent cycle per 10 s; drives heartbeat, snapshot, and `queue_log` tail publication together (board_ops_store_pg.py:477–515, board_ops_store.py:168–177). |
| Agent heartbeat interval | `HEARTBEAT_SECONDS` | 10 | seconds | One `board.agent` row per poll; `STALE_AGENT_S = 30.0` (board_ops_state.py:54) means a heartbeat older than 30 s reports `stale`, so a 10 s heartbeat stays well inside the window. |
| `board.agent` fields | — | `id`, `heartbeat_at`, `version`, `host_info_json` | — | board-ops-schema.sql:69–72 |
| `queue_log` tail publication interval | `QUEUE_LOG_TAIL_PUBLISH_SECONDS` | 10 | seconds | Agent reads `real_queue.log` new lines every poll (10 s) via incremental offset tracking like `queue_watch.py:new_lines` (queue_watch.py:80–97); publishes each batch to `board.queue_log` (at, line). |
| `queue_log` tail read size | `QUEUE_LOG_TAIL_LINES` | 200 | lines | Each poll publishes at most the last 200 new lines (per-batch, new lines since last offset, queue_watch.py:80–97); each line is bounded to `MAX_CHARS_PER_LINE` = 500 chars (truncated on publication to keep the tail readable). |
| `host_snapshot` publication interval | `SNAPSHOT_SECONDS` | 10 | seconds | One `board.host_snapshot` row per agent poll (board_ops_store.py:168–177, read via `get_snapshot("host_snapshot", limit=1)` in dashboard.py:611); `process_panel` aggregates the snapshot (board_ops_state.py:124–157). |
| `host_snapshot` `data_json` fields | — | Docker VM (`avail_mb`, `swap_used_mb`, PSI values), watchdog (last action, restart count, last incident), queue (`alive`, `mission`, `task`, `last_event_age_s`), `queue_watch` (last sent notices, notices/hour), `tg_relay` (alive, last update id, last log line), n8n (running), health booleans (4 services), `hermes_config` (model+provider), memory limits, hardware (display only) | — | board-ops.md:133–154 |
| `queue_launcher` request max age | `MAX_AGE_S` | 600 | seconds | queue_launcher.py:32 |
| `queue_launcher` max starts per hour | `MAX_PER_HOUR` | 4 | starts/hour | queue_launcher.py:33 |
| `guard_restart` max requests per hour | `RESTART_LIMIT_PER_HOUR` | 4 | requests/hour | guard_restart.py:24 |

The agent publishes one `board.agent` heartbeat row, one `board.host_snapshot` row, and a `queue_log` tail batch per poll cycle (`POLL_SECONDS` = 10 s; `HEARTBEAT_SECONDS` = 10 s, `SNAPSHOT_SECONDS` = 10 s, `QUEUE_LOG_TAIL_PUBLISH_SECONDS` = 10 s). The `queue_log` tail publishes at most `QUEUE_LOG_TAIL_LINES` = 200 lines per batch, each truncated to `MAX_CHARS_PER_LINE` = 500 chars. The guard reads the published tail with `QUEUE_LOG_LIMIT` (board_ops_settings.py). All constants above are named in the design note; the agent's poll loop drives all three publications from one timer.

## Missing routes

None missing: because the agent reaches the database directly over `pgdb`,
it needs no dashboard routes of its own, and the Control tab is already
served by existing routes. No agent route added (checked against this list
on 2026-10-24): claim-with-version-check, `result_json` write, `board.agent`
heartbeat, and `board.queue_log` / `board.host_snapshot` publication are all
covered by the agent's direct `pgdb` writes as described below.

**Agent — claim:** no route. The agent replicates the `PgOpsStore.claim_next`
transaction (board_ops_store_pg.py:295–323) as SQL: in one transaction
`SELECT id … WHERE status='new' ORDER BY requested_at LIMIT 1 FOR UPDATE
SKIP LOCKED`, a count of the `running` rows (roll back when > 0 — the
single-running-command invariant), then
`UPDATE commands SET status='running', started_at=now() WHERE id=… AND
status='new'`. Since `pgdb._psql` runs one script per call (pgdb.py:44–49),
the agent must issue this as one script wrapped in `BEGIN … COMMIT` to keep
the sequence atomic.

**Agent — result write:** no route. Replicates
`PgOpsStore.finish_command` (board_ops_store_pg.py:325–357): `SELECT … WHERE
id=… FOR UPDATE`, accept only a `running` row, then
`UPDATE commands SET status=…(done|failed), finished_at=now(), result_json=…`.
A refused command uses the `PgOpsStore.reject_command` transaction
(board_ops_store_pg.py:359–388): accepts `new`/`running`, then
`UPDATE … SET status='rejected', finished_at=now(), result_json={"reason": …}`.

**Agent — heartbeat:** no route. Replicates
`PgOpsStore.put_heartbeat` (board_ops_store_pg.py:477–515):
`INSERT INTO agent (heartbeat_at, version, host_info_json) VALUES (now(), …)`
plus the changelog audit row (`PgOpsStore._audit`, board_ops_store_pg.py:226–248),
in one transaction.

**Agent — queue_log and host_snapshot:** no route, and no store method either.
`OpsStore` has only the read methods `get_queue_log` / `get_snapshot`
(board_ops_store.py:168–177) — it has no `put_queue_log` or `put_snapshot` —
so the agent publishes both tables with its own INSERTs:

- `INSERT INTO board.queue_log (at, line)` — columns `id` (bigserial PK),
  `at`, `line` (board-ops-schema.sql:88–92), with incremental offset
  tracking for the `real_queue.log` tail like `queue_watch.py:new_lines`
  (board-ops.md:348–349).
- `INSERT INTO board.host_snapshot (at, data_json)` — columns `id`
  (bigserial PK), `at`, `data_json` (board-ops-schema.sql:97–100).

The agent role is granted exactly these writes — `SELECT, UPDATE` on
`board.commands` and `SELECT, INSERT` on `board.agent`, `board.queue_log`,
`board.host_snapshot` (board-ops-schema.sql:120–140) — and no dashboard
route writes any of them on the agent's behalf, so direct SQL is the only
path consistent with the design.

**Control tab — status:** `GET /api/board/ops/status`
(board_ops_api.py:189–222), carrying the agent's `alive`/`stale` state and
`actions_enabled`.

**Control tab — history:** `GET /api/board/ops/commands`
(board_ops_api.py:461–481), the filterable command journal
(`action`, `status`, `requested_by`, `since`, `limit`).

The published tail and snapshot data the agent's INSERTs produce are already
read by existing routes: `GET /watchers` (`queue_log_tail`,
board_ops_api.py:256) and the panels built from `_latest_snapshot`
(board_ops_api.py:114–121) — no further read routes are needed.

## Agent loop

`data-tables/ops/board_agent.py` does not exist yet (it is absent from
`data-tables/ops/`; no `board_agent*.py` in the tree). The loop it must run,
mirroring the guards tab pattern:

- **Tick bound.** One poll every `POLL_SECONDS` = 10 s (named constant with its
  unit; the design note's heartbeat table, board-ops-agent.md "Heartbeat and
  publishing"). Each tick does at most one command: claim the oldest `new` row
  through the store's claim protocol, execute it, write the result. Nothing is
  done between ticks; there is no second command while one is running (the
  single-running-command invariant, board_ops_store_pg.py:181–212).
- **One command per claim.** The claim is the atomic `board.commands` protocol
  (`OpsStore.claim_next`, board_ops_store.py:101–104; `PgOpsStore.claim_next`,
  board_ops_store_pg.py:295–323): lock the oldest `new` row (`FOR UPDATE SKIP
  LOCKED`), roll back when a `running` row already exists, mark it `running`
  with `started_at = now()`. The agent runs exactly one command per successful
  claim; the next claim waits for the next tick.
- **Timeouts.** Every subprocess call has a named timeout; a `running` command
  older than `stale_command_timeout_seconds` (default 600 s,
  board-ops.md:110) is marked `failed` with reason "timeout" by
  `mark_stale` (board_ops_store.py:116–119; board_ops_store_pg.py:390–421),
  run by the dashboard on every read (`_expire_stale`, board_ops_api.py:609–619)
  and by the agent's own reap while alive. `pause_at_boundary` waits at most
  8 h (28800 s, board-ops-notes.md:348) for the task boundary.
- **Bounded output.** Command output is cut to `max_output_chars` (named
  constant with its unit); anything that looks like a credential is scrubbed
  by `sanitize_result` (board_ops_redact.py:79–98: cap to `max_chars` and
  `max_lines`). The agent never reads or prints `.env` or environment values.
- **DRY mode.** With the env flag `BOARD_AGENT_DRY=1` the agent prints every
  command it would run and executes nothing: no subprocess, no claim result
  other than `done` with the printed argv, no writes to the database beyond the
  heartbeat. It is the only mode in which the agent may be started by a test
  (the design: `tests/test_board_agent.py` imports the file by path as
  `tests/test_qa_worker.py` does — `OPS_DIR / "board_agent.py"` loaded through
  `importlib.util.spec_from_file_location`, test_qa_worker.py:25, :30–35 — with
  fakes for the database, the process list and the runner).
- **Per tick, besides the command:** write one `board.agent` heartbeat row
  (`put_heartbeat`, board_ops_store.py:143–147; board_ops_store_pg.py:477–515),
  publish the new tail of `real_queue.log` into `board.queue_log`
  (incremental offset tracking like `queue_watch.py:new_lines`,
  queue_watch.py:80–97; at most `QUEUE_LOG_TAIL_LINES` = 200 new lines, each
  truncated to `MAX_CHARS_PER_LINE` = 500), and publish one `board.host_snapshot`
  row (the parsed watcher/environment data, board-ops.md:133–154). A heartbeat
  older than `STALE_AGENT_S` = 30 s (board_ops_state.py:54, :84–89) reports
  `stale` and disables every action on the dashboard (503,
  `_stale_agent_error`, board_ops_api.py:622–638; `actions_enabled` in
  `GET /status`, board_ops_api.py:212–222).

## Control tab

The Control tab is a fourth tab of the board, built on the same pattern as the
Guards tab: a pure view module plus a DOM module plus a stylesheet, mounted
conditionally behind a flag, with a bounded poll that is cleared on page leave.

- **Files (new, mirroring the guards tab):** `board_control_view.js` (the pure
  core: turns `GET /api/board/ops/status` and `GET /api/board/ops/commands`
  answers into one model, no DOM; the node test
  `tests/board_control_view.test.js` reads the shared fixture
  `tests/control_view_cases.json`, the same arrangement as
  `board_guards_view.js` + `tests/board_guards_view.test.js` +
  `tests/guards_view_cases.json`), `board_control.js` (the DOM glue:
  `mount(el, helpers)` taking `t(key, English)`, `esc(text)`, `setHTML` —
  the signature `BoardGuards.mount(pane, { t: guardT, esc, setHTML })` uses,
  dashboard.html:1238–1244), and `board_control.css` (colours from the
  dashboard variables, like board_guards.css:1–4). All three are served by the
  existing `_board_static` pattern (dashboard.py:174–193) at
  `/static/board_control_view.js`, `/static/board_control.js`,
  `/static/board_control.css`.
- **Wiring behind the flag.** As in `enableGuardsTab` (dashboard.html:1255–1277):
  the button, the pane and the stylesheet are created only when
  `/api/board/ops/status` says the tab flag is on; with the flag off the page is
  byte-for-byte unchanged. The status strip above the tab bar
  (`#guardstrip`, dashboard.html:228–240, 1164–1198) shows agent-alive by
  heartbeat age, queue running or not, the current mission and task.
- **Status strip.** One row per state, built like `guardEntryHtml`
  (dashboard.html:1149–1153): the agent's status (`alive`/`stale` from
  `GET /api/board/ops/status`'s `agent.status`, board_ops_api.py:189–222), the
  queue (`process_panel.queue.alive`), the current mission and task (from the
  same panel, `board_ops_state.process_panel`, board_ops_state.py:124–157).
- **Buttons.** `resume`, `pause_at_boundary`, `stop_mission`, `restart_queue` —
  each opens the existing two-step prepare/confirm flow
  (`POST /api/board/ops/commands/prepare` → `POST /api/board/ops/commands/confirm`,
  board_ops_api.py:388–410, :453–457), showing the action's `consequences`
  text before the token is sent (board_ops_actions.py:103–233). Every write
  carries the header `X-AHAWR-Dashboard: 1` (enforced for all POST/PUT/DELETE
  under `/api/board`, dashboard.py:143–155).
- **History.** The command journal: `GET /api/board/ops/commands`
  (board_ops_api.py:461–481), the last commands with their `status`
  (`new`, `running`, `done`, `failed`, `rejected`).
- **Agent-not-running message.** A clear banner when the agent is stale
  (heartbeat > `STALE_AGENT_S` = 30 s): what to start and how — the detached
  start command of the "Operator start" section below. It disables the action
  buttons while the agent is stale, like the "host agent not responding" banner
  of the ops page design (board-ops.md:367–368) and the 503 refusals of
  prepare/confirm while the agent is stale (board_ops_api.py:622–638).
- **Bounded poll, cleared on leave.** The tab polls `GET /api/board/ops/status`
  on a bounded timer (`REFRESH_MS` = 10000 in board_guards.js:8; one poll per
  10 s) and clears it when the page goes away — `BoardGuards.unmount()`
  clears the interval and removes the click listener (board_guards.js:149–154),
  called from `showGuardsTab` (dashboard.html:1232–1245) when the tab is
  hidden. The Control tab must do the same in its `unmount()`.
- **Response fields (read routes, no new route needed).** `GET /api/board/ops/status`
  (board_ops_api.py:189–222) returns `agent` (`status` = `alive`/`stale`,
  `heartbeat_at`, `actions_enabled`), `actions_enabled`, and `process_panel`,
  whose `queue` object carries `alive`, `mission`, `task`
  (board_ops_state.py:145–147). `process_panel.tg_relay` exposes `token` and
  `chat_id` by presence only. `GET /api/board/ops/commands`
  (board_ops_api.py:461–481) returns `commands` (rows with `id`, `action`,
  `args_json`, `status`, `requested_by`, `requested_at`, `started_at`,
  `finished_at`, `result_json`) and `count`. `confirm_token` is never
  returned by the read routes.

## Flag

- **`DASHBOARD_OPS_TAB`, OFF by default.** The tab exists only when the flag is
  on: with it off the dashboard is byte-for-byte unchanged (a test proves the
  tab and its script are absent). It follows the same rule as
  `DASHBOARD_GUARDS_TAB` (guards.py:177: `tab=master and _bool(env,
  "DASHBOARD_GUARDS_TAB", False)`), which is OFF by default in compose
  (docker-compose.yml:292, `${DASHBOARD_GUARDS_TAB:-0}`).
- **Proposed compose env line (text only, not written to docker-compose.yml):**

  ```
      - DASHBOARD_OPS_TAB=${DASHBOARD_OPS_TAB:-0}
  ```

  placed in the `ahawr-dashboard` service's `environment` block next to the
  existing `DASHBOARD_GUARDS_TAB` line (docker-compose.yml:292). The file itself
  is unchanged: this line appears in the note only as a proposal for the
  operator.
- The flag name also goes to `.env.example` without a value (per the mission
  design), so the operator can set `DASHBOARD_OPS_TAB=1` in their `.env`.

## Operator start

The agent is started detached by the operator, the same way `queue_watch.py` is
started (CIM `Win32_Process.Create`, board-ops.md:21–28, board-ops-notes.md:242–255):

```
powershell.exe -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='D:\Python312\python.exe D:\n8n\data-tables\ops\board_agent.py'; CurrentDirectory='D:\n8n\data-tables\ops'} | Out-Null"
```

**NOT RUN.** The agent is not started by this task; the operator runs it. In DRY
mode (`BOARD_AGENT_DRY=1`) it prints every command and runs nothing, which is
how the operator can verify the agent without touching the queue. To switch the
tab on, the operator sets `DASHBOARD_OPS_TAB=1` in `.env` and restarts the
dashboard container — also **NOT RUN** in this task.

## Failure cases

- **Agent dead.** The heartbeat row in `board.agent` is older than
  `STALE_AGENT_S` = 30 s; `agent_status` reports `stale` (board_ops_state.py:84–89).
  Every prepare/confirm/repeat is refused with 503 (`_stale_agent_error`,
  board_ops_api.py:622–638) and `actions_enabled` is false in `GET /status`
  (board_ops_api.py:212–222). The Control tab shows the agent-not-running banner
  and disables its buttons. Recovery: the operator restarts the agent with the
  detached start command above; its first heartbeat (within one tick,
  `POLL_SECONDS` = 10 s) clears the stale state.
- **Command stuck running.** A `running` command older than
  `stale_command_timeout_seconds` (default 600 s, board-ops.md:110) is marked
  `failed` with `result_json = {"reason": "timeout"}` by `mark_stale`
  (board_ops_store.py:116–119; board_ops_store_pg.py:390–421). The dashboard
  runs it on every read (`_expire_stale`, board_ops_api.py:609–619), so a stuck
  command self-heals even while the agent is alive; the agent's own reap makes
  its rows self-healing between ticks. The single-running-command invariant then
  frees the next claim on the following tick.
- **Double start.** Starting a second agent while one runs would double the
  heartbeats and the publications. The claim protocol already prevents double
  *execution* (a new claim is refused while any row is `running`,
  board_ops_store_pg.py:295–323), but a second agent process would still write
  heartbeats and queue-log tails. The operator must not start a second agent:
  the start command is run once, and the agent-not-running banner (stale
  heartbeat) is the only signal to start again. There is no guard in the agent
  itself against a duplicate start; the single process is assumed.

## Rollback

1. **Stop the agent.** Kill the `board_agent.py` process:
   `powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match '[b]oard_agent\.py' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"`
   (the bracketed pattern prevents self-match, the convention of
   board-ops.md:298). Its last heartbeat stays; after 30 s the dashboard reports
   `stale` and all actions are disabled.
2. **Flag off.** Set `DASHBOARD_OPS_TAB=0` (or unset it) and restart the
   dashboard. With the flag off the page, the tab, its script and its
   stylesheet are absent — byte-for-byte unchanged, the same guarantee the
   Guards tab has behind `DASHBOARD_GUARDS_TAB` (dashboard.html:1228–1245,
   guards.py:177). The command rows already in `board.commands` remain in the
   database but are no longer visible in the tab; no row is deleted.

## Not done

- **`data-tables/ops/board_agent.py` is not written** — it is the missing executor;
  this note describes its loop, not its code.
- **The Control tab is not built** — `board_control_view.js`, `board_control.js`,
  `board_control.css`, the flag wiring, the i18n keys and the tests are absent.
- **The ops tables are not applied anywhere in the live or QA code.**
  `board_ops_store_pg.py` has no DDL / CREATE statements; `dashboard.py` has
  none; the only schema-application code is `qa_stack.py`, and it applies
  `board-schema.sql` only, not `board-ops-schema.sql` (qa_stack.py:122).
  Whether the live `board` schema contains them is unknown from the repository
  (the operator's read-only query, NOT RUN, is in the "Board tables" section).
- **The agent is not started, and the flag is not set** — both are operator steps
  marked NOT RUN above; no write route of the real dashboard was called.

## Board tables

Each of the four tables below is marked **applied**, **missing** (in the live
code) or **unknown** (live-DB state, not verified — a connection was not
opened, so the read-only query is recorded as NOT RUN for the operator).

### board.commands

| Column | Agent / guards need | Evidence |
|---|---|---|
| `id` (bigint PK) | claim target, `repeat` | board-ops-schema.sql:38 |
| `action` (text) | whitelist dispatch | board-ops-schema.sql:39 |
| `args_json` (jsonb) | validated args | board-ops-schema.sql:40 |
| `status` (new/running/done/failed/rejected) | claim protocol, single-running invariant | board-ops-schema.sql:41–42; board_ops_store_pg.py:181–212 |
| `requested_by` (text) | audit | board-ops-schema.sql:43 |
| `requested_at` (timestamptz) | ordering for claim | board-ops-schema.sql:44 |
| `started_at` (timestamptz) | claim stamp | board-ops-schema.sql:45 |
| `finished_at` (timestamptz) | terminal state | board-ops-schema.sql:46 |
| `result_json` (jsonb) | scrubbed result/reason | board-ops-schema.sql:47 |
| `confirm_token` (text) | dangerous-action confirm | board-ops-schema.sql:48 |

**Applied:** no (no DDL in `board_ops_store_pg.py` or `board_pg.py`); the QA
stack applies `board-schema.sql` only (qa_stack.py:122). **Live-DB:** unknown —
NOT RUN, see below.

### board.agent

| Column | Agent / guards need | Evidence |
|---|---|---|
| `id` (bigint PK) | — | board-ops-schema.sql:69 |
| `heartbeat_at` (timestamptz) | staleness check (> 30 s disables actions) | board-ops-schema.sql:70; board-ops.md:87–88 |
| `version` (text) | agent version string | board-ops-schema.sql:71 |
| `host_info_json` (jsonb) | host facts | board-ops-schema.sql:72 |

**Applied:** no (no DDL in runner code). **Live-DB:** unknown — NOT RUN.

### board.queue_log

| Column | Agent / guards need | Evidence |
|---|---|---|
| `id` (bigint PK) | cursor for incremental read | board-ops-schema.sql:89 |
| `at` (timestamptz) | line timestamp; ordering | board-ops-schema.sql:90 |
| `line` (text) | verbatim log line parsed by `MissionNoticeGuard` | board-ops-schema.sql:91 |

Fields `mission_notice` reads: `at` and `line`. `get_queue_log` returns
`{"at", "line"}` (board_ops_store_pg.py:601–609); the guard iterates rows and
parses `row["line"]` via `_notices_for` / `_queue_notice` (guard_queue.py:179–186,
:215–243; `_line_key` uses `at` + `line`, guard_queue.py:127–129).

**Applied:** no (no DDL in runner code). **Live-DB:** unknown — NOT RUN.

### board.host_snapshot

| Column | Agent / guards need | Evidence |
|---|---|---|
| `id` (bigint PK) | — | board-ops-schema.sql:98 |
| `at` (timestamptz) | snapshot time | board-ops-schema.sql:99 |
| `data_json` (jsonb) | published watcher/environment data | board-ops-schema.sql:100 |

Fields `idle` reads from `data_json` (the newest row, `limit=1`):
- `data_json["queue"]["alive"]` (bool) — the `alive` flag; fallback
  `data_json["alive"]`. Evidence: `_queue_alive`, dashboard.py:618–629.
- `data_json["running"]` (int) — the running run count (executions + runner
  runs). Evidence: `_queue_running`, dashboard.py:632–639;
  `IdleGuard.check`, guard_queue.py:272–286.

`mission_notice` does not read `board.host_snapshot`; it reads
`board.queue_log` (`at`, `line`) plus the `review_parsed` event
(`task_index`, `task_attempt`, `review_score`, `review_reason`). Evidence:
`MissionNoticeGuard.check` / `_notices_for` / `_last_review`,
guard_queue.py:173–240; duty table in docs/guards.md:71–74.

**Applied:** no (no DDL in runner code). **Live-DB:** unknown — NOT RUN.

### Operator read-only query (NOT RUN)

No database connection was opened. To verify the live state, the operator runs
(from a client that can reach the `board` schema):

```sql
SELECT to_regclass('board.commands')      AS commands,
       to_regclass('board.agent')         AS agent,
       to_regclass('board.queue_log')     AS queue_log,
       to_regclass('board.host_snapshot') AS host_snapshot;
```

Each non-NULL result means the table exists in the live schema. This query is
NOT RUN in this task.

### Fill missing tables (NOT RUN)

If the query above reports any of the four tables as missing, the operator
applies `board-ops-agent-schema.sql`: it copies the definitions of
`board.commands`, `board.agent`, `board.queue_log` and `board.host_snapshot`
from `board-ops-schema.sql` using `CREATE TABLE IF NOT EXISTS` /
`CREATE INDEX IF NOT EXISTS` only — no DROP, no ALTER of existing tables,
nothing on n8n tables. This file is an operator step and is NOT RUN in this
task.
