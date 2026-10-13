#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const WF_PATH = path.join(__dirname, "..", "..", "AHAWR_Status_Bot.json");
const wf = JSON.parse(fs.readFileSync(WF_PATH, "utf-8"));

const nodesByName = {};
for (const n of wf.nodes) nodesByName[n.name] = n;

// --- n8n stub harness ------------------------------------------------------
// runNode(nodeName, data, jsonItem, staticData) -> result array.
// data: object mapping node name -> array of {json}.
// jsonItem: the $json item for this node's input.
// staticData: mutable object passed to $getWorkflowStaticData.

function runNode(nodeName, data, jsonItem, staticData) {
  const code = nodesByName[nodeName].parameters.jsCode;
  const dollar = name => {
    const arr = data[name] || [];
    return {
      first: () => (arr.length ? arr[0] : undefined),
      all: () => arr,
      last: () => (arr.length ? arr[arr.length - 1] : undefined),
    };
  };
  const fn = new Function("$", "$json", "$getWorkflowStaticData", "dataRef", "\n" + code);
  return fn(dollar, jsonItem, () => staticData, data);
}

// --- fixtures ---------------------------------------------------------------

const editable = {
  worker_model: { type: "string", min: 1, max: 120, table: "hermes_config" },
  worker_provider: { type: "string", min: 1, max: 60, table: "hermes_config" },
  reviewer_model: { type: "string", min: 1, max: 120, table: "hermes_config" },
  reviewer_provider: { type: "string", min: 1, max: 60, table: "hermes_config" },
  max_tasks: { type: "number", min: 1, max: 500, table: "hermes_config" },
  max_attempts_per_task: { type: "number", min: 1, max: 10, table: "hermes_config" },
  architect_poll_seconds: { type: "number", min: 1, max: 3600, table: "hermes_config" },
  worker_poll_seconds: { type: "number", min: 1, max: 3600, table: "hermes_config" },
  reviewer_poll_seconds: { type: "number", min: 1, max: 3600, table: "hermes_config" },
  name: { type: "string", min: 1, max: 120, table: "hermes_config" },
  objective: { type: "string", min: 1, max: 4000, table: "hermes_config" },
  working_folder: { type: "string", min: 1, max: 300, table: "hermes_config" },
  enabled: { type: "boolean", table: "hermes_config" },
};

const CFG = {
  state_namespace: "llama.cpp-dashboard-v1",
  execution_mode: "production",
  profile_id: "claude-code",
  active_mission_id: "ahawr-context-retrieval-layer-v1",
  telegram_chat_id: "12345 67890", // two allowed chats
  poll_seconds: "30",
  telegram_timeout: "30",
  confirm_ttl_sec: "120",
  audit_table: "AHAWR Bot Audit Log",
  editable_json: editable,
};

const LIVE = {
  workflow_status: "running",
  total_tasks: 10,
  task_index: 3,
  current_task_id: "task-003",
  task_attempt: 2,
  last_review_score: 0.7,
  worker_status: "idle",
  reviewer_status: "idle",
  current_run_id: "run-abc",
  updated_at: "2026-10-09T00:00:00Z",
  queue: ["mission-a", "mission-b"],
  operator_feedback: "previous note",
};

const MISSION_ROW = { mission_id: "ahawr-context-retrieval-layer-v1", name: "Retrieval Layer", enabled: true, version: 3 };
const CONFIG_ROW = { profile_id: "claude-code", worker_model: "claude-sonnet-5", worker_provider: "anthropic", reviewer_model: "claude-haiku-4-5", reviewer_provider: "anthropic" };
const AUDIT_ROWS = [
  { json: { when: "2026-10-09T00:00:00Z", who: "12345", command: "set max_tasks = 100", result: "approved" } },
  { json: { when: "2026-10-09T00:01:00Z", who: "12345", command: "stop", result: "approved" } },
];

function dataOf(...args) {
  const d = {};
  for (let i = 0; i < args.length; i += 2) {
    const items = args[i + 1];
    d[args[i]] = items.map(it => ({ json: JSON.parse(JSON.stringify(it.json)) }));
  }
  return d;
}

// --- assertion helpers -------------------------------------------------------

let passed = 0, failed = 0;
function assert(label, cond) {
  if (cond) { passed++; console.log("PASS  " + label); }
  else { failed++; console.log("FAIL  " + label); }
}

// =============================================================================
// 1. Parse Command — command parsing, allow-list, callback_query
// =============================================================================
console.log("\n[Parse Command]");

// message from allowed chat
let sd = {};
let r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { message: { chat: { id: 12345 }, text: "/status" } } }, sd);
assert("allowed chat 12345 -> allowed=true", r[0].json.allowed === true);
assert("command parsed from first token", r[0].json.command === "/status");
assert("text captured", r[0].json.text === "/status");

// message from non-allowed chat
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { message: { chat: { id: 99999 }, text: "/stop" } } }, sd);
assert("non-allowed chat 99999 -> allowed=false", r[0].json.allowed === false);
assert("non-allowed chat counted", sd.bot_ignored_count === 1);

// callback_query from allowed chat
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { callback_query: { message: { chat: { id: 67890 } }, data: "confirm" } } }, sd);
assert("callback_query from allowed chat -> allowed=true", r[0].json.allowed === true);
assert("callback_query command set", r[0].json.command === "callback_query");
assert("callback_data captured", r[0].json.callback_data === "confirm");

// callback with no data and kind 'callback' -> resolved='callback_query'
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { kind: "callback", update: { callback_query: { message: { chat: { id: 12345 } }, data: null } } }, sd);
assert("callback with null data -> resolved=callback_query", r[0].json.resolved === "callback_query");

// two allowed chats both accepted
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { message: { chat: { id: 67890 }, text: "/whoami" } } }, sd);
assert("second allowed chat 67890 -> allowed=true", r[0].json.allowed === true);

// =============================================================================
// 2. Route Category — command -> category mapping, aliases, mutating flag
// =============================================================================
console.log("\n[Route Category]");

function route(cmdObj) {
  const sd = {};
  return runNode("Route Category",
    dataOf("Config", [{ json: CFG }], "Parse Command", [cmdObj]),
    { json: cmdObj }, sd)[0].json;
}

assert("/status -> view", route({ json: { resolved: "/status" } }).category === "view");
assert("/stop -> control", route({ json: { resolved: "/stop" } }).category === "control");
assert("/set -> edit", route({ json: { resolved: "/set" } }).category === "edit");
assert("/approve -> confirm", route({ json: { resolved: "/approve" } }).category === "confirm");
assert("/help -> help", route({ json: { resolved: "/help" } }).category === "help");
assert("callback_query -> confirm", route({ json: { resolved: "callback_query" } }).category === "confirm");
assert("alias /tasks -> /agents -> view", route({ json: { resolved: "/tasks" } }).category === "view");
assert("alias /v -> /version -> view", route({ json: { resolved: "/v" } }).category === "view");
assert("alias /q -> /queue -> control", route({ json: { resolved: "/q" } }).category === "control");
assert("mutating=true for control", route({ json: { resolved: "/stop" } }).mutating === true);
assert("mutating=true for edit", route({ json: { resolved: "/set" } }).mutating === true);
assert("mutating=false for view", route({ json: { resolved: "/status" } }).mutating === false);
assert("unknown command -> unknown", route({ json: { resolved: "/bogus" } }).category === "unknown");

// /start without arguments -> greeting (Handle Help branch)
r = runNode("Handle Help",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/start", command: "/start", allowed: true } }]),
  {}, {})[0].json;
assert("/start (no args) -> greeting", r.text === "AHAWR Status Bot online. Use /help for commands.");

// /help -> grouped command list (Handle Help branch)
r = runNode("Handle Help",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/help", command: "/help", allowed: true } }]),
  {}, {})[0].json;
assert("/help -> grouped list (Session, Info, Configuration)",
  r.text.includes("Session: /accept, /agents, /approve, /deny, /pause, /queue, /restart, /resume, /retry, /start, /status, /steer, /stop, /undo") &&
  r.text.includes("Info: /commands, /errors, /help, /insights, /profile, /usage, /version, /whoami") &&
  r.text.includes("Configuration: /config, /model, /set"));

// /help with a filter -> only matching commands
r = runNode("Handle Help",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/help info", command: "/help", args: ["info"], allowed: true } }]),
  {}, {})[0].json;
assert("/help info -> only the Info group",
  r.text === "Info: /commands, /errors, /help, /insights, /profile, /usage, /version, /whoami");

// /commands -> paginated list, 20 per page (Handle Help branch)
r = runNode("Handle Help",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/commands", command: "/commands", allowed: true } }]),
  {}, {})[0].json;
assert("/commands page 1 -> 20 sorted commands",
  r.text === "Page 1: /accept, /agents, /approve, /audit, /commands, /config, /deny, /errors, /help, /insights, /mission, /missions, /model, /pause, /profile, /progress, /queue, /restart, /resume, /retry");

// =============================================================================
// 3. Handle View — status formatting, section splitting
// =============================================================================
console.log("\n[Handle View]");

// /status formatting
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/status", command: "/status" } }]),
  {}, {})[0].json;
assert("/status shows workflow_status", r.text.includes("Status: running"));
assert("/status shows progress 3/10", r.text.includes("3/10"));
assert("/status shows attempt and score", r.text.includes("Attempt: 2  score: 0.7"));
assert("/status shows worker/reviewer", r.text.includes("Worker: idle  Reviewer: idle"));
assert("/status shows run id", r.text.includes("Run: run-abc"));

// /whoami shows admin for allowed chat
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/whoami", command: "/whoami", allowed: true } }]),
  {}, {})[0].json;
assert("/whoami shows admin level", r.text.includes("admin (control and edit)"));

// /agents -> worker/reviewer + run id (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/agents", command: "/agents", allowed: true } }]),
  {}, {})[0].json;
assert("/agents -> 'Agents: idle / idle on run run-abc'", r.text === "Agents: idle / idle on run run-abc");

// /profile -> profile id + allowed chat (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/profile", command: "/profile", allowed: true } }]),
  {}, {})[0].json;
assert("/profile -> 'Profile: claude-code (allowed chat: 12345 67890)'",
  r.text === "Profile: claude-code (allowed chat: 12345 67890)");

