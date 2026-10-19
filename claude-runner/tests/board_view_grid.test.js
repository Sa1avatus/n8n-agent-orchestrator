"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const GRID = path.join(__dirname, "..", "src", "claude_runner", "board_view_grid.js");

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

function freshGrid() {
  delete require.cache[require.resolve(GRID)];
  return require(GRID);
}

function ctxFor(overrides) {
  const counter = { calls: 0 };
  return {
    counter,
    ctx: Object.assign({
      data: { missions: [], projects: [] },
      missions: [],
      projects: [],
      project: null,
      query: "",
      el: makeEl(counter),
      card: null,
      tr: (key) => key,
    }, overrides),
  };
}

function renderWith(views, overrides) {
  const { ctx, counter } = ctxFor(overrides);
  const body = makeNode("div");
  views.render(body, ctx);
  return { body, ctx, counter };
}

function headers(body) {
  return byClass(body, "g")[0].children[0].children;
}

function bodyRows(body) {
  return byClass(body, "g")[0].children.slice(1);
}

function rowIds(body) {
  return bodyRows(body).map((r) => r.dataset.id);
}

const MISSIONS = [
  {
    mission_id: "m1", name: "Bravo", state_group: "running", hours: 2, attempts: 3, cache_ratio: 0.5,
    cost: 1.5, tok_s: 20, project: { key: "p2", name: "Two" },
    tasks: [{ id: "T1", status: "accepted" }, { id: "T2", status: "running" }],
  },
  {
    mission_id: "m2", name: "Alpha", state_group: "queued", hours: null, attempts: 1, cache_ratio: null,
    cost: 0.25, tok_s: null, project: { key: "p1", name: "One" }, tasks: [],
  },
  {
    mission_id: "m3", name: "Charlie", state_group: "completed", hours: 4, attempts: 3, cache_ratio: 0.9,
    cost: 0, tok_s: 5, project: { key: "p1", name: "One" },
    tasks: [{ id: "T9", status: "accepted" }, { id: "T10", status: "accepted_retry" }],
  },
  {
    mission_id: "m4", name: "Delta", state_group: "not_completed", hours: 1, attempts: 2, cache_ratio: 0.1,
    cost: 3, tok_s: 9, project: { key: "p2", name: "Two" }, tasks: [],
  },
  {
    mission_id: "m5", name: "Echo", state_group: "disabled", hours: 0, attempts: 0, cache_ratio: 0,
    cost: 0, tok_s: 0, project: null, tasks: [],
  },
];

test("render draws a header row and one body row per mission", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  assert.equal(headers(body).length, 13);
  assert.equal(bodyRows(body).length, 5);
  assert.equal(headers(body)[0].dataset.sort, "num");
  const first = headers(body)[1];
  assert.equal(first.textContent, "h.mission ↑");
  assert.equal(first.dataset.sort, "name");
});

test("default sort is by mission name, ascending", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  assert.deepEqual(rowIds(body), ["m2", "m1", "m3", "m4", "m5"]);
});

test("clicking a header sorts by that column and flips the direction", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const attempts = headers(body).find((h) => h.dataset.sort === "att");
  attempts.dispatch("click");
  const sortedAsc = rowIds(body);
  assert.equal(sortedAsc[0], "m5");
  const attemptsAgain = headers(body).find((h) => h.dataset.sort === "att");
  attemptsAgain.dispatch("click");
  const sortedDesc = rowIds(body);
  assert.equal(sortedDesc[sortedDesc.length - 1], "m5");
  assert.equal(headers(body).find((h) => h.dataset.sort === "att").getAttribute("aria-sort"), "descending");
});

test("sort is stable: equal values keep the summary order in both directions", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const attempts = () => headers(body).find((h) => h.dataset.sort === "att");
  attempts().dispatch("click");
  const asc = rowIds(body);
  assert.deepEqual(asc.filter((id) => ["m1", "m3"].includes(id)), ["m1", "m3"]);
  attempts().dispatch("click");
  const desc = rowIds(body);
  assert.deepEqual(desc.filter((id) => ["m1", "m3"].includes(id)), ["m1", "m3"]);
});

test("missing numbers sort last in both directions", () => {
  const views = freshGrid();
  const sorted = views.sortMissions(MISSIONS, "time", 1);
  assert.equal(sorted[sorted.length - 1].mission_id, "m2");
  const desc = views.sortMissions(MISSIONS, "time", -1);
  assert.equal(desc[desc.length - 1].mission_id, "m2");
});

test("sortMissions keys on the column given and does not mutate the input", () => {
  const views = freshGrid();
  const input = MISSIONS.slice();
  const byCost = views.sortMissions(input, "cost", 1).map((m) => m.mission_id);
  assert.deepEqual(byCost, ["m3", "m5", "m2", "m1", "m4"]);
  assert.deepEqual(input, MISSIONS);
  const byState = views.sortMissions(input, "state", 1).map((m) => m.mission_id);
  assert.deepEqual(byState, ["m1", "m4", "m2", "m3", "m5"]);
});

