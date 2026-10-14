# AHAWR Status Bot (control bot)

Workflow `AHAWR_Status_Bot.json` (n8n Data Tables stack, `state_namespace` `llama.cpp-dashboard-v1`, profile `claude-code`, active mission `ahawr-context-retrieval-layer-v1`). This document matches the live behaviour of the shipped file: every command below has exactly one of the three status labels, and every "работает" claim names the test that backs it (suite `tests/n8n-code/run.js`, 243 passed, 0 failed — `node tests/n8n-code/run.js`). The bot token lives in the `telegramApi` credential (n8n credential `Telegram account`, referenced by the Get Updates / Send Reply / Send Callback nodes) and is never written anywhere in the repository or in this document.

Status labels used throughout:

- **работает** — implemented in `AHAWR_Status_Bot.json`, covered by the named test in the same row (or in a section below);
- **пишет в очередь, исполняет оператор** — the bot records the command; the operator runs the launch line;
- **не реализована** — no implementation in the shipped workflow (the chat gets no reply; `Route Category` maps it to `unknown`).

## The two bots

One Telegram bot token can have only one consumer of `getUpdates`. The AHAWR control bot uses its own token; a separate Hermes gateway bot (own token) is needed for Hermes chat commands (`docs/telegram/hermes-commands.md`). Do not share the token.

## Update delivery (since 0.4.0)

The workflow does not poll Telegram any more (the shipped `Get Updates` node used `$credentials('telegramApi').token`, which n8n does not provide, so the first versions never read a message). `data-tables/ops/tg_relay.py` long-polls `getUpdates` (timeout 50 s, `allowed_updates` message and callback_query) with `TELEGRAM_BOT_TOKEN` from `.env` and posts every update to the `Telegram Update` webhook node (`POST /webhook/<AHAWR_TG_WEBHOOK_PATH>`); the offset is saved in `tg_relay.offset` only after n8n answered 2xx, so nothing is lost while n8n is down. At start it calls `setMyCommands` with the list from `data-tables/ops/tg_commands.py` (the "/" menu with descriptions); `/help` and `/commands` print the same list. `queue_watch.py` starts the relay again when it is gone. Only one consumer of `getUpdates` may run: do not start the old schedule version next to the relay. The workflow file in the repository holds placeholders (`REDACTED-CHAT-ID`, `tg-REPLACE-WITH-AHAWR_TG_WEBHOOK_PATH-FROM-ENV`); `data-tables/ops/patch_bot_webhook.py <export.json> <out.json>` fills the chat id from `hermes_config` and the path from `.env`, then `n8n import:workflow`, `n8n update:workflow --active=true` and an n8n restart deploy it.

Other fixes of the same release: the update item passed to `Parse Command` is `{update, kind}`; `Load Live State` reads the task state of the most recently updated active mission (`Shape Live State` maps it to the fields `/status` prints); the data-loading nodes are connected before `Dispatch`; the Switch nodes use `rules.values`; replies pass `Pick Reply` and `Has Buttons?` (HTML, no n8n footer, `reply_markup` only with buttons).

## Polling parameters and executions per day (before 0.4.0, replaced by the relay)

From the workflow:

- `Poll Telegram` (scheduleTrigger): `secondsInterval = 60` — one run every 60 s.
- `Get Updates`: `timeout` query parameter **25** (long poll, in seconds), `limit` **100**, `allowed_updates` = `["message","callback_query"]`; HTTP client timeout **30000 ms**.
- `Prepare Poll` sets a static-data flag `poll_in_progress` before `getUpdates` and clears it in `Expand Updates`; a trigger that finds the flag set (`Prepare Poll` returns no items) skips the run, so executions never overlap. A getUpdates error item ends the run after the flag is cleared.
- `Expand Updates` drops updates with `update_id <` the stored offset and stores `max(update_id) + 1` as the new offset.

Estimate: 60 s interval → **~1440 executions per day** (3600·24/60), plus one extra execution per non-empty update batch (a burst of updates is processed in a single run, so bursts do not add many).

## Access and roles

