/* board-scope-wrapped */
(function () {
"use strict";

const STATE_OF_GROUP = {
  running: "run",
  queued: "q",
  completed: "ok",
  not_completed: "bad",
  disabled: "off",
};
const STATES = ["run", "q", "ok", "bad", "off"];
const DONE_STATUSES = ["accepted", "accepted_retry"];

const openKeys = new Set();
let selectedMission = null;

function stateOf(mission) {
  return STATE_OF_GROUP[mission.state_group] || "off";
}

function sumOf(missions, pick) {
  return missions.reduce((total, m) => total + (Number(pick(m)) || 0), 0);
}

function formatHours(value) {
  return (Number(value) || 0).toFixed(1);
}

function formatCost(value) {
  return (Number(value) || 0).toFixed(2);
}

function formatTokens(value) {
  const n = Number(value) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function taskCounts(mission) {
  const tasks = Array.isArray(mission.tasks) ? mission.tasks : [];
  const done = tasks.filter((task) => DONE_STATUSES.includes(task.status)).length;
  return { done, total: tasks.length };
}

function percentDone(mission) {
  const { done, total } = taskCounts(mission);
  return total ? Math.round(100 * done / total) : 0;
}

function groupByProject(missions) {
  const groups = new Map();
  for (const mission of missions) {
    const key = mission.project && mission.project.key;
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(mission);
  }
  return groups;
}

function stackBar(ctx, missions) {
  const { el, tr } = ctx;
  const counts = Object.fromEntries(STATES.map((s) => [s, 0]));
  for (const mission of missions) counts[stateOf(mission)] += 1;
  const total = missions.length;
  return el("div", { class: "stack" }, STATES.filter((s) => counts[s] > 0).map((s) => el("i", {
    class: "s-" + s,
    style: "width:" + (100 * counts[s] / total) + "%",
    title: tr("st." + s) + ": " + counts[s],
  })));
}

function missionRow(ctx, mission) {
  const { el, tr } = ctx;
  const state = stateOf(mission);
  const { done, total } = taskCounts(mission);
  return el("div", { class: "kc", "data-m": mission.mission_id }, [
    el("span", { class: "pill p-" + state, text: tr("st." + state) }),
    el("b", { text: mission.name || mission.mission_id }),
    el("span", { class: "mono", text: tr("of", { a: done, b: total }) }),
    el("span", { class: "mono", text: tr("hours", { n: formatHours(mission.hours) }) }),
  ]);
}

function wireMissionRows(rows) {
  for (const row of rows) {
    row.addEventListener("click", (event) => {
      event.stopPropagation();
      selectedMission = row.dataset.m;
      for (const other of rows) other.classList.toggle("sel", other === row);
    });
    row.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      location.hash = "#board/" + encodeURIComponent(row.dataset.m);
    });
  }
}

function projectCard(ctx, project, missions) {
  const { el, tr } = ctx;
  const key = project.key;
  const name = project.name || key || tr("proj.nofolder");
  const isOpen = openKeys.has(key);
  const running = missions.find((m) => stateOf(m) === "run");
  const now = running ? tr("proj.now", { name: running.name || running.mission_id, pct: percentDone(running) }) : tr("proj.none");

  const rows = missions.map((m) => missionRow(ctx, m));
  wireMissionRows(rows);

  const arrow = el("button", {
    type: "button",
    class: "arr",
    "aria-label": tr("proj.expand"),
    "aria-expanded": isOpen ? "true" : "false",
    text: "▸",
  });

  const card = el("div", {
    class: "bv-card pr" + (isOpen ? " open" : ""),
    "data-id": key,
    "data-project": key,
    "data-search": (name + " " + key).toLowerCase(),
  }, [
    el("div", { class: "prh" }, [
      el("div", {}, [
        el("b", { text: name }),
        el("span", { class: "mono", text: project.rule || "" }),
      ]),
      el("div", {}, [stackBar(ctx, missions), el("span", { class: "mono", text: now })]),
      el("div", { class: "kpi" }, [el("span", { text: tr("kpi.m") }), el("b", { text: String(missions.length) })]),
      el("div", { class: "kpi" }, [el("span", { text: tr("kpi.t") }), el("b", { text: tr("hours", { n: formatHours(sumOf(missions, (m) => m.hours)) }) })]),
      el("div", { class: "kpi" }, [el("span", { text: tr("kpi.k") }), el("b", { text: formatTokens(sumOf(missions, (m) => m.tokens)) })]),
      el("div", { class: "kpi" }, [el("span", { text: tr("kpi.c") }), el("b", { text: formatCost(sumOf(missions, (m) => m.cost)) })]),
      arrow,
    ]),
    el("div", { class: "prb" }, rows),
  ]);

  arrow.addEventListener("click", () => {
    const nowOpen = !card.classList.contains("open");
    card.classList.toggle("open", nowOpen);
    arrow.setAttribute("aria-expanded", nowOpen ? "true" : "false");
    if (nowOpen) openKeys.add(key);
    else openKeys.delete(key);
  });

  return card;
}

function render(body, ctx) {
  const { el, tr } = ctx;
  if (!ctx.data) {
    body.append(el("p", { class: "bv-note", text: tr("board.offline") }));
    return;
  }
  const projects = Array.isArray(ctx.projects) ? ctx.projects : [];
  if (projects.length === 0) {
    body.append(el("p", { class: "bv-note", text: tr("proj.hint") }));
    return;
  }
  const groups = groupByProject(ctx.missions);
  for (const project of projects) {
    const missions = groups.get(project.key) || [];
    body.append(projectCard(ctx, project, missions));
  }
}

function open(id) {
  location.hash = "#board/view/kan?project=" + encodeURIComponent(id);
}

const impl = { render, open };

if (typeof module !== "undefined" && module.exports) {
  module.exports = { render, open, stateOf, taskCounts, formatTokens };
} else {
  if (globalThis.BoardViews) globalThis.BoardViews.register("proj", impl);
}
})();
