// Node test for board_mission_dependencies.js and the depends_on wiring added
// by T024-T026 (queue card / kanban card renderers, editor save, create form,
// copy prefill, refusal messages, i18n key parity).
// Run: node --test ahawr-runner/tests/board_mission_dependencies.test.js
//
// Proves:
//   1. the "after: a, b" line for depends_on ["a","b"], with every id escaped;
//   2. the "waiting" / "blocked" chips render from the API state, the reason
//    as the escaped tooltip;
//   3. a mission answer without the depends_on key (the mission_dependencies
//    flag off) renders "" — the queue card and the kanban card keep exactly the
//    markup they had before the feature (flag-off pin);
//   4. the editor field parser handles commas, spaces and repeated separators,
//    and duplicates round-trip through the field text;
//   5. the editor save and the create form POST depends_on as a list with the
//    X-AHAWR-Dashboard: 1 header (fetch stubbed);
//   6. a refused save (unknown id, cycle) shows the reason in the banner and
//    in #ew-err;
//   7. copying a mission prefills the source's depends_on ids and the create
//    body sends them;
//   8. every t() key the module uses exists in board_i18n_en.json and
//    board_i18n_ru.json;
//   9. the move behaviour of T027: a drop the guard refuses shows the message
//    naming the ids and leaves the card where it was; when the named ids are the
//    dependents of the moved card it asks "move them too" and the accepted
//    confirm resends the same position with move_dependents (a cancelled
//    confirm sends nothing); "Run next" of a waiting or blocked mission asks
//    "run anyway" and the accepted confirm sends run_anyway;
//   10. every move and start POST carries the X-AHAWR-Dashboard: 1 header;
//   11. with no depends_on in the data (the flag off) the move and "Run next"
//    bodies are exactly the old ones.

"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

require(path.join(__dirname, "i18n_stub.js"));
const SRC = path.join(__dirname, "..", "src", "ahawr_runner");
const MD = require(path.join(SRC, "board_mission_dependencies.js"));
const MD_SRC = fs.readFileSync(path.join(SRC, "board_mission_dependencies.js"), "utf8");
const EN = JSON.parse(fs.readFileSync(path.join(SRC, "board_i18n_en.json"), "utf8"));
const RU = JSON.parse(fs.readFileSync(path.join(SRC, "board_i18n_ru.json"), "utf8"));

function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- the "after: <ids>" line ---
test("the line renders 'after: a, b' for depends_on a,b", () => {
  const line = MD.renderDependencyLine({ mission_id: "m1", depends_on: ["a", "b"] });
  assert.ok(has(line, "after: a, b"));
  assert.ok(has(line, 'class="q-deps"'));
});

test("ids with '<' are escaped in the line", () => {
  const line = MD.renderDependencyLine({ depends_on: ["a<b"] });
  assert.ok(has(line, "a&lt;b"));
  assert.ok(!has(line, "a<b"));
});

test("no depends_on key renders an empty string, an empty list the none text", () => {
  assert.equal(MD.renderDependencyLine({ mission_id: "m1" }), "");
  assert.ok(has(MD.renderDependencyLine({ depends_on: [] }), "no mission dependencies"));
});

// --- the waiting / blocked chip ---
test("the waiting chip renders from the API state with the reason as tooltip", () => {
  const chip = MD.renderDependencyChip({
    mission_id: "m1", depends_on: ["a", "b"], dependency_state: "waiting",
    dependency_reason: "waiting for a, b",
  });
  assert.ok(has(chip, 'class="dep-chip q-dep-waiting"'));
  assert.ok(has(chip, "waiting for a, b"));
  assert.ok(has(chip, 'title="waiting for a, b"'));
});

test("the blocked chip renders with the reason; a '<' in the reason is escaped", () => {
  const chip = MD.renderDependencyChip({
    mission_id: "m1", depends_on: ["a"], dependency_state: "blocked",
    dependency_reason: "blocked by a<b: a dependency failed",
  });
  assert.ok(has(chip, 'class="dep-chip q-dep-blocked"'));
  assert.ok(has(chip, "blocked by a"));
  assert.ok(has(chip, "a&lt;b"));
  assert.ok(!has(chip, 'title="blocked by a<b'));
});

test("due and a missing state render no chip", () => {
  assert.equal(MD.renderDependencyChip({ mission_id: "m1", dependency_state: "due" }), "");
  assert.equal(MD.renderDependencyChip({ mission_id: "m1" }), "");
  assert.equal(MD.renderDependencyChip(null), "");
});

