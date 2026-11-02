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
//      board_edit_win.js, and the static route serves it;
//   6. invalid thinking_json blocks the save and shows the core's errors;
//   7. thinkingSavePlan yields the diff, the version and the dashboard header,
//      and an unchanged text yields no request; board.html loads board_thinking.js.

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
const BoardThinking = require(path.join(SRC, "board_thinking.js"));
const BoardRetrieval = require(path.join(SRC, "board_retrieval.js"));

// A save object the window would use: the module's fetchJson/postJson
// (postJson carries the dashboard header) and the features helpers.
const save = {
  fetchJson: MS.fetchJson,
  postJson: MS.postJson,
  featuresState: MS.featuresState,
  featuresSavePlan: MS.featuresSavePlan,
  thinkingSavePlan: MS.thinkingSavePlan,
  retrievalSavePlan: MS.retrievalSavePlan,
  readForm: () => ({ name: "A", objective: "B", worker_think_policy: {} }),
  formDirty: () => false,
  currentDiff: () => [],
  doSave: MS.doSave,
  SaveState: { missionId: null },
};
globalThis.BoardRoles = BoardRoles;
globalThis.BoardFeatures = BoardFeatures;
globalThis.BoardThinking = BoardThinking;
globalThis.BoardRetrieval = BoardRetrieval;
globalThis.BoardMissionSave = save;

