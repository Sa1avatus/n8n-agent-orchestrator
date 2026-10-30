"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const SPRINT = path.join(__dirname, "..", "src", "claude_runner", "board_view_sprint.js");

function makeNode(tag) {
  const node = {
    tag,
    children: [],
    attrs: {},
    dataset: {},
    classes: new Set(),
    listeners: {},
    textContent: "",
    classList: {
      add: (c) => node.classes.add(c),
      remove: (c) => node.classes.delete(c),
      contains: (c) => node.classes.has(c),
    },
    setAttribute(k, v) {
      node.attrs[k] = String(v);
      if (k.startsWith("data-")) {
        const key = k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
        node.dataset[key] = String(v);
      }
    },
    getAttribute: (k) => (k in node.attrs ? node.attrs[k] : null),
    append: (...nodes) => node.children.push(...nodes),
    replaceChildren: (...nodes) => { node.children = nodes; },
    addEventListener(type, fn) {
      (node.listeners[type] = node.listeners[type] || []).push(fn);
    },
    dispatch(type, extra) {
      const event = { stopped: false, stopPropagation() { event.stopped = true; }, ...(extra || {}) };
      for (const fn of node.listeners[type] || []) fn(event);
      return event;
    },
  };
  return node;
}

function makeEl(counter) {
  return (tag, attrs, children) => {
    counter.calls += 1;
    const node = makeNode(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") {
        for (const c of String(value).split(" ")) if (c) node.classes.add(c);
      } else if (key === "text") {
        node.textContent = String(value);
      } else {
        node.setAttribute(key, value === true ? "" : value);
      }
    }
    for (const child of [].concat(children || [])) if (child) node.children.push(child);
    return node;
  };
}

function walk(node, pred, out = []) {
  if (pred(node)) out.push(node);
  for (const child of node.children) walk(child, pred, out);
  return out;
}

function byClass(node, cls) {
  return walk(node, (n) => n.classes && n.classes.has(cls));
}

function freshSprint() {
  delete require.cache[require.resolve(SPRINT)];
  return require(SPRINT);
}

function renderWith(views, overrides, timeline) {
  const counter = { calls: 0 };
  const ctx = Object.assign({
    data: { missions: [], projects: [] },
    missions: [],
    projects: [],
    project: null,
    query: "",
    el: makeEl(counter),
    card: null,
    tr: (key) => key,
    loadTimeline: () => Promise.resolve(timeline || { accepted: [], obstacles: [] }),
  }, overrides);
  const body = makeNode("div");
  const done = views.render(body, ctx);
  return { body, ctx, counter, done };
}

function notes(body) {
  return byClass(body, "bv-note").map((n) => n.textContent);
}

const MISSIONS = [
  {
    mission_id: "m1", name: "Bravo", state_group: "running", hours: 2, attempts: 3, cache_ratio: 0.5,
    cost: 1.5, tok_s: 20, project: { key: "p2", name: "Two" },
    tasks: [
      { id: "T1", title: "Parse", status: "accepted", attempts: 1, deps: [] },
      { id: "T2", title: "Render", status: "running", attempts: 2, deps: ["T1"] },
      { id: "T3", title: "Ship", status: "waiting", attempts: 0, deps: ["T2"] },
    ],
  },
  {
    mission_id: "m2", name: "Alpha", state_group: "queued", hours: null, attempts: 1, cache_ratio: null,
    cost: 0.25, tok_s: null, project: { key: "p1", name: "One" }, tasks: [],
  },
  {
    mission_id: "m3", name: "Charlie", state_group: "completed", hours: 4, attempts: 3, cache_ratio: 0.9,
    cost: 0, tok_s: 5, project: { key: "p1", name: "One" },
    tasks: [{ id: "T9", title: "Done", status: "accepted", attempts: 1, deps: [] }],
  },
];

test("figures count done tasks, first-attempt acceptance, speed and cache", () => {
  const views = freshSprint();
  assert.deepEqual(views.figures(MISSIONS[0]), {
    done: 1, total: 3, firstTry: 1, tps: 20, tpsLocal: null, tpsCloud: null, cache: 50, thinking: null,
  });
  assert.deepEqual(views.figures(MISSIONS[1]), {
    done: 0, total: 0, firstTry: 0, tps: null, tpsLocal: null, tpsCloud: null, cache: null, thinking: null,
  });
});

test("figures keep the local and the cloud speed apart", () => {
  const views = freshSprint();
  const figs = views.figures({ tasks: [], tok_s: 40, tok_s_local: 12, tok_s_cloud: 56 });
  assert.equal(figs.tpsLocal, 12);
  assert.equal(figs.tpsCloud, 56);
});

