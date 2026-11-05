// Node test: which exports of board_mission.js STAY, pinned on the code as it
// is today (T011, step 1 of the unused-export cleanup of the mission view).
// Run: node --test tests/board_mission_exports_stay.test.js
//
// Pins:
//   1. every name the shell, board_mission_save.js and the other node tests
//    read off BoardMissionView is still exported and callable: renderMission,
//    formHtml, fieldRow, render, BoardMission, MIDRUN_CONFIG_FIELDS,
//    NON_EDITABLE_CONFIG_FIELDS, esc, taskModel, depsMap, highlightSet,
//    dependencyRefusal, warningFor, taskRowHtml, renderTaskTable, statsText,
//    lessonsStatsHtml;
//   2. the behaviour the audit proves is never read from outside the module
//    survives through the staying API: this file never names the seven internal
//    helpers marked unused by the audit, so deleting their entries in the export
//    block cannot break it; deleting a name listed in (1), or the internal
//    helper behind a behaviour pinned here, can.

"use strict";
const path = require("path");
require(path.join(__dirname, "i18n_stub.js"));
const BM = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_mission.js"));
const BO = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_order.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- shared data: one mission row, one queue entry, one config ---
const mission = {
  mission_id: "m1", name: "Mission A", objective: "the objective",
  working_directory: "/tmp/x", enabled: true, rules: ["rule a"],
  acceptance_criteria: ["criterion 1"], retrieval_corpora: ["corpus-a"],
};
const queue = [{ position: 0, mission_id: "m1", enabled: true, attempts_per_task: 5 }];
const config = {
  worker_model: "claude-opus-4-8", max_tasks: 10,
  worker_think_policy: { variants: [{ name: "a", weight: 2 }] },
  worker_think_policy_from_task: 0,
  retrieval_corpora: ["corpus-a", "corpus-b"],
  hints: ["hint 1", "hint 2"],
  required_files: [{ path: "/tmp/x/data.csv", text: "the data file" }],
};
const runningData = {
  mission, queue, config,
  live_state: { status: "active", task_index: 1 },
  groups: { running: ["m1"] },
};
const idleData = { mission, queue, config, live_state: null, groups: {} };

// --- the plan of four tasks: T2 depends on T1, T3 on T2, T4 on nothing ---
const plan = {
  version: "v1",
  tasks: [
    { id: "T1", dependencies: [] },
    { id: "T2", dependencies: ["T1"] },
    { id: "T3", dependencies: ["T2"] },
    { id: "T4", dependencies: [] },
  ],
  tasks_rows: null,
};
const taskRows = [
  { task_id: "T1", title: "first", status: "waiting" },
  { task_id: "T2", title: "second", status: "waiting" },
  { task_id: "T3", title: "third", status: "accepted" },
  { task_id: "T4", title: "fourth", status: "waiting" },
];

// --- DOM stubs: #boardmission holds the preset buttons of the rendered form,
// #boardtasks the task table; both record listener registrations. ---
const attemptsInput = { value: "" };
let presetEls = [];
function buildPresets(html) {
  return (html.match(/<button type="button" class="frow-preset"[^>]*>/g) || []).map((tag) => {
    const el = {
      dataset: { preset: (tag.match(/data-preset="(\d+)"/) || [])[1] },
      disabled: / disabled/.test(tag),
      handlers: {},
      addEventListener: (ev, fn) => { el.handlers[ev] = fn; },
    };
    return el;
  });
}
const missionBox = {
  _html: "",
  get innerHTML() { return missionBox._html; },
  set innerHTML(v) {
    missionBox._html = v;
    presetEls = buildPresets(v);
  },
  querySelectorAll: (sel) => (sel === ".frow-preset" ? presetEls : []),
  querySelector: (sel) => (sel === 'input[name="attempts_per_task"]' ? attemptsInput : null),
};
let stubRows = [];
const tasksBox = {
  _html: "",
  get innerHTML() { return tasksBox._html; },
  set innerHTML(v) {
    tasksBox._html = v;
    // one stub row per task row, carrying its dataset.taskId like the real DOM;
    // classList.toggle("hl") records the highlight state in the html string.
    stubRows = (v.match(/<tr class="trow [^>]*>/g) || []).map((tag) => {
      const id = (tag.match(/data-task-id="([^"]+)"/) || [])[1];
      // the attribute tail of the row tag, for classList.toggle("hl") checks
      let cls = tag.slice(0, -1);
      return {
        dataset: { taskId: id },
        get className() { return cls; },
        classList: { toggle: (name, on) => { cls = on ? `${cls} ${name}` : cls.replace(` ${name}`, ""); } },
      };
    });
  },
  handlers: {},
  addEventListener: (ev, fn) => { tasksBox.handlers[ev] = fn; },
  querySelectorAll: (sel) => (sel === 'tr[data-task-id]' ? stubRows : []),
};
const doc = {
  getElementById: (id) => {
    if (id === "boardmission") return missionBox;
    if (id === "boardtasks") return tasksBox;
    if (id === "boardstate") return { textContent: "", className: "" };
    if (id === "board") return { appendChild: () => {} };
    return null;
  },
  createElement: () => ({ id: "", addEventListener: () => {}, appendChild: () => {} }),
};