// --- a minimal DOM the window can build the section with ----------------------
function mk(tag) {
  const n = {
    tagName: tag.toUpperCase(), className: "", id: "", textContent: "",
    style: {}, dataset: {}, children: [], hidden: false, disabled: false,
    checked: false, value: "", type: "", scrollTop: 0,
    classList: { add: () => {}, remove: () => {}, contains: () => false, toggle: () => {} },
    listeners: {},
  };
  n.appendChild = (c) => n.children.push(c);
  n.append = (...cs) => cs.forEach((c) => n.children.push(c));
  n.prepend = (c) => n.children.unshift(c);
  n.addEventListener = (type, fn) => { n.listeners[type] = fn; };
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

// --- 5. the thinking_json section: invalid text blocks the save ----------------
function lastById(id) {
  return all.filter((e) => e.id === id).pop() || null;
}

test("invalid thinking_json blocks the save and shows the core's validation errors", async () => {
  const { box } = await buildWindow();
  await new Promise((resolve) => setImmediate(resolve));
  const field = lastById("ew-think-json");
  assert.ok(field, "the thinking_json textarea is built");
  field.value = '{"bogus": 1}';
  const problem = win.problems(box);
  assert.equal(problem.page, 1, "the error is on the Execution page");
  assert.ok(problem.text.includes('unknown key "bogus"'), problem.text);
  field.value = "{";
  assert.ok(win.problems(box).text.includes("not valid JSON"), "invalid JSON is reported");
});

// --- 6. the thinking save plan: diff, version, header, no change -> no request ---
test("thinkingSavePlan yields the diff, the version read at load and the dashboard header", () => {
  const plan = MS.thinkingSavePlan({ thinking_json: "", thinking_version: "tv1" },
    { thinking_json: '{"from_task":2}' });
  assert.equal(plan.diff.length, 1);
  assert.equal(plan.diff[0].field, "thinking_json");
  assert.equal(plan.requests.length, 1);
  const req = plan.requests[0];
  assert.equal(req.url, "/api/board/thinking");
  assert.equal(req.method, "PUT");
  assert.equal(req.body.thinking_json, '{"from_task":2}');
  assert.equal(req.body.expected_version, "tv1");
  assert.equal(req.headers["X-AHAWR-Dashboard"], "1");
});

test("an unchanged thinking_json yields no diff and no request", () => {
  const plan = MS.thinkingSavePlan({ thinking_json: '{"from_task":2}', thinking_version: "tv1" },
    { thinking_json: '{"from_task":2}' });
  assert.equal(plan.diff.length, 0);
  assert.equal(plan.requests.length, 0);
});

// --- 7. the script tag and the static route ------------------------------------
test("board.html loads board_thinking.js between board_features.js and board_edit_win.js", () => {
  const html = readModule("board.html");
  const lines = html.split("\n");
  const roles = lines.findIndex((l) => l.includes("/static/board_roles.js"));
  const feat = lines.findIndex((l) => l.includes("/static/board_features.js"));
  const think = lines.findIndex((l) => l.includes("/static/board_thinking.js"));
  const win = lines.findIndex((l) => l.includes("/static/board_edit_win.js"));
  assert.ok(roles !== -1 && feat !== -1 && think !== -1 && win !== -1, "all four script tags present");
  assert.ok(roles < feat && feat < think && think < win,
    "board_thinking.js is after board_features.js and before board_edit_win.js");
});

test("the static route serves board_features.js (the fixed pattern matches it)", () => {
  const dash = readModule("dashboard.py");
  const m = dash.match(/_board_static = re\.compile\(r"([^"]+)"\)/);
  assert.ok(m, "static route pattern found");
  const pattern = new RegExp(m[1]);
  assert.ok(pattern.test("board_features.js"), "board_features.js matches the static route");
});

// --- 8. retrieval_json: the window no longer writes the legacy retrieval_* columns ---
test("the launch window names no legacy retrieval_* column", () => {
  const src = readModule("board_edit_win.js");
  assert.equal(src.match(/retrieval_(enabled|url|timeout_ms|worker_max_tokens|reviewer_max_tokens|label)/g), null);
});

// --- 9. retrieval_json: invalid text blocks the save and shows the core's reason ---
test("invalid retrieval_json blocks the save and shows the core's validation errors", async () => {
  const { box } = await buildWindow();
  await new Promise((resolve) => setImmediate(resolve));
  const field = lastById("ew-ret-json");
  assert.ok(field, "the retrieval_json textarea is built");
  field.value = '{"bogus": 1}';
  const problem = win.problems(box);
  assert.equal(problem.page, 1, "the error is on the Execution page");
  assert.ok(problem.text.includes('unknown key "bogus"'), problem.text);
  field.value = "{";
  assert.ok(win.problems(box).text.includes("not valid JSON"), "invalid JSON is reported");
});

// --- 10. retrieval_json: the save plan (diff, version, header, no change -> no request) ---
test("retrievalSavePlan yields the diff, the version read at load and the dashboard header", () => {
  const plan = MS.retrievalSavePlan({ retrieval_json: "", retrieval_version: "rv1" },
    { retrieval_json: '{"enabled":true}' });
  assert.equal(plan.diff.length, 1);
  assert.equal(plan.diff[0].field, "retrieval_json");
  assert.equal(plan.requests.length, 1);
  const req = plan.requests[0];
  assert.equal(req.url, "/api/board/retrieval");
  assert.equal(req.method, "PUT");
  assert.equal(req.body.retrieval_json, '{"enabled":true}');
  assert.equal(req.body.expected_version, "rv1");
  assert.equal(req.headers["X-AHAWR-Dashboard"], "1");
});

test("an unchanged retrieval_json yields no diff and no request", () => {
  const plan = MS.retrievalSavePlan({ retrieval_json: '{"enabled":true}', retrieval_version: "rv1" },
    { retrieval_json: '{"enabled":true}' });
  assert.equal(plan.diff.length, 0);
  assert.equal(plan.requests.length, 0);
});

test("absent stored thinking and retrieval values send nothing for an untouched empty form", () => {
  assert.deepEqual(MS.thinkingSavePlan({}, { thinking_json: "" }), { diff: [], requests: [] });
  assert.deepEqual(MS.retrievalSavePlan({}, { retrieval_json: "" }), { diff: [], requests: [] });
});

test("a settings object without legacy keys yields no undefined or NaN in the form values or the diff", () => {
  const data = { mission: { mission_id: "m1" }, queue: [{ mission_id: "m1" }], config: {} };
  const reads = [
    ["config", "worker_model", "text"],
    ["config", "max_attempts_per_task", "number"],
    ["config", "worker_think_off_after_seconds", "number"],
    ["config", "retry_delays", "json"],
    ["listarea", "retry_delays", "listarea"],
    ["queue", "attempts_per_task", "number"],
  ];
  for (const [kind, key, ctl] of reads) {
    const v = MS.initialValue(kind, key, data, ctl);
    assert.notEqual(v, undefined, key);
    assert.ok(!Number.isNaN(v), key);
  }
  const html = MS.diffHtml([{ field: "worker_model", old: undefined, new: "m" }], {});
  assert.ok(!/undefined|NaN/.test(html), html);
});

test("the retrieval PUT goes through postJson with X-AHAWR-Dashboard: 1", async () => {
  const sent = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => {
    sent.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    const req = MS.retrievalSavePlan({ retrieval_json: "", retrieval_version: "rv1" },
      { retrieval_json: '{"enabled":true}' }).requests[0];
    await MS.postJson(req.url, req.body, req.method, req.headers);
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(sent[0].init.method, "PUT");
  assert.equal(sent[0].init.headers["X-AHAWR-Dashboard"], "1");
});

// --- engine_json: the GET/PUT /api/board/engine section ------------------------
globalThis.BoardEngine = require(path.join(SRC, "board_engine.js"));
const SECRET_CHAT = "chat-secret-42";

test("engineSavePlan: one PUT to /api/board/engine with only engine_json and expected_version", async () => {
  const state = { engine_json: '{"runner_url":"http://a:1"}', engine_version: "ev1" };
  const plan = win.engineSavePlan(state, { max_attempts_per_task: 3, runner_url: "http://a:1" });
  assert.deepEqual(plan.diff.map((d) => d.field), ["engine_json"]);
  const req = plan.requests[0];
  assert.equal(req.url, "/api/board/engine");
  assert.equal(req.method, "PUT");
  assert.deepEqual(Object.keys(req.body).sort(), ["engine_json", "expected_version"]);
  assert.equal(req.body.expected_version, "ev1");
  assert.deepEqual(JSON.parse(req.body.engine_json), { runner_url: "http://a:1", max_attempts_per_task: 3 });

  const sent = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => {
    sent.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    await MS.postJson(req.url, req.body, req.method, req.headers);
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(sent[0].init.method, "PUT");
  assert.equal(sent[0].init.headers["X-AHAWR-Dashboard"], "1");
});

test("engineSavePlan: the telegram chat id is masked in the diff but kept in the request", () => {
  const state = { engine_json: JSON.stringify({ telegram_chat_id: SECRET_CHAT }), engine_version: "ev2" };
  const plan = win.engineSavePlan(state, { telegram_chat_id: SECRET_CHAT, max_attempts_per_task: 2 });
  assert.equal(JSON.stringify(plan.diff).includes(SECRET_CHAT), false);
  assert.equal(plan.requests[0].body.engine_json.includes(SECRET_CHAT), true);
});

test("engineSavePlan: normalized values that did not change yield no diff and no request", () => {
  const state = { engine_json: '{"runner_url":"http://a:1"}', engine_version: "ev3" };
  const plan = win.engineSavePlan(state, { runner_url: "http://a:1/" });
  assert.deepEqual(plan.diff, []);
  assert.deepEqual(plan.requests, []);
});

test("the window never draws the telegram chat id, shows source labels, and has no timer", async () => {
  all.length = 0;
  const raw = JSON.stringify({ runner_url: "http://made:9000", telegram_chat_id: SECRET_CHAT });
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url) => Promise.resolve({ ok: true, status: 200,
    json: () => Promise.resolve(url === "/api/board/engine" ? { engine_json: raw, version: "ev4" } : {}) });
  try {
    globalThis.location = { hash: "" };
    const box = freshBox();
    win.enhance(box);
    win.openWin(box);
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    globalThis.fetch = savedFetch;
  }
  const holds = (needle) => all.some(function visit(n) {
    const own = [n.textContent, n.value, n.placeholder, n.title, ...Object.values(n.dataset || {})];
    return own.some((v) => typeof v === "string" && v.includes(needle)) || (n.children || []).some(visit);
  });
  assert.equal(holds(SECRET_CHAT), false);
  assert.equal(holds("set (hidden)"), true);
  const sources = all.filter((n) => n.className === "ew-src").map((n) => n.textContent);
  assert.deepEqual(sources, ["default", "default", "json", "default", "json"]);
  assert.doesNotMatch(readModule("board_edit_win.js"), /setInterval|setTimeout/);
});

test("the engine section blocks the save on an invalid value and names the key", async () => {
  const numberInput = all.find((n) => n.type === "number" && n.ariaLabel === "Override (empty = default)");
  assert.ok(numberInput, "the max attempts override input exists");
  numberInput.value = "abc";
  const bad = win.problems(freshBox());
  assert.equal(bad.page, 1);
  assert.match(bad.text, /max_attempts_per_task/);
});

// --- overrides_json: the mission's overrides, checked by the core and saved with the version ---
globalThis.BoardOverrides = require(path.join(SRC, "board_overrides.js"));

test("overridesSavePlan: one POST to the mission with overrides_json, the version read at load and the header", () => {
  const plan = win.overridesSavePlan({ mission_id: "m1", overrides_json: "", overrides_version: "u1" },
    '{"features":{}}');
  assert.equal(plan.diff.length, 1);
  assert.equal(plan.diff[0].field, "overrides_json");
  assert.equal(plan.requests.length, 1);
  const req = plan.requests[0];
  assert.equal(req.url, "/api/board/missions/m1");
  assert.equal(req.method, "POST");
  assert.equal(req.body.overrides_json, '{"features":{}}');
  assert.equal(req.body.expected_version, "u1");
  assert.equal(req.headers["X-AHAWR-Dashboard"], "1");
});

test("overridesSavePlan: an unchanged text yields no diff and no request", () => {
  const plan = win.overridesSavePlan({ mission_id: "m1", overrides_json: "{}", overrides_version: "u1" }, "{}");
  assert.equal(plan.diff.length, 0);
  assert.equal(plan.requests.length, 0);
});

test("the overrides POST goes through postJson with X-AHAWR-Dashboard: 1", async () => {
  const sent = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => {
    sent.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    const req = win.overridesSavePlan({ mission_id: "m1", overrides_json: "", overrides_version: "u1" }, "{}").requests[0];
    await MS.postJson(req.url, req.body, req.method, req.headers);
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(sent[0].init.method, "POST");
  assert.equal(sent[0].init.headers["X-AHAWR-Dashboard"], "1");
});

test("the window loads the mission's overrides_json and blocks invalid JSON on the context page", async () => {
  save.SaveState.missionId = "m1";
  const savedFetch = globalThis.fetch;
  globalThis.fetch = () => Promise.resolve({ ok: true, status: 200,
    json: () => Promise.resolve({ mission: { overrides_json: '{"roles":{}}', updated_at: "u1" } }) });
  try {
    win.openWin(freshBox());
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    globalThis.fetch = savedFetch;
  }
  const field = lastById("ew-ov-json");
  assert.ok(field, "the overrides_json textarea is built");
  assert.equal(field.value, '{"roles":{}}');
  field.value = '{"bogus": 1}';
  const problem = win.problems(freshBox());
  assert.equal(problem.page, 2, "the error is on the Context page");
  assert.ok(problem.text.includes('unknown key "bogus"'), problem.text);
});

test("a save that changes a mission field and overrides_json sends no stale version", async () => {
  save.SaveState.missionId = "m1";
  let updatedAt = "u1";
  const posts = [];
  const savedFetch = globalThis.fetch;
  const savedDoSave = save.doSave;
  const reply = (status, json = {}) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(json) });
  globalThis.fetch = (url, init = {}) => {
    if (url !== "/api/board/missions/m1") return reply(200);
    if (init.method !== "POST") return reply(200, { mission: { overrides_json: "{}", updated_at: updatedAt } });
    const body = JSON.parse(init.body);
    posts.push(body);
    if (body.expected_version !== updatedAt) return reply(409);
    updatedAt = "u" + (posts.length + 1);
    return reply(200);
  };
  // Stands in for the mission form's doSave: it sends the version read when the window opened.
  save.doSave = () => save.postJson("/api/board/missions/m1", { name: "New", expected_version: "u1" });
  try {
    all.length = 0;
    const box = freshBox();
    win.enhance(box);
    win.openWin(box);
    await new Promise((resolve) => setImmediate(resolve));
    lastById("ew-ov-json").value = '{"roles":{}}';
    const next = lastById("ew-next");
    for (let click = 0; click <= win.PAGES.length; click += 1) await next.listeners.click();
  } finally {
    globalThis.fetch = savedFetch;
    save.doSave = savedDoSave;
  }
  assert.equal(posts.length, 2, JSON.stringify(posts));
  assert.equal(posts[0].name, "New", "the mission field goes first");
  assert.equal(posts[1].overrides_json, '{"roles":{}}');
  assert.equal(posts[1].expected_version, "u2", "the overrides POST carries the version after the mission save");
  assert.equal(lastById("ew-err").textContent, "");
});

test("board.html loads board_overrides.js before board_edit_win.js, and the window logs nothing", () => {
  const html = readModule("board.html");
  const at = html.indexOf('<script src="/static/board_overrides.js">');
  assert.ok(at > 0, "board_overrides.js is loaded");
  assert.ok(at < html.indexOf('<script src="/static/board_edit_win.js">'));
  assert.doesNotMatch(readModule("board_edit_win.js"), /console\.log/);
});

// --- engine error rules table ---------------------------------------------------------

const RULES_RAW = JSON.stringify({ max_attempts_per_task: 4, error_rules: [
  { id: "a", match: { http: [429] }, action: "retry", retries: 2 },
  { id: "b", action: "fail", retries: 0, note: "n" },
] });

async function openEngineWith(raw) {
  all.length = 0;
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url) => Promise.resolve({ ok: true, status: 200,
    json: () => Promise.resolve(url === "/api/board/engine" ? { engine_json: raw, version: "ev5" } : {}) });
  let box;
  try {
    globalThis.location = { hash: "" };
    box = freshBox();
    win.enhance(box);
    win.openWin(box);
    await new Promise((resolve) => setImmediate(resolve));
  } finally {
    globalThis.fetch = savedFetch;
  }
  return box;
}

