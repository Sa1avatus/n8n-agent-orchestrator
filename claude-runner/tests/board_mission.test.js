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
require(path.join(__dirname, "i18n_stub.js"));
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
check("working_directory note", has(html, "bound to the live Worker session"));
check("retrieval_corpora note", has(html, "the retrieval corpora go into the live run query"));
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
check("running note", has(html, "changes apply from the next task"));

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
check("breadcrumb link", has(html, '<a class="back" href="#board">Missions</a>'));
check("breadcrumb name", has(html, '<span class="crumb-name">Миссия &quot;A&quot; &amp; B</span>'));
check("meta line has id", has(html, "id: m1"));
check("meta line has state", has(html, "status: active"));
check("meta line has task number", has(html, "task 2"));
check("meta line has folder", has(html, "folder: /tmp/x"));

// --- 8. task table: chips, drag rules, dependency select, runs link ---
const planT = {
  plan: { tasks: [
    { id: "T1", dependencies: [] },
    { id: "T2", dependencies: ["T1"] },
    { id: "T3", dependencies: ["T2"] },
    { id: "T4", dependencies: [] },
  ] },
  tasks: [
    { task_id: "T1", title: "a", status: "waiting" },
    { task_id: "T2", title: "b", status: "waiting" },
    { task_id: "T3", title: "c", status: "accepted" },
    { task_id: "T4", title: "d", status: "waiting" },
  ],
};
const T = BM.taskModel(planT);
const tById = (id) => T.find((x) => x.id === id);
const DEPS = BM.depsMap(T);
check("taskModel marks waiting as g and others locked",
  tById("T1").st === "g" && tById("T2").st === "g" && tById("T3").st === "accepted");
check("taskModel reads dependencies from the plan", tById("T2").deps.join() === "T1");
check("depsMap lists dependencies", DEPS.T3.join() === "T2");
const tableHtml = BM.renderTaskTable({
  tasks: T, stats: { T1: { attempts: 2, max_attempts_per_task: 5, input_tokens: 1500, output_tokens: 500, cost_usd: 0.5 } },
  highlight: null, warning: null, notice: null, missionId: "m1",
});
check("columns: depends on, needed by, statistics, add dependency",
  has(tableHtml, "Depends on") && has(tableHtml, "Needed by") &&
  has(tableHtml, "Statistics") && has(tableHtml, "+ dependency"));
check("depends-on chip of T2 is T1", has(tableHtml, 'data-owner="T2" data-chip="T1"'));
check("needed-by chip of T1 is T2", has(tableHtml, 'data-owner="T1" data-chip="T2"'));
check("statistics text from stats", has(tableHtml, "attempts 2/5") && has(tableHtml, "2.0K tok") && has(tableHtml, "$0.50"));
check("statistics dash without stats", BM.statsText(null) === "—");
check("runs link uses the mission hash", has(tableHtml, 'href="/#mission=m1"'));
check("waiting rows draggable", has(tableHtml, 'data-task-id="T1" draggable="true"'));
check("locked row not draggable", !has(tableHtml, 'data-task-id="T3" draggable'));
check("select on waiting rows only", has(tableHtml, 'class="dep-select" data-task-id="T1"') &&
  !has(tableHtml, 'class="dep-select" data-task-id="T3"'));
const t2Select = tableHtml.match(/<select class="dep-select" data-task-id="T2">[\s\S]*?<\/select>/)[0];
check("select lists other tasks and skips the task itself and its dependencies",
  has(tableHtml, '<option value="T3">T3 c</option>') &&
  !has(t2Select, '<option value="T1">') && !has(t2Select, '<option value="T2">'));
const hl = BM.highlightSet(T, "T2");
check("chip highlight covers the task and both neighbours",
  hl.T1 && hl.T2 && hl.T3 && !BM.highlightSet(T, "T3").T1);
check("dependency refusal closes a cycle",
  /Cycle/.test(BM.dependencyRefusal(T, "T1", "T2") || ""));
