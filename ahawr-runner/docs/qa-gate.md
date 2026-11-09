# QA gate design

Status: implemented, not switched on. The runner queue (`qa_requests.py`, `/v1/qa/requests` routes) is in the repository; the host worker 
(`ahawr_runner.qa_worker`, started by `data-tables/ops/_start_qa_worker.ps1`; `data-tables/ops/qa_worker.py` is a retired stub that prints and exits 2) and the workflow patch (`data-tables/ops/qa_workflow_patch.py`, applied by `launch_mission.sh` for the `roles_json` QA mode) are operator scripts outside git. QA is OFF until the operator sets the mode and starts the worker.

## Architecture

The gate is split in three parts so that QA never runs inside n8n.

- **n8n (workflow)**: only asks and waits. Nodes `QA Request`, `QA Wait`, `QA Review` and `QA Notice`
  are inserted after the final acceptance node by `data-tables/ops/qa_workflow_patch.py`. They call the
  runner over HTTP, poll the request, decide the outcome and send at most one Telegram notice.
- **Runner (`ahawr-runner`)**: keeps a small QA request queue in `ahawr_runner/qa_requests.py`
  (pure state machine and store, no network). The routes are in `ahawr_runner/api.py`. The queue holds
  requests until the host worker claims and finishes them, or until they expire.
- **Host worker (`ahawr_runner.qa_worker`, started by `data-tables/ops/_start_qa_worker.ps1`)**: claims
  one request at a time, finds the changed files with git in the mission working directory, runs the
  layers the request asks for (`python -m ahawr_runner.qa <layer>`, `qa_worker.py:326-357`), and posts
  the verdict. When `options.project_apps` is true and the mission matches a profile with a `ui` block,
  the `ui` layer starts the project's own app on `127.0.0.1` instead of the qa child
  (`qa_worker.py:360-383`, `386-405`; see [qa-layers.md](qa-layers.md)). It runs on the host because it
  needs the host git checkout, the throwaway QA stack and, for a project app, the host checkout of the
  project.

## Sequence

```
n8n                      ahawr-runner (API + queue)              host worker (ahawr_runner.qa_worker)
 |  POST /v1/qa/requests        |                                          |
 |----------------------------->| create: status=queued                    |
 |  <-- id                      |                                          |
 |  QA Wait: GET ?status=queued  (worker side)                             |
 |                              |<---------- GET /v1/qa/requests?status=queued
 |                              |----------- queued requests ------------->|
 |                              |<---------- POST /{id}/claim -------------|
 |                              |   status: queued -> running              |
 |                              |                                          | git diff --name-only HEAD
 |                              |                                          | git ls-files --others --exclude-standard
 |                              |                                          | UI files changed?
 |                              |                                          |   no  -> verdict skipped
 |                              |                                          |   yes -> python -m ahawr_runner.qa <layer>
 |                              |                                          |        (ui: the project app on 127.0.0.1 when project_apps is true)
 |                              |<---------- POST /{id}/result ------------|
 |                              |   status: running -> done (verdict) or error
 |  GET /v1/qa/requests/{id}    |                                          |
 |----------------------------->| (repeated until done, error or timeout)  |
 |  <-- status + verdict        |                                          |
 |  QA Review (Code node): apply the fail-open / fail-closed table          |
 |  QA Notice (Telegram): one notice, or none for skipped                   |
```

## Request fields and states

Each request has: `id`, `mission_id`, `working_directory`, `mode` (`final` or `ui_tasks`), `status`,
`created`, `finished`, `verdict` (list of verdict values), `error` (text), and `nonce`. Create is
idempotent by the pair `mission_id` plus `nonce`: a second create with the same pair returns the
existing request.

Verdict values: `pass`, `fail`, `skipped`, `blocked`.

States: `queued`, `running`, `done`, `error`, `timeout`.

Legal transitions (any other transition is refused):

| From | To | Trigger |
| --- | --- | --- |
| (none) | `queued` | create (`POST /v1/qa/requests`) |
| `queued` | `running` | claim by the host worker (`POST /v1/qa/requests/{id}/claim`, bearer) |
| `queued` | `timeout` | expiry: not claimed within `QA_CLAIM_WAIT_MINUTES` |
| `running` | `done` | result with a verdict (`POST /v1/qa/requests/{id}/result`, bearer) |
| `running` | `error` | result with an error text (for example the QA run crashed) |
| `running` | `timeout` | expiry: no result within the worker run timeout after the claim |

Refused: claim of a request that is not `queued`; result for a request that is not `running`; any
transition out of `done`, `error` or `timeout`.

Expiry rule: a request nobody finishes becomes `timeout` once its bound passes, so n8n never waits
forever. The bound is `QA_CLAIM_WAIT_MINUTES` for a `queued` request and the worker run timeout for a
`running` request. Expiry is checked when the request is read or claimed. The runner keeps at most the
last 200 requests and drops the oldest finished ones first.

## Fail-open / fail-closed table

Rule: a QA verdict `fail` is the only outcome that sends a mission back. Infrastructure problems never
reject a mission; they accept it with one notice that QA could not run.

Retry counter: `retries` is the number of times this mission was already sent back for QA. It starts at
0 and is kept per mission, with no new data table column.