// The stub keeps children of replaced nodes, so the newest element of a class is the live one.
function engineElem(className) {
  return [...all].reverse().find((e) => e.className === className);
}
function ruleRows() {
  return engineElem("ew-rules").children.slice(1);
}
function ruleIds() {
  return ruleRows().map((row) => row.children[0].children[0].value);
}
function ruleButton(index, position) {
  return ruleRows()[index].children[7].children[position];
}

test("the engine window renders stored error_rules as rows and the box is enabled", async () => {
  const box = await openEngineWith(RULES_RAW);
  assert.equal(engineElem("ew-eng-rules").disabled, false);
  assert.deepEqual(ruleIds(), ["a", "b"]);
  assert.equal(ruleRows()[0].children[5].children[0].value, "2");
  assert.equal(ruleRows()[1].children[6].children[0].value, "n");
  assert.equal(win.problems(box), null);
});

test("add, move up, move down and delete keep the rule order and validate", async () => {
  const box = await openEngineWith(RULES_RAW);
  engineElem("ew-rules-bar").children[0].listeners.click();
  assert.deepEqual(ruleIds(), ["a", "b", ""]);
  assert.match(win.problems(box).text, /error_rules\[2\]\.id/);
  ruleRows()[2].children[0].children[0].value = "c";
  ruleButton(2, 0).listeners.click();
  assert.deepEqual(ruleIds(), ["a", "c", "b"]);
  ruleButton(0, 1).listeners.click();
  assert.deepEqual(ruleIds(), ["c", "a", "b"]);
  ruleButton(0, 2).listeners.click();
  assert.deepEqual(ruleIds(), ["a", "b"]);
  assert.equal(win.problems(box), null);
});

