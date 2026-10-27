# Host actions: design (part 1)

## Problem

The Worker runs in `claude-runner` and has no Docker access. Some tasks need a container recreated or restarted
after a change: a new environment value, a new mount, a rebuilt image. Giving the container the Docker socket would
make it root on the host, because any process that can talk to the socket can start a privileged container with the
host filesystem mounted. So today such a task fails three times in the Worker and the operator does the recreate by
hand.

## Services and tiers

Tiers: **AUTO** = the runner may recreate or restart the service after a whitelisted request.
**CONFIRM** = the action is allowed only after the operator confirms it. **NOT WHITELISTED** = the runner never
acts on the service.

| Service (compose)  | Container name        | Tier            | Reason                                                                                  |
|--------------------|-----------------------|-----------------|-----------------------------------------------------------------------------------------|
| ahawr-postgres     | ahawr-postgres        | NOT WHITELISTED | Stateful: holds the n8n and board database; a restart or recreate can lose or corrupt data. |
| n8n                | n8n-autonomous-agents | CONFIRM         | Runs the live workflows and the queue; a restart interrupts running missions.           |
| ahawr-retrieval    | ahawr-retrieval       | NOT WHITELISTED | Stateful: the index store lives in the `ahawr-retrieval-data` volume; the mission does not name it. |
| claude-runner      | claude-runner         | CONFIRM         | Runs the Worker, Reviewer and Architect; a restart interrupts the active run.           |
| ahawr-qa           | ahawr-qa              | AUTO            | Side container that runs QA scenarios; holds no state of its own (`qa-runs` volume only). |
| ahawr-dashboard    | ahawr-dashboard       | AUTO            | Read-only dashboard; a restart has no effect on runs.                                   |
| litellm            | ahawr-litellm         | CONFIRM         | Model gateway (LiteLLM) to the llama server; a restart interrupts local model calls.    |

The llama server runs on the Docker host, outside compose, so it has no row here; any llama server that is named
in a mission is CONFIRM.

## Whitelist file

The whitelist is loaded from a file whose path is a setting. The default below is built into `host_actions.py` and is
used when the file is missing. The whitelist is never taken from a request: a request names a service and an action,
and the runner looks both up in the loaded whitelist.

```json
{
  "actions": {
    "recreate": "docker compose up -d --no-deps <service>",
    "restart": "docker restart <container>"
  },
  "services": {
    "ahawr-postgres": {"container": "ahawr-postgres", "tier": "NOT WHITELISTED"},
    "n8n": {"container": "n8n-autonomous-agents", "tier": "CONFIRM"},
    "ahawr-retrieval": {"container": "ahawr-retrieval", "tier": "NOT WHITELISTED"},
    "claude-runner": {"container": "claude-runner", "tier": "CONFIRM"},
    "ahawr-qa": {"container": "ahawr-qa", "tier": "AUTO"},
    "ahawr-dashboard": {"container": "ahawr-dashboard", "tier": "AUTO"},
    "litellm": {"container": "ahawr-litellm", "tier": "CONFIRM"}
  }
}
```

## Lifecycle and APIs

Repository facts this section relies on (line numbers as read for this design):

| Fact | Source |
|---|---|
| QA queue is one JSON file, `data_dir/qa_requests.json`, opened by the runner process only | `claude-runner/src/claude_runner/api.py:131`; `claude-runner/src/claude_runner/qa_requests.py:100-105`, `:201-205` |
| `data_dir` comes from `CLAUDE_RUNNER_DATA_DIR`, default `/data` | `claude-runner/src/claude_runner/config.py:497` |
| `/data` is the `claude-runner-data` volume, mounted only in `claude-runner` | `docker-compose.yml:184`; `ahawr-dashboard` mounts (`docker-compose.yml:303-318`) hold no claude-runner data |
| Runner published on loopback only, for the host executor | `docker-compose.yml:130-131` (`127.0.0.1:8700:8700`) |
| Dashboard reaches the runner on the stack network with its own key | `docker-compose.yml:277-279` (`DASHBOARD_RUNNER_URL`, `DASHBOARD_RUNNER_API_KEY`) |
| Bearer check on the routes, key from `CLAUDE_RUNNER_API_KEY` | `claude-runner/src/claude_runner/api.py:183-188` |
| Dashboard refuses a stop/write without `X-AHAWR-Dashboard` (403) | `claude-runner/src/claude_runner/dashboard.py:152` |
| Host QA client: bearer, runner URL, timeouts, poll loop, claim | `claude-runner/src/claude_runner/qa_worker.py:39-43` (URL, bearer env, HTTP timeout, `POLL_SECONDS = 60`), `:44-46` (backoff cap, error limit, cycle bound), `:48` (`RUN_TIMEOUT_SECONDS`), `:59-60` (heartbeat path), `:94` (`_call`), `:109` (`list_queued`), `:114` (`claim`), `:173` (`_write_heartbeat`), `:508` (`main`) |
| `data-tables/ops/qa_worker.py` is retired: it prints and exits 2 | `data-tables/ops/qa_worker.py:9-14`; not a base for the executor |