// --- the editor field parsing ---
test("the field parser handles commas, spaces and repeated separators", () => {
  assert.deepEqual(MD.parseDependencyField("a, b  c,d"), ["a", "b", "c", "d"]);
  assert.deepEqual(MD.parseDependencyField("a,,  b ,c"), ["a", "b", "c"]);
  assert.deepEqual(MD.parseDependencyField(""), []);
  assert.deepEqual(MD.parseDependencyField(null), []);
});

test("duplicate ids survive the parser and round-trip through the field text", () => {
  // shortcut: the parser keeps duplicates, board_dependencies.build_dependencies
  // drops them on the server; upgrade if the form needs to dedupe before save.
  assert.deepEqual(MD.parseDependencyField("a, a, b"), ["a", "a", "b"]);
  assert.equal(MD.formatDependencyField(["a", "b"]), "a, b");
  assert.equal(MD.formatDependencyField(["  a ", "", "b"]), "a, b");
  assert.equal(MD.formatDependencyField([]), "");
  assert.equal(MD.formatDependencyField(null), "");
  assert.deepEqual(MD.parseDependencyField(MD.formatDependencyField(["a", "b"])), ["a", "b"]);
});

// --- the move and start bodies ---
test("the move body carries the ids, the position, the version and the flag", () => {
  const body = MD.buildMoveBody("m1", 3, "v37", true);
  assert.equal(body.mission_id, "m1");
  assert.equal(body.to_position, 3);
  assert.equal(body.expected_version, "v37");
  assert.equal(body.move_dependents, true);
  assert.equal(MD.buildMoveBody("m1", 0, "v1", false).move_dependents, false);
  assert.equal(MD.buildStartBody(true).run_anyway, true);
  assert.equal(MD.buildStartBody(undefined).run_anyway, false);
});

// --- the refusal message mapping ---
test("a guard refusal names the order rule; an unknown id falls back to the API message", () => {
  const guard = {
    status: "failed",
    error: { code: "guard", message: "mission m1 cannot move before its dependency a" },
    code: "guard", status_code: 403,
    reason: "mission m1 cannot move before its dependency a",
    blocked_by: ["a", "b"],
  };
  assert.ok(has(MD.dependencyRefusalMessage(guard, "m1"), "The move is refused: m1 must run after a, b"));
  assert.equal(
    MD.dependencyRefusalMessage({ error: { code: "bad_request", message: "unknown mission dependency x" } }, "m1"),
    "unknown mission dependency x",
  );
});

test("a cycle refusal names the closing pair, escaped", () => {
  const cycle = {
    status: "failed",
    error: { code: "cycle", message: "the dependency closes a cycle" },
    code: "cycle", status_code: 422, closing_pair: ["a", "b"],
  };
  assert.ok(has(MD.dependencyRefusalMessage(cycle, "m1"), "Mission dependency closes a cycle: a -> b -> a"));
  assert.ok(has(MD.dependencyRefusalMessage({ code: "cycle", closing_pair: ["a<b", "c"] }, "m1"), "a&lt;b -> c"));
});

test("a refused start reads as a refused run, not as a refused move", () => {
  const startRefusal = {
    status: "failed",
    error: { code: "guard", message: "mission m1 is waiting: pass run_anyway: true" },
    code: "guard", status_code: 403,
    reason: "mission m1 is waiting: pass run_anyway: true",
    blocked_by: ["a", "b"],
    dependency_state: "waiting",
  };
  assert.ok(has(MD.dependencyRefusalMessage(startRefusal, "m1"), "Run refused: m1 still waits for a, b"));
  assert.ok(MD.isDependencyRefusal({ status: 403, body: startRefusal }));
  assert.ok(!MD.isDependencyRefusal({ status: 409, body: { code: "conflict", error: { code: "conflict" } } }));
  assert.ok(!MD.isDependencyRefusal({ status: 403 }));
  assert.equal(MD.blockedIdsText(startRefusal), "a, b");
  assert.ok(has(MD.dependencyRunConfirm(startRefusal), "run anyway? (a, b)"));
  assert.ok(has(MD.dependencyRunConfirm({ dependency_state: "waiting" }), "run anyway?"));
});

