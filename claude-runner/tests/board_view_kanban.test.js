"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
require(path.join(__dirname, "i18n_stub.js"));
const MODULES = ["board_view_kanban.js", "board_dnd.js", "board_actions.js", "board_logic.js"];

// --- a tiny DOM node: attributes, dataset, classes, listeners, children ---

function attrValue(node, name) {
  if (name.startsWith("data-")) {
    const key = name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    if (node.dataset && node.dataset[key] !== undefined) return node.dataset[key];
    if (node.attrs && node.attrs[name] !== undefined) return node.attrs[name];
    return undefined;
  }
  if (node.attrs && node.attrs[name] !== undefined) return node.attrs[name];
  if (node.dataset && node.dataset[name] !== undefined) return node.dataset[name];
  return undefined;
}

// Matches one simple selector against a node: a tag, then any number of
// .class and [attr] / [attr="value"] segments (the board views only use
// that subset).
function matchSel(node, sel) {
  const s = String(sel).trim();
  let rest = s;
  const tagM = rest.match(/^[a-z][a-z0-9-]*/i);
  const tag = tagM ? tagM[0] : null;
  if (tag) rest = rest.slice(tag.length);
  const segs = [];
  const re = /^(\.[A-Za-z0-9_]+|\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\])/y;
  let m;
  while ((m = re.exec(rest)) !== null) {
    if (m[1].startsWith(".")) segs.push({ type: "class", value: m[1].slice(1) });
    else segs.push({ type: "attr", name: m[2], value: m[3] !== undefined ? m[3] : null });
  }
  if (tag && node.tag !== tag) return false;
  for (const seg of segs) {
    if (seg.type === "class") {
      if (!node.classes || !node.classes.has(seg.value)) return false;
    } else if (attrValue(node, seg.name) !== seg.value) return false;
  }
  return true;
}

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
    get className() {
      return [...node.classes].join(" ");
    },
    set className(value) {
      node.classes = new Set(String(value).split(" ").filter(Boolean));
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
    appendChild: (child) => node.children.push(child),
    replaceChildren: (...nodes) => { node.children = nodes; },
    addEventListener(type, fn) {
      (node.listeners[type] = node.listeners[type] || []).push(fn);
    },
    dispatch(type, extra) {
      const event = {
        stopped: false,
        preventDefault() {},
        stopPropagation() { event.stopped = true; },
        ...(extra || {}),
      };
      for (const fn of node.listeners[type] || []) fn(event);
      return event;
    },
    querySelector(sel) {
      const out = [];
      (function walk(n) {
        if (matchSel(n, sel)) out.push(n);
        for (const c of n.children) walk(c);
      })(node);
      return out[0] || null;
    },
    querySelectorAll(sel) {
      const out = [];
      (function walk(n) {
        if (matchSel(n, sel)) out.push(n);
        for (const c of n.children) walk(c);
      })(node);
      return out;
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

// The queued column and the document event bus the kanban view re-renders
// through.  reset() clears both so each test starts from a clean view.
let boardviewBody = null;
let docListeners = {};

function resetDom() {
  boardviewBody = null;
  docListeners = {};
  globalThis.document = {
    createElement: (tag) => makeNode(tag),
    getElementById: (id) => (id === "boardviewbody" ? boardviewBody : null),
    querySelectorAll: () => [],
    addEventListener: (type, fn) => {
      (docListeners[type] = docListeners[type] || []).push(fn);
    },
    dispatchEvent: (event) => {
      for (const fn of docListeners[event.type] || []) fn(event);
    },
  };
  class CustomEvent {
    constructor(type, init = {}) {
      this.type = type;
      Object.assign(this, init);
    }
  }
  globalThis.CustomEvent = CustomEvent;
}

function registerBody(body) {
  boardviewBody = body;
}

function freshKanban() {
  for (const name of MODULES) delete require.cache[require.resolve(path.join(SRC, name))];
  globalThis.BoardLogic = require(path.join(SRC, "board_logic.js"));
  return require(path.join(SRC, "board_view_kanban.js"));
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

function stubFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || "GET", body: opts.body });
    const r = handler(url, opts);
    return { ok: r.status < 400, status: r.status, json: async () => r.body };
  };
  return calls;
}

function flush() {
  return new Promise((resolve) => setImmediate(resolve));
}

const MISSIONS = [
  {
    mission_id: "r1", name: "Running one", state_group: "running",
    project: { key: "p1", name: "Proj One" }, tasks: [{ id: "T1", status: "accepted" }, { id: "T2", status: "running" }],
  },
  {
    mission_id: "q1", name: "Queued one", state_group: "queued",
    project: { key: "p1", name: "Proj One" }, tasks: [],
  },
  {
    mission_id: "q2", name: "Queued two", state_group: "queued",
    project: null, tasks: [],
  },
  {
    mission_id: "o1", name: "Done one", state_group: "completed",
    project: { key: "p2", name: "Proj Two" }, tasks: [{ id: "T9", status: "accepted_retry" }],
  },
  {
    mission_id: "b1", name: "Broken one", state_group: "not_completed",
    project: { key: "p2", name: "Proj Two" }, tasks: [],
  },
  {
    mission_id: "d1", name: "Off one", state_group: "disabled",
    project: { key: "p2", name: "Proj Two" }, tasks: [],
  },
];