// /version -> static reply (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/version", command: "/version", allowed: true } }]),
  {}, {})[0].json;
assert("/version -> 'Workflow: AHAWR Status Bot v1; runner: Claude Code; n8n: this stack'",
  r.text === "Workflow: AHAWR Status Bot v1; runner: Claude Code; n8n: this stack");

// /usage -> static reply (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/usage", command: "/usage", allowed: true } }]),
  {}, {})[0].json;
assert("/usage -> static reply",
  r.text === "Usage: see runner events for tokens, tokens/s, cache share, time per task.");

// /insights -> static reply (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/insights", command: "/insights", allowed: true } }]),
  {}, {})[0].json;
assert("/insights -> static reply",
  r.text === "Insights: scores and retries per task over the last N days (see attempts table).");

// /errors -> static reply (Handle View branch)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/errors", command: "/errors", allowed: true } }]),
  {}, {})[0].json;
assert("/errors -> static reply",
  r.text === "Errors: last failed executions with node name and message (secrets masked).");

// /missions lists mission rows
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/missions", command: "/missions" } }], "Missions", [{ json: MISSION_ROW }]),
  {}, {})[0].json;
assert("/missions lists mission id and name", r.text.includes("ahawr-context-retrieval-layer-v1") && r.text.includes("Retrieval Layer"));

// /audit shows last N entries
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/audit 2", command: "/audit", args: ["2"] } }], "Audit Recent", AUDIT_ROWS),
  {}, {})[0].json;
assert("/audit 2 shows two entries", r.text.includes("Audit (last 2):") && r.text.includes("set max_tasks = 100"));

// /config lists editable keys (no secrets)
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/config", command: "/config" } }]),
  {}, {})[0].json;
assert("/config lists editable keys", r.text.includes("worker_model") && r.text.includes("max_tasks"));
assert("/config does not expose secrets", !r.text.includes("api_key") && !r.text.includes("bot_token"));

// /status with special chars -> HTML escaped
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, workflow_status: "a<b>c" } }], "Parse Command", [{ json: { chat_id: 12345, text: "/status", command: "/status" } }]),
  {}, {})[0].json;
assert("/status escapes HTML in status", r.text.includes("a&lt;b&gt;c"));

// Section splitting: a long field value (no embedded newlines) must stay as one
// intact line — the handler joins sections with \n but never splits a field mid-string.
// /status emits exactly 6 sections: Status, Progress, Attempt, Worker/Reviewer, Run, Updated.
const LONG_VAL = "A".repeat(1500);
r = runNode("Handle View",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, workflow_status: LONG_VAL } }], "Parse Command", [{ json: { chat_id: 12345, text: "/status", command: "/status" } }]),
  {}, {})[0].json;
assert("/status long field -> output has exactly 6 lines", r.text.split("\n").length === 6);
assert("/status long field -> value is not split mid-string", r.text.split("\n").some(line => line.includes(LONG_VAL)));
assert("/status long field -> no empty line (no double newline)", !r.text.includes("\n\n"));

// =============================================================================
// 4. Handle Control — pending, TTL, one-time code, buttons
// =============================================================================
console.log("\n[Handle Control]");

// /stop -> per-chat pending stored, TTL = now+120s, code issued, buttons carry the code
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/stop", command: "/stop", allowed: true } }]),
  {}, sd)[0].json;
assert("/stop pending stored per chat", sd.bot_pending && sd.bot_pending["12345"] !== null);
assert("/stop pending kind=control", sd.bot_pending["12345"].kind === "control");
assert("/stop pending role=admin", sd.bot_pending["12345"].role === "admin");
assert("/stop per-entry TTL is created_at+120s", Math.abs((sd.bot_pending["12345"].created_at + sd.bot_pending["12345"].ttl_ms) - (Date.now() + 120000)) < 1000);
assert("/stop code issued", r.code && r.code.startsWith("CONF-"));
assert("/stop stored code matches reply code", sd.bot_pending["12345"].code === r.code);
assert("/stop buttons present", Array.isArray(r.buttons) && r.buttons.length === 2);
assert("/stop approve button carries the code", r.buttons[0].callback_data === r.code);
assert("/stop deny button", r.buttons[1].callback_data === "deny");

// /pause with reason -> structured args
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/pause maintenance", command: "/pause", allowed: true } }]),
  {}, sd)[0].json;
assert("/pause stores reason in args", sd.bot_pending["12345"].args.reason === "maintenance");

// /pause off -> reason empty
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/pause off", command: "/pause", allowed: true } }]),
  {}, sd)[0].json;
assert("/pause off -> reason empty", sd.bot_pending["12345"].args.reason === "");

// /steer -> prompt captured
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/steer keep it simple", command: "/steer", allowed: true } }]),
  {}, sd)[0].json;
assert("/steer stores prompt", sd.bot_pending["12345"].args.prompt === "keep it simple");

// /queue add -> op+mission captured
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/queue add mission-c", command: "/queue", allowed: true } }]),
  {}, sd)[0].json;
assert("/queue add captures op and mission", sd.bot_pending["12345"].args.op === "add" && sd.bot_pending["12345"].args.mission === "mission-c");

// /start with mission arg -> mission captured
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 12345, text: "/start mission-x", command: "/start", allowed: true } }]),
  {}, sd)[0].json;
assert("/start captures mission arg", sd.bot_pending["12345"].args.mission === "mission-x");

// viewer role -> Handle Control refuses and stores nothing
sd = {};
r = runNode("Handle Control",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }], "Parse Command", [{ json: { chat_id: 77777, text: "/stop", command: "/stop", allowed: false } }]),
  {}, sd)[0].json;
assert("/stop from viewer -> rejected, no pending stored", r.text.includes("Rejected") && (!sd.bot_pending || !sd.bot_pending["77777"]) && r.audit && r.audit.result === "rejected" && r.audit.reason === "viewer role");

// =============================================================================
// 5. Handle Edit — whitelist validation, /model, /undo
// =============================================================================
console.log("\n[Handle Edit]");

// /set with a valid key and number in range
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set max_tasks 100", command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set max_tasks 100 -> pending stored per chat", sd.bot_pending && sd.bot_pending["12345"] !== null);
assert("/set pending key=max_tasks", sd.bot_pending["12345"].key === "max_tasks");
assert("/set pending kind=edit", sd.bot_pending["12345"].kind === "edit");

// /set with number out of range -> rejected, no pending
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set max_tasks 9999", command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set max_tasks 9999 -> rejected", r.text.includes("Rejected"));
assert("/set out of range -> no pending stored", !sd.bot_pending || !sd.bot_pending["12345"]);

// /set with non-number -> rejected
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set max_tasks abc", command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set non-number -> rejected", r.text.includes("Rejected"));

// /set with boolean true
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set enabled true", command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set enabled true -> pending stored", sd.bot_pending && sd.bot_pending["12345"] !== null && sd.bot_pending["12345"].value === "true");

// /set with unknown key -> "Not editable"
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set api_key xyz", command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set unknown key api_key -> Not editable", r.text.includes("Not editable"));
assert("/set unknown key -> no pending", !sd.bot_pending || !sd.bot_pending["12345"]);

// string length too long -> rejected
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/set name " + "x".repeat(121), command: "/set", allowed: true } }]),
  {}, sd)[0].json;
assert("/set name too long -> rejected", r.text.includes("Rejected"));

// /model with role and model -> pending with args
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/model worker claude-sonnet-5 --provider anthropic", command: "/model", allowed: true } }]),
  {}, sd)[0].json;
assert("/model pending stored", sd.bot_pending && sd.bot_pending["12345"] !== null);
assert("/model pending action starts with 'model '", sd.bot_pending["12345"].action.startsWith("model "));
assert("/model captures role and model in args", sd.bot_pending["12345"].args.role === "worker" && sd.bot_pending["12345"].args.model === "claude-sonnet-5");
assert("/model captures provider", sd.bot_pending["12345"].args.provider === "anthropic");

// /model missing model -> usage message, no pending
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/model worker", command: "/model", allowed: true } }]),
  {}, sd)[0].json;
assert("/model missing model -> usage, no pending", r.text.includes("Usage") && (!sd.bot_pending || !sd.bot_pending["12345"]));

// /undo N -> pending stores the key/value restored from this chat's latest
// approved audit row (args.n kept for the audit command text)
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/undo 3", command: "/undo", allowed: true } }],
        "Audit Recent", [
          { json: { who: "12345", when: "2026-10-09T00:00:00Z", command: "set max_tasks = 100", before: "10", after: "100", result: "approved" } },
          { json: { who: "67890", when: "2026-10-09T00:01:00Z", command: "set max_tasks = 50", before: "10", after: "50", result: "approved" } },
        ]),
  {}, sd)[0].json;
assert("/undo 3 -> pending stores the restored key/value",
  sd.bot_pending && sd.bot_pending["12345"] !== null &&
  sd.bot_pending["12345"].key === "max_tasks" && sd.bot_pending["12345"].value === "10" &&
  sd.bot_pending["12345"].table === "hermes_config" && sd.bot_pending["12345"].kind === "edit");
assert("/undo captures n=3", sd.bot_pending["12345"].args.n === 3);

// /undo with no successful editable audit row for the chat -> no pending
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/undo", command: "/undo", allowed: true } }],
        "Audit Recent", [
          { json: { who: "12345", when: "2026-10-09T00:00:00Z", command: "stop", result: "approved" } },
          { json: { who: "12345", when: "2026-10-09T00:01:00Z", command: "set max_tasks = 100", before: "10", after: "100", result: "denied" } },
        ]),
  {}, sd)[0].json;
assert("/undo with no successful editable audit row -> 'Nothing to undo', no pending",
  r.text.includes("Nothing to undo") && (!sd.bot_pending || !sd.bot_pending["12345"]));

// viewer role -> Handle Edit refuses and stores nothing
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 77777, text: "/set max_tasks 100", command: "/set", allowed: false } }]),
  {}, sd)[0].json;
assert("/set from viewer -> rejected, no pending stored", r.text.includes("Rejected") && (!sd.bot_pending || !sd.bot_pending["77777"]) && r.audit && r.audit.result === "rejected" && r.audit.reason === "viewer role");