`Parse Command` splits `Config.telegram_chat_id` on `;`, spaces or commas into the allow-list and sets `allowed`. Only allowed chats get a reply; other chats are counted in the static-data counter `bot_ignored_count` and receive nothing. Role: `allowed` → `admin` (control and edit), otherwise `viewer` (read-only). `Route Category` marks mutating commands (`control`, `edit`) with `mutating: true`.

## Commands

| Command | Status | What it does | Test backing it |
|---|---|---|---|
| `/status` | работает | Status of the live-state row: workflow status, progress `task_index/total_tasks`, attempt, last score, worker/reviewer, run id, updated at. | `"/status shows workflow_status"`, `"/status shows progress 3/10"`, `"/status shows attempt and score"`, `"/status shows worker/reviewer"`, `"/status shows run id"`, `"/status escapes HTML in status"`, `"/status long field -> ..."` |
| `/progress` | работает | Same handler as `/status`. | same |
| `/whoami` | работает | Access level (admin/viewer) and chat id. | `"/whoami shows admin level"` |
| `/missions` | работает | List of missions: id, name, enabled, version (Missions node, one row). | `"/missions lists mission id and name"` |
| `/mission [id]` | не реализована | `/mission` without arguments is not answered: `Handle View` has no `/mission` branch and `Handle Edit` rejects a missing id ("Usage: /mission <id> enable\|disable"). The planned per-mission details view does not exist in the shipped file. (Only the enable/disable form below is implemented.) | `unknown command -> unknown` (Route Category maps `/mission` → view; no branch in Handle View or Handle Edit answers it) |
| `/mission <id> edit <field> …` | не реализована | `/mission <id> edit <field> …` (name/objective/working_folder, `rule add\|del\|set N`, `criterion add\|del\|set N`) is not in the shipped file: `Handle Edit` treats it as a plain `/set` (the id is not parsed as a field), so it either fails on the whitelist or is misread; the version bump and undo via edit history described in the plan are not implemented. | none |
| `/mission <id> enable\|disable` | работает | Edits `enabled` (boolean) of the mission row matched by `mission_id` (falls back to `Config.active_mission_id`). | `/mission enable -> missions table, key=enabled, value=true`, `/mission disable -> value=false` |
| `/agents` | работает | Worker/Reviewer status and run id from the live-state row. | `"/agents -> 'Agents: idle / idle on run run-abc'"` (branch in `Handle View`, `AHAWR_Status_Bot.json:425`) |
| `/tasks` | работает | Alias for `/agents` (`Route Category` maps `/tasks` → `/agents`). | `alias /tasks -> /agents -> view` (routing) + the `/agents` branch test `"/agents -> 'Agents: idle / idle on run run-abc'"` (same handler) |
| `/usage` | работает | Static reply: "Usage: see runner events for tokens, tokens/s, cache share, time per task." (no live table read). | `"/usage -> static reply"` (branch in `Handle View`, `AHAWR_Status_Bot.json:425`) |
| `/insights` | работает | Static reply: scores and retries per task, see the attempts table (no live read). | `"/insights -> static reply"` (branch in `Handle View`, `AHAWR_Status_Bot.json:425`) |
| `/errors` | работает | Static reply: last failed executions with node name and message (secrets masked); no live read in the shipped file. | `"/errors -> static reply"` (branch in `Handle View`, `AHAWR_Status_Bot.json:425`) |
| `/config` | работает | Lists editable keys from `Config.editable_json` with type and range; never shows secrets. | `"/config lists editable keys"`, `"/config does not expose secrets"` |
| `/profile` | работает | Active profile id and the allowed chat id. | `"/profile -> 'Profile: claude-code (allowed chat: 12345 67890)'"` (branch in `Handle View`, `AHAWR_Status_Bot.json:425`) |
| `/version` | работает | Static reply: workflow, runner, n8n. | `"/version -> 'Workflow: AHAWR Status Bot v1; runner: Claude Code; n8n: this stack'"` + `alias /v -> /version -> view` (routing) |
| `/audit [N]` | работает | Last N rows of `AHAWR Bot Audit Log` (who, when, command, result); default 10. | `"/audit 2 shows two entries"` |
| `/help [filter]` | работает | Command list grouped by category (Hermes style); filters by name or category. | `"/help -> grouped list (Session, Info, Configuration)"`, `"/help info -> only the Info group"` (branch in `Handle Help`, `AHAWR_Status_Bot.json:477`) |
| `/commands [page]` | работает | Paginated full command list, 20 per page. | `"/commands page 1 -> 20 sorted commands"` (branch in `Handle Help`, `AHAWR_Status_Bot.json:477`) |
| `/start` | работает | Without arguments: greeting "AHAWR Status Bot online. Use /help for commands." (branch in `Handle Help`, `AHAWR_Status_Bot.json:477`). | `/start (no args) -> greeting` |
| `/start <mission>` | пишет в очередь, исполняет оператор | Confirmed, inserts a row into `AHAWR Command Queue`; reply gives the launch line. | `e2e: /start reply is the exact queue text with the launch line`, `e2e: /start inserted a queue row (dataTable insert node)`, `e2e: queue row carries the operator launch line as result`, `e2e: /start audit row written, result approved, command recorded` |
| `/resume <mission>` | пишет в очередь, исполняет оператор | Same queue row, launch line. | `e2e: /resume reply is the exact queue text with the launch line`, `e2e: /resume queue row written by the insert node, status pending` |
| `/restart <mission>` | пишет в очередь, исполняет оператор | Same queue row, launch line. (T009: `/restart` was a Live-Status write before the fix; now it queues.) | `e2e: /restart reply is the exact queue text with the launch line`, `e2e: /restart queue row written by the insert node, status pending`, `/restart -> queued=true, table=AHAWR Command Queue` |
| `/stop` | работает | Sets `workflow_status = stopped` in the live-state row; the main workflow's `Stop Check Worker` consumes it. | `"/stop -> live state key=workflow_status, value=stopped"`, `stop: Stop Check Worker condition is true`, `stop: Stop Workflow output has workflow_status='stopped'` |
| `/pause [reason \| off]` | работает | Sets `paused = true` (or `false` for `pause off`) in the live-state row; the main workflow's `Pause Gate` blocks the start of the next task while paused. | `"/pause -> paused=true"`, `"/pause off -> paused=false"`, `pause: Pause Gate condition is true (blocked)`, `pause: Hold Paused routes to execution_route 'paused'` |
| `/retry` | работает | Sets `retry_requested = true` in the live-state row; the main workflow's `Retry Current Task` allows one attempt beyond `max_attempts_per_task` with the operator remark (steer + feedback) in `review_feedback`. | `e2e: /retry reply states what the workflow will do and when`, `retry: one attempt beyond the limit is allowed with the flag`, `retry: without the flag the attempt limit is enforced` |
| `/accept [reason]` | работает | Sets `accept_requested = true` in the live-state row; the main workflow's `Review Passed?` accepts the task when the flag is set (reason recorded in `last_control`). | `e2e: /accept reply states what the workflow will do and when`, `accept: Review Passed? passes on accept_requested even without review_status=pass` |
| `/steer <text>` | работает | Appends the note to `operator_feedback` in the live-state row; the main workflow's `Prepare Current Task` adds it to the next attempt's `review_feedback` as `OPERATOR STEER NOTE`, and `Clear Consumed Flags` empties `steer_note` after consumption. | `"/steer -> operator_feedback appended"`, `steer: Prepare Current Task review_feedback carries the steer note`, `steer: no steer note -> review_feedback unchanged` |
| `/queue [list\|add <mission>\|rm N\|move A B\|clear]` | работает | Edits the `queue` array in the live-state row (`live.queue`). | `"/queue add -> queue includes mission-c"`, `"/queue rm 0 -> 'a' removed"`, `"/queue clear -> empty queue"`, `"/queue move 0 2 -> swapped"`, `"/queue list -> queue returned"` |
| `/set <key> <value>` | работает | Edits one whitelisted field (type and range checks, re-validated in Apply Mutation before the write); reply shows "было -> стало". | `e2e: /approve reply says 'было -> стало'`, `e2e: confirmed /set changed hermes_config.worker_model`, `/set max_tasks 9999 -> rejected`, `/set unknown key api_key -> Not editable`, `number out of range -> failed`, `boolean field with 'maybe' -> failed` |
| `/model <role> <model> [--provider name]` | работает | Edits `<role>_model` (and provider) in `hermes_config`; confirmation, audit. | `/model pending stored`, `/model captures role and model in args`, `/model captures provider`, `/model -> hermes_config key=worker_model value=claude-sonnet-5` |
| `/undo [N]` | работает | Restores the editable key/value of the last successful (approved) audit row **by this chat**: parses `set key = value` or `model role ...` from its `command`, takes the `before` value, stores it as a pending and confirms it like any `/set` (re-validated, audited). No version-bump, no edit_history table. | `e2e: /undo restored the pre-edit value`, `e2e: /undo reply built from the re-read value`, `e2e: /undo audit row holds before and after, approved`, `/undo with no successful editable audit row -> 'Nothing to undo'` |
| `/approve [code]` | работает | Confirms the pending of this chat (code or buttons); applies the change and writes the audit row. | `e2e: /approve reply says 'было -> стало'`, `/approve -> result approved`, `expired pending -> rejected with 'code expired'`, `e2e: code reuse -> value unchanged on second use` |
| `/deny [code]` | работает | Cancels the pending; audited as rejected. | `/deny -> denied`, `callback 'deny' -> denied` |
| `callback_query` (Approve/Deny buttons) | работает | Button callback carrying the code (or `deny`) is confirmed like `/approve`/`/deny`; every callback is acknowledged with `answerCallbackQuery`. | `e2e: callback carrying code -> reply 'было -> стало'`, `Answer Callback? -> 'callback' for callback update`, `realistic callback_query -> callback_data captured` |
| `/sethome` | не реализована | `Route Category` maps the alias `/set-home` → `/sethome` but no handler branch answers it in the shipped file (it is not in `Handle View`, `Handle Control`, `Handle Edit` or `Handle Help`): the chat gets no reply. The old plan of a one-time code in the n8n log was not implemented. | none |
| `/start_greet` | не реализована | Listed in `Route Category`'s map (`/start_greet` → `help`) but no handler branch answers it: the chat gets no reply. The greeting is sent by `/start` without arguments. | none |

