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

// /undo N -> pending with args.n
sd = {};
r = runNode("Handle Edit",
  dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: { chat_id: 12345, text: "/undo 3", command: "/undo", allowed: true } }]),
  {}, sd)[0].json;
assert("/undo 3 -> pending stored", sd.bot_pending && sd.bot_pending["12345"] !== null);
assert("/undo captures n=3", sd.bot_pending["12345"].args.n === 3);

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
// 7. Apply Mutation — concrete state transitions
// =============================================================================
console.log("\n[Apply Mutation]");

// /stop -> workflow_status 'stopped', owner set, run id preserved
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "stop", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/stop -> workflow_status=stopped", r.workflow_status === "stopped");
assert("/stop -> owner set", r.owner === 12345);
assert("/stop -> current_run_id preserved", r.current_run_id === "run-abc");

// /pause maintenance -> paused=true, reason set
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "pause maintenance", chat_id: 12345, kind: "control", args: { reason: "maintenance" } } }, {})[0].json;
assert("/pause -> paused=true", r.paused === true);
assert("/pause -> reason=maintenance", r.pause_reason === "maintenance");

// /pause off -> paused=false, reason empty
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "pause off", chat_id: 12345, kind: "control", args: { reason: "" } } }, {})[0].json;
assert("/pause off -> paused=false", r.paused === false);
assert("/pause off -> reason empty", r.pause_reason === "");

// /restart -> workflow_status 'starting'
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "restart", chat_id: 12345, kind: "control", args: {} } }, {})[0].json;
assert("/restart -> workflow_status=starting", r.workflow_status === "starting");

// /steer -> steer_note appended to operator_feedback
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "steer keep it simple", chat_id: 12345, kind: "control", args: { prompt: "keep it simple" } } }, {})[0].json;
assert("/steer -> steer_note appended", r.steer_note === "previous note\nkeep it simple");

// /queue add -> queue grows
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "queue add mission-c", chat_id: 12345, kind: "control", args: { op: "add", mission: "mission-c" } } }, {})[0].json;
assert("/queue add -> queue includes mission-c", r.queue && r.queue.includes("mission-c"));
assert("/queue add -> queue length 3", r.queue.length === 3);

// /queue rm 0 -> removes first
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, queue: ["a", "b", "c"] } }]),
  { pending: { action: "queue rm 0", chat_id: 12345, kind: "control", args: { op: "rm", index: 0 } } }, {})[0].json;
assert("/queue rm 0 -> 'a' removed", r.queue.length === 2 && r.queue[0] === "b");

// /queue move 0 2 -> swap first and last
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: { ...LIVE, queue: ["a", "b", "c"] } }]),
  { pending: { action: "queue move 0 2", chat_id: 12345, kind: "control", args: { op: "move", a: 0, b: 2 } } }, {})[0].json;
assert("/queue move 0 2 -> swapped", r.queue[0] === "c" && r.queue[2] === "a");

// /queue clear -> empty
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "queue clear", chat_id: 12345, kind: "control", args: { op: "clear" } } }, {})[0].json;
assert("/queue clear -> empty queue", r.queue.length === 0);

// /queue list -> returns existing queue
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
  { pending: { action: "queue list", chat_id: 12345, kind: "control", args: { op: "list" } } }, {})[0].json;
assert("/queue list -> returns queue", r.queue && r.queue.length === 2);

// /model worker claude-sonnet-5 --provider anthropic -> writes to config row
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "model worker claude-sonnet-5", key: "worker_model", value: "claude-sonnet-5", table: "hermes_config", kind: "edit", args: { role: "worker", model: "claude-sonnet-5", provider: "anthropic" } } }, {})[0].json;
assert("/model -> config row updated", r.table === "hermes_config" && r.key === "worker_model" && r.value === "claude-sonnet-5");

// /undo 2 with edit_history -> rolled_back last 2
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: MISSION_ROW }]),
  { pending: { action: "undo 2", key: "undo", value: "2", table: "missions", kind: "edit", args: { n: 2 }, edit_history: ["e1", "e2", "e3"] } }, {})[0].json;
assert("/undo 2 -> rolled_back last 2 (reversed)", JSON.stringify(r.rolled_back) === JSON.stringify(["e3", "e2"]));

// /set max_tasks 100 (plain edit) -> writes value to config row
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Config Row", [{ json: CONFIG_ROW }]),
  { pending: { action: "set max_tasks = 100", key: "max_tasks", value: "100", table: "hermes_config", kind: "edit" } }, {})[0].json;
assert("/set plain edit -> value written", r.value === "100");

// /mission <id> enable -> concrete state transition in mission table
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: { ...MISSION_ROW, enabled: false } }]),
  { pending: { action: "mission mission-a enable", chat_id: 12345, kind: "edit", key: "enabled", value: "true", type: "boolean", table: "missions", args: { mission_id: "mission-a", enabled: true } } }, {})[0].json;
