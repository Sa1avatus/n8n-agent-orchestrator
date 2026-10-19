"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
require(path.join(__dirname, "i18n_stub.js"));
require(path.join(SRC, "board_order.js"));

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

function matchSel(node, sel) {
  let rest = String(sel).trim();
  const tagM = rest.match(/^[a-z][a-z0-9-]*/i);
  const tag = tagM ? tagM[0] : null;
  if (tag) rest = rest.slice(tag.length);
  const segs = [];
  const re = /^(\.[A-Za-z0-9_-]+|\[([A-Za-z0-9_-]+)(?:="([^"]*)")?\])/y;
  let m;
  while ((m = re.exec(rest)) !== null) {
    if (m[1].startsWith(".")) segs.push({ type: "class", value: m[1].slice(1) });
    else segs.push({ type: "attr", name: m[2], value: m[3] !== undefined ? m[3] : null });
  }
  if (tag && node.tag !== tag) return false;
  for (const seg of segs) {
    if (seg.type === "class") {
      if (!node.classes.has(seg.value)) return false;
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
    replaceChildren: (...nodes) => { node.children = nodes; },
    addEventListener(type, fn) {
      (node.listeners[type] = node.listeners[type] || []).push(fn);
    },
    dispatch(type, extra) {
      const event = { preventDefault() {}, stopPropagation() {}, ...(extra || {}) };
      for (const fn of node.listeners[type] || []) fn(event);
      return event;
    },
    querySelector(sel) {
      return node.querySelectorAll(sel)[0] || null;
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

globalThis.document = { createElement: makeNode };
const BoardViewLanes = require(path.join(SRC, "board_view_lanes.js"));

// --- fixtures and helpers ---

const flush = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
  for (let i = 0; i < 12; i++) await flush();
}

function task(id, status, extra) {
  return Object.assign({ id, title: "title " + id, status, deps: [], attempts: 1 }, extra || {});
}

function mission(id, tasks, extra) {
  return Object.assign({ mission_id: id, name: "M " + id, project: { key: "p", name: "P" }, position: 0, tasks }, extra || {});
}

const STATS = {
  worker_hours: 1,
  reviewer_minutes: 2,
  architect_minutes: 3,
  input_tokens: 1000,
  output_tokens: 500,
  cache_ratio: 0.4,
  tok_per_s: 33.4,
  thinking_runs: 1,
  thinking_steps: 4,
  cost_usd: 0.1234,
};

function installIo(overrides) {
  const calls = { reorder: [], plan: 0 };
  BoardViewLanes._setIo(Object.assign({
    getJSON: async (url) => {
      if (url.endsWith("/plan")) {
        calls.plan += 1;
        return { version: 7 };
      }
      if (url.endsWith("/stats")) return STATS;
      throw new Error("unexpected " + url);
    },
    postReorder: async (id, body) => {
      calls.reorder.push({ id, body });
      return { plan: {}, changelog: {}, running_warning: false };
    },
    fetchSummary: async () => ({ missions: [], projects: [] }),
  }, overrides || {}));
  return calls;
}

function renderLanes(missions) {
  BoardViewLanes._reset();
  const body = makeNode("div");
  BoardViewLanes.render(body, { data: {}, missions, projects: [], project: null, query: "", el: null, card: null, tr: null });
  return body;
}

function rows(body) {
  return body.children.filter((c) => c.attrs["data-lane"] !== undefined);
}

function waitCell(row) {
  return row.children[1];
}

function notesOf(cell) {
  return cell.children;
}

function noteFor(body, tid) {
  return body.querySelectorAll(".ln-note").find((n) => n.attrs["data-tid"] === tid);
}

const TWO_WAITING = mission("M1", [
  task("T001", "waiting"),
  task("T002", "waiting", { deps: ["T001"] }),
  task("T003", "waiting"),
]);

// --- note rendering ---

test("note shows id, title, dependencies, attempts and waiting placeholder", () => {
  installIo();
  const body = renderLanes([TWO_WAITING]);
  const note = noteFor(body, "T002");
  const text = note.querySelectorAll("small").map((s) => s.textContent);
  assert.ok(note.querySelector("b").textContent === "T002");
  assert.ok(note.querySelector(".ln-title").textContent === "title T002");
  assert.ok(text.includes("← T001"));
  assert.ok(text.includes(globalThis.t("att", { n: 1 })));
  assert.equal(note.querySelector(".ln-stats").textContent, globalThis.t("not.run"));
});

test("running note fetches stats and puts the short line in place, full line in tooltip", async () => {
  installIo();
  const running = mission("M2", [task("T010", "running", { attempts: 2 }), task("T011", "accepted")]);
  const body = renderLanes([running]);
  const note = noteFor(body, "T010");
  assert.equal(note.querySelector(".ln-stats").textContent, "…");
  await settle();
  const f = BoardViewLanes.figures(STATS);
  assert.equal(f.minutes, 65);
  assert.equal(f.cache, 40);
  assert.equal(note.querySelector(".ln-stats").textContent, BoardViewLanes.shortLine(f));
  const tip = note.attrs.title;
  assert.ok(tip.includes(BoardViewLanes.fullLine(f)));
  assert.ok(tip.includes("$0.12"));
  assert.ok(tip.startsWith("T010 title T010"));
});

test("only the first six active missions are shown, completed ones are skipped", () => {
  installIo();
  const many = [];
  for (let i = 0; i < 8; i++) many.push(mission("M" + i, [task("T" + i, "running")], { position: i }));
  many.push(mission("DONE", [task("T900", "accepted")], { position: -1 }));
  const body = renderLanes(many);
  assert.equal(rows(body).length, 6);
  assert.ok(!rows(body).some((r) => r.attrs["data-lane"] === "DONE"));
  const more = body.querySelector(".ln-more");
  assert.ok(more);
  more.dispatch("click");
  assert.equal(rows(body).length, 8);
});

test("click selects by toggling classes without redrawing", () => {
  installIo();
  const body = renderLanes([TWO_WAITING]);
  const first = noteFor(body, "T001");
  const second = noteFor(body, "T003");
  first.dispatch("click");
  assert.ok(first.classes.has("sel"));
  second.dispatch("click");
  assert.ok(!first.classes.has("sel"));
  assert.ok(second.classes.has("sel"));
  assert.strictEqual(noteFor(body, "T003"), second);
});

// --- drag gating ---

test("only waiting notes are draggable", () => {
  installIo();
  const body = renderLanes([mission("M3", [task("T1", "waiting"), task("T2", "running"), task("T3", "rejected")])]);
  assert.equal(noteFor(body, "T1").attrs.draggable, "true");
  assert.equal(noteFor(body, "T2").attrs.draggable, undefined);
  assert.equal(noteFor(body, "T3").attrs.draggable, undefined);
});

test("drag stays within its own mission", () => {
  installIo();
  const body = renderLanes([
    mission("A", [task("A1", "waiting"), task("A2", "waiting")]),
    mission("B", [task("B1", "waiting")]),
  ]);
  noteFor(body, "A1").dispatch("dragstart", { dataTransfer: { setData() {} } });
  let accepted = 0;
  noteFor(body, "B1").dispatch("dragover", { preventDefault() { accepted += 1; } });
  assert.equal(accepted, 0);
  assert.ok(!noteFor(body, "B1").classes.has("dropb"));
});

// --- warning-box flow ---

function dragT001OntoT002(body) {
  noteFor(body, "T001").dispatch("dragstart", { dataTransfer: { setData() {} } });
  noteFor(body, "T002").dispatch("drop", {});
}

test("a dependency violation opens the warning box and changes nothing", () => {
  const calls = installIo();
  const body = renderLanes([TWO_WAITING]);
  const before = notesOf(waitCell(rows(body)[0])).map((n) => n.attrs["data-tid"]);
  dragT001OntoT002(body);
  const box = body.querySelector(".warnbox");
  assert.ok(box, "warning box rendered");
  assert.ok(box.querySelector(".ln-group"), "Move together button");
  assert.ok(box.querySelector(".ln-cancel"), "Cancel button");
  assert.equal(calls.reorder.length, 0);
  const after = notesOf(waitCell(rows(body)[0])).map((n) => n.attrs["data-tid"]);
  assert.deepEqual(after, before);
});

test("cancel dismisses the warning box without a request", () => {
  const calls = installIo();
  const body = renderLanes([TWO_WAITING]);
  dragT001OntoT002(body);
  body.querySelector(".ln-cancel").dispatch("click");
  assert.equal(body.querySelector(".warnbox"), null);
  assert.equal(calls.reorder.length, 0);
});

test("move together sends the group order and redraws only after the server accepts", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const accepted = mission("M1", [task("T003", "waiting"), task("T001", "waiting"), task("T002", "waiting", { deps: ["T001"] })]);
  const calls = installIo({
    postReorder: async (id, body) => {
      calls.reorder.push({ id, body });
      await gate;
      return { plan: {}, changelog: {}, running_warning: true };
    },
    fetchSummary: async () => ({ missions: [accepted], projects: [] }),
  });
  const body = renderLanes([TWO_WAITING]);
  dragT001OntoT002(body);
  body.querySelector(".ln-group").dispatch("click");
  await settle();

  assert.equal(calls.reorder.length, 1);
  assert.equal(calls.reorder[0].id, "M1");
  assert.deepEqual(calls.reorder[0].body.order, ["T003", "T001", "T002"]);
  assert.equal(calls.reorder[0].body.expected_version, 7);
  const during = notesOf(waitCell(rows(body)[0])).map((n) => n.attrs["data-tid"]);
  assert.deepEqual(during, ["T001", "T002", "T003"], "no local reorder before server responds");

  release();
  await settle();
  const after = notesOf(waitCell(rows(body)[0])).map((n) => n.attrs["data-tid"]);
  assert.deepEqual(after, ["T003", "T001", "T002"], "redraw follows the server-accepted order");
  assert.equal(body.querySelector(".ln-group"), null, "warning box dismissed after acceptance");
});

test("running_warning from the server is shown", async () => {
  installIo({
    postReorder: async () => ({ plan: {}, changelog: {}, running_warning: true }),
  });
  const body = renderLanes([TWO_WAITING]);
  dragT001OntoT002(body);
  body.querySelector(".ln-group").dispatch("click");
  await settle();
  const notice = body.children.find((c) => c.textContent === globalThis.t("running.warn"));
  assert.ok(notice, "running warning rendered");
});

test("a rejected reorder leaves the lane unchanged and shows the conflict", async () => {
  installIo({
    postReorder: async () => {
      const err = new Error("reorder 409");
      err.status = 409;
      throw err;
    },
  });
  const body = renderLanes([TWO_WAITING]);
  dragT001OntoT002(body);
  body.querySelector(".ln-group").dispatch("click");
  await settle();
  const after = notesOf(waitCell(rows(body)[0])).map((n) => n.attrs["data-tid"]);
  assert.deepEqual(after, ["T001", "T002", "T003"]);
  const notice = body.children.find((c) => c.textContent === globalThis.t("conflict.plan_changed"));
  assert.ok(notice, "conflict notice rendered");
});

test("a group-invalid move shows no move-together button", () => {
  installIo();
  const original = globalThis.BoardOrder.tryMove;
  globalThis.BoardOrder.tryMove = () => ({ ok: false, reason: "group-invalid", broken: [["A", "B"]], group: null });
  try {
    const body = renderLanes([TWO_WAITING]);
    dragT001OntoT002(body);
    const box = body.querySelector(".warnbox");
    assert.ok(box);
    assert.equal(box.querySelector(".ln-group"), null);
    assert.ok(box.querySelector(".ln-cancel"));
  } finally {
    globalThis.BoardOrder.tryMove = original;
  }
});