test("a retried acceptance is done but not accepted on the first attempt", () => {
  const views = freshSprint();
  const figs = views.figures({
    tasks: [{ id: "T1", status: "accepted_retry", attempts: 2 }, { id: "T2", status: "accepted", attempts: 1 }],
  });
  assert.equal(figs.done, 2);
  assert.equal(figs.firstTry, 1);
});

test("columns split tasks into waiting, in progress and done", () => {
  const views = freshSprint();
  const cols = views.columnsOf(MISSIONS[0]);
  assert.deepEqual(cols.done.map((t) => t.id), ["T1"]);
  assert.deepEqual(cols.prog.map((t) => t.id), ["T2"]);
  assert.deepEqual(cols.wait.map((t) => t.id), ["T3"]);
});

test("chart points follow the accepted timeline in time order", () => {
  const views = freshSprint();
  const accepted = [
    { task: "T3", ts: "2026-10-01T12:00:00Z" },
    { task: "T1", ts: "2026-10-01T10:00:00Z" },
    { task: "T2", ts: "2026-10-01T11:00:00Z" },
  ];
  assert.deepEqual(views.chartPoints(accepted, 4), [
    { x: 4, y: 43 },
    { x: 120, y: 30 },
    { x: 236, y: 17 },
  ]);
});

test("chart points skip entries without a usable timestamp and are empty without accepted tasks", () => {
  const views = freshSprint();
  assert.deepEqual(views.chartPoints([], 3), []);
  assert.deepEqual(views.chartPoints(undefined, 3), []);
  assert.deepEqual(views.chartPoints([{ task: "T1", ts: "not a date" }], 3), []);
});

test("chart svg holds one polyline with the computed points and nothing for an empty list", () => {
  const views = freshSprint();
  const points = views.chartPoints([{ task: "T1", ts: "2026-10-01T10:00:00Z" }], 2);
  const svg = views.chartSvg(points);
  assert.ok(svg.includes('<polyline points="' + points[0].x + "," + points[0].y + '"'));
  assert.equal(views.chartSvg([]), "");
});

test("render lists queued and running missions only, in summary order", () => {
  const views = freshSprint();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const cards = byClass(body, "bv-card").map((c) => c.dataset.id);
  assert.deepEqual(cards, ["m1", "m2"]);
  const first = byClass(body, "bv-card")[0];
  assert.equal(first.dataset.project, "p2");
  assert.equal(first.dataset.search, "bravo m1");
  assert.ok(first.classes.has("sel"));
});

test("a selected sprint is drawn with its tasks in the three columns", () => {
  const views = freshSprint();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const columns = byClass(body, "sb")[0].children;
  const ids = columns.map((col) => byClass(col, "spr-task").map((t) => t.children[0].textContent));
  assert.deepEqual(ids, [["T3"], ["T2"], ["T1"]]);
});

test("a timeline with no accepted tasks and no obstacles shows empty notes, not an error", async () => {
  const views = freshSprint();
  const { body, done } = renderWith(views, { data: {}, missions: MISSIONS }, { accepted: [], obstacles: [] });
  await done;
  assert.deepEqual(notes(body).filter((n) => n.startsWith("spr.")), ["spr.hint", "spr.empty", "spr.noobst"]);
  assert.ok(!notes(body).includes("board.offline"));
  const chart = byClass(body, "spr-chart-body")[0];
  assert.equal(chart.children.length, 1);
  assert.equal(chart.children[0].textContent, "spr.empty");
});

test("the accepted timeline fills the chart as an svg", async () => {
  const views = freshSprint();
  const accepted = [{ task: "T1", ts: "2026-10-01T10:00:00Z" }];
  const { body, done } = renderWith(views, { data: {}, missions: MISSIONS }, { accepted, obstacles: [] });
  await done;
  const chart = byClass(body, "spr-chart-body")[0];
  assert.equal(chart.innerHTML, views.chartSvg(views.chartPoints(accepted, 3)));
});

test("obstacles render kind, task and detail as text, never as markup", async () => {
  const views = freshSprint();
  const hostile = '<img src=x onerror="alert(1)"><b>x</b>';
  const obstacles = [
    { kind: "rejected_3x", task: "T2", ts: "2026-10-01T10:00:00Z", detail: hostile },
    { kind: "timeout", task: "T3", ts: "2026-10-01T11:00:00Z", detail: "" },
  ];
  const { body, done } = renderWith(views, { data: {}, missions: MISSIONS }, { accepted: [], obstacles });
  await done;
  const list = byClass(body, "spr-obst")[0];
  assert.equal(list.children.length, 2);
  assert.equal(list.children[0].children[0].textContent, "imp.rejected_3x");
  assert.equal(list.children[0].children[1].textContent, " T2");
  assert.equal(walk(body, (n) => n.textContent === hostile).length, 1);
  assert.equal(walk(body, (n) => n.tag === "img").length, 0);
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "x").length, 0);
});