// =============================================================================
// 6. Handle Confirm — TTL expiry, callback_query, /approve /deny
// =============================================================================
console.log("\n[Handle Confirm]");

// Fresh pending + /approve -> approved, pending consumed, audit row emitted
sd = { bot_pending: { "12345": { action: "stop", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/approve", command: "/approve", allowed: true } }]),
  {}, sd)[0].json;
assert("/approve -> result approved", r.text.includes("approved"));
assert("/approve -> pending consumed", sd.bot_pending === null);
assert("/approve -> audit row emitted", r.audit && r.audit.command === "stop" && r.audit.result === "approved");

// Expired pending (per-entry created_at + ttl_ms past) + /approve -> rejected
sd = { bot_pending: { "12345": { action: "stop", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now() - 130000, ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/approve", command: "/approve", allowed: true } }]),
  {}, sd)[0].json;
assert("expired pending -> rejected with 'code expired'", r.text.includes("code expired") && r.text.includes("No change applied"));
assert("expired pending -> pending cleared", sd.bot_pending === null);

// callback_query 'confirm' -> approve
sd = { bot_pending: { "12345": { action: "pause maintenance", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "", command: "callback_query", callback_data: "confirm", allowed: true } }]),
  {}, sd)[0].json;
assert("callback 'confirm' -> approved", r.text.includes("approved"));
assert("callback 'confirm' -> pending consumed", sd.bot_pending === null);

// callback_query 'deny' -> denied
sd = { bot_pending: { "12345": { action: "restart", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "", command: "callback_query", callback_data: "deny", allowed: true } }]),
  {}, sd)[0].json;
assert("callback 'deny' -> denied", r.text.includes("denied"));

// /deny -> denied
sd = { bot_pending: { "12345": { action: "stop", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/deny no reason", command: "/deny", allowed: true } }]),
  {}, sd)[0].json;
assert("/deny -> denied", r.text.includes("denied"));

// No pending + /approve (a decision was presented) -> rejected with audit item
sd = {};
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/approve", command: "/approve", allowed: true } }]),
  {}, sd)[0].json;
assert("no pending + /approve -> rejected with 'no pending command'", r.text.includes("Rejected") && r.text.includes("no pending command for this chat"));
assert("no pending + /approve -> audit item rejected", r.audit && r.audit.result === "rejected" && r.audit.reason === "no pending command for this chat");

// No pending + /approve with an unknown code -> rejected 'unknown one-time code'
sd = {};
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/approve CONF-99999-XXXXXX", command: "/approve", allowed: true } }]),
  {}, sd)[0].json;
assert("no pending + unknown code -> rejected 'unknown one-time code'", r.text.includes("Rejected") && r.text.includes("unknown one-time code"));
assert("no pending + unknown code -> audit item rejected", r.audit && r.audit.result === "rejected" && r.audit.reason === "unknown one-time code");

// No pending + plain message (no decision presented) -> "No pending change"
sd = {};
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "hello", command: "hello", allowed: true } }]),
  {}, sd)[0].json;
assert("no pending + no decision -> 'No pending change'", r.text.includes("No pending change") && !r.audit);

// Pending but no decision (plain message) -> "Pending: ..."
sd = { bot_pending: { "12345": { action: "stop", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000 } } };
r = runNode("Handle Confirm",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "hello", command: "hello", allowed: true } }]),
  {}, sd)[0].json;
assert("no decision -> 'Pending:' message", r.text.includes("Pending:"));

// =============================================================================
// 6b. Parse Command — callback id extraction; Answer Callback? routing
// =============================================================================
console.log("\n[Answer Callback routing]");

// Realistic callback_query update: the Telegram Bot API returns
// callback_query.id (not callback_query_id) as the unique identifier.
// Parse Command must read u.callback_query.id and surface it as callback_id.
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { callback_query: { id: "cb1", from: { id: 12345, is_bot: false }, message: { chat: { id: 12345 } }, data: "CONF-12345-ABCD12" } } }, sd);
assert("realistic callback_query -> callback_id === 'cb1'", r[0].json.callback_id === "cb1");
assert("realistic callback_query -> command === callback_query", r[0].json.command === "callback_query");
assert("realistic callback_query -> callback_data captured", r[0].json.callback_data === "CONF-12345-ABCD12");

// Plain message: no callback_query, so callback_id is null.
sd = {};
r = runNode("Parse Command",
  dataOf("Config", [{ json: CFG }]),
  { update: { message: { chat: { id: 12345 }, text: "/status" } } }, sd);
assert("plain message -> callback_id is null", r[0].json.callback_id === null);

// Answer Callback? condition expression (evaluated after `ev` is defined
// in the E2E Chain section below — see block 6b-2 at the bottom of the file).
// The expression is read from the workflow node here so the test stays
// coupled to the actual node parameters.
const acExpr = nodesByName["Answer Callback?"].parameters.conditions.conditions[0].leftValue;

// =============================================================================
// 7. Apply Mutation — validation before any write; output is
// {table, key, value, match, before, audit} (or {failed: true, reason}).
// =============================================================================
console.log("\n[Apply Mutation]");

// /stop -> live-state write payload: key/value for the live row
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "stop", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/stop -> live state key=workflow_status, value=stopped", r.key === "workflow_status" && r.value === "stopped");
assert("/stop -> routed to live state table by state_key", r.table === "Autonomous Agent Live State" && r.match.field === "state_key" && r.match.value === CFG.state_namespace);
assert("/stop -> before taken from re-read row", r.before === "running");

// /restart -> queued (bot cannot launch runs; operator executes the launch line)
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "restart", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/restart -> queued=true, table=AHAWR Command Queue", r.queued === true && r.table === "AHAWR Command Queue");

// /pause maintenance -> paused=true
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "pause maintenance", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/pause -> paused=true", r.key === "paused" && r.value === true);

// /pause off -> paused=false
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "pause off", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/pause off -> paused=false", r.value === false);

// /steer -> operator_feedback appended
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "steer keep it simple", chat_id: 12345, kind: "control", args: { prompt: "keep it simple" } } }, {})[0].json;
assert("/steer -> operator_feedback appended", r.key === "operator_feedback" && r.value === "previous note\nkeep it simple");

// /queue add -> queue updated in place
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "queue add mission-c", chat_id: 12345, kind: "control", args: { op: "add", mission: "mission-c" } } }, {})[0].json;
assert("/queue add -> queue includes mission-c", Array.isArray(r.value) && r.value.includes("mission-c") && r.value.length === 3);

// /queue rm 0 -> first removed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace, queue: ["a", "b", "c"] } }]),
  { pending: { action: "queue rm 0", chat_id: 12345, kind: "control", args: { op: "rm", index: 0 } } }, {})[0].json;
assert("/queue rm 0 -> 'a' removed", r.value.length === 2 && r.value[0] === "b");

// /queue clear -> empty
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "queue clear", chat_id: 12345, kind: "control", args: { op: "clear" } } }, {})[0].json;
assert("/queue clear -> empty queue", Array.isArray(r.value) && r.value.length === 0);

// /queue move 0 2 -> swap first and last
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace, queue: ["a", "b", "c"] } }]),
  { pending: { action: "queue move 0 2", chat_id: 12345, kind: "control", args: { op: "move", a: 0, b: 2 } } }, {})[0].json;
assert("/queue move 0 2 -> swapped", r.value[0] === "c" && r.value[2] === "a");

// /queue list -> existing queue returned
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: { action: "queue list", chat_id: 12345, kind: "control", args: { op: "list" } } }, {})[0].json;
assert("/queue list -> queue returned", Array.isArray(r.value) && r.value.length === 2);

// /model worker claude-sonnet-5 -> edit payload for hermes_config
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "model worker claude-sonnet-5", key: "worker_model", value: "claude-sonnet-5", table: "hermes_config", kind: "edit", args: { role: "worker", model: "claude-sonnet-5", provider: "anthropic" } } }, {})[0].json;
assert("/model -> hermes_config key=worker_model value=claude-sonnet-5", r.table === "hermes_config" && r.key === "worker_model" && r.value === "claude-sonnet-5");

// editable_json given as a JSON string: parsed once in validation, never indexed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: { ...CFG, editable_json: JSON.stringify(editable) } }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set worker_model = claude-opus-5", key: "worker_model", value: "claude-opus-5", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("editable_json as JSON string -> parsed, write payload valid", r.value === "claude-opus-5" && r.table === "hermes_config");

// editable_json as an invalid JSON string -> empty whitelist, fail
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: { ...CFG, editable_json: "not-json" } }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set worker_model = claude-opus-5", key: "worker_model", value: "claude-opus-5", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("invalid editable_json string -> failed, no write payload", r.failed === true);

// field not in the whitelist -> failed before any write
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set api_key = xyz", key: "api_key", value: "xyz", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("non-whitelisted field -> failed, reason names the field", r.failed === true && r.reason.includes("api_key"));
assert("non-whitelisted field -> no table/key emitted", r.table === undefined && r.key === undefined);

// number out of range -> failed before any write
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set max_tasks = 9999", key: "max_tasks", value: "9999", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("number out of range -> failed", r.failed === true && r.reason.includes("[1..500]"));

// non-number for a number field -> failed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set max_tasks = abc", key: "max_tasks", value: "abc", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("non-number for number field -> failed", r.failed === true);

// boolean field with a non-boolean value -> failed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set enabled = maybe", key: "enabled", value: "maybe", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("boolean field with 'maybe' -> failed", r.failed === true);

// string over max length -> failed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set name = " + "x".repeat(121), key: "name", value: "x".repeat(121), table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("string over max length -> failed", r.failed === true);

// live state row missing -> failed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", []),
  { pending: { action: "stop", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("live state row missing -> failed", r.failed === true);

// /mission <id> enable -> edit payload for the mission table
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: { ...MISSION_ROW, enabled: false } }]),
  { pending: { action: "mission " + MISSION_ROW.mission_id + " enable", key: "enabled", value: "true", table: "missions", kind: "edit", args: { mission_id: MISSION_ROW.mission_id, enabled: true } } }, {})[0].json;