### (a) Store location

Decision: the host-action store is one JSON file next to the QA store, `settings.data_dir / "host_actions.json"`
(the same pattern as `api.py:131`; `/data/host_actions.json` in `claude-runner`). Only the runner reads and writes it.

Reach from the dashboard: **it cannot read or write that path.** The `ahawr-dashboard` service mounts only the
heartbeat directory, the guard request directory and the guards state volume (`docker-compose.yml:303-318`). The
dashboard therefore reaches the store through a runner route on the stack network, `http://claude-runner:8700`, with
the key it already has (`docker-compose.yml:277-279`). No compose change is needed for this option.

Rejected option, a shared volume: the dashboard could read the file, but two processes writing one JSON file break the
single in-process lock of `QaRequestStore` (`qa_requests.py:104`), and writes would still have to go through the
runner. Proposed diff, text only, NOT applied, for the record:

```diff
   ahawr-dashboard:
     volumes:
+      # host action store, read-only: writes still go through the runner
+      - type: volume
+        source: claude-runner-data
+        target: /data
+        read_only: true
```

### (b) No Worker, Reviewer or QA run is active

Checked inside the runner at claim time, with existing code:

- Worker, Reviewer and Architect runs: `Store.list_runs(status=...)` (`claude-runner/src/claude_runner/store.py:361`)
  called with `status="running"` and `status="queued"`. `ACTIVE = ("queued", "running")` (`store.py:18`) is the
  existing set of active run statuses.
- QA requests: `QaRequestStore.list_requests(status)` (`claude-runner/src/claude_runner/qa_requests.py:145`) with
  `"queued"` and `"running"`. QA runs are not rows in the runs table.
- Not sufficient alone: `Store.active_run(session_id)` (`store.py:186`) checks one session; `RunManager.list_processes()`
  (`claude-runner/src/claude_runner/runs.py:764`, route `GET /v1/processes` at `api.py:280`) lists only this manager's
  CLI processes and misses queued runs.
- The runner has no route that lists runs by status (`api.py` has only `POST /v1/runs` and `GET /v1/runs/{run_id}`,
  `api.py:244` and `:272`), so the check is a runner-side function called from the claim route, not a client call.

### (c) Queue at a task boundary, no running worker or reviewer in Live State

- `LiveState` model (`claude-runner/src/claude_runner/board.py:89-109`): `status`, `worker_status`, `reviewer_status`,
  `worker_run_id`, `reviewer_run_id`.
- Reads: `BoardStore.list_live_states()` (`board.py:196`; `board.py:677` in-memory, `board_pg.py:450` Postgres) and
  `BoardStore.get_live_state(state_key)` (`board.py:200`; `board.py:681`, `board_pg.py:461`).
- Derived check: `BoardStore.running_missions()` (`board.py:249`) returns missions whose Live State is `active` and not
  stale; `is_stale_active` (`board.py:112`) treats an `active` row older than `RUNNING_STALE_HOURS` (6) as not running.
  `BoardStore.mission_groups()["running"]` (`board.py:259-292`) is the same set grouped.
- HTTP read: `GET /api/board/live-states` (`claude-runner/src/claude_runner/board_api.py:714-717`).
- Queue: `BoardStore.queue()` (`board.py:221`; `board.py:702` in-memory, `board_pg.py:529` Postgres).
- Gap: no paused flag exists in the code (no `paused` in `board*.py`). A pause is an operator act (`data-tables/ops/_pause_*.sh`).
  No script in `data-tables/ops` reads Live State. So "queue paused" cannot be read from code; the executor relies on
  `running_missions()` being empty.