Unknown commands: `Route Category` routes them to `unknown`; no handler answers them, so the chat gets no reply (tested: `unknown command -> unknown`).

## Field whitelist (types and ranges)

`Config.editable_json` — identical to `EDITABLE` in `scripts/patch_status_bot_control.py` and to the table in `docs/telegram/control-bot-findings.md` (Q4b), verified field by field. Secrets are never editable and never shown.

| Field | Table | Type | Min | Max |
|---|---|---|---|---|
| `worker_model` | `hermes_config` | string | 1 | 120 |
| `worker_provider` | `hermes_config` | string | 1 | 60 |
| `reviewer_model` | `hermes_config` | string | 1 | 120 |
| `reviewer_provider` | `hermes_config` | string | 1 | 60 |
| `max_tasks` | `hermes_config` | number | 1 | 500 |
| `max_attempts_per_task` | `hermes_config` | number | 1 | 10 |
| `architect_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `worker_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `reviewer_poll_seconds` | `hermes_config` | number | 1 | 3600 |
| `name` | `missions` | string | 1 | 120 |
| `objective` | `missions` | string | 1 | 4000 |
| `working_folder` | `missions` | string | 1 | 300 |
| `enabled` | `missions` | boolean | — | — |

Checks (Handle Edit + Apply Mutation): number must be finite and in `[min..max]`; boolean must be `true`/`false`; string length must be in `[min..max]`; a field not in the whitelist is rejected with "Not editable" and the allowed list. The write nodes match the row by `profile_id` (hermes_config), `mission_id` (missions), or `state_key` (live state).

