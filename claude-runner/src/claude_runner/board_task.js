/* board-scope-wrapped */
(function () {
/* Board task view (part A, T008): the single task of a mission.
 *
 * Loaded by board.html via <script src="/static/board_task.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardTask(missionId, taskId) is the render function the shell (board.js)
 * calls for the #board/<mission_id>/<task_id> fragment: it loads
 * GET /api/board/missions/{id}/plan and
 * GET /api/board/missions/{id}/tasks/{key}/stats and renders #boardtask —
 * the task header (id, title, objective, status), the attempts with the
 * Reviewer score and reason, and the runs of each attempt.  Every field
 * shows "—" until the task's statistics arrive.
 *
 * Every value taken from server data flows through esc() (board_logic.js's
 * esc, with a local fallback when that script failed to load); the container
 * is filled through innerHTML with the esc-wrapped template output.
 */
"use strict";

// translated text; the English text is the fallback (node tests, a table without the key)
function L(key, en, params) {
  let v = en;
  try {
    if (typeof t === "function") { const r = t(key); if (r && r !== key) v = r; }
  } catch (e) { /* keep the English text */ }
  if (params) for (const k of Object.keys(params)) v = v.split("{" + k + "}").join(String(params[k]));
  return v;
}


// esc() from board_logic.js; a local fallback keeps the view safe when that
// script has not loaded.
function esc(v) {
  if (typeof BoardLogic !== "undefined" && typeof BoardLogic.esc === "function") {
    return BoardLogic.esc(v);
  }
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// One run id: the mono text of the id.
function runId(id) {
  return `<span class="tmono">` + esc(id) + `</span>`;
}

// One attempt block: the number, its time, and its runs; a task with no
// statistics shows "—".  The API carries no per-attempt Reviewer score or
// reason (task_stats.json's attempts_detail has only attempt/seconds/runs,
// and TaskView holds the task-level review_score/review_reason), so those
// stay in the meta line above.
function attemptHtml(detail) {
  const runs = detail && Array.isArray(detail.runs) ? detail.runs : [];
  const number = detail ? (Number(detail.attempt) || "?") : "?";
  const time = detail && detail.seconds != null
    ? L("s.min", "{n} min", { n: (Math.round(Number(detail.seconds)) / 60).toFixed(1) }) : "—";
  return (
    `<div class="tattempt">` +
    `<span class="tatt">` + esc(L("tk.attempt", "attempt")) + ` ` + esc(number) + `</span> ` +
    `<span class="tatt-time">` + esc(time) + `</span> · ` +
    `<span class="tatt-runs">` + (runs.length ? runs.map(runId).join(", ") : esc("—")) +
    `</span></div>`
  );
}

// The loading line, shown until the plan fetch has returned.
function loadingHtml() {
  return `<div class="tloading">` + esc(L("tk.loading", "Loading…")) + `</div>`;
}

// The lessons given to this task: BoardLessonsUI.givenHtml when the module is
// loaded, a plain list otherwise (the lesson texts are still esc()-wrapped).
function givenLessonsHtml(lessons) {
  const list = Array.isArray(lessons) ? lessons : [];
  if (typeof BoardLessonsUI !== "undefined" && typeof BoardLessonsUI.givenHtml === "function") {
    const ui = { L: (key, en, params) => L(key, en, params) };
    return BoardLessonsUI.givenHtml(list, ui);
  }
  const items = list.map((l) => `<li>` + esc(l.situation) + ": " + esc(l.lesson) + `</li>`).join("");
  return `<h4>` + esc(L("lessons.given.title", "Lessons given to this task")) + `</h4>` +
    (items ? `<ul>` + items + `</ul>`
      : `<p>` + esc(L("lessons.given.empty", "No lessons were given to this task.")) + `</p>`);
}

// Pure render: the task header (id, title, objective, status), then the
// attempts with the Reviewer score and reason, then the runs of each
// attempt, then the lessons given to the task.  view is the task's TaskView
// (the plan's tasks entry) and stats its statistics object (null while it has
// not arrived yet); every figure then renders "—".  Returns the escaped HTML
// string of the whole #boardtask content; a task not in the plan renders the
// empty state.
function renderTask(missionId, taskId, view, stats, lessons) {
  const t = view || {};
  const title = t.title == null ? "" : t.title;
  const objective = t.objective == null ? "" : t.objective;
  const status = t.status || "waiting";
  if (view == null || t.task_id == null) {
    return `<div class="tempty">` + esc(L("tk.missing", "Task {id} is not in the plan.", { id: taskId })) + `</div>`;
  }
  const attempts = stats ? (Array.isArray(stats.attempts_detail) ? stats.attempts_detail : []) : [];
  const used = stats ? (Number(stats.attempts) || 0) : null;
  const max = stats ? (stats.max_attempts_per_task != null ? Number(stats.max_attempts_per_task) : null) : null;
  const attemptsUsed = used != null && max != null ? esc(used) + "/" + esc(max) : esc("—");
  const score = view && view.review_score != null ? esc(Number(view.review_score).toFixed(1)) : esc("—");
  const reason = view && view.review_reason != null ? esc(view.review_reason) : esc("—");

  return (
    `<div class="crumbs"><a class="back" href="#board/` + esc(missionId) + `">` + esc(L("tk.back", "← back to mission")) + `</a></div>` +
    `<h2>` + esc(title) + `</h2>` +
    `<div class="meta">` +
    `<span class="tid">id: ` + esc(t.task_id) + `</span>` +
    `<span>` + esc(L("tk.status", "status")) + `: ` + esc(L("tstat." + status, status)) + `</span>` +
    `<span>` + esc(L("tk.attempts", "attempts")) + `: ` + attemptsUsed + `</span>` +
    `<span>` + esc(L("tk.score", "reviewer score")) + `: ` + score + `</span>` +
    `<span>` + esc(L("tk.reason", "reviewer reason")) + `: ` + reason + `</span>` +
    `</div>` +
    (objective ? `<div class="tobj">` + esc(objective) + `</div>` : "") +
    `<h3>` + esc(L("tk.attempts.h", "Attempts")) + `</h3>` +
    (attempts.length
      ? attempts.map((d) => attemptHtml(d)).join("")
      : `<div class="tempty">` + esc(L("tk.none", "No attempts yet.")) + `</div>`) +
    givenLessonsHtml(lessons)
  );
}

// Resolve the container: an element or a string id (default "boardtask");
// create it inside #board when missing.
function resolveInto(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardtask");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardtask";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
}

// Render into #boardtask, creating the container inside #board when the
// page has no #boardtask yet; a successful render clears the shell's
// #boardstate line (the shell sets it back on later errors).
function render(container, missionId, taskId, view, stats, lessons) {
  resolveInto(container).innerHTML = renderTask(missionId, taskId, view, stats, lessons);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
}

// Error state: put the escaped message in a .terror div in the container and
// flag the shell's #boardstate line.
function renderError(container, message) {
  resolveInto(container).innerHTML =
    `<div class="terror">${esc(message)}</div>`;
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = message;
    state.className = "error";
  }
}

// BoardTask(missionId, taskId): load the plan and the task's statistics,
// render #boardtask.  Called by the shell's route() for the
// #board/<mission_id>/<task_id> fragment.
async function renderBoardTask(container, missionId, taskId, lessons) {
  const planPath = "/api/board/missions/" + encodeURIComponent(missionId) + "/plan";
  const statsPath = "/api/board/missions/" + encodeURIComponent(missionId) +
    "/tasks/" + encodeURIComponent(taskId) + "/stats";
  resolveInto(container).innerHTML = loadingHtml();
  let planData;
  try {
    planData = await getJSON(planPath);
  } catch (e) {
    renderError(container, e && e.message ? e.message : e);
    return;
  }
  const tasks = Array.isArray(planData.tasks) ? planData.tasks : [];
  const view = tasks.find((t) => t.task_id === taskId) || null;
  let stats = null;
  try {
    stats = await getJSON(statsPath);
  } catch (e) {
    stats = null; // a failed stats load leaves the figures at dashes
  }
  // The lessons given to this task: the caller's list, else the per-task route;
  // a failed load leaves the empty state.
  let given = lessons;
  if (given === undefined) {
    try {
      given = (await getJSON("/api/board/missions/" + encodeURIComponent(missionId) +
        "/tasks/" + encodeURIComponent(taskId) + "/lessons")).lessons;
    } catch (e) {
      given = [];
    }
  }
  render(container, missionId, taskId, view, stats, given);
}

// BoardTask(missionId, taskId): the entry point the shell's route() calls
// for the #board/<mission_id>/<task_id> fragment (board.js line 66); with
// no container the view falls back to #boardtask, created inside #board.
function BoardTask(missionId, taskId, lessons) {
  return renderBoardTask(null, missionId, taskId, lessons);
}

// === export (browser global + CommonJS module.exports) ===
const BoardTaskView = {
  BoardTask, renderBoardTask, renderTask, render, renderError, givenLessonsHtml, esc,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardTaskView;
} else {
  globalThis.BoardTask = BoardTask;
  globalThis.BoardTaskView = BoardTaskView;
}
})();
