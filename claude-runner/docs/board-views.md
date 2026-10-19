# Board views: summary, projects, i18n

The mission board's dashboard reads one summary document and redraws from it. This page is the reference for that summary, the project rules that group missions, the translation strings and their routes, the frontend views and hooks, and how the operator applies the database changes. Paths are relative to `claude-runner/` unless shown otherwise. All values in the examples are made up.

## Summary: `GET /api/board/summary`

One response for the whole board. Every mission is listed with its state group, queue position, project, task list and figures. A failing or absent runner gives `null` figures; the route still answers HTTP 200.

Request:

```
GET /api/board/summary
Accept: application/json
```

Response (truncated to one mission and one project):

```json
{
  "missions": [
    {
      "mission_id": "m-1042",
      "name": "Example mission",
      "state_group": "running",
      "position": 2,
      "project": {"key": "example-app", "name": "example-app", "rule": "*/example-app"},
      "tasks": [
        {"id": "T001", "title": "Parse config", "status": "accepted_retry",
         "attempts": 2, "deps": []},
        {"id": "T002", "title": "Write tests", "status": "waiting",
         "attempts": 0, "deps": ["T001"]}
      ],
      "attempts": 3,
      "hours": 1.25,
      "tokens": 84210,
      "cache_ratio": 0.62,
      "cost": 0.42,
      "tok_s": 38.5,
      "thinking_share": 0.31
    }
  ],
  "projects": [
    {"key": "example-app", "name": "example-app", "rule": "*/example-app",
     "mission_ids": ["m-1042"]}
  ]
}
```

### Fields

- `state_group`, `position`: from `store.mission_groups()` and `store.queue()`. `position` is `null` when the mission is not queued.
- `project`: see "Project matching" below.
- `tasks[].id`: only ids matching `^T[0-9]+[A-Z]*$` are listed; other ids are skipped.
- `tasks[].status`: the card status mapped to `waiting`, `running` (also used for `review_pending`), `accepted`, and so on. An accepted task with more than one attempt becomes `accepted_retry`.
- `tasks[].deps`: the `dependencies` of the task in the stored plan.
- `attempts`: sum of the attempts of every task.
- `hours`: worker hours (`worker_hours`).
- `tokens`: input plus output tokens.
- `cache_ratio`: as computed by `board_stats`.
- `cost`: `cost_usd` from `board_stats`, rounded to cents.
- `tok_s`: tokens per second (`tok_per_s`).
- `thinking_share`: `thinking_runs / thinking_steps`, or `null` when there are no thinking steps. Despite the name, this is a ratio of counts, not a share of time or tokens.
- Any figure is `null` when the runner is unreachable or raises.

### Figure caching

The figures come from `board_stats.mission_figures`, cached per mission in `board_stats`: 600 s for a `completed` or `failed` mission and 15 s otherwise. The per-mission stats route (`GET /missions/{mission_id}/stats`) keeps its own cache in `board_api.py` with the same TTLs.

## Project matching

Project rules are stored and read by `board_api.py` (`_mission_project`, `_summary_projects`):

- A mission's working directory is normalized (backslashes to slashes, trailing slash removed).
- The stored rules are tried in `position` order with `fnmatch`. The first matching `pattern` wins; its `name` is the project key and name.
- With no matching rule, the default project is the last folder of the working directory, and its `rule` is `null`.
- `projects` lists every project that has at least one mission, sorted by key.

## Project rules

### `GET /api/board/project-rules`

Response:

```json
{
  "rules": [
    {"name": "example-app", "pattern": "*/example-app", "position": 1}
  ],
  "version": "v-20261018-03"
}
```

### `PUT /api/board/project-rules`

Requires the header `X-AHAWR-Dashboard: 1`. Without it the route answers HTTP 403 with code `guard`.

Request:

```
PUT /api/board/project-rules
X-AHAWR-Dashboard: 1
Content-Type: application/json

{
  "rules": [
    {"name": "example-app", "pattern": "*/example-app", "position": 1},
    {"name": "demo-lib", "pattern": "*/demo-lib", "position": 2}
  ],
  "expected_version": "v-20261018-03",
  "who": "dashboard"
}
```

Each rule needs a string `name`, a string `pattern` and an integer `position`; otherwise HTTP 400. If the store refuses a stale `expected_version` with a `ConflictError`, the answer is HTTP 409 (code `conflict`). The response is the stored rules and the new version, as in the GET.

