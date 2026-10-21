# Guards notes

## Runner process lifecycle

The run lifecycle lives in `claude-runner/src/claude_runner/runs.py` (`RunManager`)
and `claude-runner/src/claude_runner/store.py` (`Store`). Every terminal status is
persisted by `RunManager._finish` (runs.py:449-506) via `Store.update_run`
(store.py:135-142); `Store.interrupt_active_runs` (store.py:325-333) is the only
status write outside `_finish`.

Terminal-status table. `_procs` holds the live CLI process per run id
(runs.py:112); the `finally` of `_execute` removes the entry at runs.py:420
before `_finish` runs, so every run that reaches `_finish after process creation
has already been removed from `_procs`.

| Terminal status | function:line | process terminated | removed from `_procs` |
|---|---|---|---|
| `completed` | `classify` claude_cli.py:407-410, saved by `_finish` runs.py:437 | yes — the CLI exits on its own, `proc.wait()` finishes inside `pumps` (runs.py:408) | yes — runs.py:420 |
| `failed` (CLI stopped: `max_turns`, `max_budget`, `session_not_found`, `rate_limit`, `overloaded`, `server_error`, `api_error`, `execution_error`) | `classify` claude_cli.py:412-465, saved by `_finish` runs.py:437 | yes — the CLI exited on its own | yes — runs.py:420 |
| `failed` with `error_code="timeout"` | timeout branch runs.py:411-415, `classify` claude_cli.py:404-406 (status `failed`, http_code 408), `_finish` runs.py:437 | yes — `self._stop(proc)` runs.py:413, escalation SIGINT→SIGTERM/SIGKILL (runs.py:508-523) | yes — runs.py:420 |
| `failed` with `error_code="cli_error"` (stream pump failed) | `self._stop(proc)` runs.py:418, error override runs.py:430-432, `_finish` runs.py:437 | yes — `self._stop(proc)` runs.py:418 | yes — runs.py:420 |
| `failed` with `error_code="cli_unavailable"` (CLI could not start) | `_execute` runs.py:376-384 (`OSError` on `create_subprocess_exec`), `_finish` runs.py:383 | no — no process was ever started | n/a — `_procs[run_id]` is set only at runs.py:385, after the early return |
| `cancelled` (cancelled while still queued) | `_execute` early branch runs.py:299-307 | no — no process was started | n/a |
| `cancelled` (cancelled while running) | `cancel()` runs.py:534-549 → `self._stop(proc)` runs.py:542; `classify` runs.py:422-429 / claude_cli.py:402-403; `_finish` runs.py:437 | yes — `self._stop(proc)` runs.py:542 | yes — runs.py:420 |
| `interrupted` (runner restart) | `Store.interrupt_active_runs` store.py:325-333, called from `RunManager.startup` runs.py:118-121 | no — it only rewrites the DB rows; the old runner's CLI processes have no handle from the new instance and are not touched | n/a — the rows belong to the previous manager instance, not to the new `_procs` |
| `interrupted` (runner shutdown) | `_finish` runs.py:451-456 (converts `cancelled` to `interrupted` when `_shutting_down`) | yes — the shutdown path cancels each run via `cancel()` → `_stop` (runs.py:126-128, 542) | yes — runs.py:420 |

Note on `timeout`: the runner never stores the status string `timeout`; a timeout
is stored as status `failed` with `error_code="timeout"` (claude_cli.py:406).
`remote_manager.py:22` (`FINISHED`) and `board_stats.py:484-485` treat `timeout`
as a distinct kind by reading that `error_code`, not a stored status.

### `interrupt_active_runs`: definition, call sites, and reach paths

- Definition: `store.py:325-333` — `UPDATE runs SET status = 'interrupted',
  error_code = 'run_not_found', error_message = ?, finished_at = ? WHERE status
  IN ('queued', 'running')` (the `ACTIVE` tuple, store.py:18).
- Single call site: `runs.py:119-121`, inside `RunManager.startup`, with the reason
  text `"Run was interrupted by a claude-runner restart; resume the session to
  continue."` (the "claude-runner restart" error text, runs.py:120).
- `startup` is invoked once per app start: `api.py:92`
  (`app.state.interrupted = await manager.startup()` inside the FastAPI
  lifespan); the count is exposed at `api.py:195`
  (`"interrupted_on_start": request.app.state.interrupted`).
- The shutdown variant of the text — `"Run was interrupted by a claude-runner
  shutdown; resume the session to continue."` — is set by `_finish` at
  runs.py:455 and does not go through `interrupt_active_runs`.

Every way `interrupt_active_runs` can be reached:

1. **Startup of a new runner on the same data directory.** Every restart of the
   runner process (crash + start, redeploy, uvicorn reload) runs the lifespan,
   which calls `startup` (api.py:92) and marks every `queued`/`running` row of
   the shared `runner.sqlite` (runs.py:102) as interrupted.
2. **A second RunManager/Store on the same data directory.** `RunManager.__init__`
   defaults to `Store(settings.data_dir / "runner.sqlite")` (runs.py:102), and
   `startup` has no check that the active runs belong to a live process — it
   matches on status only (store.py:329-332). Two runner instances (or a test
   suite) sharing `CLAUDE_RUNNER_DATA_DIR` can interrupt each other's active
   runs; the second instance's `startup` marks the first instance's still-running
   CLI processes' runs as interrupted.
3. **A reload that starts the new process before the old one dies.** If the old
   runner is still serving when the new process boots, the new process's
   `startup` interrupts the old instance's active runs while its CLI processes
   keep running (see hypothesis 1 below).
4. **A shutdown path that does not wait for the pump.** `shutdown` (runs.py:123-132)
   cancels every task and waits at most `interrupt_grace_seconds + 5` seconds
   (runs.py:131, exceptions suppressed). **Fixed:** the `finally` of `_execute`
   (runs.py) now runs `_terminate_proc` on every exit path, so a task cancelled
   mid-pump terminates the process tree and reaches `_finish`, which closes the
   row as `interrupted` before shutdown completes. The row can no longer be left
   `running` with its process alive to be rewritten by the next `startup` or a
   second manager.

### Hypotheses: a run marked `interrupted` while its process lives

1. **Restart/reload of the runner while the previous instance is still alive.**
   Evidence: `startup` runs `interrupt_active_runs` (runs.py:118-121) on every
   app start (api.py:92); the SQL update matches only on status (store.py:329-332)
   and never touches processes, so the previous runner's CLI children are left
   running while their run rows read `interrupted`.
2. **Two managers/stores on one data directory.** Evidence: the default store
   path is shared by every `RunManager` in the same settings (runs.py:102);
   `startup` marks all `ACTIVE` rows of that file without an owner check
   (runs.py:119-121, store.py:325-333), so a second instance (or a test suite
   opening the same `runner.sqlite`) marks the first instance's live runs as
   interrupted.
3. **Shutdown that did not wait for the pump.** Evidence: `shutdown` waits only
   `interrupt_grace_seconds + 5` seconds per task and suppresses the result
   (runs.py:130-131); a task cancelled mid-pump (runs.py:408-415) leaves the run
   row `running` and the process alive; the next `startup` then rewrites that row
   to `interrupted` (store.py:329-332) without killing the process.
4. **A run cancelled during shutdown is marked `interrupted` by `_finish`**
   (runs.py:451-456) rather than by `interrupt_active_runs`; here the process
   *is* terminated via `cancel` → `_stop` (runs.py:126-128, 542), so this is an
   `interrupted` path, but not a process-still-alive hypothesis.

### Root cause findings

Fixes live in `tests/test_interrupt_root_cause.py` (all pass). Each hypothesis
from the section above is checked against the fixed code; the test for each
reproduced path now proves the fix, and hypothesis 4 keeps its ruled-out
verdict.

The owner check is token-based: a run row stores `owner_pid` as
`f"{pid}:{boot_id}"` (boot_id is a uuid4 per `RunManager` instance, runs.py).
`interrupt_active_runs` (store.py) skips a row only when its token is in the
module-level registry `_LIVE_OWNER_TOKENS` (store.py); a `RunManager` adds its
token on `startup` and removes it on `shutdown`. A token from a dead manager, or a
token from a *different* manager in the same process (same pid, different boot
id), is not live and is interrupted. Old databases get the `owner_pid` column
via the migration loop in `Store.__init__` (store.py).

| Hypothesis (T001) | Reproduced | Fix applied | Evidence |
|---|---|---|---|
| 1. Restart/reload of the runner while the previous instance is still alive | **Yes** (before the fix) — a second app start on the shared data dir flipped the live run's row to `interrupted` while its `claude -p` process stayed alive. | Token-based owner check: a run row stores `owner_pid` as `f"{pid}:{boot_id}"`; `interrupt_active_runs` skips a row only when its token is in `_LIVE_OWNER_TOKENS` (a live manager). The second app's manager has a different token, so the first app's live row is not skipped and is not interrupted. | `test_restart_while_old_instance_alive_does_not_mark_running_row` (passes): the second app's `interrupted_on_start` is 0 and the live run keeps its `running` row while its CLI process is alive. |
| 2. Two managers/stores on one data directory | **Yes** (before the fix) — a second `RunManager(settings)` on the same `runner.sqlite` interrupted the first instance's active rows without an owner check and left the first instance's process untouched. | Same token-based check: each `RunManager` registers its own token on `startup` and removes it on `shutdown`. A second manager in the same process (same pid, different boot id) therefore does not skip the first manager's rows — and vice versa. | `test_second_manager_on_shared_db_does_not_mark_running_row` (passes): the second manager's `startup()` returns 0 and the live run keeps its `running` row. |
| 3. Shutdown that did not wait for the pump | **Yes (fixed)** — the race (a `_execute` task cancelled mid-pump before `_finish`) is now closed: every exit path of `_execute` runs `_terminate_proc` (the `finally` at runs.py) and `_finish` closes the row as `interrupted` before shutdown completes. | `shutdown` (runs.py) awaits each task; a task cancelled mid-pump now terminates the process tree and reaches `_finish`, so the row is closed as `interrupted` before shutdown ends. `shutdown` also removes the manager's token from `_LIVE_OWNER_TOKENS` before closing the store. No `xfail` remains. | `test_shutdown_closes_run_before_process_is_terminated` (passes): the row is `interrupted` and the CLI process is not left alive. |
| 4. A run cancelled during shutdown is marked `interrupted` by `_finish` | **Ruled out** (not a process-still-alive path) — the row is `interrupted` but the CLI process is terminated, so no live process is left. | None needed for this path; the process-tree termination already covers it (Part 2, docs/guards.md "RUNNER: terminating the process tree when a run is closed"). | `test_shutdown_cancelled_run_is_interrupted_but_process_is_terminated` (passes): the row is `interrupted` and the CLI process is terminated. |

### All `interrupted` / restart-text occurrences in `src/claude_runner`

