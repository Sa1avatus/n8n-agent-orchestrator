"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const views = require(path.join(__dirname, "..", "src", "claude_runner", "board_view_projects.js"));

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
      toggle: (c, force) => {
        const on = force === undefined ? !node.classes.has(c) : !!force;
        if (on) node.classes.add(c);
        else node.classes.delete(c);
        return on;
      },
      contains: (c) => node.classes.has(c),
    },
    setAttribute(k, v) {
      node.attrs[k] = String(v);
      if (k.startsWith("data-")) node.dataset[k.slice(5)] = String(v);
    },
    getAttribute: (k) => (k in node.attrs ? node.attrs[k] : null),
    append: (...nodes) => node.children.push(...nodes),
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

const MISSIONS = [
  {
    mission_id: "m1", name: "Alpha", state_group: "running", hours: 2,
    project: { key: "p1", name: "Proj One", rule: "rule-a" },
    tasks: [{ id: "T1", status: "accepted" }, { id: "T2", status: "accepted_retry" }, { id: "T3", status: "running" }],
    tokens: 1500000, cost: 1.5,
  },
  {
    mission_id: "m2", name: "Beta", state_group: "queued", hours: 1,
    project: { key: "p1", name: "Proj One", rule: "rule-a" },
    tasks: [], tokens: 2000, cost: 0.25,
  },
  {
    mission_id: "m3", name: "Gamma", state_group: "completed", hours: 4,
    project: { key: "p2", name: "Proj Two", rule: "rule-b" },
    tasks: [{ id: "T9", status: "accepted" }], tokens: 0, cost: 0,
  },
];
const PROJECTS = [
  { key: "p1", name: "Proj One", rule: "rule-a" },
  { key: "p2", name: "Proj Two", rule: "rule-b" },
];

function renderWith(overrides) {
  const { ctx, counter } = ctxFor(overrides);
  const body = makeNode("div");
  views.render(body, ctx);
  return { body, ctx, counter };
}

test("render draws one card per project with mission rows", () => {
  const { body } = renderWith({ data: {}, missions: MISSIONS, projects: PROJECTS });
  const cards = byClass(body, "pr");
  assert.equal(cards.length, 2);
  assert.equal(cards[0].dataset.id, "p1");
  assert.equal(cards[0].dataset.project, "p1");
  assert.equal(cards[0].dataset.search, "proj one p1");
  assert.equal(byClass(cards[0], "kc").length, 2);
  assert.equal(byClass(cards[1], "kc").length, 1);
  const pills = byClass(cards[0], "pill").map((n) => n.textContent);
  assert.deepEqual(pills, ["st.run", "st.q"]);
});

test("hostile project name is shown as text, never as markup", () => {
  const hostile = '<img src=x onerror="alert(1)"><b>x</b>';
  const { body } = renderWith({
    data: {},
    missions: [{ mission_id: "m1", name: "A", state_group: "running", project: { key: "h", name: hostile }, tasks: [] }],
    projects: [{ key: "h", name: hostile }],
  });
  const title = walk(body, (n) => n.tag === "b" && n.textContent === hostile);
  assert.equal(title.length, 1);
  assert.equal(title[0].children.length, 0);
  assert.equal(walk(body, (n) => n.tag === "img").length, 0);
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "x").length, 0);
});

test("clicking a mission row selects it without redrawing", () => {
  const { body, counter } = renderWith({ data: {}, missions: MISSIONS, projects: PROJECTS });
  const rows = byClass(body, "kc");
  const before = counter.calls;
  const childrenBefore = body.children.slice();
  const event = rows[0].dispatch("click");
  assert.equal(event.stopped, true, "row click must not reach the shared card handler");
  assert.equal(rows[0].classes.has("sel"), true);
  rows[1].dispatch("click");
  assert.equal(rows[0].classes.has("sel"), false);
  assert.equal(rows[1].classes.has("sel"), true);
  assert.equal(counter.calls, before, "no element was created, so nothing was redrawn");
  assert.deepEqual(body.children, childrenBefore);
});

test("double-clicking a mission row opens the mission", () => {
  globalThis.location = { hash: "" };
  const { body } = renderWith({ data: {}, missions: MISSIONS, projects: PROJECTS });
  const row = byClass(body, "kc")[0];
  const event = row.dispatch("dblclick");
  assert.equal(event.stopped, true);
  assert.equal(globalThis.location.hash, "#board/m1");
});

test("double-clicking a project opens the kanban filtered by it", () => {
  globalThis.location = { hash: "" };
  views.open("p1");
  assert.equal(globalThis.location.hash, "#board/view/kan?project=p1");
  views.open("a b");
  assert.equal(globalThis.location.hash, "#board/view/kan?project=a%20b");
});

test("arrow toggles the open state of a project card", () => {
  const { body } = renderWith({
    data: {},
    missions: [{ mission_id: "m9", name: "Z", state_group: "queued", project: { key: "arrow-p" }, tasks: [] }],
    projects: [{ key: "arrow-p", name: "Arrow" }],
  });
  const card = byClass(body, "pr")[0];
  const arrow = byClass(card, "arr")[0];
  assert.equal(card.classes.has("open"), false);
  arrow.dispatch("click");
  assert.equal(card.classes.has("open"), true);
  assert.equal(arrow.getAttribute("aria-expanded"), "true");
  arrow.dispatch("click");
  assert.equal(card.classes.has("open"), false);
});

test("no projects shows the hint note", () => {
  const { body } = renderWith({ data: {}, missions: [], projects: [] });
  const notes = byClass(body, "bv-note");
  assert.equal(notes.length, 1);
  assert.equal(notes[0].textContent, "proj.hint");
  assert.equal(byClass(body, "pr").length, 0);
});

test("missing data shows the offline note", () => {
  const { body } = renderWith({ data: null, missions: [], projects: [] });
  const notes = byClass(body, "bv-note");
  assert.equal(notes.length, 1);
  assert.equal(notes[0].textContent, "board.offline");
});

test("labels come from the translator passed in the context", () => {
  const tr = (key, params) => "X:" + key + (params ? JSON.stringify(params) : "");
  const { body } = renderWith({ data: null, tr });
  assert.equal(byClass(body, "bv-note")[0].textContent, "X:board.offline");
  const withData = renderWith({ data: {}, missions: MISSIONS, projects: PROJECTS, tr });
  const pill = byClass(withData.body, "pill")[0];
  assert.equal(pill.textContent, "X:st.run");
});

test("state and task helpers map the summary fields", () => {
  assert.equal(views.stateOf({ state_group: "running" }), "run");
  assert.equal(views.stateOf({ state_group: "not_completed" }), "bad");
  assert.equal(views.stateOf({ state_group: "disabled" }), "off");
  assert.equal(views.stateOf({ state_group: "unknown" }), "off");
  assert.deepEqual(views.taskCounts(MISSIONS[0]), { done: 2, total: 3 });
  assert.equal(views.formatTokens(1500000), "1.5M");
  assert.equal(views.formatTokens(2000), "2.0k");
});
