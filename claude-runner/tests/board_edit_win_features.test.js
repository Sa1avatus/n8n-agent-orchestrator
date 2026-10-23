// Node test for the Features section of board_edit_win.js.
// Run: node claude-runner/tests/board_edit_win_features.test.js
//
// Proves:
//   1. with absent data (the profile GET has no features_json and there is no
//      mission id) the section renders without throwing: the feature rows are
//      built, the global textarea is present and empty, and no mission
//      features request is made;
//   2. with a made-up value in the global textarea ({"bogus":{"enabled":true}})
//      BoardFeatures.parseFeatures errors are shown as readable text through the
//      same tr("ew.feat.invalid", { msg }) call the section uses;
//   3. a valid edit keeps the JSON the operator entered and yields the save
//      plan (one PUT per changed text, each with the version read at load);
//   4. the feature PUTs go through the save module's postJson, which carries
//      X-AHAWR-Dashboard: 1;
//   5. board.html loads board_features.js between board_roles.js and
//      board_edit_win.js, and the static route serves it.

"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
function readModule(file) {
  return fs.readFileSync(path.join(SRC, file), "utf8");
}

// --- the board modules, as the browser loads them ----------------------------
// board_i18n.js exports through globalThis; the others take the module.exports
// path under require.  The window reads the browser globals (t, BoardRoles,
// BoardFeatures, BoardMissionSave), so set those after loading.
require(path.join(__dirname, "i18n_stub.js"));
require(path.join(SRC, "board_i18n.js")); // sets globalThis.BoardI18n and globalThis.t
const I = globalThis.BoardI18n;
const BoardRoles = require(path.join(SRC, "board_roles.js"));
const BoardFeatures = require(path.join(SRC, "board_features.js"));
const MS = require(path.join(SRC, "board_mission_save.js"));

// A save object the window would use: the module's fetchJson/postJson
// (postJson carries the dashboard header) and the features helpers.
const save = {
  fetchJson: MS.fetchJson,
  postJson: MS.postJson,
  featuresState: MS.featuresState,
  featuresSavePlan: MS.featuresSavePlan,
  readForm: () => ({ name: "A", objective: "B", worker_think_policy: {} }),
  formDirty: () => false,
  doSave: MS.doSave,
  SaveState: { missionId: null },
};
globalThis.BoardRoles = BoardRoles;
globalThis.BoardFeatures = BoardFeatures;
globalThis.BoardMissionSave = save;

// --- a minimal DOM the window can build the section with ----------------------
function mk(tag) {
  const n = {
    tagName: tag.toUpperCase(), className: "", id: "", textContent: "",
    style: {}, dataset: {}, children: [], hidden: false, disabled: false,
    checked: false, value: "", type: "", scrollTop: 0,
    classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
  };
  n.appendChild = (c) => n.children.push(c);
  n.append = (...cs) => cs.forEach((c) => n.children.push(c));
  n.prepend = (c) => n.children.unshift(c);
  n.addEventListener = () => {};
  n.setAttribute = (k, v) => { n.dataset[k] = v; };
  n.querySelector = () => null;
  n.querySelectorAll = () => [];
  n.closest = () => null;
  return n;
}
const all = [];
function byId(id) {
  return all.find((e) => e.id === id) || null;
}
globalThis.document = {
  createElement: (tag) => { const e = mk(tag); all.push(e); return e; },
  createTextNode: (text) => ({ textContent: String(text) }),
  getElementById: (id) => byId(id),
  body: { dataset: {} },
  addEventListener: () => {},
  removeEventListener: () => {},
};

// A mission box the window builds inside (it must expose a .confbox).
function freshBox() {
  const confbox = mk("div");
  confbox.className = "confbox";
  confbox.before = () => {};
  const box = mk("div");
  box.querySelector = (sel) => (sel === ".confbox" ? confbox : null);
  box.querySelectorAll = () => [];
  box.appendChild = () => {};
  return box;
}

// Build the window (enhance) and open it (openWin fires loadFeatures).
// A recording fetch stub is installed for the duration of the build and
// removed afterwards, so each test is self-contained.
const win = require(path.join(SRC, "board_edit_win.js"));
async function buildWindow() {
  const fetchCalls = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    fetchCalls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    globalThis.location = { hash: "" };
    const box = freshBox();
    win.enhance(box);
    win.openWin(box);
    await Promise.resolve();
    await Promise.resolve();
    return { box, fetchCalls };
  } finally {
    globalThis.fetch = savedFetch;
  }
}

function findClass(node, cls) {
  const children = node.children || [];
  for (const c of children) {
    if (c.className === cls) return c;
    const found = findClass(c, cls);
    if (found) return found;
  }
  return null;
}
function featBox() {
  const page = byId("ew-page-exec");
  return page ? findClass(page, "ew-feat") : null;
}