The whole list is replaced on each save; the rows' `updated_at` stamps make up the version (the max over the rows, `""` when the list is empty). The write is audited to `board.changelog`.

## Translation strings (i18n)

### Tables

The strings live in two `board` tables (`docs/board-schema.sql`):

- `board.i18n_languages`: `code` (primary key), `name`, `enabled` (default `true`; `enabled = false` hides the language from the picker).
- `board.i18n_strings`: `lang` + `key` (primary key), `text` (never empty), `updated_at`, `updated_by`.

`docs/board-role.sql` grants `board_operator` `SELECT, INSERT, UPDATE, DELETE` on both tables.

### `GET /api/board/i18n`

Seeds the tables once from `board_i18n_en.json` and `board_i18n_ru.json` when they are empty, then returns the store's languages and strings. The seed inserts into the empty tables only: it is idempotent and never overwrites rows the operator edited. English is always present, and any missing English key is filled from the `en` file.

Response (shape):

```json
{
  "languages": [
    {"code": "en", "name": "English", "enabled": true},
    {"code": "ru", "name": "Русский", "enabled": true}
  ],
  "strings": {
    "en": {"app.title": "Mission board", "btn.ok": "OK"},
    "ru": {"app.title": "Доска миссий"}
  }
}
```

### `POST /api/board/i18n/languages`

Requires `X-AHAWR-Dashboard: 1`. The `code` must match `^[a-z]{2,8}$` and the `name` must be non-empty; otherwise HTTP 400. English cannot be removed or disabled (there is no delete route, and `en` is always restored in the view). Adding an existing `code` answers HTTP 409 (code `conflict`).

```
POST /api/board/i18n/languages
X-AHAWR-Dashboard: 1
Content-Type: application/json

{"code": "de", "name": "Deutsch"}
```

Response: the same shape as `GET /api/board/i18n`. The write is audited to `board.changelog`.

### `PUT /api/board/i18n/{lang}/{key}`

Requires `X-AHAWR-Dashboard: 1`. `text` must be a string. The language must be `en` or a language already in the store; otherwise HTTP 404. An empty `text` deletes the row (the string then falls back to English on the client).

```
PUT /api/board/i18n/de/btn.ok
X-AHAWR-Dashboard: 1
Content-Type: application/json

{"text": "OK", "who": "dashboard"}
```

Response: `{"lang": "de", "key": "btn.ok", "text": "OK"}`. The write (or deletion) is audited to `board.changelog`.

## How to add a language

