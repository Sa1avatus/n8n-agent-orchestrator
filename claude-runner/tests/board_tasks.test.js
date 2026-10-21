// Node test for board_tasks.js (the mission's task cards).
// Run: node claude-runner/tests/board_tasks.test.js
//
// Proves:
//   1. the cards render the plan's data: id, title, status per task;
//   2. a hostile title is shown as escaped text, never as markup;
//   3. before the statistics arrive every figure renders "—";
//   4. at most 3 statistics fetches are in flight at once (stubbed
//      getJSON), and the cards are redrawn as the figures arrive;
//   5. each card links to #board/<mission_id>/<task_id>;
//   6. an empty plan renders the empty state and a failed plan load
//      renders the error state;
//   7. the QA chip shows only for a task whose ui_check.needed is true.

"use strict";
const path = require("path");
const fs = require("fs");
const BT = require(path.join(__dirname, "..", "src", "claude_runner", "board_tasks.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- fixtures ---
const plan = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "board_part2", "plan.json"), "utf8")
);
const stats = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "board_part2", "task_stats.json"), "utf8")
);
const tasks = plan.tasks; // T001 accepted, T002 not_accepted, T003 waiting
const containerStub = { innerHTML: "" };

// --- 1. the cards render the plan's data ---
const empty = BT.renderTasks("m_running", tasks, {});
check("three cards, one per task in plan order",
  (empty.match(/<a class="tcard"/g) || []).length === 3);
check("T001 renders id, title and status",
  has(empty, 'data-task-id="T001"') &&
  has(empty, '<span class="tname">Prepare the working folder</span>') &&
  has(empty, '<span class="tstatus">accepted</span>'));
check("T002 renders id, title and status",
  has(empty, 'data-task-id="T002"') &&
  has(empty, '<span class="tname">Extract the source tables</span>') &&
  has(empty, '<span class="tstatus">not_accepted</span>'));
check("T003 renders id and status",
  has(empty, 'data-task-id="T003"') &&
  has(empty, '<span class="tstatus">waiting</span>'));