- store.py:329 — the SQL that sets `status = 'interrupted'` (in `interrupt_active_runs`, 325-333)
- store.py:325 — `interrupt_active_runs` definition
- runs.py:119 — the only call of `interrupt_active_runs`
- runs.py:120 — the "claude-runner restart" error text
- runs.py:167 — `public` maps stored status `interrupted` to reported `failed`
- runs.py:452 — `_finish` sets `outcome.status = "interrupted"` on shutdown
- runs.py:455 — the "claude-runner shutdown" error text
- runs.py:529 — `get` raises `run_not_found` (404) for an `interrupted` run
- api.py:92 — `manager.startup()` in the app lifespan
- api.py:195 — `"interrupted_on_start"` in the run response

## API and dashboard

### The runner app: `create_app` (api.py:82)

The runner's FastAPI app is built by `create_app(settings)` (api.py:82-293).
The app object is created at api.py:138 (`FastAPI(title="claude-runner",
version="0.6.100", lifespan=lifespan)`). `lifespan` (api.py:85-136) creates the
`RunManager` and stores it as `app.state.manager` (api.py:87-88), attaches the
board routes via `add_board_routes(app, settings)` (api.py:91, after the
manager exists), runs `manager.startup()` (api.py:92), starts the
`ThinkSwitch` task (api.py:117) and the `MemGuard` task (api.py:128) when
their settings are on, and in the `finally` block cancels both tasks and awaits
`manager.shutdown()` (api.py:132-136). The `finally` block of `lifespan` is
therefore where a background supervisor started during startup can be stopped
(same pattern as `switch_task`/`guard_task`: `task.cancel()` then
`await manager.shutdown()`, api.py:132-136).

Auth dependency: `authorize` (api.py:140-145) reads the `Authorization` header,
strips `Bearer `, and compares it to `settings.api_key` with
`hmac.compare_digest`; with no `api_key` set it allows everything; otherwise a
missing/invalid token raises `RunnerError("unauthorized", ..., 401)`. Routes use
it as `dependencies=[Depends(authorize)]`:

- `POST /v1/runs` — api.py:198
- `GET /v1/sessions/{session_id}` — api.py:234
- `GET /v1/sessions/{session_id}/digest` — api.py:243
- `GET /v1/search` — api.py:253
- `POST /v1/sessions/{session_id}/compact` — api.py:286

and the read-only run list/activity router is attached with the same
dependency at api.py:222-224 (`build_router(..., prefix="/v1",
dependencies=[Depends(authorize)])` from `run_views.build_router`).
`GET /v1/runs/{run_id}` (api.py:226) and `POST /v1/runs/{run_id}/cancel`
(api.py:230) use `Depends(authorize)` directly; `GET /health` (api.py:165)
is open. `RunnerError` responses are shaped by the exception handler at
api.py:153-163.

### The dashboard app: `create_dashboard_app` (dashboard.py:64)

The dashboard is a separate FastAPI process, created by
`create_dashboard_app(settings)` (dashboard.py:64-337); the app object is at
dashboard.py:91-93 and served via `uvicorn.run(create_dashboard_app(), ...)`
(dashboard.py:406). Its `lifespan` (dashboard.py:69-89) is an
`asynccontextmanager` holding an `httpx.AsyncClient` for the whole app
lifetime and storing `app.state.runner` (`RunnerSource`, dashboard.py:74-78)
and `app.state.hermes` (`HermesSource`, dashboard.py:79-88). A background
supervisor can be started and stopped exactly in this `lifespan`: start it
before the `yield` and cancel/close it after the `yield` (the
`asynccontextmanager` body around dashboard.py:71-89, matching the
`switch_task`/`guard_task` pattern in api.py:132-136).

Dashboard auth is not bearer-token based: it serves only `DASHBOARD_HOSTS`
(`TrustedHostMiddleware`, dashboard.py:94), and its mutation routes require
the `X-AHAWR-Dashboard: 1` header — enforced by the `board_header_guard`
middleware for `POST/PUT/DELETE` under `/api/board/` (dashboard.py:99-109)
and directly in `cancel_run` (dashboard.py:288-289).