check("dependency allowed when no cycle", BM.dependencyRefusal(T, "T3", "T1") === null);
const BO = require(path.join(__dirname, "..", "src", "claude_runner", "board_order.js"));
const moveDown = BO.tryMove(T, "T1", 1);
check("moving a dependency below its dependent is refused", moveDown.ok === false && moveDown.reason === "dependency");
const wDown = BM.warningFor(T, "T1", moveDown);
check("warning offers move together with the dependents",
  wDown.group && wDown.group.ids.join() === "T1,T2" && /T2/.test(wDown.text));
const moveUp = BO.tryMove(T, "T2", 0);
check("moving a dependent above its dependency is refused", moveUp.ok === false);
const warnHtml = BM.renderTaskTable({
  tasks: T, stats: {}, highlight: null, warning: wDown, notice: { text: "The mission is running", bad: false }, missionId: "m1",
});
check("warning box has move together and cancel",
  has(warnHtml, 'data-act="group"') && has(warnHtml, 'data-act="cancel"') && has(warnHtml, 'role="alert"'));
check("running notice is shown", has(warnHtml, "The mission is running"));
check("task title is escaped", !has(BM.taskRowHtml(
  { id: "T9", title: "<img src=x>", status: "waiting", st: "g", deps: [] }, [], null, null), "<img"));