| # | Case | Source | Outcome | Retry counter | Notice text |
| --- | --- | --- | --- | --- | --- |
| 1 | verdict `pass` | verdict | accept | unchanged | `QA passed` |
| 2 | verdict `fail`, `retries < QA_MAX_RETRIES` | verdict | needs_changes review; `next_task` names the failed step and the reason | +1 | `QA failed (ui 3 of 20 scenarios):` then the first three scenarios with their reason and `(+N more)` |
| 3 | verdict `fail`, `retries = QA_MAX_RETRIES` | verdict | accept | unchanged | `QA still failing after 2 retries.` followed by the same summary |
| 4 | verdict `skipped` (no UI files changed) | verdict | accept | unchanged | none (silent) |
| 5 | verdict `blocked` (missing folder, not a git repository, Playwright missing, or the project app that did not start or never became healthy) | verdict | accept | unchanged | `QA could not run` with the reason |
| 6 | state `error` (QA run crashed or the worker reported an error) | infrastructure | accept | unchanged | `QA could not run` with the reason |
| 7 | state `timeout` after claim (worker run timeout reached) | infrastructure | accept | unchanged | `QA could not run` (timed out) |
| 8 | no claim within `QA_CLAIM_WAIT_MINUTES` (state `timeout` while `queued`) | infrastructure | accept | unchanged | `QA could not run` (no worker claimed the request) |
| 9 | runner unreachable (n8n HTTP call fails) | infrastructure | accept | unchanged | `QA could not run` (runner unreachable) |
| 10 | missing worker (no heartbeat, nothing claims) | infrastructure | accept, same as row 8 | unchanged | `QA could not run` (no worker) |

A mission with no changed UI files gets verdict `skipped` (row 4): it is accepted and no notice is sent.

## Constants

| Constant | Value | Unit | Where it lives | Status |
| --- | --- | --- | --- | --- |
| `QA_MAX_RETRIES` | 2 | send-backs per mission | named constant in the `QA Review` Code node, written by `data-tables/ops/qa_workflow_patch.py` | implemented |
| `QA_CLAIM_WAIT_MINUTES` | 5 (default) | minutes a request may stay `queued` | `ahawr_runner/qa_requests.py` (expiry of `queued` requests) and the `QA Wait` bound in `qa_workflow_patch.py` | implemented |
| worker run timeout | 20 | minutes per QA run | `QA_RUN_TIMEOUT_MINUTES = 20` in `ahawr_runner/qa_worker.py:65`; kills the QA child process tree; the runner's `running` expiry uses the same bound | implemented |
| `QA_WAIT_MAX_POLLS` | 50 (set in the patch) | polls | `QA Wait` node, written by `data-tables/ops/qa_workflow_patch.py` | implemented |
| poll interval | 30 (set in the patch) | seconds between polls | `QA Wait` node, written by `data-tables/ops/qa_workflow_patch.py` | implemented |
| stored request bound | 200 | requests kept | `ahawr_runner/qa_requests.py` | implemented |

Derivation of the poll bound: the longest legal wait is the claim wait plus the run timeout,
`QA_CLAIM_WAIT_MINUTES + worker run timeout = 5 + 20 = 25 minutes = 1500 seconds`. The number of polls is
`QA_WAIT_MAX_POLLS = ceil(1500 / poll interval)`; with a 30-second interval that is `ceil(1500 / 30) = 50`.
The poll bound is therefore never shorter than the runner's own expiry, so `QA Wait` cannot give up
before the runner marks the request `timeout`.

Not the same as the existing `qa` role option `max_minutes` (default 20, see `roles-config.md`), which
limits the Playwright QA run inside the runner; the worker timeout above is a separate, outer bound.

## Not in this part

Operator start and stop steps, the `roles_json` switch, the worker start line in
`data-tables/ops/_start_qa_worker.ps1`, per-task UI checks and parallel QA are described in the later
parts of this design.

## Operator

The host worker (`ahawr_runner.qa_worker`) is the only process that runs QA. It is started and
stopped on the host by the operator; n8n and the runner never start it. `data-tables/ops/qa_worker.py`
is a retired stub that prints and exits 2; `data-tables/ops/_start_qa_worker.ps1` is the launcher.

### Turn QA on

1. Set the `qa` options in `roles_json` to `mode` `final` (the limits keep their defaults, see `roles-config.md`):

   ```json
   {"qa": {"1": "anthropic/sonnet", "options": {"mode": "final", "max_minutes": 20, "max_cost_usd": 1}}}
   ```

   Add `"project_apps": true` only for the projects whose own app should run the `ui` layer; the key is
   absent by default, which keeps the qa child path (see [qa-layers.md](qa-layers.md)).

2. Start the host worker in a hidden window with `data-tables/ops/_start_qa_worker.ps1`, which runs
   `python -X utf8 -m ahawr_runner.qa_worker` with `QA_STACK_MODE=docker` (its lines 9 and 11).

### Turn QA off

1. Set `"mode": "off"` in the `qa` options of `roles_json`. From then on no new mission sends a QA request.
2. Stop the worker process: find the process whose command line contains `ahawr_runner.qa_worker` and stop
   it, the same way `retire_host_watchers.sh` stops the other watchers.

Requests still queued when the worker stops expire after `QA_CLAIM_WAIT_MINUTES`; those missions are accepted
with the notice `QA could not run` (table row 8). Stop the worker only after the mode is `off`, so that no
new request arrives while it is down.

### Heartbeat

The worker writes `data-tables/ops/heartbeat/qa_worker.json`, one JSON line with `name`, `time` and
`last_action`, the same format as `docker_watchdog.json`. The dashboard reads it through the read-only mount of
`data-tables/ops`. QA is on and the file is missing or stale: no worker claims requests, and missions get the
notice `QA could not run` (table row 10). The staleness limit is not defined yet.

## Not done

- Per-task `ui_check` runs: QA runs once per mission at the final acceptance node (`mode` `final`); no
  check runs after each task.
- Parallel QA: the worker claims one request at a time; no second QA run starts while one is running.