Its board backend: `board_api.add_board_routes(app, settings)` is called at
dashboard.py:110 (the same function the runner's lifespan calls at api.py:91).
The store is opened there: `add_board_routes` keeps a pre-set
`app.state.board_store` (test in-memory store) or otherwise calls
`get_board_store()` (board_api.py:219-224, board.py:1223-1231,
board_pg.py:1476-1499) — a `PgBoardStore` when `DASHBOARD_BOARD_PG_DSN`
(board_pg.py:40) is set and connectable, otherwise an
`UnavailableBoardStore` whose `status()` answers 503 without leaking the DSN.
The runner manager the board stats need is a remote stand-in:
`app.state.manager = RemoteRunManager(settings.runner_url,
settings.runner_api_key)` (dashboard.py:111-114, remote_manager.py:130-146).
The HTML page `dashboard.html` is served at `/` (dashboard.py:95, 119-121) and
the board's static files at `/static/{name}` (dashboard.py:133-145).

Existing `/api` routes of the dashboard:

- `GET /api/runs` — dashboard.py:151
- `GET /api/search` — dashboard.py:240
- `GET /api/runs/{run_id}/events` — dashboard.py:282
- `POST /api/runs/{run_id}/cancel` — dashboard.py:286 (the existing cancel
  call; header-guarded, 403 without the header, 400 for Hermes runs)
- `GET /api/runs/{run_id}/export` — dashboard.py:305
- `GET /api/sessions/{session_id}/export` — dashboard.py:312
- plus `GET /healthz` (dashboard.py:147) and the whole `/api/board`
  router of `board_api._build_router()` (board_api.py:388).

### remote_manager.py: client methods for runs, events and cancel

`RemoteRunManager` (remote_manager.py:130-146) is a read-only stand-in for
`RunManager`; it exposes `store` (`_RemoteStore`), `events` (`_RemoteEvents`)
and a no-op `local_pricing` (`_NoPricing`, remote_manager.py:143).
Base URL and auth settings: `self.url = url.rstrip("/")` and
`self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}`
(remote_manager.py:136-137), with one `httpx.Client(timeout=20.0)`
(remote_manager.py:138).

Methods (exact names):

- `list_index()` (remote_manager.py:148-155) — `GET /v1/runs?limit=500` kept
  `LIST_TTL` = 15 s (remote_manager.py:24)
- `get_json(path, params=None)` (remote_manager.py:157-168) — the shared HTTP
  GET helper (non-200 or non-dict bodies come back as `None`)
- `_RemoteStore.get_run(run_id)` (remote_manager.py:65-75) —
  `GET /v1/runs/{run_id}`
- `_RemoteStore.list_runs(limit, session_id=None)` (remote_manager.py:90-95) —
  `GET /v1/runs`
- `_RemoteEvents.read(run_id, after=0)` (remote_manager.py:104-127) —
  `GET /v1/runs/{run_id}/events?after=...`, up to 200 pages

There is **no cancel method** in `remote_manager.py`: it is strictly read-only
(the docstring, remote_manager.py:1, says so). The cancel call lives in
`RunnerSource.cancel(run_id)` (sources.py:104-112, `POST /v1/runs/{run_id}/cancel`)
and is invoked by the dashboard's `POST /api/runs/{run_id}/cancel` route
(dashboard.py:286-303), which the page triggers from `stopRun`
(dashboard.html:660).

### i18n in dashboard.html

There is **no i18n mechanism in `dashboard.html` itself**: no translation
map, no `translate()` helper, no locale switching; the file is
`<html lang="en">` (dashboard.html:2) and its UI text is hardcoded English or
Russian literals (e.g. the tab labels `Прогоны`/`Миссии`,
dashboard.html:242, and `Confirm`-style strings such as
dashboard.html:657). The i18n mechanism exists only in the board: keys live in
`board_i18n_en.json` / `board_i18n_ru.json`, are seeded into the board store
by `_ensure_i18n_seed` (board_api.py:100-107) and exposed/edited through the
`/api/board/i18n` routes (board_api.py:1246-1277). If `dashboard.html` needs
translated text, it must fetch them from `GET /api/board/i18n` itself;
there is no existing client-side key table to extend.

A status strip in `dashboard.html` fits the run head area: `renderHead`
(dashboard.html:774-805) renders `<section id="head">` with the `.meta` line
(dashboard.html:784) and the `.stats` line (dashboard.html:791-803); a
status strip could be added between them (the `.stats` block ends at
dashboard.html:803, the error line `#errline`/`.errline` at dashboard.html:804),
or as a separate strip between `#head` and `#overview` in the `#runview`
template (dashboard.html:628-633, CSS `.stats` at dashboard.html:118-120).

### How the board reads mission/live-state data

The board routes read everything through `app.state.board_store`
(`BoardStore`, set by `add_board_routes`, board_api.py:219-224; the 503 guard
is `_store_guard`, board_api.py:78-85). Mission and live-state reads use:

- `store.list_missions()` / `store.get_mission(id)` (board.py:168-174, Pg
  impl board_pg.py:634-640)
- `store.get_live_state(state_key)` (board.py:180, Pg board_pg.py:450) — the
  live-state row (status, position, plan)
- `store.list_live_states()` (board.py:176)
- `store.task_views(state_key)` (board.py:260)
- `store.events(state_key)` (board.py:184) — the mission's event log
- `store.mission_groups()` (board.py:229)
- `store.mission_config(id)` / `store.task_overrides(id)` (board.py:196-204)
- `store.running_missions()` (board.py:220)

The runner-side run data the stats need comes through the manager
(`request.app.state.manager`, set in the runner lifespan api.py:88 or the
remote `RemoteRunManager` in the dashboard, dashboard.py:114):
`board_stats.mission_card_stats` (board_api.py:1131),
`board_stats.task_stats` (board_api.py:1144), `board_stats.mission_timeline`
(board_api.py:1152) and `board_stats.cached_mission_figures`
(board_api.py:1175) read runs via `manager.store.get_run/list_runs`,
`manager.events.read` and `manager.local_pricing` — locally in the runner,
over HTTP in the dashboard (remote_manager.py:1-10).

The board's *mission/live-state* data itself is never read from the runner:
it is the board store (Postgres via `DASHBOARD_BOARD_PG_DSN`, or the
in-memory `InMemoryBoardStore` in tests; without a DSN the routes answer 503).

## Host scripts: orphan_guard, notify

### `orphan_guard.py` (173 lines, read-only study)

**What it checks.** Every `CHECK_SECONDS` (default 30 s, env
`ORPHAN_CHECK_SECONDS`): it reads the `claude -p` CLI processes in the
`claude-runner` container (`docker exec claude-runner ps`) and the runs from
the runner dashboard API (`http://localhost:8701/api/runs?limit=100`, header
`X-AHAWR-Dashboard: 1`). A process is an ORPHAN when no run with status
`running`/`queued` carries its session id (`session_id` or
`claude_session_id`). It also counts runs closed as `interrupted` less than
`QUICK_END_SECONDS` (120 s) after start.

**Its action.** A process seen as an orphan in two consecutive checks and
older than `MIN_AGE_SECONDS` (default 120 s, env `ORPHAN_MIN_AGE`) is
killed: `kill`, then `kill -9` 10 s later if still alive. At most one
Telegram notice per `NOTICE_GAP` (600 s); the rest go to the log only. Three
`interrupted` runs within 15 minutes of their start raise one "interrupt
loop" notice (no automatic action). The guard does nothing when docker or
the API do not answer, and never touches a process younger than
`MIN_AGE_SECONDS`.

**Notice text.**
- Kill: `"Страж раннера: остановлен процесс claude-сирота (pid %d, возраст %d мин, сессия %s). Его запуск уже был закрыт, процесс занимал слот llama."`
- Interrupt loop: `"Страж раннера: %d запуска подряд закрыты как interrupted меньше чем через %d секунд после старта (за 15 минут). Похоже на петлю, нужно смотреть причину."`

**Dependencies.** Docker (the `claude-runner` container must be answerable),
the runner dashboard HTTP API (Postgres is not used directly — the run list
comes from the API), `notify.py` (subprocess, for the Telegram notices),
`orphan_guard.log` (append log in the same folder). No Postgres dependency
in this script.

**Functions.**

| function | line | what it does |
|---|---|---|
| `log(msg)` | 40 | appends a timestamped line to `orphan_guard.log` and prints it |
| `notify(text)` | 47 | runs `notify.py text` as a subprocess (timeout 60 s); a failed notice is logged, not fatal |
| `etime_seconds(text)` | 54 | parses `ps` etime `[[dd-]hh:]mm:ss` to seconds |
| `processes()` | 66 | `docker exec claude-runner ps -eo pid,etime,args`; returns `[(pid, age_s, session_id)]` of `claude -p` processes, or `None` when docker does not answer |
| `runs()` | 84 | `GET http://localhost:8701/api/runs?limit=100` (header `X-AHAWR-Dashboard: 1`); returns the `runs` list or `None` |
| `kill(pid)` | 94 | `docker exec claude-runner kill pid`; after 10 s checks `kill -0` and, if alive, `kill -9`; returns `"kill"` or `"kill -9"` |
| `parse_ts(text)` | 105 | parses an ISO timestamp to epoch seconds (`calendar.timegm`), or `None` |
| `main()` | 112 | the loop: every `CHECK_SECONDS` reads processes and runs, classifies orphans, kills confirmed ones (with the 10-min notice cap), counts quick `interrupted` ends, and sleeps. `--once` does one check, kills nothing, prints what it would do |

### `notify.py` (85 lines, read-only study)

**What it checks.** Nothing periodic — it is called on demand. It builds the
notice text, prepends a mission tag, resolves the chat id, and POSTs to the
n8n webhook.

**Its action.** Sends one Telegram message through the n8n webhook
`"AHAWR Queue Alert"` (path `AHAWR_ALERT_WEBHOOK_PATH` from `.env`, the bot
token stays in the n8n `telegramApi` credential). The webhook splits the
text into up to 5 messages of 4000 characters each. Returns `True` on HTTP
200, `False` otherwise. Never raises.

**Notice text.** The text passed by the caller (e.g. the orphan-guard
notices above), optionally prefixed with the mission tag
`"📌 <state_key> · Txxx"` (or `"📌 <mission>"` from `real_queue.log` as
fallback).

**Dependencies.** Docker (only for the Postgres queries, via `pgdb.py`),
Postgres (`ahawr-postgres`, for the mission tag and the chat id), `pgdb.py`
(`q`, `T`), `real_queue.log` (fallback for the mission tag when the database
is unavailable), `AHAWR_ALERT_WEBHOOK_PATH` in `.env`, and the n8n webhook
`http://localhost:5678/webhook/<path>`.

**Functions.**

| function | line | what it does |
|---|---|---|
| `_env(name)` | 17 | reads a value from the repo-root `.env` file (the line starting `name=`), or `""` |
| `mission_tag()` | 27 | returns `"📌 <state_key> · Txxx"` of the most recently updated `active` live-state row (Postgres via `pgdb`, table `data_table_user_Yn9lOhVhQ9gFymNs`); fallback: the last `"started <mission> (run N"` line of `real_queue.log`; `""` when unknown |
| `send(text, chat_id=None)` | 55 | prepends the mission tag (unless already in the text), reads `AHAWR_ALERT_WEBHOOK_PATH` from `.env`, resolves `chat_id` from the hermes profile row (`id = 5`, column `telegram_chat_id`, table `data_table_user_1S21PsMqT0FpR7eD`) when not passed, POSTs `{"chat_id", "text"}` to the n8n webhook, returns `True` on 200 / `False` otherwise |
| `__main__` | 77 | CLI entry: `python notify.py "text"` (or `@path` to read UTF-8 text from a file, then delete the file); prints `sent` / `NOT sent`, exit 0/1 |

### Telegram variables used by `notify.py` (names only, no values)

- `AHAWR_ALERT_WEBHOOK_PATH` — the webhook path, read from the repo-root
  `.env` by `_env()` (line 17). This is the only secret-bearing variable the
  script touches; the bot token itself lives in the n8n `telegramApi`
  credential and is never read or printed by `notify.py`.
- `telegram_chat_id` — the column name in the hermes profile table
  (`data_table_user_1S21PsMqT0FpR7eD`, row `id = 5`), read by `send()` (line
  65). The header of `hermes_config.csv` lists this column name.
- `chat_id` — the local variable in `send()` (line 64) holding the resolved
  chat id before the POST body is built.

No token value, no chat-id value, and no webhook-path value appears in the
notes above; only the variable and column names are recorded.

## Host scripts: queue_watch

### `queue_watch.py` (266 lines, read-only study)

Host watcher, started detached as `python queue_watch.py`, runs forever.

**What it checks.** Every 60 s (`time.sleep(60)`, line 262):
- new lines of `real_queue.log` (`new_lines`/`read_from`, offsets kept in
  `queue_watch.state.json` key `q`): `MISSION NOT COMPLETED` (line 204),
  `ended <mission>: completed` (line 207), `REAL QUEUE STOPPED` (line 210),
  `ALERT:` (line 213), `=== real queue done` (line 215);
- new lines of `docker_watchdog.log` (offset key `d`): `restarting Docker
  Desktop` (line 219), `containers are back` (line 221), `not restarting
  again` (line 223);
- the queue bash process (`queue_alive()`, line 122 — a PowerShell
  `Get-CimInstance Win32_Process` count of `bash.exe` whose command line
  matches `real_queue.sh`), n8n executions (`executions_running()`, line
  129 — Postgres join of `execution_entity`/`workflow_entity` for workflows
  `like 'AHAWR v13%'` with status `running`/`new`/`waiting`) and the runner
  (`runner_running()`, line 138 — `docker exec claude-runner` curl to
  `localhost:8700/health`, Bearer `CLAUDE_RUNNER_API_KEY`; counts
  `running` + `queued`);
- the tg relay process and the think archive (lines 255, 259, run every
  tick).

**Notices (all via `notify()` → `notify.py send`, capped at 20/hour, lines
42-52; unsent ones go to `pending` for up to 6 h, retried every minute,
lines 55-66, prefix `(с опозданием, событие в HH:MM)`):**
- `❌ Миссия <mission> не завершена (состояние <state>)` — mission not
  completed, plus the last review appended by `last_review()` (line 100):
  the newest `review_parsed` event of the mission's `EVENTS` table
  (`data_table_user_ceJ7PFzVJvPN0LsV`) — task index, attempt, score, first
  350 chars of the reason (line 206);
- `✅ Миссия <mission> завершена.` — `ended <mission>: completed` (line 209);
- `⛔ Очередь миссий остановилась: <reason>. Сторож запустит её заново,
  когда n8n освободится.` — `REAL QUEUE STOPPED` line without `ALERT`
  (line 212); sets `queue_end = "stopped"`;
- `⚠️ <alert text>` — `ALERT:` line, skipped when the line carries
  `DISABLE_THINKING`, `new missions were not inserted`, or `REAL QUEUE
  STOPPED` (line 214);
- `🏁 Очередь миссий закончилась: все миссии пройдены или пропущены.` —
  `=== real queue done` (line 217); sets `queue_end = "done"`;
- `🐳 Docker завис, сторож перезапускает Docker Desktop.` / `🐳 Docker
  вернулся, контейнеры подняты. Очередь продолжит сама.` / `🐳 Docker
  зависает слишком часто (3 перезапуска в час), сторож больше не
  перезапускает его. Нужно вмешательство.` — the three new
  `docker_watchdog.log` markers (lines 220, 222, 224);
- `🔁 Очередь миссий запущена заново (после остановки | её процесса не
  было).` — queue restarted (line 238);
- `💤 Ничего не выполняется уже 20 минут, хотя очередь жива. Проверьте
  прогон.` — idle notice (line 247).

**The restart action (lines 227-241).** When `queue_alive()` is False and
`queue_end != "done"`, the queue is started again via `start_queue()`
(line 182 — PowerShell `Win32_Process Create` running
`bash -c "... NOWAIT=1 /usr/bin/bash real_queue.sh >> real_queue.out 2>&1"`
in `/d/n8n/data-tables/ops`), only when `executions_running() == 0` and
fewer than 4 restarts in the last hour (`restarts` list pruned at line 233);
with 4+ restarts in an hour it logs `queue is down, 4 restarts in the last
hour: not starting it again` (line 241) and does not start.

**The idle-notice condition (quoted).** Lines 243-251, inside the 60 s loop:

```python
            # nothing runs for 20 minutes while the queue is alive
            if alive and executions_running() == 0 and runner_running() == 0:
                idle_since = idle_since or time.time()
                if time.time() - idle_since > 1200 and not idle_notified:
                    notify("💤 Ничего не выполняется уже 20 минут, хотя очередь жива. Проверьте прогон.")
                    idle_notified = True
            else:
                idle_since = None
                idle_notified = False
```

**The queued-only gap (data-tables/ops/queue_watch.py:244-246).**
`runner_running()` counts runs in *both* states — `running` **and**
`queued` (line 144: `int(r.get("running", 0)) + int(r.get("queued", 0))`).
So when missions are only queued in the runner (nothing running yet),
`runner_running()` is not 0 and the idle branch is never entered:
`idle_since` and `idle_notified` are reset to `None`/`False` every minute
(lines 250-251) and the 20-minute timer never starts. The idle notice fires
only when there is no runner run in any state and no n8n execution; a
queue whose missions are all stuck in the runner queue looks "alive and
busy" and produces no idle notice, even though nothing is being executed.

**Think archive.** `archive_think_steps()` (lines 161-167) runs at most once
every 6 h (module-level `LAST_ARCHIVE` timestamp, line 158; the first call
fires immediately on watcher start) and executes `think_archive.py`
(line 166) as a subprocess of the same interpreter, timeout 900 s; its
stdout/stderr tail (160 chars) is written to `queue_watch.log` (line 167).
`think_archive.py` (53 lines) itself: `archive()` (lines 15-35) pipes
`think_model_extract.py` into `docker exec -i claude-runner python -`
(timeout 900 s) and appends the not-yet-archived runs to
`think_steps_archive.jsonl` in the same `ops` folder, one JSON line per run
(a still-running run is rewritten once it has a `finished_at`, line 31);
`load()` (lines 38-48) reads the last record per run id. Source: the
`runs` table and `events/*.jsonl` inside the `claude-runner` container
(`think_model_extract.py` reads `/data/runner.sqlite` and
`/data/events/<run_id>.jsonl` for local Worker runs, lines 9-13);
destination: `think_steps_archive.jsonl` (local file). Trigger: every 6 h
from `queue_watch.py`. **Dependencies: the host plus Docker** — it needs
`docker exec` into the `claude-runner` container (and a file mount for
the archive), not Postgres and not HTTP.

**How it starts other watchers.** `ensure_relay()` (lines 170-179):
counts `python.exe` processes whose command line matches `tg_relay.py`
(pattern built from two pieces so the check does not match itself); if zero
and `tg_relay.enabled` exists, starts `D:\Python312\python.exe
<ops>\tg_relay.py` via PowerShell `Win32_Process Create` and logs
`tg_relay started again`. The queue itself is started by `start_queue()`
as described above. `slot_loop_running()` (lines 149-155) checks for a
`slot_cache.py loop` process; in this file it is defined but never called
(the slot save/restore lives in `slot_keeper.py` in the
llama-cpp-dashboard container, comment at line 183).

**Data sources.** `real_queue.log` and `docker_watchdog.log` (offsets in
`queue_watch.state.json`, keys `q`/`d`), Postgres tables
`data_table_user_Yn9lOhVhQ9gFymNs` (`STATES`) and
`data_table_user_ceJ7PFzVJvPN0LsV` (`EVENTS`) via `pgdb.T`/`pgdb.q`,
Postgres `execution_entity`/`workflow_entity` (n8n database), the runner
health endpoint `localhost:8700/health` (Bearer `CLAUDE_RUNNER_API_KEY`),
and `notify.py` for the Telegram notices.

**Functions.**

| function | line | what it does |
|---|---|---|
| `log(m)` | 37 | appends a timestamped line to `queue_watch.log` |
| `notify(text)` | 42 | sends via `notify.send`; max 20 notices per hour (older ones pruned, line 44); a failed send is kept in `pending` (max 10) |
| `retry_pending()` | 55 | resends `pending` notices with the late-event prefix; drops them after 6 h; stops on the first failure (channel still down) |
| `load_state()` | 69 | reads `queue_watch.state.json` (empty dict on error) |
| `save_state(s)` | 76 | writes `queue_watch.state.json` |
| `new_lines(path, key, state)` | 80 | returns new log lines from the stored offset; first run starts at the end, a truncated file is re-read from the start |
| `read_from(path, pos, state, key)` | 92 | reads from `pos` to the current end and updates the offset |
| `last_review(mission)` | 100 | newest `review_parsed` event of the mission from `EVENTS`; returns a one-line review text (task, attempt, score, reason truncated to 350 chars) or `""` |
| `progress(mission)` | 112 | latest state of the mission from `STATES` — defined but not called in this file |
| `queue_alive()` | 122 | PowerShell process count of `bash.exe` running `real_queue.sh`; True when > 0 |
| `executions_running()` | 129 | count of n8n `AHAWR v13%` executions in status `running`/`new`/`waiting`; -1 when Postgres does not answer |
| `runner_running()` | 138 | `running` + `queued` runs from the runner health endpoint; -1 when unreachable |
| `slot_loop_running()` | 149 | checks for a `slot_cache.py loop` process (not called here) |
| `archive_think_steps()` | 161 | every 6 h runs `think_archive.py` (see above) |
| `ensure_relay()` | 170 | restarts `tg_relay.py` when the process is gone and `tg_relay.enabled` exists |
| `start_queue()` | 182 | starts `real_queue.sh` via PowerShell process creation (`NOWAIT=1`, output to `real_queue.out`) |
| `main()` | 190 | the 60 s loop: notices for new log lines, restart logic, idle check, relay and archive calls, error logging, `time.sleep(60)` |

## Host-only watchers and compose

### `docker_watchdog.ps1` (242 lines, host script)

Started as `powershell -NoProfile -ExecutionPolicy Bypass -File docker_watchdog.ps1`, runs forever.

**Check.** Every 20 s (`$everySeconds = 20`, line 22):
- the Docker engine: `docker version` with a 12 s hard limit (`Test-Engine`,
  lines 29-40) — answers when the Server Version matches a `d.d` pattern;
- HTTP status of three ports: n8n `http://localhost:5678/healthz` (200),
  dashboard `http://localhost:8701/healthz` (200), llama
  `http://localhost:8033/health` (200) (`Test-Url`, lines 121-125);
- Docker VM memory, only when the engine answers (`Write-VmSample`,
  lines 106-119): `wsl.exe -d docker-desktop` reads
  `/proc/meminfo` (MemAvailable, SwapTotal, SwapFree) and
  `/proc/pressure/memory` + `/proc/pressure/io` (`Get-VmMemory`,
  lines 95-104) → one line per probe in `docker_vm.csv`
  (`time, avail_mb, swap_used_mb, mem_some10, mem_full10, io_some10, io_full10`);
- one line per probe in `docker_watchdog.csv`
  (`time, engine, engine_ms, n8n, dashboard, llama, free_ram_gb`).

**Action.**
- Engine hung (4 failed probes in a row, `$hungAfter = 4`, about 90 s):
  `Restart-DockerDesktop` (lines 137-184) — stops the Docker Desktop
  processes, `wsl.exe --shutdown`, moves the stale
  `%LOCALAPPDATA%\docker-secrets-engine` folder aside, starts Docker
  Desktop, waits up to 6 min for the engine and up to 6 min for the
  containers (`docker ps` with at least 6 `healthy`), then runs
  `slot_cache.py restore` after the llama-server `/health` answers (5 min
  poll, 10 s steps). Each probe episode also writes one snapshot file
  into `docker_snapshots\*.txt` (host RAM, commit charge, CPU 3 s
  sample, Docker/WSL process table, TCP states, `nvidia-smi` power/utilization,
  `wsl -l -v`, `wsl -d docker-desktop` load/PSI/free/dmesg, `docker info`,
  Docker Desktop log warnings; `Save-Snapshot`, lines 55-90).
- Dashboard down while the engine answers (5 probes, `$dashAfter = 5`):
  `docker restart ahawr-dashboard` (line 229).
- Episodes that end on their own are recorded as `recovered-by-itself`
  (lines 233-238). Every episode also gets a row in `docker_incidents.csv`
  (`start, end, duration_s, kind, action, note`).
- At most 3 restarts per hour (`$maxRestartsPerHour = 3`): when the limit
  is hit the watchdog writes `real_queue.alert` and stops restarting
  (lines 215-218), keeps measuring, sleeps 300 s.

**Notice.** VM under memory pressure — `avail_mb < 400` or `mem_full10 > 20`
— once per 15 min: a text line in `docker_watchdog.log` and a Telegram
message (Russian text) sent via `notify.py` (lines 111-117). The
restart/stall/stop-restarting events are logged in `docker_watchdog.log`
(`restarting Docker Desktop`, `containers are back`, `not restarting again`);
`queue_watch.py` turns those log markers into Telegram notices.

**Host dependencies.** Windows + PowerShell (process control, CIM,
`Get-Counter`), Docker CLI (version, ps, restart), Docker Desktop,
WSL (`wsl.exe -d docker-desktop`, `wsl --shutdown`), `nvidia-smi`
(snapshot only), `python notify.py` (notice), `slot_cache.py` (slot
restore).

### `docker_vm` (a log, not a separate script)

`docker_vm.csv` is an output of `docker_watchdog.ps1` (`Write-VmSample`,
lines 106-119): one line per engine-alive probe,
`time, avail_mb, swap_used_mb, mem_some10, mem_full10, io_some10, io_full10`.
No separate host script exists for it. It is the source of the board's
`vm_memory` panel (`board_ops_api.py` `/watchers`,
`board_ops_parsers.parse_vm_csv`; the snapshot section key is
`docker_vm`).

### `gpu_power_governor.py` (195 lines, host script)

Host process: `python gpu_power_governor.py` (dry run, default) or
`python gpu_power_governor.py --apply` (really switches the limit).

**Check.** Follows `docker logs -f --since 1s <container>`
(default container `llama-cpp-server`; `run()`, lines 158-177, 10 s
sleep between streams). It parses two llama-server log lines:
- `selected slot by LCP similarity, f_sim_best = S, f_keep = K`
  (`SEL`, line 30) → the uncached part of the prompt (the fraction 1 − S
  and the token count, lines 132-142);
- `prompt processing, n_tokens = X, progress = P` (`PROG`, line 32) →
  how much of the uncached part is done (`P = 1.00` = end,
  `feed()`, lines 144-155); a cold prompt (nothing cached) is estimated
  as the whole prompt from `X / P`.
- A boost watchdog thread (`watchdog()`, lines 115-119) checks every
  10 s that a boost does not outlive `--max-boost-seconds`
  (default 900 s).

**Action.** Boost starts when the uncached part is at least
`--min-tokens` (default 4096) and, if set, at least `--min-fraction`
of the prompt; with `--apply` it runs the high command
(`--high-cmd`, default `schtasks /run /tn GPU_PL_HIGH`, elevated
`nvidia-smi -pl 170`) and releases with the low command
(`--low-cmd`, default `schtasks /run /tn GPU_PL_LOW`,
`nvidia-smi -pl 110.5`) on prefill done, after the max boost time, on
log stream end or exit. Without `--apply` it only logs what it would do
(`gpu_power_governor.log`, human readable; `gpu_power_governor.jsonl`,
one JSON line per boost). The current power limit is read with
`nvidia-smi --query-gpu=power.limit` (`power_limit()`, lines 67-73).

**Notice.** Log lines only (`gpu_power_governor.log`): start, each
boost/release with the uncached size and the current limit, dry-run
estimates of saved time. No Telegram notices.

**Host dependencies.** Docker CLI (follows the llama-server container
log), `nvidia-smi` (power limit read, and the actual limit switching
inside the scheduled tasks), Windows `schtasks` for the elevated
`GPU_PL_HIGH` / `GPU_PL_LOW` tasks. Display-only for the board: the
board reads the log head, never changes anything (per the mission rule).

### Existing watcher mentions in the docs (file:line)

`claude-runner/docs/board-ops.md`:
- 22 — `docker_vm.csv` tails in the snapshot description
- 24 — `queue_watch` snapshot data (notices per hour)
- 128 — snapshot data sources
- 145 — Docker VM data from `docker_watchdog.ps1` probe (97-103)
- 146 — watchdog data from `docker_watchdog.csv` / `docker_incidents.csv`
- 148 — `queue_watch` section
- 274, 276 — `restart_docker_watchdog` action
- 277 — `restart_docker_watchdog` description
- 300, 303, 304 — the watchdog restart command
- 349, 350 — snapshot tails including `docker_vm.csv`
- 154 — GPU governor display-only (from `gpu_power_governor.log` head)

`claude-runner/docs/board-ops-notes.md`:
- 184, 227, 228, 232, 235, 237, 238, 239, 247, 255, 257, 262, 270, 271,
  276, 277 — watcher/environment references
- 329 — snapshot data sources
- 330 — `data-tables/ops/docker_watchdog.ps1` in the recorded file list
- 366, 373, 374, 375, 376, 377, 378, 379 — watcher references
- 377, 378 — restart docker_watchdog action
- 407, 408 — dashboard probed by `docker_watchdog.ps1`
- 417, 418 — recorded file list including `docker_watchdog.ps1` and
  `gpu_power_governor.py`
- 542, 544, 548 — Docker VM memory measured by `docker_watchdog.ps1`
- 564, 567, 569 — docker_watchdog.ps1 — output CSVs section
- 587, 588, 589 — related outputs (text log, incidents, snapshots)
- 590, 592, 595 — `queue_watch.state.json` sample
- 610, 611, 617, 629 — queue_watch notices

`claude-runner/docs/guards-notes.md` (existing, before this section):
- 370 — `## Host scripts: queue_watch` heading
- 372 — `queue_watch.py` (266 lines, read-only study)
- 374 — host watcher, runs forever
- 378, 381 — new lines of `real_queue.log` / `docker_watchdog.log`
- 416 — `docker_watchdog.log` markers
- 445 — `data-tables/ops/queue_watch.py:244-246` gap
- 460, 471, 474 — think archive
- 486, 487, 498, 501, 502 — data sources and functions

`docs/cold-prefill.md`: 294, 298, 307, 318, 320, 330, 331, 332, 333, 335,
340, 344, 358, 359, 360, 374, 375, 380, 383, 386, 388, 389, 1346, 1366,
1368, 1369, 1385, 1386, 1392, 1393, 1408, 1409, 1420, 1440, 1441, 1444,
1445, 1484 — watcher mentions (historical notes).

`docs/mission-reports/T017-mission-report-ru.md`: 185, 186, 191, 193,
197, 199, 208, 233, 235, 250.
`docs/mission-reports/mission-report-prefill-cost-ru.md`: 46.
`docs/telegram-bot.md`: 17.
`README.md`: 803 — `queue_watch.py` restarts the tg relay.

### The ahawr-dashboard compose service (names only, no values)

`docker-compose.yml`, service `ahawr-dashboard` (lines 207-246):

**Environment variable names** (lines 223-231):
- `DASHBOARD_RUNNER_URL` — the runner's HTTP URL (fixed in compose)
- `DASHBOARD_BOARD_PG_DSN` — Postgres DSN (empty by default)
- `DASHBOARD_RUNNER_API_KEY` — the runner API key (from
  `CLAUDE_RUNNER_API_KEY`)
- `DASHBOARD_HERMES_URL` — the Hermes API URL (from
  `HERMES_API_URL`; empty turns the Hermes source off)
- `DASHBOARD_HERMES_API_KEY` — the Hermes API key (from
  `HERMES_API_KEY`)
- `DASHBOARD_HERMES_SOURCE` — the Hermes session source (from
  `HERMES_SESSION_SOURCE`)

**Telegram bot variable names** — none in the `ahawr-dashboard` service.
The Telegram bot variables used by the host watchers live in `notify.py`
and `queue_watch.py` (the repo-root `.env`), read there:
- `AHAWR_ALERT_WEBHOOK_PATH` — the n8n webhook path (`notify.py:17`)
- `telegram_chat_id` — the column in the hermes profile table
  (read by `notify.py send()`, line 65)
- `CLAUDE_RUNNER_API_KEY` — the runner Bearer key (used by
  `queue_watch.py runner_running()` and by the `DASHBOARD_RUNNER_API_KEY`
  above)
- `TELEGRAM_BOT_TOKEN` — used by `tg_relay.py` on the host
  (`README.md:803`)

**Volumes** — none. The `ahawr-dashboard` service has no volume mounts.

**Networks** — none declared. No `networks` key for the service;
`extra_hosts` (line 233-235) maps `host.docker.internal` to
`host-gateway`. The service is reachable only at
`127.0.0.1:<AHAWR_DASHBOARD_PORT>` (line 220).

**Existing read-only mounts** — none for `ahawr-dashboard`. The only
read-only mounts in the compose file are:
- `ahawr-retrieval`: `/workspace` read-only (line 103), the host drive
  `D:\` mounted at `/d` read-only (lines 105-108), and
  `litellm`: `./litellm/config.yaml`, `ahawr_hooks.py`,
  `think_leak_guard.py`, `cache_trace.py` all read-only (lines 275-279),
  `./litellm/runtime` read-only (line 282).
- `claude-runner`: `/workspace` read-write (line 174), the host drive at
  `/d` read-write (lines 176-178), `./litellm/runtime` read-write
  (line 183), the scratch dir read-write (lines 185-189).

**Could an existing mount carry a host heartbeat file?** Yes. The host
drive is mounted at `/d` read-only in `ahawr-retrieval`
(docker-compose.yml:105-108) and read-write in `claude-runner`
(docker-compose.yml:176-178), so a heartbeat file written by a host
script to `D:\n8n\data-tables\ops` — for example
`D:\n8n\data-tables\ops\guards_heartbeat.json` (an example name) — is
visible as `/d/n8n/data-tables/ops/guards_heartbeat.json` in those two
containers (the path assumes the default `AHAWR_HOST_DRIVE` of `D:\`).
The `ahawr-dashboard` service has NO volumes, so the dashboard cannot
read it today. Part 3 therefore needs a proposed read-only bind mount
for `ahawr-dashboard`, shown as an example block only and not applied
to `docker-compose.yml`:

```yaml
volumes:
  - type: bind
    source: ${AHAWR_HOST_DRIVE:-D:\}
    target: /d
    read_only: true
```

## Migration research: framework

Facts for the guard migration (read-only; line numbers from the working tree).

- `guards.py:97` `GuardSettings` fields: `master`, `dry_run`, `per_guard`, `notice_limit`, `orphan_confirm_checks`, `interrupt_loop_threshold`, `interrupt_loop_window_s`, `interrupt_loop_quick_s`. `from_env` (`guards.py:110`) reads them.
- Env names read by `GuardSettings.from_env` (`guards.py:114-156`): `DASHBOARD_GUARDS` (master, bool, default off); `DASHBOARD_GUARDS_<NAME>_ENABLED` and `DASHBOARD_GUARDS_<NAME>_INTERVAL` (per guard, read only when master is on, `guards.py:119-137`); `DASHBOARD_GUARDS_NOTICE_LIMIT` (float, default `NOTICE_LIMIT_PER_MIN` = 1.0, clamped to >= 0, `guards.py:41,138-145`); `DASHBOARD_GUARDS_DRY_RUN` (bool, default off, `guards.py:143`); `DASHBOARD_GUARDS_ORPHAN_CONFIRM_CHECKS` (default 2, `guards.py:146`); `DASHBOARD_GUARDS_INTERRUPT_LOOP_THRESHOLD` (default 3, `guards.py:149`); `DASHBOARD_GUARDS_INTERRUPT_LOOP_WINDOW_S` (default 900.0, `guards.py:152`); `DASHBOARD_GUARDS_INTERRUPT_LOOP_QUICK_S` (default 120.0, `guards.py:155`).
- Parsing helpers: `_float`, `_int`, `_bool` (`guards.py:53,62,71`). `GuardSettings.enabled` and `interval` (`guards.py:160,166`): with master off, `enabled` is always False.
- Defaults: `DEFAULT_INTERVAL_S` = 60.0 (`guards.py:37`), `BACKOFF_BASE_S` = 60.0 (`guards.py:34`).
- `Guard` base (`guards.py:199`), constructor `Guard(name, interval)`. `Finding(detail, severity)` (`guards.py:172`). `GuardState` fields `last_run`, `last_result`, `last_action`, `error_count`, `status`; `snapshot()` (`guards.py:180-196`).
- `RunnerClient` (`guards.py:216`): injectable interface with `list_processes()`, `list_runs()`, `terminate(pid)`. No concrete httpx implementation exists in `guards.py`.
- `OrphanProcessGuard(client, notifier, interval, confirm_checks)` (`guards.py:244`): closed-run pid must be seen for `confirm_checks` checks in a row (`guards.py:269-273`); `act` calls `client.terminate` (`guards.py:284`).
- `InterruptLoopGuard(client, notifier, interval, threshold, window_s, quick_s)` (`guards.py:301`): notice only, one per window (`guards.py:331-333`); `act` logs when no notifier (`guards.py:343-345`).
- `NoticeLimiter(limit_per_minute)` (`guards.py:350`): fixed 60 s window, `allow(now)` (`guards.py:358`). Created in `GuardSupervisor.__init__` with `settings.notice_limit` (`guards.py:443-450`).
- `Notifier(env, transport, env_provider)` (`guards.py:369,378`): token and chat id from `DASHBOARD_TELEGRAM_BOT_TOKEN` and `DASHBOARD_TELEGRAM_CHAT_ID` (`guards.py:29-30`); `send(text)` (`guards.py:399`) returns True on log fallback; `_notify` in the supervisor applies the limiter first (`guards.py:540-541`).
- `GuardSupervisor` (`guards.py:440`): `start()` (`guards.py:458`), `stop()` (`guards.py:473`), `state()` (`guards.py:491`) returns `SupervisorSnapshot` with `dry_run`, `running`, `guards` (`guards.py:433-437`).
- Dashboard wiring: `dashboard.py:92-101` creates `GuardSupervisor(GuardSettings.from_env(), [], Notifier())`, starts it in the lifespan and stops it in `finally`. No guards are registered in production (`[]` at `dashboard.py:94`).
- `GET /api/guards` (`dashboard.py:165-179`): returns `enabled` (settings master), `dry_run`, `running`, and `guards` as a list of `{name, status, last_run, last_result, last_action, error_count}`. No auth header is checked, matching the other read routes.
- Guard strip in `dashboard.html`: `<div id="guardstrip">` (`dashboard.html:253`), styled at `dashboard.html:224-236`. `renderGuardStrip` shows `off` text when `enabled` is false, otherwise one `<span class="g">` per guard (`guardEntryHtml`: status, last action, last run, `result` = `last_result`, and a `dry run` mark when `dry_run`), then one read-only `<span class="g">` per host heartbeat (`heartbeatHtml`: `fresh`/`stale`/`error`/`unknown`, last action, time, result, `read-only` mark), plus a `#guardrefresh` button. Heartbeats come from the same `/api/guards` response, so the existing `loadGuards` refresh covers them; no new poller.
- i18n: `GUARD_TEXT` holds English and Russian fallbacks; `guardT` first reads `guards.<key>` from the board i18n payload fetched from `/api/board/i18n`, then the fallback. The `guards.*` keys live in `board_i18n_en.json` and `board_i18n_ru.json`. They are also in `BUNDLED_EN` in `board_i18n.js`: the parity test "bundled table matches the JSON file" requires every key of `board_i18n_en.json` to be in the bundled table, so the new keys were added there too. They are not `L()` keys.
- Tests covering guards: `tests/test_guards.py` (settings, notifier, supervisor; fakes `FailingGuard`, `OkGuard`, `FlakyGuard`, helper `settings_from`); `tests/test_guards_framework.py` (supervisor, limiter, snapshot; fakes `FiringGuard`, `FailingGuard`, `FlakyGuard`, `QuietGuard`, `_fake_transport`, helper `_supervisor`; no network); `tests/test_guards_api.py` (`GET /api/guards` with fake `FiringGuard` and the `client` fixture; no secret in the response); `tests/test_dashboard.py:325-362` (route off by default, dry-run field, no env values; helper `dashboard(client)` at `test_dashboard.py:290`).
- Not tested: `OrphanProcessGuard` and `InterruptLoopGuard` have no test in `tests/` (grep for both class names returns only `guards.py`), and `RunnerClient` has no fake in the tests.
- Open points: `docs/guards.md` has no open-points section; its "Container guards planned" list (`docs/guards.md:47-60`) is the work still to do. This notes file has no open-question section either.
- `dashboard.html` has no `L()` keys; the board `L()` mechanism is separate (see `i18n in dashboard.html`, `guards-notes.md:247`).

## Migration research: queue_watch

Read-only research for the guard migration (line numbers from `data-tables/ops/queue_watch.py` and `data-tables/ops/notify.py`; store lines from `claude-runner/src/claude_runner/board_pg.py`).

Duties, one line each: function, limit constant and value, data source.

- Loop every 60 s: `main()` `time.sleep(60)` (queue_watch.py:262); limit 60 s; host-only, no store equivalent.
- Mission not completed with last review: `main()` regex `MISSION NOT COMPLETED: (\S+) \((\w+)\)` (queue_watch.py:204-206) and `last_review()` (queue_watch.py:100-109), review text cut to 350 chars (queue_watch.py:102); trigger from host file `real_queue.log`; review from `_EVENT_LOG_TABLE` (board_pg.py:46) via `BoardStore.events(state_key)` (board_pg.py:489), filtered in SQL by `event_type = 'review_parsed'` and ordered by `"createdAt"` (store orders by `id` and returns `created_at`).
- Mission completed: `main()` regex `ended (\S+): completed` (queue_watch.py:207-209); limit none; source host file `real_queue.log`; store only has a copy of the line via `get_queue_log()` (board_ops_store_pg.py:601, table `queue_log`), not a parsed event.
- ALERT lines: `main()` (queue_watch.py:213-214), skipped when the line contains `DISABLE_THINKING`, `new missions were not inserted` or `REAL QUEUE STOPPED`; limit none; source host file `real_queue.log`; store copy via `get_queue_log()`.
- Queue stopped: `main()` (queue_watch.py:210-212), sets `queue_end = "stopped"`; limit none; source host file `real_queue.log`; store copy via `get_queue_log()`.
- Queue done: `main()` (queue_watch.py:215-217), sets `queue_end = "done"`; limit none; source host file `real_queue.log`; store copy via `get_queue_log()`.
- Docker notices: `main()` (queue_watch.py:218-224), markers `restarting Docker Desktop`, `containers are back`, `not restarting again`; the 3-restarts-per-hour limit is in `data-tables/ops/docker_watchdog.ps1:12`, not in queue_watch.py; source host file `docker_watchdog.log`; store has no parsed log markers (watchdog CSV is a separate snapshot, `board_ops_parsers.parse_watchdog_csv`).
- Queue restart: `main()` (queue_watch.py:227-241), `start_queue()` (queue_watch.py:182); limit `len(restarts) < 4` within 3600 s (queue_watch.py:233-234), gated by `executions_running() == 0` and `queue_end != "done"`; sources: `queue_alive()` PowerShell `Win32_Process` count (queue_watch.py:122-126) and n8n Postgres `execution_entity` (queue_watch.py:131); no store method exposes process state.
- Idle notice: `main()` (queue_watch.py:243-251); limit 1200 s, flag `idle_notified`; sources: `executions_running()` and `runner_running()` (queue_watch.py:138-146, runner `/health` via `docker exec`, counts `running` + `queued`); store has per-mission status in `list_live_states()` (board_pg.py:444) but no runner run counts. Known gap: a queued-only runner run keeps `runner_running()` above 0, so the timer never starts (see the queue_watch.py:244-246 note above).
- Notice cap 20 per hour: `notify()` (queue_watch.py:42-48), window 3600 s, kept in memory in `sent_times`; store none (lost on restart).
- Pending notices: `notify()` keeps up to 10 (queue_watch.py:51), `retry_pending()` (queue_watch.py:55-66) drops them after 6 h (line 59) and retries each minute; in memory; store none.
- Start tg_relay: `ensure_relay()` (queue_watch.py:170-179); limit: runs only when `tg_relay.enabled` exists (queue_watch.py:175); sources: host process list and `data-tables/ops/tg_relay.enabled`; the Python path `D:\Python312\python.exe` is a literal in the command (queue_watch.py:177) and needs a setting in the migration; store exposes only the `tg_relay` panel fields (`board_ops_api.py:234`), not the start action.
- Think archive every 6 h: `archive_think_steps()` (queue_watch.py:161-167), `6 * 3600`, timeout 900; host-only (not in the task list but still a duty of the loop).

`queue_watch.state.json` fields: only `q` (bytes of `real_queue.log` already read) and `d` (bytes of `docker_watchdog.log` already read), written by `save_state()` (queue_watch.py:76-77) from `new_lines()` (queue_watch.py:192-193). Host file; `parse_queue_watch_state()` (board_ops_parsers.py:189) parses it and returns `q`, `d`, `notices_per_hour` and `last_notifications`; the publisher that writes it into the store was not checked here.

Telegram text formats (exact, as sent by queue_watch.py; `mission` and `state` are regex captures, `\n` joins lines):

- `❌ Миссия {mission} не завершена (состояние {state}).` + review block when a `review_parsed` event exists: `\nПоследнее ревью: задача {task_index+1}, попытка {task_attempt}, оценка {review_score}.\n{review_reason[:350]}` (newlines in the reason replaced by spaces).
- `✅ Миссия {mission} завершена.`
- `⛔ Очередь миссий остановилась: {text after "REAL QUEUE STOPPED:"}. Сторож запустит её заново, когда n8n освободится.`
- `⚠️ {text after "ALERT:"}`
- `🏁 Очередь миссий закончилась: все миссии пройдены или пропущены.`
- `🐳 Docker завис, сторож перезапускает Docker Desktop.`
- `🐳 Docker вернулся, контейнеры подняты. Очередь продолжит сама.`
- `🐳 Docker зависает слишком часто (3 перезапуска в час), сторож больше не перезапускает его. Нужно вмешательство.`
- `🔁 Очередь миссий запущена заново (после остановки).` or `🔁 Очередь миссий запущена заново (её процесса не было).`
- `💤 Ничего не выполняется уже 20 минут, хотя очередь жива. Проверьте прогон.`
- Late retry prefix (queue_watch.py:62): `(с опозданием, событие в HH:MM) ` before the original text.

Added by `notify.send()` (notify.py:27-59, 60-72) before every notice, which the new guards must reproduce: a tag line `📌 {mission} · T{NNN}` plus a newline, from the active row of the live-state table (`status = 'active'`, newest `updatedAt`), or `📌 {last started mission}` from `real_queue.log` when the database is unreachable; not added when the mission id is already in the text. The text is posted as JSON to `http://localhost:5678/webhook/<AHAWR_ALERT_WEBHOOK_PATH>` with the chat id from `telegram_chat_id` (id 5 of the table `data_table_user_1S21PsMqT0FpR7eD`).

Checks: no code changed; `git diff --stat` is checked in the report.

## Migration research: relay and orphan guard

Read-only study of `data-tables/ops/tg_relay.py`, `tg_commands.py` and `orphan_guard.py` (T003). No code changed.

### `tg_relay.py` facts (file:function)

- `main()` (tg_relay.py:70): exits with code 1 and logs when `TELEGRAM_BOT_TOKEN` or `AHAWR_TG_WEBHOOK_PATH` is missing in `.env` (lines 71-75).
- Long poll, `main()` line 86: `getUpdates` with body `timeout` 50 s, `limit` 50, `allowed_updates` `["message", "callback_query"]`. The HTTP read timeout is 70 s (`call()` default, line 44).
- `set_menu()` (line 51): `setMyCommands` with HTTP timeout 30 s; the list is built from `COMMANDS` as `{"command", "description"[:256]}`.
- Offset file: `OFFSET_FILE` = `data-tables/ops/tg_relay.offset` (line 24). Format: plain decimal text, one integer, `update_id + 1`. `load_offset()` (line 63) returns 0 when the file is missing or not an integer.
- Offset save: `main()` line 94, once per update, and only after `forward()` (lines 57-60, POST, timeout 30 s, accepts 2xx) returned true (lines 91-92). A rejected update raises at line 92; the offset stays at the last saved value and the update is delivered again.
- Webhook URL: `N8N` = env `AHAWR_N8N_URL`, default `http://localhost:5678` (line 26). Path from `.env` variable `AHAWR_TG_WEBHOOK_PATH` via `env()` (line 29, read at line 72). URL built at line 76 as `<N8N>/webhook/<path>`. Token from `.env` variable `TELEGRAM_BOT_TOKEN` (line 71), replaced by `<token>` in error text (line 98).
- Retry and backoff: `main()` wraps each cycle in `except Exception` (line 96). `fails` counter, reset to 0 after each successful `getUpdates` (line 89). Sleep `min(60, 3 * fails)` seconds (line 101), so 3, 6, 9 ... up to 60 s. No sleep after a good cycle (the long poll paces it). A `getUpdates` answer with `ok` false raises (line 88) and enters the backoff.
- Menu retry: `menu_done` is set after `set_menu()` returns (lines 79, 85). If the call raises, it is retried on the next cycle. If Telegram answers `ok` false, no exception is raised, `menu_done` is still set, and the log says `ok=False` (line 54); the menu is not retried until the process restarts.
- Log lines (`log()`, line 39, file `tg_relay.log`, line 25): `relay started, offset N` (78), `setMyCommands: ok=..., N commands` (54), `forwarded update N (message|callback)` (95), `error #N: ...` (100, first 3 errors and every 20th; text stripped of CR/LF and cut to 200 characters).

### `tg_commands.py` facts

- `COMMANDS` (tg_commands.py:6-33): 26 tuples `(name, category, description)`. Categories: Session 13, AHAWR 3, Configuration 3, Info 7. Descriptions are in Russian, 3-256 characters, no `<>&`.
- `python data-tables/ops/tg_commands.py` (self-check, line 38-41) printed `26 commands ok`.
- `ALIASES` (line 36): 4 names `progress`, `tasks`, `restart`, `commands`. No importer uses it (grep for `ALIASES` finds only the definition).
- Importers: `tg_relay.py:22` and `patch_bot_webhook.py:20` (`from tg_commands import COMMANDS`).
- Reachability from the container: the `claude-runner` service builds from `context: ./claude-runner` (`docker-compose.yml:120-121`, `208-209`). `data-tables/ops/` is outside that context and no compose volume or Dockerfile line mounts `data-tables` (grep finds none). `data-tables/ops/` is not in git. Conclusion: the file cannot be imported from the container.

**Decision: copy the list into the claude-runner package and add an equality test.** Reason: the container cannot import `data-tables/ops/tg_commands.py`, and making it reachable needs a new mount or build change, which is a Docker topology change (ask first). The copy keeps `tg_relay.py` and `patch_bot_webhook.py` unchanged. The equality test must skip when `data-tables/ops/tg_commands.py` is absent, because a git checkout does not contain that folder. This matches the mission plan in `data-tables/ops/_make_guards2_mission.py:19`.

### `orphan_guard.py` duties and framework coverage

| # | orphan_guard.py duty (file:line) | Framework (guards.py) | Covered |
|---|---|---|---|
| 1 | Reads CLI processes (`processes()`, :66) and runs (`runs()`, :84) | `RunnerClient.list_processes` / `list_runs` (:221-228): interface only, no concrete implementation | Partly |
| 2 | Match process to run by `session_id` or `claude_session_id` (:121-131), session id from `--session-id`/`--resume` (:79) | `OrphanProcessGuard.check` (:258-275) works on a `status` per pid supplied by the client; the matching rule is not in guards.py | Only if the client does it |
| 3 | Orphan seen in two checks in a row (:136-139) | `confirm_checks` = 2 (`ORPHAN_CONFIRM_CHECKS`, :249, :269-273) | Yes |
| 4 | Process younger than `MIN_AGE_SECONDS` (120 s) is never touched (:133-134) | No age check anywhere in `OrphanProcessGuard` | **No (gap)** |
| 5 | Unknown state never kills: nothing is done when docker or the API does not answer (:122, :163-165) | A client error raises in `check()` and produces no findings; the supervisor's handling was not checked here. A process with no `status` is counted as closed (`str(... or "")` at :266-268), so a client that omits status would kill every process | **Gap** |
| 6 | Kill, then `kill -9` after 10 s if alive (:94-102) | `OrphanProcessGuard.act` calls `client.terminate(pid)` (:284); escalation is not in guards.py and the runner endpoint behind it was not verified | Unknown |
| 7 | Telegram notice for every kill, at most one per 600 s (:142-145) | `OrphanProcessGuard.act` (:277-290) never calls `self.notifier`; `self._noticed` is written (:289) and never read. `notifier` is stored at :253 only | **No (gap)** |
| 8 | Three quick `interrupted` ends in 900 s raise one "interrupt loop" notice, no action (:149-162) | `InterruptLoopGuard` (:318-347): threshold 3, window 900 s, quick 120 s, notice only | Yes, with differences below |
| 9 | Dry run `--once`, kills nothing (:113, :137-138) | `DASHBOARD_GUARDS_DRY_RUN` (guards-notes.md section "Migration research: framework") | Yes, by setting |
| 10 | Process filter: only `claude -p` processes (:76-77) | None in guards.py | Only if the client does it |

Differences in the interrupt loop (row 8):
- Quick end test: orphan_guard uses `f - s < 120` (:155), the framework uses `<= quick_s` (guards.py:329).
- Notice gap: orphan_guard waits 1800 s between notices (:158), the framework waits `window_s` = 900 s (guards.py:331).
- Notice text: orphan_guard sends the Russian text (:161-162); the framework sends its own English `Finding` detail through `notifier.send` (guards.py:346).

**Gaps not covered by the existing guards:** the 120 s minimum age (row 4), the kill-notice to Telegram (row 7, because `OrphanProcessGuard.act` never sends), the empty-status-kills rule (row 5), and the `kill -9` escalation (row 6, unless the terminate endpoint does it). With the default 60 s interval and two confirmations, a process can be terminated about 60 s after its first closed reading, which is before the 120 s age limit that orphan_guard uses.

**Tests:** no test file references `OrphanProcessGuard` or `InterruptLoopGuard` outside `guards.py` (grep under `claude-runner/tests` and `claude-runner/src` lists only `guards.py`). No test covers the gaps above.

## Migration research: host side

Read-only research for the heartbeat and launcher design. No script, compose file or queue was changed.

### Facts per script (file:line)

- `data-tables/ops/docker_watchdog.ps1` (242 lines)
  - `$ops = $PSScriptRoot` at line 15; logs `docker_watchdog.csv` (16), `docker_incidents.csv` (17), `docker_watchdog.log` (18, `Log()` at 27), `real_queue.alert` (19), `docker_vm.csv` (92).
  - Limits: `everySeconds=20` (22), `hungAfter=4` (23), `dashAfter=5` (24), `maxRestartsPerHour=3` (25).
  - `Restart-DockerDesktop` at 137: `Stop-Process` (139), `& wsl.exe --shutdown` (141), `Start-Process` of Docker Desktop (148).
  - Main loop `while ($true) {` at 192; `Add-Incident` at 224, 231, 235; dashboard restart `& docker restart ahawr-dashboard` at 229; alert write at 217; pacing `sleep` at 241.
- `data-tables/ops/gpu_power_governor.py` (195 lines)
  - `OPS` = script directory (28); `log()` writes `gpu_power_governor.log` (36-43); JSONL write at 110.
  - `boost()` at 75 calls `_run(high_cmd)` (82); `release()` at 90 calls `_run(low_cmd)` (100).
  - Watchdog thread 115-119; `run()` at 158; `while True` at 166; `docker logs -f llama-cpp-server` via `Popen` at 168; reconnect sleep at 177.
  - Defaults `schtasks /run /tn GPU_PL_HIGH` (188) and `GPU_PL_LOW` (189); `--apply` at 183, dry run by default.
- `docker-compose.yml` (292 lines)
  - Service keys at 4, 21, 69, 119, 207, 252; top-level `volumes:` at 288-292; no `networks:` key.
  - `ahawr-dashboard` block 207-250: `environment` 17-25 (`DASHBOARD_RUNNER_URL`, `DASHBOARD_BOARD_PG_DSN`, `DASHBOARD_RUNNER_API_KEY`, `DASHBOARD_HERMES_URL`, `DASHBOARD_HERMES_API_KEY`, `DASHBOARD_HERMES_SOURCE`), `extra_hosts` 27-29, `healthcheck` 31-35, no `volumes`.
  - `ahawr-retrieval` read-only bind of `/d` at 104-108: the existing pattern for a read-only mount.
- `data-tables/ops/_start_watchers_hidden.ps1`
  - `$ops` (2), `$py` (3), `Start-Hidden` helper (4-6); starts `queue_watch.py` (7), `orphan_guard.py` (8), `tg_relay.py` (9), `gpu_power_governor.py` (11), `docker_watchdog.ps1` (12), and `bash _restart_queue_at_end.sh` (13). It does not start `real_queue.sh` directly.
- `data-tables/ops/_stop_queue.sh`
  - `kill_tree()` (3); patterns `[q]ueue_watch\.py` (4) and `[r]eal_queue\.sh|[w]ait_mission_end|[l]aunch_mission` (5). Bracketed regex avoids self-match.
- `data-tables/ops/_restart_queue_at_end.sh`
  - Waits for `ahawr-dashboard-board-views-2` (11-16); `kill_tree` (18-19); `docker stop`/`start n8n-autonomous-agents` if executions still run (23-30); log `restart_queue_at_end.log`; starts `queue_watch.py` at 32 via `Invoke-CimMethod`.
- `real_queue.sh` start path: `queue_watch.py:182-187` runs `bash real_queue.sh` with `NOWAIT=1`, output to `real_queue.out`. `real_queue.sh` writes `real_queue.log` (line 11) and `real_queue.alert` (line 67). Also documented in `claude-runner/docs/board-ops.md:302`.

### Proposal

1. Heartbeat: one JSON line per write, overwriting a single file per watcher:
   `{"name":"docker_watchdog","time":"2026-10-21T12:00:00+03:00","last_action":"engine ok"}`
   Fields: `name` (watcher id), `time` (ISO 8601 with offset), `last_action` (short text, no secrets, no prompts).
2. Heartbeat host directory: `data-tables/ops/heartbeat/` (`D:\n8n\data-tables\ops\heartbeat\`). It is outside git, like the rest of `data-tables/ops/`.
3. Queue restart request file: `data-tables/ops/requests/queue_restart.req`. The dashboard writes it; a small host launcher (about 40 lines) reads it, runs the `_restart_queue_at_end.sh` path only when no execution runs, and limits itself to 4 restarts per hour. Record the final choice in `docs/guards.md`.
4. Read-only bind mount for the dashboard (example, not applied):
   ```yaml
   ahawr-dashboard:
     volumes:
       - /d/n8n/data-tables/ops/heartbeat:/heartbeat:ro
   ```
   The request directory needs a read-write mount, so it is a separate, larger decision.

### Alternative launcher: scheduled task

The only scheduled-task mechanism in the repo is `schtasks /run /tn GPU_PL_HIGH` and `GPU_PL_LOW` (`gpu_power_governor.py:188-189`). Only `/query` and `/run` appear; no `/create` exists in the repo, so the tasks are registered outside it. A scheduled task for the launcher would need the same out-of-repo registration.

### Open items

- Changing `docker-compose.yml` (new mounts) is Docker topology: ask first.
- Heartbeat directory and request path are proposals, not decisions.

## Migration baseline

Recorded before any code change (task T005).

Pytest (`tests/test_guards.py`, `test_guards_framework.py`, `test_guards_api.py`, `test_dashboard.py`, `-q --no-cov`):
- Exit 0, no failing tests; no `F` or `E` progress markers. Progress reached `[100%]`.
- The run printed no pass-count summary line (`-q` with this config), so the count is not quoted.

Node (`tests/board_i18n.test.js`, `tests/board_i18n_ui.test.js`):
- `# tests 2`, `# pass 2`, `# fail 0`.

Lint counts (before the change):

| File | ruff | mypy |
| --- | --- | --- |
| `src/claude_runner/guards.py` | 0 (`All checks passed!`) | 0 errors |
| `src/claude_runner/dashboard.py` | 0 (`All checks passed!`) | 0 errors |

Mypy reported `Found 67 errors in 9 files (checked 2 source files)`; all 67 are in imported modules (`board_pg.py`, `board_ready.py`, `board_api.py`, `board_stats.py`), not in the two checked files.

SHA-256 of files that must not change:
- `docker-compose.yml`: `11899924f9e5cf0f1898c01503000cfd9e739daa5899bbef60759a031bcaf1ce`
- `data-tables/ops/_start_watchers_hidden.ps1`: `159d8ed95f6bf832bf8ffbb72475d270ff44cef1d2089aeec04dd45c39b0c410`

## Migration heartbeats

Task T014. Heartbeat writes added to the two host watchers. Additive only: no existing line was removed or changed.

- `docker_watchdog.ps1`: `Write-Heartbeat` (before the main loop) writes `data-tables/ops/heartbeat/docker_watchdog.json` once per loop, just before the pacing sleep. Its own try/catch logs a failed write and returns, so the loop continues. `last_action` is the loop action (`docker-desktop-restart`, `dashboard-restart`, ...) or `engine ok` / `engine failed`.
- `gpu_power_governor.py`: `Governor.heartbeat()` writes `data-tables/ops/heartbeat/gpu_power_governor.json` from the watchdog thread every 10 s. `last_action` is the current state (`boosting` or `idle`), not a history.
- Both write `{"name", "time" (UTC, ISO 8601 with Z), "last_action"}` as one JSON object, replacing the previous file.
- Not run: neither script was executed. Checks: `py_compile` of the Python file and a diff check (below). The PowerShell parse check did not run (no PowerShell on the host).

Diff of `docker_watchdog.ps1` (untracked file; compared with a copy taken before the edit):

```diff
@@ -183,6 +183,16 @@
     return $true
 }

+function Write-Heartbeat($action) {
+    # a failed write is logged; it must never stop the watchdog loop
+    try {
+        $hbDir = Join-Path $ops 'heartbeat'
+        New-Item -ItemType Directory -Force -Path $hbDir | Out-Null
+        $json = [ordered]@{ name = 'docker_watchdog'; time = [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [System.Globalization.CultureInfo]::InvariantCulture); last_action = [string]$action } | ConvertTo-Json -Compress
+        [System.IO.File]::WriteAllText((Join-Path $hbDir 'docker_watchdog.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
+    } catch { Log ('heartbeat write failed: ' + $_.Exception.Message) }
+}
+
 if (-not (Test-Path $probeLog)) { Set-Content -Path $probeLog -Value 'time,engine,engine_ms,n8n,dashboard,llama,free_ram_gb' -Encoding UTF8 }
 Log 'watchdog started'
 $failEngine = 0; $failDash = 0
@@ -237,6 +247,7 @@
         $episode = $null
     }

+    Write-Heartbeat $(if ($action) { $action } elseif ($engine) { 'engine ok' } else { 'engine failed' })
     $spent = ((Get-Date) - $t0).TotalSeconds
     if ($spent -lt $everySeconds) { Start-Sleep -Seconds ([int]($everySeconds - $spent)) }
 }
```

Diff of `gpu_power_governor.py`:

```diff
@@ -26,6 +26,7 @@
 import time

 OPS = os.path.dirname(os.path.abspath(__file__))
+HEARTBEAT_DIR = os.path.join(OPS, "heartbeat")
 ANSI = re.compile(r"\x1b\[[0-9;]*m")
 SEL = re.compile(r"selected slot by (?:LCP similarity, f_sim_best = ([\d.]+).*f_keep = ([\d.]+)|lru)")
 REL = re.compile(r"stop processing: n_tokens = (\d+)")
@@ -112,9 +113,20 @@
             except OSError:
                 pass

+    def heartbeat(self):
+        action = "boosting" if self.boosted_at is not None else "idle"
+        try:
+            os.makedirs(HEARTBEAT_DIR, exist_ok=True)
+            with open(os.path.join(HEARTBEAT_DIR, "gpu_power_governor.json"), "w", encoding="utf-8") as f:
+                json.dump({"name": "gpu_power_governor", "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
+                           "last_action": action}, f)
+        except OSError as e:
+            log("heartbeat write failed: %s" % e)
+
     def watchdog(self):
         while True:
             time.sleep(10)
+            self.heartbeat()
             if self.boosted_at is not None and time.time() - self.boosted_at > self.a.max_boost_seconds:
                 self.release("max boost time")

```

## Migration dry-run

Replay: `claude-runner/tests/replay_guard_queue.py` (offline, read-only). It runs `MissionNoticeGuard` and
`IdleGuard` with `dry_run`, a `SeenStore` path that must not exist (so no state file is read or written),
an empty event source and a `Notifier` with no environment (nothing is sent). It reads
`data-tables/ops/real_queue.log` and `queue_watch.log` and changes neither.

Mission: `ahawr-worker-attempt-chain`, completed at `real_queue.log:2647` (2026-10-21 04:43:10), the last
finished mission in the log. Window: `real_queue.log` lines 2406-2784 (the end line of the previous mission,
`ahawr-guards-in-dashboard`, to the last line), 379 rows. Each row gets the timestamp of the last timestamped
line above it; the real database rows may carry other timestamps, which can shift the 50-55 s delays below.

Would-be notices (5): 2026-10-20 03:43:26 `ahawr-guards-in-dashboard` completed; 2026-10-20 07:48:22
`ahawr-qa-acceptance` not completed (failed); 2026-10-21 04:43:10 `ahawr-worker-attempt-chain` completed;
2026-10-21 08:44:37 `ahawr-qa-acceptance` not completed; 2026-10-21 09:17:25 `ahawr-qa-acceptance` not completed.

Sent by `queue_watch.py` from 2026-10-20 03:43:26 (12): 5 matched by the replay, 7 restart notices
("queue started again", process-table check).

Differences and reasons:

- Restart notices (7 sent, 0 would-be): the guard only reads queue-log lines. `queue_watch.py:227-241` sends
  them from the process table, so they are not in the log. Not replayable from the logs.
- `ahawr-qa-acceptance` notices (3 matched, text differs): the sent text adds the "last review" block
  (task, attempt, score, reason) from the database `events`. The replay has an empty event source, so it
  sends only the first line. In production `MissionNoticeGuard._last_review` supplies this block.
- Delays (26-55 s between the log line and the send): the send time comes from the sender's own poll cycle, not from the log line. Not a content difference (cause not verified).
- Idle notice: none sent in the window (`queue_watch.log` has no idle notice), and none would be sent by the
  replay, which has no historical run counts. The synthetic check (running=0, alive=True, clock only) fires at
  minute 20 (`IdleGuard`, default 20 min). Not a replay result.
- Queued-only gap: `queue_watch.py:144` counts running plus queued runs as "running", so the idle branch at
  `queue_watch.py:244-246` never fires while work is queued. `IdleGuard.check` (`guard_queue.py:273-277`)
  counts only running runs. The two can differ when runs are queued and none is running. Not replayable: the
  logs hold no run counts.
- ALERT lines ("new missions were not inserted", "DISABLE_THINKING is still in .env") are ignored by both
  sides, as in `ALERT_IGNORED`.

Completed or failed: the replay takes "finished" as the last line that ended a mission with "completed". The
last failed event is `ahawr-qa-acceptance` at `real_queue.log:2724` (2026-10-21 09:17:25); the replay has a
`MISSION NOT COMPLETED` notice for it, and `queue_watch.log` sent one at 09:17:51.

Checks: `python tests/replay_guard_queue.py` exit 0, 5 would-be notices, 12 sent, 5 matched, 7 restart notices
unmatched, idle synthetic minute 20. `python -m ruff check tests/replay_guard_queue.py`: "All checks passed!".
Not run: mypy on the script, pytest (no test covers the script).


## Migration final

Task T025. Final checks of the guards work and a CHANGELOG entry proposal. CHANGELOG.md, README.md, ROADMAP.md and docs/roadmap.json were not edited by this task.

Checks (run from `claude-runner/`):

- Targeted pytest: `python -m pytest tests/test_guards.py tests/test_guard_queue.py tests/test_guard_restart.py tests/test_guard_relay.py tests/test_guard_heartbeat.py tests/test_queue_launcher.py tests/test_dashboard.py --no-cov`: `112 passed in 53.11s`.
- Node: `node --test tests/board_i18n.test.js`: `# tests 1`, `# pass 1`, `# fail 0`. `node --test tests/board_i18n_ui.test.js` (the second T005 file): `# tests 1`, `# pass 1`, `# fail 0`.
- Ruff: `python -m ruff check src/claude_runner/guards.py src/claude_runner/guard_*.py src/claude_runner/dashboard.py`: `All checks passed!`.
- Mypy on the six target files (guards, guard_queue, guard_restart, guard_relay, guard_heartbeat, dashboard): `Found 72 errors in 10 files (checked 6 source files)`; none are in the six target files. The +1 against T005's 71 (the record read 67 in 9 files) is `board_ops_redact.py:124 Incompatible types in assignment`. That file has no working-tree diff against HEAD, and the read-only HEAD copy (`git show HEAD:...`) shows the identical error at line 124, so it is not caused by this mission.
- Changed files against HEAD (read-only `git archive HEAD` copy in /tmp):
  - Ruff over the 22 tracked changed .py files: 74 errors at HEAD, 74 now, identical per file. The 23 new untracked .py files add 0 errors (ruff over all 45 changed .py files: `Found 74 errors.`).
  - Mypy over the 11 tracked changed src files: 76 errors at HEAD, 77 now. The one extra error is `src/claude_runner/board_ops_redact.py: error: Incompatible types in assignment`, in a file that is not modified; not investigated. Mypy over all 19 changed src files: `Found 77 errors in 11 files`; the new untracked src files add 0 errors.
- `git diff --check`: exit 0 after stripping the trailing space on lines 969, 986, 998, 1007, 1024 (blank `+` lines in the diff blocks of the Migration heartbeats section, written under T014). CRLF endings preserved (1099/1099 lines CRLF); `git diff --check -- claude-runner/docs/guards-notes.md` prints no trailing-whitespace lines.
- SHA-256 unchanged against T005: `docker-compose.yml` `11899924f9e5cf0f1898c01503000cfd9e739daa5899bbef60759a031bcaf1ce`; `data-tables/ops/_start_watchers_hidden.ps1` `159d8ed95f6bf832bf8ffbb72475d270ff44cef1d2089aeec04dd45c39b0c410`.
- `data-tables/ops/docker_watchdog.ps1` against `docker_watchdog.ps1.bak-T010` (the newest copy found): 0 removed lines, 32 added lines. The copy is older than the T014 edit, so this covers every edit since T010, not T014 alone.
- `data-tables/ops/gpu_power_governor.py`: additions-only is unverified because no pre-edit copy exists and the file is gitignored (`/data-tables/` in .gitignore line 41), so `git diff -- data-tables/ops/gpu_power_governor.py` is empty by definition, not proof of additions-only.
- DASHBOARD_GUARDS unset is covered by passing tests: `tests/test_dashboard.py::test_guards_route_off_by_default_and_stops_cleanly` and `test_guards_route_unset_returns_previous_shape` (`-k` run: `2 passed, 40 deselected`), and `tests/test_guards.py:225` (`_build_guards(settings_from({}), notifier) == []`).
- Unchanged files: CHANGELOG.md and README.md are absent from `git status` and `git diff --stat`. ROADMAP.md (10 lines) and docs/roadmap.json (28 lines) were already modified in the working tree before this task and were not reverted, so the literal "unchanged" criterion is not met for them.

Not run: the full pytest suite and other test files of the changed modules (outside the verification list); the PowerShell parse check (no PowerShell on the host); mypy on the test files.

Risks: the mypy and ruff comparisons use HEAD as the baseline because the T005 per-file record is incomplete; the host-script additions-only check is partial as described above.

CHANGELOG proposal (text only; to be pasted into the `## 2026-10-21 (0.8.0)` section of CHANGELOG.md by the owner of that file; the version stays 0.8.0 because the new functions raised the middle number from 0.7.400 once):

- **Guard supervisor: host heartbeat, queue restart, Telegram relay and notices** (new functions). `GuardSupervisor` (`guards.py`) runs the guards `idle`, `mission_notice`, `queue_restart`, `host_heartbeat` (`HostHeartbeatGuard` in `guard_heartbeat.py`, read-only over the host heartbeat files) and `tg_relay` (`TelegramRelay` in `guard_relay.py`). `DASHBOARD_GUARDS=1` starts them; `DASHBOARD_GUARDS_DRY_RUN=1` sets dry run. With `DASHBOARD_GUARDS` unset `_build_guards` returns no guards and `GET /api/guards` answers `enabled: false` with the previous body shape. The host watchers `docker_watchdog.ps1` and `gpu_power_governor.py` write one heartbeat file per loop to `data-tables/ops/heartbeat/`.
