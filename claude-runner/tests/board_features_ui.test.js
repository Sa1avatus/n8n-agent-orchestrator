// Node tests of the pure Features logic: rows built from features, rows back to
// JSON, the JSON fallback round-trip and the error display.
// Run from claude-runner/: node --test tests/board_features_ui.test.js
"use strict";
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
require(path.join(__dirname, "i18n_stub.js"));
require(path.join(SRC, "board_i18n.js"));
const BoardRoles = require(path.join(SRC, "board_roles.js"));
const BoardFeatures = require(path.join(SRC, "board_features.js"));
const MS = require(path.join(SRC, "board_mission_save.js"));

const { FEATURE_DEFAULTS, parseFeatures, flatten } = BoardFeatures;

globalThis.BoardRoles = BoardRoles;
globalThis.BoardFeatures = BoardFeatures;
globalThis.BoardMissionSave = {
  fetchJson: MS.fetchJson,
  postJson: MS.postJson,
  featuresState: MS.featuresState,
  featuresSavePlan: MS.featuresSavePlan,
  readForm: () => ({ name: "A", objective: "B", worker_think_policy: {} }),
  formDirty: () => false,
  doSave: MS.doSave,
  SaveState: { missionId: null },
};

// --- pure core ---------------------------------------------------------------

test("absent data: no text resolves to the defaults with no errors", () => {
  for (const [text, missionText] of [["", ""], [null, undefined]]) {
    const { features, errors } = parseFeatures(text, missionText);
    assert.deepEqual(errors, []);
    assert.deepEqual(features, FEATURE_DEFAULTS);
  }
});

test("operator value: the global JSON overrides the defaults and the mission JSON overrides per parameter", () => {
  const { features, errors } = parseFeatures(
    '{"plan_no_truncate":{"enabled":true,"max_tasks":20}}',
    '{"plan_no_truncate":{"max_tasks_hard":40},"run_hygiene":{"enabled":true}}',
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(features.plan_no_truncate, { enabled: true, max_tasks: 20, max_tasks_hard: 40 });
  assert.deepEqual(features.run_hygiene, { enabled: true });
  assert.deepEqual(features.adaptive_split, FEATURE_DEFAULTS.adaptive_split);
});

test("invalid value: unknown feature, wrong type and crossed limits are reported and no features are returned", () => {
  const cases = [
    ['{"bogus":{"enabled":true}}', 'features_json: unknown feature "bogus"'],
    ['{"run_hygiene":{"enabled":"yes"}}', 'features_json: run_hygiene.enabled must be true or false, got "yes"'],
    ['{"plan_no_truncate":{"max_tasks":"20"}}',
      'features_json: plan_no_truncate.max_tasks must be an integer >= 1, got "20"'],
    ['{"plan_no_truncate":{"max_tasks":20,"max_tasks_hard":10}}',
      "plan_no_truncate.max_tasks_hard must be at least plan_no_truncate.max_tasks (20), got 10"],
  ];
  for (const [text, message] of cases) {
    const { features, errors } = parseFeatures(text, "");
    assert.equal(features, null, text);
    assert.deepEqual(errors, [message]);
  }
  const { features, errors } = parseFeatures("{not json", "");
  assert.equal(features, null);
  assert.ok(errors[0].startsWith("features_json: not valid JSON ("), errors[0]);
});

test("JSON fallback round-trip: features serialised back to JSON parse to the same features", () => {
  const { features } = parseFeatures('{"adaptive_split":{"enabled":true,"max_splits":3}}', "");
  const again = parseFeatures(JSON.stringify(features, null, 2), "");
  assert.deepEqual(again.errors, []);
  assert.deepEqual(again.features, features);
  assert.deepEqual(flatten(again.features), flatten(features));
});

// --- the section's rows and error, through the window's listeners ---------------

const all = [];
function mk(tag) {
  return {
    tagName: tag.toUpperCase(), className: "", id: "", textContent: "", style: {}, dataset: {},
    children: [], listeners: {}, hidden: false, disabled: false, checked: false, value: "",
    type: "", scrollTop: 0,
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    appendChild(c) { this.children.push(c); },
    append(...cs) { this.children.push(...cs); },
    prepend(c) { this.children.unshift(c); },
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); },
    setAttribute(k, v) { this.dataset[k] = v; },
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
  };
}
function byId(id) {
  return all.find((e) => e.id === id) || null;
}
globalThis.document = {
  createElement: (tag) => { const e = mk(tag); all.push(e); return e; },
  createTextNode: (text) => ({ textContent: String(text) }),
  getElementById: (id) => byId(id),
  body: { dataset: {} },
  addEventListener() {},
  removeEventListener() {},
};

