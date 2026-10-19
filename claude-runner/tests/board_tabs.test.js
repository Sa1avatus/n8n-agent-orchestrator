// board_tabs.js: groups the mission panels into pages, one page visible at a time.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SRC = path.join(__dirname, "..", "src", "claude_runner", "board_tabs.js");

function makeDom(ids) {
  const make = (tag, id) => ({
    tag, id, hidden: false, children: [], attrs: {}, _text: "", listeners: {}, disabled: false,
    classList: { set: new Set(), toggle(c, on) { on ? this.set.add(c) : this.set.delete(c); } },
    setAttribute(k, v) { this.attrs[k] = v; if (k === "id") this.id = v; },
    appendChild(n) { this.children.push(n); n.parent = this; return n; },
    append(...ns) { ns.forEach((n) => this.appendChild(n)); },
    addEventListener(e, f) { this.listeners[e] = f; },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this); },
    insertBefore(n, ref) { const i = this.children.indexOf(ref); n.parent = this; this.children.splice(i, 0, n); },
    querySelector(sel) { return this.children.find((c) => sel.slice(1).split(".").every((k) => (c.attrs.class || "").split(" ").includes(k))); },
    set textContent(v) { this._text = v; }, get textContent() { return this._text; },
  });
  const root = make("div", "board");
  for (const id of ids) root.appendChild(make("div", id));
  const find = (id) => {
    if (id === "board") return root;
    const walk = (n) => (n.id === id ? n : n.children.map(walk).find(Boolean));
    return walk(root) || null;
  };
  return { root, document: { getElementById: find, createElement: (tag) => make(tag, "") } };
}

function load(ids) {
  const dom = makeDom(ids);
  const store = {};
  const sandbox = {
    document: dom.document, window: { scrollTo() {} }, t: (k) => k,
    sessionStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SRC, "utf8"), sandbox, { filename: SRC });
  return { dom, tabs: sandbox.BoardTabs, store };
}

test("one page is visible, the others are hidden, the bars exist", () => {
  const { dom, tabs } = load(["boardmission", "boardtasks", "boardthink", "boardhistory"]);
  tabs.apply();
  const visible = dom.root.children.filter((c) => c.id.startsWith("board") && !["boardtabs", "boardpager"].includes(c.id) && !c.hidden);
  assert.deepEqual(visible.map((c) => c.id), ["boardmission"]);
  assert.ok(dom.document.getElementById("boardtabs"));
  assert.equal(dom.document.getElementById("boardtabs").children.length, 4);
  assert.ok(dom.document.getElementById("boardpager"));
});

test("show() switches the page and remembers it; a panel that does not exist gives no tab", () => {
  const { dom, tabs, store } = load(["boardmission", "boardtasks", "boardthink"]);
  tabs.apply();
  tabs.show(2);
  assert.equal(dom.document.getElementById("boardthink").hidden, false);
  assert.equal(dom.document.getElementById("boardmission").hidden, true);
  assert.equal(store["ahawr-board-tab"], "think");
  tabs.show(99);
  assert.equal(dom.document.getElementById("boardthink").hidden, false);
});

test("apply() again restores the remembered page and clear() removes the bars", () => {
  const { dom, tabs } = load(["boardmission", "boardtasks"]);
  tabs.apply();
  tabs.show(1);
  tabs.apply();
  assert.equal(dom.document.getElementById("boardtasks").hidden, false);
  assert.equal(dom.root.children.filter((c) => c.id === "boardtabs").length, 1);
  tabs.clear();
  assert.equal(dom.document.getElementById("boardtabs"), null);
});