## Confirmation flow

Pending commands are stored in the workflow static data as `bot_pending[chatId]` — **per chat**, one pending per chat; there is no `bot_pending_ttl_ms` column or table. A pending has: `action`, `chat_id`, `role`, `code` (`CONF-<digits of chat id>-<6 random base36>`), `created_at`, `ttl_ms = 120000` (2 minutes), `kind` (`control`/`edit`), plus `launch` for queued commands and `flag` for flag commands.

Rules, each backed by a test:

1. **Per chat**: a pending belongs to the chat that created it. A callback or `/approve` from another chat is rejected ("foreign chat: this code belongs to chat …") — `e2e: foreign chat -> value unchanged`, `e2e: foreign chat -> rejected audit row`.
2. **TTL 2 minutes**: after `created_at + ttl_ms` the confirmation is rejected ("code expired after 2 minutes") — `expired pending -> rejected with 'code expired'`, `e2e: expired code -> rejected audit row`.
3. **Code is one-time**: used codes are remembered in static data `usedCodes`; a replayed code is rejected ("code already used") — `e2e: code reuse -> value unchanged on second use`. A wrong code is rejected ("wrong one-time code") — `e2e: wrong code -> value unchanged`.
4. **Role**: viewer cannot create mutating commands and cannot confirm ("Rejected: viewer role …") — `/stop from viewer -> rejected, no pending stored`, `e2e: viewer role -> value unchanged`.
5. **No pending**: `/approve` without a pending replies "No pending change (or it was already confirmed)." and a presented code with no matching pending is rejected as "unknown one-time code" — `no pending + /approve -> rejected with 'no pending command'`, `no pending + unknown code -> rejected 'unknown one-time code'`.
6. **Write verification**: Apply Mutation re-validates the value against the whitelist, then Write Table → table update → **re-read**; the reply is only "было -> стало" if the re-read row carries the written value. A write failure (error item or re-read mismatch) replies "Error: … No change confirmed." and audits `failed` — `e2e: write failure -> audit result 'failed'`, `e2e: write error item -> audit result 'failed'`.
7. **Queued commands** (/start, /resume, /restart) are checked before re-read: the queue row has no key/value pair, so the reply is the launch line and the audit row says `approved` with `after = queued: <action>`.

