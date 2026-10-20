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