1. Open the translation editor (the header's "Translations" button).
2. Add the language: `POST /api/board/i18n/languages` with `{"code": "de", "name": "Deutsch"}` — it is created with `enabled: true`.
3. Translate the keys in the editor: it lists every English key, one input per key, and each saved input is a `PUT /api/board/i18n/de/{key}` with `{"text": ...}`. The "only missing" checkbox shows the keys that still have no translation; the counter shows how many are missing.
4. A key that has no translation falls back to English on the client (server, then bundled table, then the key itself) and is highlighted in the editor.

## Frontend views

The view switcher is `src/claude_runner/board_views.js`; the registered views:

- `proj` — `board_view_projects.js`. Cards per project, each with its missions; a click highlights, the arrow expands the project, a double click on the project opens the kanban filtered by it, and a double click on a mission opens it (`#board/<mission_id>`).
- `kan` — `board_view_kanban.js`. Four state columns (Running, Queued, Completed, Not completed). A click highlights a card; a double click opens the runs dashboard filtered by the mission (see "Navigation paths"). The queued column keeps the queue's drag and up/down controls and the "new mission" button.
- `grid` — `board_view_grid.js`. A sortable table of missions (state, tasks, attempts, time, cache, cost, speed).
- `lane` — `board_view_lanes.js`. A row per active mission, one column per task state: Waiting (execution order), In progress, Review, Accepted, Rejected. Only waiting tasks move; a drop goes through `BoardOrder` and then `POST .../tasks/reorder`. A drop that breaks the rules opens a warning box and nothing changes until the server accepts the order. The Review column is always empty because the summary has no review status. Double-click falls back to `#board/<mission_id>`.
- `spr` — `board_view_sprint.js`. The backlog of queued and running missions, the selected mission as a sprint with its figures, an accepted-over-time chart and its obstacles. A click highlights a backlog card; a double click opens `#board/<mission_id>`. The chart and obstacles are filled after render from `GET /api/board/missions/<id>/timeline`. Obstacle kinds `rejected_3x`, `timeout` and `interrupted` have their own labels; any other kind shows the generic `imp.t` label.

`register(name, impl)` attaches a view by name; an `impl` carries `render(body)`, `select(card)` (highlight) and `open(id)` (what a double click does). The shared loader in `board_views.js`:

- `SUMMARY_URL` is `/api/board/summary`. `fetchSummary()` sends it with `Accept: application/json` and `credentials: "same-origin"`, and throws `summary <status>` on a non-OK response.
- `REFRESH_MS` is `15000`. A timer calls `refreshOnce` at that interval.
- `refreshOnce(deps)` fetches the summary and compares its JSON with the last one. If the view is busy (for example the user is dragging a card or the mission form has unsaved changes), it stores the data, marks the view stale and returns `"deferred"`. Otherwise it redraws when the data changed or a redraw was deferred, and returns `"redrawn"`; with no change it returns `"unchanged"`.

## Frontend hooks

- `board_i18n.js` holds the strings in the browser: `t(key, params)` looks the key up in the current language, then in English (the server strings, then the bundled table, then the key itself); `{name}` placeholders are filled from `params`. `setLanguage`/`getLanguage` keep the choice in `localStorage`; `setString(lang, key, text)` writes a translation into the in-memory table (an empty text deletes it) and notifies the `onChange` listeners. `load()` fetches from `GET /api/board/i18n` once, keeps only the enabled languages, and falls back to the bundled English table when the fetch fails.
- `board_i18n_ui.js` is the translation editor opened by the `boardviews:translations` event. It reads `GET /api/board/i18n`, renders the language select, the "only missing" filter, the counter and one input per English key, and the add-language form. Its writes go through `i18nWrite`, which carries the `X-AHAWR-Dashboard: 1` header; a saved translation is also written into `BoardI18n` (`setString`), so the views show it at once.
- `board_views.js` wires the header (tabs, project chips, search, language select, theme) and the body: a click on a card calls the view's `select`, a double click calls its `open`, and without an `open` falls back to `location.hash = "#board/<id>"`.

## Ordering rules

The rules for the waiting tasks of a mission live in two twin files, kept in sync by hand: `src/claude_runner/board_order.js` (the browser, exposed as `BoardOrder`) and `src/claude_runner/board_order.py` (the server). The server uses the Python file to check every order and dependency change; the browser uses the JS file to refuse a drop before it is sent.

- Only waiting tasks move. A task that is running, accepted or rejected is locked and keeps its slot. An order may list only waiting tasks, each exactly once.
- A dependency is a pair `[dependency, dependent]`. An order breaks a dependency when the dependency comes after its dependent (`violations`). Dependencies on locked tasks are ignored for ordering.
- A new dependency that closes a cycle is refused (`would_cycle`); the answer names the closing pair.
- A move (`try_move`) returns `ok` with the new order, or one of: `unknown` (no such task), `locked` (the task is not waiting), `dependency` (the plain move breaks dependencies; the answer gives the broken pairs and a `group` of the task plus its dependents, or dependencies, that moves together) or `group-invalid` (the group move breaks them too).
- `repair_order` puts the waiting tasks in an order that respects every dependency, keeping the list order where it can. A cycle raises an error.

## Mission board routes (part 2)

All writes below require `X-AHAWR-Dashboard: 1` (or it answers HTTP 403, code `guard`) and take an optional `expected_version`; a stale one answers HTTP 409 (code `conflict`). The `who` field is recorded in the changelog.

### `POST /api/board/missions/{mission_id}/tasks/reorder`

Body: `{"order": ["T004", "T003", "T002"], "expected_version": "...", "who": "lanes"}`. `order` must list every waiting task exactly once.

| Case | HTTP | Code | Extra fields |
|---|---|---|---|
| Not a list of strings, unknown id, or not every waiting task listed | 400 | `bad_request` | |
| A locked task would move | 403 | `guard` | `locked` |
| The order breaks a dependency | 403 | `guard` | `broken` (pairs `[dependency, dependent]`) |
| Stale `expected_version` or a concurrent write | 409 | `conflict` | |

Success returns `{"plan": ..., "changelog": ..., "running_warning": false}`. The plan is written with the tasks in the new order.

### `POST /api/board/missions/{mission_id}/tasks/{task_key}/dependencies`

Body: `{"action": "add" | "remove", "dependency": "T001", "expected_version": "...", "who": "..."}`. The task's dependencies change, then the waiting tasks are put in a valid order (`repair_order`), so the reply's plan can differ from the list shown before.

| Case | HTTP | Code | Extra fields |
|---|---|---|---|
| Unknown task or dependency, or `action` not `add`/`remove` | 400 | `bad_request` | |
| The task is locked | 403 | `guard` | `locked` |
| The new dependency closes a cycle | 422 | `cycle` | `closing_pair` |
| Stale `expected_version` or a concurrent write | 409 | `conflict` | |

Success returns the same shape as the reorder route.

### `running_warning`

Both write routes return `running_warning`: `true` when the mission's live status is `active` (a run is going on). The change is saved anyway; the browser shows the warning text (`running.warn`) so the operator knows the running work may not follow the new plan. It is `false` otherwise.

### `GET /api/board/missions/{mission_id}/timeline`

Returns `{"accepted": [...], "obstacles": [...]}`. `accepted` lists each passing review as `{"task", "ts"}`. `obstacles` lists `{"kind", "task", "ts", "detail"}` for a task rejected three times or more (`rejected_3x`), and for a run that timed out (`timeout`) or was interrupted (`interrupted`). Both lists are empty when the data or the run manager is missing. An unknown mission answers HTTP 404 (code `not_found`).

### `GET /api/runs?mission=<mission_id>`

The runs dashboard's list route (`dashboard.py`) takes `mission` as one more filter. Only runs of that mission's state (`_mission_run_ids`) are kept; the other filters combine with it by AND. An unknown mission gives an empty list. The fetch size is 500 when a mission filter is set, before `limit` applies. The store being unavailable answers HTTP 503.

## Mission screen

The mission screen is `#board/<mission_id>` (`board_mission.js`, saved by `board_mission_save.js`). Its task table (`renderTaskTable`) has these columns:

- the drag grip (`⠿`, waiting tasks only), the task id and title;
- **Status** and **Statistics** (per-task figures);
- **Depends on** (chips of the task's dependencies) and **Needed by** (chips of the tasks that depend on it; the chips highlight the related rows);
- **+ dependency**, a select that adds a dependency from the waiting tasks (the rows that would create a cycle are not offered).

Drag and drop, and the dependency select, both send the requests above. A refused change shows the notice or warning box; the table is reloaded from the server.

## Navigation paths

- `#board` — the board; `#board/view/<view>?project=<key>` — a view with an optional project filter (`proj`, `kan`, `lane`, `spr`, `grid`).
- `#board/<mission_id>` — the mission screen. Its back link returns to `#board`.
- A double click in `kan` opens the runs dashboard as `/#mission=<mission_id>&from=<board hash>`: the runs list filtered by that mission, with a back link to the board view it came from.
- A double click in `lane` and `spr` opens `#board/<mission_id>`.

## Errors

Board errors use one envelope, built by `_error` in `board_api.py`:

```json
{
  "status": "failed",
  "error": {"code": "guard", "message": "missing X-AHAWR-Dashboard header"},
  "code": "guard",
  "status_code": 403
}
```

| Exception | Code | HTTP status |
|---|---|---|
| `ConflictError` | `conflict` | 409 |
| `GuardError` | `guard` | 403 |
| `MissionIdError` | `bad_mission_id` | 400 |
| `KeyError` | `not_found` | 404 |
| `ValueError` | `bad_request` | 400 |
| `RuntimeError` (store unavailable) | `unavailable` | 503 |

The summary route is the exception: it never returns an error for a failing runner.

## Tests

- Node: `tests/board_actions.test.js`, `tests/board_mission.test.js`, `tests/board_mission_save.test.js`, `tests/board_queue.test.js`. They load the browser files with `require(path.join(__dirname, "..", "src", "claude_runner", "<file>.js"))`.
- Python: `tests/test_board_api.py`, `tests/test_board_pg.py`, `tests/test_board.py`. The Postgres tests use a fake connection patched with `monkeypatch.setattr(psycopg, "connect", ...)`, so they need no database.

## Applying the database changes

The two files to run are `docs/board-schema.sql` (the `board` tables, including `project_rules`, `i18n_languages` and `i18n_strings`) and `docs/board-role.sql` (the `board_operator` role and its grants). Both are idempotent. Run them against the Postgres container:

```bash
docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 < claude-runner/docs/board-schema.sql
docker exec -i ahawr-postgres psql -U n8n -d n8n -v ON_ERROR_STOP=1 < claude-runner/docs/board-role.sql
```

Then set the real password in place of the `CHANGE_ME` placeholder:

```sql
ALTER ROLE board_operator PASSWORD '<real-password>';
```
