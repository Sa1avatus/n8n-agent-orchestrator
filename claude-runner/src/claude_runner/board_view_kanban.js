/* board-scope-wrapped */
(function () {
"use strict";
/* Kanban view (T010): four state columns as in docs/board-views-mockup.html.
 *
 * The Queued column does not duplicate the queue: its cards get the drag and
 * up/down controls from BoardDnd.attachCard, the card actions from
 * BoardActions.attachCardActions, and the "new mission" button calls
 * BoardActions.toggleNewMission (the same button instance the queue view
 * toggles, supplied by BoardActions.getNewMissionButton).
 *
 * A move (drag or up/down) sends the same request as before; afterwards the
 * queued column is re-rendered from the shared queue data (the
 * "board:refresh" event BoardDnd.applyMove dispatches) instead of waiting
 * for the next summary change. A failed queue load is shown in the queued
 * column with tr("board.load_failed").
 */

const STATE_OF_GROUP = {
  running: "run",
  queued: "q",
  completed: "ok",
  not_completed: "bad",
  disabled: "off",
};
const COLUMNS = ["run", "q", "ok", "bad"];
const DONE_STATUSES = ["accepted", "accepted_retry"];

let loadingQueue = false;
let queueFailed = false;
let refreshHandler = null;
let newButton = null;

function stateOf(mission) {
  return STATE_OF_GROUP[mission.state_group] || "off";
}

function taskCounts(mission) {
  const tasks = Array.isArray(mission.tasks) ? mission.tasks : [];
  const done = tasks.filter((task) => DONE_STATUSES.includes(task.status)).length;
  return { done, total: tasks.length };
}

function queueModules() {
  if (typeof module !== "undefined" && module.exports) {
    return { dnd: require("./board_dnd.js"), actions: require("./board_actions.js") };
  }
  return { dnd: globalThis.BoardDnd, actions: globalThis.BoardActions };
}

function attachQueuedCards(controls, cards) {
  for (const card of cards) {
    controls.dnd.attachCard(card, controls.dnd.DndState.data);
    controls.actions.attachCardActions(card, controls.dnd.DndState.data);
  }
}

function newMissionButton(controls, ctx) {
  if (!controls.actions || typeof controls.actions.toggleNewMission !== "function") {
    return null;
  }
  if (!newButton) {
    newButton = controls.actions.getNewMissionButton ? controls.actions.getNewMissionButton() : null;
    if (!newButton) {
      newButton = document.createElement("button");
      newButton.type = "button";
      newButton.className = "qnew";
      newButton.dataset.formOpen = "0";
      newButton.addEventListener("click", () => {
        try {
          controls.actions.toggleNewMission();
        } catch (e) { /* the queue view owns the button state */ }
      });
    }
  }
  newButton.textContent = ctx.tr("kan.new");
  return newButton;
}

function queuedColumn(ctx, key, missions, cards, controls) {
  const { el, tr } = ctx;
  const nodes = missions.map((mission, i) => missionCard(ctx, mission, i));
  if (cards) cards.push(...nodes);
  const children = [el("h3", {}, [
    el("span", { text: tr("st." + key) }),
    el("span", { class: "mono", text: String(missions.length) }),
    newMissionButton(controls, ctx),
  ])];
  if (queueFailed) {
    children.push(el("p", { class: "bv-note", text: tr("board.load_failed") }));
  } else {
    children.push(el("div", { class: "kcol-cards" }, nodes));
  }
  return el("div", { class: "kcol", "data-col": key }, children);
}

function missionCard(ctx, mission, position) {
  const { el, tr } = ctx;
  const id = mission.mission_id;
  const name = mission.name || id;
  const project = mission.project || {};
  const projectKey = project.key || "";
  const { done, total } = taskCounts(mission);
  const state = stateOf(mission);
  return el("div", {
    class: "bv-card kcard",
    "data-id": id,
    "data-project": projectKey,
    "data-mission-id": id,
    "data-group": mission.state_group || "",
    "data-position": String(position),
    "data-search": (name + " " + id).toLowerCase(),
  }, [
    el("b", { text: name }),
    el("div", {}, [
      projectKey ? el("span", { class: "kchip", text: project.name || projectKey }) : null,
      el("span", { class: "mono", text: id }),
    ]),
    el("div", { class: "bar" }, [el("i", { style: "width:" + (total ? Math.round(100 * done / total) : 0) + "%" })]),
    el("div", { class: "kchips" }, figureChips(ctx, mission, done, total)),
  ]);
}

function figureChips(ctx, mission, done, total) {
  const { el, tr } = ctx;
  const num = (v) => (v === null || v === undefined || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
  const attempts = num(mission.attempts);
  const hours = num(mission.hours);
  const cost = num(mission.cost);
  const tps = num(mission.tok_s);
  const cache = num(mission.cache_ratio);
  const think = num(mission.thinking_share);
  const chips = [tr("of", { a: done, b: total })];
  const since = mission.started_at || mission.created_at;
  if (since && Number.isFinite(Date.parse(since))) {
    const d = new Date(Date.parse(since));
    const p = (n) => String(n).padStart(2, "0");
    chips.push(tr("kan.since", { d: d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) }));
  }
  if (attempts) chips.push(tr("att", { n: attempts }));
  if (hours) chips.push(tr("hours", { n: hours.toFixed(1) }));
  if (cost) chips.push("$" + cost.toFixed(2));
  if (tps) chips.push(tr("s.tps", { n: Math.round(tps) }));
  if (cache) chips.push(tr("cache", { n: Math.round(cache * 100) }));
  if (think !== null) chips.push(tr("think", { n: Math.round(think * 100) }));
  return chips.map((text) => el("span", { class: "ch", text }));
}

function column(ctx, key, missions, cards, controls) {
  const { el, tr } = ctx;
  if (key === "q") return queuedColumn(ctx, key, missions, cards, controls);
  const nodes = missions.map((mission, i) => missionCard(ctx, mission, i));
  if (cards) cards.push(...nodes);
  const children = [el("h3", {}, [
    el("span", { text: tr("st." + key) }),
    el("span", { class: "mono", text: String(missions.length) }),
  ]), el("div", { class: "kcol-cards" }, nodes)];
  return el("div", { class: "kcol", "data-col": key }, children);
}

let lastCtx = null;

// Re-render the queued column from the current queue data: the card order
// comes from the queue, so the moved mission appears without waiting for the
// next summary change.
function requeue(controls, ctx) {
  const { el } = ctx;
  const body = document.getElementById("boardviewbody");
  if (!body) return;
  const col = body.querySelector('[data-col="q"]');
  if (!col) return;
  const queue = controls.dnd.DndState.data && controls.dnd.DndState.data.queue
    ? controls.dnd.DndState.data.queue : [];
  const byId = {};
  for (const m of ctx.missions || []) if (stateOf(m) === "q") byId[m.mission_id] = m;
  const ordered = queue
    .map((entry) => byId[entry.mission_id])
    .filter((m) => m);
  const head = col.querySelector("h3");
  col.replaceChildren(
    head,
    el("div", { class: "kcol-cards" }, ordered.map((m, i) => missionCard(ctx, m, i))),
  );
  attachQueuedCards(controls, [...col.querySelectorAll(".kcard")]);
}

function onRefresh() {
  if (!lastCtx) return;
  const { dnd, actions } = queueModules();
  if (!dnd || !actions || !dnd.DndState.data) return;
  requeue({ dnd, actions }, lastCtx);
}

function render(body, ctx) {
  const { el, tr } = ctx;
  if (!ctx.data) {
    body.append(el("p", { class: "bv-note", text: tr("board.offline") }));
    return;
  }
  body.append(el("p", { class: "bv-note", text: tr("kan.hint") }));
  const missions = Array.isArray(ctx.missions) ? ctx.missions : [];
  const queued = [];
  const { dnd, actions } = queueModules();
  const controls = { dnd, actions };
  lastCtx = ctx;
  queueFailed = false;
  body.append(el("div", { class: "kb" }, COLUMNS.map((key) => column(
    ctx,
    key,
    missions.filter((mission) => stateOf(mission) === key),
    key === "q" ? queued : null,
    controls,
  ))));
  if (!dnd || !actions) return;
  if (dnd.DndState.data) attachQueuedCards(controls, queued);
  else loadQueueData(controls, queued, ctx);
  // Re-render the queued column when a move lands (BoardDnd.applyMove
  // dispatches "board:refresh" after a successful move).
  if (!refreshHandler) {
    refreshHandler = onRefresh;
    document.addEventListener("board:refresh", refreshHandler);
  }
}

function loadQueueData(controls, cards, ctx) {
  const dnd = controls.dnd;
  if (loadingQueue) return;
  loadingQueue = true;
  dnd.loadBoardData()
    .then((data) => {
      dnd.DndState.data = data;
      attachQueuedCards(controls, cards);
    })
    .catch(() => {
      queueFailed = true;
      const body = document.getElementById("boardviewbody");
      if (body) {
        const col = body.querySelector('[data-col="q"]');
        if (col) {
          const cardsDiv = col.querySelector(".kcol-cards");
          if (cardsDiv) cardsDiv.replaceChildren();
          const note = document.createElement("p");
          note.className = "bv-note";
          note.textContent = ctx.tr("board.load_failed");
          col.appendChild(note);
        }
      }
    })
    .finally(() => { loadingQueue = false; });
}

function select(card) {
  return card.dataset.id;
}

function open(id) {
  const back = location.hash.replace(/^#/, "") || "board";
  (typeof window !== "undefined" && window.top ? window.top : globalThis).location.href = "/#mission=" + encodeURIComponent(id) + "&from=" + encodeURIComponent(back);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, select, open, stateOf, taskCounts, requeue, onRefresh };
} else if (globalThis.BoardViews) {
  globalThis.BoardViews.register("kan", { render, select, open });
}
})();
