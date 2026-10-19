/* board-scope-wrapped */
(function () {
"use strict";
/* Grid view (T010): a sortable table of missions with a segmented task bar.
 *
 * A click highlights a row without redrawing; a double click opens
 * #board/<mission_id>.  Sorting is stable and keyed by column: rows that
 * compare equal keep their summary order, and missing values sort last in
 * both directions.
 */

const STATE_OF_GROUP = {
  running: "run",
  queued: "q",
  completed: "ok",
  not_completed: "bad",
  disabled: "off",
};
const STATE_ORDER = { run: 0, bad: 1, q: 2, ok: 3, off: 4 };
const DONE_STATUSES = ["accepted", "accepted_retry"];

let sortState = { key: "name", dir: 1 };
let selectedId = null;

function stateOf(mission) {
  return STATE_OF_GROUP[mission.state_group] || "off";
}

function taskCounts(mission) {
  const tasks = Array.isArray(mission.tasks) ? mission.tasks : [];
  const done = tasks.filter((task) => DONE_STATUSES.includes(task.status)).length;
  return { done, total: tasks.length };
}

function numberOf(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// an ISO time as a sortable number (null when there is none)
function timeOf(iso) {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

// "2026-10-19 09:15" in the viewer's time zone ("—" when there is no date)
function fmtDate(iso) {
  const ms = timeOf(iso);
  if (ms === null) return "—";
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

function projectName(mission) {
  const project = mission.project || {};
  return project.name || project.key || "";
}

const COLUMNS = [
  { key: "name", label: "h.mission", value: (m) => String(m.name || m.mission_id).toLowerCase() },
  { key: "id", label: "h.id", value: (m) => String(m.mission_id || "").toLowerCase() },
  { key: "proj", label: "h.project", value: (m) => projectName(m).toLowerCase() },
  { key: "state", label: "h.state", value: (m) => STATE_ORDER[stateOf(m)] },
  { key: "tasks", label: "h.tasks", value: (m) => taskCounts(m).done },
  { key: "started", label: "h.started", value: (m) => timeOf(m.started_at || m.created_at) },
  { key: "last", label: "h.last", value: (m) => timeOf(m.last_at) },
  { key: "att", label: "h.att", numeric: true, value: (m) => numberOf(m.attempts) },
  { key: "time", label: "h.time", numeric: true, value: (m) => numberOf(m.hours) },
  { key: "cache", label: "h.cache", numeric: true, value: (m) => numberOf(m.cache_ratio) },
  { key: "cost", label: "h.cost", numeric: true, value: (m) => numberOf(m.cost) },
  { key: "tps", label: "h.tps", numeric: true, value: (m) => numberOf(m.tok_s) },
];

function sortMissions(missions, key, dir) {
  const column = COLUMNS.find((c) => c.key === key) || COLUMNS[0];
  return missions
    .map((mission, index) => ({ mission, index, value: column.value(mission) }))
    .sort((a, b) => {
      if (a.value === null || b.value === null) {
        if (a.value === b.value) return a.index - b.index;
        return a.value === null ? 1 : -1;
      }
      const order = a.value < b.value ? -1 : a.value > b.value ? 1 : 0;
      return order * dir || a.index - b.index;
    })
    .map((entry) => entry.mission);
}

function formatNumber(value, digits) {
  return value === null ? "—" : value.toFixed(digits);
}

function segmentKind(status) {
  if (DONE_STATUSES.includes(status)) return "done";
  if (status === "running") return "run";
  return "rest";
}

function segmentBar(ctx, mission) {
  const { el } = ctx;
  const tasks = Array.isArray(mission.tasks) ? mission.tasks : [];
  const width = tasks.length ? 100 / tasks.length : 0;
  return el("span", { class: "segbar" }, tasks.map((task) => el("i", {
    class: "seg seg-" + segmentKind(task.status),
    style: "width:" + width + "%",
  })));
}

function missionRow(ctx, mission) {
  const { el, tr } = ctx;
  const id = mission.mission_id;
  const name = mission.name || id;
  const state = stateOf(mission);
  const { done, total } = taskCounts(mission);
  const attempts = numberOf(mission.attempts);
  const hours = numberOf(mission.hours);
  const cache = numberOf(mission.cache_ratio);
  const cost = numberOf(mission.cost);
  const tps = numberOf(mission.tok_s);
  return el("tr", {
    class: "row bv-card" + (selectedId === id ? " sel" : ""),
    "data-id": id,
    "data-project": (mission.project && mission.project.key) || "",
    "data-search": (name + " " + id).toLowerCase(),
  }, [
    el("td", {}, [el("b", { text: name }), el("div", { class: "mono", text: id })]),
    el("td", { class: "mono", text: id }),
    el("td", { text: projectName(mission) }),
    el("td", {}, [el("span", { class: "pill p-" + state, text: tr("st." + state) })]),
    el("td", {}, [segmentBar(ctx, mission), el("span", { class: "mono", text: tr("of", { a: done, b: total }) })]),
    el("td", { class: "mono", text: fmtDate(mission.started_at || mission.created_at) }),
    el("td", { class: "mono", text: fmtDate(mission.last_at) }),
    el("td", { class: "num", text: formatNumber(attempts, 0) }),
    el("td", { class: "num", text: formatNumber(hours, 1) }),
    el("td", { class: "num", text: cache === null ? "—" : String(Math.round(cache * 100)) }),
    el("td", { class: "num", text: formatNumber(cost, 2) }),
    el("td", { class: "num", text: formatNumber(tps, 1) }),
  ]);
}

function headerCell(ctx, column, wrap) {
  const { el, tr } = ctx;
  const active = sortState.key === column.key;
  const arrow = active ? (sortState.dir > 0 ? " ↑" : " ↓") : "";
  const cell = el("th", {
    class: column.numeric ? "num" : undefined,
    "data-sort": column.key,
    "aria-sort": active ? (sortState.dir > 0 ? "ascending" : "descending") : "none",
    text: tr(column.label) + arrow,
  });
  cell.addEventListener("click", () => {
    sortState = sortState.key === column.key
      ? { key: column.key, dir: -sortState.dir }
      : { key: column.key, dir: 1 };
    drawTable(wrap, ctx);
  });
  return cell;
}

function drawTable(wrap, ctx) {
  const { el } = ctx;
  const sorted = sortMissions(ctx.missions, sortState.key, sortState.dir);
  const head = el("tr", {}, COLUMNS.map((column) => headerCell(ctx, column, wrap)));
  const rows = sorted.map((mission) => missionRow(ctx, mission));
  wrap.replaceChildren(el("table", { class: "g" }, [head, ...rows]));
}

function render(body, ctx) {
  const { el, tr } = ctx;
  if (!ctx.data) {
    body.append(el("p", { class: "bv-note", text: tr("board.offline") }));
    return;
  }
  body.append(el("p", { class: "bv-note", text: tr("grid.hint") }));
  if (ctx.missions.length === 0) {
    body.append(el("p", { class: "bv-note", text: tr("grid.empty") }));
    return;
  }
  const wrap = el("div", { class: "gridwrap" });
  body.append(wrap);
  drawTable(wrap, ctx);
}

function select(card) {
  selectedId = card.dataset.id;
}

function open(id) {
  location.hash = "#board/" + encodeURIComponent(id);
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, select, open, sortMissions, COLUMNS };
} else if (globalThis.BoardViews) {
  globalThis.BoardViews.register("grid", { render, select, open });
}
})();