assert("/mission enable -> missions table, key=enabled, value=true", r.table === "missions" && r.key === "enabled" && r.value === true);
assert("/mission enable -> matched by mission_id", r.match.field === "mission_id" && r.match.value === MISSION_ROW.mission_id);
assert("/mission enable -> before from mission row", r.before === false);

// /mission <id> disable -> edit payload
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: { ...MISSION_ROW, enabled: true } }]),
  { pending: { action: "mission mission-a disable", key: "enabled", value: "false", table: "missions", kind: "edit", args: { mission_id: "mission-a", enabled: false } } }, {})[0].json;
assert("/mission disable -> value=false", r.value === false);

// mission edit without a mission id -> failed
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: MISSION_ROW }]),
  { pending: { action: "set enabled = true", key: "enabled", value: "true", table: "missions", kind: "edit", args: {} } }, {})[0].json;
assert("mission edit without mission id -> routes to the active mission", r.table === "missions" && r.match.value === CFG.active_mission_id);

// /undo pending without edit_history -> failed (undo handling stays at T005)
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: MISSION_ROW }]),
  { pending: { action: "undo 3", key: "undo", value: "3", table: "missions", kind: "edit", args: { n: 3 } } }, {})[0].json;
assert("/undo without edit history -> failed, no write payload", r.failed === true && r.reason === "no edit history to undo");

// No pending -> returns [] (no output)
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]),
  { pending: null }, {});
assert("no pending -> empty output", r.length === 0);

// =============================================================================
// 8. Build Audit Row
// =============================================================================
console.log("\n[Build Audit Row]");

r = runNode("Build Audit Row", {}, { audit: { who: "12345", when: "2026-10-09T00:00:00Z", command: "stop", before: "—", after: "stop", result: "approved" } }, {})[0].json;
assert("audit row has all columns", r.who === "12345" && r.when === "2026-10-09T00:00:00Z" && r.command === "stop" && r.before === "—" && r.after === "stop" && r.result === "approved");

// No audit -> empty
r = runNode("Build Audit Row", {}, { audit: null }, {});
assert("no audit -> empty output", r.length === 0);

// =============================================================================
// 9. E2E chain — graph walk from Handle Confirm over the real workflow
//    connections in AHAWR_Status_Bot.json. Node code is loaded by name via
//    runNode; `{{ ... }}` expressions (including $('X')) are evaluated
//    against the item store. In-memory tables mirror the dataTable nodes:
//      get    -> Load Live State / Load Config Row / Load Mission Row
//               (read before the write; their rows are the 'before' source)
//      update -> Write hermes_config / Write missions / Write Autonomous
//                Agent Live State
//      insert -> Write Audit Log (one new row per audit item)
//      get    -> Read After hermes_config / Read After missions /
//                Read After Live State (re-read after the write)
// =============================================================================
console.log("\n[E2E Chain]");

// Evaluate a "={{ expr }}" expression against the item store (the $() refs)
// plus the node's own item (the $json refs).
const ev = (s, store, item) => {
  if (typeof s !== "string" || !s.startsWith("={{ ") || !s.endsWith(" }}")) return s;
  const dollar = name => {
    const arr = (store && store[name]) || [];
    return {
      first: () => (arr.length ? arr[0] : undefined),
      all: () => arr,
      last: () => (arr.length ? arr[arr.length - 1] : undefined),
    };
  };
  try { return new Function("$", "$json", "return " + s.slice(3, -3))(dollar, item || {}); }
  catch (e) { return undefined; }
};

function makeTables(failWrites, errorWrite) {
  const tables = {
    "Autonomous Agent Live State": [{ ...LIVE, state_key: CFG.state_namespace }],
    hermes_config: [{ ...CONFIG_ROW }],
    missions: [{ ...MISSION_ROW }],
    "AHAWR Bot Audit Log": [],
    "AHAWR Command Queue": [],
  };
  return {
    tables,
    failWrites: failWrites || [],
    errorWrite: errorWrite || null, // node name whose update emits an error item
    value(table, key) { const r = tables[table] && tables[table][0]; return r ? r[key] : undefined; },
    auditRows() { return tables["AHAWR Bot Audit Log"]; },
  };
}

// Walk the workflow graph starting at Handle Confirm. Returns
// { replies, trace } — replies are the payloads sent to the telegram node.
function runGraph(sd, parseItem, T) {
  const conn = wf.connections;
  const nodes = {
    Config: [{ json: JSON.parse(JSON.stringify(CFG)) }],
    "Parse Command": [{ json: parseItem }],
    "Load Live State": T.tables["Autonomous Agent Live State"].map(r => ({ json: { ...r } })),
    "Load Config Row": T.tables.hermes_config.map(r => ({ json: { ...r } })),
    "Load Mission Row": T.tables.missions.map(r => ({ json: { ...r } })),
  };
  const replies = [], trace = [];
  const queue = [{ node: "Handle Confirm", items: [{ json: parseItem }] }];
  const branchIdxOf = (n) => {
    if (n.type === "n8n-nodes-base.if") {
      const c = n.parameters.conditions.conditions[0];
      return String(ev(c.leftValue, nodes)) === String(c.rightValue) ? 0 : 1;
    }
    if (n.type === "n8n-nodes-base.switch") {
      const left = ev(n.parameters.values[0].leftValue, nodes);
      const i = n.parameters.values.findIndex(v => String(v.rightValue) === String(left));
      return i < 0 ? 0 : i;
    }
    return 0;
  };
  while (queue.length) {
    const name = queue[0].node, inItems = queue[0].items;
    queue.shift();
    trace.push(name);
    const n = nodesByName[name];
    let out = inItems;
    if (!inItems.length) { out = []; }
    else if (n.type === "n8n-nodes-base.code") {
      out = runNode(name, nodes, inItems[0].json, sd);
      nodes[name] = out;
    } else if (n.type === "n8n-nodes-base.dataTable") {
      const table = n.parameters.dataTableId.value;
      const rows = T.tables[table] || [];
      const item = inItems.length ? inItems[0].json : null;
      let matched = rows;
      let filtered = false;
      for (const f of (n.parameters.filters ? n.parameters.filters.conditions : [])) {
        const fv = ev(f.keyValue, nodes, item);
        matched = matched.filter(r => String(r[f.keyName]) === String(fv));
        filtered = true;
      }
      if (n.parameters.operation === "update") {
        const cols = (n.parameters.columns && n.parameters.columns.column) || [];
        if (T.errorWrite === name) {
          // modeled node error (onError continueRegularOutput): error item out,
          // row left unchanged, no re-read after it
          out = [{ json: { error: { message: "update failed: simulated node error" } } }];
        } else if (!T.failWrites.includes(table)) {
          if (filtered && matched.length && cols.length && cols[0].value !== undefined) {
            for (const c of cols) matched[0][ev(c.name, nodes, item)] = ev(c.value, nodes, item);
          }
        } else if (matched.length) {
          // simulated write failure: row left unchanged, failure marker out
          out = [{ json: { __writeFailed: table } }];
        }
      } else if (n.parameters.operation === "insert") {
        // insert (Write Audit Log): append one new row built from the
        // item's fields; every audit item becomes its own row
        const cols = (n.parameters.columns && n.parameters.columns.column) || [];
        const row = {};
        for (const c of cols) row[c.name] = c.value !== undefined ? ev(c.value, nodes, item) : (item ? item[c.name] : undefined);
        rows.push(row);
        out = [{ json: row }];
      } else {
        out = matched.map(r => ({ json: { ...r } }));
      }
    } else if (n.type === "n8n-nodes-base.telegram") {
      const p = n.parameters;
      const item = inItems.length ? inItems[0].json : null;
      replies.push({ chatId: ev(p.chatId, nodes, item), text: ev(p.text, nodes, item), buttons: p.additionalFields ? (ev(p.additionalFields.reply_markup, nodes, item) || "") : "" });
      continue; // terminal node, no outgoing connections
    }
    const idx = branchIdxOf(n);
    const targets = (conn[name] && conn[name].main[idx]) || [];
    for (const o of targets) queue.push({ node: o.node, items: out });
  }
  return { replies, trace };
}

