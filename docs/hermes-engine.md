# Hermes engine

## Current Hermes calls

### Sources and verification

- **Live exports (staged by the operator on the host, files dated 2026-10-31):**
  `scratchpad/live_workflow_export.json` — workflow `AHAWR v13 — Claude Code`
  (`updatedAt` 2026-10-23, `active: false`, 102 nodes);
  `scratchpad/live_run_manager_export.json` — workflow `Claude Code Run Manager v1`
  (`updatedAt` 2026-09-27, `active: false`).
- **No fresh export of `Hermes Run Manager v5` exists on the host.** The only copy of that
  workflow is `Hermes_Run_Manager_v5.json` in the repository root, which is NOT what runs: every
  fact below about the Hermes run manager's own calls is tagged **repo copy, not verified against
  live**. Blocker for live facts: a Worker has no docker access, so the live n8n instance and the
  live `hermes_config` row cannot be read; the exports are copies, not proof that they are the
  active version.
- **Patch files:** `grep -n 'hermes\|HERMES' data-tables/ops/*_patch.py` gives 16 hits in 12 files, all
  either the `hermes_config` data table name or the `hermes_error` field of a Code node
  (`adaptive_split_patch.py:421`); no patch touches a Hermes-gateway node. The only added HTTP node that
  mentions a run is
  `adaptive_split_patch.py:903-906` (`Cancel Previous Run`), which targets `runner_url` — the runner, not
  the Hermes gateway.
- **`.env.example`, variable NAMES only:** `HERMES_API_URL` (line 198), `HERMES_API_KEY` (line 199),
  `HERMES_SESSION_SOURCE` (line 201); the comment at lines 196-197 names `API_SERVER_KEY` and says the key
  is the same value as the n8n `Bearer Auth account` credential. No values are quoted here.
