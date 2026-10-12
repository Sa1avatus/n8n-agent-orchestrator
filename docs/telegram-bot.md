# AHAWR Telegram Control Bot

**Workflow file**: `AHAWR_Status_Bot.json`
**Source of truth**: `scripts/patch_status_bot_control.py` (idempotent rewrite, all checks must pass)

The bot is a long-polling n8n workflow that handles both `message` and `callback_query` updates from Telegram. Only chats in `hermes_config.telegram_chat_id` are answered; all other chats are counted silently. Mutating commands (control and edit) require a confirmation before any change is applied. Every confirmed mutation is written to the audit table.

The bot token lives in the `telegramApi` n8n credential (id `czj7GHvr93VLvcH5`, name "Telegram account"), never in the workflow file.

## Two-Bot / Two-Token Constraint

One Telegram bot token can have only **one** consumer of `getUpdates`. If Hermes' own Telegram gateway is connected, it needs its own bot (its own token). The AHAWR bot must **not** share that token.

- Hermes gateway: its own bot, its own token, its own `getUpdates` consumer.
- AHAWR bot: a separate bot, a separate token, a separate `getUpdates` consumer.

Sharing a token between the two consumers would cause one of them to silently starve (the other would drain all updates).

## Command List

The command registry has five categories: **view** (read-only), **control** (mutating, confirmation required), **edit** (mutating, confirmation required), **confirm**, and **help**.

### View (read-only, no confirmation)

| Command | What it does |
|---|---|
| `/status [mission]` | Mission status: progress, task, attempt, last scores, Worker/Reviewer state, time, llama-server and runner health. |
| `/missions` | List of missions: id, name, enabled, version. |
| `/mission <id>` | Details of one mission. |
| `/progress [mission]` | The plan with the score and attempt count of every task. |
| `/agents` | Active agent runs from claude-runner (role, task, time, tokens) and the n8n execution. Alias `/tasks`. |
| `/usage [reset [--force]]` | Tokens, tokens per second, cache share and time per task of the current mission, from the runner events. |
| `/insights [days]` | Scores and retries per task over the last N days, slowest tasks. |
| `/errors [N]` | The last failed executions with node name and message (secrets masked). |
| `/config` | Show the editable config keys with their current values and allowed ranges (never secrets). |
| `/profile` | The active hermes_config profile (e.g. claude-code) and the chat that is allowed. |
| `/version` | Versions: workflow, bot, Claude Code in the runner, n8n. |
| `/whoami` | Access level of this chat: admin (control and edit) or viewer (read-only). |
| `/audit [N]` | The last N entries of the audit log (who, when, command, before, after, result). |

### Control (mutating, confirmation required)

| Command | What it does |
|---|---|
| `/start <mission>` | Start a mission with confirmation. Without an argument it is the greeting. |
| `/resume [mission]` | Continue a mission from its saved state. |
| `/stop` | Stop the running AHAWR execution. Leaves no run in state "running" without an owner. |
| `/restart` | Stop, wait for the active run to drain, start again from the saved state. |
| `/pause [reason \| off]` | No new task starts after the current one finishes (flag in the config table, checked before each task). `/pause off` resumes. |
| `/retry` | One more attempt of the current task with the last review as feedback. |
| `/accept [reason]` | Accept the current task by hand and go to the next one; the reason goes to the audit log. |
| `/steer <text>` | Operator note for the current task: appended to the feedback of the next attempt. |
| `/queue [add <mission>\|list\|rm N\|move A B\|clear]` | A queue of missions. The next mission starts when the current one is approved. |

### Edit (mutating, confirmation required)

| Command | What it does |
|---|---|
| `/set <key> <value>` | Edit one config value from the allow-list with type and range checks. |
| `/model <role> <model> [--provider name]` | Change the model or provider of architect, worker, reviewer or service. |
| `/mission <id> edit <field> ...` | Edit name, working folder, objective; `rule add\|del\|set N <text>` and `criterion add\|del\|set N <text>` for the rule and criteria lists. Raises the mission version and keeps the old one for /undo. |
| `/undo [N]` | Roll back the last N mission or config edits from the edit history. |

### Confirm

| Command | What it does |
|---|---|
| `/approve [session\|always]` | Confirm the pending change (same as the inline button). Without a pending change, says so. |
| `/deny [all] [reason]` | Cancel the pending change (same as the inline button). |

### Help

| Command | What it does |
|---|---|
| `/help [<filter>]` | Short list grouped by category (Hermes style); `/help <text>` filters. |
| `/commands [page]` | Paginated full list. |
| `/start_greet` | First contact: short greeting and a pointer to /help (only for allowed chats). |

### Aliases

| Alias | Command |
|---|---|
| `/tasks` | `/agents` |
| `/v` | `/version` |
| `/q` | `/queue` |
| `/set-home` | `/sethome` |

## Permissions

Two access levels:

- **admin** — can run control and edit commands (mutating). Confirms via inline buttons or one-time code.
- **viewer** — read-only; view commands only.

Access is determined by the chat's presence in `hermes_config.telegram_chat_id`. Only allowed chats get any reply; all others are counted silently.

## Whitelist of Editable Config/Mission Fields

Only the fields below may be edited via `/set` or `/mission <id> edit`. Secrets (`api_key`, `service_*`, bot token, `.env` values) are deliberately absent: they are never shown or accepted for edit.

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