test("the dependents among the ids a refusal named are the rows that list the moved mission", () => {
  const rows = [
    { position: 0, mission_id: "m1", enabled: true },
    { position: 1, mission_id: "m2", enabled: true, depends_on: ["m1"] },
    { position: 2, mission_id: "m3", enabled: true, depends_on: ["m0"] },
  ];
  assert.deepEqual(MD.dependentsAmong(rows, "m1", ["m2", "m3"]), ["m2"]);
  assert.equal(MD.dependentsAmong([], "m1", ["m2"]).length, 0);
  assert.ok(has(MD.dependencyGroupConfirm(rows, "m1", { blocked_by: ["m2", "m3"] }), "m2 (1) depend on it"));
  assert.equal(MD.dependencyGroupConfirm(rows, "m1", { blocked_by: ["m3"] }), "");
});

// --- the flag-off pin: a mission answer without depends_on keeps the old HTML ---
test("the queue card HTML for a mission without the field equals the pre-feature HTML", () => {
  const BQ = require(path.join(SRC, "board_queue.js"));
  const withDeps = { mission_id: "m1", name: "Alpha", depends_on: ["m0", "m9"],
    dependency_state: "waiting", dependency_reason: "m0, m9 are ahead of it in the queue" };
  const withoutDeps = { mission_id: "m1", name: "Alpha" };
  const htmlWithDeps = BQ.cardHtml(withDeps, 0, "queued", null);
  assert.ok(has(htmlWithDeps, "after: m0, m9"));

  // Renderers returning "" is exactly what the pre-feature code emitted: no
  // dependency node at all.  Mutate the cached module for one call, then restore.
  const origLine = MD.renderDependencyLine;
  const origChip = MD.renderDependencyChip;
  MD.renderDependencyLine = () => "";
  MD.renderDependencyChip = () => "";
  let htmlBefore;
  try {
    htmlBefore = BQ.cardHtml(withDeps, 0, "queued", null);
  } finally {
    MD.renderDependencyLine = origLine;
    MD.renderDependencyChip = origChip;
  }
  const htmlOff = BQ.cardHtml(withoutDeps, 0, "queued", null);
  assert.equal(htmlOff, htmlBefore);
  assert.ok(!has(htmlOff, "q-deps"));
  assert.ok(!has(htmlOff, "dep-chip"));
});

test("the kanban card adds no dependency box when the renderers return no HTML", () => {
  // dependencyNode is not exported to node: pin the wiring on the source and
  // prove the renderers give "" for a flag-off answer.
  const kanbanSrc = fs.readFileSync(path.join(SRC, "board_view_kanban.js"), "utf8");
  assert.ok(has(kanbanSrc, "if (!html) return null;"));
  assert.ok(has(kanbanSrc, "md.renderDependencyLine(mission) + md.renderDependencyChip(mission)"));
  assert.equal(MD.renderDependencyLine({ mission_id: "m1" }) + MD.renderDependencyChip({ mission_id: "m1" }), "");
});