// The queue load: /api/board/missions (empty) + /api/board/queue (order and
// version).
const QUEUE_VERSION = 7;
function queueHandler(url) {
  if (url === "/api/board/missions") return { status: 200, body: { missions: [] } };
  if (url === "/api/board/queue") return {
    status: 200,
    body: {
      missions: [],
      queue: [
        { mission_id: "q1", enabled: true, position: 0 },
        { mission_id: "q2", enabled: true, position: 1 },
      ],
      groups: { running: [], queued: ["q1", "q2"] },
      version: QUEUE_VERSION,
    },
  };
  return { status: 200, body: {} };
}

// A queue that actually reorders on a POST move: the queued column must be
// rebuilt from the fresh order, not from the order it was drawn with.
function movableQueueHandler(initial) {
  let order = initial.slice();
  return (url, opts) => {
    if (opts && opts.method === "POST" && url === "/api/board/queue/move") {
      const body = JSON.parse(opts.body);
      const rest = order.filter((id) => id !== body.mission_id);
      order = rest.slice();
      order.splice(body.to_position, 0, body.mission_id);
      return { status: 200, body: { ok: true } };
    }
    if (url === "/api/board/queue") return {
      status: 200,
      body: {
        missions: [],
        queue: order.map((id, i) => ({ mission_id: id, enabled: true, position: i })),
        groups: { running: [], queued: order.slice() },
        version: QUEUE_VERSION,
      },
    };
    if (url === "/api/board/missions") return { status: 200, body: { missions: [] } };
    return { status: 200, body: {} };
  };
}

function renderWith(views, overrides) {
  const { ctx, counter } = ctxFor(overrides);
  const body = makeNode("div");
  registerBody(body);
  views.render(body, ctx);
  return { body, ctx, counter };
}

test("render draws four state columns in order with their cards", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const cols = byClass(body, "kcol");
  assert.deepEqual(cols.map((c) => c.dataset.col), ["run", "q", "ok", "bad"]);
  assert.deepEqual(byClass(cols[1], "kcard").map((c) => c.dataset.id), ["q1", "q2"]);
  assert.deepEqual(byClass(cols[2], "kcard").map((c) => c.dataset.id), ["o1"]);
  assert.equal(walk(body, (n) => n.dataset && n.dataset.id === "d1").length, 0, "disabled missions are not shown");
});

test("each card carries its project chip and mission data attributes", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const card = byClass(body, "kcard").find((c) => c.dataset.id === "q1");
  assert.equal(card.dataset.project, "p1");
  assert.equal(card.dataset.missionId, "q1");
  assert.equal(card.dataset.group, "queued");
  assert.equal(card.dataset.position, "0");
  assert.equal(card.dataset.search, "queued one q1");
  assert.equal(byClass(card, "kchip")[0].textContent, "Proj One");
  const noProject = byClass(body, "kcard").find((c) => c.dataset.id === "q2");
  assert.equal(byClass(noProject, "kchip").length, 0);
});

test("the figure chips come from the translator", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const tr = (key, params) => (params ? key + ":" + JSON.stringify(params) : key);
  const { body } = renderWith(views, { data: {}, missions: MISSIONS, tr });
  const running = byClass(body, "kcard").find((c) => c.dataset.id === "r1");
  assert.equal(byClass(running, "ch")[0].textContent, 'of:{"a":1,"b":2}');
  assert.equal(byClass(running, "mono")[0].textContent, "r1");
  const heads = byClass(body, "kcol").map((c) => byClass(c, "mono")[0].textContent);
  assert.deepEqual(heads, ["1", "2", "1", "1"]);
});

test("the queued column header carries the new-mission button, labelled from t()", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const btn = byClass(body, "qnew")[0];
  assert.ok(btn, "the button is present in the queued column");
  assert.equal(btn.textContent, "kan.new", "the label is the t() key");
});

test("clicking the new-mission button calls toggleNewMission exactly once", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const actions = require(path.join(SRC, "board_actions.js"));
  const realToggle = actions.toggleNewMission;
  let toggleCalls = 0;
  actions.toggleNewMission = () => { toggleCalls += 1; };
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const btn = byClass(body, "qnew")[0];
  btn.dispatch("click");
  assert.equal(toggleCalls, 1, "one click toggles once");
  actions.toggleNewMission = realToggle;
});

test("a failed queue load shows board.load_failed in the queued column", async () => {
  resetDom();
  stubFetch(() => ({ status: 503, body: { error: "down" } }));
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  await flush();
  const col = body.querySelector('[data-col="q"]');
  const notes = byClass(col, "bv-note");
  assert.equal(notes.length, 1, "the queued column shows the failure note");
  assert.equal(notes[0].textContent, "board.load_failed");
});

