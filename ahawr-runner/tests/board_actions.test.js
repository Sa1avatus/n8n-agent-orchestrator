// Node test for board_actions.js (card actions, confirmations, auto-refresh).
// Run: node ahawr-runner/tests/board_actions.test.js
//
// Stubs fetch and document (the module is required through its CommonJS
// export, so hookUp() does not run) and proves:
//   1. a disable of a running mission requests ?confirm=true and the
//      X-AHAWR-Dashboard header;
//   2. a dangerous button routes through confirmActions (window.confirm is
//      consulted before the request; the request is skipped when it refuses);
//   3. tick() returns early (no request) when BoardDnd.DndState.dragId is
//      set, and when the form is dirty;
//   4. the creation form: no depends_on field and no depends_on in the body
//      while mission_dependencies is off, the labelled field when it is on,
//      the ids of the copied source mission prefilled, and a refused create
//      showing the reason next to the field with the form still open.

"use strict";
const path = require("path");

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}

// --- fetch stub: records every call and answers minimally ---
// featuresJson is the saved features text of the profile: empty means every
// feature keeps its shipped default (mission_dependencies off).
const { FEATURE_ROUTE_DEFAULTS } = require("./features_route_defaults.js");
let featuresJson = "";
const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options: options || {} });
  const u = String(url);
  if (u === "/api/board/features") {
    return { ok: true, json: async () => ({
      ...FEATURE_ROUTE_DEFAULTS,
      features_json: featuresJson,
      version: "fv1",
    }) };
  }
  if (u === "/api/board/missions") {
    return { ok: true, json: async () => ({
      groups: { running: ["m1"], queued: ["m2"], completed: [],
        not_completed: [], disabled: [] },
      missions: [
        { mission_id: "m1", name: "Running" },
        { mission_id: "m2", name: "Queued" },
      ],
    }) };
  }
  if (u === "/api/board/queue") {
    return { ok: true, json: async () => ({
      queue: [
        { position: 0, mission_id: "m1", enabled: true, depends_on: ["m0", "m9"] },
        { position: 1, mission_id: "m2", enabled: true },
      ],
      version: "2026-01-01T00:00:00+00:00",
    }) };
  }
  if (u.indexOf("/api/board/missions/m1") === 0) {
    return { ok: true, json: async () => ({ live_state: { status: "active" },
      task_views: [] }) };
  }
  return { ok: true, json: async () => ({}) };
};

// --- document stub (tracks ids so created form elements are findable) ---
function fakeEl(tag) {
  const el = { tagName: tag, dataset: {}, className: "", textContent: "",
    title: "", type: "", innerHTML: "", value: "", children: [], listeners: {} };
  Object.defineProperty(el, "id", {
    set(v) { el._id = v; byId[v] = el; },
    get() { return el._id || ""; },
  });
  el._id = "";
  el.appendChild = (c) => { el.children.push(c); return el; };
  el.addEventListener = (t, fn) => { el.listeners[t] = fn; };
  el.prepend = (c) => { el.children.unshift(c); return el; };
  el.remove = () => { delete byId[el._id]; };
  el.insertBefore = (c, ref) => {
    const pos = el.children.indexOf(ref);
    if (pos === -1) el.children.push(c);
    else el.children.splice(pos, 0, c);
    return el;
  };
  // [name="..."] lookups, walking every descendant (selects and buttons are
  // not part of the "input, textarea" querySelectorAll below)
  el.querySelector = (sel) => {
    const name = (String(sel).match(/name="([^"]+)"/) || [])[1];
    if (!name) return null;
    const walk = (node) => {
      for (const child of node.children || []) {
        if (child.name === name) return child;
        const hit = walk(child);
        if (hit) return hit;
      }
      return null;
    };
    return walk(el);
  };
  // return the descendant inputs/textarea for querySelectorAll("input, textarea")
  el.querySelectorAll = (sel) => {
    const out = [];
    const walk = (node) => {
      for (const c of node.children || []) {
        if (c.tagName === "input" || c.tagName === "textarea") out.push(c);
        walk(c);
      }
    };
    walk(el);
    return out;
  };
  return el;
}
const byId = {};
const boardStub = fakeEl("div"); boardStub.id = "board";
const stateStub = fakeEl("div"); stateStub.id = "boardstate";
const queueStub = fakeEl("div"); queueStub.id = "boardqueue";
queueStub.querySelectorAll = () => [];
globalThis.document = {
  hidden: false,
  getElementById(id) { return byId[id] || null; },
  createElement: fakeEl,
};
let confirmCalls = [];
globalThis.window = {
  confirm(msg) { confirmCalls.push(msg); return true; },
  // T009 teardown listeners (board_actions.js wireTeardown) register on this stub.
  addEventListener(name, fn) { if (!windowListeners[name]) windowListeners[name] = []; windowListeners[name].push(fn); },
};
const windowListeners = {};
globalThis.BoardDnd = { DndState: { data: null, dragId: null },
  attachDnd: async () => {} };