assert("/mission enable -> mission_id set", r.mission_id === "mission-a");
assert("/mission enable -> enabled=true", r.enabled === true);

// /mission <id> disable -> concrete state transition in mission table
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Mission Row", [{ json: { ...MISSION_ROW, enabled: true } }]),
  { pending: { action: "mission mission-a disable", chat_id: 12345, kind: "edit", key: "enabled", value: "false", type: "boolean", table: "missions", args: { mission_id: "mission-a", enabled: false } } }, {})[0].json;
assert("/mission disable -> enabled=false", r.enabled === false);

// No pending -> returns [] (no output)
r = runNode("Apply Mutation",
  dataOf("Config", [{ json: CFG }], "Load Live State", [{ json: LIVE }]),
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
// 9. E2E chain — Handle Confirm -> Apply Mutation -> Build Audit Row ->
//    audit insert (Send Reply is the telegram node; its payload is the reply
//    object returned by Handle Confirm, recorded below as `reply`).
//    All node code is loaded from AHAWR_Status_Bot.json by node name via
//    runNode — nothing is copied into the harness.
//    The in-memory table stub mirrors the workflow's dataTable nodes:
//      get    -> Load Live State / Load Config Row / Load Mission Row / Audit Recent
//      insert -> Write Audit Log (the workflow's only write node)
//      update -> the workflow has no write node for hermes_config/missions/live
//                state, so Apply Mutation's output is only *recorded*; the
//                table is changed when the bot actually persists it.
// =============================================================================
console.log("\n[E2E Chain]");

function makeTables() {
  const tables = {
    "Autonomous Agent Live State": [{ ...LIVE, state_key: CFG.state_namespace }],
    hermes_config: [{ ...CONFIG_ROW }],
    missions: [{ ...MISSION_ROW }],
    "AHAWR Bot Audit Log": [],
  };
  return {
    tables,
    get(name) { return (tables[name] || []).map(r => ({ json: { ...r } })); },
    live(name) { return (tables[name] || []).map(r => ({ json: r })); },
    value(table, key) { const r = tables[table] && tables[table][0]; return r ? r[key] : undefined; },
    auditRows() { return tables["AHAWR Bot Audit Log"]; },
    insertAudit(row) { tables["AHAWR Bot Audit Log"].push({ ...row }); },
  };
}

// Run the Code-node chain starting at Handle Confirm with one parse item:
//   Handle Confirm -> Apply Mutation -> Build Audit Row -> audit insert.
// (Send Reply is a telegram node, not a Code node; its payload is the
// reply object produced by Handle Confirm, returned here as `reply`.)
function runConfirmChain(sd, parseItem, T) {
  // Rows are passed as live references (not deep copies) so that the
  // mutations Apply Mutation makes to the table rows are visible in T.tables.
  const data = {
    Config: [{ json: JSON.parse(JSON.stringify(CFG)) }],
    "Load Live State": T.live("Autonomous Agent Live State"),
    "Load Config Row": T.live("hermes_config"),
    "Load Mission Row": T.live("missions"),
    "Parse Command": [{ json: parseItem }],
  };
  const hc = runNode("Handle Confirm", data, { json: parseItem }, sd);
  const reply = hc.length ? hc[0].json : null;
  // $json is the node payload itself (the convention used by runNode and
  // the unit tests): Apply Mutation reads $json.pending, Build Audit Row
  // reads $json.audit.
  const amend = hc.length ? runNode("Apply Mutation", data, hc[0].json, sd) : [];
  const rows = amend.length ? runNode("Build Audit Row", data, amend[0].json, sd) : [];
  for (const row of rows) T.insertAudit(row.json);
  return { reply, amend, rows };
}

// Seed a fresh /set pending through Handle Edit (real node code), then
// approve it through the chain. Returns { T, sd, out }.
function setAndApprove(T, key, value, chatId, text) {
  const sd = {};
  const editItem = { chat_id: chatId, text: text, command: text.split(/\s+/)[0], allowed: true };
  runNode("Handle Edit",
    dataOf("Config", [{ json: CFG }], "Parse Command", [{ json: editItem }]),
    { json: editItem }, sd);
  const out = runConfirmChain(sd, { chat_id: chatId, text: "/approve", command: "/approve", allowed: true }, T);
  return { T, sd, out };
}

// --- (a) confirmed /set changes the stub table value -------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = setAndApprove(T, "worker_model", "claude-opus-5", 12345, "/set worker_model claude-opus-5");
  assert("e2e: /approve reply says approved", res.out.reply && res.out.reply.text.includes("approved"));
  assert("e2e: confirmed /set changed hermes_config.worker_model", T.value("hermes_config", "worker_model") === "claude-opus-5");
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

// --- (c) /undo restores before ----------------------------------------
{
  const T = makeTables();
  T.tables.hermes_config[0].worker_model = "claude-opus-5"; // value set by the edit being undone
  const sd = {
    bot_pending: { "12345": { action: "undo 1", key: "worker_model", value: "claude-sonnet-5", table: "hermes_config", chat_id: 12345, role: "admin", code: "CONF-12345-ABCD12", created_at: Date.now(), ttl_ms: 120000, kind: "edit", args: { n: 1 }, edit_history: ["set worker_model = claude-opus-5"] } },
  };
  const out = runConfirmChain(sd, { chat_id: 12345, text: "/approve", command: "/approve", allowed: true }, T);
  assert("e2e: /undo restored before value", T.value("hermes_config", "worker_model") === "claude-sonnet-5");
  const last = T.auditRows().pop();
  assert("e2e: /undo audit row holds before and after",
    last && last.command === "undo 1" && last.before === "claude-opus-5" && last.after === "claude-sonnet-5" && last.result === "approved");
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
  const out = runConfirmChain(sd, parseItem, T);
  return { T, sd, out, code };
}

// --- (d) wrong code -----------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 12345, text: "CONF-12345-ZZZZ99", command: "conf-12345-zzzz99", allowed: true });
  assert("e2e: wrong code -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: wrong code -> reply not approved", res.out.reply && !res.out.reply.text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: wrong code -> failed/rejected audit row", last && (last.result === "failed" || last.result === "rejected"));
}

// --- (e) foreign chat ---------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 67890, text: "", command: "callback_query", callback_data: "confirm", allowed: true });
  assert("e2e: foreign chat -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: foreign chat -> reply not approved", res.out.reply && !res.out.reply.text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: foreign chat -> failed/rejected audit row", last && (last.result === "failed" || last.result === "rejected"));
}

// --- (f) viewer role -----------------------------------------------------
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const res = confirmCase(T, { chat_id: 77777, text: "", command: "callback_query", callback_data: "confirm", allowed: false });
  assert("e2e: viewer role -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: viewer role -> reply not approved", res.out.reply && !res.out.reply.text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: viewer role -> failed/rejected audit row", last && (last.result === "failed" || last.result === "rejected"));
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
  const code = sd.bot_pending["12345"].code;
  const out = runConfirmChain(sd, { chat_id: 12345, text: code, command: "confirm", allowed: true }, T);
  assert("e2e: expired code -> value unchanged", T.value("hermes_config", "worker_model") === before);
  const last = T.auditRows().pop();
  assert("e2e: expired code -> failed/rejected audit row", last && (last.result === "failed" || last.result === "rejected"));
}

// --- (g2) unknown code, chat with no pending -> no write, rejected audit ---
{
  const T = makeTables();
  const before = T.value("hermes_config", "worker_model");
  const sd = {}; // no pending stored anywhere
  const out = runConfirmChain(sd, { chat_id: 12345, text: "/approve CONF-99999-UNKNOWN", command: "/approve", allowed: true }, T);
  assert("e2e: unknown code, no pending -> value unchanged", T.value("hermes_config", "worker_model") === before);
  assert("e2e: unknown code, no pending -> reply rejected", out.reply && out.reply.text.includes("Rejected") && !out.reply.text.includes("approved"));
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
  const res = runConfirmChain(sd, { chat_id: 12345, text: code, command: "confirm", allowed: true }, T);
  const afterFirst = T.value("hermes_config", "worker_model");
  // second use of the same one-time code, same chat
  const second = runConfirmChain(sd, { chat_id: 12345, text: code, command: "confirm", allowed: true }, T);
  assert("e2e: code reuse -> value unchanged on second use", T.value("hermes_config", "worker_model") === afterFirst);
  assert("e2e: code reuse -> second reply not approved", second.reply && !second.reply.text.includes("approved"));
  const last = T.auditRows().pop();
  assert("e2e: code reuse -> failed/rejected audit row on second use", last && (last.result === "failed" || last.result === "rejected"));
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
  const res = runConfirmChain(sd, { chat_id: 12345, text: "", command: "callback_query", callback_data: code, allowed: true }, T);
  assert("e2e: callback carrying code -> accepted (approved)", res.reply && res.reply.text.includes("approved"));
  assert("e2e: callback carrying code -> value written", T.value("hermes_config", "worker_model") === "claude-opus-5");
  const last = T.auditRows().pop();
  assert("e2e: callback carrying code -> approved audit row", last && last.result === "approved" && last.command === "set worker_model = claude-opus-5");
}

// =============================================================================
// Summary
// =============================================================================
console.log("\n=========================================");
console.log("TOTAL: " + passed + " passed, " + failed + " failed");
console.log("=========================================");
process.exit(failed > 0 ? 1 : 0);