## Reply formats

Exact strings, with the source in `AHAWR_Status_Bot.json` (line of the node's `jsCode`):

**Control confirmation pending** (`Handle Control`, line 438):

```
Confirming: <action>
Reply with the one-time code CONF-… or use the buttons. Valid for 2 minutes.
```

plus Approve/Deny buttons (the Approve button carries the code).

**Edit confirmation pending** (`Handle Edit`, line 451) — first line varies by command:

- `/set`: `Confirm edit: <key> = <value> (table <table>)`
- `/model`: `Confirm: set <role> model[ provider=<provider>] to <model>`
- `/undo`: `Confirm: undo last <N> edit(s) -> <key> = <before>`
- `/mission`: `Confirm: <id> enabled|disabled`

Each is followed by the same second line:

```
Reply with the one-time code CONF-… or use the buttons. Valid for 2 minutes.
```

plus Approve/Deny buttons.

**Rejections** (`Handle Confirm`, line 464):

- `Rejected: <reason>. No change applied.` (audited) — reasons include `code expired after 2 minutes`, `foreign chat: this code belongs to chat <id>`, `wrong one-time code`, `code already used`, `no pending command for this chat`, `unknown one-time code`, `viewer role cannot confirm (read-only access)`;
- no pending, no decision: `No pending change (or it was already confirmed).`
- pending, no decision yet: `Pending: <action>. Reply with the one-time code, use the buttons, or /approve. Valid for 2 minutes.`

**Confirmed results** (`Build Reply`, line 930):

- key/value edit: `<key>: было <before> -> стало <after>.`
- flag (/retry, /accept): `<flag>: было — -> стало true. <note> as soon as the running mission reaches the next task.` (note: retry — `the workflow will retry the current task once more with the last review as feedback`; accept — `the workflow will accept the current task and continue with the next one`)
- queued (/start, /resume, /restart): `записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh <mission>`
- write failure / re-read mismatch: `Error: <reason>. No change confirmed.` (audited `failed`)

**Usage / error strings** (`Handle Edit`, line 451): `Usage: /model <role> <model> [--provider name]`, `Usage: /mission <id> enable|disable`, `Nothing to undo: no successful edit by this chat in the audit log.`, `Not editable: <key>. Allowed: <list> (secrets are never editable).`, `Not editable: <key> (whitelist field).`

None of the confirmed-command replies contains the word "approved" in the chat text (tests: `e2e: /approve reply never says 'approved'`, `e2e: /start reply never says 'approved'`, `e2e: /accept reply never says 'approved'`, `e2e: /retry reply never says 'approved'`).

## Schemas

### AHAWR Bot Audit Log (7 columns)

| Column | Type | Notes |
|---|---|---|
| `who` | string | Chat id of the operator. |
| `when` | string | ISO 8601. |
| `command` | string | Action string (`stop`, `set max_tasks = 100`, `start mission-a`, …). |
| `before` | string | Previous value or `—`. |
| `after` | string | New value; for queued commands `queued: <action>`; for flags the flag value. |
| `result` | string | `approved`, `rejected`, `failed`. |
| `reason` | string | Free text (`viewer role`, `code expired`, `unknown one-time code`, `command recorded in the queue table`, …); empty when none. |

(The old 11-column draft with `role`/`target_table`/`row_key`/`field` was not used; the shipped insert node writes exactly these 7 columns.)

### AHAWR Command Queue (10 columns written by the bot)

| Column | Type | Notes |
|---|---|---|
| `action` | string | `start <mission>`, `resume <mission>`, `restart <mission>`. |
| `mission_id` | string | Mission to launch (argument, or `Config.active_mission_id` if none). |
| `state_key` | string | The mission's `state_namespace` (restoration key). |
| `chat_id` | string | Operator who requested it. |
| `role` | string | `admin`. |
| `status` | string | `pending` (the bot always writes `pending`; the operator's watcher marks `executed`/`failed`). |
| `code` | string | The one-time confirmation code that approved it. |
| `created_at` | string | ISO 8601. |
| `executed_at` | string | Set by the operator's watcher (`null` until then). |
| `result` | string | The operator launch line `bash data-tables/ops/launch_mission.sh <mission>`; the watcher overwrites it with the launch outcome. |

The table has an auto-increment `id` primary key (created with the table).

### Autonomous Agent Live State — new control columns

| Column | Type | Used by |
|---|---|---|
| `workflow_status` | string | `stopped` (/stop), `starting` is never written by the bot (the queue branch replaces it); consumed by Stop Check Worker. |
| `paused` | boolean | Pause Gate (before each new task); `/pause off` clears it. |
| `pause_reason` | string | Reason text carried with `paused`. |
| `stop_requested` | boolean | Stop Check Worker / Reviewer stop check. |
| `accept_requested` | boolean | Review Passed? (accept gate). |
| `retry_requested` | boolean | Retry Current Task (one extra attempt). |
| `steer_note` | string | Steer note consumed by Prepare Current Task; cleared by Clear Consumed Flags. |
| `operator_feedback` | string | Accumulated steer notes and operator remarks; `/steer` appends. |
| `last_control` | string | Last control action, including the accept reason. |
| `queue` | array (JSON) | Mission queue edited by `/queue`. |

## Operator steps (apply and verify)

1. **Create the tables** in n8n Data Tables (n8n stopped for any DB surgery):
   - `AHAWR Bot Audit Log` — 7 columns above (string columns; `id` auto-increment).
   - `AHAWR Command Queue` — 10 columns above plus auto-increment `id`.
   - Verify the `Autonomous Agent Live State` row for `state_key = llama.cpp-dashboard-v1` has the new control columns (`workflow_status`, `paused`, `pause_reason`, `stop_requested`, `accept_requested`, `retry_requested`, `steer_note`, `operator_feedback`, `last_control`, `queue`); add them if missing.
2. **Patch the live export**: export the live AHAWR v13 workflow (`n8n export:workflow` in the container), then run `python3 scripts/patch_main_control_flags.py <exported-file>` (idempotent; re-run prints "already patched, skipping"; a missing anchor node fails with "missing anchor node(s): …"). Then import the patched file and verify: `node tests/n8n-code/run.js` (243 passed, 0 failed) and `python3 scripts/check_workflows.py`.
3. **Import the bot workflow**: import `AHAWR_Status_Bot.json` into n8n (it was generated by `scripts/patch_status_bot_control.py` — 39 nodes; `makeTables` in the test fixture includes the `AHAWR Command Queue` table).
4. **Bind the credential**: in n8n create/bind the `telegramApi` credential ("Telegram account") — the Get Updates, Send Reply and Send Callback nodes reference it. The token is in the credential only; it is not in the workflow file and must not be written anywhere else.
5. **Activate**: activate the workflow. The 60 s schedule then starts polling.
6. **Telegram checks** (from the allowed chat; `<code>` is the one-time code from the confirmation reply). Quoted replies are exact; sources: `Handle Control` (line 438), `Handle Edit` (line 451), `Handle Confirm` (line 464), `Build Reply` (line 930), `Handle Help` (line 477), `Handle View` (line 425) of `AHAWR_Status_Bot.json`.

| Send | Expected reply (exact) |
|---|---|
| `/whoami` | `Access level: admin (control and edit)` + second line `Chat: <chat id>` (Handle View, 425) |
| `/status` | `Status: running` / `Progress: 3/10 task task-003` / `Attempt: 2  score: 0.7` / `Worker: idle  Reviewer: idle` / `Run: run-abc` / `Updated: 2026-10-09T00:00:00Z` (six lines; Handle View, 425) |
| `/missions` | one line per mission: `mission-id \| name \| enabled=true \| v3` (Handle View, 425) |
| `/config` | `Editable keys (no secrets):` + one line per field `  <key> (string 1..120)` (or `(number 1..500)` / `(boolean)`) (Handle View, 425) |
| `/audit 2` | `Audit (last 2):` + rows `when \| who \| command \| result` (Handle View, 425) |
| `/set worker_model claude-opus-5` | `Confirm edit: worker_model = claude-opus-5 (table hermes_config)` + `Reply with the one-time code CONF-… or use the buttons. Valid for 2 minutes.` + Approve/Deny buttons (Handle Edit, 451) |
| `<code>` (or Approve button) | `worker_model: было claude-sonnet-5 -> стало claude-opus-5.` (Build Reply, 930) |
| `/set max_tasks 9999` | `Rejected: max_tasks must be a number in [1..500]` (Handle Edit, 451) |
| `/set api_key x` | `Not editable: api_key. Allowed: worker_model, worker_provider, reviewer_model, reviewer_provider, max_tasks, max_attempts_per_task, architect_poll_seconds, worker_poll_seconds, reviewer_poll_seconds, name, objective, working_folder, enabled (secrets are never editable).` (Handle Edit, 451) |
| `/stop` | `Confirming: stop` + `Reply with the one-time code CONF-… or use the buttons. Valid for 2 minutes.` (Handle Control, 438); after confirm: `workflow_status: было running -> стало stopped.` (Build Reply, 930) |
| `/pause maintenance` | `Confirming: pause maintenance` + code line (Handle Control, 438); after confirm: `paused: было false -> стало true.` (Build Reply, 930) |
| `/pause off` | `Confirming: pause off` + code line (Handle Control, 438); after confirm: `paused: было true -> стало false.` (Build Reply, 930) |
| `/steer keep it short` | `Confirming: steer keep it short` + code line (Handle Control, 438); after confirm: `operator_feedback: было <old> -> стало <old>\nkeep it short.` (Build Reply, 930) |
| `/queue add mission-x` | `Confirming: queue add mission-x` + code line (Handle Control, 438); after confirm: `queue: было ["mission-a","mission-b"] -> стало ["mission-a","mission-b","mission-x"].` (Build Reply, 930) |
| `/retry` | `Confirming: retry` + code line (Handle Control, 438); after confirm: `retry_requested: было — -> стало true. the workflow will retry the current task once more with the last review as feedback as soon as the running mission reaches the next task.` (Build Reply, 930) |
| `/accept by hand` | `Confirming: accept by hand` + code line (Handle Control, 438); after confirm: `accept_requested: было — -> стало true. the workflow will accept the current task and continue with the next one as soon as the running mission reaches the next task.` (Build Reply, 930) |
| `/start mission-a` | `Confirming: start mission-a` + code line (Handle Control, 438); after confirm: `записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh mission-a` (Build Reply, 930) |
| `/resume mission-b` | `Confirming: resume mission-b` + code line (Handle Control, 438); after confirm: `записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh mission-b` (Build Reply, 930) |
| `/restart mission-a` | `Confirming: restart mission-a` + code line (Handle Control, 438); after confirm: `записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh mission-a` (Build Reply, 930) |
| `/deny` | `Pending change: <action>` + `Result: denied` (Handle Confirm, 464; no change applied) |
| expired `<code>` | `Rejected: code expired after 2 minutes. No change applied.` (Handle Confirm, 464) |
| code from another chat | `Rejected: foreign chat: this code belongs to chat <id>. No change applied.` (Handle Confirm, 464) |
| viewer chat `/stop` | `Rejected: viewer role cannot create this command (read-only access).` (Handle Control, 438 / Handle Edit, 451) |
| `/start` (no args) | `AHAWR Status Bot online. Use /help for commands.` (Handle Help, 477) |
| `/help` | grouped list, e.g. `Session: /accept, /agents, /approve, /deny, /pause, /queue, /restart, /resume, /retry, /start, /status, /steer, /stop, /undo` … (Handle Help, 477) |
| `/commands` | `Page 1: /accept, /agents, /approve, /audit, /commands, /config, /deny, /errors, /help, /insights, /mission, /missions, /model, /pause, /profile, /progress, /queue, /restart, /resume, /retry` (20 per page; page 2: /set, /start, /status, /steer, /stop, /undo, /usage, /version, /whoami; Handle Help, 477) |
| `/sethome` | **no reply** (not implemented; `Route Category` maps it to itself with no handler) |

## Main-workflow behaviour (patched `AHAWR_v13_ClaudeCode.json`)

- **Pause**: `Pause Gate Flags` reads the fresh live row; when `paused = true` the next task is routed to `Hold Paused` (`execution_route = paused`, `final_status = paused_by_operator`, `workflow_status = paused`, owner cleared). `/pause off` clears the flag and the gate passes (`Clear Consumed Flags` path).
- **Stop**: `Stop Check Worker` reads the fresh `stop_requested`; when true, `Stop Workflow` sets `workflow_status = stopped` and clears the owner (nothing left running without an owner). The reviewer stop check reads its own fresh pair.
- **Steer**: `Prepare Current Task` appends `OPERATOR STEER NOTE: <note>` to `review_feedback` of the next attempt (the worker input changes only by this note); `Clear Consumed Flags` records `cleared_flags.steer_note` and empties `steer_note` so the next attempt starts clean.
- **Accept**: `Review Passed?` is an OR condition that passes when `accept_requested = true` even without `review_status = pass`; the accept reason is recorded in `last_control`.
- **Retry**: `Retry Current Task` allows one attempt beyond `max_attempts_per_task` when `retry_requested = true`, and the operator remark (steer + feedback) rides in `review_feedback` of the new attempt; without the flag the attempt limit is enforced (`failed_task_attempt_limit`).
- **Worker input unchanged**: with all flags unset, the patched `Build Worker Run Input` output is byte-identical to the unpatched node's output (`worker input: patched output (flags unset) is byte-identical to the unpatched output`).

The before-fix failing cases are recorded in `docs/telegram/tests-before-fix.txt` (unpatched file via `git show HEAD:…`); the T011 test section is `tests/n8n-code/run.js` §11.