test("a move re-renders the queued column from the fresh order without waiting 15 s", async () => {
  resetDom();
  const calls = stubFetch(movableQueueHandler(["q1", "q2"]));
  const views = freshKanban();
  const { body, counter } = renderWith(views, { data: {}, missions: MISSIONS });
  await flush();
  const before = counter.calls;
  const first = byClass(body, "kcard").find((c) => c.dataset.id === "q1");
  byClass(first, "qmove-down")[0].dispatch("click");
  await flush();
  const post = calls.find((c) => c.method === "POST");
  assert.equal(post.url, "/api/board/queue/move");
  assert.deepEqual(JSON.parse(post.body), { mission_id: "q1", to_position: 1, expected_version: QUEUE_VERSION });
  const col = body.querySelector('[data-col="q"]');
  const order = byClass(col, "kcard").map((c) => c.dataset.id);
  assert.deepEqual(order, ["q2", "q1"], "the queued column was re-rendered from the new queue order");
  assert.ok(counter.calls > before, "new cards were built on the re-render");
});

test("drag and up/down on queued cards send the same move request as the queue", async () => {
  resetDom();
  const calls = stubFetch((url, opts) => {
    if (opts && opts.method === "POST") return { status: 200, body: { ok: true } };
    return queueHandler(url);
  });
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  await flush();
  const first = byClass(body, "kcard").find((c) => c.dataset.id === "q1");
  const second = byClass(body, "kcard").find((c) => c.dataset.id === "q2");
  assert.equal(first.dataset.dndReady, "1");
  assert.equal(first.getAttribute("draggable"), "true");
  byClass(first, "qmove-down")[0].dispatch("click");
  await flush();
  const post = calls.find((c) => c.method === "POST");
  assert.equal(post.url, "/api/board/queue/move");
  assert.deepEqual(JSON.parse(post.body), { mission_id: "q1", to_position: 1, expected_version: QUEUE_VERSION });
  assert.ok(second.dataset.dndReady === "1");
});

test("queued cards are controlled only after the queue data loads", async () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  const first = byClass(body, "kcard").find((c) => c.dataset.id === "q1");
  assert.equal(first.dataset.dndReady, undefined, "nothing is attached before the data arrives");
  await flush();
  assert.equal(first.dataset.dndReady, "1");
  assert.equal(byClass(first, "qmove").length, 1);
});

test("a failed queue load leaves the columns rendered without controls", async () => {
  resetDom();
  stubFetch(() => ({ status: 503, body: { error: "down" } }));
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: MISSIONS });
  await flush();
  assert.equal(byClass(body, "kcol").length, 4);
  assert.equal(byClass(body, "qmove").length, 0);
});

test("empty board still shows the four columns with zero counts", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: {}, missions: [] });
  const cols = byClass(body, "kcol");
  assert.equal(cols.length, 4);
  assert.equal(byClass(body, "kcard").length, 0);
  assert.deepEqual(cols.map((c) => byClass(c, "mono")[0].textContent), ["0", "0", "0", "0"]);
});

test("missing data shows the offline note", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body } = renderWith(views, { data: null, missions: [] });
  const notes = byClass(body, "bv-note");
  assert.equal(notes.length, 1);
  assert.equal(notes[0].textContent, "board.offline");
  assert.equal(byClass(body, "kcol").length, 0);
});

test("hostile mission and project names are shown as text, never as markup", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const hostile = '<img src=x onerror="alert(1)"><b>x</b>';
  const { body } = renderWith(views, {
    data: {},
    missions: [{ mission_id: "h1", name: hostile, state_group: "queued", project: { key: "h", name: hostile }, tasks: [] }],
  });
  const title = walk(body, (n) => n.tag === "b" && n.textContent === hostile);
  assert.equal(title.length, 1);
  assert.equal(title[0].children.length, 0);
  assert.equal(walk(body, (n) => n.tag === "img").length, 0);
  assert.equal(walk(body, (n) => n.tag === "b" && n.textContent === "x").length, 0);
  const chip = byClass(body, "kchip")[0];
  assert.equal(chip.textContent, hostile);
  assert.equal(chip.children.length, 0);
});

test("clicking a card selects it without redrawing", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  const { body, counter } = renderWith(views, { data: {}, missions: MISSIONS });
  const card = byClass(body, "kcard")[0];
  const before = counter.calls;
  const childrenBefore = body.children.slice();
  assert.equal(views.select(card), "r1");
  assert.equal(counter.calls, before, "no element was created, so nothing was redrawn");
  assert.deepEqual(body.children, childrenBefore);
});

test("double-clicking a card opens the runs dashboard filtered by the mission, with a back target", () => {
  resetDom();
  stubFetch(queueHandler);
  const views = freshKanban();
  globalThis.location = { hash: "#board/view/kan" };
  views.open("q1");
  assert.equal(globalThis.location.href, "/#mission=q1&from=board%2Fview%2Fkan");
  delete globalThis.location;
});