// --- the editor save: the ids go to the API as a list with the header ---
const bannerStub = { textContent: "", hidden: true };
const ewErrStub = { textContent: "" };
const saveField = {
  dataset: { key: "depends_on", kind: "queue", control: "text" },
  type: "text", value: "m0, m9", checked: false, closest: () => null,
};
const saveBoxEls = [saveField];
function saveBox() {
  return {
    dataset: {},
    querySelectorAll: (sel) => (sel === "[data-key]" ? saveBoxEls : []),
    querySelector: (sel) => (sel === ".savepanel"
      ? { querySelector: (s) => (s === ".savebanner" ? bannerStub : { hidden: true, innerHTML: "" }), appendChild: () => {} }
      : saveBoxEls.find((el) => el.dataset.key === (String(sel).match(/data-key="([^"]+)"/) || [])[1]) || null),
  };
}

async function runSave(postStatus, postBody) {
  const MS = require(path.join(SRC, "board_mission_save.js"));
  globalThis.document = {
    createElement: () => ({ className: "", hidden: true, appendChild: () => {}, addEventListener: () => {},
      querySelector: () => ({ hidden: true, innerHTML: "" }) }),
    getElementById: (id) => (id === "ew-err" ? ewErrStub : null),
  };
  bannerStub.textContent = "";
  ewErrStub.textContent = "";
  const calls = [];
  globalThis.fetch = (url, options) => {
    calls.push({ url: String(url), options: options || {} });
    return Promise.resolve({
      ok: postStatus === 200, status: postStatus,
      json: () => Promise.resolve(postBody),
    });
  };
  MS.captureInitial(saveBox(), {
    mission: { mission_id: "m9", updated_at: "v9", name: "Nine" },
    config: {},
    queue: [{ position: 3, mission_id: "m9", depends_on: ["m0", "m9x"] }],
  });
  saveField.value = "zz, ww";
  await MS.doSave(saveBox());
  return calls;
}

test("the editor save posts the ids as a list with the X-AHAWR-Dashboard header", async () => {
  const calls = await runSave(400, {
    status: "failed",
    error: { code: "bad_request", message: "unknown mission dependency zz" },
    code: "bad_request", status_code: 400,
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/board/missions/m9");
  assert.equal(calls[0].options.headers["X-AHAWR-Dashboard"], "1");
  assert.deepEqual(JSON.parse(calls[0].options.body).depends_on, ["zz", "ww"]);
  assert.equal(bannerStub.textContent, "unknown mission dependency zz");
  assert.equal(ewErrStub.textContent, "unknown mission dependency zz");
});

test("a save refused by a cycle shows the closing pair in the banner", async () => {
  const calls = await runSave(422, {
    status: "failed",
    error: { code: "cycle", message: "the dependency closes a cycle" },
    code: "cycle", status_code: 422, closing_pair: ["m9", "zz"],
  });
  assert.equal(calls.length, 1);
  assert.ok(has(bannerStub.textContent, "Mission dependency closes a cycle: m9 -> zz -> m9"));
  assert.ok(has(ewErrStub.textContent, "cycle"));
});

// --- the create form and the copy prefill ---
function fakeEl(tag) {
  const el = {
    tagName: tag, dataset: {}, className: "", textContent: "", title: "",
    type: "", value: "", required: false, id: "", children: [], listeners: {},
  };
  el.appendChild = (c) => { el.children.push(c); return el; };
  el.insertBefore = (c, ref) => {
    const pos = el.children.indexOf(ref);
    el.children.splice(pos < 0 ? el.children.length : pos, 0, c);
    return el;
  };
  el.addEventListener = (name, fn) => { el.listeners[name] = fn; };
  return el;
}

async function runCreateForm(featureJson, queueRows) {
  const BA = require(path.join(SRC, "board_actions.js"));
  globalThis.BoardFeatures = require(path.join(SRC, "board_features.js"));
  const { FEATURE_ROUTE_DEFAULTS } = require("./features_route_defaults.js");
  const calls = [];
  globalThis.fetch = (url, options) => {
    calls.push({ url: String(url), options: options || {} });
    const u = String(url);
    if (u === "/api/board/features") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({
        ...FEATURE_ROUTE_DEFAULTS, features_json: featureJson, version: "fv1",
      }) });
    }
    if (u === "/api/board/queue") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ queue: queueRows, version: "v1" }) });
    }
    // The create POST is answered with a refusal: the body is still recorded
    // in `calls`, and the error path returns before the success path would
    // need the real button DOM (hookUp has not run in node).
    return Promise.resolve({
      ok: false, status: 400,
      json: () => Promise.resolve({ status: "failed", error: { code: "bad_request", message: "stub refusal" } }),
    });
  };

  const inputs = {
    mission_id: fakeEl("input"), name: fakeEl("input"), objective: fakeEl("textarea"),
    template: fakeEl("select"), position: fakeEl("input"),
  };
  inputs.mission_id.value = "m9new";
  inputs.name.value = "New mission";
  inputs.objective.value = "Objective";
  inputs.template.value = "mission:m1";
  inputs.position.value = "";
  const form = fakeEl("div");
  form.querySelector = (sel) => {
    const key = (String(sel).match(/name="([^"]+)"/) || [])[1];
    if (!key) return null;
    if (inputs[key]) return inputs[key];
    for (const child of form.children) {
      for (const grand of child.children || []) {
        if (grand.name === key) return grand;
      }
    }
    return null;
  };
  const submit = fakeEl("button");
  const tplSel = fakeEl("select");
  globalThis.document = {
    createElement: fakeEl,
    getElementById: (id) => (id === "missionform" ? form : null),
  };

  await BA.attachDependencyField(form, tplSel, submit);
  tplSel.value = "mission:m1";
  if (tplSel.listeners.change) {
    tplSel.listeners.change();
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  await BA.createMissionForm();
  return { form, calls };
}

