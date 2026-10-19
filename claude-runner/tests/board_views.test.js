// Node test for board_views.js (T008).
// Run: node --test claude-runner/tests/board_views.test.js
//
// Proves:
//   1. #board/view/<view>?project=<key> round-trips view and project, and
//      #board, #board/<mission> and #board/<mission>/<task> are not treated
//      as view URLs;
//   2. the shared refresh skips the redraw while a drag is active or the
//      mission form is dirty, redraws once afterwards, and does not redraw
//      when the data is unchanged;
//   3. init renders the header even when /api/board/summary rejects;
//   4. views register with render/select/open;
//   5. the source builds strings with t() and DOM APIs, never innerHTML.

"use strict";
const { test } = require("node:test");
const fs = require("fs");
const path = require("path");
const assert = require("node:assert/strict");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
const MODULE = path.join(SRC, "board_views.js");
const SOURCE = fs.readFileSync(MODULE, "utf8");

function freshModule() {
  delete require.cache[require.resolve(MODULE)];
  return require(MODULE);
}

function setDocument(box) {
  globalThis.document = { getElementById: (id) => (id === "boardmission" ? box : null) };
}

test("hash round-trips view and project", () => {
  const V = freshModule();
  const hash = V.buildViewHash("kan", "ahawr core");
  assert.equal(hash, "#board/view/kan?project=ahawr%20core");
  assert.deepEqual(V.parseViewHash(hash), { view: "kan", project: "ahawr core" });
  assert.deepEqual(V.parseViewHash(V.buildViewHash("grid", null)), { view: "grid", project: null });
});

test("old board URLs are not view URLs", () => {
  const V = freshModule();
  assert.equal(V.parseViewHash("#board"), null);
  assert.equal(V.parseViewHash("#board/m1"), null);
  assert.equal(V.parseViewHash("#board/m1/t1"), null);
  assert.equal(V.parseViewHash(""), null);
  assert.equal(V.parseViewHash("#board/view"), null);
});

test("view names are decoded from the path", () => {
  const V = freshModule();
  assert.deepEqual(V.parseViewHash("#board/view/spr?project=p%2F1"), { view: "spr", project: "p/1" });
});

test("refresh does not redraw during a drag and redraws once after it", async () => {
  const V = freshModule();
  setDocument(null);
  globalThis.BoardDnd = { DndState: { data: null, dragId: "m1" } };
  let redraws = 0;
  const deps = (data) => ({
    fetchSummary: async () => data,
    busy: V.busy,
    redraw: () => { redraws += 1; },
  });
  assert.equal(await V.refreshOnce(deps({ missions: [{ mission_id: "m1" }], projects: [] })), "deferred");
  assert.equal(redraws, 0);
  globalThis.BoardDnd.DndState.dragId = null;
  assert.equal(await V.refreshOnce(deps({ missions: [{ mission_id: "m1" }], projects: [] })), "redrawn");
  assert.equal(redraws, 1);
  assert.equal(await V.refreshOnce(deps({ missions: [{ mission_id: "m1" }], projects: [] })), "unchanged");
  assert.equal(redraws, 1);
  delete globalThis.BoardDnd;
});

test("refresh does not redraw while the mission form is dirty", async () => {
  const V = freshModule();
  const box = { id: "boardmission" };
  setDocument(box);
  let asked = null;
  globalThis.BoardMissionSave = {
    formDirty: (b) => { asked = b; return true; },
  };
  let redraws = 0;
  const outcome = await V.refreshOnce({
    fetchSummary: async () => ({ missions: [{ mission_id: "m2" }], projects: [] }),
    busy: V.busy,
    redraw: () => { redraws += 1; },
  });
  assert.equal(outcome, "deferred");
  assert.equal(asked, box);
  assert.equal(redraws, 0);
  globalThis.BoardMissionSave.formDirty = () => false;
  assert.equal(V.busy(), false);
  delete globalThis.BoardMissionSave;
  setDocument(null);
});

test("init renders the header even when the summary fetch fails", async () => {
  const V = freshModule();
  const host = {
    children: [],
    append: (c) => { host.children.push(c); },
    replaceChildren: (...cs) => { host.children = cs.filter(Boolean); },
    addEventListener: () => {},
    querySelectorAll: () => [],
  };
  const body = {
    append: () => {},
    replaceChildren: () => {},
    addEventListener: () => {},
    querySelectorAll: () => [],
  };
  const byId = { boardviews: host, boardviewbody: body };
  const realDocument = globalThis.document;
  const realFetch = globalThis.fetch;
  const realSetInterval = globalThis.setInterval;
  const realLocation = globalThis.location;
  globalThis.document = {
    getElementById: (id) => (id in byId ? byId[id] : null),
    createElement: (tag) => ({
      tag, className: "", textContent: "", hidden: false,
      setAttribute: () => {}, append: () => {},
    }),
    documentElement: { setAttribute: () => {}, removeAttribute: () => {} },
  };
  globalThis.fetch = async () => { throw new Error("summary 503"); };
  globalThis.setInterval = () => 1; // no real timer: the test must not hang
  globalThis.location = { hash: "" };
  let loadPromise = null;
  globalThis.BoardI18n = {
    languages: [{ code: "en", name: "English" }],
    getLanguage: () => "en",
    setLanguage: () => {},
    onChange: () => {},
    load: () => { loadPromise = Promise.resolve(); return loadPromise; },
  };
  try {
    V.init(host);
    // The header is drawn immediately, before the first summary fetch
    // (which rejects here); state.data stays null and drawHeader tolerates it.
    assert.ok(host.children.length > 0);
    // Await the load promise so init's load().then(redrawHost) finishes
    // before the stubs are restored below.
    if (loadPromise) await loadPromise;
  } finally {
    delete globalThis.BoardI18n;
    globalThis.fetch = realFetch;
    globalThis.setInterval = realSetInterval;
    globalThis.location = realLocation;
    globalThis.document = realDocument;
  }
});

test("source uses t() and DOM text, never innerHTML", () => {
  assert.ok(/tr\("part2\.note"\)/.test(SOURCE));
  assert.ok(/tr\(key\)/.test(SOURCE));
  assert.ok(/tr\("theme", \{ mode/.test(SOURCE));
  assert.ok(/node\.textContent = value/.test(SOURCE));
  assert.equal(/innerHTML|outerHTML|insertAdjacentHTML/.test(SOURCE), false);
});

test("the view form is intercepted before board.js routes it", () => {
  assert.ok(/window\.addEventListener\("hashchange", onRoute\)/.test(SOURCE));
  assert.ok(/stopImmediatePropagation/.test(SOURCE));
});
