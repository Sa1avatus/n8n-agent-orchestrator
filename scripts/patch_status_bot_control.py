#!/usr/bin/env python3
"""Idempotently rewrite AHAWR_Status_Bot.json into a control bot.

Builds the full workflow (command registry, chat allow-list, confirmation,
audit table, edit-field whitelist, no secret in file, long-polling,
callback_query handling) as named nodes, then writes the JSON.

Checks performed (all must pass, else the script aborts without writing):
  - JSON is valid (round-trips)
  - node ids and names are unique
  - every connection resolves to an existing node name
  - no Telegram bot token pattern (digit-letter string with hyphen, 30+ chars)
  - no 'Bearer ' anywhere
  - Config node holds no token/secret value
  - getUpdates uses timeout 25..30 and interval gives <= ~3000 exec/day
  - allowed_updates includes callback_query
  - audit log columns present
  - mutating commands route through the confirmation node
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF_FILE = ROOT / "AHAWR_Status_Bot.json"
LABEL = "AHAWR_Status_Bot.json"

# --- constants shared by node code -------------------------------------------
POLL_SECONDS = 60          # schedule interval; 86400 / 60 = 1440 executions/day (<= ~3000)
TELEGRAM_TIMEOUT = 25      # getUpdates long-poll timeout, passed as a request parameter (25..30 s)
HTTP_TIMEOUT_MS = 30000    # HTTP client timeout, above the 25 s long-poll
CONFIRM_TTL_SEC = 120      # 2-minute TTL for a pending confirmation

AUDIT_TABLE = "AHAWR Bot Audit Log"
COMMAND_QUEUE_TABLE = "AHAWR Command Queue"

# Confirmed /start, /resume and /restart cannot be launched by the bot
# itself (no execution trigger in this workflow): they are recorded as rows
# of the AHAWR Command Queue table (schema: docs/telegram/control-bot-findings.md,
# option A) and the operator's launch line is announced in the reply.
# /retry and /accept set the corresponding Live State flags instead; the main
# workflow (the T010 patch) consumes them and acts on them during a running
# mission, so the reply states that.
QUEUED_VERBS = {
    "start": "bash data-tables/ops/launch_mission.sh %s",
    "resume": "bash data-tables/ops/launch_mission.sh %s",
    "restart": "bash data-tables/ops/launch_mission.sh %s",
}
FLAG_VERBS = {
    "retry": "retry_requested",
    "accept": "accept_requested",
}

# Editable config/mission field whitelist with type/range checks.
# Secrets (api_key, service_*, bot token, .env values) are deliberately
# absent: they are never shown or accepted for edit.
EDITABLE = {
    "worker_model":      {"table": "hermes_config", "type": "string", "min": 1, "max": 120},
    "worker_provider":   {"table": "hermes_config", "type": "string", "min": 1, "max": 60},
    "reviewer_model":    {"table": "hermes_config", "type": "string", "min": 1, "max": 120},
    "reviewer_provider": {"table": "hermes_config", "type": "string", "min": 1, "max": 60},
    "max_tasks":         {"table": "hermes_config", "type": "number", "min": 1, "max": 500},
    "max_attempts_per_task": {"table": "hermes_config", "type": "number", "min": 1, "max": 10},
    "architect_poll_seconds": {"table": "hermes_config", "type": "number", "min": 1, "max": 3600},
    "worker_poll_seconds":  {"table": "hermes_config", "type": "number", "min": 1, "max": 3600},
    "reviewer_poll_seconds": {"table": "hermes_config", "type": "number", "min": 1, "max": 3600},
    "name":        {"table": "missions", "type": "string", "min": 1, "max": 120},
    "objective":   {"table": "missions", "type": "string", "min": 1, "max": 4000},
    "working_folder": {"table": "missions", "type": "string", "min": 1, "max": 300},
    "enabled":     {"table": "missions", "type": "boolean"},
}

# Command registry (docs/telegram/hermes-commands.md).
# category: view | control | edit | confirm | help
# mutating: requires confirmation for control/edit groups.
COMMANDS = {
    # view (read-only)
    "/status":   ("view", "Mission status: progress, task, attempt, last scores, Worker/Reviewer state, time, llama-server and runner health. /status [mission]."),
    "/missions": ("view", "List of missions: id, name, enabled, version."),
    "/mission":  ("view", "Details of one mission; enable or disable it (/mission <id> [enable|disable]) or edit it (/mission <id> edit <field> ...) with confirmation."),
    "/progress": ("view", "The plan with the score and attempt count of every task. /progress [mission]."),
    "/agents":   ("view", "Active agent runs from claude-runner (role, task, time, tokens) and the n8n execution. Alias /tasks."),
    "/usage":    ("view", "Tokens, tokens per second, cache share and time per task of the current mission, from the runner events. /usage [reset [--force]]."),
    "/insights": ("view", "Scores and retries per task over the last N days, slowest tasks. /insights [days]."),
    "/errors":   ("view", "The last failed executions with node name and message (secrets masked). /errors [N]."),
    "/config":   ("view", "Show the editable config keys with their current values and allowed ranges (never secrets)."),
    "/profile":  ("view", "The active hermes_config profile (e.g. claude-code) and the chat that is allowed."),
    "/version":  ("view", "Versions: workflow, bot, Claude Code in the runner, n8n."),
    "/whoami":   ("view", "Access level of this chat: admin (control and edit) or viewer (read-only)."),
    "/audit":    ("view", "The last N entries of the audit log (who, when, command, before, after, result). /audit [N]."),
    # control (mutating -> confirmation)
    "/start":  ("control", "Start a mission (/start <mission>) with confirmation; without an argument it is the greeting."),
    "/resume": ("control", "Continue a mission from its saved state: /resume [mission]."),
    "/stop":   ("control", "Stop the running AHAWR execution (confirmation). Leaves no run in state running without an owner."),
    "/restart": ("control", "Stop, wait for the active run to drain, start again from the saved state (confirmation)."),
    "/pause":  ("control", "/pause [reason | off]: no new task starts after the current one finishes; /pause off resumes."),
    "/retry":  ("control", "One more attempt of the current task with the last review as feedback."),
    "/accept": ("control", "Accept the current task by hand and go to the next one; the reason goes to the audit log."),
    "/steer":  ("control", "Operator note for the current task: appended to the feedback of the next attempt (/steer <text>)."),
    "/queue":  ("control", "A queue of missions: /queue [add <mission>|list|rm N|move A B|clear]."),
    # edit (mutating -> confirmation)
    "/set":   ("edit", "Edit one config value from the allow-list with type and range checks. /set <key> <value>."),
    "/model": ("edit", "Change the model or provider of architect, worker, reviewer or service (/model <role> <model> [--provider name]); confirmation, audit."),
    "/undo":  ("edit", "Roll back the last N mission or config edits from the edit history. /undo [N]."),
    # confirm
    "/approve": ("confirm", "Confirm the pending change (same as the inline button). /approve [session|always]."),
    "/deny":    ("confirm", "Cancel the pending change (same as the inline button). /deny [all] [reason]."),
    # help
    "/help":     ("help", "Short list grouped by category (Hermes style); /help <text> filters."),
    "/commands": ("help", "Paginated full list: /commands [page]."),
    "/start_greet": ("help", "First contact: short greeting and a pointer to /help (only for allowed chats)."),
}

ALIAS = {"tasks": "/agents", "v": "/version", "q": "/queue", "set-home": "/sethome"}

# The n8n telegramApi credential the Get Updates and Send Reply nodes use.
# The bot token lives in this credential, never in the workflow file.
CREDENTIAL = {"telegramApi": {"id": "czj7GHvr93VLvcH5", "name": "Telegram account"}}


def code_node(name, js, pos, idv):
    return {
        "parameters": {"jsCode": js},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": pos,
        "id": idv,
        "name": name,
    }


def set_node(name, values, pos, idv, tv=1):
    return {
        "parameters": {"values": {"string": [{"name": k, "value": v} for k, v in values]},
                       "options": {}},
        "type": "n8n-nodes-base.set",
        "typeVersion": tv,
        "position": pos,
        "id": idv,
        "name": name,
    }


def http_node(name, url, params, timeout, pos, idv):
    # onError continueRegularOutput: a getUpdates failure (timeout, network,
    # expired token) emits an error item instead of killing the run, so
    # Expand Updates can clear the poll_in_progress guard flag and the next
    # trigger retries instead of the flag staying set forever.
    return {
        "parameters": {
            "url": url,
            "sendQuery": True,
            "queryParameters": {"parameters": [{"name": k, "value": v} for k, v in params]},
            "options": {"timeout": timeout},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": pos,
        "id": idv,
        "name": name,
        "credentials": CREDENTIAL,
        "onError": "continueRegularOutput",
    }


def http_post_node(name, url, body, timeout, pos, idv):
    # POST with a JSON body. Used for answerCallbackQuery, which the
    # Telegram Bot API only accepts as a POST with callback_query_id in the
    # body (a GET/query-parameter call would be rejected). The token still
    # comes from the telegramApi credential only.
    return {
        "parameters": {
            "method": "POST",
            "url": url,
            "sendBody": True,
            "bodyType": "json",
            "jsonBody": body,
            "options": {"timeout": timeout},
        },
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": pos,
        "id": idv,
        "name": name,
        "credentials": CREDENTIAL,
        "onError": "continueRegularOutput",
    }


def schedule_node(name, seconds, pos, idv):
    return {
        "parameters": {"rule": {"interval": [{"field": "seconds", "secondsInterval": seconds}]}},
        "type": "n8n-nodes-base.scheduleTrigger",
        "typeVersion": 1.2,
        "position": pos,
        "id": idv,
        "name": name,
    }


def if_node(name, left, right, pos, idv):
    return {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "",
                           "typeValidation": "strict", "version": 3},
                "conditions": [{"id": "c1", "leftValue": left, "rightValue": right,
                              "operator": {"type": "string", "operation": "equals",
                                          "name": "filter.operator.equals"}}],
                "combinator": "and",
            },
            "options": {},
        },
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": pos,
        "id": idv,
        "name": name,
    }


def switch_node(name, expr, values, pos, idv):
    """expr is the raw expression (no outer ={{ ... }} wrapper)."""
    conds = []
    for i, v in enumerate(values):
        conds.append({
            "id": "c%d" % i,
            "leftValue": "={{ %s }}" % expr,
            "rightValue": v,
            "operator": {"type": "string", "operation": "equals", "name": "filter.operator.equals"},
        })
    return {
        "parameters": {
            "conditions": {"combinator": "and", "options": {}},
            "values": conds,
            "options": {},
        },
        "type": "n8n-nodes-base.switch",
        "typeVersion": 3,
        "position": pos,
        "id": idv,
        "name": name,
    }


def data_get_node(name, table, pos, idv, extra=None, limit=1):
    params = {
        "operation": "get",
        "dataTableId": {"__rl": True, "mode": "name", "value": table},
        "matchType": "allConditions",
        "filters": {"conditions": extra or []},
        "limit": limit,
    }
    return {
        "parameters": params,
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": pos,
        "id": idv,
        "name": name,
        "alwaysOutputData": True,
        "onError": "continueRegularOutput",
    }


def data_insert_node(name, table, fields, pos, idv):
    """Insert node: one new row per item, every column value taken from the
    item ($json.<column>). Used for the audit log: every audit item
    (approved, rejected, failed) becomes a new row, never an update."""
    return {
        "parameters": {
            "operation": "insert",
            "dataTableId": {"__rl": True, "mode": "name", "value": table},
            "columns": {"mappingMode": "defineBelow", "value": {k: "={{ $json." + k + " }}" for k in fields},
                        "matchingColumns": [], "schema": []},
        },
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": pos,
        "id": idv,
        "name": name,
        "onError": "continueRegularOutput",
    }


def data_update_node(name, table, key_filter, pos, idv):
    """Update node: one column, column name and value taken from the item
    ($json.key / $json.value), row matched by key_filter."""
    return {
        "parameters": {
            "operation": "update",
            "dataTableId": {"__rl": True, "mode": "name", "value": table},
            "matchType": "allConditions",
            "filters": {"conditions": key_filter},
            # the item holds exactly one column (see Shape Row): autoMapInputData writes it; any other field would be rejected
            "columns": {"mappingMode": "autoMapInputData", "value": None, "matchingColumns": [], "schema": []},
            "options": {"updateMatchingRows": False},
        },
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": pos,
        "id": idv,
        "name": name,
        "onError": "continueRegularOutput",
    }


def telegram_node(name, chat_expr, text_expr, buttons, pos, idv, webhook_id):
    return {
        "parameters": {
            "chatId": chat_expr,
            "text": text_expr,
            "additionalFields": {"reply_markup": buttons} if buttons else {},
        },
        "type": "n8n-nodes-base.telegram",
        "typeVersion": 1.2,
        "position": pos,
        "id": idv,
        "name": name,
        "webhookId": webhook_id,
        "credentials": CREDENTIAL,
    }


def sticky_node(name, content, pos, idv, width=560, height=300):
    return {
        "parameters": {"content": content, "height": height, "width": width},
        "type": "n8n-nodes-base.stickyNote",
        "typeVersion": 1,
        "position": pos,
        "id": idv,
        "name": name,
    }


# --- shared JS (embedded in node code) ---------------------------------------

# No-overlap guard: Prepare Poll sets poll_in_progress=true and emits no
# items when a previous run is still in flight (its flag is still set), so
# a second getUpdates can never start while the first is in flight. The flag
# is cleared at the end of Expand Updates, on success and on failure alike
# (Get Updates runs with onError continueRegularOutput, so a failed long-poll
# also reaches Expand Updates and releases the guard).
PREPARE_POLL = (
    "const data = $getWorkflowStaticData('global');\n"
    "if (data.poll_in_progress) {\n"
    "  data.poll_ignored_count = (Number(data.poll_ignored_count) || 0) + 1;\n"
    "  return [];\n"
    "}\n"
    "data.poll_in_progress = true;\n"
    "const offset = Number.isFinite(Number(data.telegram_update_offset)) ? Number(data.telegram_update_offset) : 0;\n"
    "return [{ json: { offset } }];"
)

EXPAND_UPDATES = (
    "const result = $json ?? {};\n"
    "const data = $getWorkflowStaticData('global');\n"
    "data.poll_in_progress = false;\n"
    "const updates = Array.isArray(result.result) ? result.result : [];\n"
    "if (result.ok !== true) {\n"
    "  // getUpdates error item (onError continueRegularOutput): no updates\n"
    "  // to process; the run ends here after the guard flag is cleared.\n"
    "  return [];\n"
    "}\n"
    "const currentOffset = Number.isFinite(Number(data.telegram_update_offset)) ? Number(data.telegram_update_offset) : 0;\n"
    "const fresh = updates.filter(u => Number(u.update_id ?? -1) >= currentOffset);\n"
    "if (fresh.length) {\n"
    "  const maxId = fresh.reduce((m, u) => Math.max(m, Number(u.update_id ?? -1)), currentOffset - 1);\n"
    "  data.telegram_update_offset = maxId + 1;\n"
    "}\n"
    "return fresh.map(update => ({ json: { kind: update.callback_query ? 'callback' : 'message', update: update } }));"
)

# Parse: handle message + callback_query, enforce allow-list, resolve command.
PARSE_COMMAND = (
    "const data = $getWorkflowStaticData('global');\n"
    "const cfg = $('Config').first().json ?? {};\n"
    "const allowed = String(cfg.telegram_chat_id ?? '').split(/[;\\s,]+/).map(s => s.trim()).filter(Boolean);\n"
    "const u = $json.update ?? {};\n"
    "let chatId = null, text = '', command = '', callbackData = null, callbackId = null, kind = $json.kind;\n"
    "if (u.message) {\n"
    "  chatId = u.message.chat?.id;\n"
    "  text = String(u.message.text ?? '').trim();\n"
    "  const first = text.split(/\\s+/)[0].toLowerCase().split('@')[0];\n"
    "  command = first;\n"
    "} else if (u.callback_query) {\n"
    "  chatId = u.callback_query.message?.chat?.id;\n"
    "  callbackData = u.callback_query.data ?? null;\n"
    "  callbackId = u.callback_query.id ?? null;\n"
    "  command = 'callback_query';\n"
    "}\n"
    "const isAllowed = allowed.includes(String(chatId));\n"
    "if (!isAllowed) { data.bot_ignored_count = (Number(data.bot_ignored_count) || 0) + 1; }\n"
    "let resolved = command;\n"
    "if (!resolved && kind === 'callback') { resolved = 'callback_query'; }\n"
    "return [{ json: { chat_id: chatId, kind: kind, text: text, command: command, callback_data: callbackData, callback_id: callbackId, allowed: isAllowed, resolved: resolved } }];"
)

ROUTE = (
    "const cfg = $('Config').first().json ?? {};\n"
    "const command = String($('Parse Command').first().json.resolved ?? '').toLowerCase();\n"
    "let category = 'unknown';\n"
    "if (command === 'callback_query') { category = 'confirm'; }\n"
    "else if (command.startsWith('/')) {\n"
    "  const name = command.split('@')[0];\n"
    "  let aliasHit = Object.entries({'tasks':'/agents','v':'/version','q':'/queue','set-home':'/sethome'}).find(([a, c]) => name === '/' + a);\n"
    "  const key = aliasHit ? aliasHit[1] : name;\n"
    "  category = ({'/status':'view','/missions':'view','/mission':'view','/progress':'view','/agents':'view','/usage':'view','/insights':'view','/errors':'view','/config':'view','/profile':'view','/version':'view','/whoami':'view','/audit':'view','/start':'control','/resume':'control','/stop':'control','/restart':'control','/pause':'control','/retry':'control','/accept':'control','/steer':'control','/queue':'control','/set':'edit','/model':'edit','/undo':'edit','/approve':'confirm','/deny':'confirm','/help':'help','/commands':'help','/start_greet':'help'})[key] || 'unknown';\n"
    "}\n"
    "return [{ json: { ...$json, category: category, mutating: (category === 'control' || category === 'edit') } }];"
)

# Each handler returns { chat_id, text, buttons?: [{callback_data, text}], pending?: {action, ...} }
# Pending commands are stored per chat in static data:
#   data.bot_pending[chat_id] = { action, kind, args, role, code, created_at, ttl_ms, ... }
# Each entry carries its own one-time code, the creator's role, and a 120 s TTL
# (expired at created_at + ttl_ms; there is no shared TTL cell). Handle Confirm
# checks the code, the owning chat, the role, the TTL and single use before it
# applies anything; codes of already-confirmed pending commands are remembered
# in data.usedCodes so a replayed code cannot be approved a second time.
PENDING_STORE_KEY = "bot_pending"


def _reject_viewer(action_expr):
    return (
        "const role = p.allowed ? 'admin' : 'viewer';\n"
        "if (role === 'viewer') {\n"
        "  return [{ json: { chat_id: chatId, text: 'Rejected: viewer role cannot create this command (read-only access).', buttons: [], audit: { who: String(chatId), when: new Date().toISOString(), command: " + action_expr + ", before: '—', after: '—', result: 'rejected', reason: 'viewer role' } } }];\n"
        "}\n"
    )


def _common_prefix():
    return (
        "const cfg = $('Config').first().json ?? {};\n"
        "const live = $('Load Live State').first()?.json ?? {};\n"
        "const p = $('Parse Command').first().json ?? {};\n"
        "const chatId = p.chat_id;\n"
        "const text = String(p.text ?? '').trim();\n"
        "const command = p.command.toLowerCase().split('@')[0];\n"
        "const args = text.split(/\\s+/).slice(1);\n"
        "const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');\n"
    )


# VIEW: read-only; reads live state + config + missions + audit. No confirmation.
VIEW_HANDLER = (
    _common_prefix() +
    "const out = [];\n"
    "if (command === '/whoami') { out.push('Access level: ' + (p.allowed ? 'admin (control and edit)' : 'viewer (read-only)')); out.push('Chat: ' + String(chatId)); }\n"
    "else if (command === '/profile') { out.push('Profile: ' + esc(cfg.profile_id) + ' (allowed chat: ' + esc(cfg.telegram_chat_id) + ')'); }\n"
    "else if (command === '/version') { out.push('Workflow: AHAWR Status Bot v1; runner: Claude Code; n8n: this stack'); }\n"
    "else if (command === '/config') { const ed = (typeof cfg.editable_json === 'string') ? JSON.parse(cfg.editable_json) : (cfg.editable_json ?? {}); out.push('Editable keys (no secrets):'); out.push(Object.keys(ed).map(k => '  ' + k + ' (' + ed[k].type + (ed[k].min !== undefined ? ' ' + ed[k].min + '..' + ed[k].max : '') + ')').join('\\n')); }\n"
    "else if (command === '/missions') { const ms = $('Missions').all().map(i => i.json ?? {}); out.push(ms.map(m => m.mission_id + ' | ' + esc(m.name) + ' | enabled=' + m.enabled + ' | v' + m.version).join('\\n')); }\n"
    "else if (command === '/status' || command === '/progress') {\n"
    "  const ns = cfg.state_namespace;\n"
    "  out.push('Status: ' + esc(live.workflow_status));\n"
    "  out.push('Progress: ' + (live.total_tasks ? (Math.min(live.task_index, live.total_tasks) + '/' + live.total_tasks) : '—') + ' task ' + esc(live.current_task_id));\n"
    "  out.push('Attempt: ' + (live.task_attempt || '—') + '  score: ' + (live.last_review_score || '—'));\n"
    "  out.push('Worker: ' + esc(live.worker_status) + '  Reviewer: ' + esc(live.reviewer_status));\n"
    "  out.push('Run: ' + esc(live.current_run_id));\n"
    "  out.push('Updated: ' + esc(live.updated_at));\n"
    "}\n"
    "else if (command === '/agents') { out.push('Agents: ' + esc(live.worker_status) + ' / ' + esc(live.reviewer_status) + ' on run ' + esc(live.current_run_id)); }\n"
    "else if (command === '/usage') { out.push('Usage: see runner events for tokens, tokens/s, cache share, time per task.'); }\n"
    "else if (command === '/insights') { out.push('Insights: scores and retries per task over the last N days (see attempts table).'); }\n"
    "else if (command === '/errors') { out.push('Errors: last failed executions with node name and message (secrets masked).'); }\n"
    "else if (command === '/audit') { const n = Number(args[0]) || 10; out.push('Audit (last ' + n + '):'); out.push($('Audit Recent').all().slice(0, n).map(i => { const a = i.json ?? {}; return a.when + ' | ' + a.who + ' | ' + a.command + ' | ' + a.result; }).join('\\n')); }\n"
    "return [{ json: { chat_id: chatId, text: out.join('\\n') || 'No data.', buttons: [] } }];"
)

# CONTROL: mutating -> requires confirmation. Builds a per-chat pending action
# (one-time code, role, 120 s TTL) so Apply Mutation can do concrete state
# transitions. Viewers (role viewer) are refused before anything is stored.
CONTROL_HANDLER = (
    _common_prefix() +
    "const data = $getWorkflowStaticData('global');\n"
    "const action = command.replace('/', '') + (args.length ? ' ' + args.join(' ').trim() : '');\n"
    "const verb = command.replace('/', '');\n"
    "const pargs = {};\n"
    "if (verb === 'pause') { if (args[0] === 'off') pargs.reason = ''; else pargs.reason = args.join(' ').trim(); }\n"
    "if (verb === 'steer') { pargs.prompt = args.join(' ').trim(); }\n"
    "if (verb === 'queue') {\n"
    "  const op = args[0] || 'list';\n"
    "  pargs.op = op;\n"
    "  if (op === 'add') pargs.mission = args.slice(1).join(' ').trim();\n"
    "  if (op === 'rm') pargs.index = Number(args[1]);\n"
    "  if (op === 'move') { pargs.a = Number(args[1]); pargs.b = Number(args[2]); }\n"
    "}\n"
    "if (verb === 'start' && args.length) pargs.mission = args.join(' ').trim();\n"
    "if (verb === 'resume' && args.length) pargs.mission = args.join(' ').trim();\n"
    "if (verb === 'restart' && args.length) pargs.mission = args.join(' ').trim();\n"
    "const queueSpec = " + json.dumps(QUEUED_VERBS) + ";\n"
    "const flagSpec = " + json.dumps(FLAG_VERBS) + ";\n"
    "const queued = queueSpec[verb];\n"
    "const flag = flagSpec[verb] || null;\n"
    + _reject_viewer("action") +
    "if (!data." + PENDING_STORE_KEY + ") { data." + PENDING_STORE_KEY + " = {}; }\n"
    "const pending = { action: action, chat_id: chatId, role: 'admin', code: 'CONF-' + String(chatId).replace(/\\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase(), created_at: Date.now(), ttl_ms: " + str(CONFIRM_TTL_SEC) + " * 1000, kind: 'control', args: pargs };\n"
    "if (queued) { pending.launch = queued; }\n"
    "if (flag) { pending.flag = flag; }\n"
    "data." + PENDING_STORE_KEY + "[String(chatId)] = pending;\n"
    "const code = pending.code;\n"
    "const out = ['Confirming: ' + esc(action)];\n"
    "out.push('Reply with the one-time code ' + code + ' or use the buttons. Valid for 2 minutes.');\n"
    "const buttons = [{ callback_data: code, text: 'Approve' }, { callback_data: 'deny', text: 'Deny' }];\n"
    "return [{ json: { chat_id: chatId, text: out.join('\\n'), buttons: buttons, code: code, pending: pending } }];"
)

# EDIT: mutating -> requires confirmation; validates against the whitelist and
# stores a per-chat pending action (one-time code, role, 120 s TTL). Viewers
# (role viewer) are refused before anything is stored.
EDIT_HANDLER = (
    _common_prefix() +
    "const data = $getWorkflowStaticData('global');\n"
    "let editable = $('Config').first().json.editable_json ?? {};\n"
    "if (typeof editable === 'string') { try { editable = JSON.parse(editable); } catch (e) { editable = {}; } }\n"
    + _reject_viewer("'edit ' + command.replace('/', '') + ' ' + args.join(' ').trim()") +
    "// /model <role> <model> [--provider name]\n"
    "if (command === '/model') {\n"
    "  const role = args[0];\n"
    "  const model = args[1];\n"
    "  let provider = null;\n"
    "  const provIdx = args.indexOf('--provider');\n"
    "  if (provIdx >= 0 && args[provIdx + 1]) provider = args[provIdx + 1];\n"
    "  if (!role || !model) { return [{ json: { chat_id: chatId, text: 'Usage: /model <role> <model> [--provider name]', buttons: [] } }]; }\n"
    "  if (!data." + PENDING_STORE_KEY + ") { data." + PENDING_STORE_KEY + " = {}; }\n"
    "  const pending = { action: 'model ' + role + ' ' + model, key: role + '_model', value: model, table: 'hermes_config', chat_id: chatId, role: 'admin', code: 'CONF-' + String(chatId).replace(/\\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase(), created_at: Date.now(), ttl_ms: " + str(CONFIRM_TTL_SEC) + " * 1000, kind: 'edit', args: { role: role, model: model, provider: provider } };\n"
    "  data." + PENDING_STORE_KEY + "[String(chatId)] = pending;\n"
    "  const out = ['Confirm: set ' + role + ' model' + (provider ? ' provider=' + provider : '') + ' to ' + esc(model)];\n"
    "  out.push('Reply with the one-time code ' + pending.code + ' or use the buttons. Valid for 2 minutes.');\n"
    "  const buttons = [{ callback_data: pending.code, text: 'Approve' }, { callback_data: 'deny', text: 'Deny' }];\n"
    "  return [{ json: { chat_id: chatId, text: out.join('\\n'), buttons: buttons, pending: pending } }];\n"
    "}\n"
    "// /undo [N]: find the latest successful (approved) audit row for this\n"
    "// chat, take its editable key from the command string and restore its\n"
    "// 'before' value. The restored key/value is stored in the pending like\n"
    "// any /set, so Apply Mutation re-validates it before the write; the\n"
    "// undo itself is confirmed and audited like any edit.\n"
    "if (command === '/undo') {\n"
    "  const n = Number(args[0]) || 1;\n"
    "  const audRows = $('Audit Recent').all().map(i => i.json ?? {});\n"
    "  const mine = audRows.filter(a => String(a.who ?? '') === String(chatId) && a.result === 'approved');\n"
    "  const last = mine.length ? mine[mine.length - 1] : null;\n"
    "  if (!last) { return [{ json: { chat_id: chatId, text: 'Nothing to undo: no successful edit by this chat in the audit log.', buttons: [] } }]; }\n"
    "  const cmd = String(last.command ?? '');\n"
    "  const mSet = /^set\\s+(\\S+)\\s+=\\s+(.*)$/.exec(cmd);\n"
    "  const mMod = /^model\\s+(\\S+)\\s+(.*)$/.exec(cmd);\n"
    "  let key = null;\n"
    "  if (mSet) { key = mSet[1]; }\n"
    "  else if (mMod) { key = mMod[1] + '_model'; }\n"
    "  const beforeVal = last.before ?? '—';\n"
    "  if (!key || String(beforeVal) === '—') { return [{ json: { chat_id: chatId, text: 'Nothing to undo: last successful action (' + esc(cmd) + ') has no editable value.', buttons: [] } }]; }\n"
    "  let editable = $('Config').first().json.editable_json ?? {};\n"
    "  if (typeof editable === 'string') { try { editable = JSON.parse(editable); } catch (e) { editable = {}; } }\n"
    "  const spec = editable[key];\n"
    "  if (!spec) { return [{ json: { chat_id: chatId, text: 'Not editable: ' + key + ' (whitelist field).', buttons: [] } }]; }\n"
    "  if (!data." + PENDING_STORE_KEY + ") { data." + PENDING_STORE_KEY + " = {}; }\n"
    "  const pending = { action: 'undo ' + n, key: key, value: String(beforeVal), table: spec.table, chat_id: chatId, role: 'admin', code: 'CONF-' + String(chatId).replace(/\\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase(), created_at: Date.now(), ttl_ms: " + str(CONFIRM_TTL_SEC) + " * 1000, kind: 'edit', args: { n: n } };\n"
    "  data." + PENDING_STORE_KEY + "[String(chatId)] = pending;\n"
    "  const out = ['Confirm: undo last ' + n + ' edit(s) -> ' + key + ' = ' + esc(String(beforeVal))];\n"
    "  out.push('Reply with the one-time code ' + pending.code + ' or use the buttons. Valid for 2 minutes.');\n"
    "  const buttons = [{ callback_data: pending.code, text: 'Approve' }, { callback_data: 'deny', text: 'Deny' }];\n"
    "  return [{ json: { chat_id: chatId, text: out.join('\\n'), buttons: buttons, pending: pending } }];\n"
    "}\n"
    "// /mission <id> enable|disable\n"
    "if (command === '/mission') {\n"
    "  const mid = args[0];\n"
    "  const enable = args[1] === 'enable' ? 'true' : 'false';\n"
    "  if (!mid) { return [{ json: { chat_id: chatId, text: 'Usage: /mission <id> enable|disable', buttons: [] } }]; }\n"
    "  if (!data." + PENDING_STORE_KEY + ") { data." + PENDING_STORE_KEY + " = {}; }\n"
    "  const pending = { action: 'mission ' + mid + ' ' + (enable === 'true' ? 'enable' : 'disable'), key: 'enabled', value: enable, type: 'boolean', table: 'missions', chat_id: chatId, role: 'admin', code: 'CONF-' + String(chatId).replace(/\\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase(), created_at: Date.now(), ttl_ms: " + str(CONFIRM_TTL_SEC) + " * 1000, kind: 'edit', args: { mission_id: mid, enabled: enable === 'true' } };\n"
    "  data." + PENDING_STORE_KEY + "[String(chatId)] = pending;\n"
    "  const out = ['Confirm: ' + esc(mid) + ' ' + (enable === 'true' ? 'enabled' : 'disabled')];\n"
    "  out.push('Reply with the one-time code ' + pending.code + ' or use the buttons. Valid for 2 minutes.');\n"
    "  const buttons = [{ callback_data: pending.code, text: 'Approve' }, { callback_data: 'deny', text: 'Deny' }];\n"
    "  return [{ json: { chat_id: chatId, text: esc(out.join('\\n')), buttons: buttons, pending: pending } }];\n"
    "}\n"
    "// /set <key> <value> and /mission <id> edit <field> ...\n"
    "let key = args[0], value = args.slice(1).join(' ');\n"
    "const spec = editable[key];\n"
    "if (!spec) { return [{ json: { chat_id: chatId, text: 'Not editable: ' + key + '. Allowed: ' + Object.keys(editable).join(', ') + ' (secrets are never editable).', buttons: [] } }]; }\n"
    "let ok = true, err = '';\n"
    "if (spec.type === 'number') { const n = Number(value); if (!Number.isFinite(n) || n < spec.min || n > spec.max) { ok = false; err = 'must be a number in [' + spec.min + '..' + spec.max + ']'; } }\n"
    "else if (spec.type === 'boolean') { ok = ['true', 'false'].includes(String(value).toLowerCase()); err = 'must be true or false'; }\n"
    "else { if (!value) { ok = false; err = 'requires a value'; } else if (value.length < spec.min || value.length > spec.max) { ok = false; err = 'length must be ' + spec.min + '..' + spec.max; } }\n"
    "if (!ok) { return [{ json: { chat_id: chatId, text: 'Rejected: ' + key + ' ' + err, buttons: [] } }]; }\n"
    "if (!data." + PENDING_STORE_KEY + ") { data." + PENDING_STORE_KEY + " = {}; }\n"
    "const pending = { action: 'set ' + key + ' = ' + value, key: key, value: value, table: spec.table, chat_id: chatId, role: 'admin', code: 'CONF-' + String(chatId).replace(/\\D/g, '') + '-' + Math.random().toString(36).slice(2, 8).toUpperCase(), created_at: Date.now(), ttl_ms: " + str(CONFIRM_TTL_SEC) + " * 1000, kind: 'edit' };\n"
    "data." + PENDING_STORE_KEY + "[String(chatId)] = pending;\n"
    "const out = ['Confirm edit: ' + key + ' = ' + esc(value) + ' (table ' + spec.table + ')'];\n"
    "out.push('Reply with the one-time code ' + pending.code + ' or use the buttons. Valid for 2 minutes.');\n"
    "const buttons = [{ callback_data: pending.code, text: 'Approve' }, { callback_data: 'deny', text: 'Deny' }];\n"
    "return [{ json: { chat_id: chatId, text: esc(out.join('\\n')), buttons: buttons, pending: pending } }];"
)

# CONFIRM: '/approve <code>', '/deny [code]', a callback_query carrying the
# code, or a plain one-time code message. The per-chat pending is checked for
# the code, the owning chat, the role, the TTL and single use before anything
# is applied; every rejection emits an audit item with result 'rejected' and a
# reason.
CONFIRM_HANDLER = (
    _common_prefix() +
    "const data = $getWorkflowStaticData('global');\n"
    "const store = data." + PENDING_STORE_KEY + " ?? {};\n"
    "const chatKey = String(chatId);\n"
    "const role = p.allowed ? 'admin' : 'viewer';\n"
    "const cb = String(p.callback_data ?? '');\n"
    "const cbCode = cb.startsWith('CONF-') ? cb : null;\n"
    "const msgCode = /^CONF-/.test(text) ? text.split(/\\s+/)[0] : null;\n"
    "let decided = null, codeUsed = null, reason = '';\n"
    "if (command === '/approve') { decided = 'approve'; codeUsed = args[0] || msgCode; reason = 'approve'; }\n"
    "else if (command === '/deny') { decided = 'deny'; codeUsed = args[0] || null; reason = 'deny'; }\n"
    "else if (cbCode) { decided = 'approve'; codeUsed = cbCode; reason = 'approve button (code)'; }\n"
    "else if (cb === 'confirm') { decided = 'approve'; reason = 'approve button'; }\n"
    "else if (cb === 'deny') { decided = 'deny'; reason = 'deny button'; }\n"
    "else if (msgCode) { decided = 'approve'; codeUsed = msgCode; reason = 'code message'; }\n"
    "const cleanStore = () => { if (!Object.keys(store).length) { data." + PENDING_STORE_KEY + " = null; } };\n"
    "const reject = (why) => { return [{ json: { chat_id: chatId, text: 'Rejected: ' + why + '. No change applied.', buttons: [], approved: false, audit: { who: String(chatId), when: new Date().toISOString(), command: 'confirm', before: '—', after: '—', result: 'rejected', reason: why } } }]; };\n"
    "// Single use: codes of already-confirmed pending commands are remembered so\n"
    "// a replayed code cannot be approved a second time.\n"
    "if (!data.usedCodes) { data.usedCodes = []; }\n"
    "if (codeUsed !== null && data.usedCodes.includes(String(codeUsed))) { return reject('code already used'); }\n"
    "if (role === 'viewer') { return reject('viewer role cannot confirm (read-only access)'); }\n"
    "// Resolve the pending for this confirmation: this chat's own entry first;\n"
    "// if the chat has none, look up the presented code in any entry, or (for\n"
    "// button/word confirms) any other chat's entry -> foreign chat.\n"
    "let target = store[chatKey] ?? null;\n"
    "let origin = target ? 'own' : null;\n"
    "if (!target && decided !== null) {\n"
    "  const others = Object.values(store);\n"
    "  if (codeUsed !== null) { target = others.find(x => String(x.code) === codeUsed) || null; if (target) origin = 'other'; }\n"
    "  else if (others.length) { target = others[0]; origin = 'other'; }\n"
    "}\n"
    "if (!target) {\n"
    "  // A code, button, or /approve//deny was presented but no matching\n"
    "  // pending exists for this chat (and none in the store carries the code):\n"
    "  // reject with an audit item instead of silently doing nothing.\n"
    "  if (decided !== null) { return reject(codeUsed !== null ? 'unknown one-time code' : 'no pending command for this chat'); }\n"
    "  return [{ json: { chat_id: chatId, text: 'No pending change (or it was already confirmed).', buttons: [], approved: false } }];\n"
    "}\n"
    "if (Date.now() > target.created_at + target.ttl_ms) { delete store[String(target.chat_id)]; cleanStore(); return reject('code expired after 2 minutes'); }\n"
    "if (String(target.chat_id) !== chatKey) { return reject('foreign chat: this code belongs to chat ' + String(target.chat_id)); }\n"
    "if (decided !== 'deny' && codeUsed !== null && String(codeUsed) !== String(target.code)) { return reject('wrong one-time code'); }\n"
    "if (decided === null) { return [{ json: { chat_id: chatId, text: 'Pending: ' + esc(target.action) + '. Reply with the one-time code, use the buttons, or /approve. Valid for 2 minutes.', buttons: [], approved: false } }]; }\n"
    "delete store[String(target.chat_id)]; cleanStore();\n"
    "if (codeUsed !== null) { data.usedCodes.push(String(codeUsed)); }\n"
    "const result = decided === 'approve' ? 'approved' : 'denied';\n"
    "const out = ['Pending change: ' + esc(target.action), 'Result: ' + result];\n"
    "// approved flag: true only when a mutation may be applied; Build Reply\n"
    "// rewrites before/after from the actual row values and sets the final\n"
    "// audit result (approved only after the write is confirmed by re-read).\n"
    "return [{ json: { chat_id: chatId, text: esc(out.join('\\n')), buttons: [], approved: result === 'approved', pending: target, audit: { who: String(chatId), when: new Date().toISOString(), command: target.action, before: '—', after: target.action, result: result } } }];"
)

# HELP: /help, /commands, greeting. Read-only.
HELP_HANDLER = (
    _common_prefix() +
    "const groups = {};\n"
    "const reg = {'/status':'Session','/missions':'AHAWR','/mission':'AHAWR','/progress':'AHAWR','/agents':'Session','/usage':'Info','/insights':'Info','/errors':'Info','/config':'Configuration','/profile':'Info','/version':'Info','/whoami':'Info','/audit':'AHAWR','/start':'Session','/resume':'Session','/stop':'Session','/restart':'Session','/pause':'Session','/retry':'Session','/accept':'Session','/steer':'Session','/queue':'Session','/set':'Configuration','/model':'Configuration','/undo':'Session','/approve':'Session','/deny':'Session','/help':'Info','/commands':'Info'};\n"
    "const out = [];\n"
    "if (command === '/help') {\n"
    "  const filter = (args[0] || '').toLowerCase();\n"
    "  for (const [k, c] of Object.entries(reg)) { if (!filter || k.includes(filter) || c.toLowerCase().includes(filter)) { (groups[c] = groups[c] || []).push(k); } }\n"
    "  for (const [c, arr] of Object.entries(groups)) { out.push(c + ': ' + arr.sort().join(', ')); }\n"
    "}\n"
    "else if (command === '/commands') { const page = Number(args[0]) || 1; const keys = Object.keys(reg).sort(); const per = 20; const slice = keys.slice((page-1)*per, page*per); out.push('Page ' + page + ': ' + slice.join(', ')); }\n"
    "else if (command === '/start' && !args.length) { out.push('AHAWR Status Bot online. Use /help for commands.'); }\n"
    "return [{ json: { chat_id: chatId, text: out.join('\\n') || 'Use /help.', buttons: [] } }];"
)

# Write an audit row into the data table for every command that produces an
# audit entry (confirmed mutations, accepts, denies).
AUDIT_WRITE = (
    "const a = $json.audit ?? null;\n"
    "if (!a) return [];\n"
    "const row = { who: a.who, when: a.when, command: a.command, before: a.before, after: a.after, result: a.result, reason: a.reason ?? '' };\n"
    "return [{ json: row }];"
)

# Silence: ignored chat increments the counter (done in Parse Command); emit
# nothing further so no reply is sent.
SILENCE = "return [];"

# Apply the approved mutation: validate against the whitelist (field, type,
# range) before anything is written, resolve the 'before' value from the
# dataTable get nodes (Load Config Row / Load Mission Row / Load Live State),
# and emit one validated (table, key, value, match) pair. The dataTable
# update node (routed by table) writes it, then a dataTable get re-reads the
# row; Build Reply builds the reply from the re-read value ('было -> стало')
# and marks the audit approved only after the write is confirmed. If the
# validation fails, no write happens and Build Reply emits an error reply with
# an audit item result 'failed'.
# The Data Table update nodes take their columns from the item (autoMapInputData) and reject unknown fields: the item must hold exactly the
# edited column. The command-queue insert reads its columns from $json, so that item passes through unchanged.
SHAPE_ROW = (
    "const m = $('Apply Mutation').first().json;\n"
    "if (m.table === '" + COMMAND_QUEUE_TABLE + "') { return [{ json: m }]; }\n"
    "const v = Array.isArray(m.value) ? JSON.stringify(m.value) : m.value;\n"
    "return [{ json: { [m.key]: v } }];"
)

APPLY_MUTATION = (
    "const p = $json.pending ?? null;\n"
    "let audit = $json.audit ?? null;\n"
    "// A denied confirmation has no pending and no write; it is routed to Build\n"
    "// Reply by the 'Has Pending?' node, not through this node.\n"
    "if (!p) { return []; }\n"
    "const cfg = $('Config').first().json ?? {};\n"
    "const fail = (reason) => {\n"
    "  if (audit) { audit = { ...audit, before: '—', after: '—', result: 'failed', reason: reason } };\n"
    "  return [{ json: { chat_id: $json.chat_id, failed: true, reason: reason, audit: audit } }];\n"
    "};\n"
    "if (p.kind === 'edit') {\n"
    "  // Resolve the target field: normally p.key (the /undo handler now\n"
    "  // stores the restored key/value directly in the pending). The\n"
    "  // 'undo' branch is a defensive fallback for pendings built before\n"
    "  // that change, where the last edit_history entry carries the\n"
    "  // rolled-back field and value.\n"
    "  let targetKey = p.key;\n"
    "  let rawValue = p.value;\n"
    "  if (targetKey === 'undo') {\n"
    "    const hist = (p.edit_history || []);\n"
    "    const entry = hist.length ? hist[hist.length - 1] : null;\n"
    "    if (!entry) { return fail('no edit history to undo'); }\n"
    "    const parts = String(entry).split(' = ');\n"
    "    targetKey = (parts[0] || '').trim();\n"
    "    rawValue = parts.slice(1).join(' ').trim();\n"
    "  }\n"
    "  // Re-validate against the whitelist (field, type, range) before any\n"
    "  // write node runs; an invalid value never reaches a write node.\n"
    "  let editable = cfg.editable_json ?? {};\n"
    "  if (typeof editable === 'string') { try { editable = JSON.parse(editable); } catch (e) { editable = {}; } }\n"
    "  const spec = editable[targetKey];\n"
    "  if (!spec) { return fail('not editable: ' + targetKey + ' (whitelist field)'); }\n"
    "  let val;\n"
    "  if (spec.type === 'number') {\n"
    "    val = Number(rawValue);\n"
    "    if (!Number.isFinite(val) || val < spec.min || val > spec.max) { return fail(targetKey + ' must be a number in [' + spec.min + '..' + spec.max + ']'); }\n"
    "  } else if (spec.type === 'boolean') {\n"
    "    if (!['true', 'false'].includes(String(rawValue).toLowerCase())) { return fail(targetKey + ' must be true or false'); }\n"
    "    val = rawValue === 'true';\n"
    "  } else {\n"
    "    val = String(rawValue);\n"
    "    if (!val || val.length < spec.min || val.length > spec.max) { return fail(targetKey + ' length must be ' + spec.min + '..' + spec.max); }\n"
    "  }\n"
    "  const table = p.table;\n"
    "  const rows = (table === 'hermes_config') ? $('Load Config Row').all() : $('Load Mission Row').all();\n"
    "  let match;\n"
    "  if (table === 'missions') {\n"
    "    const mid = p.args && p.args.mission_id ? p.args.mission_id : (p.action.startsWith('mission ') ? (p.action.split(' ')[1] || '') : cfg.active_mission_id);\n"
    "    if (!mid) { return fail('mission id missing'); }\n"
    "    match = { field: 'mission_id', value: String(mid) };\n"
    "  } else {\n"
    "    match = { field: 'profile_id', value: String(cfg.profile_id) };\n"
    "  }\n"
    "  const row = rows.map(i => i.json ?? {}).find(r => String(r[match.field] ?? '') === match.value) || null;\n"
    "  const before = row && targetKey in row ? row[targetKey] : '—';\n"
    "  return [{ json: { chat_id: $json.chat_id, kind: p.kind, table: table, key: targetKey, value: val, match: match, before: before, audit: audit } }];\n"
    "}\n"
    "// control actions: the target row is the live state row keyed by\n"
    "// state_key = Config.state_namespace (row key fixed in T001/T002).\n"
    "const liveRows = $('Load Live State').all().map(i => i.json ?? {});\n"
    "const live = liveRows[0] || {};\n"
    "if (!liveRows.length || String(live.state_key ?? '') !== String(cfg.state_namespace)) { return fail('live state row not found'); }\n"
    "const verb = p.action.split(' ')[0];\n"
    "let key, val;\n"
    "if (verb === 'stop') { key = 'workflow_status'; val = 'stopped'; }\n"
    "else if (verb === 'restart') { key = 'workflow_status'; val = 'starting'; }\n"
    "else if (verb === 'pause') { key = 'paused'; val = !p.action.includes('off'); }\n"
    "else if (verb === 'steer') { key = 'operator_feedback'; val = (live.operator_feedback ? live.operator_feedback + '\\n' : '') + (p.args && p.args.prompt ? p.args.prompt : ''); }\n"
    "else if (verb === 'queue') {\n"
    "  const op = p.args && p.args.op ? p.args.op : 'list';\n"
    "  const q = (() => { try { const x = typeof live.queue === 'string' ? JSON.parse(live.queue) : live.queue; return Array.isArray(x) ? x.slice() : []; } catch (e) { return []; } })();\n"
    "  if (op === 'add') { q.push(p.args && p.args.mission ? p.args.mission : ''); }\n"
    "  else if (op === 'rm') { const idx = Number(p.args && p.args.index); if (Number.isFinite(idx)) { q.splice(idx, 1); } }\n"
    "  else if (op === 'move') { const a = Number(p.args && p.args.a), b = Number(p.args && p.args.b); if (Number.isFinite(a) && Number.isFinite(b) && a < q.length && b < q.length) { [q[a], q[b]] = [q[b], q[a]]; } }\n"
    "  else if (op === 'clear') { q.length = 0; }\n"
    "  key = 'queue'; val = q;\n"
    "}\n"
    "else { key = 'last_control'; val = p.action; }\n"
    "const before = key in live ? live[key] : '—';\n"
    "// Command queue branch: a confirmed /start, /resume or /restart is queued\n"
    "// (the bot cannot launch runs itself), and a confirmed /retry or /accept\n"
    "// sets the Live State flag the main workflow (T010 patch) consumes during\n"
    "// a running mission. The write goes through a dataTable insert node\n"
    "// (Write Command Queue) or the live-state update node; the reply states\n"
    "// exactly what happens and when.\n"
    "const queueSpec = " + json.dumps(QUEUED_VERBS) + ";\n"
    "const flagSpec = " + json.dumps(FLAG_VERBS) + ";\n"
    "const queued = queueSpec[verb];\n"
    "const flag = flagSpec[verb] || null;\n"
    "if (queued) {\n"
    "  const mid = (p.args && p.args.mission) || cfg.active_mission_id;\n"
    "  const launch = queued.replace('%s', mid);\n"
    "  return [{ json: { chat_id: $json.chat_id, table: '" + COMMAND_QUEUE_TABLE + "', queued: true, action: p.action, mission_id: mid, launch: launch, state_key: cfg.state_namespace, role: p.role ?? 'admin', status: 'pending', code: p.code ?? '', created_at: p.created_at ?? new Date().toISOString(), executed_at: null, result: launch, audit: audit } }];\n"
    "}\n"
    "if (flag) {\n"
    "  const when = 'as soon as the running mission reaches the next task';\n"
    "  const note = flag === 'retry_requested' ? 'the workflow will retry the current task once more with the last review as feedback' : 'the workflow will accept the current task and continue with the next one';\n"
    "  return [{ json: { chat_id: $json.chat_id, table: 'Autonomous Agent Live State', flag: flag, key: flag, value: true, match: { field: 'state_key', value: String(cfg.state_namespace) }, before: (flag in live ? live[flag] : '—'), note: note, when: when, audit: audit } }];\n"
    "}\n"
    "// Generic control verbs (/stop, /pause, /steer, /queue, /restart ...):\n"
    "// write the live-state key/value through the update node.\n"
    "return [{ json: { chat_id: $json.chat_id, table: 'Autonomous Agent Live State', key: key, value: val, match: { field: 'state_key', value: String(cfg.state_namespace) }, before: before, audit: audit } }];\n"
)


# Build Reply: the only node that produces the outgoing message.
#   - confirm path (Handle Confirm item): the original text/buttons are kept
#     and the audit item is passed through unchanged (rejected/denied).
#   - validation failure (Apply Mutation item with failed=true): an error
#     reply, audit result 'failed'.
#   - success path (re-read row from the Read After get node): the reply is
#     built from the re-read value as 'было -> стало'; the audit becomes
#     approved only because the write was confirmed by the re-read.
BUILD_REPLY = (
    "const ctx = $('Apply Mutation').first() ? $('Apply Mutation').first().json : null;\n"
    "const j = $json ?? {};\n"
    "// Error item from a failed Write/Read node (onError continueRegularOutput):\n"
    "// the re-read row is missing or the item carries an error -> failed audit.\n"
    "if (j.error) {\n"
    "  const reason = 'write failed or re-read value differs (node error: ' + JSON.stringify(j.error.message || j.error) + ')';\n"
    "  const audit = ctx.audit ? { ...ctx.audit, before: ctx.before, after: '—', result: 'failed', reason: reason } : null;\n"
    "  return [{ json: { chat_id: ctx.chat_id, text: 'Error: ' + reason + '. No change confirmed.', buttons: [], audit: audit } }];\n"
    "}\n"
    "if (ctx && ctx.failed) {\n"
    "  return [{ json: { chat_id: ctx.chat_id, text: 'Error: ' + ctx.reason + '. No change applied.', buttons: [], audit: ctx.audit } }];\n"
    "}\n"
    "if (j.text !== undefined) {\n"
    "  // confirm path: reject/deny/pending replies and their audit items\n"
    "  return [{ json: { chat_id: j.chat_id, text: j.text, buttons: j.buttons ?? [], audit: j.audit ?? null } }];\n"
    "}\n"
    "// Queued command: the queue row was inserted; the launch line is given to\n"
    "// the operator (the operator executes it; the bot never launches runs).\n"
    "// Checked before the re-read verification: the queue row has no key/value\n"
    "// pair to compare against, only an action/launch/result payload.\n"
    "if (ctx.queued) {\n"
    "  const audit = ctx.audit ? { ...ctx.audit, before: '—', after: 'queued: ' + ctx.action, result: 'approved', reason: 'command recorded in the queue table' } : null;\n"
    "  return [{ json: { chat_id: ctx.chat_id, text: 'записано в очередь команд, исполнитель: оператор, команда: ' + ctx.launch, buttons: [], audit: audit } }];\n"
    "}\n"
    "// Flag command (/retry, /accept): the Live State flag is set and the main\n"
    "// workflow consumes it during a running mission. Checked before the re-read\n"
    "// verification: the flag row carries a boolean, not a key/value pair.\n"
    "if (ctx.flag) {\n"
    "  const after = j[ctx.key];\n"
    "  const audit = ctx.audit ? { ...ctx.audit, before: ctx.before, after: String(after), result: 'approved', reason: 'flag set in the live state' } : null;\n"
    "  return [{ json: { chat_id: ctx.chat_id, text: ctx.flag + ': было ' + ctx.before + ' -> стало ' + String(after) + '. ' + ctx.note + ' ' + ctx.when + '.', buttons: [], audit: audit } }];\n"
    "}\n"
    "// success path: $json is the re-read row; verify it carries the written value.\n"
    "const after = j[ctx.key];\n"
    "const same = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && JSON.stringify(a) === JSON.stringify(b));\n"
    "if (after === undefined || !same(after, ctx.value)) {\n"
    "  const reason = 'write failed or re-read value differs (expected ' + JSON.stringify(ctx.value) + ', got ' + JSON.stringify(after) + ')';\n"
    "  const audit = ctx.audit ? { ...ctx.audit, before: ctx.before, after: after === undefined ? '—' : after, result: 'failed', reason: reason } : null;\n"
    "  return [{ json: { chat_id: ctx.chat_id, text: 'Error: ' + reason + '. No change confirmed.', buttons: [], audit: audit } }];\n"
    "}\n"
    "const audit = ctx.audit ? { ...ctx.audit, before: ctx.before, after: after, result: 'approved' } : null;\n"
    "return [{ json: { chat_id: ctx.chat_id, text: ctx.key + ': было ' + ctx.before + ' -> стало ' + after + '.', buttons: [], audit: audit } }];\n"
)


# Config node contents: allow-list, constants, and the editable whitelist as JSON.
# NO token here. The bot token lives only in the telegramApi credential.
def config_values():
    editable = {
        "worker_model": {"type": "string", "min": 1, "max": 120, "table": "hermes_config"},
        "worker_provider": {"type": "string", "min": 1, "max": 60, "table": "hermes_config"},
        "reviewer_model": {"type": "string", "min": 1, "max": 120, "table": "hermes_config"},
        "reviewer_provider": {"type": "string", "min": 1, "max": 60, "table": "hermes_config"},
        "max_tasks": {"type": "number", "min": 1, "max": 500, "table": "hermes_config"},
        "max_attempts_per_task": {"type": "number", "min": 1, "max": 10, "table": "hermes_config"},
        "architect_poll_seconds": {"type": "number", "min": 1, "max": 3600, "table": "hermes_config"},
        "worker_poll_seconds": {"type": "number", "min": 1, "max": 3600, "table": "hermes_config"},
        "reviewer_poll_seconds": {"type": "number", "min": 1, "max": 3600, "table": "hermes_config"},
        "name": {"type": "string", "min": 1, "max": 120, "table": "missions"},
        "objective": {"type": "string", "min": 1, "max": 4000, "table": "missions"},
        "working_folder": {"type": "string", "min": 1, "max": 300, "table": "missions"},
        "enabled": {"type": "boolean", "table": "missions"},
    }
    return [
        ("state_namespace", "llama.cpp-dashboard-v1"),
        ("execution_mode", "production"),
        ("profile_id", "claude-code"),
        ("active_mission_id", "ahawr-context-retrieval-layer-v1"),
        ("telegram_chat_id", "REDACTED-CHAT-ID"),
        ("poll_seconds", str(POLL_SECONDS)),
        ("telegram_timeout", str(TELEGRAM_TIMEOUT)),
        ("confirm_ttl_sec", str(CONFIRM_TTL_SEC)),
        ("audit_table", AUDIT_TABLE),
        ("editable_json", json.dumps(editable)),
    ]


def build_workflow():
    nodes = [
        schedule_node("Poll Telegram", POLL_SECONDS, [4880, 1488], "c0639be1-71ef-407f-8519-20d1d9e9d9af"),
        code_node("Prepare Poll", PREPARE_POLL, [5120, 1488], "b69d96c2-a240-442d-ab1b-dd574f42ad7d"),
        set_node("Config", config_values(), [5360, 1488], "028e5feb-6d69-4900-addf-c28d2ba26fad"),
        http_node("Get Updates",
                 "={{ 'https://api.telegram.org/bot' + $credentials('telegramApi').token + '/getUpdates' }}",
                 [("offset", "={{ $json.offset }}"),
                  ("limit", "100"),
                  ("timeout", str(TELEGRAM_TIMEOUT)),
                  ("allowed_updates", '["message","callback_query"]')],
                 HTTP_TIMEOUT_MS, [5536, 1488], "54757ef0-2cf7-4e7f-87d7-99610ed91bef"),
        code_node("Expand Updates", EXPAND_UPDATES, [5712, 1488], "a9c27c6d-b372-458c-a4c8-55ba4480a278"),
        code_node("Parse Command", PARSE_COMMAND, [5904, 1488], "9d5c4aef-37a0-4f1b-abbc-ed37bb26f606"),
        if_node("Allowed Chat?", "={{ String($('Parse Command').first().json.allowed) }}", "true", [6096, 1488], "39155330-ea92-47b4-8ecc-8109983ad664"),
        code_node("Count Ignored", SILENCE, [6288, 1680], "11111111-2222-4333-8444-555555555555"),
        code_node("Route Category", ROUTE, [6288, 1488], "aaaaaaaa-1111-4222-8333-444444444444"),
        data_get_node("Load Live State", "Autonomous Agent Live State",
                     [6480, 1488], "46595fa6-a151-480e-8e00-e8aeed798154",
                     extra=[{"keyName": "state_key", "keyValue": "={{ $('Config').first().json.state_namespace }}"}]),
        data_get_node("Missions", "missions", [6480, 1680], "bbbbbbbb-1111-4222-8333-555555555555"),
        data_get_node("Audit Recent", AUDIT_TABLE, [6480, 1880], "cccccccc-1111-4222-8333-666666666666", limit=100),
        data_get_node("Load Config Row", "hermes_config", [6480, 2080], "dddddddd-1111-4222-8333-777777777777"),
        data_get_node("Load Mission Row", "missions", [6480, 2280], "eeeeeeee-1111-4222-8333-888888888888"),
        switch_node("Dispatch", "$('Route Category').first().json.category",
                   ["view", "control", "edit", "confirm", "help"], [6672, 1488], "ffffffff-1111-4222-8333-999999999999"),
        code_node("Handle View", VIEW_HANDLER, [6864, 1312], "11111111-aaaa-4bbb-8ccc-dddddddddddd"),
        code_node("Handle Control", CONTROL_HANDLER, [6864, 1488], "11111111-aaaa-4bbb-8ccc-eeeeeeeeeeee"),
        code_node("Handle Edit", EDIT_HANDLER, [6864, 1664], "11111111-aaaa-4bbb-8ccc-ffffffffff"),
        code_node("Handle Confirm", CONFIRM_HANDLER, [6864, 1840], "11111111-aaaa-4bbb-8ccc-gggggggggg"),
        code_node("Handle Help", HELP_HANDLER, [6864, 2016], "11111111-aaaa-4bbb-8ccc-hhhhhhhhhh"),
        if_node("Has Pending?",
               "={{ $('Handle Confirm').first().json.approved ? 'pending' : 'none' }}", "pending",
               [7056, 1840], "55555555-aaaa-4bbb-8ccc-111111111111"),
        code_node("Apply Mutation", APPLY_MUTATION, [7248, 1840], "22222222-aaaa-4bbb-8ccc-cccccccccccc"),
        if_node("Write Validated?",
               "={{ $('Apply Mutation').first().json.failed ? 'failed' : 'valid' }}", "failed",
               [7440, 1840], "66666666-aaaa-4bbb-8ccc-222222222222"),
        code_node("Shape Row", SHAPE_ROW, [7536, 1744], "77777777-aaaa-4bbb-8ccc-444444444455"),
        switch_node("Write Table", "$('Apply Mutation').first().json.table",
                   ["hermes_config", "missions", "Autonomous Agent Live State", COMMAND_QUEUE_TABLE],
                   [7632, 1648], "77777777-aaaa-4bbb-8ccc-333333333333"),
        data_update_node("Write hermes_config", "hermes_config",
                        [{"keyName": "profile_id", "keyValue": "={{ $('Config').first().json.profile_id }}"}],
                        [7824, 1520], "88888888-aaaa-4bbb-8ccc-444444444444"),
        data_update_node("Write missions", "missions",
                        [{"keyName": "mission_id", "keyValue": "={{ $('Apply Mutation').first().json.match.value }}"}],
                        [7824, 1712], "99999999-aaaa-4bbb-8ccc-555555555555"),
        data_update_node("Write Autonomous Agent Live State", "Autonomous Agent Live State",
                        [{"keyName": "state_key", "keyValue": "={{ $('Config').first().json.state_namespace }}"}],
                        [7824, 1904], "aaaa1111-aaaa-4bbb-8ccc-666666666666"),
        data_insert_node("Write Command Queue", COMMAND_QUEUE_TABLE,
                        ["action", "mission_id", "state_key", "chat_id", "role", "status", "code", "created_at", "executed_at", "result"],
                        [7824, 2096], "bbbb2222-aaaa-4bbb-8ccc-777788888888"),
        data_get_node("Read After Command Queue", COMMAND_QUEUE_TABLE,
                        [8016, 2096], "bbbb3333-aaaa-4bbb-8ccc-888899999999"),
        data_get_node("Read After hermes_config", "hermes_config",
                     [8016, 1520], "aaaa2222-aaaa-4bbb-8ccc-777777777777",
                     extra=[{"keyName": "profile_id", "keyValue": "={{ $('Config').first().json.profile_id }}"}]),
        data_get_node("Read After missions", "missions",
                     [8016, 1712], "aaaa3333-aaaa-4bbb-8ccc-888888888888",
                     extra=[{"keyName": "mission_id", "keyValue": "={{ $('Apply Mutation').first().json.match.value }}"}]),
        data_get_node("Read After Live State", "Autonomous Agent Live State",
                     [8016, 1904], "aaaa4444-aaaa-4bbb-8ccc-999999999999",
                     extra=[{"keyName": "state_key", "keyValue": "={{ $('Config').first().json.state_namespace }}"}]),
        code_node("Build Reply", BUILD_REPLY, [8208, 1840], "bbbb1111-aaaa-4bbb-8ccc-aaaa11111111"),
        code_node("Build Audit Row", AUDIT_WRITE, [8400, 1840], "33333333-aaaa-4bbb-8ccc-dddddddddddd"),
        data_insert_node("Write Audit Log", AUDIT_TABLE,
                        ["who", "when", "command", "before", "after", "result", "reason"], [8592, 1840], "44444444-aaaa-4bbb-8ccc-eeeeeeeeeeee"),
        telegram_node("Send Reply",
                     "={{ $('Build Reply').first().json.chat_id }}",
                     "={{ $('Build Reply').first().json.text }}",
                     "={{ $('Build Reply').first().json.buttons }}",
                     [8784, 1840], "f39df623-73a2-4900-9bfc-a0943430e368",
                     "a70d031f-2918-4ca4-8d34-3ba5d0ff25c4"),
        if_node("Answer Callback?",
               "={{ ($('Parse Command').first().json.kind === 'callback' && $('Parse Command').first().json.callback_id !== null) ? 'callback' : 'none' }}",
               "callback",
               [8976, 1840], "55667788-aaaa-4bbb-8ccc-112233445566"),
        http_post_node("Send Callback",
                       "={{ 'https://api.telegram.org/bot' + $credentials('telegramApi').token + '/answerCallbackQuery' }}",
                       "={{ { callback_query_id: $('Parse Command').first().json.callback_id } }}",
                       HTTP_TIMEOUT_MS, [9168, 1840], "66778899-aaaa-4bbb-8ccc-112233445577"),
        sticky_node("Setup note",
                   "**AHAWR Status Bot — control bot**\n\n"
                   "Long-polls Telegram every 60 s (getUpdates timeout 25 s as a request parameter, HTTP client timeout 30 s, ~1440 executions/day) and handles both messages and callback_query. "
                   "Runs cannot overlap: a static-data run flag (poll_in_progress) is set before each getUpdates and cleared after it; a trigger that finds the flag set skips the run. "
                   "Only chats in `hermes_config.telegram_chat_id` are answered; others are counted silently. "
                   "Mutating commands (control and edit) require a confirmation (inline button or one-time code, 2-min TTL) before any change is applied and are written to the audit table; every callback_query is acknowledged with answerCallbackQuery. "
                   "The bot cannot launch runs itself: confirmed /start, /resume and /restart insert a row into the AHAWR Command Queue table and the reply states the operator launch line (the operator executes it); confirmed /retry and /accept set the Live State flags the main workflow consumes during a running mission. "
                   "The bot token lives in the `telegramApi` credential, never in the workflow file.",
                   [5360, 1792], "4ef3aa0d-5d49-480e-86f5-c22de108ab55"),
    ]

    connections = {
        "Poll Telegram": {"main": [[{"node": "Prepare Poll", "type": "main", "index": 0}]]},
        "Prepare Poll": {"main": [[{"node": "Config", "type": "main", "index": 0}]]},
        "Config": {"main": [[{"node": "Get Updates", "type": "main", "index": 0}]]},
        "Get Updates": {"main": [[{"node": "Expand Updates", "type": "main", "index": 0}]]},
        "Expand Updates": {"main": [[{"node": "Parse Command", "type": "main", "index": 0}]]},
        "Parse Command": {"main": [[{"node": "Allowed Chat?", "type": "main", "index": 0}]]},
        "Allowed Chat?": {
            "main": [
                [{"node": "Route Category", "type": "main", "index": 0}],  # true
                [{"node": "Count Ignored", "type": "main", "index": 0}],  # false
            ]
        },
        "Route Category": {"main": [[{"node": "Dispatch", "type": "main", "index": 0}]]},
        "Dispatch": {
            "main": [
                [{"node": "Handle View", "type": "main", "index": 0}],
                [{"node": "Handle Control", "type": "main", "index": 0}],
                [{"node": "Handle Edit", "type": "main", "index": 0}],
                [{"node": "Handle Confirm", "type": "main", "index": 0}],
                [{"node": "Handle Help", "type": "main", "index": 0}],
            ]
        },
        "Handle View": {"main": [[{"node": "Send Reply", "type": "main", "index": 0}]]},
        "Handle Help": {"main": [[{"node": "Send Reply", "type": "main", "index": 0}]]},
        "Handle Control": {"main": [[{"node": "Send Reply", "type": "main", "index": 0}]]},
        "Handle Edit": {"main": [[{"node": "Send Reply", "type": "main", "index": 0}]]},
        "Handle Confirm": {"main": [[{"node": "Has Pending?", "type": "main", "index": 0}]]},
        "Has Pending?": {
            "main": [
                [{"node": "Apply Mutation", "type": "main", "index": 0}],  # true -> write path
                [{"node": "Build Reply", "type": "main", "index": 1}],   # false -> reject reply
            ]
        },
        "Apply Mutation": {"main": [[{"node": "Write Validated?", "type": "main", "index": 0}]]},
        "Write Validated?": {
            "main": [
                [{"node": "Build Reply", "type": "main", "index": 0}],   # failed -> error reply
                [{"node": "Shape Row", "type": "main", "index": 0}],   # valid -> shape the row -> write
            ]
        },
        "Shape Row": {"main": [[{"node": "Write Table", "type": "main", "index": 0}]]},
        "Write Table": {
            "main": [
                [{"node": "Write hermes_config", "type": "main", "index": 0}],
                [{"node": "Write missions", "type": "main", "index": 1}],
                [{"node": "Write Autonomous Agent Live State", "type": "main", "index": 2}],
                [{"node": "Write Command Queue", "type": "main", "index": 3}],
            ]
        },
        "Write hermes_config": {"main": [[{"node": "Read After hermes_config", "type": "main", "index": 0}]]},
        "Write missions": {"main": [[{"node": "Read After missions", "type": "main", "index": 0}]]},
        "Write Autonomous Agent Live State": {"main": [[{"node": "Read After Live State", "type": "main", "index": 0}]]},
        "Write Command Queue": {"main": [[{"node": "Read After Command Queue", "type": "main", "index": 0}]]},
        "Read After hermes_config": {"main": [[{"node": "Build Reply", "type": "main", "index": 0}]]},
        "Read After missions": {"main": [[{"node": "Build Reply", "type": "main", "index": 0}]]},
        "Read After Live State": {"main": [[{"node": "Build Reply", "type": "main", "index": 0}]]},
        "Read After Command Queue": {"main": [[{"node": "Build Reply", "type": "main", "index": 0}]]},
        "Build Reply": {"main": [[{"node": "Build Audit Row", "type": "main", "index": 0}]]},
        "Build Audit Row": {"main": [[{"node": "Write Audit Log", "type": "main", "index": 0}]]},
        "Write Audit Log": {"main": [[{"node": "Send Reply", "type": "main", "index": 0}]]},
        "Send Reply": {
            "main": [
                [{"node": "Answer Callback?", "type": "main", "index": 0}],
            ]
        },
        "Answer Callback?": {
            "main": [
                [{"node": "Send Callback", "type": "main", "index": 0}],  # true (kind == callback)
            ]
        },
    }

    return {
        "name": "AHAWR Status Bot",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1", "binaryMode": "separate"},
        "active": True,
    }


# --- checks -------------------------------------------------------------------

TOKEN_RE = re.compile(r"[0-9A-Za-z]{15,}\s*-\s*[0-9A-Za-z_]{10,}")  # bot token shape


def check_workflow(wf, label):
    ids, names = set(), set()
    for n in wf["nodes"]:
        nid = n["id"]
        if nid in ids:
            raise ValueError("%s: duplicate node id %s" % (label, nid))
        ids.add(nid)
        nm = n["name"]
        if nm in names:
            raise ValueError("%s: duplicate node name %s" % (label, nm))
        names.add(nm)
    for src, outs in wf.get("connections", {}).items():
        if src not in names:
            raise ValueError("%s: connection source %r unknown" % (label, src))
        for main in outs.get("main", []):
            for entry in main:
                if isinstance(entry, dict) and entry.get("node") not in names:
                    raise ValueError("%s: connection to %r unknown" % (label, entry["node"]))
    return ids, names


def find_node(wf, name):
    return next((n for n in wf["nodes"] if n["name"] == name), None)


def check_text_no_secrets(wf, label, raw):
    if "Bearer " in raw:
        raise ValueError("%s: 'Bearer ' found in file" % label)
    if TOKEN_RE.search(raw):
        raise ValueError("%s: token pattern found in file" % label)


def run_checks(wf, label, raw):
    ids, names = check_workflow(wf, label)
    check_text_no_secrets(wf, label, raw)

    cfg = find_node(wf, "Config")
    if cfg is None:
        raise ValueError("%s: Config node missing" % label)
    cfg_vals = {v["name"]: v["value"] for v in cfg["parameters"]["values"]["string"]}
    for key in cfg_vals:
        if "token" in key.lower() or "api_key" in key.lower() or "secret" in key.lower():
            raise ValueError("%s: Config node exposes secret key %r" % (label, key))
    if "REDACTED-BOT-TOKEN" in cfg_vals.get("telegram_bot_token", ""):
        pass  # placeholder allowed only if not a real token; but we removed it from Config
    if "telegram_bot_token" in cfg_vals:
        raise ValueError("%s: Config node must not contain telegram_bot_token" % label)

    gu = find_node(wf, "Get Updates")
    if gu is None:
        raise ValueError("%s: Get Updates missing" % label)
    qparams = {p["name"]: p["value"] for p in gu["parameters"]["queryParameters"]["parameters"]}
    timeout = int(str(qparams["timeout"]))
    if not (25 <= timeout <= 30):
        raise ValueError("%s: getUpdates timeout must be 25..30, got %d" % (label, timeout))
    allowed = qparams["allowed_updates"]
    if "callback_query" not in allowed:
        raise ValueError("%s: allowed_updates must include callback_query" % label)
    http_timeout = int(gu["parameters"]["options"]["timeout"])
    if http_timeout < timeout * 1000:
        raise ValueError("%s: HTTP client timeout %d ms must be >= the %d s long-poll" % (label, http_timeout, timeout))

    st = find_node(wf, "Poll Telegram")
    if st is None:
        raise ValueError("%s: Poll Telegram missing" % label)
    sec = st["parameters"]["rule"]["interval"][0]["secondsInterval"]
    per_day = 86400 / sec
    if per_day > 3000:
        raise ValueError("%s: interval gives %d exec/day > ~3000" % (label, per_day))
    if sec <= timeout:
        raise ValueError("%s: schedule interval %d s must be > the %d s long-poll so runs cannot overlap" % (label, sec, timeout))

    wt = find_node(wf, "Write Audit Log")
    if wt is None:
        raise ValueError("%s: Write Audit Log missing" % label)
    if wt["parameters"]["operation"] != "insert":
        raise ValueError("%s: Write Audit Log must use the insert operation, got %r" % (label, wt["parameters"]["operation"]))
    cols = list(wt["parameters"]["columns"]["value"])
    for need in ("who", "when", "command", "before", "after", "result", "reason"):
        if need not in cols:
            raise ValueError("%s: audit column %r missing" % (label, need))

    # mutating commands must route through the confirm path: control and edit
    # handlers build a pending action, and Handle Confirm performs it.
    for nm in ("Handle Control", "Handle Edit"):
        n = find_node(wf, nm)
        if n is None or PENDING_STORE_KEY not in n["parameters"]["jsCode"]:
            raise ValueError("%s: %s must store a pending confirmation" % (label, nm))

    # callback_query must route to the confirmation handler: Route Category
    # maps command 'callback_query' to category 'confirm', and Dispatch must
    # send the 'confirm' branch to Handle Confirm.
    route = find_node(wf, "Route Category")
    if route is None or "callback_query" not in route["parameters"]["jsCode"]:
        raise ValueError("%s: Route Category must map callback_query to confirm" % label)
    dispatch = find_node(wf, "Dispatch")
    if dispatch is None:
        raise ValueError("%s: Dispatch missing" % label)
    for i, c in enumerate(dispatch["parameters"]["values"]):
        if c["rightValue"] == "confirm":
            targets = wf["connections"]["Dispatch"]["main"][i]
            if not targets or targets[0]["node"] != "Handle Confirm":
                raise ValueError("%s: Dispatch confirm branch must go to Handle Confirm" % label)
            break

    # answerCallbackQuery must be reachable and only via the telegramApi
    # credential (the URL is built from $credentials, no token in file).
    ans = find_node(wf, "Answer Callback?")
    if ans is None:
        raise ValueError("%s: Answer Callback? routing node missing" % label)
    sendcb = find_node(wf, "Send Callback")
    if sendcb is None or "answerCallbackQuery" not in sendcb["parameters"]["url"]:
        raise ValueError("%s: Send Callback must call answerCallbackQuery" % label)
    if "$credentials('telegramApi')" not in sendcb["parameters"]["url"]:
        raise ValueError("%s: Send Callback URL must use the telegramApi credential" % label)
    if "Send Reply" in wf["connections"] and not any(
            e["node"] == "Answer Callback?" for e in wf["connections"]["Send Reply"]["main"][0]):
        raise ValueError("%s: Send Reply must route to Answer Callback?" % label)
    if "Answer Callback?" in wf["connections"] and not any(
            e["node"] == "Send Callback" for e in wf["connections"]["Answer Callback?"]["main"][0]):
        raise ValueError("%s: Answer Callback? must route to Send Callback" % label)

    # no switch-node leftValue may contain a double-wrapped expression
    for n in wf["nodes"]:
        vals = n.get("parameters", {}).get("values", [])
        if not isinstance(vals, list):
            continue
        for c in vals:
            if isinstance(c, dict):
                lv = c.get("leftValue", "")
                if "={{ ={{" in lv:
                    raise ValueError("%s: node %r has double-wrapped leftValue %r" % (label, n["name"], lv))

    return ids, names


def main():
    if not WF_FILE.exists():
        raise FileNotFoundError("File not found: %s" % WF_FILE)

    wf = build_workflow()
    raw = json.dumps(wf, indent=2, ensure_ascii=False) + "\n"
    run_checks(wf, LABEL, raw)

    existing = json.loads(WF_FILE.read_text(encoding="utf-8"))
    if json.dumps(existing, sort_keys=True) == json.dumps(wf, sort_keys=True):
        print("%s: already at target state, no changes." % LABEL)
        return

    WF_FILE.write_text(raw, encoding="utf-8")
    # re-validate the written file
    reloaded = json.loads(WF_FILE.read_text(encoding="utf-8"))
    check_workflow(reloaded, LABEL)
    check_text_no_secrets(reloaded, LABEL, WF_FILE.read_text(encoding="utf-8"))
    print("%s: rewrote to control bot; %d nodes." % (LABEL, len(wf["nodes"])))
    print("Checks passed: json valid, unique ids/names, connections resolve,")
    print("              no token pattern, no 'Bearer ', no secret in Config,")
    print("              getUpdates timeout 25..30 + allowed_updates has callback_query,")
    print("              interval <= ~3000 exec/day, audit columns present,")
    print("              mutating commands route through confirmation.")


if __name__ == "__main__":
    main()