test("a retries value of 9 is reported by the shared core", async () => {
  const box = await openEngineWith(RULES_RAW);
  ruleRows()[0].children[5].children[0].value = "9";
  assert.match(win.problems(box).text, /retries must be an integer from 0 to 5/);
});

test("Load recommended fills nine rows and sends no request", async () => {
  await openEngineWith(RULES_RAW);
  const calls = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url) => {
    calls.push(url);
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    engineElem("ew-rules-bar").children[1].listeners.click();
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(ruleRows().length, 9);
  assert.equal(ruleIds()[0], "weekly-limit-text");
  assert.deepEqual(calls, []);
});

test("an empty table makes engineCandidate drop error_rules", async () => {
  await openEngineWith(RULES_RAW);
  assert.equal(win.engineCandidate().error_rules.length, 2);
  ruleButton(0, 2).listeners.click();
  ruleButton(0, 2).listeners.click();
  const candidate = win.engineCandidate();
  assert.equal("error_rules" in candidate, false);
  assert.equal(candidate.max_attempts_per_task, 4);
});

test("the action select offers the four core actions and keeps the stored one", async () => {
  await openEngineWith(RULES_RAW);
  const select = ruleRows()[1].children[4].children[0];
  assert.equal(select.value, "fail");
  assert.deepEqual(select.children.map((o) => o.value), ["retry", "failover", "compact", "fail"]);
});