### Lifecycle

```
Worker (claude-runner, inside its run)
  POST /v1/host-actions {action, service, reason}  -> pending        (8700, bearer)
  the Worker ends its run; the request waits for the operator

Runner, at creation
  whitelist tier AUTO (for example ahawr-qa, ahawr-dashboard): pending -> confirmed automatically
  whitelist tier CONFIRM (for example n8n, claude-runner, litellm): the request stays pending for the operator

Operator (dashboard, 127.0.0.1:8701)
  GET  /api/host-actions                           -> list           (no header)
  POST /api/host-actions/<id>/confirm              -> confirmed      (header, 8701)
  POST /api/host-actions/<id>/reject               -> rejected       (header, 8701)
    the dashboard handler records the decision in the runner through a dashboard-only key:
    POST /v1/host-actions/<id>/operator with Authorization: Bearer <CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY>
    (no route on 8700 that uses CLAUDE_RUNNER_API_KEY sets confirmed or rejected)

Executor (host, 127.0.0.1:8700, bearer)
  POST /v1/host-actions/<id>/claim                 -> running        (refused 409 while a run is active or Live State is active)
  applies the whitelisted command: docker compose up -d --no-deps <service>, or docker restart <container>
  POST /v1/host-actions/<id>/result {ok, message}  -> done | failed

Worker, next run
  GET /v1/host-actions/<id>                        -> reads status
```

Refused claims (409) are retried by the executor with the backoff of `qa_worker.py:44`, bounded by the confirmed
expiry. The result POST can fail while `claude-runner` is being recreated: the executor retries it; if the run bound
passes first, the runner marks the request `failed`.

### State table

| State | Meaning | Entered by | Allowed next state | Trigger of the next state |
|---|---|---|---|---|
| `pending` | created, waiting for confirmation | Worker: `POST /v1/host-actions` | `confirmed`, `rejected`, `expired` | runner auto-confirm for tier AUTO; operator confirm or reject on 8701 for tier CONFIRM; expiry after `max_pending_hours` |
| `confirmed` | approved; not claimed yet | runner, at creation, for tier AUTO; operator via dashboard confirm (dashboard-only key) for tier CONFIRM | `running`, `expired`, `rejected` | executor claim when (b) and (c) pass; expiry after `HOST_ACTION_CLAIM_MINUTES`; operator reject on 8701 (the operator may still refuse after confirming) |
| `running` | executor is applying the action | executor: `POST /v1/host-actions/<id>/claim` | `done`, `failed` | executor result; expiry after `HOST_ACTION_RUN_MINUTES` gives `failed` |
| `done` | action applied, executor reports ok | executor: `POST /v1/host-actions/<id>/result` with `ok: true` | none (terminal) | - |
| `failed` | executor reported an error, or the run bound passed | executor result with `ok: false`; runner expiry of `running` | none (terminal) | - |
| `rejected` | operator refused | operator via dashboard reject (dashboard-only key) | none (terminal) | - |
| `expired` | not confirmed or not claimed in time | runner, checked on read like `QaRequestStore._expire` (`qa_requests.py:176`) | none (terminal) | - |

Illegal transitions are refused with 409, the same pattern as `QA_TRANSITIONS` (`qa_requests.py:32-38`).

Proposed bounds, not yet set in code: `max_pending_hours = 1` (whitelist file, see Conditions),
`HOST_ACTION_CLAIM_MINUTES = 30`, `HOST_ACTION_RUN_MINUTES = 15` (named constants, minutes).

### Routes

Runner, port 8700. The shared `CLAUDE_RUNNER_API_KEY` covers create, list, get, claim and result. Confirm and
reject exist only on 8701; no route on 8700 that a Worker can call with the shared key sets `confirmed` or
`rejected`. The runner-side record of the operator's decision uses a separate dashboard-only secret:

- The runner route `POST /v1/host-actions/<id>/operator` requires `Authorization: Bearer <CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY>`.
- `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` is a new env var kept in the `ahawr-dashboard` environment and in the `claude-runner`
  environment; it is never given to the Worker run environment.
- The route answers 401 for the shared `CLAUDE_RUNNER_API_KEY`, the Worker key, so the Worker cannot set
  `confirmed` or `rejected`.