- **docs/**: `docs/config-cutover.md:98,100,101,114` (run manager wiring),
  `docs/config-columns-plan.md:28,72`, `docs/guards.md:49,54` (cancel route of the runner API).

### How the workflow reaches Hermes today

The main workflow does not call the Hermes gateway itself: its HTTP nodes go to the retrieval service
(`Retrieve Worker Context` → `retrieval_url + '/retrieve'`) and to the runner (`QA Request` →
`runner_url + '/v1/qa/requests'`, `QA Poll`) — node names in
`scratchpad/live_workflow_export.json`. Hermes is reached only through the sub-workflow named by
`run_manager_workflow_id` (`Architect Start`, `Worker Start`, `Reviewer Start`,
`Reviewer Verdict Retry Start`; `docs/config-cutover.md:98,100,101,114`). Default `nhjwX1G7FiVTO2Ah`
(`docs/config-cutover.md:114`). The live `engine_json` example in `docs/config-columns-plan.md:72` names
`6WE2ATGEDzUopBJH`, which is `Claude Code Run Manager v1` — that run manager calls
`http://ahawr-runner:8700`, not Hermes. Which id the live `hermes_config` row names today is
**UNKNOWN** (no docker access), so whether Hermes is called at all in the current live setup is unknown.

### Base URL

- Repo copy: hard-coded `http://host.docker.internal:8642` in `Hermes_Run_Manager_v5.json:33`
  (repo copy, not verified against live). No node reads `HERMES_API_URL` from the environment in either
  export (`grep -o '\$env\.[A-Z_]*'` on both staged exports returns nothing): the env variable is used by
  the dashboard, `docker-compose.yml:355` (`DASHBOARD_HERMES_URL=${HERMES_API_URL-...}`).
- UNKNOWN: whether the live Hermes run manager uses `HERMES_API_URL` or the same hard-coded host.

### Endpoints and HTTP methods

Source for every row: `Hermes_Run_Manager_v5.json` (repo copy, not verified against live), node name given.

| Endpoint | Method | Node name | Line |
|---|---|---|---|
| `/v1/runs` | POST | Start / Resume Run | 32-33 |
| `/v1/runs/{run_id}` | GET (method not set in the node → n8n default GET) | Get Run Status | 95 |
| `/v1/runs` | POST | Resume Existing Session | 185-186 |
| `/v1/runs` | POST | Start New Session from Handoff | 1188-1189 |
| `/auth/password-login` (dashboard port 9119) | POST | Hermes Dashboard Login | 1234-1235 |
| `/api/auth/ws-ticket` (dashboard port 9119) | POST | Get TUI WS Ticket | 1292-1293 |

Compression is not an HTTP call on the API gateway: the `Compress Session` node is
`n8n-nodes-base.executeCommand` (`Hermes_Run_Manager_v5.json:1083,1090`), which runs a python script in
the container. UNKNOWN: the endpoints the dashboard calls on
port 9119 are not part of the API gateway (`/v1/...`); their live state is unknown.

### Auth header name

- `Authorization` (Bearer), through the n8n generic credential `httpBearerAuth`, credential name
  `Bearer Auth account` (`Hermes_Run_Manager_v5.json:73-74,119`). Its value is the `API_SERVER_KEY`
  value named in `.env.example:196-197`; the value is not recorded here.
- Custom headers on the start call: `Content-Type: application/json`, `X-Hermes-Session-Id`
  (`Hermes_Run_Manager_v5.json:44`), `X-Hermes-Working-Directory` (`Hermes_Run_Manager_v5.json:48`).
- The poll node sends no custom headers (`Hermes_Run_Manager_v5.json:99-100`): `headerParameters` empty.
- Contrast, for the run manager that is exported live (`scratchpad/live_run_manager_export.json`, node
  `Start / Resume Run`): the `Authorization` header is built from `runner_api_key`
  (`.env.example` name `CLAUDE_RUNNER_API_KEY`, line 78) and an extra header `X-AHAWR-Role`.

### Start request body

Node `Start / Resume Run`, `jsonBody` (`Hermes_Run_Manager_v5.json:55`): `input` (or `resume_input`),
`model`, `provider`; `session_id` only when a session id is present, `working_directory` only when set.
The same fields come from `Normalize Request` (`Hermes_Run_Manager_v5.json:19`), which throws if
`model`, `provider` or the input is missing.
UNKNOWN: the live body of the Hermes call — the staged export holds the Claude Code run manager body,
which adds `role` and the `think_policy` fields.

### Poll endpoint and interval

- Poll endpoint: `GET /v1/runs/{run_id}` (`Get Run Status`, `Hermes_Run_Manager_v5.json:95`).
- Interval: `Poll Wait` = `poll_seconds` (`Hermes_Run_Manager_v5.json:408`), default 3, from
  `Normalize Request` (`:19`: `poll_seconds ?? 3`, `max_polls ?? 40`). The values come from the
  `hermes_config` columns `architect_poll_seconds` / `worker_poll_seconds` / `reviewer_poll_seconds` and
  `*_max_polls` (`docs/config-cutover.md:813-814`), passed by the main workflow's `Build * Run Input`
  nodes into the sub-workflow.
- Both HTTP nodes run with `neverError: true` and `responseFormat: text`
  (`Hermes_Run_Manager_v5.json:59,105`), so an HTTP error is parsed from the body by
  `Evaluate Status` (`:128`), not thrown by n8n.
- UNKNOWN: the live poll interval values (the `hermes_config` row is not readable without docker).

### Stop / cancel

- UNKNOWN for the Hermes gateway: the repo copy has no stop, cancel or abort HTTP node
  (`grep -c 'cancel' Hermes_Run_Manager_v5.json` → 2 hits, both inside `Evaluate Status` reading a run
  status `cancelled`). A run cancelled by another client is read as status `cancelled` / `canceled` →
  route `cancelled` (`Hermes_Run_Manager_v5.json:128`).
- The runner API has the cancel route `POST /v1/runs/{run_id}/cancel` (`docs/guards.md:49,54`) and the
  patch adds `Cancel Previous Run` against `runner_url` (`adaptive_split_patch.py:903-906`) — different
  service, not the Hermes gateway.

### Session and continue (follow-up) fields

- Session fields in the start body/header: `X-Hermes-Session-Id` (`Hermes_Run_Manager_v5.json:44`),
  `session_id` in the body (`:55`). Response session keys read by `Capture Run Response`: `session_id`,
  `sessionId`, `session.session_id`, `session.id`; a mismatch against the requested session sets
  `session_mismatch` (`Hermes_Run_Manager_v5.json:19`, `:91`).
- Continue: `input` / `resume_input`; on `route === 'restart_session'` the node `Resume Existing
  Session` re-POSTs `/v1/runs` with `session_id` (`:208`). A new session from a compression handoff
  POSTs without `session_id` (`:1207`).
- UNKNOWN: whether `HERMES_SESSION_SOURCE` (`.env.example:201`) affects the workflow. It is read by the
  dashboard only.

### Result shape

As read by `Evaluate Status` (`Hermes_Run_Manager_v5.json:128`) and returned by `Build Result`
(`:431`): `run_id`, `session_id`, `status`, `output` (read as `output` / `response` / `content`), `error`,
`error_code`, `http_code`, `poll_count`, `retry_count`, `run_not_found`, `transient_error`, `resumed`,
`session_verified`, `session_mismatch`, `route`, `final_status`, `failure_reason`, `service_model`,
`service_provider`, `compression_failed`, `compression_handoff`, `handoff_from_session_id`.
The start response gives `run_id` and a session key (`Capture Run Response`, `:91`).
UNKNOWN: the exact field names the Hermes gateway returns — the repo copy reads them with fallbacks, so
the gateway's own shape is not fixed by these files.

### Error shape and HTTP status codes

Error object read from the body (`Evaluate Status`, `Hermes_Run_Manager_v5.json:128`): keys `error`,
`message`, `details`, `detail`, and a `code`. Status code from the envelope `statusCode` or from the
body, or from a `4xx`/`5xx` inside the error text.

- `404` (or code `run_not_found` / `not_found`, or the text "run not found") → `run_not_found`, route
  `restart_session` when a session was requested, else `start_new`.
- `failed` + transient code `408, 409, 425, 429, 500, 502, 503, 504, 529` or a transient word
  (`temporarily overloaded`, `upstream error`, `service unavailable`, `timeout`, `rate limit`, …) →
  route `transient_error`; retries come from `Retry Controller` with `retry_delays` (default
  `15, 30, 60, 120, 300`) and `max_retries` (default 3), `Retry Wait` between attempts.
- `failed` otherwise → `permanent_failure`.
- `completed` → `completed`; `cancelled` / `canceled` → `cancelled`.
- `running`, `started`, `queued`, `waiting_for_approval` → `running`, or `timeout` after `max_polls`
  (default 40).
- The parent reads the result through `Evaluate *` nodes, which keep the `http_error_code` and
  `hermes_error` fields (`scratchpad/live_workflow_export.json`, nodes `Evaluate Architect`,
  `Evaluate Worker`, `Evaluate Reviewer`; `adaptive_split_patch.py:421`).
- UNKNOWN: which status codes the Hermes gateway itself returns for success (the repo copy only lists the
  codes the workflow treats as transient).

## Runner lifecycle mapping

Sources for this section: the runner code itself (`ahawr-runner/src/ahawr_runner/`); Hermes calls as
recorded above under "Current Hermes calls". A future Hermes engine module mirrors the Codex module layout.

### Hermes call -> runner step -> Codex counterpart

| Hermes call (T001A) | Runner step | Codex counterpart (the layout the Hermes module mirrors) |
|---|---|---|
| start (`POST /v1/runs`) | `RunManager.start` (`runs.py:310`); engine dispatch to the module at `runs.py:327-332` | `codex_run.bind` (`codex_run.py:37`), run executed by `codex_run.execute` (`codex_run.py:60`), dispatched at `runs.py:458-461` |
| poll (`GET /v1/runs/{run_id}`) | `RunManager.get` (`runs.py:836`); the view `RunManager.public` (`runs.py:279`, its docstring says the view is Hermes-compatible) | No poll-side function in the Codex module: the row is written by `_finish` from `codex_run.py:138`; the state read back after the run is the `CodexEventMapper` attributes (`codex_events.py:158-167`) |
| stop (UNKNOWN on the Hermes side) | `RunManager.cancel` (`runs.py:845`): flag `_cancelled`, `_terminate_proc` (`runs.py:741`), `_stop` (`runs.py:722`); cancel-while-queued finishes as `cancelled` at `runs.py:426-435` | The cancel flag is read in `codex_run._outcome` (`codex_run.py:214-215`); the run-timeout path calls `manager._terminate_proc` (`codex_run.py:188-192`) |
| continue | `start` with `session_id`: attaches to the active run (`runs.py:319-324`); session handling `_bind` (`runs.py:361`) | `codex_run.bind` (`codex_run.py:47-51`: resumes only a captured thread id, a fresh retry never resumes); the resume command `build_resume_command` (`codex_cli.py:46`) |
| result | `RunManager._finish` (`runs.py:651`), per-run share `run_cost` (`runs.py:71`), the end entry (`runs.py:705-714`); result class `Outcome` (`claude_cli.py:411`) | `codex_run._outcome` (`codex_run.py:196`), `_result` (`codex_run.py:229`), `_cumulative_cost` (`codex_run.py:249` mirrors `run_cost`), `_take_last_message` (`codex_run.py:268`) |
| error | `RunnerError` (`runs.py:157`, code + status_code); the error object is built in `public` (`runs.py:281-283`) | `classify_failure` (`codex_events.py:136`), `_match_error` (`codex_events.py:122`) |

Event append: the prompt entry is written at `runs.py:402` (`events.open` at `runs.py:401`); the Claude
stream goes through `events.feed` (`runs.py:593`, `StreamState.feed` `claude_cli.py:309`); Codex writes
entries through `manager.events.add` (`codex_run.py:176`) from `CodexEventMapper.feed`
(`codex_events.py:169`).

### Failure classes and the Hermes mapping

Runner failure-class constants, defined in `codex_events.py:29-32`: `TRANSIENT` (`:29`), `PERMANENT`
(`:30`), `CONTEXT_OVERFLOW` (`:31`), `OTHER` (`:32`). The runner exception class is `RunnerError`
(`runs.py:157`).

| Hermes error or status (T001A) | Runner failure class / code | Codex rule that produces it (file:line) |
|---|---|---|
| `transient_error` route (codes 408, 409, 425, 429, 500, 502, 503, 504, 529 or transient words) | `TRANSIENT` | `FAILURE_BY_CODEX_INFO` `rateLimitExceeded`/`serverOverloaded`/`internalServerError` and stream-disconnect codes (`codex_events.py:33-39`), `FAILURE_BY_HTTP_STATUS` 429/529 (`codex_events.py:46`); words in `FAILURE_BY_MESSAGE` (`codex_events.py:47-51`) |
| `permanent_failure` route | `PERMANENT` | `unauthorized`, `badRequest`, `usageLimitExceeded` (`codex_events.py:40-42`) |
| context overflow / compression (`compression_failed`, routes `restart_session` / `start_new` on the Hermes side; no error code in the T001A error section — UNKNOWN) | `CONTEXT_OVERFLOW` | `contextWindowExceeded` (`codex_events.py:43`) and the message needles (`codex_events.py:49-50`) |
| `run_not_found` / 404 | `RunnerError("run_not_found", 404)` (`runs.py:839`; interrupted runs are reported as `run_not_found` so the run manager resumes the session, `runs.py:841-842`, `runs.py:656`) | UNKNOWN for the Codex module (no not-found class there) |
| `cancelled` | `Outcome("cancelled")` (`claude_cli.py:411`; `runs.py:426-435`) | `run_id in manager._cancelled` branch (`codex_run.py:214-215`) |
| `timeout` after `max_polls` (poll timeout, n8n side) | UNKNOWN for the runner step; runner-side the run timeout finishes `failed`/`timeout` 408 | `codex_run.py:216-218` |
| CLI start failure (UNKNOWN on the Hermes side) | `cli_unavailable` 503 (`runs.py:569`) | `codex_run.py:99-108` |
| no failed turn event + non-zero exit | `OTHER` + `cli_error` | `classify_failure` (`codex_events.py:144-148`); pump read failure also maps to `cli_error` (`codex_run.py:244-245`) |

### Codex module layout (the second-engine example)

Public functions of `codex_run.py`: `bind` (`:37`), `session_dir` (`:54`), `execute` (`:60`). Public
function of `codex_events.py`: `classify_failure` (`:136`) and `CodexEventMapper` (`:151`, `feed`
`codex_events.py:169`). Entries use the kinds and fields of `events.py` (`codex_events.py:5-7`); unmapped
lines are logged and skipped, never raise (`codex_events.py:6-7`). The module never touches the process
registry, timeout, cancel or watchdog handling — those stay in `RunManager` (`codex_run.py:4-5`).
Session resume: the session row keeps the runner session id until the run captures the thread id, then
`bind` returns the captured id with `resume=True` (`codex_run.py:40-51`); the captured id is written back
after the run at `codex_run.py:136-137`.

### Helpers the Hermes module must reuse (exact names)

- HTTP/JSON: `api.py _json_body` (`api.py:146`, request-body validation), `sources.py _check`
  (`sources.py:49`, response to JSON), `sources.py _rows` (`sources.py:59`). No shared JSON-parse helper
  exists for engine modules — Codex parses with `json.loads` in `_parse_event` (`codex_events.py:77`); do
  not add a second parse helper.
- ISO time: `store.py now()` (`store.py:74`), used by `RunManager` for `started_at`/`finished_at`
  (`runs.py:438`, `runs.py:692`); `sources.py _iso` (`sources.py:28`). Event entries stamp time as
  `t: round(time.time(), 3)` (`events.py:335`).
- Event writer: `EventLog` (`events.py:289`) with `open` (`events.py:320`), `add` (`events.py:330`),
  `feed` (`events.py:341`), `close` (`events.py:356`); the text-clip helper `_clip` (`events.py:26`) is
  reused by `codex_events.py` (`codex_events.py:18`).

## Thinking profiles

The Architect may put an optional field `thinking` on a plan task with a profile name: `none`,
`plan`, `plan+pulse`, `heavy`. A missing or unknown name means the global policy of `hermes_config`
— the task runs as today. The catalog of profiles lives in `hermes_config` `thinking_json`: each
profile is a think_policy variant list in the format `think_policy.py` already parses, plus a cap —
the maximum thinking steps per task. The Architect picks only the name; the numbers come from the
catalog.

Example catalog (`none` means no thinking steps; `plan+pulse` is the current
`data-tables/ops/think_policy_plan_pulse.json` — think 8, off 25, think 10, off):

```json
{
  "profiles": {
    "none": {"variants": [{"name": "none", "first": [["off", null]]}], "cap": 0},
    "plan+pulse": {"variants": [{"name": "plan+pulse", "weight": 1,
      "first": [["think", 8], ["off", 25], ["think", 10], ["off", null]],
      "retry": [["think", 8], ["off", 25], ["think", 10], ["off", null]]}], "cap": 18}
  }
}
```

`plan` and `heavy` follow the same shape. The feature is behind the `features_json` flag
`think_profiles`, OFF by default. With the flag off the behaviour is byte-for-byte unchanged: no
task field is read and no per-run policy is added. With it on, the Worker launch passes the chosen
profile as the per-run `think_policy` (`runs.think_policy` exists and ThinkSwitch follows it), and
the profile name is recorded in `runs.think_variant` so statistics can group runs by it.

Architect selection rule (DESIGN, item 5): `none` for mechanical edits (rename, move, constants),
`plan` for tasks that read unfamiliar code, `plan+pulse` for tasks of five steps or more with
debugging, `heavy` only for finding the cause of a failure. In a characterization-tests /
one-change / checks trio: tests `plan`, change `plan+pulse`, checks `none`.

## Proposal: Run Manager change

Text only, as asked: no patch file is written, nothing under `data-tables/ops` is created or changed,
and the live workflow is not touched. The target is the sub-workflow named by `run_manager_workflow_id`
(`docs/config-cutover.md:98,100,101,114`), whichever id the live `hermes_config` row carries — the
Hermes run manager (repo copy `Hermes_Run_Manager_v5.json`) or the Claude Code run manager that is
exported live (`scratchpad/live_run_manager_export.json`).

### The idea in one sentence

The run manager keeps its structure — start, poll, evaluate, build result — and changes only the
address of its HTTP nodes: the runner (`runner_url`) instead of the Hermes gateway (port 8642). The
Hermes engine then becomes a provider of the runner, reached through the runner's own dispatch, rather
than a second service the workflow talks to directly.

### The calls, node by node

- **`Start / Resume Run`** (node name `Hermes_Run_Manager_v5.json:72`, URL `:33`): the URL becomes
  `runner_url + '/v1/runs'`, the route the Claude Code run manager already uses
  (`api.py:382`). The `jsonBody` gains no field: `input`, `model`, `provider`, optional `session_id`,
  optional `working_directory` are exactly what the runner reads (`api.py:405-408`), and the
  `Normalize Request` guards stay (`Hermes_Run_Manager_v5.json:19`).
- **Auth**: the gateway Bearer credential (`httpBearerAuth`, `:73-74,119`) is replaced by
  `runner_api_key` (`CLAUDE_RUNNER_API_KEY`, `.env.example:78`) and `X-AHAWR-Role` is added, exactly as
  in the live run manager export; the runner takes the role from the body or from that header
  (`api.py:388,409`).
- **`X-Hermes-Session-Id` (`:44`) and `X-Hermes-Working-Directory` (`:48`)**: they may stay — the
  runner reads them as fallbacks for the body fields (`api.py:386-387,407-408`). If a patch deletes
  them, it must first prove the body carries both values, or every start becomes a fresh session.
- **`Get Run Status`** (`:95`): `GET runner_url + '/v1/runs/' + run_id` (`api.py:422`). The view is
  already Hermes-shaped — `run_id`, `session_id`, `status`, `output`, `error`, `http_code`
  (`runs.py:281-309`) — so `Evaluate Status` (`:128`) needs no new key.
- **`Poll Wait`** (`:408`) and the `poll_seconds` / `max_polls` columns of `hermes_config`: unchanged.
  The runner bounds the loop itself (`hermes_poll_interval_ms`, `hermes_max_polls`,
  `hermes_run_timeout_ms`, `config.py:264-269`), so the n8n `max_polls` becomes a ceiling rather than
  the only bound.
- **Stop**: the manager can add `POST runner_url + '/v1/runs/{run_id}/cancel'` (`api.py:426`); today it
  has no stop node against the gateway at all (see "Stop / cancel").
- **Error handling**: `neverError: true` and `responseFormat: text` (`:59,105`) stay — the runner
  answers an error as a JSON body, and `Evaluate Status` still decides the route.
- **Retry and compression nodes**: `Resume Existing Session` (`:185-186`, `:208`) re-POSTs with
  `session_id` on the same runner route; `Compress Session` is an executeCommand in the container
  (`:1083,1090`) and is outside the URL change.

### Runner-side prerequisites, not part of the patch

- `CLAUDE_RUNNER_HERMES_ENGINE_ENABLED` on the runner container (`config.py:698`). With it off,
  `ENGINE=hermes` is rejected by the config with the pre-existing message (`config.py:329-335`) and the
  dispatch keeps the Claude Code path byte for byte — pinned by
  `ahawr-runner/tests/test_hermes_run.py::test_flag_off_run_stays_on_the_claude_path_byte_for_byte`.
- The provider block whose name the run manager sends as `provider` must carry
  `CLAUDE_RUNNER_PROVIDER_<NAME>__ENGINE=hermes` plus the gateway env-variable NAMES
  (`HERMES_BASE_URL_VAR`, `HERMES_API_KEY_VAR`, `config.py:474-487`). Values stay in the runner's env;
  the workflow JSON never holds a key.
- Setting `run_manager_workflow_id` to a new workflow id is a change of persistent data and is out of
  scope here: it needs the operator's decision, not a patch.

### Anchors such a patch would assert exactly once

Per the repo rule (idempotent, every anchor asserted once, syntax check on a copy of a live export),
"exactly once" means one rewrite site per anchor: the patch counts the anchor in the export first and
fails if the count differs from the verified one below. Counts come from
`grep -c -F '<anchor>' Hermes_Run_Manager_v5.json` on the repo copy.

| Anchor | Rewrite sites | Verified occurrences |
|---|---|---|
| `Start / Resume Run` | 1 node, name `:72`, URL `:33` | 6 (name, references inside Code nodes, the connection map) |
| `Get Run Status` | 1 node, name `:118`, URL `:95` | 4 |
| `Poll Wait` | 1 node, name `:417` | 4 |
| `Evaluate Status` | 1 node, name `:137`, its `jsCode` at `:128` | 6 |
| `Build Result` | 1 node, name `:431` | 4 |
| `Resume Existing Session` | 1 node, name `:225`, URL `:186` | 4 |
| `http://host.docker.internal:8642` | 4 HTTP nodes | 4 (`:33`, `:95`, `:186`, `:1189`) — a fifth occurrence means the copy is not the version the patch was written against |
| `X-Hermes-Session-Id` | 2 header entries | 2 (`:44`, `:197`) |
| `X-Hermes-Working-Directory` | 3 header entries | 3 (`:48`, `:201`, `:1200`) |
| `neverError: true` with `responseFormat: text` | every HTTP node must keep both | 5 (`:59`, `:105`, `:212`, `:1211`, `:1256`) |
| `runner_url` | 1 expression per rewritten node | 0 in the Hermes copy: the patch introduces it, asserting zero before and one per node after |
| `runner_api_key` | the auth node of each rewritten HTTP node | 0 in the Hermes copy — the gateway key travels as a Bearer credential |
| `poll_seconds` / `max_polls` | unchanged `Normalize Request` defaults (`:19`) | 4 each; the patch asserts the defaults are still present, not that they moved |

UNKNOWN and blocking: there is no fresh export of the Hermes run manager on the host, so the syntax
check of such a patch has to run against a copy of the live export the operator stages first
(`scratchpad/` exports, dated 2026-10-31); the repo copy `Hermes_Run_Manager_v5.json` is not proof of
what runs.

## Итог (миссия ahawr-hermes-engine, задачи T001A–T005C)

Отчёт на русском по плану; сам документ и весь код выше — на английском, как требует CLAUDE.md.

### Что сделано (пути файлов)

- **T001A–T001B — факты о текущих вызовах Hermes и mapping жизненного цикла.** Секции
  «Current Hermes calls» и «Runner lifecycle mapping» в этом файле: endpoints, auth, body
  start, poll, stop, session/continue, формы result и error — каждая строка с file:line
  (рекоп `Hermes_Run_Manager_v5.json`, staged-экспорты `scratchpad/`); все непроверенные
  факты помечены **UNKNOWN**.
- **T002A–T002B — конфигурация.** `ahawr-runner/src/ahawr_runner/config.py`: ключи движка (имена переменных, не значения)
  `HERMES_BASE_URL_VAR`, `HERMES_API_KEY_VAR` (имена переменных, не значения),
  `HERMES_START_TIMEOUT_MS`, `HERMES_POLL_INTERVAL_MS`, `HERMES_RUN_TIMEOUT_MS`,
  `HERMES_MAX_POLLS` (`config.py:188-193`, defaults `config.py:204-208`), поля профиля
  `hermes_*` (`config.py:262-269`). `ENGINE=hermes` принимается только с флагом: `_engine`
  (`config.py:329-335`); другие значения ENGINE по-прежнему дают `ValueError`. Флаг
  `CLAUDE_RUNNER_HERMES_ENGINE_ENABLED` OFF по умолчанию (`config.py:698`,
  `.env.example:204` — пустое значение).
- **T003A–T003C — движок.** `ahawr-runner/src/ahawr_runner/hermes_run.py`: `start_run`
  (`:94`), `poll_run` (`:149`) с bound-циклом `poll_count < profile.hermes_max_polls` и
  дедлайном (`:158`), `cancel_run` (`:173`), ошибка `HermesError` (`:34`); stdlib urllib,
  секретные значения в сообщениях не попадают. `hermes_events.py`: `route_for` (`:53`),
  `classify_failure` (`:80`), `result_entries` (`:105`), `continue_fields` (`:140`);
  общие помощники импортируются из `codex_events.py` (`:17`), не копируются.
  Подключение в `runs.py`: dispatch start (`runs.py:328-335`), execute (`runs.py:470-477`),
  путь движка `_execute_hermes` (`runs.py:659`), таблица запусков gateway `_hermes_runs`
  (`runs.py:215`), cancel-route для ENGINE=hermes (`runs.py:933-940`).
- **T004A — флаг.** Флаг OFF по умолчанию; с ним выключенным `ENGINE=hermes` rejected
  существующим сообщением и путь Claude Code не меняется ни на один символ — тест
  `test_flag_off_run_stays_on_the_claude_path_byte_for_byte`
  (`ahawr-runner/tests/test_hermes_run.py:394`). Патч Run Manager НЕ писался: только
  текстовое предложение в секции «Proposal: Run Manager change» выше.
- **T005A–T005C — офлайн-тесты.** `ahawr-runner/tests/fake_hermes.py`
  (`FakeHermesServer`, stdlib, `:80`); `test_hermes_run.py` — 8 тестов (нормальный run,
  follow-up со stored session, cancel, auth-401/403 без ключа в тексте, timeout по
  max_polls и дедлайну, unmapped-роут → fallback notice, rejection флага, byte-for-byte
  при OFF); `test_hermes_events.py` — 6 тестов маппинга записей и классов failures.

### Что НЕ проверялось вживую

- Реального вызова Hermes-гейтвера (порт 8642) не было: только fake-сервер из stdlib.
- Свежей выгрузки `Hermes Run Manager v5` на хосте нет (только рекоп в репозитории), поэтому
  синтаксис гипотетического патча не проверялся на копии live-экспорта.
- Docker/живая n8n недоступны Worker'у: значения `hermes_config` и активный
  `run_manager_workflow_id` не читались; включённый флаг в контейнере не запускался.

### Шаги оператора (выполняет оператор, не Worker)

1. Включить флаг в env runner-контейнера: `CLAUDE_RUNNER_HERMES_ENGINE_ENABLED=1`
   (имя в `.env.example:204`).
2. В блоке провайдера указать имена переменных: `CLAUDE_RUNNER_PROVIDER_<NAME>__ENGINE=hermes`,
   `HERMES_BASE_URL_VAR`, `HERMES_API_KEY_VAR`, при желании — `HERMES_START_TIMEOUT_MS`,
   `HERMES_POLL_INTERVAL_MS`, `HERMES_RUN_TIMEOUT_MS`, `HERMES_MAX_POLLS`
   (имена — `.env.example:206-207`, чтение — `config.py:188-193`). Значения ключей остаются
   в env, в JSON workflow не попадают.
3. Перезапустить сервис через `POST /v1/host-actions` на runner (белый список решает
   сервис/уровень), НЕ через `docker`; результат читать позже через
   `GET /v1/host-actions/<id>`.
4. (По желанию) применить предложение из «Proposal: Run Manager change»: менять URL HTTP-
   узлов run-менеджера на `runner_url`; перед этим оператор должен заранее выгрузить
   live-экспорт Hermes run manager — без него патч нельзя проверить синтаксис-проверкой.
5. Сменить `run_manager_workflow_id` — изменение постоянных данных, только решение оператора.

### Риски и UNKNOWN

- Stop-эндпоинт гейтвера UNKNOWN: `cancel_run` использует runner-путь
  `/v1/runs/{run_id}/cancel` с пометкой `shortcut` (`hermes_run.py:173-178`); апгрейд —
  когда live-экспорт назовёт путь.
- Формы ответов гейтвера UNKNOWN: session-ключи и статусы читаются с фолбэками
  (`hermes_run.py:122-128`, `poll_run` `:161`); апгрейд после live-проверки.
- `poll_run` берёт один timeout из профиля (`shortcut` в `hermes_run.py:78-79`).
- `route_for` не извлекает коды 4xx/5xx из текста ошибки (`shortcut` в `hermes_events.py:53-57`).
- `HERMES_SESSION_SOURCE` (`.env.example:201`) модулем движка не читается — только dashboard.

### Несовпадение RULES и ACCEPTANCE CRITERIA миссии

Боксы RULES и ACCEPT в миссии `ahawr-hermes-engine` созданы копированием из завершённой
миссии `ahawr-lessons-library` (`data-tables/ops/_make_hermes_engine_mission.py:10`), и
пункты ACCEPT относятся к функции «lessons»/roles (файлы `docs/config-roles-step1.md`,
`roles_options_patch.py`, ключи i18n) — НЕ к движку Hermes. Эта работа (lessons) в данной
миссии НЕ выполнялась: Worker делал только T001–T006 движка Hermes по OBJECTIVE.
Оператору следует подтвердить, что criteria про lessons относятся к другой миссии, а не
к этой.

### Ничего не коммитилось, не перезапускалось, не импортировалось, не отправлялось

`git status --short` показывает только uncommitted-файлы в рабочем дереве; commit, push,
stash не выполнялись; в n8n ничего не импортировалось; `POST /v1/host-actions` не вызывался.

### Проверки (что реально запускалось)

- `python -m pytest tests/test_hermes_run.py tests/test_hermes_events.py -q --no-cov` →
  `.............. (100%)` — 14 тестов (8 + 6) пройдено.
- `python -m ruff check src/ahawr_runner/hermes_run.py src/ahawr_runner/hermes_events.py
  src/ahawr_runner/config.py src/ahawr_runner/runs.py` → «All checks passed!».
- `python -m mypy src/ahawr_runner/hermes_run.py src/ahawr_runner/hermes_events.py` →
  «Success: no issues found in 2 source files».
- `git diff --check` → exit 0 (предупреждения только про CRLF в `docs/roadmap.*`, не
  изменённых этой работой).

Не запускались: node-тесты (JS не менялся), полный suite (не требуется по CLAUDE.md),
mypy/ruff остальных модулей.

### Отзыв ревьюера: «QA ui failed at 0/1: Locator.wait_for timeout waiting for get_by_text("Providers")»

- Сбой QA UI **не закрыт (unresolved, open)**: offline Worker'у причину проверить
  нельзя, воспроизвести её может только оператор на живом дашборде.
- Текст вкладки `Providers` в дашборде есть: `dashboard.html:340`
  (`<button id="tabprov" type="button">Providers</button>`), перевод —
  `dashboard.html:1212` (`SHELL_TEXT = { en: ... "Providers" ..., ru: ... "Провайдеры" ... }`).
- Locator QA ищет именно видимый текст: `qa_runner.py:700`
  (`page.get_by_text(text, exact=True).locator(VISIBLE_ONLY).first`) и
  `qa_runner.py:705` (`wait_for` на видимом элементе).
- Утверждать, что сбой не связан с рабочим деревом, нельзя: `ahawr-runner/src/ahawr_runner/
  dashboard.html` имеет uncommitted-изменения другой работы (`git status --short`:
  ` M ahawr-runner/src/ahawr_runner/dashboard.html`), и Worker не может проверить, при
  каком состоянии dashборда падал QA.
- Оператору: запустить QA UI на живом дашборде (`127.0.0.1:8701`) и зафиксировать
  воспроизведение; до воспроизведения вопрос остаётся открытым.

### Предложение версии (только предложение, ничего не изменено)

Факты: последний коммит — `0.12.020` (`git log --oneline -1`: `721a315 0.12.020: ...`);
правило в `CHANGELOG.md:6` («new functions raise the middle number by 1»); `pyproject.toml:7`
в рабочем дереве уже читает `version = "1.0.0"` — это uncommitted-изменения другой работы.
Согласованное предложение одно: версия стека **`0.13.0`** («Hermes как третий движок runner
за флагом») по правилу CHANGELOG.md:6 (`0.12.020` → `0.13.0`). Тогда `pyproject.toml`
перед релизом нужно вернуть к стековому числу `0.13.0`: стоящая в дереве `1.0.0` —
не эта миссия, а чужые uncommitted-правки, и релиз без возврата к числу стека будет
несогласован. Вариант «оставить 1.0.0 и записать её в CHANGELOG» правилу CHANGELOG.md:6
не соответствует, поэтому не предлагается. Никаких изменений в CHANGELOG.md, pyproject.toml
или api.py не делалось.
