// Node test for board_actions.js (card actions, confirmations, auto-refresh).
// Run: node claude-runner/tests/board_actions.test.js
//
// Stubs fetch and document (the module is required through its CommonJS
// export, so hookUp() does not run) and proves:
//   1. a disable of a running mission requests ?confirm=true and the
//      X-AHAWR-Dashboard header;
//   2. a dangerous button routes through confirmActions (window.confirm is
//      consulted before the request; the request is skipped when it refuses);
//   3. tick() returns early (no request) when BoardDnd.DndState.dragId is
//      set, and when the form is dirty.

"use strict";
const path = require("path");

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}

// --- fetch stub: records every call and answers minimally ---
const calls = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options: options || {} });
  const u = String(url);
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
        { position: 0, mission_id: "m1", enabled: true },
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
};
globalThis.BoardDnd = { DndState: { data: null, dragId: null },
  attachDnd: async () => {} };

require(path.join(__dirname, "i18n_stub.js"));
const BA = require(path.join(__dirname, "..", "src", "claude_runner", "board_actions.js"));

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
  BA.toggleNewMission(); // close the form
  BA.stopAutoRefresh(); // clear the interval so node exits
})().catch((e) => { console.error(e.message); process.exit(1); });

console.log("all ok");
