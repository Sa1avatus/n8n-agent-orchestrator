/* board-scope-wrapped */
(function () {
/* Board task cards (part A, T006): one card per task of a mission.
 *
 * Loaded by board.html via <script src="/static/board_tasks.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardTasks.render(container, missionId, opts) is the render entry point:
 * it loads GET /api/board/missions/{id}/plan and draws one card per task of
 * the plan — id, title, status, attempts used/max, time, tokens, cache
 * share, thinking share, cost, speed.  Every field shows "—" until the
 * task's own statistics arrive: the figures come from
 * GET /api/board/missions/{id}/tasks/{key}/stats, at most 3 requests in
 * flight (the same worker pool the queue uses), and the cards are redrawn
 * as they arrive.  Each card links to #board/<mission_id>/<task_id>.
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

// Compact token count: 1234567 -> "1.2M".
function fmtTokens(v) {
  const n = Number(v) || 0;
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(Math.round(n));
}

// Total run time (worker hours + reviewer minutes) as "1.3 h" / "25 min".
function fmtTime(workerHours, reviewerMinutes) {
  const minutes = (Number(workerHours) || 0) * 60 + (Number(reviewerMinutes) || 0);
  if (minutes >= 60) return L("hours", "{n} h", { n: (minutes / 60).toFixed(1) });
  return L("s.min", "{n} min", { n: Math.round(minutes) });
}

// A ratio (0..1) as a percent; "—" when there is nothing to count.
function fmtPercent(ratio, parts) {
  const total = Number(parts) || 0;
  if (total <= 0) return "—";
  const r = Number(ratio);
  if (!isFinite(r) || r < 0) return "—";
  return Math.round(r * 100) + "%";
}

// One task card: id, title, status, attempts used/max, time, tokens, cache
// share, cost, speed, thinking share.  stats is the task's own stats object
// or null while it has not arrived yet; every figure then renders "—".
// Returns the escaped HTML string of the card.
function taskCardHtml(missionId, task, stats) {
  const id = task.task_id == null ? "" : task.task_id;
  const status = task.status || "waiting";
  const used = stats ? (Number(stats.attempts) || 0) : null;
  const max = stats ? (stats.max_attempts_per_task != null ? Number(stats.max_attempts_per_task) : null) : null;
  const attempts = used != null && max != null ? esc(used) + "/" + esc(max) : esc("—");
  const time = stats ? fmtTime(stats.worker_hours, stats.reviewer_minutes) : "—";
  const tokens = stats ? fmtTokens((Number(stats.input_tokens) || 0) + (Number(stats.output_tokens) || 0)) : "—";
  const cache = stats ? fmtPercent(stats.cache_ratio, (Number(stats.cache_read_tokens) || 0) + (Number(stats.cache_write_tokens) || 0)) : "—";
  const cost = stats && stats.cost_usd != null ? "$" + Number(stats.cost_usd).toFixed(2) : "—";
  const speed = stats && stats.tok_per_s != null ? L("s.tps", "{n} tok/s", { n: Math.round(Number(stats.tok_per_s)) }) : "—";
  const thinking = stats ? fmtPercent(
    stats.thinking_steps ? stats.thinking_runs / stats.thinking_steps : 0,
    stats.thinking_steps,
  ) : "—";

  return (
    `<a class="tcard" href="#board/${esc(missionId)}/${esc(id)}" ` +
    `data-mission-id="${esc(missionId)}" data-task-id="${esc(id)}">` +
    `<span class="tid">${esc(id)}</span>` +
    `<span class="tname">${esc(task.title == null ? "" : task.title)}</span>` +
    `<span class="tstatus">${esc(L("tstat." + status, status))}</span>` +
    `<div class="tmeta">` +
    `<span class="tstats" title="${esc(L("tc.title", "attempts · time · tokens · cache · cost · speed · thinking"))}">` +
    attempts + " " + L("tc.attempts", "attempts") + " · " + esc(time) + " · " + esc(tokens) + " " + L("tc.tokens", "tokens") + " · " +
    esc(cache) + " " + L("tc.cache", "cache") + " · " + esc(cost) + " · " + esc(speed) + " · " + esc(thinking) +
    " " + L("tc.thinking", "thinking") + `</span>` +
    `</div></a>`
  );
}

// Pure render: one card per task, in plan order.  statsByTask maps a task id
// to its stats object (null while not yet loaded).  Returns the escaped HTML
// string of the whole container content; no tasks renders the empty state.
function renderTasks(missionId, tasks, statsByTask) {
  if (!Array.isArray(tasks) || tasks.length === 0) {
    return `<div class="tempty">No tasks in the plan.</div>`;
  }
  return tasks.map((t) =>
    taskCardHtml(missionId, t, statsByTask[t.task_id] || null)
  ).join("");
}

// Resolve the container: an element or a string id (default "boardtasks");
// create it inside #board when missing.
function resolveContainer(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardtasks");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardtasks";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
}

// Fill the container: render the cards and clear the shell's #boardstate line
// on success.
function render(container, missionId, tasks, statsByTask) {
  resolveContainer(container).innerHTML = renderTasks(missionId, tasks, statsByTask);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
}

// Error state: put the escaped message in a .terror div in the container and
// flag the shell's #boardstate line.
function renderError(container, message) {
  resolveContainer(container).innerHTML =
    `<div class="terror">${esc(message)}</div>`;
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = message;
    state.className = "error";
  }
}

// Load one task's statistics; a failure leaves its card at dashes.
async function loadTaskStats(data, missionId, key) {
  const path = "/api/board/missions/" + encodeURIComponent(missionId) +
    "/tasks/" + encodeURIComponent(key) + "/stats";
  try {
    data.stats[key] = await getJSON(path);
  } catch (e) {
    data.stats[key] = null;
  }
}

// BoardTasks.render(container, missionId, opts): load the plan and draw one
// card per task; the task figures are fetched afterwards, a few at a time,
// and the cards are redrawn as they arrive.
const BOARD_TASKS_PARALLEL = 3;

async function renderBoardTasks(container, missionId, opts) {
  const path = "/api/board/missions/" + encodeURIComponent(missionId) + "/plan";
  let planData;
  try {
    planData = await getJSON(path);
  } catch (e) {
    renderError(container, e && e.message ? e.message : e);
    return;
  }
  const tasks = Array.isArray(planData.tasks) ? planData.tasks : [];
  const data = { stats: {} };
  render(container, missionId, tasks, data.stats);
  const keys = tasks.map((t) => t.task_id).filter((k) => k != null);
  let next = 0;
  let sinceRender = 0;
  async function worker() {
    while (next < keys.length) {
      const key = keys[next++];
      await loadTaskStats(data, missionId, key);
      sinceRender += 1;
      if (sinceRender >= 3 || next >= keys.length) { sinceRender = 0; render(container, missionId, tasks, data.stats); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(BOARD_TASKS_PARALLEL, keys.length) }, worker));
  render(container, missionId, tasks, data.stats);
}

// === export (browser global + CommonJS module.exports) ===
const BoardTasks = {
  render: renderBoardTasks,
  renderTasks,
  taskCardHtml,
  esc,
  fmtTokens,
  fmtTime,
  fmtPercent,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardTasks;
} else {
  globalThis.BoardTasks = BoardTasks;
}
})();