// --- 2. the hostile title is shown as text, never as markup ---
const hostileTask = Object.assign({}, tasks[1], {
  title: "<img src=x onerror=alert(1)>",
});
const hostileHtml = BT.taskCardHtml("m_running", hostileTask, null);
check("hostile title is escaped in the card text",
  has(hostileHtml, "<img src=x onerror=alert(1)>".replace(/&/g, "&amp;")
    .replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")));
// the title text carries no raw < or > at all: only escaped entities
const tnameText = hostileHtml.slice(
  hostileHtml.indexOf('<span class="tname">') + '<span class="tname">'.length,
  hostileHtml.indexOf("</span>", hostileHtml.indexOf('<span class="tname">')));
check("hostile title never appears as raw markup (no real tag)",
  !tnameText.includes("<") && !tnameText.includes(">") &&
  has(tnameText, "&lt;img src=x onerror=alert(1)&gt;"));

// --- 3. dashes appear before the statistics load ---
const dashCount = (empty.match(/—/g) || []).length;
check("every figure of every card is a dash before stats load", dashCount === 21);
check("attempts is a dash without stats", has(empty, "— attempts"));
check("time, tokens, cache, cost, speed and thinking are dashes without stats",
  has(empty, "— · ") && has(empty, "— tokens") && has(empty, "— cache") &&
  !has(empty, "tok/s") && has(empty, "— thinking"));

// --- 4. at most 3 concurrent stats fetches ---
// renderTasks renders synchronously from the data it is given, so the
// dash-state checks above use it; the async render() is driven by the
// global getJSON (board.js defines it on globalThis) — board_tasks.js
// calls getJSON, never fetch directly.
let inflight = 0;
let maxInflight = 0;
const getJsonLog = [];
globalThis.getJSON = async (url) => {
  const u = String(url);
  inflight += 1;
  if (inflight > maxInflight) maxInflight = inflight;
  getJsonLog.push({ url: u, seq: getJsonLog.length + 1 });
  await Promise.resolve();
  await Promise.resolve();
  inflight -= 1;
  if (u === "/api/board/missions/m_running/plan") {
    return plan;
  }
  if (/^\/api\/board\/missions\/m_running\/tasks\/(\w+)\/stats$/.test(u)) {
    return u.endsWith("/T002/stats") ? stats : null; // T001 and T003 never arrive
  }
  throw new Error("unexpected url: " + u);
};
globalThis.document = {
  getElementById: () => containerStub,
  createElement: () => ({ innerHTML: "" }),
};

(async () => {
  await BT.render(containerStub, "m_running", {});
  check("the plan is fetched", getJsonLog.some((f) => f.url === "/api/board/missions/m_running/plan"));
  check("stats fetches happen for every task with a key",
    getJsonLog.filter((f) => /\/stats$/.test(f.url)).length === 3);
  check("at most 3 concurrent fetches are in flight at once", maxInflight <= 3);
  check("exactly 3 concurrent fetches are reached (pool of 3 is used)",
    maxInflight === 3);
  // the only stats that arrive fill T002; T001 and T003 stay at dashes
  const final = containerStub.innerHTML;
  check("T002 renders the stats figures",
    has(final, "2/5 attempts") && has(final, "1.7 h") && has(final, "244.0K tokens") &&
    has(final, "42% cache") && has(final, "$3.20") && has(final, "63 tok/s") &&
    has(final, "67% thinking"));
  check("T001 keeps its dashes", /data-task-id="T001"[\s\S]*?— attempts[\s\S]*?— thinking/.test(final));
  check("T003 keeps its dashes", /data-task-id="T003"[\s\S]*?— attempts[\s\S]*?— thinking/.test(final));
  delete globalThis.document;
  delete globalThis.getJSON;

  // --- 5. the card link is #board/<m>/<t> ---
  check("every card links to #board/m_running/<task_id>",
    has(final, 'href="#board/m_running/T001"') &&
    has(final, 'href="#board/m_running/T002"') &&
    has(final, 'href="#board/m_running/T003"'));
  check("the data attributes match the ids",
    has(final, 'data-mission-id="m_running"') &&
    has(final, 'data-task-id="T001"') &&
    has(final, 'data-task-id="T002"') &&
    has(final, 'data-task-id="T003"'));

  // --- 6. the empty and error states ---
  const emptyState = BT.renderTasks("m_running", [], {});
  check("an empty plan renders the empty state",
    emptyState.indexOf('<div class="tempty">No tasks in the plan.</div>') !== -1);
  check("a non-array task list also renders the empty state",
    BT.renderTasks("m_running", null, {}).indexOf('<div class="tempty">No tasks in the plan.</div>') !== -1);

  const errorContainer = { innerHTML: "" };
  const stateStub = { textContent: "", className: "" };
  globalThis.document = {
    getElementById: (id) => (id === "boardstate" ? stateStub : errorContainer),
    createElement: () => ({ innerHTML: "" }),
  };
  globalThis.getJSON = async () => { throw new Error("board offline"); };
  await BT.render(errorContainer, "m_missing", {});
  check("a failed plan load renders the error state",
    has(errorContainer.innerHTML, '<div class="terror">board offline</div>') &&
    stateStub.textContent === "board offline" && stateStub.className === "error");
  delete globalThis.document;
  delete globalThis.getJSON;

  // --- 7. the QA chip shows only for ui_check.needed === true ---
  const qaTask = (uiCheck) => Object.assign({}, tasks[0], uiCheck === undefined ? {} : { ui_check: uiCheck });
  const needed = BT.taskCardHtml("m_running", qaTask({ needed: true, why: "w", scenario: "Login page renders", steps: [] }), null);
  check("the chip shows with the scenario as its title when needed is true",
    has(needed, '<span class="tqa" title="Login page renders">QA</span>'));
  const notNeeded = BT.taskCardHtml("m_running", qaTask({ needed: false, why: "", scenario: "", steps: [] }), null);
  const noField = BT.taskCardHtml("m_running", qaTask(undefined), null);
  check("no chip when needed is false or ui_check is missing",
    !has(notNeeded, "tqa") && !has(noField, "tqa"));
  const hostileScenario = BT.taskCardHtml("m_running", qaTask({ needed: true, why: "", scenario: '<b>"x"</b>', steps: [] }), null);
  check("the scenario in the chip title is escaped",
    has(hostileScenario, 'title="&lt;b&gt;&quot;x&quot;&lt;/b&gt;"') && !has(hostileScenario, "<b>"));

  globalThis.document = {
    getElementById: (id) => (id === "boardstate" ? null : containerStub),
    createElement: () => ({ innerHTML: "" }),
  };
  globalThis.getJSON = async (url) => {
    if (/\/plan$/.test(String(url))) {
      return {
        plan: { tasks: [
          { id: "T001", ui_check: { needed: true, why: "w", scenario: "Folder is mounted", steps: [] } },
          { id: "T002", ui_check: { needed: false, why: "", scenario: "", steps: [] } },
        ] },
        version: 1,
        tasks: [{ task_id: "T001", title: "A", status: "waiting" }, { task_id: "T002", title: "B", status: "waiting" }],
      };
    }
    return null;
  };
  await BT.render(containerStub, "m_running", {});
  const chipCards = containerStub.innerHTML.split('<a class="tcard"');
  check("through render, only the needed task gets the chip",
    chipCards.length === 3 && has(chipCards[1], 'title="Folder is mounted"') && !has(chipCards[2], "tqa"));
  delete globalThis.document;
  delete globalThis.getJSON;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
