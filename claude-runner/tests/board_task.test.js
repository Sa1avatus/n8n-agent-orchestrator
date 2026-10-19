// Node test for board_task.js (the single task view of a mission).
// Run: node claude-runner/tests/board_task.test.js
//
// Proves:
//   1. renderTask renders the attempts: each attempt number, its time in
//      minutes and its run ids;
//   2. the header carries the Reviewer score and reason from the task view;
//   3. a hostile reason (or title) is escaped and never appears as markup;
//   4. renderBoardTask fetches exactly
//      GET /api/board/missions/<mission_id>/plan and
//      GET /api/board/missions/<mission_id>/tasks/<task_id>/stats,
//      with each segment encodeURIComponent-encoded;
//   5. a task not in the plan renders the empty state and a failed plan
//      load renders the error state.

"use strict";
const path = require("path");
const fs = require("fs");
const BT = require(path.join(__dirname, "..", "src", "claude_runner", "board_task.js"));

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
const t1 = tasks[0];
const t2 = tasks[1];

// --- 1. the attempts render with score, reason and runs ---
// renderTask takes an explicit container so no document stub is needed here.
const withStats = BT.renderTask("m_running", "T002", t2, stats);
check("the task header carries id, title, objective and status",
  has(withStats, 'id: T002') &&
  has(withStats, '<h2>Extract the source tables</h2>') &&
  has(withStats, 'Load the source corpora and extract the required tables.') &&
  has(withStats, 'status: not_accepted'));
check("the reviewer score and reason come from the task view",
  has(withStats, "reviewer score: 4.0") &&
  has(withStats, "reviewer reason: table counts do not match the source; fix and resubmit"));
check("the attempts render their number, time and runs",
  has(withStats, 'attempt 1') && has(withStats, '40.5 min') &&
  has(withStats, 'run_aaaa0000</span>, <span class="tmono">run_bbbb0000</span>') &&
  has(withStats, 'attempt 2') && has(withStats, '31.5 min') &&
  has(withStats, 'run_cccc0000</span>, <span class="tmono">run_bbbb0000</span>'));
// a run id with markup in it is escaped too (it flows through esc()).
const statsWithHostileRun = Object.assign({}, stats, {
  attempts_detail: [Object.assign({}, stats.attempts_detail[0], {
    runs: ["<img src=x onerror=alert(1)>"],
  })],
});
const hostileRun = BT.renderTask("m_running", "T002", t2, statsWithHostileRun);
check("a hostile run id is escaped, never markup",
  has(hostileRun, "&lt;img src=x onerror=alert(1)&gt;") &&
  !hostileRun.includes("<img src=x onerror=alert(1)>"));
// without statistics the attempt figure is a dash and no attempt block
// appears (the score and reason come from the plan view, not the stats).
const noStats = BT.renderTask("m_running", "T002", t2, null);
check("without statistics the attempts figure is a dash",
  has(noStats, "attempts: —") &&
  has(noStats, "reviewer score: 4.0") &&
  has(noStats, "reviewer reason: table counts do not match the source; fix and resubmit"));
check("without statistics no attempt block is rendered",
  !has(noStats, 'class="tattempt"') && has(noStats, '<div class="tempty">No attempts yet.</div>'));

// --- 2. a hostile reason is escaped ---
const hostileReason = Object.assign({}, t2, {
  review_reason: "<img src=x onerror=alert(1)>&\"'",
});
const hostileHtml = BT.renderTask("m_running", "T002", hostileReason, stats);
check("a hostile reason is escaped in the meta line",
  has(hostileHtml, "reviewer reason: &lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;"));
check("the hostile reason never appears as raw markup",
  !hostileHtml.includes('<img src=x onerror=alert(1)>'));
const hostileTitle = Object.assign({}, t2, {
  title: "<b onmouseover=x>alert(1)</b>",
});
check("a hostile title is escaped in the header",
  has(BT.renderTask("m_running", "T002", hostileTitle, stats),
    "<h2>&lt;b onmouseover=x&gt;alert(1)&lt;/b&gt;</h2>") &&
  !BT.renderTask("m_running", "T002", hostileTitle, stats).includes("<b onmouseover=x>"));

// --- 3. the exact GET URLs ---
let fetched = [];
globalThis.getJSON = async (url) => {
  fetched.push(String(url));
  if (url === "/api/board/missions/m%20running/plan") {
    return plan;
  }
  if (url === "/api/board/missions/m%20running/tasks/T002/stats") {
    return stats;
  }
  throw new Error("unexpected url: " + url);
};

(async () => {
  const containerStub = { innerHTML: "" };
  // render() and renderError() read #boardstate; a stub keeps node happy.
  globalThis.document = {
    getElementById: () => ({ textContent: "", className: "" }),
    createElement: () => ({ innerHTML: "" }),
  };
  await BT.renderBoardTask(containerStub, "m running", "T002");
  check("exactly two GETs and nothing else", fetched.length === 2);
  check("the plan URL is exactly encoded",
    fetched[0] === "/api/board/missions/m%20running/plan");
  check("the stats URL is exactly encoded",
    fetched[1] === "/api/board/missions/m%20running/tasks/T002/stats");
  check("the plan data was rendered into the container",
    has(containerStub.innerHTML, "Extract the source tables") &&
    has(containerStub.innerHTML, "attempts: 2/5") &&
    has(containerStub.innerHTML,
      'run_aaaa0000</span>, <span class="tmono">run_bbbb0000</span>'));

  // a stats load failure leaves the figures at dashes; the plan still renders.
  const statsFail = { innerHTML: "" };
  globalThis.getJSON = async (url) => {
    if (url === "/api/board/missions/m_running/plan") return plan;
    throw new Error("stats offline");
  };
  await BT.renderBoardTask(statsFail, "m_running", "T002");
  check("a failed stats load keeps the plan but dashes the figures",
    has(statsFail.innerHTML, "Extract the source tables") &&
    has(statsFail.innerHTML, "attempts: —") &&
    has(statsFail.innerHTML, "No attempts yet."));

  // --- 4. the empty state (no attempts) and the error state ---
  const emptyContainer = { innerHTML: "" };
  globalThis.document = {
    getElementById: () => ({ textContent: "", className: "" }),
    createElement: () => ({ innerHTML: "" }),
  };
  await BT.renderBoardTask(emptyContainer, "m_running", "T099");
  check("a task not in the plan renders the empty state",
    has(emptyContainer.innerHTML,
      '<div class="tempty">Task T099 is not in the plan.</div>'));
  check("the empty state has no attempt blocks and no figures",
    !has(emptyContainer.innerHTML, 'class="tattempt"') &&
    !has(emptyContainer.innerHTML, 'class="meta"'));

  const errorContainer = { innerHTML: "" };
  const stateStub = { textContent: "", className: "" };
  globalThis.document = {
    getElementById: (id) => (id === "boardstate" ? stateStub : errorContainer),
    createElement: () => ({ innerHTML: "" }),
  };
  globalThis.getJSON = async () => { throw new Error("board offline"); };
  await BT.renderBoardTask(errorContainer, "m_missing", "T001");
  check("a failed plan load renders the error state",
    has(errorContainer.innerHTML, '<div class="terror">board offline</div>') &&
    stateStub.textContent === "board offline" && stateStub.className === "error");
  delete globalThis.document;
  delete globalThis.getJSON;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
