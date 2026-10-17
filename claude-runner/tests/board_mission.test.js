// Node test for board_mission.js (the mission view and the run
// configuration form).
// Run: node claude-runner/tests/board_mission.test.js
//
// Proves:
//   1. renderMission renders every listed field from the API data (the
//      mission row, the queue entry and the config object);
//   2. a running mission greys exactly the fields that do not apply from
//      the next task (working_directory, retrieval_corpora) with the
//      Russian explanation, and leaves every other field editable;
//   3. every value from data goes through esc() (the dangerous value
//      never appears unescaped in the markup);
//   4. list fields render as listarea textareas with one item per line
//      (rules ['a','b'] -> "a\nb"), and worker_think_policy renders as
//      pretty JSON, never [object Object];
//   5. the breadcrumb is "Миссии / <имя>" and the attempts presets
//      (3/5/10) are rendered, disabled while running;
//   6. render() writes #boardmission and clears #boardstate;
//   7. BoardMission() loads the detail and the queue via fetch and renders.

"use strict";
const path = require("path");
const BM = require(path.join(__dirname, "..", "src", "claude_runner", "board_mission.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- shared data ---
const mission = {
  mission_id: "m1", name: 'Миссия "A" & B', objective: "цель",
  working_directory: "/tmp/x", enabled: true, rules: ["a<b"],
  acceptance_criteria: ["критерий"], retrieval_corpora: ["corpus-a"],
};
const queue = [{ position: 0, mission_id: "m1", enabled: true, attempts_per_task: 5 }];
const config = {
  worker_model: "claude-opus-4-8", worker_provider: "anthropic",
  reviewer_model: "claude-sonnet-4-5", reviewer_provider: "anthropic",
  architect_model: "claude-opus-4-8", architect_provider: "anthropic",
  max_tasks: 10, worker_think_policy: "on", worker_think_policy_from_task: 0,
  retrieval_corpora: ["corpus-a", "corpus-b"],
  hints: ["подсказка 1", "подсказка 2"], required_files: ["/tmp/x/data.csv"],
};
const running = { mission, queue, config,
  live_state: { status: "active", task_index: 1 },
  groups: { running: ["m1"] } };
const idle = { mission, queue, config,
  live_state: { status: "queued", task_index: null }, groups: {} };

// --- 1. every listed field renders, carrying kind/key in the dataset ---
const keys = [
  "name", "objective", "rules", "acceptance_criteria", "working_directory",
  "enabled", "attempts_per_task", "worker_model", "worker_provider",
  "reviewer_model", "reviewer_provider", "architect_model", "architect_provider",
  "max_tasks", "worker_think_policy", "worker_think_policy_from_task",
  "retrieval_corpora", "hints", "required_files",
];
for (const dataset of [BM.renderMission(running), BM.renderMission(idle)]) {
  for (const k of keys) {
    check(`field ${k} present`, has(dataset, `data-key="${k}"`));
  }
}

// values from each source land in the markup
const html = BM.renderMission(running);
check("name from mission row", has(html, 'value="Миссия &quot;A&quot; &amp; B"'));
check("objective from mission row (textarea)", has(html, ">цель</textarea>"));
check("rules listarea from mission row", has(html, ">a&lt;b</textarea>"));
check("acceptance listarea from mission row", has(html, ">критерий</textarea>"));
check("working_directory from mission row", has(html, "value=\"/tmp/x\""));
check("enabled checkbox from mission row",
  has(html, '<input type="checkbox" class="frow" name="enabled"') &&
  has(html, 'data-key="enabled"') &&
  /name="enabled"[\s\S]*?checked/.test(html));
check("attempts_per_task from queue entry", has(html, "value=\"5\""));
check("worker model from config", has(html, "value=\"claude-opus-4-8\""));
check("max_tasks from config", has(html, "value=\"10\""));
check("hints listarea from config", has(html, ">подсказка 1\nподсказка 2</textarea>"));
check("required_files listarea from config", has(html, ">/tmp/x/data.csv</textarea>"));
check("corpora listarea from config", has(html, ">corpus-a\ncorpus-b</textarea>"));
check("think policy from config (json control)", has(html, ">on</textarea>"));
check("think from_task from config", has(html, "value=\"0\""));

// --- 2. a running mission greys exactly the two non-editable fields ---
const disabledCount = (h) => (h.match(/frow-wrap disabled/g) || []).length;
check("running: exactly 2 greyed rows", disabledCount(html) === 2);
check("idle: no greyed rows", disabledCount(BM.renderMission(idle)) === 0);
check("working_directory is greyed while running",
  has(html, 'name="working_directory"') &&
  /frow-wrap disabled[\s\S]*?data-key="working_directory"/.test(html));
check("retrieval_corpora is greyed while running",
  has(html, 'name="retrieval_corpora"') &&
  /frow-wrap disabled[\s\S]*?data-key="retrieval_corpora"/.test(html));
check("working_directory note", has(html, "привязана к живому сеансу Worker"));
check("retrieval_corpora note", has(html, "корпуса поиска уходят в живой запрос"));
check("state_namespace has no form row", !has(html, "state_namespace"));
// per-row: greyed only if the disabled row itself carries that data-key
const greyedKeys = [...html.matchAll(
  /<div class="frow-wrap disabled">[\s\S]*?data-key="([^"]+)"/g)].map((m) => m[1]);
check("greyed rows are exactly working_directory and retrieval_corpora",
  greyedKeys.length === 2 &&
  greyedKeys.includes("working_directory") &&
  greyedKeys.includes("retrieval_corpora"));
check("no other field greyed",
  !["worker_model", "max_tasks", "name", "hints", "attempts_per_task"]
    .some((k) => greyedKeys.includes(k)));
check("greyed inputs are disabled",
  /frow-wrap disabled[\s\S]*?data-key="working_directory"[^>]*disabled/.test(html));
// the running note explains when changes apply
check("running note", has(html, "изменения применяются со следующей задачи"));

// preset buttons for attempts
check("presets present",
  has(html, 'data-preset="3"') && has(html, 'data-preset="5"') &&
  has(html, 'data-preset="10"'));
check("presets disabled while running",
  /frow-preset" data-preset="3" disabled/.test(html));
check("presets enabled while idle",
  has(BM.renderMission(idle), 'data-preset="3"') &&
  !/frow-preset" data-preset="3" disabled/.test(BM.renderMission(idle)));

// --- 3. every value goes through esc() ---
const evilMission = Object.assign({}, mission, {
  name: 'x<script>"&', objective: "<script>alert('x')<\/script>",
  working_directory: '/a"b<c', rules: ['a&b"c'],
  acceptance_criteria: ["<i>crit</i>"],
});
const evilHtml = BM.renderMission({
  mission: evilMission, queue, config,
  live_state: { status: "active" }, groups: { running: [evilMission.mission_id] },
});
check("esc: no raw <script> from data", !has(evilHtml, "x<script>") &&
  !has(evilHtml, "<script>alert"));
check("esc: name is escaped", has(evilHtml, "x&lt;script&gt;&quot;&amp;"));
check("esc: objective is escaped (textarea)",
  has(evilHtml, "&lt;script&gt;alert(&#39;x&#39;)&lt;/script&gt;</textarea>"));
check("esc: working_directory is escaped", has(evilHtml, "/a&quot;b&lt;c"));
check("esc: rules list is escaped", has(evilHtml, "a&amp;b&quot;c"));
check("esc: criteria list is escaped", has(evilHtml, "&lt;i&gt;crit&lt;/i&gt;"));

// --- 4. listarea and json controls ---
// rules of two strings render a listarea textarea with one item per line
const twoRules = Object.assign({}, mission, { rules: ["a", "b"] });
const twoHtml = BM.renderMission({
  mission: twoRules, queue, config,
  live_state: { status: "queued" }, groups: {},
});
check("rules ['a','b'] render a listarea textarea with one item per line",
  has(twoHtml, '<textarea class="frow" name="rules"') &&
  has(twoHtml, ">a\nb</textarea>"));
// an object list item uses its string field (name/text/title) or JSON.stringify
const objList = Object.assign({}, mission, { rules: [{ name: "x" }, { a: 1 }] });
const objListHtml = BM.renderMission({
  mission: objList, queue, config,
  live_state: { status: "queued" }, groups: {},
});
check("object list item uses its name field, fallback JSON.stringify",
  has(objListHtml, ">x\n{&quot;a&quot;:1}</textarea>"));
// an object policy renders pretty JSON, never [object Object]
const objConfig = Object.assign({}, config, {
  worker_think_policy: { variants: [{ name: "off_first" }] },
});
const objHtml = BM.renderMission({
  mission, queue, config: objConfig,
  live_state: { status: "queued" }, groups: {},
});
check("policy object renders pretty JSON, not [object Object]",
  has(objHtml, '<textarea class="frow" name="worker_think_policy"') &&
  has(objHtml, "&quot;variants&quot;") && !has(objHtml, "[object Object]"));
// a string policy stays a raw string in the json textarea
check("string policy stays raw in the json textarea", has(html, ">on</textarea>"));

// --- 5. breadcrumb and summary ---
check("breadcrumb link", has(html, '<a class="back" href="#board">Миссии</a>'));
check("breadcrumb name", has(html, '<span class="crumb-name">Миссия &quot;A&quot; &amp; B</span>'));
check("meta line has id", has(html, "id: m1"));
check("meta line has state", has(html, "состояние: active"));
check("meta line has task number", has(html, "задача 2"));
check("meta line has folder", has(html, "папка: /tmp/x"));

// --- 6. render() writes #boardmission and clears #boardstate ---
const appended = [];
const boardStub = { appendChild(el) { appended.push(el); } };
const stateStub = { textContent: "Loading the board…", className: "bad" };
globalThis.document = {
  getElementById(id) {
    if (id === "boardmission") return null;
    if (id === "board") return boardStub;
    if (id === "boardstate") return stateStub;
    return null;
  },
  createElement(tag) {
    const el = { tagName: tag, id: "", innerHTML: "", children: [] };
    el.appendChild = (c) => { el.children.push(c); };
    return el;
  },
};
BM.render(running);
check("boardmission created",
  appended.length === 1 && appended[0].id === "boardmission");
check("boardmission content",
  appended[0].innerHTML.indexOf('href="#board"') !== -1 &&
  appended[0].innerHTML.indexOf("Конфигурация запуска") !== -1);
check("boardstate cleared", stateStub.textContent === "" && stateStub.className === "");

// a second render reuses the existing #boardmission (no second append)
globalThis.document.getElementById = (id) =>
  (id === "boardmission" ? appended[0] :
    (id === "board" ? boardStub : stateStub));
BM.render(idle);
check("boardmission reused", appended.length === 1);
delete globalThis.document;

// --- 7. the fetch flow of BoardMission (a stub for fetch) ---
// BoardMission loads GET /api/board/missions/{id} and GET /api/board/queue
// and renders the result into #boardmission.
const fetchCalls = [];
const detail = {
  mission,
  live_state: { status: "active", task_index: 1 },
  config,
  task_views: [],
  task_overrides: {},
};
globalThis.fetch = async (url) => {
  fetchCalls.push(String(url));
  if (String(url) === "/api/board/missions/m1") {
    return { ok: true, json: async () => detail };
  }
  if (String(url) === "/api/board/queue") {
    return { ok: true, json: async () => ({ queue, version: "v1" }) };
  }
  return { ok: true, json: async () => ({}) };
};
const boardmissionStub = { innerHTML: "", querySelectorAll: () => [] };
globalThis.document = {
  getElementById(id) {
    if (id === "boardmission") return boardmissionStub;
    if (id === "board") return { appendChild() {} };
    if (id === "boardstate") return { textContent: "", className: "" };
    return null;
  },
  createElement: () => ({}),
};

(async () => {
  await BM.BoardMission("m1");
  check("BoardMission fetched the mission detail",
    fetchCalls.indexOf("/api/board/missions/m1") !== -1);
  check("BoardMission fetched the queue",
    fetchCalls.indexOf("/api/board/queue") !== -1);
  check("BoardMission rendered the form",
    boardmissionStub.innerHTML.indexOf('href="#board"') !== -1 &&
    boardmissionStub.innerHTML.indexOf("Конфигурация запуска") !== -1);
  check("BoardMission rendered the greyed fields (running)",
    boardmissionStub.innerHTML.indexOf("frow-wrap disabled") !== -1);
  check("BoardMission rendered the breadcrumb name",
    boardmissionStub.innerHTML.indexOf('<span class="crumb-name">Миссия &quot;A&quot; &amp; B</span>') !== -1);
  delete globalThis.document;
  // 7. a text field is a text input (not a number input), a number field keeps its step attribute
  {
    const M = require("../src/claude_runner/board_mission.js");
    const textRow = M.fieldRow("mission", "name", "название", "text", "AHAWR: x", false, "");
    const numRow = M.fieldRow("queue", "attempts_per_task", "попыток", "number", 5, false, "");
    if (!/<input type="text" class="frow" name="name"/.test(textRow)) throw new Error("text field is not type=text: " + textRow);
    if (!/<input type="number" class="frow" name="attempts_per_task"[^>]* step="1"/.test(numRow)) throw new Error("number field lost type or step: " + numRow);
  }
  delete globalThis.fetch;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
