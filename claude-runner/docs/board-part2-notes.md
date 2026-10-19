# Board — part 2 notes

Research notes for the second part of the mission board work. The parts below
(A–F) are the mission's parts:

- **A — task cards and task view**: per-task figures and editing of a mission's
  tasks.
- **B — plan editing through the guarded routes**: plan edits via the guarded
  board routes (optimistic locking, server-side guards).
- **C — thinking-policy panel**: thinking-policy statistics and layout preview
  (`board_think.py`, per-variant statistics).
- **D — readiness, start guard, preflight and change history with rollback**:
  readiness checks, the start guard, preflight, and the `board.changelog`
  history with rollback (`board_ready.py`).
- **E — queue seed**: the board queue table, seeded by
  `ops/board_queue_seed.py`.
- **F — e2e acceptance check without the live stack**: verification of parts
  A–D without running the live n8n stack.

## Gaps and design constraints

### Known gaps (2026-10-17, CHANGELOG.md lines 47–48)

The 2026-10-17 (0.5.0) entry lists the known gaps that are **not** in the 30
tasks of the mission:

1. **The task cards view of a mission (per-task figures and editing)** —
   in the API, not in the page yet.
   → maps to **A** (task cards and task view) and **B** (plan editing through
   the guarded routes).
2. **The thinking-policy statistics and layout preview** — in the API, not in
   the page yet.
   → maps to **C** (thinking-policy panel).
3. **The preflight / backup / rollback screens** — in the API, not in the page
   yet.
   → maps to **D** (readiness, start guard, preflight and change history with
   rollback).
4. **The board queue table starts empty.**
   → maps to **E** (queue seed, `ops/board_queue_seed.py`).
5. **The queue-to-`real_queue.sh` apply step** — in the API, not in the page yet.
   → out of scope: it belongs to the mission `ahawr-dashboard-ops-control`
   and must not be done here.

Part **F** (e2e acceptance check without the live stack) covers verification
of parts A–D.

## API routes

