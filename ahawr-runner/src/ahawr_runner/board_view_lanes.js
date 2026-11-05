/* board-scope-wrapped */
(function () {
"use strict";
/* Lanes view (T009): a row per active mission, a column per task state.
 * Only waiting notes move, and the rules are BoardOrder's: a drop that breaks
 * them opens a warning box, and nothing changes until the server accepts an order.
 */

const SHOW_FIRST = 6;
const STATS_TTL_MS = 15000;
const ACCEPTED = ["accepted", "accepted_retry"];
const COLUMNS = [
  { key: "wait", label: "col.wait", statuses: ["waiting"], tone: "ln-wait" },
  { key: "prog", label: "col.prog", statuses: ["running"], tone: "ln-run" },
  // the summary has no review status, so this column stays empty
  { key: "rev", label: "col.rev", statuses: [], tone: "ln-rev" },
  { key: "acc", label: "col.acc", statuses: ACCEPTED, tone: "ln-ok" },
  { key: "rej", label: "col.rej", statuses: ["rejected"], tone: "ln-bad" },
];

const state = {
  showAll: false,
  notice: null,
  warning: null,
  saving: false,
  drag: null,
  stats: new Map(),
  body: null,
  ctx: null,
};

async function postTaskReorder(missionId, body) {
  const res = await fetch("/api/board/missions/" + encodeURIComponent(missionId) + "/tasks/reorder", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json", "X-AHAWR-Dashboard": "1" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error("reorder " + res.status);
    err.status = res.status;
    throw err;
  }
  return data;
}

const io = {
  getJSON: (url) => globalThis.getJSON(url),
  postReorder: postTaskReorder,
  fetchSummary: () => globalThis.BoardViews.fetchSummary(),
};

function tr(key, params) {
  return typeof globalThis.t === "function" ? globalThis.t(key, params) : key;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children || []) if (child) node.append(child);
  return node;
}

function isActive(m) {
  return (m.tasks || []).some((t) => t.status === "running" || t.status === "waiting");
}

function byPosition(a, b) {
  const pa = a.position == null ? Infinity : a.position;
  const pb = b.position == null ? Infinity : b.position;
  if (pa === pb) return 0;
  return pa < pb ? -1 : 1;
}

function waitingTasks(m) {
  return (m.tasks || []).filter((t) => t.status === "waiting");
}

function waitingIndex(m, id) {
  return waitingTasks(m).findIndex((t) => t.id === id);
}

function boardTasks(m) {
  return (m.tasks || []).map((t) => ({
    id: t.id,
    st: t.status === "waiting" ? "g" : t.status,
    deps: t.deps || [],
  }));
}

function statsKey(mid, tid) {
  return mid + "\n" + tid;
}

function ensureStats(mid, tid) {
  const key = statsKey(mid, tid);
  const entry = state.stats.get(key);
  if (entry && Date.now() - entry.at < STATS_TTL_MS) return entry.pending;
  const url = "/api/board/missions/" + encodeURIComponent(mid) + "/tasks/" + encodeURIComponent(tid) + "/stats";
  const next = { at: Date.now(), data: entry ? entry.data : null, pending: null };
  next.pending = Promise.resolve()
    .then(() => io.getJSON(url))
    .then((data) => {
      next.data = data || null;
      return next.data;
    }, () => null);
  state.stats.set(key, next);
  return next.pending;
}

function figures(s) {
  const steps = Number(s.thinking_steps) || 0;
  return {
    minutes: Math.round((Number(s.worker_hours) || 0) * 60 + (Number(s.reviewer_minutes) || 0) + (Number(s.architect_minutes) || 0)),
    tokens: Math.round(((Number(s.input_tokens) || 0) + (Number(s.output_tokens) || 0)) / 1000),
    cache: Math.round((Number(s.cache_ratio) || 0) * 100),
    speed: Math.round(Number(s.tok_per_s) || 0),
    think: steps ? Math.round((100 * (Number(s.thinking_runs) || 0)) / steps) : 0,
    cost: (Number(s.cost_usd) || 0).toFixed(2),
  };
}

function shortLine(f) {
  return [
    tr("s.min", { n: f.minutes }),
    tr("s.tok", { n: f.tokens }),
    tr("s.cache", { n: f.cache }),
    tr("s.tps", { n: f.speed }),
  ].join(" · ");
}

function fullLine(f) {
  return shortLine(f) + " · " + tr("s.think", { n: f.think }) + " · $" + f.cost;
}

function statsOf(mid, tid) {
  const entry = state.stats.get(statsKey(mid, tid));
  return entry && entry.data ? figures(entry.data) : null;
}

function paintStats(note, m, task) {
  const waiting = task.status === "waiting";
  const f = waiting ? null : statsOf(m.mission_id, task.id);
  note.querySelector(".ln-stats").textContent = waiting ? tr("not.run") : f ? shortLine(f) : "…";
  const deps = task.deps || [];
  const head = task.id + " " + task.title + (deps.length ? " ← " + deps.join(", ") : "");
  note.setAttribute("title", head + "\n" + (waiting ? tr("not.run") : f ? fullLine(f) : "…"));
}

function select(note) {
  if (!state.body) return;
  state.body.querySelectorAll(".ln-note.sel").forEach((n) => n.classList.remove("sel"));
  note.classList.add("sel");
}

function isDropTarget(mid, tid) {
  return !!state.drag && state.drag.mid === mid && state.drag.id !== tid;
}

function bindDrag(note, m, task) {
  const mid = m.mission_id;
  note.addEventListener("dragstart", (event) => {
    state.drag = { mid, id: task.id };
    if (event.dataTransfer) event.dataTransfer.setData("text/plain", task.id);
  });
  note.addEventListener("dragend", () => {
    state.drag = null;
  });
  note.addEventListener("dragover", (event) => {
    if (!isDropTarget(mid, task.id)) return;
    event.preventDefault();
    note.classList.add("dropb");
  });
  note.addEventListener("dragleave", () => note.classList.remove("dropb"));
  note.addEventListener("drop", (event) => {
    note.classList.remove("dropb");
    if (!isDropTarget(mid, task.id)) return;
    event.preventDefault();
    const id = state.drag.id;
    state.drag = null;
    attemptMove(m, id, waitingIndex(m, task.id));
  });
}

function taskNote(m, task, col) {
  const waiting = task.status === "waiting";
  const deps = task.deps || [];
  const note = el("div", {
    class: "ln-note " + col.tone,
    "data-mid": m.mission_id,
    "data-tid": task.id,
    draggable: waiting ? "true" : null,
  }, [
    el("b", { text: task.id }),
    el("span", { class: "ln-title", text: task.title }),
    deps.length ? el("small", { class: "ln-deps", text: "← " + deps.join(", ") }) : null,
    el("small", { class: "ln-att", text: tr("att", { n: task.attempts || 0 }) }),
    el("span", { class: "ln-stats" }),
  ]);
  paintStats(note, m, task);
  note.addEventListener("click", () => select(note));
  if (waiting) bindDrag(note, m, task);
  else ensureStats(m.mission_id, task.id).then(() => paintStats(note, m, task));
  return note;
}

function missionRow(m) {
  const tasks = m.tasks || [];
  const done = tasks.filter((t) => ACCEPTED.includes(t.status)).length;
  const project = m.project ? m.project.name || m.project.key || "" : "";
  return el("div", { class: "lane", "data-lane": m.mission_id }, [
    el("div", { class: "lh" }, [
      el("b", { text: m.name || m.mission_id }),
      el("span", { class: "mono", text: tr("of", { a: done, b: tasks.length }) + " · " + project }),
    ]),
    ...COLUMNS.map((col) => {
      const cell = el("div", { class: col.key === "wait" ? "pend" : "" });
      for (const task of tasks) if (col.statuses.includes(task.status)) cell.append(taskNote(m, task, col));
      return cell;
    }),
  ]);
}

function pairsText(broken) {
  return broken.map((p) => p[0] + "→" + p[1]).join(", ");
}

function warningBox(m, warning) {
  const { id, res, down } = warning;
  const task = (m.tasks || []).find((t) => t.id === id) || { id, title: "" };
  const box = el("div", { class: "warnbox" + (res.group ? "" : " bad") });
  let text;
  if (res.reason !== "dependency") {
    text = tr("w.bad", { pairs: pairsText(res.broken) });
  } else if (down) {
    const list = res.broken.filter((p) => p[0] === id).map((p) => p[1]).join(", ");
    text = tr("w.down", { id, title: task.title, list });
  } else {
    const list = res.broken.filter((p) => p[1] === id).map((p) => p[0]).join(", ");
    text = tr("w.up", { id, title: task.title, list });
  }
  box.append(el("div", { text }));
  if (res.group) {
    box.append(el("div", {
      text: tr("w.group", {
        rel: tr(down ? "rel.dependents" : "rel.dependencies"),
        list: res.group.ids.join(", "),
        n: res.group.ids.length,
      }),
    }));
    const go = el("button", { type: "button", class: "ln-group", text: tr("btn.group") });
    go.addEventListener("click", () => save(m, res.group.order));
    box.append(go);
  }
  const cancel = el("button", { type: "button", class: "ln-cancel", text: tr("btn.cancel") });
  cancel.addEventListener("click", () => {
    state.warning = null;
    rerender();
  });
  box.append(cancel);
  return box;
}

function attemptMove(m, id, toIndex) {
  state.warning = null;
  const res = globalThis.BoardOrder.tryMove(boardTasks(m), id, toIndex);
  if (res.ok) return save(m, res.order);
  if (res.reason === "dependency" || res.reason === "group-invalid") {
    state.warning = { mid: m.mission_id, id, res, down: toIndex > waitingIndex(m, id) };
  }
  rerender();
}

async function save(m, order) {
  if (state.saving) return;
  state.saving = true;
  state.warning = null;
  state.notice = null;
  const waiting = new Set(waitingTasks(m).map((t) => t.id));
  const body = { order: order.filter((id) => waiting.has(id)), expected_version: null, who: "" };
  try {
    const plan = await io.getJSON("/api/board/missions/" + encodeURIComponent(m.mission_id) + "/plan");
    body.expected_version = plan.version;
    const res = await io.postReorder(m.mission_id, body);
    state.notice = res && res.running_warning ? { text: tr("running.warn"), bad: false } : null;
    const data = await io.fetchSummary().catch(() => null);
    if (data) {
      state.ctx = Object.assign({}, state.ctx, {
        data,
        missions: data.missions || [],
        projects: data.projects || [],
      });
    }
    rerender();
  } catch (err) {
    const conflict = err && err.status === 409;
    state.notice = { text: tr(conflict ? "conflict.plan_changed" : "move.failed"), bad: true };
    rerender();
  } finally {
    state.saving = false;
  }
}

function renderInto(body, ctx) {
  body.replaceChildren();
  const missions = (ctx.missions || []).filter(isActive).sort(byPosition);
  const shown = state.showAll ? missions : missions.slice(0, SHOW_FIRST);
  body.append(el("p", { class: "sub", text: tr("lane.hint") }));
  if (state.notice) {
    body.append(el("div", { class: "warnbox" + (state.notice.bad ? " bad" : ""), text: state.notice.text }));
  }
  body.append(el("div", { class: "lane hd" }, [el("div"), ...COLUMNS.map((c) => el("div", { text: tr(c.label) }))]));
  for (const m of shown) {
    if (state.warning && state.warning.mid === m.mission_id) body.append(warningBox(m, state.warning));
    body.append(missionRow(m));
  }
  if (!state.showAll && missions.length > SHOW_FIRST) {
    const more = el("button", { type: "button", class: "ln-more", text: tr("more", { n: missions.length - SHOW_FIRST }) });
    more.addEventListener("click", () => {
      state.showAll = true;
      rerender();
    });
    body.append(more);
  }
}

function rerender() {
  if (state.body && state.ctx) renderInto(state.body, state.ctx);
}

function render(body, ctx) {
  state.body = body;
  state.ctx = ctx;
  renderInto(body, ctx);
}

function _reset() {
  Object.assign(state, {
    showAll: false,
    notice: null,
    warning: null,
    saving: false,
    drag: null,
    body: null,
    ctx: null,
  });
  state.stats = new Map();
}

function _setIo(overrides) {
  Object.assign(io, overrides);
}

const BoardViewLanes = {
  render,
  boardTasks,
  figures,
  shortLine,
  fullLine,
  isActive,
  _reset,
  _setIo,
};

globalThis.BoardViewLanes = BoardViewLanes;
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardViewLanes;
} else {
  globalThis.BoardViews.register("lane", { render });
}
})();