- The dashboard's confirm and reject handlers call that route with `Authorization: Bearer <CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY>`.

| Method | Path | Caller | Body | Response | Refusals |
|---|---|---|---|---|---|
| POST | `/v1/host-actions` | Worker | `{"action": "recreate" or "restart", "service": "<key in the whitelist>", "reason": "<one line>"}` | record, `status: "pending"` | 400 unknown action or service; 401 bearer |
| GET | `/v1/host-actions?status=<state>` | dashboard, executor | none | list of records | 401 |
| GET | `/v1/host-actions/<id>` | Worker, dashboard | none | record | 404 unknown id |
| POST | `/v1/host-actions/<id>/claim` | executor | none | record, `status: "running"` | 409 not `confirmed`, or a run is active (b), or Live State active (c); 404 |
| POST | `/v1/host-actions/<id>/result` | executor | `{"ok": true or false, "message": "<text>"}` | record, `status: "done"` or `"failed"` | 409 not `running`; 404 |
| POST | `/v1/host-actions/<id>/operator` | dashboard only, with `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` | `{"approved": true or false}` | record, `status: "confirmed"` or `"rejected"` | 409 not `pending` (approve) or not `pending`/`confirmed` (refuse); 404; 401 for `CLAUDE_RUNNER_API_KEY` |

`POST /v1/host-actions/<id>/operator` is a new public API route and needs approval. The bearer
check (`claude-runner/src/claude_runner/api.py:183-188`) must keep exempting everything else and check
`CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` only on the `/operator` route. A test must assert that a request to
`/operator` with `CLAUDE_RUNNER_API_KEY` gets 401.

The Worker run environment excludes `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` by its prefix: `child_env`
(`claude-runner/src/claude_runner/claude_cli.py:213`) builds the env as the container env minus every key that
starts with `CLAUDE_RUNNER_` (`claude_cli.py:232`), and `runs.py` calls it (`runs.py:486`). No change to `child_env`
is needed. A test must assert that the child env built by `child_env()` with
`CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` in `os.environ` never contains it.

Proposed compose diff, **not applied** (text only):

```diff
# claude-runner (docker-compose.yml, after the existing CLAUDE_RUNNER_* environment block)
+      - CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY=${HOST_ACTIONS_OPERATOR_KEY:-}
+
 # ahawr-dashboard (docker-compose.yml:279, next to DASHBOARD_RUNNER_API_KEY)
+      - CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY=${HOST_ACTIONS_OPERATOR_KEY:-}
```

Dashboard, port 8701 (`docker-compose.yml:272-273`, `127.0.0.1` only). Confirm and reject exist only here:

| Method | Path | Caller | Header | Body | Response | Refusals |
|---|---|---|---|---|---|---|
| GET | `/api/host-actions` | operator UI | none | none | list of records, proxied from the runner | 502 runner unreachable |
| POST | `/api/host-actions/<id>/confirm` | operator UI | `X-AHAWR-Dashboard: 1` | none | record, `status: "confirmed"` | 403 header missing; 404; 409 not `pending` or not CONFIRM tier |
| POST | `/api/host-actions/<id>/reject` | operator UI | `X-AHAWR-Dashboard: 1` | optional `{"reason": "<text>"}` | record, `status: "rejected"` | 403 header missing; 404; 409 only when the runner refuses the transition |

`board_header_guard` (`claude-runner/src/claude_runner/dashboard.py:147-152`) only covers paths that start with
`/api/board/`. The new `/api/host-actions/*` POST routes need the same `X-AHAWR-Dashboard` check, or a prefix
extension, and a test.

Record fields (proposed): `id`, `action`, `service`, `reason`, `status`, `created_at`, `confirmed_at`, `claimed_at`,
`finished_at`, `ok`, `message`.

### The request a task text can use

```bash
curl -sS -X POST http://claude-runner:8700/v1/host-actions \
  -H "Authorization: Bearer $CLAUDE_RUNNER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"action":"recreate","service":"ahawr-qa","reason":"QA_RUNS_DIR value changed in docker-compose.yml"}'
```

The response is the record with `"status":"pending"`. The task must not print the key; the `reason` must not hold secrets.

### Executor facts (from qa_worker.py)