test("with the flag on the create form sends depends_on with the dashboard header", async () => {
  const queueRows = [
    { position: 0, mission_id: "m1", enabled: true, depends_on: ["m0", "m9"] },
    { position: 1, mission_id: "m2", enabled: true },
  ];
  const { form, calls } = await runCreateForm(
    JSON.stringify({ mission_dependencies: { enabled: true } }), queueRows,
  );
  const posts = calls.filter((c) => c.url === "/api/board/missions/create");
  assert.equal(posts.length, 1);
  assert.equal(posts[0].options.headers["X-AHAWR-Dashboard"], "1");
  const body = JSON.parse(posts[0].options.body);
  assert.deepEqual(body.depends_on, ["m0", "m9"]);
  assert.equal(body.source_mission_id, "m1");
  // The copied source's ids prefilled the field and are its initial value, so
  // the untouched copy still sends the source dependencies.
  const depInput = form.querySelector('[name="depends_on"]');
  assert.ok(depInput);
  assert.equal(depInput.value, "m0, m9");
  assert.equal(depInput.dataset.initial, "m0, m9");
});

test("with the flag off the form has no depends_on field and the body has no key", async () => {
  const { form, calls } = await runCreateForm("", []);
  assert.equal(form.querySelector('[name="depends_on"]'), null);
  const posts = calls.filter((c) => c.url === "/api/board/missions/create");
  assert.equal(posts.length, 1);
  const body = JSON.parse(posts[0].options.body);
  assert.ok(!("depends_on" in body));
  assert.equal(body.source_mission_id, "m1");
});

// --- the move behaviour the drag and drop and "Run next" show (T027) ---
// The fetch stub answers the queue routes as board_api.py does: the dependency
// guard refuses a move unless the body carries move_dependents, and refuses a
// "Run next" of a waiting or blocked mission unless the body carries run_anyway.
// window.confirm is recorded and #boardstate holds the shown text, so every test
// reads what the operator sees.  The payloads are the _refusal envelopes of
// board_api.py: a move refusal names blocked_by only, a start refusal adds
// dependency_state.
const MOVE_QUEUE = [
  { position: 0, mission_id: "m1", enabled: true },
  { position: 1, mission_id: "m2", enabled: true, depends_on: ["m1"] },
  { position: 2, mission_id: "m3", enabled: true, depends_on: ["m1"] },
];

function guardRefusal(reason, blockedBy, state) {
  const payload = {
    status: "failed",
    error: { code: "guard", message: reason },
    code: "guard", status_code: 403,
    reason, blocked_by: blockedBy,
  };
  if (state) payload.dependency_state = state;
  return payload;
}

function stubBoard() {
  // #boardstate is visible, so showNotice (the fallback) is never reached.
  const stateEl = { textContent: "", className: "", hidden: false };
  globalThis.document = {
    getElementById: (id) => (id === "boardstate" ? stateEl : null),
    dispatchEvent: () => {},
  };
  globalThis.CustomEvent = function CustomEvent() {};
  return stateEl;
}

function stubConfirm(answer) {
  const texts = [];
  globalThis.window = { confirm: (text) => { texts.push(text); return answer; } };
  return texts;
}

async function stubQueueRoutes(queue, refusal, accepted) {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const route = String(url);
    calls.push({ url: route, options: options || {} });
    if (route === "/api/board/missions") {
      return { ok: true, status: 200, json: async () => ({ groups: {}, missions: [] }) };
    }
    if (route === "/api/board/queue") {
      return { ok: true, status: 200, json: async () => ({ queue, version: "v1" }) };
    }
    if (route === "/api/board/queue/move" || route === "/api/board/queue/first") {
      const body = JSON.parse(options.body);
      if (refusal && !accepted(body)) {
        return { ok: false, status: 403, json: async () => refusal };
      }
      return { ok: true, status: 200, json: async () => ({ queue, version: "v2" }) };
    }
    throw new Error("unexpected route " + route);
  };
  return calls;
}

function moveBodiesOf(calls) {
  return calls.filter((c) => c.url === "/api/board/queue/move")
    .map((c) => JSON.parse(c.options.body));
}