async function fetchStub(url) {
  const u = String(url);
  if (u === "/api/board/missions/m1") {
    return { ok: true, json: async () => ({ mission, live_state: null, config, defaults: {} }) };
  }
  if (u === "/api/board/queue") {
    return { ok: true, json: async () => ({ queue, version: "v1" }) };
  }
  if (u === "/api/board/missions/m1/plan") {
    // loadTasks reads the plan response as {plan, tasks}: the task rows come
    // from the server's own shape, not from the fixture object above.
    return { ok: true, json: async () => ({ plan, tasks: taskRows }) };
  }
  if (/\/tasks\/T\d\/stats$/.test(u)) {
    const id = u.match(/\/tasks\/(T\d)\/stats$/)[1];
    return { ok: true, json: async () => ({ attempts: 2, max_attempts_per_task: 5, input_tokens: 1500, output_tokens: 500, cost_usd: 0.5 }) };
  }
  return { ok: true, json: async () => ({}) };
}

(async () => {
  // --- 1. the staying names are still exported and callable ---
  const staying = [
    "renderMission", "formHtml", "fieldRow", "render", "BoardMission",
    "MIDRUN_CONFIG_FIELDS", "NON_EDITABLE_CONFIG_FIELDS", "esc",
    "taskModel", "depsMap", "highlightSet", "dependencyRefusal", "warningFor",
    "taskRowHtml", "renderTaskTable", "statsText", "lessonsStatsHtml",
  ];
  for (const name of staying) {
    check(`BoardMissionView.${name} is still exported`, BM[name] !== undefined);
  }
  for (const name of ["renderMission", "formHtml", "fieldRow", "render", "BoardMission",
    "taskModel", "depsMap", "highlightSet", "dependencyRefusal", "warningFor",
    "taskRowHtml", "renderTaskTable", "statsText", "lessonsStatsHtml"]) {
    check(`BoardMissionView.${name} is still a function`, typeof BM[name] === "function");
  }

  // --- 2. a basic call of each staying export ---
  check("esc escapes hostile markup", BM.esc("<img src=x onerror=alert(1)>")
    === "&lt;img src=x onerror=alert(1)&gt;");
  check("MIDRUN_CONFIG_FIELDS still lists the mid-run config keys",
    Array.isArray(BM.MIDRUN_CONFIG_FIELDS) && BM.MIDRUN_CONFIG_FIELDS.length > 0 &&
    BM.MIDRUN_CONFIG_FIELDS.includes("worker_model"));
  check("NON_EDITABLE_CONFIG_FIELDS still holds the locked fields with their note keys",
    BM.NON_EDITABLE_CONFIG_FIELDS.working_directory === "form.locked.working_directory" &&
    BM.NON_EDITABLE_CONFIG_FIELDS.retrieval_corpora === "form.locked.retrieval_corpora");

  const form = BM.formHtml(mission, queue, config, false, {});
  check("formHtml renders the configuration box", has(form, 'id="confbox"'));
  check("fieldRow renders one labelled input carrying kind and key",
    has(BM.fieldRow("config", "worker_model", "worker model", "text", "claude-opus-4-8", false, null, null),
      'data-key="worker_model"') &&
    has(BM.fieldRow("config", "worker_model", "worker model", "text", "claude-opus-4-8", true, "locked while running", null),
      "disabled"));

  const tasks = BM.taskModel({ plan, tasks: taskRows });
  check("taskModel marks a waiting task as g and an accepted one otherwise",
    tasks.length === 4 && tasks[0].st === "g" && tasks[2].status === "accepted");
  check("depsMap keeps the plan dependencies", BM.depsMap(tasks).T3.join() === "T2");
  check("highlightSet covers the task and its neighbours",
    BM.highlightSet(tasks, "T2").T1 && BM.highlightSet(tasks, "T2").T3);
  check("dependencyRefusal refuses a cycle and allows an acyclic edge",
    !!BM.dependencyRefusal(tasks, "T1", "T2") && BM.dependencyRefusal(tasks, "T4", "T1") === null);
  const refused = BO.tryMove(tasks, "T1", 1);
  check("warningFor explains a refused move and offers the group",
    refused.ok === false && !!BM.warningFor(tasks, "T1", refused).text);
  check("taskRowHtml escapes the title and marks a locked row",
    has(BM.taskRowHtml(tasks[2], tasks, null, null), "locked") &&
    !has(BM.taskRowHtml({ id: "T9", title: "<img>", status: "waiting", st: "g", deps: [] }, tasks, null, null), "<img"));
  check("statsText formats attempts, tokens and cost",
    BM.statsText(null) === "—" &&
    has(BM.statsText({ attempts: 2, max_attempts_per_task: 5, input_tokens: 1500, output_tokens: 500, cost_usd: 0.5 }), "attempts 2/5"));
  const table = BM.renderTaskTable({ tasks, stats: {}, highlight: null, warning: null, notice: null, missionId: "m1" });
  check("renderTaskTable renders the header and one row per task",
    has(table, "<thead>") && (table.match(/<tr class="trow /g) || []).length === 4);
  check("lessonsStatsHtml renders the six lesson figures",
    (BM.lessonsStatsHtml({ with_lessons: { tasks: 3 }, attempts_per_task: { before_first_lesson: 2 } })
      .match(/<span>/g) || []).length === 6);

  // --- 3. the behaviours of the internal helpers, reached only through the
  //        staying API: nothing here names the helpers marked unused by the
  //        audit, so their export entries can be deleted without touching
  //        this file. ---
  const runningHtml = BM.renderMission(runningData);

  // the internal form-row table: every field of the form still has its own row.
  const formKeys = [...runningHtml.matchAll(/data-key="([^"]+)"/g)].map((m) => m[1]);
  check("the form still renders one row per configured field",
    formKeys.length === 19 && new Set(formKeys).size === 19);

  // the listarea formatter: listarea values render one item per line.
  const rulesField = runningHtml.match(/data-key="rules"[^>]*>([\s\S]*?)<\/textarea>/)[1];
  const hintsField = runningHtml.match(/data-key="hints"[^>]*>([\s\S]*?)<\/textarea>/)[1];
  const filesField = runningHtml.match(/data-key="required_files"[^>]*>([\s\S]*?)<\/textarea>/)[1];
  check("rules render one item per line", rulesField === "rule a");
  check("hints render one item per line", hintsField === "hint 1\nhint 2");
  check("an object item of a listarea renders its text field, not [object Object]",
    filesField === "the data file" && !has(filesField, "[object Object]"));

  // the json formatter: the thinking policy renders as pretty JSON, never String(obj).
  const policyField = runningHtml.match(/data-key="worker_think_policy"[^>]*>([\s\S]*?)<\/textarea>/)[1];
  check("the json control renders pretty JSON", has(policyField, "&quot;variants&quot;: [") &&
    !has(policyField, "[object Object]"));

  // the lock-note helper: only the locked fields get a note and stay editable.
  check("a running mission greys working_directory with its note",
    /frow-wrap disabled"><label class="frow-label">working folder<\/label><input[^>]*data-key="working_directory"[^>]*disabled/.test(runningHtml) &&
    has(runningHtml, "bound to the live Worker session"));
  check("worker_model stays editable while running",
    /<input[^>]*data-key="worker_model"[^>]*><\/div>/.test(runningHtml) &&
    !/<input[^>]*data-key="worker_model"[^>]*disabled/.test(runningHtml));

  // the attempts row carries the three presets, disabled while the mission
  // is running.
  check("the attempts row renders the 3/5/10 presets",
    has(runningHtml, 'data-preset="3"') && has(runningHtml, 'data-preset="5"') &&
    has(runningHtml, 'data-preset="10"'));
  check("the presets are disabled while running",
    buildPresets(runningHtml).every((b) => b.disabled));
  check("the presets are enabled for an idle mission",
    buildPresets(BM.renderMission(idleData)).every((b) => !b.disabled));

  // BoardMission, the shell's staying entry point, wires the preset buttons
  // and the task-view handlers when it writes #boardmission, #boardstate and
  // #boardtasks.
  const prevFetch = globalThis.fetch;
  globalThis.fetch = fetchStub;
  globalThis.document = doc;
  await BM.BoardMission("m1");
  globalThis.fetch = prevFetch;
  check("BoardMission attaches the preset buttons once each",
    presetEls.length === 3 && presetEls.every((b) => b.dataset.attached === "1"));
  presetEls[1].handlers.click();
  check("a preset click sets the attempts input to its dataset value",
    attemptsInput.value === "5");
  check("BoardMission renders the task table into #boardtasks",
    has(tasksBox.innerHTML, "<table class=\"ttable\">") &&
    (tasksBox.innerHTML.match(/<tr class="trow /g) || []).length === 4);
  const chipClick = tasksBox.handlers.click;
  check("the tasks container is bound for the chip and drag handlers",
    !!chipClick && !!tasksBox.handlers.drop && !!tasksBox.handlers.dragstart);
  chipClick({ target: { closest: (sel) => (sel === ".chip" ? { dataset: { owner: "T2" } } : null) } });
  check("a chip click highlights the task and its neighbours without redrawing the table",
    stubRows.length === 4 && stubRows[1].className.includes("hl") &&
    stubRows[0].className.includes("hl") && stubRows[2].className.includes("hl") &&
    !stubRows[3].className.includes("hl"));

  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