require(path.join(__dirname, "i18n_stub.js"));
// boardFeatureFlags() reads the flags through board_features.js, which the page
// loads as a global before the actions module.
globalThis.BoardFeatures = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_features.js"));
const BA = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_actions.js"));

(async () => {
  // --- 1. disable of a running mission: ?confirm=true and the header ---
  calls.length = 0;
  await BA.doAction("disable", "m1");
  const disableCall = calls.find((c) => c.url.indexOf("/disable") !== -1);
  check("disable call present", !!disableCall);
  check("disable url has confirm=true",
    disableCall.url === "/api/board/missions/m1/disable?confirm=true");
  check("disable header",
    disableCall.options.headers &&
    disableCall.options.headers["X-AHAWR-Dashboard"] === "1");

  // a waiting mission without a queue row gets one before "to the end" moves it
  const ensured = [];
  globalThis.BoardDnd.ensureQueued = async (id) => { ensured.push(id); return { version: "v2", queue: [] }; };
  calls.length = 0;
  await BA.doAction("last", "m9");
  const lastCall = calls.find((c) => c.url === "/api/board/queue/last");
  check("last asks ensureQueued first", ensured.length === 1 && ensured[0] === "m9");
  check("last uses the fresh queue version",
    lastCall && JSON.parse(lastCall.options.body).expected_version === "v2");
  delete globalThis.BoardDnd.ensureQueued;

  // stop takes the same confirm style
  calls.length = 0;
  await BA.doAction("stop", "m1");
  const stopCall = calls.find((c) => c.url.indexOf("/stop") !== -1);
  check("stop url has confirm=true",
    stopCall.url === "/api/board/missions/m1/stop?confirm=true");

  // pause is the flags endpoint
  calls.length = 0;
  await BA.doAction("pause", "m1");
  const pauseCall = calls.find((c) => c.url.indexOf("/flags/") !== -1);
  check("pause goes to /flags/queue_paused",
    pauseCall && pauseCall.url === "/api/board/flags/queue_paused");
  check("pause body is the flag value",
    pauseCall && JSON.parse(pauseCall.options.body).value === "1");

  // --- 2. dangerous button routes through confirmActions ---
  const data = {
    groups: { running: ["m1"], queued: ["m2"], completed: [],
      not_completed: [], disabled: [] },
    missions: [{ mission_id: "m1", name: "Running" }],
    queue: [{ position: 0, mission_id: "m1", enabled: true }],
    version: "v1",
  };
  const card = fakeEl("a");
  card.dataset.missionId = "m1";
  card.dataset.group = "running";
  queueStub.querySelectorAll = () => [card];
  BA.attachCardActionsTo(data);
  // the buttons live in the action group appended to the card
  const group = card.children[0];
  const disableBtn = group.children.find((b) => b.textContent === "disable");
  check("disable button present", !!disableBtn);
  confirmCalls.length = 0;
  calls.length = 0;
  disableBtn.listeners.click({ preventDefault() {}, stopPropagation() {} });
  check("confirm was asked for disable",
    confirmCalls.length === 1 && confirmCalls[0].indexOf("Disable mission") !== -1);
  const cDisable = calls.find((c) => c.url.indexOf("/disable") !== -1);
  check("disable after confirm has confirm=true",
    cDisable && cDisable.url === "/api/board/missions/m1/disable?confirm=true");

  // a refused confirmation makes no request
  window.confirm = (msg) => { confirmCalls.push(msg); return false; };
  confirmCalls.length = 0;
  calls.length = 0;
  disableBtn.listeners.click({ preventDefault() {}, stopPropagation() {} });
  check("refused confirm makes no request",
    confirmCalls.length === 1 && calls.length === 0);
  window.confirm = (msg) => { confirmCalls.push(msg); return true; };

  // --- 3. tick() returns early while dragging or editing ---
  calls.length = 0;
  BoardDnd.DndState.dragId = "m1";
  BA.tick();
  check("tick skips while dragging", calls.length === 0);
  BoardDnd.DndState.dragId = null;

  // the "New mission" button is created by hookUp()
  BA.hookUp();

  // dirty form: open the form and mark a field edited
  BA.toggleNewMission();
  const form = document.getElementById("missionform");
  const idField = form.children[0].children[0]; // label > input
  idField.value = "edited";
  calls.length = 0;
  BA.tick();
  check("tick skips a dirty form", calls.length === 0);

  // clean form: the tick proceeds (it fetches)
  idField.value = "";
  calls.length = 0;
  await BA.tick();
  check("tick fetches when nothing is blocked",
    calls.some((c) => c.url === "/api/board/missions"));

  // --- 4. the depends_on field of the creation form ---
  // The flag off (the shipped defaults): no field, no key in the create body.
  // attachDependencyField is async, so the attach must settle first.
  await new Promise((resolve) => setImmediate(resolve));
  const formOff = document.getElementById("missionform");
  check("flag off: no dependency input in the form",
    !formOff.querySelector('[name="depends_on"]'));
  check("flag off: no dependency error element",
    !document.getElementById("missionform-dep-error"));
  formOff.querySelector('[name="mission_id"]').value = "m-new";
  formOff.querySelector('[name="name"]').value = "New";
  formOff.querySelector('[name="objective"]').value = "objective text";
  calls.length = 0;
  await BA.createMissionForm();
  const createOff = calls.find((c) => c.url === "/api/board/missions/create");
  check("flag off: the create body has no depends_on key",
    !!createOff && !("depends_on" in JSON.parse(createOff.options.body)));
  check("flag off: the create call carries the dashboard header",
    createOff.options.headers["X-AHAWR-Dashboard"] === "1");

  // The flag on: the labelled field, its hint and the error element beside it.
  featuresJson = JSON.stringify({ mission_dependencies: { enabled: true } });
  BA.toggleNewMission();
  await new Promise((resolve) => setImmediate(resolve));
  const formOn = document.getElementById("missionform");
  const depInput = formOn.querySelector('[name="depends_on"]');
  check("flag on: the dependency input is present", !!depInput);
  check("flag on: the label text comes from m.dep",
    formOn.children.some((c) => c.tagName === "label"
      && c.textContent.trim() === "After missions"));
  check("flag on: the hint comes from m.dep.hint",
    depInput.title.indexOf("Ids of the missions that must run before") === 0);
  check("flag on: the error element is next to the field",
    !!document.getElementById("missionform-dep-error"));
  // Choosing a source mission prefills its ids, and they are the initial state
  // so dirtyForm() does not read an untouched copy as an edit.
  const tplSel = formOn.querySelector('[name="template"]');
  tplSel.value = "mission:m1";
  tplSel.listeners.change({ preventDefault() {}, stopPropagation() {} });
  await new Promise((resolve) => setImmediate(resolve));
  check("the copy source prefills the ids as the field shows them",
    depInput.value === "m0, m9");
  check("the prefilled ids are the initial state", depInput.dataset.initial === "m0, m9");
  calls.length = 0;
  await BA.createMissionForm();
  const createOn = calls.find((c) => c.url === "/api/board/missions/create");
  check("flag on: the create body sends the ids as a list",
    JSON.stringify(JSON.parse(createOn.options.body).depends_on) === '["m0","m9"]');

  // A refused create: the reason is visible next to the field, the form stays
  // open, nothing else is requested.
  const createFetch = globalThis.fetch;
  globalThis.fetch = (url, options) => {
    calls.push({ url: String(url), options: options || {} });
    const u = String(url);
    if (u === "/api/board/missions/create") {
      return { ok: false, status: 422, json: async () => ({
        status: "failed",
        error: { code: "cycle", message: "the dependency closes a cycle" },
        code: "cycle", status_code: 422, closing_pair: ["m-new", "m0"],
      }) };
    }
    if (u === "/api/board/features") {
      // the reopened form must still see the flag on, so its attach works
      return { ok: true, json: async () => ({
        ...FEATURE_ROUTE_DEFAULTS,
        features_json: featuresJson,
        version: "fv2",
      }) };
    }
    return { ok: true, json: async () => ({}) };
  };
  BA.toggleNewMission(); // the successful create above closed it: reopen it
  await new Promise((resolve) => setImmediate(resolve));
  const formAgain = document.getElementById("missionform");
  formAgain.querySelector('[name="mission_id"]').value = "m-new";
  const depAgain = formAgain.querySelector('[name="depends_on"]');
  depAgain.value = "m0";
  calls.length = 0;
  await BA.createMissionForm();
  check("the refused create posts exactly one body", calls.length === 1);
  check("the refusal names the closing pair next to the field",
    document.getElementById("missionform-dep-error").textContent ===
      "Mission dependency closes a cycle: m-new -> m0 -> m-new");
  check("the refusal is also shown in the board state",
    stateStub.textContent.indexOf("closes a cycle") !== -1);
  check("the refused create leaves the form open", !!document.getElementById("missionform"));
  check("the refused create keeps the typed ids", depAgain.value === "m0");
  globalThis.fetch = createFetch;

  // --- 5. the dependency guard of "Run next" and of the queue move ---
  // The guard refuses while the plain body is sent, and accepts the resend that
  // carries run_anyway / move_dependents.
  const firstFetch = globalThis.fetch;
  const guardCalls = [];
  const startRefusal = {
    status: "failed",
    error: { code: "guard", message: "mission m1 is waiting: pass run_anyway: true" },
    code: "guard", status_code: 403,
    reason: "mission m1 is waiting: pass run_anyway: true",
    blocked_by: ["m0"],
    dependency_state: "waiting",
  };
  const moveRefusal = {
    status: "failed",
    error: { code: "guard", message: "mission m1 must run after m2" },
    code: "guard", status_code: 403,
    reason: "mission m1 must run after m2",
    blocked_by: ["m2"],
  };
  globalThis.fetch = (url, options) => {
    const u = String(url);
    guardCalls.push({ url: u, options: options || {} });
    if (u === "/api/board/queue/first" || u === "/api/board/queue/last") {
      const body = JSON.parse(options.body);
      if (body.run_anyway || body.move_dependents) {
        return { ok: true, status: 200, json: async () => ({ queue: [], version: "v3" }) };
      }
      const refusal = u.endsWith("/first") ? startRefusal : moveRefusal;
      return { ok: false, status: 403, json: async () => refusal };
    }
    if (u === "/api/board/features") {
      return { ok: true, status: 200, json: async () => ({
        ...FEATURE_ROUTE_DEFAULTS, features_json: featuresJson, version: "fv2",
      }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  globalThis.BoardDnd.ensureQueued = async () => ({
    queue: [
      { position: 0, mission_id: "m1", enabled: true },
      { position: 1, mission_id: "m2", enabled: true, depends_on: ["m1"] },
    ],
    version: "v9",
  });

  // "Run next" of a waiting mission: the question, then the run_anyway resend.
  confirmCalls.length = 0;
  guardCalls.length = 0;
  await BA.doAction("first", "m1");
  const firstCalls = guardCalls.filter((c) => c.url === "/api/board/queue/first");
  check("Run next asks once, naming the dependency id",
    confirmCalls.length === 1
    && confirmCalls[0].indexOf("run anyway?") !== -1
    && confirmCalls[0].indexOf("m0") !== -1);
  check("Run next sends two guarded bodies", firstCalls.length === 2);
  check("the plain body carries no run_anyway key",
    !("run_anyway" in JSON.parse(firstCalls[0].options.body)));
  check("the confirm resends with run_anyway: true",
    JSON.parse(firstCalls[1].options.body).run_anyway === true);
  check("the run-anyway resend keeps the dashboard header",
    firstCalls[1].options.headers["X-AHAWR-Dashboard"] === "1");

  // Refused confirm: no second request, the refusal message is visible.
  window.confirm = (msg) => { confirmCalls.push(msg); return false; };
  confirmCalls.length = 0;
  guardCalls.length = 0;
  await BA.doAction("first", "m1");
  check("a refused run-anyway confirm sends only the first request",
    guardCalls.filter((c) => c.url === "/api/board/queue/first").length === 1);
  check("the refused run shows the message naming the ids",
    stateStub.textContent.indexOf("Run refused: m1 still waits for m0") !== -1);
  window.confirm = (msg) => { confirmCalls.push(msg); return true; };

  // A move the dependents stand in the way: the "move them too" question and the
  // move_dependents resend.
  confirmCalls.length = 0;
  guardCalls.length = 0;
  await BA.doAction("last", "m1");
  const lastCalls = guardCalls.filter((c) => c.url === "/api/board/queue/last");
  check("the move asks about the dependents by id",
    confirmCalls.length === 1 && confirmCalls[0].indexOf("m2 (1) depend on it") !== -1);
  check("the move resend carries move_dependents: true",
    lastCalls.length === 2 && JSON.parse(lastCalls[1].options.body).move_dependents === true);
  check("the first move body has no move_dependents key",
    !("move_dependents" in JSON.parse(lastCalls[0].options.body)));

  confirmCalls.length = 0;
  guardCalls.length = 0;
  window.confirm = (msg) => { confirmCalls.push(msg); return false; };
  await BA.doAction("last", "m1");
  check("a refused 'move them too' confirm sends nothing more",
    guardCalls.filter((c) => c.url === "/api/board/queue/last").length === 1);
  check("the refused move shows the order message",
    stateStub.textContent.indexOf("The move is refused: m1 must run after m2") !== -1);
  window.confirm = (msg) => { confirmCalls.push(msg); return true; };

  globalThis.fetch = firstFetch;
  delete globalThis.BoardDnd.ensureQueued;

  BA.toggleNewMission(); // close the form
  BA.stopAutoRefresh(); // clear the interval so node exits
})().catch((e) => { console.error(e.message); process.exit(1); });

console.log("all ok");