test("a failed timeline request shows the offline note in the chart and obstacles", async () => {
  const views = freshSprint();
  const { body, done } = renderWith(views, {
    data: {},
    missions: MISSIONS,
    loadTimeline: () => Promise.reject(new Error("HTTP 500")),
  });
  await done;
  assert.equal(byClass(byClass(body, "spr-chart-body")[0], "bv-note")[0].textContent, "board.offline");
  assert.equal(byClass(byClass(body, "spr-obst")[0], "bv-note")[0].textContent, "board.offline");
});

test("a late timeline response for a previous selection is ignored", async () => {
  const views = freshSprint();
  let release;
  const slow = new Promise((resolve) => { release = resolve; });
  const first = renderWith(views, { data: {}, missions: MISSIONS, loadTimeline: () => slow });
  const second = renderWith(views, { data: {}, missions: MISSIONS });
  await second.done;
  release({ accepted: [{ task: "T1", ts: "2026-10-01T10:00:00Z" }], obstacles: [] });
  await first.done;
  assert.equal(byClass(first.body, "spr-chart-body")[0].innerHTML, undefined);
});

test("clicking a backlog card highlights it without redrawing", () => {
  const views = freshSprint();
  const { body, counter } = renderWith(views, { data: {}, missions: MISSIONS });
  const cards = byClass(body, "bv-card");
  const before = counter.calls;
  const childrenBefore = body.children.slice();
  views.select(cards[1]);
  assert.equal(counter.calls, before, "select does not build any element");
  assert.deepEqual(body.children, childrenBefore);
  const again = renderWith(views, { data: {}, missions: MISSIONS }).body;
  assert.deepEqual(byClass(again, "bv-card").filter((c) => c.classes.has("sel")).map((c) => c.dataset.id), ["m2"]);
  assert.equal(byClass(again, "spr-task").length, 0);
});

test("double-clicking a mission opens #board/<mission_id>", () => {
  globalThis.location = { hash: "" };
  const views = freshSprint();
  views.open("m1");
  assert.equal(globalThis.location.hash, "#board/m1");
  views.open("a b/c");
  assert.equal(globalThis.location.hash, "#board/a%20b%2Fc");
});

test("hostile mission and project names are shown as text, never as markup", () => {
  const views = freshSprint();
  const hostile = '<img src=x onerror="alert(1)"><b>x</b>';
  const { body } = renderWith(views, {
    data: {},
    missions: [{ mission_id: "h1", name: hostile, state_group: "running", project: { key: "h", name: hostile }, tasks: [] }],
  });
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "1. " + hostile).length, 1);
  assert.equal(walk(body, (n) => n.tag === "img").length, 0);
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "x").length, 0);
});

test("missing data shows the offline note only", () => {
  const views = freshSprint();
  const { body } = renderWith(views, { data: null, missions: [] });
  assert.deepEqual(notes(body), ["board.offline"]);
});

test("no queued or running missions shows the empty note", () => {
  const views = freshSprint();
  const { body } = renderWith(views, { data: {}, missions: [MISSIONS[2]] });
  assert.deepEqual(notes(body), ["spr.hint", "grid.empty"]);
  assert.equal(byClass(body, "spr").length, 0);
});

test("an unknown obstacle kind falls back to the generic label, not a raw key", async () => {
  const views = freshSprint();
  const obstacles = [{ kind: "unknown_kind", task: "T2", ts: "2026-10-01T10:00:00Z", detail: "" }];
  const { body, done } = renderWith(views, { data: {}, missions: MISSIONS }, { accepted: [], obstacles });
  await done;
  const list = byClass(body, "spr-obst")[0];
  assert.equal(list.children.length, 1);
  assert.equal(list.children[0].children[0].textContent, "imp.t");
  assert.equal(walk(body, (n) => n.textContent.startsWith("imp.unknown_kind")).length, 0);
});

test("every key the view uses exists in board_i18n_en.json", () => {
  const fs = require("node:fs");
  const dict = JSON.parse(fs.readFileSync(
    path.join(__dirname, "..", "src", "claude_runner", "board_i18n_en.json"), "utf8"));
  for (const key of ["kpi.first", "kpi.chart", "spr.empty", "spr.noobst",
                     "imp.rejected_3x", "imp.timeout", "imp.interrupted"]) {
    assert.ok(key in dict, "missing i18n key: " + key);
  }
});