The board routes live under ``/api/board`` (``board_api.py``). Every mutation
passes an ``expected_version`` (the row's ``updatedAt`` as read by the caller);
the store's optimistic lock raises ``ConflictError`` (HTTP 409) on a drift,
and every write is audited to ``board.changelog`` by the store. ``board_plan``
holds the server-side guards (the task status is re-derived from the event log,
never taken from the browser); ``board_ready`` the readiness checks, the start
guard and the rollback; ``board_stats`` the mission/task cards; ``board_think``
the thinking-policy statistics.

Global error envelope (``_error``, board_api.py:47): all error responses are
``{"status": "failed", "error": {"code", "message"}, "code", "status_code"}``.
The exception→status map is registered in ``add_board_routes`` (board_api.py:152):
``ConflictError``→409, ``GuardError``→403, ``MissionIdError``→400, ``KeyError``→404,
``ValueError``→400, ``RuntimeError``→503. A 503 is also raised directly when the
store is unavailable (``_store_guard``, board_api.py:75) — the reason carries no
DSN.

### Task views (part A)

`TaskView` (board.py:107) carries, per task:
`task_id`, `task_index`, `title`, `objective`, `status`, `attempt`,
`review_score`, `review_reason`.
`status` is one of `waiting`, `running`, `review_pending`, `accepted`,
`not_accepted`, `completed`, `failed`.

**A task view does NOT return runs.** It has only the fields above — no list
of run ids, and no per-attempt detail. (Per-attempt run detail is in the
per-task *stats* response, see below.) It **does** return the Reviewer score
(`review_score`) and the Reviewer reason (`review_reason`).

The task views are embedded in two responses:
- `GET /missions/{mission_id}/plan` → `tasks` (a list of TaskView dumps).
- `GET /missions/{mission_id}` → `task_views` (a list of TaskView dumps).

### Routes used by parts A–D

**GET plan** (board_api.py:317) — `GET /missions/{mission_id}/plan`
- Body: none (query only; no params used).
- Response: `{"plan": {...}|None, "version": str|None, "tasks": [TaskView...]}`.
- Errors: 404 (mission not found), 503 (store unavailable).

**Task views** (covered above): `GET /missions/{mission_id}` and
`GET /missions/{mission_id}/plan` both embed the per-task TaskView list.

**GET task stats** (board_api.py:845) — `GET /missions/{mission_id}/tasks/{task_key}/stats`
- Body: none.
- Response: one task object (board_stats.py:293) with `state_key`, `mission_id`,
  `task_id`, `status`, `runs` (the task's aggregated runs), `worker_hours`,
  `reviewer_minutes`, `architect_minutes`, token fields, cost fields,
  `tok_per_s`, `cache_ratio`, `compactions`, thinking fields,
  `max_attempts_per_task`, `attempts` (count), `attempt_seconds` (per-attempt
  seconds), and `attempts_detail` (a list of `{attempt, seconds, runs}` —
  **this** is where the per-attempt run ids appear).
- Errors: 404 (mission or task not found), 503.

**GET mission stats** (board_api.py:828) — `GET /missions/{mission_id}/stats`
- Body: none.
- Response: the mission card (board_stats.py:223) — the mission's aggregated
  runs, status, per-task `attempts` / `last_review_score` / `last_review_reason`
  (a `tasks` map), and all cost/token/thinking figures. Cached
  (10 min finished / 15 s running).
- Errors: 404, 503.

**Plan write routes** (board_api.py). All read the plan via `_plan_guard`,
then run the `board_plan` guard. The shared version check
(`_check_version`, board_plan.py:132) raises **409** when
`expected_version` is missing or differs from the stored version —
**except delete**, which never answers 409 (see below).

How they guard ran tasks (**403**): the server re-derives each task's status
from the live row + event log. A task that is `running`, `review_pending`,
`accepted`, `completed` or `failed` is read-only (board_plan.py:188) and
rejects content edits with 403. `review_pending`/`not_accepted` allow only
the hint field; `not_accepted` additionally allows a retry. The plan write
routes that guard this way:

- **edit** (board_api.py:328) — `POST /missions/{mission_id}/tasks/{task_key}`.
  Body: `expected_version` + content fields (`title`, `objective`,
  `acceptance_criteria`; `who` ignored). Response:
  `{"plan", "changelog"}`. Errors: 403 (read-only / not-editable field),
  404 (task not in plan), 409 (version drift).
- **add** (board_api.py:348) — `POST /missions/{mission_id}/tasks`.
  Body: `expected_version`, `title`, `objective`, `acceptance_criteria`,
  `after_key`, `who`. Response: `{"plan", "changelog"}`.
  Guards (403): cannot insert at/before the live `task_index`. Errors: 400
  (title required), 403, 404, 409.
- **delete** (board_api.py:367) — `DELETE /missions/{mission_id}/tasks/{task_key}`.
  Query: `confirm` (default false). Body: none. The route takes no
  `expected_version` (board_api.py:383–386 passes the stored version as both),
  so it never answers 409. When the mission live status is `active` it needs
  `?confirm=true` or answers 403 "the mission is running; pass confirm=true"
  (board_api.py:122–125, 374). Guards (403): only pending (index > live
  `task_index`) tasks can be deleted.
  Response: `{"plan", "changelog"}`. Errors: 400 (not in plan), 403.
- **duplicate** (board_api.py:403) — `POST /missions/{mission_id}/tasks/{task_key}/duplicate`.
  Body: `expected_version`, `after_key`, `who`. Response: `{"plan", "changelog"}`.
  Guards (403): source must be pending; insert must be after live `task_index`.
  Errors: 403, 404, 409.
- **move** (board_api.py:386) — `POST /missions/{mission_id}/tasks/{task_key}/move`.
  Body: `expected_version`, `to_position`, `who`. Response: `{"plan", "changelog"}`.
  Guards (403): task must be pending and target position past live `task_index`.
  Errors: 403, 404, 409.
- **retry** (board_api.py:448) — `POST /missions/{mission_id}/tasks/{task_key}/retry`.
  Body: `expected_version`, `who`. Response: `{"plan", "changelog"}`.
  Guard (403): only a `not_accepted` task may be retried (board_plan.py:468);
  the live state's `task_attempt` is reset to 1.
  Errors: 403, 404, 409.
- **hint** (board_api.py:421) — `POST /missions/{mission_id}/tasks/{task_key}/hint`.
  Body: `expected_version`, `hint`, `extra`, `who`. Response:
  `{"plan", "override"}`.
  Guards (403): `waiting` and `review_pending` allowed; `not_accepted` is the
  only status where the hint is the sole editable field (board_plan.py:432).
  Errors: 403, 404, 409.

**POST thinking/policy** (board_api.py:857) — `POST /thinking/policy`
- Body: `policy`. Response: `{"valid": bool, "problems": [str...]}`.
  No store/store-guard errors (pure validation).

**POST thinking/variant** (board_api.py:862) — `POST /thinking/variant`
- Body: `policy`, `task_number`. Response: `{"variant": str|None}`.

**POST thinking/layout** (board_api.py:867) — `POST /thinking/layout`
- Body: `policy`, `n`. Response: `{"layout": [str|None, ...]}` — the variant
  for each of the first `n` tasks. **A layout route exists** (board_api.py:867).

**POST thinking/stats** (board_api.py:872) — `POST /thinking/stats`
- Body: `attempts` (rows), `control` (default `"off"`), `min_arm` (default 100).
  Response: `{"variants": [...]}` — per-variant `{variant, attempts, passes,
  acceptance_share, minutes_per_attempt, thinking_tokens,
  share_with_thinking, share_without_thinking, uplift}` where `uplift` is the
  pass-rate uplift vs the control in percentage points with a 90% bootstrap
  interval, or `{"text": "мало данных, нужно ещё N попыток на ветку"}` when
  below `min_arm`.

**POST thinking/thompson** (board_api.py:878) — `POST /thinking/thompson`
- Body: `attempts`, `seed` (default 7), `samples` (default 10000).
  Response: `{"thompson": [...]}` — per-variant
  `{variant, attempts, passes, p_best, rate_per_hour, suggested_weight}`.

**GET readiness** (board_api.py:474) — `GET /missions/{mission_id}/readiness`
- Query: `host_visible` (default true), `preflight_max_age_seconds`
  (default 3600). Body: none.
- Response (board_ready.py:342): `{"mission_id", "status", "items": [...],
  "errors": [...], "confirmed_needed": bool}`. Each item is
  `{check, status, reason}` (`status` ∈ ok/warning/error).
- Errors: 404, 503.

**POST start** (board_api.py:488) — `POST /missions/{mission_id}/start`
- Query: only `confirmed` (default false). Body: none. It has no
  `host_visible` / `preflight_max_age_seconds` query — those exist only on
  `GET /readiness`. The handler calls `collect_readiness` with the defaults
  (`host_visible=True`, `preflight_max_age_seconds=3600`).
- Guard: re-runs the full readiness (same checks as `/readiness`); any error
  item **blocks** the start unless `confirmed=true` (board_ready.py:359).
- Response (board_ready.py:360): `{"allowed": bool, "blocked_by": [...],
  "confirmed": bool, "reason": str}`.
- Errors: 404, 503.

**GET preflight** (board_api.py:745) — `GET /missions/{mission_id}/preflight`
- Body: none. Response: `{"preflight": [...]}` (the recorded preflight rows).

**POST preflight** (board_api.py:750) — `POST /missions/{mission_id}/preflight`
- Body: `items` (a list). Response: `{"preflight": [...]}`.
- Errors: 400 (items not a list), 503.

**GET changelog** (board_api.py:822) — `GET /changelog`
- Query: `limit` (default 200, 1–1000). Body: none.
- Response: `{"changelog": [entry...]}` — the audit log entries.

**POST rollback** (board_api.py:526) — `POST /changelog/{changelog_id}/rollback`
- Query: `who` (default ""). Body: none.
- Writes the entry's `before_json` back as a new change. Only honoured while
  the current row still equals the entry's `after_json` (board_ready.py:397):
  any later change raises **409**. The rollback is its own changelog row.
- Response: `{"action": "rollback", "restored": {...}, "changelog_id"}`.
- Errors: 404 (entry/row not found), 400 (entry carries no value / wrong
  entry kind / restores no fields), 409 (row changed after the entry).

### Backend gaps

Closed (T005, 2026-10-17):

1. **The edit route accepted only `title`, `objective` and
   `acceptance_criteria`** (`EDITABLE_TASK_FIELDS`, board_plan.py).
   `verification` and `scope` are now editable for a `waiting` task:
   `EDITABLE_TASK_FIELDS` carries them and the shared guard in
   `board_plan._validate_editable_fields` allows them. `hint` stays out of
   the edit route: it is set through a separate route,
   `POST /missions/{id}/tasks/{task_key}/hint` (board_api.py:421).
   Covered by `tests/test_board_api_part2.py`.

For every other route a part needs (A–D), nothing is missing:
- A: task views (`GET /missions/{id}` and `GET /missions/{id}/plan`) and
  `GET .../tasks/{task_key}/stats`.
- B: all plan write routes (edit, add, delete, duplicate, move, retry, hint).
- C: `POST /thinking/policy`, `/thinking/variant`, `/thinking/layout`,
  `/thinking/stats`, `/thinking/thompson`.
- D: `GET /readiness`, `POST /start` (guard + preflight), `GET`/`POST
  /preflight`, `GET /changelog`, `POST /changelog/{id}/rollback`.

The task view returns the Reviewer score, the reason, and `attempt`, but not
the runs (per-attempt run ids live in the per-task stats `attempts_detail`).
A variant-layout route exists (`POST /thinking/layout`).

### The plan_json overwrite issue

A running mission's plan can be overwritten by the n8n workflow: the workflow
re-writes the mission's `plan_json` on a running mission, so an operator edit
of a running mission's plan can be lost. The warning to show in the board UI:

> правка плана идущей миссии может быть перезаписана воркфлоу: делайте её в паузе

Constraint: show this warning as a visible banner on the plan of a mission whose
state is active, and keep the edit controls available (this is Part B).

Out of scope: no other gap is recorded in the 2026-10-17 entry.

## Frontend conventions

### IIFE header and scope wrapping

Every `board*.js` is wrapped in an IIFE and starts with the marker
`/* board-scope-wrapped */` on line 1, then `(function () {` and `"use strict";`
(board.js:1–2, board_logic.js:1–2, and the rest the same). The body is closed
by `})();`. The comment block at the top of each file states what the view
does and how it is loaded by `board.html` via `<script src="/static/...">`.
Each file keeps its own local `esc(v)` fallback (board.js:20 `localEsc`,
board_logic.js:23, board_actions.js:28, board_claude_md.js:29 `claudeMdEsc`,
board_dnd.js:37, board_mission.js:35, board_mission_save.js:30,
board_queue.js:32) that delegates to `BoardLogic.esc` when that script loaded
and otherwise does the `& < > " '` entity replacement itself.

### Taken top-level names and globalThis exports

The files never `declare` a shared top-level function; each IIFE is its own
scope and only a handful of globals are assigned. The full set of
`globalThis` assignments across `board*.js`:

| file | exported global(s) |
|---|---|
| board.js:99 | `globalThis.getJSON = getJSON` |
| board_logic.js:230 | `globalThis.BoardLogic = BoardLogic` |
| board_actions.js:451 | `globalThis.BoardActions = BoardActions` |
| board_claude_md.js:230–231 | `globalThis.BoardClaudeMd = BoardClaudeMd`, `globalThis.BoardClaudeMdView = BoardClaudeMdView` |
| board_dnd.js:365 | `globalThis.BoardDnd = BoardDnd` |
| board_mission.js:324–326 | `globalThis.BoardMission = BoardMission`, `globalThis.BoardMissionView = BoardMissionView`, `globalThis.renderMission = renderMission` |
| board_mission_save.js:589 | `globalThis.BoardMissionSave = BoardMissionSave` (only when `document` is defined) |
| board_queue.js:222–223 | `globalThis.BoardQueue = BoardQueue`, `globalThis.renderQueue = renderQueue` |

Each view also defines a `module.exports` CommonJS object (UMD-style) for use
in node: `BoardLogic` (board_logic.js:225), `BoardMissionView`
(board_mission.js:316), `BoardMissionSave` (board_mission_save.js:579),
`BoardActions` (board_actions.js:426), `BoardDnd` (board_dnd.js:351),
`BoardClaudeMdView` (board_claude_md.js:223), `BoardQueueView`
(board_queue.js:217). The `BoardMissionView` export object (board_mission.js:316)
carries: `renderMission, formHtml, fieldRow, presetButtons, fmtLines, fmtJson,
greyNote, render, BoardMission, attachPresetButtons, MIDRUN_CONFIG_FIELDS,
NON_EDITABLE_CONFIG_FIELDS, FORM_ROWS, esc`.

The `BoardLogic` export object (board_logic.js:225) carries:
`esc, reorder, dropPosition, mergeServerIntoForm, parseHash, buildHash,
diffConfig, thinkLayout`.

Note: `BoardTask` is referenced by the shell's route() (board.js:65–66) but is
**not defined in any `board*.js`** — it is only a placeholder the shell calls
if it happens to be a function, otherwise it prints "Task view not loaded yet."
No `board_task.js` exists. This is the gap for part A.

### The BoardTask call signature the shell expects

board.js:64–69 (in `route()`): when `parsed.task_id` is set the shell calls
`await BoardTask(parsed.mission_id, parsed.task_id)` — i.e. the signature the
shell expects for `#board/<mission_id>/<task_id>` is
`BoardTask(mission_id, task_id)`. `parseHash` (board_logic.js:132) yields
`{ mission_id, task_id }` from the fragment; `buildHash(mission_id, task_id)`
produces `#board/<mission_id>/<task_id>` (board_logic.js:150).

### getJSON signature and the data-control convention

`getJSON(url)` (board.js:30–40) is the shell's shared fetch helper, exported as
`globalThis.getJSON`. It does `fetch(url, { cache: "no-store" })`, reads
`res.json()` (caught to `{}`), and on `!res.ok` throws an `Error` carrying
`err.status` (the HTTP status) and `err.body` (the parsed body). Otherwise it
returns the parsed JSON body. Views call it as `await fetchJson(url)` — but
board_mission.js/board_mission_save.js define their own local `fetchJson`
(board_mission.js:302, board_mission_save.js:536) rather than calling the
shell's `getJSON`.

The data-control convention: every rendered input carries
`data-key` (the field name) and `data-kind` (mission/queue/config) plus
`data-control` (text/textarea/listarea/bool/number/json). `board_mission.js`
renders each field through `fieldRow(kind, key, label, control, shown, running, reason)`
(board_mission.js:144) and the form layout is the `FORM_ROWS` array
(board_mission.js:80–100). `board_mission_save.js` reads the form back with
`readForm(box)` (board_mission_save.js:72–94): it walks
`box.querySelectorAll("[data-key]")` and, per control kind, produces
- checkbox → boolean `el.checked`;
- `listarea` → array of trimmed, non-empty lines;
- `json` → parsed object (or the raw string if not JSON);
- `number` → `Number` (or null when empty);
- otherwise → `String(el.value)`.

`initialValue(kind, key, data, control)` (board_mission_save.js:103–136)
returns the same canonical shape from the server data so `formDirty` and the
diff compare apples-to-apples. Textarea values are normalised CRLF→LF
(`String(v).replace(/\r\n?/g, "\n")`) so the diff is CRLF-normalised.

### The diff shape

`BoardLogic.diffConfig(a, b)` (board_logic.js:170–186) compares two config
objects field-by-field at the top level: it unions the keys, sorts them, and
emits `{ field, old, new }` for each key whose `JSON.stringify(old) !==
JSON.stringify(new)` (missing side is `null`). Values compare with deep
equality via `JSON.stringify` (the comment notes the shortcut: nested-object
key order is significant, but configs are flat so no deep walk is needed).
`board_mission_save.js`'s `currentDiff(box)` (board_mission_save.js:167–181)
calls `BoardLogic.diffConfig(SaveState.initial, readForm(box))` and falls back
to an inline equivalent that also sorts by field. The diff is rendered by
`diffHtml(diff, labels)` (board_mission_save.js:184–200): one row per changed
field, `label: old → new`, arrays joined with `", "`, objects via
`JSON.stringify`.

### The node test harness (DOM stub, fetch stub)

`board_mission.test.js` runs the view in plain node with stubs, no DOM library:
- It `require`s the view: `const BM = require(path.join(__dirname, "..", "src", "claude_runner", "board_mission.js"))` (board_mission.test.js:23).
- `check(name, cond)` (line 25) throws on failure; `has(html, needle)` (line 28) is `indexOf !== -1`.
- Pure renders are tested by calling `BM.renderMission(data)` and asserting on
  the returned HTML string (the keys list, the escaped values, the greyed-row
  count).
- For `render()` it stubs `globalThis.document` with a fake
  `getElementById`/`createElement` (board_mission.test.js:188–200):
  `getElementById` returns a `boardStub` (with `appendChild`) for `board`, a
  `stateStub` (with `textContent`/`className`) for `boardstate`, and null for
  `boardmission` first (so the container is created), then a second stub
  returns the already-appended element (board_mission.test.js:210–214).
- For the `BoardMission()` fetch flow it stubs `globalThis.fetch`
  (board_mission.test.js:228–237) returning canned
  `{ ok: true, json: async () => ... }` for `/api/board/missions/m1` and
  `/api/board/queue`, and re-stubs `globalThis.document` with a
  `boardmissionStub` carrying `innerHTML` and `querySelectorAll`
  (board_mission.test.js:238–247).
- At the end it `delete globalThis.document` / `globalThis.fetch` to clean up,
  and prints `all ok`.

The other node tests (`board_logic.test.js`, `board_actions.test.js`,
`board_queue.test.js`, `board_mission_save.test.js`) use the same pattern of
`require` + `globalThis.document`/`globalThis.fetch` stubs.

### The static test rules

`test_board_js_static.py` is a pure static check over every
`src/claude_runner/board*.js` file (read in parts):
1. `_check_esc` (lines 59–72): every `esc`-named function that does the
   `.replace(...)` itself must escape `& < > " '` to the five entities; an
   `esc` name that only delegates (calls `localEsc`/`esc`/`BoardLogic.esc`) is
   accepted.
2. `_check_templates` (lines 75–85): every backtick template literal that
   builds a tag (contains `<tag`) must wrap each `${...}` interpolation in an
   `esc(` / `Esc(` call.
3. `_check_innerhtml` (lines 88–103): every `.innerHTML =` assignment must
   carry `esc(` / `Esc(` in its right-hand side, be a call of a render
   function (covered by rule 2), or be `JSON.stringify` (never HTML).
4. `test_board_html_no_inline_handlers` (lines 136–141): `board.html` must have
   no inline event-handler attributes (`on...="..."`).

The file is read in 1200-char parts with a 200-char overlap
(`_parts`, lines 106–117) so no backtick string is split across a part
boundary.

### How the API test builds the app with an in-memory store and a manager stub

`test_board_api.py` builds the runner app with an in-memory board store
instead of a real DSN (the fixture `board_client`, lines 23–47):
1. `store = InMemoryBoardStore()` (imported from `claude_runner.board`,
   line 15) and seeds it directly: `store._missions["m1"] = Mission(...)`,
   `store.set_mission_config("m1", {...}, None)`, `store._queue["m1"] =
   QueueEntry(...)`, `store._queue_order.append("m1")`, and
   `store.upsert_live_state("sk1", None, plan={...}, status="queued")`.
2. `from claude_runner.api import create_app; app = create_app(settings)`
   (line 40–41), then **swaps in the store**: `app.state.board_store = store`
   (line 42) — the DSN is unset, so the default store is the unavailable stub;
   the in-memory store replaces it.
3. `await _run_lifespan(app)` (line 44, helper at lines 18–20:
   `async with app.router.lifespan_context(app): pass`) runs the app's
   lifespan once.
4. `transport = ASGITransport(app=app)` and an `AsyncClient(transport=transport,
   base_url="http://test")` (httpx) is used to hit the routes
   (lines 45–47).

The `settings` fixture comes from `tests/conftest.py` (lines 59–71): it builds
`Settings.from_env(env)` with a `CLAUDE_RUNNER_DATA_DIR`, a `fake_bin`
(claude binary stub), a `workspace` path, and an explicit `interrupt_grace_seconds`.
The "manager stub" is the `fake_bin` (a fake claude binary path) used by the
settings fixture so no real claude process is needed.

To check 503 behaviour, `TestUnavailableStore` (lines 50–73) instead builds the
app **without** swapping the store (`create_app(settings)` with no DSN) and
asserts the board routes answer 503 with `code` `"unavailable"` and a reason
that says "не задан" but carries no DSN.

## Part 1 status

Part 1 files, checked by presence on disk (2026-10-18):

- present: `src/claude_runner/board_views.js`, `board_view_projects.js`, `board_view_kanban.js`, `board_view_grid.js`, `board_i18n.js`, `docs/board-views.md`
- Part 1 views: `proj`, `kan`, `grid` are implemented. `lane` and `spr` are placeholders that print `tr("part2.note")` ("Coming in part 2"), registered in `board_views.js`.
- The kanban `open(id)` hook calls `BoardViews.onKanbanOpen(id)`, currently a no-op. See `docs/board-views.md` "Part 2 hooks".

## board-ui2 status

Presence on disk, one line per file:

- present: `src/claude_runner/board_task.js`. Publishes `globalThis.BoardTask` (line 184) and `globalThis.BoardTaskView` (line 185).
- present: `src/claude_runner/board_tasks.js`. Publishes `globalThis.BoardTasks` (line 203).
- present: `src/claude_runner/board_plan_edit.js`. Publishes `globalThis.BoardPlanEdit` (line 497).
- present: `src/claude_runner/board_think.py`. Public `def` functions: `validate_policy` (190), `variant_for_task` (207), `layout_preview` (227), `variant_stats` (405), `thompson_best` (475).
- present: `src/claude_runner/board_think_ui.js`. Publishes `globalThis.BoardThinkUI` (line 354).
- present: `src/claude_runner/board_ready.py`. Public `def` functions: `check_working_folder` (86), `check_required_files` (106), `check_llama_model` (164), `check_plan_present` (209), `check_live_state_present` (219), `check_not_running` (227), `check_preflight_fresh` (235), `start_guard` (351), `rollback` (397), `validate_mission_id` (452), `create_mission` (563), `save_template` (642).
- present: `src/claude_runner/board_ready_ui.js`. Publishes `globalThis.BoardReadyUI` (line 221).
- present: `src/claude_runner/board_history_ui.js`. Publishes `globalThis.BoardHistoryUI` (line 227).
- present: `src/claude_runner/board_plan.py`. Public `def` functions: `update_task` (214), `add_task` (252), `delete_task` (309), `move_task` (338), `duplicate_task` (376), `set_task_hint` (415), `request_retry` (452), `update_mission_config` (490), `compose_task_text` (524), `task_overrides_hint` (546), `editable_task_fields` (556).
- absent: `src/claude_runner/board_queue_seed.py`
- present: `tests/e2e/test_board_views_e2e.py`
- absent: `tests/e2e/serve_board.py`

## Mockup rules

Source: `docs/board-views-mockup.html`. Only pending tasks (`st === "g"`) take part in ordering. The order is the list of pending ids, from `pend()` (line 194).

- `closure(m, id, down)` (line 197): walks the dependents (`down` true) or the dependencies (`down` false) of `id` through pending tasks only. It returns the ids it reaches, and it does not include `id`.
- `violations(m, order)` (line 198): returns the pairs `[d, id]` where a dependency `d` sits after the task `id` that needs it in `order`.
- `moveOrder(order, ids, target)` (line 199): removes `ids` from `order` and reinserts them as a block at the index `target`, counted among the non-moved items. The block keeps its relative order.
- `wouldCycle(m, from, to)` (line 200): depth-first walk over dependents from `from`. Returns `true` when it reaches `to`, meaning that adding "`from` depends on `to`" would close a cycle.
- `tryMove(m, id, toIndex)` (lines 201-204):
  1. Returns early when `id` is not pending or the index is unchanged.
  2. Tries a plain move with `moveOrder(order, [id], ...)`. If it has no violations, it applies it with `applyOrder` and clears the message.
  3. Otherwise it builds a group `[id] + closure(m, id, down)`, moves the group, and checks its violations. The group is offered as "Move together" only when `bad` is empty. The message stores `down`, `dependents`, `deps`, `group`, `order` and `bad`.
- `applyOrder(m, order, mv)` (line 205): rewrites `m.tasks` as the non-pending tasks followed by the pending tasks in `order`, and marks the ids in `mv` as moved.
- Handlers in `wire()` (line 249):
  - `data-add` onchange (line 259) rejects a dependency with `wouldCycle` and sets `msg["cyc"+id]`. Otherwise it pushes the dependency, and if `violations` is non-empty it re-sorts the pending order before calling `applyOrder`.
  - `data-wc` (line 260) clears the warning.
  - `data-wg` (line 261) applies the stored group order.
  - Drag drop on a task row (line 266) calls `tryMove(m, dtask, index)`.

### Double-click paths

Single click only changes selection or highlight. Double click navigates. Handlers are in `wire()`:

- Project header `.prh` (line 250): click toggles `.sel` on the project and sets `selP`. Double click sets `pf` and `selP`, then calls `go("kan")`. This is the project-to-Kanban path.
- Mission in a project backlog `.prb .kc` (line 251): click sets `selM` and highlights the card. Double click sets `openM` and calls `go("mission", "proj")`.
- Kanban card `.kb .kc` (line 252): click sets `selM` and highlights the card. Double click (ignored when the target is a button) sets `runsFor` and calls `go("runs", "kan")`. The runs view then gets its mission filter set automatically.
- Grid row `tr.row` (line 253): click sets `selM` and highlights the row. Double click sets `openM` and calls `go("mission", "grid")`.
- Sprint backlog mission `[data-sp]` (line 254): click sets `selSprint` and highlights the item. Double click sets `openM` to `e.dataset.sp` and calls `go("mission", "spr")`.

## I18N keys

Copied from the mockup `I18N.en` object (line 118, and the `lane.hint` value on line 125). Values are verbatim.

| Key | English text |
|---|---|
| `lane.hint` | A row is a mission. In Waiting the tasks can be dragged: the order respects dependencies (← T001 means after T001). If a move breaks them you can cancel it or move the task together with its dependents. Accepted and running tasks are locked. |
| `spr.hint` | Backlog on the left, the selected mission as a sprint in the middle, numbers and obstacles on the right. A double click on a mission in the backlog opens it with its tasks. |
| `spr.back` | Backlog and current |
| `spr.wait` | Waiting |
| `spr.prog` | In progress |
| `spr.done` | Done |
| `m.runs` | Runs of this mission |
| `m.hint` | Drag the unfinished tasks (⠿). A task cannot go after one that depends on it, or above one it depends on: you can cancel or move it together with the dependents. A new dependency that closes a cycle is rejected. |
| `t.task` | Task |
| `t.desc` | Description |
| `t.status` | Status |
| `t.att` | Att. |
| `t.stat` | Statistics |
| `t.dep` | Depends on |
| `t.need` | Needed by |
| `runs.crumb` | Runs dashboard |
| `runs.filter` | mission: {id} |
| `runs.auto` | the mission filter is set automatically |
| `runs.empty` | No runs yet |
| `w.down` | «{id} {title}» is needed by {list}: it cannot be placed after them. |
| `w.up` | «{id} {title}» depends on {list}: it cannot be placed above them. |
| `w.group` | It can be moved together with {rel}: {list} ({n}). |
| `w.bad` | Moving together is not possible either (it would break: {pairs}). |
| `w.cycle` | Cycle: {dep} already depends on {id} (directly or through other tasks), so {id} cannot depend on {dep}. The dependency was rejected; the order did not change. |
| `btn.group` | Move together |
| `btn.cancel` | Cancel |
| `btn.ok` | Got it |



- Mockup `LANGS` (line 116): `en`, `ru`, `de`. The mockup's `I18N` object (line 117) has `en` (line 118) and `ru` (line 135). There is no `de` table, so `de` falls back to English.
- Lookup `t(k, p)` (line 155): `I18N[LANG][k] || I18N.en[k] || k`, then `{name}` placeholders are filled from `p`.
- Keys used by the ordering warnings: `w.down`, `w.up`, `w.group`, `rel.dependents`, `rel.dependencies`, `w.bad`, `btn.group`, `btn.cancel`, `btn.ok`, `w.cycle`.
- Keys used by the runs view: `runs.crumb`, `runs.filter`, `runs.auto`, `runs.empty`.
- Keys used by the translation editor: `tr.*`, including `tr.saved` with `{n}` and `{total}`.
- Production side: `board_i18n.js` `t()` (see `docs/board-views.md` "Frontend hooks"). Server routes are listed under "API shapes".

## API shapes

Documented shapes are from `docs/board-views.md`. Live verification was not possible: `http://127.0.0.1:8701` refused connections (curl exit 7) on `/api/board/summary`, `/api/board/missions` and `/api/runs`. Treat every shape below as unverified against the running server.

- `GET /api/board/summary` (`board_api.py:1003`): `{missions: [...], projects: [...]}`. A mission has `mission_id`, `name`, `state_group`, `position`, `project {key, name, rule}`, `tasks[{id, title, status, attempts, deps}]`, `attempts`, `hours`, `tokens`, `cache_ratio`, `cost`, `tok_s`, `thinking_share`. Figures are `null` when the runner fails; the route still returns 200.
- `GET /api/board/project-rules` (`board_api.py:1069`): `{rules: [{name, pattern, position}], version}`.
- `PUT /api/board/project-rules` (`board_api.py:1075`): needs `X-AHAWR-Dashboard: 1`. Body `{rules, expected_version, who}`. Returns 409 `conflict` on a stale version.
- `GET /api/board/i18n` (`board_api.py:1090`): `{languages: [{code, name, enabled}], strings: {lang: {key: text}}}`.
- `POST /api/board/i18n/languages` (`board_api.py:1096`): needs the header. Body `{code, name}`, with `code` matching `^[a-z]{2,8}$`.
- `PUT /api/board/i18n/{lang}/{key}` (`board_api.py:1109`): needs the header. Body `{text, who}`. An empty `text` deletes the row. Unknown language gives 404.
- `GET /api/board/missions/{mission_id}` (`board_api.py:348`): mission detail with `updated_at`, used as the default `expected_version`.
- `GET /api/runs` (`dashboard.py:150-151`): the runs list, default port 8701 (`dashboard.py:389`).
- Errors: one envelope from `_error` (`code`, `status_code`), with `guard` 403, `conflict` 409, `bad_request` 400, `not_found` 404, `unavailable` 503.

## Backend hooks

- Dashboard guard: `_require_dashboard_header` (`board_api.py:93`). The store guard `_store_guard` (`board_api.py:78`) returns 503 when the store is unavailable.
- Plan edit guard: `_plan_guard` (`board_api.py:142`) reads the live state and events. The guarded task update is at `board_api.py:463-492`. It runs `board_plan.update_task` with `expected_version`, then `store.upsert_live_state`, and returns `{plan, changelog}`.
- Changelog: `GET /changelog` (`board_api.py:968`). The store appends each write to `board.changelog`: `board.py:216`, `board.py:688`, `board_pg.py:585`.
- Plan logic: `board_plan.py:124-138` (normalize, check_version), `board_plan.py:338` (`move_task`).
- I18N store methods: `board.py:431` / `1163` (`put_i18n_string`), `435` / `1193` (`add_i18n_language`), `443` / `1205` (`seed_i18n`). `board_pg.py:123`, `126`, `133` and `1375`, `1408`, `1435` carry the same three.
- Postgres table constants: `board_pg.py:44-57`.
- Run ids of a mission: `board_stats.py:27-38` (`_mission_run_ids`), used at `board_stats.py:195`.
- Summary figures are cached in `board_stats` (600 s completed or failed, 15 s otherwise).

## Runs page hash/tags

Source: `src/claude_runner/dashboard.html`.

- Hash parse: `urlFilter()` (lines 417-428). A hash starting with `#board` returns an empty filter. The run-filter part is the text before `#run=`, parsed as `field=value&...`.
- `writeUrl()` (around line 428): skips the write when `TAB === "board"`, because the board tab owns the hash. It writes `#run=<id>` for the selected run.
- Tag row: markup at line 230 (`#tags`) and the hint at line 231 ("Fields are combined with AND; values of one field are combined with OR."). Rendering at lines 557-585: one chip per field and value, a `session` chip, and a `clear all` button. Chip removal is handled at line 576.
- Board tab sync: `boardFrameUrl()` (line 1049), `syncTab()` (line 1067), `hashchange` listeners (lines 1076-1084). `fromHash()` (line 1083) selects the run from `run=...`.
- Mockup runs view: `vRuns` in `board-views-mockup.html` shows a filter tag `runs.filter` with `mission: {id}` and the text `runs.auto`. The tag is not interactive in the mockup.
- The board's runs deep link `#board/<id>` and the run hash `#run=<id>` both live in `location.hash`. Keep the `#board` guard in `urlFilter()` when changing either.

## Open items for this note

- Live API shapes are unverified (server on 8701 refused connections). Re-check the summary, project-rules and i18n responses once the server runs.
- Line numbers refer to the working-tree copies on 2026-10-18 and will drift with edits.