async function runDrop(refusal, confirmAnswer, queue) {
  const BoardDnd = require(path.join(SRC, "board_dnd.js"));
  globalThis.BoardLogic = globalThis.BoardLogic || require(path.join(SRC, "board_logic.js"));
  const stateEl = stubBoard();
  const confirmTexts = stubConfirm(confirmAnswer);
  const calls = await stubQueueRoutes(queue, refusal, (body) => body.move_dependents === true);
  BoardDnd.DndState.data = {
    groups: {
      queued: queue.map((r) => r.mission_id),
      running: [], completed: [], not_completed: [], disabled: [],
    },
    queue: queue.map((r) => ({ ...r })), version: "v9", missions: [],
  };
  // The refused drop: m1 (a dependency) dragged behind m2.
  await BoardDnd.applyMove("m1", "m2", "after");
  return {
    calls, confirmTexts, stateEl,
    order: BoardDnd.DndState.data.queue.map((r) => r.mission_id),
  };
}

async function runRunNext(refusal, confirmAnswer, queue) {
  const BoardActions = require(path.join(SRC, "board_actions.js"));
  const stateEl = stubBoard();
  const confirmTexts = stubConfirm(confirmAnswer);
  const calls = await stubQueueRoutes(queue, refusal, (body) => body.run_anyway === true);
  await BoardActions.refreshBoard(); // fills the version the request body carries
  await BoardActions.doAction("first", "m1");
  return {
    calls, confirmTexts, stateEl,
    bodies: calls.filter((c) => c.url === "/api/board/queue/first")
      .map((c) => JSON.parse(c.options.body)),
  };
}

test("the refused drop of a dependency asks 'move them too' naming its dependents", async () => {
  const refusal = guardRefusal("mission m1 must run after m2, m3", ["m2", "m3"]);
  const { confirmTexts, calls } = await runDrop(refusal, true, MOVE_QUEUE);
  assert.equal(confirmTexts.length, 1, "the guard asks once");
  assert.ok(has(confirmTexts[0], "m2, m3 (2)"), `the confirm names the dependents: ${confirmTexts[0]}`);
  assert.ok(has(confirmTexts[0], "depend on it"), "the confirm is the group question");
  assert.equal(moveBodiesOf(calls).length, 2, "the accepted confirm resends once");
});

test("the accepted 'move them too' resends the same position with move_dependents", async () => {
  const refusal = guardRefusal("mission m1 must run after m2, m3", ["m2", "m3"]);
  const { calls } = await runDrop(refusal, true, MOVE_QUEUE);
  const bodies = moveBodiesOf(calls);
  assert.deepEqual(bodies[0], { mission_id: "m1", to_position: 1, expected_version: "v9" });
  assert.deepEqual(bodies[1], {
    mission_id: "m1", to_position: 1, expected_version: "v9", move_dependents: true,
  });
});

test("a cancelled 'move them too' sends no second request and the card stays put", async () => {
  const refusal = guardRefusal("mission m1 must run after m2, m3", ["m2", "m3"]);
  const { confirmTexts, stateEl, order, calls } = await runDrop(refusal, false, MOVE_QUEUE);
  assert.equal(moveBodiesOf(calls).length, 1, "the refused confirm sends nothing more");
  assert.equal(confirmTexts.length, 1, "the question was asked, then cancelled");
  assert.deepEqual(order, ["m1", "m2", "m3"], "the queue order is unchanged");
  assert.ok(has(stateEl.textContent, "The move is refused: m1 must run after m2, m3"),
    `the banner names the ids in the i18n wording: ${stateEl.textContent}`);
});

test("a refusal naming nothing that depends on the card asks nothing, only shows the ids", async () => {
  const refusal = guardRefusal("mission m1 must run after m0", ["m0"]);
  const { confirmTexts, stateEl, order, calls } = await runDrop(refusal, true, MOVE_QUEUE);
  assert.equal(confirmTexts.length, 0, "no named id depends on it, so nothing is asked");
  assert.equal(moveBodiesOf(calls).length, 1, "the refusal is not resent");
  assert.deepEqual(order, ["m1", "m2", "m3"], "the card returned to its position");
  assert.ok(has(stateEl.textContent, "The move is refused: m1 must run after m0"),
    `the banner is the i18n order text, not the raw API message: ${stateEl.textContent}`);
});

