/* board-scope-wrapped */
(function () {
"use strict";
/* Sprint view (T011): the backlog of queued and running missions, the
 * selected mission as a sprint with its figures, an accepted-over-time chart
 * and the obstacles.  A click highlights a backlog card without redrawing; a
 * double click opens #board/<mission_id>.  The chart and obstacles are filled
 * after render from GET /api/board/missions/<id>/timeline.  Obstacles carry a
 * kind of "rejected_3x", "timeout" or "interrupted" (the contract for that
 * endpoint); any other kind renders the generic "imp.t" label.
 */

const DONE_STATUSES = ["accepted", "accepted_retry"];
const STATE_OF_GROUP = { running: "run", queued: "q" };
const KNOWN_OBSTACLE_KINDS = new Set(["rejected_3x", "timeout", "interrupted"]);
const CHART = { width: 240, height: 60, pad: 4 };
const SHOWN_TASKS = 7;

let selectedId = null;
let timelineSeq = 0;

function numberOf(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function tasksOf(mission) {
  return Array.isArray(mission.tasks) ? mission.tasks : [];
}

function projectName(mission) {
  const project = mission.project || {};
  return project.name || project.key || "";
}

function figures(mission) {
  const tasks = tasksOf(mission);
  const done = tasks.filter((task) => DONE_STATUSES.includes(task.status));
  const firstTry = done.filter((task) => {
    const attempts = numberOf(task.attempts);
    return attempts === null || attempts <= 1;
  });
  const cache = numberOf(mission.cache_ratio);
  return {
    done: done.length,
    total: tasks.length,
    firstTry: firstTry.length,
    tps: numberOf(mission.tok_s),
    tpsLocal: numberOf(mission.tok_s_local),
    tpsCloud: numberOf(mission.tok_s_cloud),
    cache: cache === null ? null : Math.round(cache * 100),
    thinking: numberOf(mission.thinking_share) === null ? null : Math.round(numberOf(mission.thinking_share) * 100),
  };
}

function columnsOf(mission) {
  const columns = { wait: [], prog: [], done: [] };
  for (const task of tasksOf(mission)) {
    if (DONE_STATUSES.includes(task.status)) columns.done.push(task);
    else if (task.status === "running") columns.prog.push(task);
    else columns.wait.push(task);
  }
  return columns;
}

function toMs(ts) {
  if (typeof ts === "number") return ts;
  const ms = Date.parse(ts);
  return Number.isFinite(ms) ? ms : NaN;
}

function round1(n) {
  return Math.round(n * 10) / 10;
}

function chartPoints(accepted, total) {
  const times = (Array.isArray(accepted) ? accepted : [])
    .map((entry) => toMs(entry && entry.ts))
    .filter((ms) => Number.isFinite(ms))
    .sort((a, b) => a - b);
  if (times.length === 0) return [];
  const { width, height, pad } = CHART;
  const span = times[times.length - 1] - times[0];
  const top = Math.max(total || 0, times.length, 1);
  return times.map((ms, i) => ({
    x: round1(span > 0 ? pad + ((width - 2 * pad) * (ms - times[0])) / span : width / 2),
    y: round1(height - pad - ((height - 2 * pad) * (i + 1)) / top),
  }));
}

function chartSvg(points) {
  if (points.length === 0) return "";
  const { width, height } = CHART;
  const line = points.map((p) => p.x + "," + p.y).join(" ");
  return '<svg viewBox="0 0 ' + width + " " + height + '" width="100%" height="' + height + '">'
    + '<polyline points="' + line + '" fill="none" stroke="var(--acc)" stroke-width="2"/></svg>';
}

async function fetchTimeline(id) {
  const res = await fetch("/api/board/missions/" + encodeURIComponent(id) + "/timeline", { cache: "no-store" });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res.json();
}

function note(ctx, key) {
  return ctx.el("p", { class: "bv-note", text: ctx.tr(key) });
}

function fillTimeline(ctx, mission, nodes) {
  const token = ++timelineSeq;
  const load = ctx.loadTimeline || fetchTimeline;
  return Promise.resolve()
    .then(() => load(mission.mission_id))
    .then((payload) => {
      if (token !== timelineSeq) return;
      const timeline = payload || {};
      const accepted = Array.isArray(timeline.accepted) ? timeline.accepted : [];
      const points = chartPoints(accepted, tasksOf(mission).length);
      if (points.length) nodes.chart.innerHTML = chartSvg(points);
      else nodes.chart.replaceChildren(note(ctx, "spr.empty"));

      const obstacles = Array.isArray(timeline.obstacles) ? timeline.obstacles : [];
      if (obstacles.length === 0) {
        nodes.obstacles.replaceChildren(note(ctx, "spr.noobst"));
        return;
      }
      nodes.obstacles.replaceChildren(...obstacles.map((o) => {
        const kindKey = "imp." + (o.kind || "");
        const labelKey = KNOWN_OBSTACLE_KINDS.has(o.kind || "") ? kindKey : "imp.t";
        return ctx.el("div", { class: "imp-item" }, [
          ctx.el("b", { text: ctx.tr(labelKey) }),
          ctx.el("span", { class: "mono", text: " " + (o.task || "") }),
          ctx.el("span", { text: o.detail || undefined }),
        ]);
      }));
    }, () => {
      if (token !== timelineSeq) return;
      nodes.chart.replaceChildren(note(ctx, "board.offline"));
      nodes.obstacles.replaceChildren(note(ctx, "board.offline"));
    });
}

function backlogCard(ctx, mission, index, current) {
  const { el, tr } = ctx;
  const id = mission.mission_id;
  const name = mission.name || id;
  const state = STATE_OF_GROUP[mission.state_group];
  return el("div", {
    class: "bl bv-card" + (id === current.mission_id ? " sel" : ""),
    "data-id": id,
    "data-project": (mission.project && mission.project.key) || "",
    "data-search": (name + " " + id).toLowerCase(),
  }, [
    el("b", { text: (index + 1) + ". " + name }),
    el("div", { class: "mono", text: projectName(mission) + " · " + tr("st." + state) }),
  ]);
}

function sprintColumn(ctx, title, tasks, planned) {
  const tone = title === "spr.wait" ? "gr" : title === "spr.prog" ? "y" : "g";
  const { el, tr } = ctx;
  return el("div", {}, [
    el("div", { class: "sub", text: tr(title) + " · " + tasks.length }),
    ...tasks.slice(0, SHOWN_TASKS).map((task) => el("div", { class: "spr-task n " + tone }, [
      el("b", { text: task.id || "" }),
      el("span", { text: " " + (task.title || "") }),
    ])),
    planned === false && title === "spr.wait" ? el("div", { class: "mono", text: tr("spr.noplan") }) : null,
    tasks.length > SHOWN_TASKS
      ? el("div", { class: "mono", text: tr("more", { n: tasks.length - SHOWN_TASKS }) })
      : null,
  ]);
}

// local and cloud speed side by side when both providers took part, else the one speed
function speedText(tr, figs) {
  if (figs.tpsLocal && figs.tpsCloud) {
    return tr("s.tps_local", { n: Math.round(figs.tpsLocal) }) + " " + tr("s.tps_cloud", { n: Math.round(figs.tpsCloud) });
  }
  return figs.tps === null ? "—" : tr("s.tps", { n: Math.round(figs.tps) });
}

function kpi(ctx, label, value) {
  return ctx.el("div", { class: "kp" }, [
    ctx.el("span", { text: ctx.tr(label) }),
    ctx.el("b", { text: value }),
  ]);
}

function render(body, ctx) {
  const { el, tr } = ctx;
  if (!ctx.data) {
    body.append(el("p", { class: "bv-note", text: tr("board.offline") }));
    return Promise.resolve();
  }
  body.append(el("p", { class: "bv-note", text: tr("spr.hint") }));
  // running first, then the queue in its order (the summary lists the missions alphabetically)
  const rank = (m) => (m.state_group === "running" ? -1 : (numberOf(m.position) ?? 1e9));
  const backlog = ctx.missions.filter((m) => STATE_OF_GROUP[m.state_group] !== undefined)
    .sort((a, b) => rank(a) - rank(b));
  if (backlog.length === 0) {
    body.append(note(ctx, "grid.empty"));
    return Promise.resolve();
  }
  const current = backlog.find((m) => m.mission_id === selectedId) || backlog[0];
  const figs = figures(current);
  const cols = columnsOf(current);
  const chart = el("div", { class: "spr-chart-body" });
  const obstacles = el("div", { class: "spr-obst" });

  const backlogCol = el("div", { class: "col" }, [
    el("h3", { text: tr("spr.back") }),
    ...backlog.map((m, i) => backlogCard(ctx, m, i, current)),
  ]);
  const sprintCol = el("div", { class: "col" }, [
    el("h3", { text: (current.name || current.mission_id) + " · " + tr("of", { a: figs.done, b: figs.total }) }),
    el("div", { class: "sb" }, [
      sprintColumn(ctx, "spr.wait", cols.wait, figs.total > 0),
      sprintColumn(ctx, "spr.prog", cols.prog, true),
      sprintColumn(ctx, "spr.done", cols.done, true),
    ]),
  ]);
  const side = el("div", {}, [
    kpi(ctx, "kpi.done", tr("of", { a: figs.done, b: figs.total })),
    kpi(ctx, "kpi.first", tr("of", { a: figs.firstTry, b: figs.done })),
    kpi(ctx, "kpi.speed", [
      speedText(tr, figs),
      figs.cache === null ? "—" : tr("s.cache", { n: figs.cache }),
      figs.thinking === null ? "—" : tr("s.think", { n: figs.thinking }),
    ].join(" · ")),
    el("div", { class: "kp" }, [el("span", { text: tr("kpi.chart") }), chart]),
    el("div", { class: "imp" }, [el("b", { text: tr("imp.t") }), obstacles]),
  ]);
  body.append(el("div", { class: "spr" }, [backlogCol, sprintCol, side]));
  return fillTimeline(ctx, current, { chart, obstacles });
}

function select(card) {
  selectedId = card.dataset.id;
}

function open(id) {
  location.hash = "#board/" + encodeURIComponent(id);
}

const api = { render, select, open, figures, columnsOf, chartPoints, chartSvg };

if (typeof module !== "undefined" && module.exports) {
  module.exports = api;
} else {
  globalThis.BoardViewSprint = api;
  if (globalThis.BoardViews) globalThis.BoardViews.register("spr", { render, select, open });
}
})();