// --- 8b. chip click toggles the highlight in place (no redraw) ---
// Drive the real bindTasksView handlers through BoardMission with a fetch stub,
// so the module's tasksView.container is our stub. The stub records listener
// registrations and every classList.toggle call; the click handler must not
// reassign container.innerHTML and must toggle "hl" on the rows it covers.
let chipHtml = tableHtml;
let chipWrites = 0;
const chipStub = {
  get innerHTML() { return chipHtml; },
  set innerHTML(v) { chipWrites += 1; chipHtml = v; },
  _listeners: {},
  _toggles: [],
  addEventListener(type, fn) { chipStub._listeners[type] = fn; },
  querySelectorAll() {
    const rows = chipStub.innerHTML.match(/<tr[^>]*data-task-id="[^"]*"[^>]*>/g) || [];
    return rows.map((r) => {
      const m = r.match(/data-task-id="([^"]+)"/);
      return {
        dataset: { taskId: m ? m[1] : "" },
        classList: {
          toggle(cls, on) { chipStub._toggles.push([m ? m[1] : "", cls, on]); },
        },
      };
    });
  },
};
const boardmissionEl = { innerHTML: "", querySelectorAll: () => [] };
const chipDoc = {
  getElementById(id) {
    if (id === "boardmission") return boardmissionEl;
    if (id === "boardtasks") return chipStub;
    if (id === "board") return { appendChild() {} };
    if (id === "boardstate") return { textContent: "", className: "" };
    return null;
  },
  createElement: () => ({ addEventListener() {} }),
};
const postBodies = [];
const missionFetch = (plan) => async (url, opts) => {
  const u = String(url);
  if (opts && opts.method === "POST") postBodies.push(opts.body);
  if (u === "/api/board/missions/m1")
    return { ok: true, json: async () => ({ mission, live_state: { status: "queued" }, config }) };
  if (u === "/api/board/queue")
    return { ok: true, json: async () => ({ queue, version: "v1" }) };
  if (u === "/api/board/missions/m1/plan")
    return { ok: true, json: async () => plan };
  return { ok: true, json: async () => ({}) };
};
async function withStubs(doc, fetchFn, fn) {
  const prev = { document: globalThis.document, fetch: globalThis.fetch };
  globalThis.document = doc;
  globalThis.fetch = fetchFn;
  try {
    return await fn();
  } finally {
    globalThis.document = prev.document;
    globalThis.fetch = prev.fetch;
  }
}
const countTr = (html) => (html.match(/<tr[^>]*>/g) || []).length;
async function s8b() {
  await withStubs(chipDoc, missionFetch(planT), () => BM.BoardMission("m1"));
  const chipClick = chipStub._listeners.click;
  const clickOwner = (owner) =>
    chipClick({ target: { closest: (sel) => (sel === ".chip" ? { dataset: { owner } } : null) } });
  const writesBefore = chipWrites;
  chipStub._toggles.length = 0;
  clickOwner("T2");
  check("chip click does not reassign container.innerHTML (no table redraw)",
    chipWrites === writesBefore && countTr(chipStub.innerHTML) === countTr(tableHtml));
  check("chip click toggles hl on T1, T2, T3 and not T4",
    chipStub._toggles.length === 4 &&
    [["T1", true], ["T2", true], ["T3", true], ["T4", false]]
      .every((p, i) => chipStub._toggles[i][0] === p[0] && chipStub._toggles[i][1] === "hl" && chipStub._toggles[i][2] === p[1]));
  const hl2 = BM.renderTaskTable({ tasks: T, stats: {}, highlight: "T2", warning: null, notice: null, missionId: "m1" });
  check("redraw with highlight T2 has three hl rows",
    (hl2.match(/<tr class="[^"]*hl[^"]*"/g) || []).length === 3);
  chipStub._toggles.length = 0;
  clickOwner("T3");
  check("chip click T3 toggles hl on T2, T3 and not T1, T4",
    chipStub._toggles.length === 4 &&
    [["T1", false], ["T2", true], ["T3", true], ["T4", false]]
      .every((p, i) => chipStub._toggles[i][0] === p[0] && chipStub._toggles[i][1] === "hl" && chipStub._toggles[i][2] === p[1]));
  chipStub._toggles.length = 0;
  clickOwner("T3");
  check("second click on the same chip clears the highlight (all toggles off)",
    chipStub._toggles.length === 4 && chipStub._toggles.every((c) => c[1] === "hl" && c[2] === false));
}
async function s8c() {
  const C = [{ id: "T1", st: "g", deps: [] }, { id: "T2", st: "g", deps: ["T1"] }, { id: "T3", st: "g", deps: ["T2"] }];
  const r3 = BO.tryMove(C, "T3", 0);
  check("chain T3->0 is refused (dependency)", r3.ok === false && r3.reason === "dependency");
  check("chain group order equals the current order (no-op)",
    r3.group.order.join(",") === C.map((x) => x.id).join(","));
  const w3 = BM.warningFor(C, "T3", r3);
  check("no-op warning offers no group and no button",
    !w3.group && !/Move together/.test(w3.text));

  const D = [{ id: "T4", st: "g", deps: [] }, { id: "T1", st: "g", deps: [] }, { id: "T2", st: "g", deps: ["T1"] }, { id: "T3", st: "g", deps: ["T2"] }];
  const r4 = BO.tryMove(D, "T3", 0);
  check("4-task T3->0 is refused with a real group", r4.ok === false && r4.reason === "dependency" && !!r4.group);
  check("4-task group order differs from the current order",
    r4.group.order.join(",") !== D.map((x) => x.id).join(","));
  const w4 = BM.warningFor(D, "T3", r4);
  check("4-task warning offers the group move", !!w4.group && w4.group.ids.join() === "T3,T2,T1");
  check("the POSTed group order has no dependency violations",
    BO.violations(w4.group.order, { T1: [], T2: ["T1"], T3: ["T2"], T4: [] }).length === 0);

  const byId = (id) => planT.tasks.find((x) => x.task_id === id);
  const allWaiting = {
    plan: { tasks: D.map((x) => planT.plan.tasks.find((p) => p.id === x.id)) },
    tasks: ["T4", "T1", "T2", "T3"].map((id) => Object.assign({}, byId(id), { status: "waiting" })),
  };
  await withStubs(chipDoc, missionFetch(allWaiting), async () => {
    await BM.BoardMission("m1");
    const dragStart = chipStub._listeners.dragstart;
    const drop = chipStub._listeners.drop;
    const groupClick = chipStub._listeners.click;
    dragStart({ target: { closest: (sel) => (sel === "tr.waiting" ? { dataset: { taskId: "T3" } } : null) }, dataTransfer: { setData() {} } });
    await drop({ preventDefault() {}, target: { closest: (sel) => (sel === "tr[data-task-id]" ? { dataset: { taskId: "T4" } } : null) } });
    check("the refused drag opens the warning box",
      has(chipStub.innerHTML, 'data-act="group"') && has(chipStub.innerHTML, 'data-act="cancel"') && has(chipStub.innerHTML, 'role="alert"'));
    postBodies.length = 0;
    groupClick({ target: { closest: (sel) => (sel === "[data-act]" ? { dataset: { act: "group" } } : null) } });
    await new Promise((r) => setTimeout(r, 0));
    const posted = postBodies.map((b) => JSON.parse(b));
    check("the group click POSTs T1,T2,T3,T4 (differs from the current order, no violations)",
      posted.length === 1 && posted[0].order.join(",") === "T1,T2,T3,T4" &&
      posted[0].order.join(",") !== D.map((x) => x.id).join(",") &&
      BO.violations(posted[0].order, { T1: [], T2: ["T1"], T3: ["T2"], T4: [] }).length === 0);
  });
}

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
    const el = { tagName: tag, id: "", innerHTML: "", children: [], addEventListener() {} };
    el.appendChild = (c) => { el.children.push(c); };
    return el;
  },
};
BM.render(running);
check("boardmission created",
  appended.length === 1 && appended[0].id === "boardmission");