- The executor follows `claude_runner/qa_worker.py`: the bearer comes from `CLAUDE_RUNNER_API_KEY` (`qa_worker.py:41`),
  the URL from `CLAUDE_RUNNER_URL` with default `http://claude-runner:8700` (`:39-40`), each call has a 15 s timeout (`:42`).
- Poll loop: `POLL_SECONDS = 60` (`:43`), backoff capped at `BACKOFF_MAX_SECONDS = 900` (`:44`), exit after
  `MAX_CONSECUTIVE_ERRORS = 10` (`:45`), cycle bound `MAX_POLL_CYCLES` (`:46`) in `main` (`:508`).
- Heartbeat: a JSON file at `QA_WORKER_HEARTBEAT_PATH`, default `D:\n8n\data-tables\ops\heartbeat\qa_worker.json`
  (`:59-60`), written by `_write_heartbeat` (`:173`). The executor should write its own file the same way.

### Open items (ask first)

1. The runner route `POST /v1/host-actions/<id>/operator` is a new public API route and needs approval. It uses
   `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY`, a dashboard-only secret not given to the Worker run environment.
2. The three `HOST_ACTION_*` bounds are proposals.
3. The "queue paused" state is not recorded anywhere in code; the executor relies on (c) only.
4. Claim-time checks (b) and (c) are a recommendation, not yet tested.

## Existing restart path

Container side (`claude-runner/src/claude_runner/guard_restart.py`):

- `QueueRestartGuard.check` (`guard_restart.py:53-63`) finds a finding only when `is_alive() is False` and
  `running_count() == 0`, no request file exists, and fewer than `RESTART_LIMIT_PER_HOUR` (4) requests were written in
  the last `HOUR_S` (3600 s). `None` (unknown) blocks the request.
- `QueueRestartGuard.act` (`guard_restart.py:65-84`) writes `{request_id (uuid4), requested_at (ISO 8601, UTC, seconds),
  reason: "queue_not_alive", limits: {per_hour, window_s}}` to `queue_restart.req.tmp`, then `os.replace` to
  `queue_restart.req`. The rename is the atomic step.
- Mismatch: `guard_restart.py:76` adds a `limits` key. The host launcher accepts only the three keys
  (`queue_launcher.py:35` `REQUEST_TYPES`, `:55-56` `set(obj) != set(REQUEST_TYPES)`), and `docs/guards.md:140` also
  specifies three keys. Every request written today is therefore discarded as invalid. Fix one side (drop `limits`
  from the guard, or allow it in the launcher) before the launcher is wired.

Host side (`data-tables/ops/queue_launcher.py`):

- One pass per run, no loop: the host starter runs `queue_launcher.py --request-dir <dir>` each poll interval
  (module docstring, lines 3-8).
- `read_request` (`:71-79`) returns `None` for missing, non-JSON or invalid content; invalid content is removed with a
  `discarded` entry (`:110-115`).
- `decide` (`:43-52`) checks, in order: already handled (request_id in the result log), age over `MAX_AGE_S` (600 s),
  `executions != 0` (uses `executions_running()`, `-1` = unknown blocks), and `MAX_PER_HOUR` (4) starts in `HOUR_S`.
- A start calls `start_queue()` (`:131`), then `finish` (`:94-98`) appends to `queue_launcher.results.jsonl` and removes
  the request file. Dry-run changes nothing.
- The launcher's record is the results JSONL, not a heartbeat file. It writes no heartbeat today.

What host actions reuse:

- Request file: tmp-then-`os.replace` on write, host-side re-validation of every key and type, request_id for
  idempotency, host-side recheck of limits (never trust the request's own limits).
- One-pass-per-run driven by the host starter; no in-process loop on the host.
- Heartbeat file format (`qa_worker._write_heartbeat`, `qa_worker.py:173-183`): JSON `{"name", "time", "last_action"}`,
  `time` as `%Y-%m-%dT%H:%M:%SZ` UTC (`guard_heartbeat.py:25` `TIME_FORMAT`), written to `.json.part` then `os.replace`.
  `guard_heartbeat._read` (`guard_heartbeat.py:95-128`) reads it: `fresh` or `stale` by `stale_seconds`, `error` when
  unreadable. A new executor is reported only after its name is added to `HEARTBEAT_NAMES`
  (`guard_heartbeat.py:23`, currently `docker_watchdog`, `gpu_power_governor`).
- Note: the request `requested_at` uses an offset (`fromisoformat`), the heartbeat `time` uses `Z`; keep them separate.

## Telegram

Notice sender:

- The one sender is `Notifier.send` (`claude_runner/guards.py:425`). It posts to Telegram; with the Telegram variables
  missing it logs the text and returns `True` (`guards.py:433-435`).
- Mission-queue notices: `MissionNoticeGuard._deliver` (`claude_runner/guard_queue.py:206-213`) calls
  `self.notifier.send(scrub(text))` at `guard_queue.py:211`. The idle notice is `IdleGuard.act`
  (`guard_queue.py:288-291`, `send` at `:291`). Heartbeat notices are `HostHeartbeatGuard.act`
  (`guard_heartbeat.py:83-91`, `send` at `:91`).
- A CONFIRM-tier request would be announced from an `act` in the same pattern, `notifier.send(scrub(text))` with the
  host-action id in the text. The id is not a secret; `scrub` runs first, as at `guard_queue.py:211`.

Telegram command decision: NOTICE ONLY.

- `guard_relay.py` does not parse commands. `_forward` (`guard_relay.py:233-242`) posts every update as raw JSON
  (`json=update`) to `DASHBOARD_GUARDS_TG_WEBHOOK_URL`, then saves the offset. The `confirm <id>` text would reach n8n,
  not the relay's own logic.
- The relay has no sender check: `grep -n chat claude-runner/src/claude_runner/guard_relay.py` returns no match, so
  any user who can message the bot passes the relay.
- The relay's environment holds only the bot token, webhook URL, offset path and dry-run flag
  (`guard_relay.py:24-27`, `load_relay_settings` `:79-88`). It has no dashboard key. Acting on `confirm <id>` needs
  `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY` (`docs/host-actions.md` open item 1), held by n8n or the relay: a new secret
  and a new route. Both are outside this task.
- Decision: a CONFIRM-tier request is announced in Telegram as a notice only; confirmation stays on the dashboard
  (operator confirm, dashboard-only key). Revisit only with a chat-id allowlist and an approved secret.

## Conditions

The claim route (`POST /v1/host-actions/<id>/claim`) grants a claim only when every condition below passes. The runner
checks them in this order at claim time; the first failure answers 409 and names the condition in the response body.

1. No active run: no Worker, Reviewer or Architect run in `ACTIVE` (`store.py:18`) and no QA request `queued` or
   `running` (section (b) above).
2. Queue at a task boundary: `BoardStore.running_missions()` is empty (section (c) above). A paused queue cannot be
   read from code (gap in (c)), so "paused" is not a separate state; an empty running set is the only accepted one.
3. `min_seconds_between_actions` per service: a new claim for service S waits until `finished_at` of the last `done`
   or `failed` request for S plus `min_seconds_between_actions`. This is a key of each service in the whitelist file;
   proposed value 300 for every service.
4. `claude-runner` waits for the requesting run: the record keeps the `run_id` of the Worker that asked (proposed
   field). A claim for `claude-runner` is refused while that run is in `ACTIVE`, because the recreate stops the container
   and with it that run. Condition 1 already refuses every claim while any run is active; this rule is written out
   because it is the case the operator will ask about.
5. `max_pending_hours`: a `pending` request older than this many hours becomes `expired` on read. Proposed value 1,
   a top-level key of the whitelist file.

A request whose conditions fail is never forced: it keeps its state (`pending` until the operator confirms, `confirmed`
after), the claim answers 409, and the executor tries again on its next poll. It ends `expired` when its own bound passes.

## Executor

- Runs on the Windows host, outside every container, as one Python process (proposed script:
  `data-tables/ops/host_actions_worker.py`, modelled on `data-tables/ops/qa_worker.py`). It lives outside
  Git, like `qa_worker.py`. The test `claude-runner/tests/test_host_actions_worker.py` imports it by path,
  the same way `test_qa_worker.py` imports `data-tables/ops/qa_worker.py`.
- The operator starts it by hand in a host terminal. Nothing starts it automatically.
- Fixed project directory: every `docker` command runs with the working directory `PROJECT_DIR` (the stack's compose
  directory, `D:\n8n`). The directory is never taken from a request or from the environment. Commands are argv lists
  with no shell.
- One action at a time: claim one request, apply it, post the result, then poll again. No second claim is made before
  the first result is posted or its run bound has passed.
- Before a claim it runs `docker version` once; if that fails, it makes no claim and polls again later.
- Polls the runner at `http://127.0.0.1:8700` (the loopback publish, `docker-compose.yml:130-131`) with the bearer from
  `CLAUDE_RUNNER_API_KEY`, with the 15 s per-call timeout of `qa_worker.py:42`, and sleeps `POLL_SECONDS = 60` between
  cycles (as `qa_worker.py:43`).
- Heartbeat: after every cycle it writes `data-tables/ops/heartbeat/host_actions_worker.json` as
  `{"name": "host_actions_worker", "time": "<UTC, %Y-%m-%dT%H:%M:%SZ>", "last_action": "<request id or none>"}`, through a
  `.part` file and `os.replace`, the same as `qa_worker._write_heartbeat` (`qa_worker.py:173-183`). The name
  `host_actions_worker` is added to `HEARTBEAT_NAMES` (`guard_heartbeat.py:23`) before the dashboard reports it.
- `HOST_ACTIONS_DRY=1`: dry run. For each confirmed request that passes the claim check it claims the request, prints
  `DRY RUN, not executed: [argv]`, and posts `{"ok": true, "message": "dry run"}` so the record reaches `done`. It runs no
  `docker` command. The heartbeat is still written. Dry run changes no state other than moving the record to `done`.

## Failure handling

- The command fails (non-zero exit) or runs past `HOST_ACTION_RUN_MINUTES = 15` minutes: the executor posts
  `{"ok": false, "message": "<first MESSAGE_MAX_CHARS = 500 characters of stderr, or timeout>"}` and the record ends
  `failed`. The executor does not retry the same request; the operator decides whether to ask again.
- Docker is down: the `docker version` check before the claim fails, so no claim is made and the request stays
  `confirmed` until `HOST_ACTION_CLAIM_MINUTES` passes. If the daemon fails during a command, that command fails as
  above with the docker error in `message`.
- The executor dies mid-run: the request stays `running` and no result is posted. When `HOST_ACTION_RUN_MINUTES` have
  passed since `claimed_at`, the runner marks it `failed` on the next read, the same way as `_expire`
  (`qa_requests.py:176`), with the message "run bound passed". The action may or may not have been applied; the
  operator checks `docker ps` before asking again. The executor does not re-claim `running` requests, so a restarted
  executor sees none of them.
- The result POST fails because `claude-runner` is being recreated: the executor retries it on each cycle; if the run
  bound passes first, the request is `failed` (see Lifecycle).

## Security notes

- No Docker socket in any container. `/var/run/docker.sock` is mounted in no service of this stack, including
  `claude-runner` and `ahawr-dashboard`. The executor is a host process and the only code path that runs `docker` for
  host actions.
- No request argument reaches a command line. `service` and `action` are keys looked up in the whitelist; the command
  is the whitelist template with `<service>` or `<container>` filled from the whitelist value. `reason` is stored and
  shown, never executed. The executor builds argv lists and never uses a shell.
- The Worker cannot confirm. Confirm and reject exist only on the dashboard (8701, `127.0.0.1`) with
  `X-AHAWR-Dashboard: 1`. The runner route `/operator` needs `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY`, which the Worker
  environment excludes (`claude_cli.py:232`), and it answers 401 to `CLAUDE_RUNNER_API_KEY`.
- `reason` and `message` hold no secrets: the executor never writes its environment, its headers or the bearer into a
  request record or a log line.

## Flag HOST_ACTIONS

- Environment variable `HOST_ACTIONS`, OFF by default (unset, empty or `0`). It must be on in the `claude-runner`
  environment for the runner, and in the executor's host environment for the executor.
- Off: every `/v1/host-actions` route (create, list, get, claim, result, operator) answers 404 and writes nothing; the
  dashboard `/api/host-actions/*` routes answer 404 too; the executor exits at start with a message and code 0.
- With the flag off no behaviour changes: no queue, run, guard or container differs from today, and the compose diff
  above is not applied.

## Rollback

1. Stop the executor (the operator closes its host terminal or stops its process). Check that
   `data-tables/ops/heartbeat/host_actions_worker.json` stops moving.
2. Set `HOST_ACTIONS` empty in `.env` and recreate `claude-runner` (operator act; the Worker does not do it). Before
   that, check `docker ps` for a host action that was running.
3. Nothing is deleted: the record file `claude-runner-data/host_actions.json` stays. Requests left `pending` or
   `confirmed` are not acted on while the flag is off. A request left `running` stays `running` until the flag is on
   again; then the run bound marks it `failed`.

## Activation (operator)

Every command below is NOT RUN. The Worker runs none of them; the operator runs them by hand, one step at a time.
The Worker never restarts or recreates a container, and the commands are written for the operator's host terminal
at `D:\n8n` (`/d/n8n` in Git Bash).

1. NOT RUN: set the flag in `.env` (the compose pass-through is open, see the note below):

   ```bash
   # NOT RUN
   HOST_ACTIONS=1
   ```

   Note: no compose line passes `HOST_ACTIONS` into `claude-runner` or `ahawr-dashboard`. The proposed diff above
   covers only `CLAUDE_RUNNER_HOST_ACTIONS_OPERATOR_KEY`. Adding the pass-through for `HOST_ACTIONS` is a compose change
   and needs the operator's approval first.

2. NOT RUN: apply the proposed compose diff (the diff above, with `HOST_ACTIONS_OPERATOR_KEY` set in `.env`), only if
   it has been approved. Otherwise skip this step.

3. NOT RUN: recreate the runner and the dashboard by hand:

   ```bash
   # NOT RUN
   cd /d/n8n && docker compose up -d --no-deps claude-runner
   # NOT RUN
   cd /d/n8n && docker compose up -d --no-deps ahawr-dashboard
   ```

4. NOT RUN: start the executor on the host in dry mode, in its own terminal:

   ```bash
   # NOT RUN
   cd /d/n8n && HOST_ACTIONS=1 HOST_ACTIONS_DRY=1 python data-tables/ops/host_actions_worker.py
   ```

5. NOT RUN: post a first request for `ahawr-dashboard` restart (tier AUTO, so it is confirmed at creation):

   ```bash
   # NOT RUN
   curl -sS -X POST http://127.0.0.1:8700/v1/host-actions \
     -H "Authorization: Bearer $CLAUDE_RUNNER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"action":"restart","service":"ahawr-dashboard","reason":"activation check, dry run"}'
   ```

   Then read the record by its `id` from the response:

   ```bash
   # NOT RUN
   curl -sS http://127.0.0.1:8700/v1/host-actions/<id> -H "Authorization: Bearer $CLAUDE_RUNNER_API_KEY"
   ```

   In dry mode the executor claims the request, prints `DRY RUN, not executed: [argv]`, and posts
   `{"ok": true, "message": "dry run"}` so the record reaches `"status":"done"`. It runs no `docker` command (see
   Executor). Check the printed argv line in its terminal and the `done` record. Note: `decide` (`host_actions.py:237`)
   refuses the claim while `queue_at_boundary` and `queue_paused` are both false, and the executor passes both as false
   (`host_actions_worker.py:173-174`), so with the current code the claim is refused and the record stays `confirmed`
   until those facts are read; the dry `done` above is what the record reaches once the claim passes.

6. NOT RUN: stop the dry executor (Ctrl+C in its terminal), then start it without `HOST_ACTIONS_DRY`:

   ```bash
   # NOT RUN
   cd /d/n8n && HOST_ACTIONS=1 python data-tables/ops/host_actions_worker.py
   ```

   Post the same `ahawr-dashboard` restart request and read the record until `"status":"done"`. From this step on the
   restart is a real `docker` command (`docker restart ahawr-dashboard`), not a dry print.

### Rollback (operator)

1. NOT RUN: unset the flag: set `HOST_ACTIONS` empty in `.env`, then recreate the runner:

   ```bash
   # NOT RUN
   cd /d/n8n && docker compose up -d --no-deps claude-runner
   ```

2. NOT RUN: stop the executor: Ctrl+C in its host terminal, or stop its process, then check that
   `data-tables/ops/heartbeat/host_actions_worker.json` stops moving.

The effects on stored requests are in Rollback above.

## Not done

- No Docker socket proxy: the design does not need one. A proxy container would change the Docker topology and needs
  approval first.
- The queue is not moved into a container: `data-tables/ops` and the queue stay on the host, and this design changes
  neither.
- This document is the design only: the executor, the runner routes and the tests now exist, but the activation steps
  above are NOT RUN and the Telegram part is notice only.