test("segmented task bar has one segment per task with its kind", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const row = bodyRows(body).find((r) => r.dataset.id === "m1");
  const segs = byClass(row, "seg");
  assert.equal(segs.length, 2);
  assert.ok(segs[0].classes.has("seg-done"));
  assert.ok(segs[1].classes.has("seg-run"));
  assert.equal(segs[0].attrs.style, "width:50%");
  const done = bodyRows(body).find((r) => r.dataset.id === "m3");
  assert.ok(byClass(done, "seg-done").length === 2);
});

test("clicking a row highlights it without redrawing", () => {
  const views = freshGrid();
  const { body, counter } = renderWith(views, { data: {}, missions: MISSIONS });
  const rows = bodyRows(body);
  const before = counter.calls;
  const childrenBefore = body.children.slice();
  const row = rows.find((r) => r.dataset.id === "m1");
  views.select(row);
  assert.equal(counter.calls, before, "select does not build any element");
  assert.deepEqual(body.children, childrenBefore);
  headers(body).find((h) => h.dataset.sort === "att").dispatch("click");
  const again = bodyRows(body).find((r) => r.dataset.id === "m1");
  assert.ok(again.classes.has("sel"));
  assert.equal(bodyRows(body).filter((r) => r.classes.has("sel")).length, 1);
});

test("double-clicking a row opens #board/<mission_id>", () => {
  globalThis.location = { hash: "" };
  const views = freshGrid();
  views.open("m1");
  assert.equal(globalThis.location.hash, "#board/m1");
  views.open("a b/c");
  assert.equal(globalThis.location.hash, "#board/a%20b%2Fc");
});

test("hostile mission and project names are shown as text, never as markup", () => {
  const views = freshGrid();
  const hostile = '<img src=x onerror="alert(1)"><b>x</b>';
  const { body } = renderWith(views, {
    data: {},
    missions: [{ mission_id: "h1", name: hostile, state_group: "running", project: { key: "h", name: hostile }, tasks: [] }],
  });
  const cell = walk(body, (n) => n.tag === "b" && n.textContent === hostile);
  assert.equal(cell.length, 1);
  assert.equal(cell[0].children.length, 0);
  assert.equal(walk(body, (n) => n.tag === "img").length, 0);
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "x").length, 0);
  const projectCells = walk(body, (n) => n.tag === "td" && n.textContent === hostile);
  assert.equal(projectCells.length, 1);
});

test("empty mission list shows the empty note and no table", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: {}, missions: [] });
  const notes = byClass(body, "bv-note").map((n) => n.textContent);
  assert.deepEqual(notes, ["grid.hint", "grid.empty"]);
  assert.equal(byClass(body, "g").length, 0);
});

test("missing data shows the offline note", () => {
  const views = freshGrid();
  const { body } = renderWith(views, { data: null, missions: [] });
  const notes = byClass(body, "bv-note");
  assert.equal(notes.length, 1);
  assert.equal(notes[0].textContent, "board.offline");
});

test("labels come from the translator passed in the context", () => {
  const views = freshGrid();
  const tr = (key) => "X:" + key;
  const { body } = renderWith(views, { data: {}, missions: MISSIONS, tr });
  assert.equal(headers(body)[3].textContent.startsWith("X:h.project"), true);
  assert.equal(byClass(body, "pill")[0].textContent, "X:st.q");
});

test("the grid sorts by mission id and by start date", () => {
  const views = freshGrid();
  const missions = [
    { mission_id: "b-two", name: "A", started_at: "2026-10-12T10:00:00Z", tasks: [] },
    { mission_id: "a-one", name: "B", started_at: "2026-10-18T10:00:00Z", tasks: [] },
    { mission_id: "c-three", name: "C", tasks: [] },
  ];
  const ids = (key, dir) => views.sortMissions(missions, key, dir).map((m) => m.mission_id);
  assert.deepEqual(ids("id", 1), ["a-one", "b-two", "c-three"]);
  assert.deepEqual(ids("id", -1), ["c-three", "b-two", "a-one"]);
  assert.deepEqual(ids("started", 1), ["b-two", "a-one", "c-three"], "no date goes last");
  assert.deepEqual(ids("started", -1), ["a-one", "b-two", "c-three"], "no date goes last");
});

test("the grid shows the table row number and sorts by it", () => {
  const views = freshGrid();
  const missions = [
    { mission_id: "x", name: "A", number: 12, tasks: [] },
    { mission_id: "y", name: "B", number: 3, tasks: [] },
    { mission_id: "z", name: "C", tasks: [] },
  ];
  const ids = (dir) => views.sortMissions(missions, "num", dir).map((m) => m.mission_id);
  assert.deepEqual(ids(1), ["y", "x", "z"]);
  assert.deepEqual(ids(-1), ["x", "y", "z"]);
  const { body } = renderWith(views, { data: {}, missions });
  assert.equal(bodyRows(body)[2].children[0].textContent, "—");
});