// Seed a fresh /set pending through Handle Edit (real node code), then
// approve it through the graph walk. Returns { T, sd, replies, trace }.
function setAndApprove(T, key, value, chatId, text) {
  const sd = {};
  const editItem = { chat_id: chatId, text: text, command: text.split(/\s+/)[0], allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const out = runGraph(sd, { chat_id: chatId, text: "/approve", command: "/approve", allowed: true }, T);
  return { T, sd, replies: out.replies, trace: out.trace };
}

// Pending seeded by a real /set through Handle Edit, then a single confirm
// pass with the given parse item. The one-time code is random, so the code
// under test is read back from the per-chat pending store.
function confirmCase(T, parseItem) {
  const sd = {};
  const editItem = { chat_id: 12345, text: "/set worker_model claude-opus-5", command: "/set", allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const code = sd.bot_pending && sd.bot_pending["12345"] ? sd.bot_pending["12345"].code : null;
  const out = runGraph(sd, parseItem, T);
  return { T, sd, replies: out.replies, trace: out.trace, code };
}

// Same, but the specified write node emits an error item instead of writing
// (models onError 'continueRegularOutput' on the dataTable update node).
function setAndApproveWithError(T, key, value, chatId, text, errNode) {
  const sd = {};
  const editItem = { chat_id: chatId, text: text, command: text.split(/\s+/)[0], allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const out = runGraph(sd, { chat_id: chatId, text: "/approve", command: "/approve", allowed: true }, { ...T, errorWrite: errNode });
  return { T, sd, replies: out.replies, trace: out.trace };
}

// --- (a) confirmed /set changes the stub table value -------------------
{
  const T = makeTables();
  const res = setAndApprove(T, "worker_model", "claude-opus-5", 12345, "/set worker_model claude-opus-5");
  assert("e2e: /approve reply says 'было -> стало'",
    res.replies.length === 1 && res.replies[0].text === "worker_model: было claude-sonnet-5 -> стало claude-opus-5.");
  assert("e2e: /approve reply never says 'approved'", !res.replies[0].text.includes("approved"));
  assert("e2e: confirmed /set changed hermes_config.worker_model", T.value("hermes_config", "worker_model") === "claude-opus-5");
  const wi = res.trace.indexOf("Write hermes_config");
  const ri = res.trace.indexOf("Read After hermes_config");
  assert("e2e: write happens before the re-read", wi >= 0 && ri > wi);
}

// --- (b) audit row holds before and after; insert added a new row -----
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const after = "claude-opus-5";
  const rowsBefore = T.auditRows().length;
  setAndApprove(T, "worker_model", after, 12345, "/set worker_model " + after);
  const rows = T.auditRows();
  const last = rows[rows.length - 1];
  assert("e2e: audit insert added a new row", rows.length === rowsBefore + 1);
  assert("e2e: audit row holds before and after",
    last && last.command === "set worker_model = " + after && last.before === before && last.after === after && last.result === "approved");
}

// --- (c) /undo restores the pre-edit value -----------------------------
// The reverted /undo handler stores no edit_history, so the pending carries
// the restored key/value directly; Apply re-validates like any edit.
{
  const T = makeTables();
  T.tables.hermes_config[0].worker_model = "claude-opus-5"; // value set by the edit being undone
  const sd = {
    bot_pending: { "12345": { action: "undo 1", key: "worker_model", value: "claude-sonnet-5", table: "hermes_config", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000, kind: "edit", args: { n: 1 } } },
  };
  const res = runGraph(sd, { chat_id: 12345, text: "/approve", command: "/approve", allowed: true }, T);
  assert("e2e: /undo restored the pre-edit value", T.value("hermes_config", "worker_model") === "claude-sonnet-5");
  assert("e2e: /undo reply built from the re-read value",
    res.replies.length === 1 && res.replies[0].text === "worker_model: было claude-opus-5 -> стало claude-sonnet-5.");
  const last = T.auditRows().pop();
  assert("e2e: /undo audit row holds before and after, approved",
    last && last.command === "undo 1" && last.before === "claude-opus-5" && last.after === "claude-sonnet-5" && last.result === "approved");
}

// --- (d) wrong code -----------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 12345, text: "CONF-12345-ZZZZ99", command: "conf-12345-zzzz99", allowed: true });
  assert("e2e: wrong code -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: wrong code -> reply not approved", res.replies.length === 1 && !res.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: wrong code -> rejected audit row", last && last.result === "rejected");
  assert("e2e: wrong code -> no Apply Mutation on the path", !res.trace.includes("Apply Mutation"));
}

// --- (e) foreign chat ---------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 67890, text: "", command: "callback_query", callback_data: "confirm", allowed: true });
  assert("e2e: foreign chat -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: foreign chat -> reply not approved", res.replies.length === 1 && !res.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: foreign chat -> rejected audit row", last && last.result === "rejected");
  assert("e2e: foreign chat -> no Apply Mutation on the path", !res.trace.includes("Apply Mutation"));
}

// --- (f) viewer role -----------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 77777, text: "", command: "callback_query", callback_data: "confirm", allowed: false });
  assert("e2e: viewer role -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: viewer role -> reply not approved", res.replies.length === 1 && !res.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: viewer role -> rejected audit row", last && last.result === "rejected");
  assert("e2e: viewer role -> no Apply Mutation on the path", !res.trace.includes("Apply Mutation"));
}

// --- (g) expired code ----------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const sd = {};
  const editItem = { chat_id: 12345, text: "/set worker_model claude-opus-5", command: "/set", allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  sd.bot_pending["12345"].created_at = Date.now() - 130000; // per-chat TTL (120 s) expired
  const res = runGraph(sd, { chat_id: 12345, text: sd.bot_pending["12345"].code, command: "confirm", allowed: true }, T);
  assert("e2e: expired code -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: expired code -> reply not approved", res.replies.length === 1 && !res.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: expired code -> rejected audit row", last && last.result === "rejected");
  assert("e2e: expired code -> no Apply Mutation on the path", !res.trace.includes("Apply Mutation"));
}

// --- (g2) unknown code, chat with no pending -> no write, rejected audit ---
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const sd = {}; // no pending stored anywhere
  const res = runGraph(sd, { chat_id: 12345, text: "/approve CONF-99999-UNKNOWN", command: "/approve", allowed: true }, T);
  assert("e2e: unknown code, no pending -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: unknown code, no pending -> reply rejected", res.replies.length === 1 && res.replies[0].text.includes("Rejected") && !res.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: unknown code, no pending -> rejected audit row", last && last.result === "rejected" && last.reason === "unknown one-time code");
}

// --- (h) code reuse ------------------------------------------------------
{
  const T = makeTables();
  const sd = {};
  const editItem = { chat_id: 12345, text: "/set worker_model claude-opus-5", command: "/set", allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const code = sd.bot_pending["12345"].code;
  runGraph(sd, { chat_id: 12345, text: code, command: "confirm", allowed: true }, T);
  const afterFirst = T.value("hermes_config", "worker_model");
  // second use of the same one-time code, same chat
  const second = runGraph(sd, { chat_id: 12345, text: code, command: "confirm", allowed: true }, T);
  assert("e2e: code reuse -> first use wrote the value", afterFirst === "claude-opus-5");
  assert("e2e: code reuse -> value unchanged on second use", T.value("hermes_config", "worker_model") === afterFirst);
  assert("e2e: code reuse -> second reply not approved", second.replies.length === 1 && !second.replies[0].text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: code reuse -> rejected audit row on second use", last && last.result === "rejected");
}

// --- (i) whitelist / type / range rejected before any write -------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const cases = [
    ["outside whitelist", "/set api_key xyz", "Not editable"],
    ["wrong type", "/set max_tasks abc", "Rejected"],
    ["out of range", "/set max_tasks 9999", "Rejected"],
  ];
  for (const [label, text, marker] of cases) {
    const sd = {};
    const item = { chat_id: 12345, text, command: text.split(/\s+/)[0], allowed: true };
    const reply = runNode("Handle Edit",
      dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: item }]),
      { json: item }, sd)[0].json;
    assert("e2e: " + label + " -> rejected with '" + marker + "'", reply.text.includes(marker));
    assert("e2e: " + label + " -> no pending stored", !sd.bot_pending);
    assert("e2e: " + label + " -> no write, no audit row", T.value("hermes_config", "worker_model") === before && T.auditRows().length === 0);
  }
}

// --- (j) callback_query carrying the code is accepted --------------------
// The code is read back from the per-chat pending store (it is random).
{
  const T = makeTables();
  const sd = {};
  const editItem = { chat_id: 12345, text: "/set worker_model claude-opus-5", command: "/set", allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const code = sd.bot_pending["12345"].code;
  const res = runGraph(sd, { chat_id: 12345, text: "", command: "callback_query", callback_data: code, allowed: true }, T);
  assert("e2e: callback carrying code -> reply 'было -> стало'",
    res.replies.length === 1 && res.replies[0].text === "worker_model: было claude-sonnet-5 -> стало claude-opus-5.");
  assert("e2e: callback carrying code -> value written", T.value("hermes_config", "worker_model") === "claude-opus-5");
  const last = T.auditRows().pop();
  assert("e2e: callback carrying code -> approved audit row", last && last.result === "approved" && last.command === "set worker_model = claude-opus-5");
}

// --- (k) simulated write failure -> error reply, audit 'failed' ---------
// The hermes_config write node is forced to fail: the row is left unchanged
// and the re-read therefore differs from the expected value.
{
  const T = makeTables(["hermes_config"]);
  const before = T.value("hermes_config", "worker_model");
  const res = setAndApprove(T, "worker_model", "claude-opus-5", 12345, "/set worker_model claude-opus-5");
  assert("e2e: write failure -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: write failure -> error reply, not 'было -> стало'",
    res.replies.length === 1 && res.replies[0].text.startsWith("Error:") && !res.replies[0].text.includes("стало"));
  const last = T.auditRows().pop();
  assert("e2e: write failure -> audit result 'failed'", last && last.result === "failed" && last.reason.startsWith("write failed or re-read value differs"));
}

// --- (l) write error item (onError continueRegularOutput) --------------
// The write node emits an error item and is not re-read; Build Reply turns
// it into an error reply with an audit result 'failed'.
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = setAndApproveWithError(T, "worker_model", "claude-opus-5", 12345, "/set worker_model claude-opus-5", "Write hermes_config");
  assert("e2e: write error item -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: write error item -> error reply, not 'было -> стало'",
    res.replies.length === 1 && res.replies[0].text.startsWith("Error:") && !res.replies[0].text.includes("стало"));
  const last = T.auditRows().pop();
  assert("e2e: write error item -> audit result 'failed'", last && last.result === "failed" && last.reason.startsWith("write failed or re-read value differs"));
}

// --- (m) Answer Callback? expression (block 6b-2) --------------------
// The condition expression is read from the workflow node (acExpr, defined
// in block 6b above). Evaluate it against a callback item (kind='callback',
// callback_id='cb1') and a plain-message item (kind='message',
// callback_id=null): it must yield 'callback' and 'none' respectively.
{
  const store = { "Parse Command": [{ json: { kind: "callback", callback_id: "cb1", allowed: true } }] };
  assert("Answer Callback? -> 'callback' for callback update",
    ev(acExpr, store, { kind: "callback", callback_id: "cb1" }) === "callback");
  store["Parse Command"] = [{ json: { kind: "message", callback_id: null, allowed: true } }];
  assert("Answer Callback? -> 'none' for plain message",
    ev(acExpr, store, { kind: "message", callback_id: null }) === "none");
}

// =============================================================================
// 10. Command queue and live-state flags for /start, /resume, /retry,
//     /accept, /restart (T009)
//     Confirmed /start, /resume and /restart insert a row into the AHAWR
//     Command Queue table (the bot cannot launch runs itself) and the reply
//     states the operator launch line; confirmed /retry and /accept set the
//     Live State flags the main workflow consumes during a running mission.
//     All replies are exact strings and none of them contains 'approved'.
// =============================================================================
console.log("\n[Command Queue and Flags]");

