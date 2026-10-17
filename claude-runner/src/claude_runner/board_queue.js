/* board-scope-wrapped */
(function () {
/* Board top-level render (T019): the queue view with mission cards.
 *
 * Loaded by board.html via <script src="/static/board_queue.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardQueue() is the render function the shell (board.js) calls for the
 * #board fragment: it loads the /api/board data and renders
 * #boardqueue.  The pure renderQueue(data) builds the view from a plain
 * object matching the /api/board responses, so it can be tested in node
 * without a DOM.
 *
 * Data shape (assembled by BoardQueue from the endpoints):
 *   groups:  { running: [id, ...], queued: [id, ...],
 *              completed: [id, ...], not_completed: [id, ...],
 *              disabled: [id, ...] }        // GET /api/board/missions
 *   missions: [ { mission_id, name, ... } ] // GET /api/board/missions
 *   queue:    [ { position, mission_id, ... } ]
 *   version:  "..."                         // GET /api/board/queue
 *   cards:    { mission_id: { live_state, task_views, stats } }
 *              // GET /api/board/missions/{id} and /api/board/missions/{id}/stats
 *
 * Every value taken from server data flows through esc() (board_logic.js's
 * esc, with a local fallback when that script failed to load).
 * No drag-and-drop and no actions here (the next tasks).
 */
"use strict";

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

// The five groups in display order, keyed by the store's group names.
const GROUPS = [
  { key: "running", title: "Идёт сейчас" },
  { key: "queued", title: "В очереди" },
  { key: "completed", title: "Завершены" },
  { key: "not_completed", title: "Не завершены/остановлены" },
  { key: "disabled", title: "Выключены" },
];

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
  if (minutes >= 60) return (minutes / 60).toFixed(1) + " h";
  return String(Math.round(minutes)) + " min";
}

// A ratio (0..1) as a percent; "—" when there is nothing to count.
function fmtPercent(ratio, parts) {
  const total = Number(parts) || 0;
  if (total <= 0) return "—";
  const r = Number(ratio);
  if (!isFinite(r) || r < 0) return "—";
  return Math.round(r * 100) + "%";
}

// One mission card: name, mission_id, state, task n of total, progress bar,
// total attempts, time, tokens, cache share, cost, avg tok/s, thinking share.
// Returns the escaped HTML string of the card.
function cardHtml(mission, position, groupKey, card) {
  const id = mission.mission_id;
  const live = card ? card.live_state : null;
  const stats = card ? card.stats : null;
  const views = card && card.task_views ? card.task_views : [];
  const total = views.length;
  const taskN = live && live.task_index != null ? Number(live.task_index) + 1 : null;
  const pct = taskN && total > 0
    ? Math.round(Math.max(0, Math.min(100, (taskN / total) * 100)))
    : 0;

  const status = live && live.status ? live.status : groupKey;
  const attempts = stats
    ? Object.values(stats.tasks || {}).reduce((s, t) => s + (Number(t.attempts) || 0), 0)
    : 0;
  const time = stats ? fmtTime(stats.worker_hours, stats.reviewer_minutes) : "—";
  const tokens = stats ? fmtTokens((Number(stats.input_tokens) || 0) + (Number(stats.output_tokens) || 0)) : "—";
  const cache = stats ? fmtPercent(stats.cache_ratio, (Number(stats.cache_read_tokens) || 0) + (Number(stats.cache_write_tokens) || 0)) : "—";
  const cost = stats && stats.cost_usd != null ? "$" + Number(stats.cost_usd).toFixed(2) : "—";
  const speed = stats && stats.tok_per_s != null ? Math.round(Number(stats.tok_per_s)) + " tok/s" : "—";
  const thinking = stats ? fmtPercent(
    stats.thinking_steps ? stats.thinking_runs / stats.thinking_steps : 0,
    stats.thinking_steps,
  ) : "—";

  return (
    `<a class="qitem qcard qcard-${esc(groupKey)}" href="#board/${esc(id)}" ` +
    `data-mission-id="${esc(id)}" data-group="${esc(groupKey)}" data-position="${esc(position)}" draggable="false">` +
    `<span class="pos">${esc(position + 1)}</span>` +
    `<span class="name">${esc(mission.name)}</span>` +
    `<span class="id">${esc(id)}</span>` +
    `<span class="status">${esc(status)}</span>` +
    `<div class="qmeta">` +
    `<span class="qtask">${taskN != null ? esc(taskN) + " of " + esc(total) : esc("—")}</span>` +
    `<span class="qbar"><span class="qbarfill" style="width:${esc(pct)}%"></span></span>` +
    `<span class="qstats" title="attempts · time · tokens · cache · cost · speed · thinking">` +
    esc(attempts) + " attempts · " + esc(time) + " · " + esc(tokens) + " tokens · " +
    esc(cache) + " cache · " + esc(cost) + " · " + esc(speed) + " · " + esc(thinking) +
    ` thinking</span>` +
    `</div></a>`
  );
}

// Pure render: the five groups with their mission cards, in the store's
// group order, missions inside a group in queue position.  Returns the
// escaped HTML string of the whole #boardqueue content.
function renderQueue(data) {
  const groups = data.groups || {};
  const missions = data.missions || [];
  const queue = data.queue || [];
  const cards = data.cards || {};
  const byId = {};
  for (const m of missions) byId[m.mission_id] = m;
  const positionOf = {};
  for (const e of queue) positionOf[e.mission_id] = Number(e.position) || 0;

  const html = [];
  for (const g of GROUPS) {
    const ids = Array.isArray(groups[g.key]) ? groups[g.key] : [];
    ids.sort((a, b) => (positionOf[a] || 0) - (positionOf[b] || 0));
    const items = ids
      .filter((id) => byId[id])
      .map((id) => cardHtml(byId[id], positionOf[id] || 0, g.key, cards[id]))
      .join("");
    html.push(
      `<section class="qgroup"><h2>${esc(g.title)}</h2>` +
      (items || `<div class="qempty">—</div>`) +
      `</section>`
    );
  }
  return html.join("");
}

// Render into #boardqueue, creating the container inside #board when the
// page has no #boardqueue yet; a successful render clears the shell's
// #boardstate line (the shell sets it back on later errors).
function render(data) {
  let el = document.getElementById("boardqueue");
  if (!el) {
    el = document.createElement("div");
    el.id = "boardqueue";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  el.innerHTML = renderQueue(data);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
}

// BoardQueue(): load the /api/board data and render #boardqueue.
// Called by the shell's route() for the #board fragment. The board is drawn at once from the mission list and the
// queue; the figures of each card (live state, task views, statistics) are fetched afterwards, a few at a time and
// in the order running, queued, not completed, completed (disabled missions have no runs, so no statistics are
// asked for), and the board is redrawn as they arrive. A card whose figures fail to load keeps its dashes.
const CARD_ORDER = ["running", "queued", "not_completed", "completed"];
const CARD_PARALLEL = 3;

async function loadCard(data, id) {
  const path = "/api/board/missions/" + encodeURIComponent(id);
  try {
    const detail = await getJSON(path);
    let stats = null;
    try { stats = await getJSON(path + "/stats"); } catch (e) { stats = null; }
    data.cards[id] = { live_state: detail.live_state, task_views: detail.task_views, stats };
  } catch (e) {
    data.cards[id] = null;
  }
}

async function BoardQueue() {
  const data = {
    ...(await getJSON("/api/board/missions")),
    ...(await getJSON("/api/board/queue")),
  };
  data.cards = {};
  render(data);
  const groups = data.groups || {};
  const ids = [];
  for (const key of CARD_ORDER) for (const id of Array.isArray(groups[key]) ? groups[key] : []) ids.push(id);
  let next = 0;
  let sinceRender = 0;
  async function worker() {
    while (next < ids.length) {
      const id = ids[next++];
      await loadCard(data, id);
      sinceRender += 1;
      if (sinceRender >= 3 || next >= ids.length) { sinceRender = 0; render(data); }
    }
  }
  await Promise.all(Array.from({ length: Math.min(CARD_PARALLEL, ids.length) }, worker));
  render(data);
}

// === export (browser global + CommonJS module.exports) ===
const BoardQueueView = { renderQueue, cardHtml, BoardQueue, render,
  GROUPS, esc, fmtTokens, fmtTime, fmtPercent };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardQueueView;
} else {
  globalThis.BoardQueue = BoardQueue;
  globalThis.renderQueue = renderQueue;
}
})();