test("a drop into the queued column asks 'move them too' and resends with move_dependents", async () => {
  // requeueMission is the column-drop path: enable, then move to the drop slot.
  const BoardDnd = require(path.join(SRC, "board_dnd.js"));
  globalThis.BoardLogic = globalThis.BoardLogic || require(path.join(SRC, "board_logic.js"));
  const refusal = guardRefusal("mission m1 must run after m2, m3", ["m2", "m3"]);
  const stateEl = stubBoard();
  const confirmTexts = stubConfirm(true);
  const calls = await stubQueueRoutes(MOVE_QUEUE, refusal, (body) => body.move_dependents === true);
  await BoardDnd.requeueMission("m1", "m2", "after");
  const bodies = moveBodiesOf(calls);
  assert.equal(bodies.length, 2, "the column drop resends once after the confirm");
  assert.ok(has(confirmTexts[0], "m2, m3 (2)"), `the confirm names the dependents: ${confirmTexts[0]}`);
  assert.equal(bodies[1].to_position, bodies[0].to_position, "the resend keeps the position");
  assert.equal(bodies[1].move_dependents, true, "the resend moves the dependents too");
  assert.ok(stateEl.textContent === "" || stateEl.className === "bad", "the shown line is a bad state");
});

test("'Run next' of a waiting mission asks 'run anyway' and sends run_anyway", async () => {
  const refusal = guardRefusal("mission m1 still waits for m2, m3", ["m2", "m3"], "waiting");
  const { confirmTexts, bodies } = await runRunNext(refusal, true, MOVE_QUEUE);
  assert.equal(confirmTexts.length, 1, "the run-anyway question is asked once");
  assert.ok(has(confirmTexts[0], "run anyway?"), `the question: ${confirmTexts[0]}`);
  assert.ok(has(confirmTexts[0], "m2, m3"), "the question names the dependencies");
  assert.deepEqual(bodies[1], {
    mission_id: "m1", expected_version: "v1", run_anyway: true,
  });
  assert.equal(bodies.length, 2, "the accepted confirm sends one resend");
});

test("'Run next' of a blocked mission: the cancelled confirm sends no second request", async () => {
  const refusal = guardRefusal("mission m1 is blocked by m2", ["m2"], "blocked");
  const { confirmTexts, bodies, stateEl } = await runRunNext(refusal, false, MOVE_QUEUE);
  assert.equal(bodies.length, 1, "the cancelled confirm sends nothing more");
  assert.ok(has(confirmTexts[0], "m2"), "the question names the blocking id");
  assert.ok(has(stateEl.textContent, "Run refused: m1 still waits for m2"),
    `the banner names the ids: ${stateEl.textContent}`);
});

test("no depends_on in the data keeps the old move and Run-next bodies", async () => {
  const plainQueue = [
    { position: 0, mission_id: "m1", enabled: true },
    { position: 1, mission_id: "m2", enabled: true },
  ];
  const drop = await runDrop(null, true, plainQueue);
  assert.deepEqual(moveBodiesOf(drop.calls), [{
    mission_id: "m1", to_position: 1, expected_version: "v9",
  }]);
  assert.ok(!("move_dependents" in moveBodiesOf(drop.calls)[0]), "no move_dependents key");
  const run = await runRunNext(null, true, plainQueue);
  assert.deepEqual(run.bodies, [{ mission_id: "m1", expected_version: "v1" }]);
  assert.ok(!("run_anyway" in run.bodies[0]), "no run_anyway key");
});

test("every move and start POST carries the X-AHAWR-Dashboard header", async () => {
  const refusal = guardRefusal("mission m1 must run after m2, m3", ["m2", "m3"]);
  const drop = await runDrop(refusal, true, MOVE_QUEUE);
  const run = await runRunNext(
    guardRefusal("mission m1 still waits for m2, m3", ["m2", "m3"], "waiting"), true, MOVE_QUEUE,
  );
  const posts = drop.calls.concat(run.calls).filter((c) => c.options.method === "POST");
  assert.ok(posts.length >= 4, "the move and the start paths posted " + posts.length + " times");
  for (const post of posts) {
    assert.equal(post.options.headers["X-AHAWR-Dashboard"], "1",
      `${post.url} sent without the dashboard header`);
  }
});

// --- i18n key parity ---
test("every t() key used by the module exists in both language tables", () => {
  const keys = new Set();
  for (const match of MD_SRC.matchAll(/t\("([^"]+)"/g)) keys.add(match[1]);
  assert.ok(keys.size > 0);
  for (const key of keys) {
    assert.ok(Object.prototype.hasOwnProperty.call(EN, key), `missing in en: ${key}`);
    assert.ok(Object.prototype.hasOwnProperty.call(RU, key), `missing in ru: ${key}`);
  }
});