// Seed a pending control command through the real Handle Control node, then
// confirm it through the graph walk. Returns { T, sd, replies, trace, pending }.
function controlAndApprove(T, chatId, text) {
  const sd = {};
  const ctrlItem = { chat_id: chatId, text: text, command: text.split(/\s+/)[0], allowed: true };
  const data = dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: ctrlItem }],
    "Load Live State", [{ json: { ...LIVE, state_key: CFG.state_namespace } }]);
  const out = runNode("Handle Control", data, { json: ctrlItem }, sd);
  const pending = sd.bot_pending && sd.bot_pending[String(chatId)] ? sd.bot_pending[String(chatId)] : null;
  const res = runGraph(sd, { chat_id: chatId, text: "/approve", command: "/approve", allowed: true }, T);
  return { T, sd, replies: res.replies, trace: res.trace, pending };
}

// --- (n) confirmed /start -> queue row, exact launch line in the reply ---
{
  const T = makeTables();
  const res = controlAndApprove(T, 12345, "/start mission-a");
  assert("e2e: /start reply is the exact queue text with the launch line",
    res.replies.length === 1 && res.replies[0].text ===
    "записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh mission-a");
  assert("e2e: /start reply never says 'approved'", !res.replies[0].text.includes("approved"));
  assert("e2e: /start inserted a queue row (dataTable insert node)",
    res.trace.includes("Write Command Queue") && T.tables["AHAWR Command Queue"].length === 1);
  const q = T.tables["AHAWR Command Queue"][0];
  assert("e2e: queue row holds action, mission, operator role and pending status",
    q.action === "start mission-a" && q.mission_id === "mission-a" && q.state_key === CFG.state_namespace
    && q.chat_id === 12345 && q.role === "admin" && q.status === "pending");
  assert("e2e: queue row carries the operator launch line as result",
    q.result === "bash data-tables/ops/launch_mission.sh mission-a");
  const last = T.auditRows().pop();
  assert("e2e: /start audit row written, result approved, command recorded",
    last && last.result === "approved" && last.command === "start mission-a"
    && last.after === "queued: start mission-a");
}

// --- (o) confirmed /accept -> Live State flag set, workflow note in reply ---
{
  const T = makeTables();
  const res = controlAndApprove(T, 12345, "/accept by hand");
  assert("e2e: /accept reply states what the workflow will do and when",
    res.replies.length === 1 && res.replies[0].text ===
    "accept_requested: было — -> стало true. the workflow will accept the current task and continue with the next one as soon as the running mission reaches the next task.");
  assert("e2e: /accept reply never says 'approved'", !res.replies[0].text.includes("approved"));
  assert("e2e: /accept set the Live State flag (live-state update node)",
    res.trace.includes("Write Autonomous Agent Live State") && T.value("Autonomous Agent Live State", "accept_requested") === true);
  const last = T.auditRows().pop();
  assert("e2e: /accept audit row written, result approved, flag recorded",
    last && last.result === "approved" && last.command === "accept by hand"
    && last.after === "true" && last.reason === "flag set in the live state");
}

// --- (p) confirmed /resume and /restart -> queue rows -------------------
for (const [verb, mission] of [["resume", "mission-b"], ["restart", "mission-a"]]) {
  const T = makeTables();
  const res = controlAndApprove(T, 12345, "/" + verb + " " + mission);
  assert("e2e: /" + verb + " reply is the exact queue text with the launch line",
    res.replies.length === 1 && res.replies[0].text ===
    "записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh " + mission);
  assert("e2e: /" + verb + " reply never says 'approved'", !res.replies[0].text.includes("approved"));
  const q = T.tables["AHAWR Command Queue"][0];
  assert("e2e: /" + verb + " queue row written by the insert node, status pending",
    res.trace.includes("Write Command Queue") && q.action === verb + " " + mission
    && q.mission_id === mission && q.status === "pending");
  const last = T.auditRows().pop();
  assert("e2e: /" + verb + " audit row written, result approved",
    last && last.result === "approved" && last.command === verb + " " + mission);
}

// --- (q) confirmed /retry -> Live State flag set ----------------------
{
  const T = makeTables();
  const res = controlAndApprove(T, 12345, "/retry");
  assert("e2e: /retry reply states what the workflow will do and when",
    res.replies.length === 1 && res.replies[0].text ===
    "retry_requested: было — -> стало true. the workflow will retry the current task once more with the last review as feedback as soon as the running mission reaches the next task.");
  assert("e2e: /retry reply never says 'approved'", !res.replies[0].text.includes("approved"));
  assert("e2e: /retry set the Live State flag (live-state update node)",
    res.trace.includes("Write Autonomous Agent Live State") && T.value("Autonomous Agent Live State", "retry_requested") === true);
  const last = T.auditRows().pop();
  assert("e2e: /retry audit row written, result approved, flag recorded",
    last && last.result === "approved" && last.command === "retry" && last.after === "true");
}

// =============================================================================
// 11. Main workflow control nodes (patched AHAWR_v13_ClaudeCode.json) —
//     pause / stop / steer / accept / retry, run on stubs against the
//     PATCHED nodes loaded by name; plus the Worker-input equality test
//     (flags unset) and the patch-script idempotency / missing-anchor
//     checks.  The pause and steer cases are ALSO run against the
//     UNPATCHED file (git show HEAD:AHAWR_v13_ClaudeCode.json, temp path)
//     to capture the before-fix failing output for the log.
// =============================================================================

function loadWorkflow(pathStr) {
  return JSON.parse(fs.readFileSync(pathStr, "utf-8"));
}

// runCode(code, dataMap, jsonItem) — run jsCode from an arbitrary workflow
// (not only the bot workflow) with the same $ / $json stubs as runNode.
// Normalizes the return to an array of {json} items (some nodes return a
// single object instead of an array).
function runCode(code, dataMap, jsonItem) {
  const dollar = name => {
    const arr = dataMap[name] || [];
    return {
      first: () => (arr.length ? arr[0] : undefined),
      all: () => arr,
      last: () => (arr.length ? arr[arr.length - 1] : undefined),
    };
  };
  const fn = new Function("$", "$json", "dataRef", "\n" + code);
  const out = fn(dollar, jsonItem, dataMap);
  if (Array.isArray(out)) return out;
  if (out && typeof out === "object") return [out];
  return [ { json: out } ];
}

// IF condition from a workflow node, evaluated against the item store.
// Handles the combinator: 'or' -> any condition true; 'and' -> all true.
function ifCondition(wf, nodeName, store, item) {
  const node = wf.nodes.find(n => n.name === nodeName);
  if (!node) throw new Error("node not found: " + nodeName);
  const conds = node.parameters.conditions.conditions;
  const comb = node.parameters.conditions.combinator;
  const vals = conds.map(c =>
    ev(c.leftValue, store, item) === c.rightValue || c.operator.operation === "true"
  );
  return comb === "or" ? vals.some(Boolean) : vals.every(Boolean);
}

const MAIN_WF_PATCHED = path.join(__dirname, "..", "..", "AHAWR_v13_ClaudeCode.json");
const MAIN_WF_UNPATCHED = path.join(__dirname, "..", "..", "AHAWR_v13_ClaudeCode.unpatched.json");
// Unpatched version: git show HEAD:AHAWR_v13_ClaudeCode.json into a temp path.
const { execSync } = require("child_process");
execSync(`git -C ${JSON.stringify(path.join(__dirname, "..", ".."))} show HEAD:AHAWR_v13_ClaudeCode.json > ${JSON.stringify(MAIN_WF_UNPATCHED)}`, { stdio: "ignore" });

// Stubs for the main-workflow nodes.
const CONSTANTS_STUB = {
  state_namespace: "ahawr-context-retrieval-layer-v1",
  max_attempts_per_task: 2,
  worker_model: "claude-sonnet-5",
  worker_provider: "anthropic",
  worker_poll_seconds: 30,
  worker_max_polls: 1,
  worker_max_provider_retries: 0,
  retry_delays: [15, 30, 60, 120, 300],
  service_provider: "anthropic",
  service_model: "claude-haiku-4-5",
  runner_url: "http://claude-runner:8700",
  runner_api_key: "",
};
const PROMPTS_STUB = { worker_prompt: "WORKER PROMPT (stub)" };
const MISSION_STUB = {
  working_directory_block: "WORKING DIRECTORY: D:\\OpenAIProjects\\ai-chemistry-video",
  working_directory: "D:\\OpenAIProjects\\ai-chemistry-video",
  acceptance_criteria: ["criterion A", "criterion B"],
  objective: "stub mission objective",
};
const TASK_STATE = {
  tasks: [
    {
      id: "task-001",
      title: "Stub task",
      objective: "do the thing",
      acceptance_criteria: ["works", "tested"],
      verification: ["node tests/n8n-code/run.js"],
      scope: ["tests/n8n-code/run.js"],
    },
    {
      id: "task-002",
      title: "Second stub task",
      objective: "second objective",
      acceptance_criteria: ["done"],
      verification: ["echo ok"],
      scope: ["README.md"],
    },
  ],
  task_index: 0,
  task_attempt: 2,
  review_feedback: "review said: fix the retry path",
  plan_summary: "stub plan",
  worker_session_id: "sess-123",
  worker_status: "completed",
  worker_run_id: "run-abc",
  worker_mission_criteria: false,
  retry_fresh_session: false,
  previous_session_digest: "digest text",
};

// =============================================================================
// 11a. Control nodes on the PATCHED main workflow (run on stubs)
// =============================================================================
console.log("\n[Main Workflow Control Nodes (patched)]");

const wfP = loadWorkflow(MAIN_WF_PATCHED);
const codeOf = (wf, name) => wf.nodes.find(n => n.name === name).parameters.jsCode;