check("boardmission content",
  appended[0].innerHTML.indexOf('href="#board"') !== -1 &&
  appended[0].innerHTML.indexOf("Launch configuration") !== -1);
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
const boardmissionStub = { innerHTML: "", querySelectorAll: () => [] };
const s7doc = {
  getElementById(id) {
    if (id === "boardmission") return boardmissionStub;
    if (id === "board") return { appendChild() {} };
    if (id === "boardstate") return { textContent: "", className: "" };
    return null;
  },
  createElement: () => ({ addEventListener() {} }),
};
const s7fetch = async (url) => {
  fetchCalls.push(String(url));
  if (String(url) === "/api/board/missions/m1") {
    return { ok: true, json: async () => detail };
  }
  if (String(url) === "/api/board/queue") {
    return { ok: true, json: async () => ({ queue, version: "v1" }) };
  }
  return { ok: true, json: async () => ({}) };
};

async function s7() {
  await withStubs(s7doc, s7fetch, () => BM.BoardMission("m1"));
  check("BoardMission fetched the mission detail",
    fetchCalls.indexOf("/api/board/missions/m1") !== -1);
  check("BoardMission fetched the queue",
    fetchCalls.indexOf("/api/board/queue") !== -1);
  check("BoardMission rendered the form",
    boardmissionStub.innerHTML.indexOf('href="#board"') !== -1 &&
    boardmissionStub.innerHTML.indexOf("Launch configuration") !== -1);
  check("BoardMission rendered the greyed fields (running)",
    boardmissionStub.innerHTML.indexOf("frow-wrap disabled") !== -1);
  check("BoardMission rendered the breadcrumb name",
    boardmissionStub.innerHTML.indexOf('<span class="crumb-name">Миссия &quot;A&quot; &amp; B</span>') !== -1);
  const M = require("../src/claude_runner/board_mission.js");
  const textRow = M.fieldRow("mission", "name", "название", "text", "AHAWR: x", false, "");
  const numRow = M.fieldRow("queue", "attempts_per_task", "попыток", "number", 5, false, "");
  if (!/<input type="text" class="frow" name="name"/.test(textRow)) throw new Error("text field is not type=text: " + textRow);
  if (!/<input type="number" class="frow" name="attempts_per_task"[^>]* step="1"/.test(numRow)) throw new Error("number field lost type or step: " + numRow);
}

(async () => {
  await s8b();
  await s8c();
  await s7();
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