function walk(node, visit) {
  for (const c of node.children || []) {
    visit(c);
    walk(c, visit);
  }
}
function featSection() {
  let found = null;
  walk(byId("ew-page-exec"), (n) => { if (n.className === "ew-feat") found = n; });
  return found;
}
function featInput(section, key) {
  let found = null;
  walk(section, (n) => { if (n.dataset && n.dataset.feat === key) found = n; });
  return found;
}
function featRows(section) {
  let found = null;
  walk(section, (n) => { if ((n.children || [])[0]?.className === "ew-feat-row") found = n; });
  return found;
}
function featTextarea(section) {
  const areas = [];
  walk(section, (n) => { if (n.tagName === "TEXTAREA") areas.push(n); });
  return areas[0];
}

const win = require(path.join(SRC, "board_edit_win.js"));
async function openSection() {
  const savedFetch = globalThis.fetch;
  globalThis.fetch = () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  let box;
  try {
    globalThis.location = { hash: "" };
    box = mk("div");
    box.className = "confbox";
    box.before = () => {};
    const host = mk("div");
    host.querySelector = (sel) => (sel === ".confbox" ? box : null);
    host.querySelectorAll = () => [];
    host.appendChild = () => {};
    win.enhance(host);
    win.openWin(host);
    await new Promise((resolve) => setImmediate(resolve));
    return { box: host, section: featSection() };
  } finally {
    globalThis.fetch = savedFetch;
  }
}

test("rows from absent data show the defaults and an empty global JSON", async () => {
  const { section } = await openSection();
  assert.equal(featInput(section, "plan_no_truncate.max_tasks").value, 15);
  assert.equal(featInput(section, "plan_no_truncate.enabled").checked, false);
  assert.equal(featTextarea(section).value, "");
});

test("rows from an operator value follow the global JSON", async () => {
  const { section } = await openSection();
  const global = featTextarea(section);
  global.value = '{"adaptive_split":{"enabled":true,"split_steps":200}}';
  for (const fn of global.listeners.input) fn({ target: global });
  assert.equal(featInput(section, "adaptive_split.enabled").checked, true);
  assert.equal(featInput(section, "adaptive_split.split_steps").value, 200);
  assert.equal(featInput(section, "adaptive_split.max_splits").value, 6);
});

test("rows back to JSON: a row change rewrites the global JSON and keeps the other features", async () => {
  const { section } = await openSection();
  const global = featTextarea(section);
  const rows = featRows(section);
  const on = featInput(section, "run_hygiene.enabled");
  on.checked = true;
  rows.listeners.input[0]({ target: on });
  const max = featInput(section, "plan_no_truncate.max_tasks");
  max.value = "25";
  rows.listeners.input[0]({ target: max });
  assert.deepEqual(JSON.parse(global.value), {
    run_hygiene: { enabled: true },
    plan_no_truncate: { max_tasks: 25 },
  });
  max.value = "";
  rows.listeners.input[0]({ target: max });
  assert.deepEqual(JSON.parse(global.value).plan_no_truncate, {});
  assert.equal(parseFeatures(global.value, "").features.run_hygiene.enabled, true);
});

test("rows back to JSON: a row change is not written over text that is not a JSON object", async () => {
  const { section } = await openSection();
  const global = featTextarea(section);
  const rows = featRows(section);
  const on = featInput(section, "run_hygiene.enabled");
  on.checked = true;
  for (const text of ["{not json", '{"run_hygiene":true}']) {
    global.value = text;
    rows.listeners.input[0]({ target: on });
    assert.equal(global.value, text);
  }
});

test("error display: an invalid value is reported under the Features label, a valid one is not", async () => {
  const { box, section } = await openSection();
  const global = featTextarea(section);
  global.value = '{"run_hygiene":{"enabled":true}}';
  assert.equal(win.problems(box), null);
  global.value = '{"bogus":{"enabled":true}}';
  const problem = win.problems(box);
  assert.equal(problem.page, 1);
  assert.equal(problem.text, 'Features: features_json: unknown feature "bogus"');
});