// --- pause blocks the start of the next task ---------------------------
// Pause Gate (fresh flag read) + Hold Paused + Hold Paused Write Live State
// must fire when the Live State row says paused=true: the gate routes the
// next task to Hold Paused (true branch), not to Prepare Current Task.
{
  const live = { paused: true, pause_reason: "maintenance", stop_requested: false, accept_requested: false, retry_requested: false, steer_note: "", operator_feedback: "previous note", workflow_status: "running", owner: "" };
  const data = { "Pause Gate Live State": [{ json: live }], "Pause Gate Flags": [] };
  const flags = runCode(codeOf(wfP, "Pause Gate Flags"), data, {}).map(x => x.json);
  assert("pause: Pause Gate Flags reads the fresh live row (paused=true)", flags[0].paused === true && flags[0].pause_reason === "maintenance");
  const store = { "Pause Gate Flags": [{ json: flags[0] }] };
  assert("pause: Pause Gate condition is true (blocked)", ifCondition(wfP, "Pause Gate", store));
  const hold = runCode(codeOf(wfP, "Hold Paused"), store, TASK_STATE).map(x => x.json);
  assert("pause: Hold Paused routes to execution_route 'paused'", hold[0].execution_route === "paused" && hold[0].final_status === "paused_by_operator");
  const holdWrite = wfP.nodes.find(n => n.name === "Hold Paused Write Live State");
  const cols = holdWrite.parameters.columns.value;
  assert("pause: Hold Paused Write sets workflow_status='paused' and clears owner", cols["workflow_status"] === "={{ 'paused' }}" && cols["owner"] === "={{ '' }}");
  // connections: Pause Gate false branch -> Clear Consumed Flags (next task start),
  // true branch -> Hold Paused
  const gateOut = wfP.connections["Pause Gate"].main;
  assert("pause: Pause Gate true branch -> Hold Paused (next task blocked)", gateOut[1].some(e => e.node === "Hold Paused"));
  assert("pause: Pause Gate false branch -> Clear Consumed Flags (unpaused path)", gateOut[0].some(e => e.node === "Clear Consumed Flags"));
}

// --- stop sets workflow_status=stopped and leaves nothing running -----
// Stop Check (fresh flag read) + Stop Workflow + Stop Workflow Write Live State.
{
  const live = { paused: false, pause_reason: "", stop_requested: true, accept_requested: false, retry_requested: false, steer_note: "", operator_feedback: "", workflow_status: "running", owner: "op-1" };
  const data = { "Stop Check Worker Live State": [{ json: live }], "Stop Check Worker Flags": [] };
  const flags = runCode(codeOf(wfP, "Stop Check Worker Flags"), data, {}).map(x => x.json);
  assert("stop: Stop Check Worker Flags reads the fresh live row (stop_requested=true)", flags[0].stop_requested === true);
  const store = { "Stop Check Worker Flags": [{ json: flags[0] }] };
  assert("stop: Stop Check Worker condition is true", ifCondition(wfP, "Stop Check Worker", store));
  const stop = runCode(codeOf(wfP, "Stop Workflow"), store, TASK_STATE).map(x => x.json);
  assert("stop: Stop Workflow output has workflow_status='stopped'", stop[0].workflow_status === "stopped" && stop[0].execution_route === "stopped");
  const stopWrite = wfP.nodes.find(n => n.name === "Stop Workflow Write Live State");
  const cols = stopWrite.parameters.columns.value;
  assert("stop: Stop Workflow Write sets workflow_status='stopped' and clears owner (nothing left running)", cols["workflow_status"] === "={{ 'stopped' }}" && cols["owner"] === "={{ '' }}");
  // Stop Check branches: false -> Worker Completed?, true -> Stop Workflow
  const scwOut = wfP.connections["Stop Check Worker"].main;
  assert("stop: Stop Check Worker false branch -> Worker Completed?", scwOut[0].some(e => e.node === "Worker Completed?"));
  assert("stop: Stop Check Worker true branch -> Stop Workflow", scwOut[1].some(e => e.node === "Stop Workflow"));
  // no running-state write: only stop/pause paths write status
  assert("stop: reviewer stop check reads its own fresh pair",
    wfP.nodes.find(n => n.name === "Stop Check Reviewer").parameters.conditions.conditions[0].leftValue === "={{ $('Stop Check Reviewer Flags').first().json.stop_requested }}");
}

// --- steer reaches review_feedback of the NEXT attempt only ------------
// Prepare Current Task appends the steer note to review_feedback (the input
// for the next attempt); the flag is consumed + cleared afterwards.
{
  const live = { paused: false, pause_reason: "", stop_requested: false, accept_requested: false, retry_requested: false, steer_note: "keep the output short", operator_feedback: "previous note", workflow_status: "running", owner: "" };
  const data = { "Pause Gate Flags": [{ json: live }], "Prompt Templates": [{ json: PROMPTS_STUB }], "Mission": [{ json: MISSION_STUB }] };
  const prep = runCode(codeOf(wfP, "Prepare Current Task"), data, TASK_STATE).map(x => x.json);
  const combined = prep[0].review_feedback;
  assert("steer: Prepare Current Task review_feedback carries the steer note", combined === "review said: fix the retry path\nOPERATOR STEER NOTE: keep the output short");
  assert("steer: worker_prompt includes the steer note in PREVIOUS REVIEW FEEDBACK", prep[0].worker_prompt.includes("OPERATOR STEER NOTE: keep the output short"));
  // steer is consumed and cleared before the next task starts
  const clear = runCode(codeOf(wfP, "Clear Consumed Flags"), data, prep[0]).map(x => x.json);
  assert("steer: Clear Consumed Flags records the consumed steer note", clear[0].cleared_flags.steer_note === "keep the output short");
  const clearWrite = wfP.nodes.find(n => n.name === "Clear Consumed Flags Write Live State");
  const cols = clearWrite.parameters.columns.value;
  assert("steer: clear write empties steer_note (next attempt starts clean)", cols["steer_note"] === "={{ '' }}");
  // and without a steer note nothing is appended
  const dataNoSteer = { "Pause Gate Flags": [{ json: { ...live, steer_note: "" } }], "Prompt Templates": [{ json: PROMPTS_STUB }], "Mission": [{ json: MISSION_STUB }] };
  const prepNoSteer = runCode(codeOf(wfP, "Prepare Current Task"), dataNoSteer, TASK_STATE).map(x => x.json);
  assert("steer: no steer note -> review_feedback unchanged (only the review feedback)", prepNoSteer[0].review_feedback === "review said: fix the retry path");
}

// --- accept advances with the reason recorded -------------------------
// The Accept Gate reads fresh; Review Passed? OR's accept_requested into
// its condition; Clear Consumed Flags records the accept reason in last_control.
{
  const live = { paused: false, pause_reason: "", stop_requested: false, accept_requested: true, retry_requested: false, steer_note: "", operator_feedback: "operator accepted it by hand", workflow_status: "running", owner: "" };
  const data = { "Accept Gate Live State": [{ json: live }], "Accept Gate Flags": [] };
  const flags = runCode(codeOf(wfP, "Accept Gate Flags"), data, {}).map(x => x.json);
  assert("accept: Accept Gate Flags reads the fresh live row (accept_requested=true)", flags[0].accept_requested === true && flags[0].operator_feedback === "operator accepted it by hand");
  const store = { "Accept Gate Flags": [{ json: flags[0] }] };
  assert("accept: Review Passed? passes on accept_requested even without review_status=pass",
    ifCondition(wfP, "Review Passed?", store, { review_status: "fail" }) );
  const rpConds = wfP.nodes.find(n => n.name === "Review Passed?").parameters.conditions;
  assert("accept: Review Passed? combinator is 'or' and has the accept-requested condition",
    rpConds.combinator === "or" && rpConds.conditions.some(c => c.id === "accept-requested"));
  const clear = runCode(codeOf(wfP, "Clear Consumed Flags"), { "Pause Gate Flags": [{ json: live }] }, { accept_requested: true }).map(x => x.json);
  assert("accept: Clear Consumed Flags records accept_requested_was_true and the reason",
    clear[0].accept_requested_was_true === true && clear[0].accept_reason === "operator accepted it by hand");
  const clearWrite = wfP.nodes.find(n => n.name === "Clear Consumed Flags Write Live State");
  const cols = clearWrite.parameters.columns.value;
  assert("accept: clear write records the accept reason in last_control",
    cols["last_control"] === "={{ $json.accept_requested_was_true ? 'accept: ' + String($json.accept_reason ?? '') : '' }}");
}

// --- retry adds one attempt with the remark ---------------------------
// Retry Current Task: retry_requested allows one attempt beyond
// max_attempts_per_task, and the operator remark (steer + feedback)
// rides along in review_feedback of the new attempt.
{
  const live = { paused: false, pause_reason: "", stop_requested: false, accept_requested: false, retry_requested: true, steer_note: "use the retrieval layer", operator_feedback: "the reviewer found a bug in the retry path", workflow_status: "running", owner: "" };
  const data = { "Accept Gate Flags": [{ json: live }] };
  // attempt 2 of 2 (max_attempts_per_task=2): without retry_requested this
  // would be the limit; with the flag one more attempt is allowed.
  const review = { review_reason: "reviewer found a bug", review_next_task: "fix the retry path", review_score: 0.4, review_status: "fail" };
  const out = runCode(codeOf(wfP, "Retry Current Task"), { "Accept Gate Flags": [{ json: live }], "Constants": [{ json: CONSTANTS_STUB }] }, { ...TASK_STATE, ...review, tasks: TASK_STATE.tasks }).map(x => x.json);
  assert("retry: one attempt beyond the limit is allowed with the flag", out[0].task_attempt === 3 && out[0].execution_route === "run_task");
  assert("retry: the remark (steer + operator feedback) is in review_feedback",
    out[0].review_feedback === "fix the retry path\nOPERATOR STEER NOTE: use the retrieval layer\nOPERATOR REMARK: the reviewer found a bug in the retry path");
  // without the flag, the same state hits the limit
  const dataNoFlag = { "Accept Gate Flags": [{ json: { ...live, retry_requested: false } }], "Constants": [{ json: CONSTANTS_STUB }] };
  const outNoFlag = runCode(codeOf(wfP, "Retry Current Task"), dataNoFlag, { ...TASK_STATE, ...review }).map(x => x.json);
  assert("retry: without the flag the attempt limit is enforced", outNoFlag[0].execution_route === "failed" && outNoFlag[0].final_status === "failed_task_attempt_limit");
}

// =============================================================================
// 11b. Worker-input equality: flags unset -> patched Build Worker Run Input
//      output is byte-identical to the unpatched node's output.
// =============================================================================
console.log("\n[Worker Input Equality (flags unset)]");

const wfU = loadWorkflow(MAIN_WF_UNPATCHED);
const nodesU = {};
for (const n of wfU.nodes) nodesU[n.name] = n;