Validation rules:
- **string**: length must be within [min, max].
- **number**: must be a finite number within [min, max].
- **boolean**: must be `true` or `false` (case-insensitive).

A key not in the whitelist is rejected with: "Not editable: <key>. Allowed: <list> (secrets are never editable)."

## Confirmation Scheme

Mutating commands (control and edit) do not apply immediately. They create a **pending action** in the workflow's static data with a **2-minute TTL** (`CONFIRM_TTL_SEC = 120`).

Two ways to confirm:

1. **Inline buttons** — the bot replies with an "Approve" button and a "Deny" button. Clicking them sends a `callback_query` with `callback_data` = `confirm` or `deny`.
2. **One-time code** — the bot generates a code of the form `CONF-<chatId digits>-<6 random base36 chars>` and tells the operator to reply with that code. The code is single-use and expires after 2 minutes.

When confirmed, the pending action is consumed and the mutation is applied. When denied or expired, the pending action is cleared. If there is no pending change (or it expired), the bot says so.

The pending action is stored in workflow static data under key `bot_pending`, with a TTL timestamp under key `bot_pending_ttl_ms`.

## Audit Log

Table: **`AHAWR Bot Audit Log`** (n8n Data Table).

Columns:

| Column | Type | Description |
|---|---|---|
| `who` | string | Chat ID of the operator who issued the command. |
| `when` | string | ISO timestamp of the action. |
| `command` | string | The action string (e.g. `set max_tasks = 100`, `stop`, `model worker claude-sonnet-4`). |
| `before` | string | The previous value (or `—` if not applicable). |
| `after` | string | The new value or action description. |
| `result` | string | `approved` or `denied`. |

An audit row is written for every confirmed mutation, accept, and deny. The `Build Audit Row` node extracts the audit object from the handler output; the `Write Audit Log` node upserts it into the data table.

## Undo Scheme

`/undo [N]` rolls back the last N mission or config edits from the **edit history**. The edit history is a list of prior edit actions (stored in the pending context or reconstructed from the audit log). Each undo:

1. Takes the last N entries from the edit history (reversed).
2. Restores the previous values for each affected key.
3. Bumps the mission version (if applicable) and keeps the old version for further undo.

The undo itself is a mutating edit command: it requires confirmation and is written to the audit log.

## Data Table Schemas

### 1. `AHAWR Bot Audit Log`

| Column | Type |
|---|---|
| `who` | string |
| `when` | string (ISO 8601) |
| `command` | string |
| `before` | string |
| `after` | string |
| `result` | string |

### 2. `hermes_config`

| Column | Type |
|---|---|
| `profile_id` | string |
| `enabled` | boolean |
| `api_url` | string |
| `api_key` | string (secret, never shown) |
| `worker_model` | string |
| `worker_provider` | string |
| `reviewer_model` | string |
| `reviewer_provider` | string |
| `max_tasks` | number |
| `max_attempts_per_task` | number |
| `architect_poll_seconds` | number |
| `worker_poll_seconds` | number |
| `reviewer_poll_seconds` | number |
| `telegram_chat_id` | string |

### 3. `missions`

| Column | Type |
|---|---|
| `mission_id` | string (PK) |
| `name` | string |
| `objective` | string |
| `working_folder` | string |
| `enabled` | boolean |
| `state_namespace` | string |
| `rules_json` | string (JSON) |
| `acceptance_criteria_json` | string (JSON) |
| `version` | number |

### 4. Mission queue

The mission queue is a list of mission IDs stored in the `Autonomous Agent Live State` table under the key `queue`. It is manipulated by `/queue [add <mission>|list|rm N|move A B|clear]`.

| Operation | Effect |
|---|---|
| `add <mission>` | Append mission to the queue. |
| `list` | Show the current queue. |
| `rm N` | Remove the Nth item (0-based). |
| `move A B` | Swap items at positions A and B. |
| `clear` | Empty the queue. |

### 5. Edit history

The edit history is a list of prior edit actions. Each entry records:

| Field | Description |
|---|---|
| `table` | Target table (`hermes_config` or `missions`) |
| `key` | Field being edited |
| `value` | New value applied |
| `profile_id` | Profile (for config edits) |
| `mission_id` | Mission (for mission edits) |
| `updated_at` | ISO timestamp |

The undo operation reads the last N entries and reverses them.

### 6. `Autonomous Agent Live State`

| Column | Type |
|---|---|
| `state_key` | string (PK) |
| `workflow_status` | string (`running`, `stopped`, `starting`, etc.) |
| `owner` | string (chat ID of the run owner) |
| `current_run_id` | string |
| `paused` | boolean |
| `pause_reason` | string |
| `steer_note` | string |
| `operator_feedback` | string |
| `last_control` | string |
| `queue` | array (mission IDs) |
| `updated_at` | string (ISO 8601) |

## Polling and Safety

- **Poll interval**: every 30 seconds (86400 / 30 = 2880 executions/day, ≤ ~3000).
- **getUpdates timeout**: 30 seconds (within the required 25–30 range).
- **allowed_updates**: `["message", "callback_query"]`.
- **No secrets in the workflow file**: the bot token is in the `telegramApi` credential only. The Config node holds no token, api_key, or secret.
- **Mutating commands always route through the confirmation node**: both `Handle Control` and `Handle Edit` store a pending confirmation in static data before any state change is applied.