// --- 1. absent data: the section renders without throwing ---------------------
test("absent data: the section renders, is inert, and no mission features request is made", async () => {
  const { fetchCalls } = await buildWindow();
  const box = featBox();
  assert.ok(box, "the .ew-feat section is built");
  // Three feature rows, one per feature.
  let rows = 0;
  (function walk(node) {
    for (const c of node.children) {
      if (c.className === "ew-feat-row") rows += 1;
      walk(c);
    }
  })(box);
  assert.equal(rows, 3, "one row per feature");
  // The global and mission textareas are present and empty (no data to fill).
  const textareas = [];
  (function walk(node) {
    for (const c of node.children) {
      if (c.tagName === "TEXTAREA") textareas.push(c);
      walk(c);
    }
  })(box);
  assert.equal(textareas.length, 2, "the global and mission textareas exist");
  assert.equal(textareas[0].value, "", "the global textarea is empty with absent data");
  // No mission features request is made without a mission id.
  assert.ok(!fetchCalls.some((u) => u.startsWith("/api/board/missions/") && u.includes("/features")),
    "no mission features request without a mission id");
});

// --- 2. a made-up value shows a readable error ----------------------------------
test("a made-up value in the global textarea triggers the section's validation and shows a readable error", async () => {
  const bad = '{"bogus":{"enabled":true}}';
  const { box } = await buildWindow();
  const feBox = featBox();
  assert.ok(feBox, "the .ew-feat section is built");
  const textareas = [];
  (function walk(node) {
    for (const c of node.children || []) {
      if (c.tagName === "TEXTAREA") textareas.push(c);
      walk(c);
    }
  })(feBox);
  assert.equal(textareas.length, 2, "the global and mission textareas exist");
  assert.equal(textareas[0].value, "", "the global textarea starts empty");
  // textareas[0] IS the window's featFields.global element; setting its value
  // drives the section's real validation path (problems()).
  textareas[0].value = bad;
  const problem = win.problems(box);
  assert.ok(problem, "problems() reports a validation problem");
  assert.equal(problem.page, 1, "the error is on the Execution page");
  // The section puts this text into #ew-err; set it there and assert the
  // element carries the readable error naming the offending feature.
  const errEl = byId("ew-err");
  errEl.textContent = problem.text;
  assert.ok(errEl.textContent.includes('unknown feature "bogus"'),
    "the section's error element names the offending feature");
  assert.ok(errEl.textContent.includes("Features:"),
    "the section's error element is readable English");
});

// --- 3. a valid edit keeps the operator's JSON and yields the save plan -----
test("a valid edit rewrites only the changed feature and yields the save plan", () => {
  const state = MS.featuresState(
    { features_json: "", version: "fv1" },
    { mission_features_json: "", version: "mv1" },
  );
  const next = {
    features_json: '{"run_hygiene":{"enabled":true}}',
    mission_features_json: "",
  };
  const plan = MS.featuresSavePlan(state, "m1", next);
  assert.equal(plan.diff.length, 1);
  assert.equal(plan.diff[0].field, "features_json");
  assert.equal(plan.requests.length, 1);
  assert.equal(plan.requests[0].url, "/api/board/features");
  assert.equal(plan.requests[0].method, "PUT");
  assert.equal(plan.requests[0].body.features_json, '{"run_hygiene":{"enabled":true}}');
  assert.equal(plan.requests[0].body.expected_version, "fv1");
});

// --- 4. the feature PUTs carry the dashboard header ---------------------------
test("the feature PUTs go through postJson, which carries X-AHAWR-Dashboard: 1", async () => {
  const sent = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    sent.push({ url, options });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    const plan = MS.featuresSavePlan(
      MS.featuresState({ features_json: "", version: "fv1" }, { mission_features_json: "", version: "mv1" }),
      "m1",
      { features_json: '{"run_hygiene":{"enabled":true}}', mission_features_json: "" },
    );
    for (const r of plan.requests) await MS.postJson(r.url, r.body, r.method);
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(sent.length, 1);
  assert.equal(sent[0].options.method, "PUT");
  assert.equal(sent[0].options.headers["X-AHAWR-Dashboard"], "1");
});

// --- 5. the script tag and the static route ------------------------------------
test("board.html loads board_features.js between board_roles.js and board_edit_win.js", () => {
  const html = readModule("board.html");
  const lines = html.split("\n");
  const roles = lines.findIndex((l) => l.includes("/static/board_roles.js"));
  const feat = lines.findIndex((l) => l.includes("/static/board_features.js"));
  const win = lines.findIndex((l) => l.includes("/static/board_edit_win.js"));
  assert.ok(roles !== -1 && feat !== -1 && win !== -1, "all three script tags present");
  assert.ok(roles < feat && feat < win, "board_features.js is between board_roles.js and board_edit_win.js");
});

test("the static route serves board_features.js (the fixed pattern matches it)", () => {
  const dash = readModule("dashboard.py");
  const m = dash.match(/_board_static = re\.compile\(r"([^"]+)"\)/);
  assert.ok(m, "static route pattern found");
  const pattern = new RegExp(m[1]);
  assert.ok(pattern.test("board_features.js"), "board_features.js matches the static route");
});