const BWRI_INPUT = {
  ...TASK_STATE,
  worker_session_id: "",   // first attempt, nothing unfinished
  worker_status: "",
  review_feedback: "",
  previous_session_digest: "",
  retry_fresh_session: false,
  worker_mission_criteria: false,
};

function bwriOutput(wf) {
  const code = wf.nodes.find(n => n.name === "Build Worker Run Input").parameters.jsCode;
  const data = {
    "Constants": [{ json: CONSTANTS_STUB }],
    "Mission": [{ json: MISSION_STUB }],
    "Prompt Templates": [{ json: PROMPTS_STUB }],
    "Attach Worker Context": [],
  };
  return runCode(code, data, BWRI_INPUT);
}

{
  const outPatched = bwriOutput(wfP);
  const outUnpatched = bwriOutput(wfU);
  const jP = JSON.stringify(outPatched[0].json);
  const jU = JSON.stringify(outUnpatched[0].json);
  assert("worker input: patched output (flags unset) is byte-identical to the unpatched output", jP === jU);
  assert("worker input: the input field is the expected prompt (task + criteria + scope)", outPatched[0].json.input.includes("TASK task-001: Stub task") && outPatched[0].json.input.includes("WORKER PROMPT (stub)") && outPatched[0].json.input.includes("WORKING DIRECTORY:"));
  assert("worker input: no steer/accept/retry flags appear in the output", !jP.includes("OPERATOR STEER NOTE") && !jP.includes("OPERATOR REMARK"));
}

// =============================================================================
// 11c. Patch script: idempotency and missing-anchor error
// =============================================================================
console.log("\n[Patch Script Checks]");

// Idempotency: re-running the patch script on the already-patched file
// must be a no-op ("already patched, skipping").
{
  const { execSync } = require("child_process");
  const patchedCopy = path.join(__dirname, "patch_test_patched.json");
  fs.writeFileSync(patchedCopy, fs.readFileSync(MAIN_WF_PATCHED, "utf-8"));
  const res = execSync("python3 scripts/patch_main_control_flags.py " + JSON.stringify(patchedCopy), { cwd: path.join(__dirname, "..", ".."), encoding: "utf-8" });
  assert("patch script: re-run on patched file is a no-op ('already patched, skipping')", res.includes("already patched, skipping"));
  // the file must be unchanged
  assert("patch script: patched file byte-identical after the no-op run",
    fs.readFileSync(patchedCopy, "utf-8") === fs.readFileSync(MAIN_WF_PATCHED, "utf-8"));
  fs.unlinkSync(patchedCopy);
}

// Missing anchor: a workflow missing a required anchor must fail with a
// clear error naming the missing anchor(s).
{
  const { execSync } = require("child_process");
  const brokenCopy = path.join(__dirname, "patch_test_broken.json");
  const wfBroken = loadWorkflow(MAIN_WF_PATCHED);
  wfBroken.nodes = wfBroken.nodes.filter(n => n.name !== "Prepare Current Task");
  fs.writeFileSync(brokenCopy, JSON.stringify(wfBroken, null, 2));
  let errText = "";
  try {
    execSync("python3 scripts/patch_main_control_flags.py " + JSON.stringify(brokenCopy), { cwd: path.join(__dirname, "..", ".."), encoding: "utf-8", stdio: "pipe" });
  } catch (e) {
    errText = (e.stderr || "") + (e.stdout || "");
  }
  assert("patch script: missing anchor 'Prepare Current Task' -> error names it",
    errText.includes("missing anchor node(s): Prepare Current Task"));
  fs.unlinkSync(brokenCopy);
}

// =============================================================================
// 11d. Before-fix: same pause / steer tests against the UNPATCHED workflow
//     (git show HEAD:AHAWR_v13_ClaudeCode.json into a temp path).  These
//     MUST fail on the unpatched file — it has no pause gate / steer append
//     — and the failing output is appended to docs/telegram/tests-before-fix.txt.
// =============================================================================
console.log("\n[Main Workflow Control Nodes (unpatched, before-fix)]");

const wfBefore = loadWorkflow(MAIN_WF_UNPATCHED);
const nodesBefore = {};
for (const n of wfBefore.nodes) nodesBefore[n.name] = n;

let beforeFixLines = [];
// Record the before-fix result to the log (and print it).  These checks are
// EXPECTED to fail on the unpatched workflow — they document the bug — so
// they are not counted toward the suite's pass/fail totals.
function beforeFix(label, cond) {
  const line = (cond ? "PASS  " : "FAIL  ") + label;
  beforeFixLines.push(line);
  console.log(line);
}

// --- pause: the paused flag must block the start of the next task -------
// (the behaviour the patched workflow provides).  On the unpatched
// workflow the Pause Gate / Hold Paused nodes do not exist, so the task
// starts anyway -> this check FAILS before the fix.
{
  const live = { paused: true, pause_reason: "maintenance", stop_requested: false, accept_requested: false, retry_requested: false, steer_note: "", operator_feedback: "previous note", workflow_status: "running", owner: "" };
  const data = { "Pause Gate Flags": [{ json: live }], "Prompt Templates": [{ json: PROMPTS_STUB }], "Mission": [{ json: MISSION_STUB }] };
  const prep = runCode(codeOf(wfBefore, "Prepare Current Task"), data, TASK_STATE).map(x => x.json);
  // The workflow blocks the start only if the next task is routed to
  // 'paused' (Hold Paused), not to 'run_task'.  Unpatched: it starts.
  beforeFix("pause: the paused flag blocks the start of the next task (routes to 'paused')",
    prep[0].execution_route === "paused");
}

// --- steer: the steer note must reach review_feedback of the next attempt
// (the behaviour the patched workflow provides).  On the unpatched
// workflow the steer note is never appended -> this check FAILS before
// the fix.
{
  const live = { paused: false, pause_reason: "", stop_requested: false, accept_requested: false, retry_requested: false, steer_note: "keep the output short", operator_feedback: "previous note", workflow_status: "running", owner: "" };
  const data = { "Pause Gate Flags": [{ json: live }], "Prompt Templates": [{ json: PROMPTS_STUB }], "Mission": [{ json: MISSION_STUB }] };
  const prep = runCode(codeOf(wfBefore, "Prepare Current Task"), data, TASK_STATE).map(x => x.json);
  // The steer note must be appended to review_feedback (and carried into
  // the worker prompt) for the next attempt.  Unpatched: it is not.
  beforeFix("steer: the steer note is appended to review_feedback of the next attempt",
    prep[0].review_feedback.includes("OPERATOR STEER NOTE") && prep[0].worker_prompt.includes("OPERATOR STEER NOTE"));
}

// Append the failing output of the pause and steer tests to the before-fix log.
const logPath = path.join(__dirname, "..", "..", "docs", "telegram", "tests-before-fix.txt");
const existing = fs.readFileSync(logPath, "utf-8");
const beforeFixBlock = [
  "",
  "[Main Workflow Control Nodes (unpatched, before-fix)]",
  ...beforeFixLines,
].join("\n") + "\n";
// replace any previous version of this block (idempotent append)
const marker = "[Main Workflow Control Nodes (unpatched, before-fix)]";
let base = existing;
const mIdx = base.indexOf("\n" + marker);
if (mIdx >= 0) base = base.slice(0, mIdx + 1);
base = base.replace(/\s+$/, "") + beforeFixBlock;
fs.writeFileSync(logPath, base, "utf-8");
console.log("  appended before-fix output to docs/telegram/tests-before-fix.txt (" + beforeFixLines.length + " lines)");

// Clean up the temp unpatched file
fs.unlinkSync(MAIN_WF_UNPATCHED);

// =============================================================================
// 10. Control flag node references (main workflows)
// =============================================================================
// For each main workflow file, verify that every $(...) node reference in
// the flag code and IF condition expressions points to an existing node
// name.  Catches the literal-parenthesis bug: $('(Pause Gate Live State)')
// would reference a node that does not exist.

const MAIN_WFS = [
  path.join(__dirname, "..", "..", "AHAWR_v13_ClaudeCode.json"),
  path.join(__dirname, "..", "..", "AHAWR_v13.json"),
];

for (const wfPath of MAIN_WFS) {
  const wfMain = JSON.parse(fs.readFileSync(wfPath, "utf-8"));
  const namesMain = new Set(wfMain.nodes.map(n => n.name));
  const badRefs = [];

  for (const n of wfMain.nodes) {
    const js = n.parameters.jsCode || "";
    for (const m of js.matchAll(/\$\('([^']*)'\)/g)) {
      if (!namesMain.has(m[1])) badRefs.push(n.name + " -> " + m[1]);
    }
    if (n.type === "n8n-nodes-base.if") {
      for (const c of n.parameters.conditions.conditions) {
        const lv = c.leftValue || "";
        for (const m of lv.matchAll(/\$\('([^']*)'\)/g)) {
          if (!namesMain.has(m[1])) badRefs.push(n.name + " -> " + m[1]);
        }
      }
    }
  }
  assert(path.basename(wfPath) + ": no bad node references in flag code", badRefs.length === 0);
  if (badRefs.length) console.log("  bad refs: " + badRefs.join(", "));
}

// Also verify no literal-parenthesis node references: $('(Name)') is invalid.
for (const wfPath of MAIN_WFS) {
  const raw = fs.readFileSync(wfPath, "utf-8");
  const parenRefs = raw.split(/\$\('\(/g).length - 1;
  assert(path.basename(wfPath) + ": no literal-parenthesis $('( references", parenRefs === 0);
}

// Verify the Clear Consumed Flags jsCode contains accept_requested_was_true.
for (const wfPath of MAIN_WFS) {
  const wfMain = JSON.parse(fs.readFileSync(wfPath, "utf-8"));
  const clear = wfMain.nodes.find(n => n.name === "Clear Consumed Flags");
  const js = clear ? (clear.parameters.jsCode || "") : "";
  assert(path.basename(wfPath) + ": Clear Consumed Flags jsCode contains accept_requested_was_true", js.includes("accept_requested_was_true"));
}

// =============================================================================
// Summary
// =============================================================================
console.log("\n=========================================");
console.log("TOTAL: " + passed + " passed, " + failed + " failed");
console.log("=========================================");
process.exit(failed > 0 ? 1 : 0);