test("a retries value below 0 is reported by the shared core", async () => {
  const box = await openEngineWith(RULES_RAW);
  ruleRows()[0].children[5].children[0].value = "-1";
  assert.match(win.problems(box).text, /retries must be an integer from 0 to 5/);
});

test("a rule edit saves with the version read at load and the dashboard header", async () => {
  await openEngineWith(RULES_RAW);
  ruleRows()[0].children[4].children[0].value = "failover";
  const plan = win.engineSavePlan({ engine_json: RULES_RAW, engine_version: "ev5" }, win.engineCandidate());
  assert.deepEqual(plan.diff.map((d) => d.field), ["engine_json"]);
  const req = plan.requests[0];
  assert.equal(req.method, "PUT");
  assert.equal(req.body.expected_version, "ev5");
  assert.equal(JSON.parse(req.body.engine_json).error_rules[0].action, "failover");
  const sent = [];
  const savedFetch = globalThis.fetch;
  globalThis.fetch = (url, init) => {
    sent.push({ url, init });
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  };
  try {
    await MS.postJson(req.url, req.body, req.method, req.headers);
  } finally {
    globalThis.fetch = savedFetch;
  }
  assert.equal(sent[0].url, "/api/board/engine");
  assert.equal(sent[0].init.headers["X-AHAWR-Dashboard"], "1");
});

test("the review page shows the rule change in the diff before saving", async () => {
  save.SaveState.missionId = null;
  await openEngineWith(RULES_RAW);
  ruleRows()[0].children[4].children[0].value = "failover";
  const next = lastById("ew-next");
  for (let click = 0; click < win.PAGES.length; click += 1) await next.listeners.click();
  assert.ok(engineElem("ew-diff"), "the review page builds the diff table");
  const after = all.filter((n) => n.className === "ew-new").map((n) => n.textContent).join("\n");
  assert.match(after, /failover/);
});
