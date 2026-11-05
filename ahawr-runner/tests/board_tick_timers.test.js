// Characterization tests for the "tick" polling timers (T008 of the review
// mission) plus the teardown added by T009: what board_actions.js and
// board_views.js do with their auto-refresh interval.  Fake timers only:
// globalThis.setInterval / clearInterval are replaced, so no real wait happens
// and nothing hangs.
// Run: cd ahawr-runner && node --test tests/board_tick_timers.test.js
//
// Pinned behaviours:
//   board_actions.js (board_actions.js:390-415):
//   - startAutoRefresh creates exactly one interval of 10000 ms, and a second
//     call with the timer already running creates no new one (the refreshTimer
//     guard); stopAutoRefresh clears it;
//   - each tick calls refreshBoard() (which fetches /api/board/missions,
//     /api/board/queue and per-mission detail/stats) unless a guard blocks it:
//     BoardDnd.DndState.dragId set, dirtyForm() true, body.dataset.editing "1",
//     or document.hidden;
//   - the stored handle (refreshTimer) is cleared on 'pagehide' and on a
//     'hashchange' whose hash is no longer the queue screen ("#board" or empty);
//     a 'hashchange' back to "#board" or empty restarts it through
//     startAutoRefresh; the teardown listeners are wired once (teardownWired).
//   board_views.js (board_views.js:368-401):
//   - init() runs tick() once immediately and then starts one interval of
//     REFRESH_MS = 15000 ms, each tick fetching /api/board/summary;
//   - the handle is stored as tickTimer and cleared on 'pagehide' and on a
//     'hashchange' whose hash is not a #board/view/... fragment; a
//     'hashchange' re-entering a view restarts it through startTick, whose
//     tickTimer guard keeps exactly one live timer.
//
// NOT TESTED here: dashboard.html loadRuns (dashboard.html:1483, setInterval
// loadRuns 3000).  T008 did not pin it: the script lives inline in
// dashboard.html inside an IIFE, loadRuns is bound to module-local state
// (S, TAB, BACK) and DOM helpers ($, esc, renderListOrSearch), and the repo's
// only node-executable precedent for dashboard.html JS is the extraction of
// pure functions between marker comments (tests/test_dashboard_js.py);
// loadRuns needs a whole DOM, so it cannot be extracted that way.  T009 left
// it unchanged because T008 did not pin it.

"use strict";
const fs = require("fs");
const path = require("node:path");
const { test } = require("node:test");
const assert = require("node:assert/strict");

const SRC = path.join(__dirname, "..", "src", "ahawr_runner");
const ACTIONS_MODULE = path.join(SRC, "board_actions.js");
const VIEWS_MODULE = path.join(SRC, "board_views.js");
const ACTIONS_SOURCE = fs.readFileSync(ACTIONS_MODULE, "utf8");
const VIEWS_SOURCE = fs.readFileSync(VIEWS_MODULE, "utf8");

// --- fake timer environment: every interval/clear is recorded, nothing real ---
function makeTimerEnv() {
  const env = { intervals: [], cleared: [], nextHandle: 1 };
  env.setInterval = (fn, ms) => {
    const handle = env.nextHandle++;
    env.intervals.push({ fn, ms, handle });
    return handle;
  };
  env.clearInterval = (handle) => { env.cleared.push(handle); };
  return env;
}

// --- fake window: records listeners, dispatches events by name ---
function makeFakeWindow() {
  const listeners = new Map();
  return {
    listeners,
    addEventListener(name, fn) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(fn);
    },
    dispatch(name) {
      for (const fn of listeners.get(name) || []) fn({ type: name });
    },
  };
}

// --- board_actions.js environment ---
function fakeEl(tag) {
  const el = { tagName: tag, dataset: {}, className: "", textContent: "",
    children: [], listeners: {} };
  el.appendChild = (c) => { el.children.push(c); return el; };
  el.addEventListener = (t, fn) => { el.listeners[t] = fn; };
  el.querySelectorAll = () => [];
  return el;
}

function actionsEnv() {
  const timer = makeTimerEnv();
  const byId = {};
  const board = fakeEl("div"); board.id = "board"; byId.board = board;
  const queueBox = fakeEl("div"); queueBox.id = "boardqueue"; byId.boardqueue = queueBox;
  const stateEl = fakeEl("div"); stateEl.id = "boardstate"; byId.boardstate = stateEl;
  const window = makeFakeWindow();
  const fetchCalls = [];
  globalThis.document = {
    hidden: false,
    body: { dataset: {} },
    getElementById: (id) => byId[id] || null,
    createElement: fakeEl,
  };
  globalThis.window = window;
  // The queue screen the teardown listener checks against (board_actions.js
  // wireTeardown): a hash other than "#board" or "" counts as "left".
  globalThis.location = { hash: "#board" };
  globalThis.BoardDnd = { DndState: { data: null, dragId: null }, attachDnd: async () => {} };
  globalThis.fetch = async (url, options) => {
    fetchCalls.push({ url: String(url), options: options || {} });
    return { ok: true, status: 200, json: async () => ({ missions: [], queue: [] }) };
  };
  globalThis.setInterval = timer.setInterval;
  globalThis.clearInterval = timer.clearInterval;
  return { timer, fetchCalls, window };
}

function restoreEnv(saved) {
  for (const key of ["document", "window", "location", "BoardDnd", "fetch", "setInterval", "clearInterval"]) {
    if (saved[key] === undefined) delete globalThis[key];
    else globalThis[key] = saved[key];
  }
}

function loadActions() {
  delete require.cache[require.resolve(ACTIONS_MODULE)];
  return require(ACTIONS_MODULE);
}

// --- board_views.js environment: minimal DOM enough for init() ---
function viewsEnv() {
  const timer = makeTimerEnv();
  const host = {
    children: [],
    append: (c) => { host.children.push(c); },
    replaceChildren: (...cs) => { host.children = cs.filter(Boolean); },
    addEventListener: () => {},
    querySelectorAll: () => [],
    after: () => {},
  };
  const body = {
    append: () => {},
    replaceChildren: () => {},
    addEventListener: () => {},
    querySelectorAll: () => [],
  };
  const byId = { boardviews: host, boardviewbody: body };
  const window = makeFakeWindow();
  globalThis.window = window;
  globalThis.document = {
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => ({
      tag, className: "", textContent: "", hidden: false, dataset: {},
      setAttribute: () => {}, append: () => {},
    }),
    documentElement: { setAttribute: () => {}, removeAttribute: () => {} },
  };
  const fetchCalls = [];
  globalThis.fetch = async (url) => {
    fetchCalls.push(String(url));
    return { ok: true, status: 200, json: async () => ({ missions: [], projects: [] }) };
  };
  globalThis.location = { hash: "" };
  globalThis.history = { replaceState: () => {} };
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.BoardI18n = {
    languages: [{ code: "en", name: "English" }],
    getLanguage: () => "en",
    setLanguage: () => {},
    onChange: () => {},
    load: () => Promise.resolve(),
  };
  globalThis.setInterval = timer.setInterval;
  globalThis.clearInterval = timer.clearInterval;
  return { timer, host, fetchCalls, window };
}

function loadViews() {
  delete require.cache[require.resolve(VIEWS_MODULE)];
  return require(VIEWS_MODULE);
}

// --- board_actions: the interval start and its interval in ms ---
test("board_actions tick starts one interval of 10000 ms, exactly once per start", () => {
  const saved = { document: globalThis.document, window: globalThis.window,
    BoardDnd: globalThis.BoardDnd, fetch: globalThis.fetch,
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  try {
    const env = actionsEnv();
    const BA = loadActions();
    BA.startAutoRefresh();
    assert.equal(env.timer.intervals.length, 1, "startAutoRefresh creates one interval");
    assert.equal(env.timer.intervals[0].ms, 10000, "the interval is 10000 ms (board_actions.js:392)");
    BA.startAutoRefresh(); // the refreshTimer guard pins: no second interval
    assert.equal(env.timer.intervals.length, 1, "a second startAutoRefresh adds no interval");
    BA.stopAutoRefresh();
    assert.deepEqual(env.timer.cleared, [env.timer.intervals[0].handle],
      "stopAutoRefresh clears exactly that handle (board_actions.js:398)");
  } finally {
    restoreEnv(saved);
  }
});

// --- what each tick calls, and the guards that skip it ---
// Note: tick() is synchronous (board_actions.js:403) and calls refreshBoard()
// without awaiting it, so after invoking the captured tick we must let the
// microtask queue run (setImmediate/setTimeout once — a fake-timer test never
// waits on the real 10000 ms interval, only on one event-loop turn).
test("board_actions tick calls refreshBoard fetches unless a guard blocks it", async () => {
  const saved = { document: globalThis.document, window: globalThis.window,
    BoardDnd: globalThis.BoardDnd, fetch: globalThis.fetch,
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  try {
    const env = actionsEnv();
    const BA = loadActions();
    // the guard set up in the test below opens the form with a dirty field
    BA.startAutoRefresh();
    const capturedTick = env.timer.intervals[0].fn;

    // helper: run the synchronous tick, then give refreshBoard's async chain
    // one event-loop turn to reach its fetches (the fake 10000 ms interval is
    // never actually waited on)
    const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

    // no guard: the tick fetches missions and queue (refreshBoard -> loadBoardData)
    capturedTick();
    await settle();
    const urls = env.fetchCalls.map((c) => c.url);
    assert.ok(urls.includes("/api/board/missions"), "tick fetches /api/board/missions");
    assert.ok(urls.includes("/api/board/queue"), "tick fetches /api/board/queue");

    // guard 1: an active drag
    env.fetchCalls.length = 0;
    globalThis.BoardDnd.DndState.dragId = "m1";
    capturedTick();
    await settle();
    assert.equal(env.fetchCalls.length, 0, "tick skips the fetch while a drag is active");
    globalThis.BoardDnd.DndState.dragId = null;

    // guard 2: the launch settings window open (body.dataset.editing === "1")
    env.fetchCalls.length = 0;
    globalThis.document.body.dataset.editing = "1";
    capturedTick();
    await settle();
    assert.equal(env.fetchCalls.length, 0, "tick skips the fetch while editing is open");
    delete globalThis.document.body.dataset.editing;

    // guard 3: the page hidden
    env.fetchCalls.length = 0;
    globalThis.document.hidden = true;
    capturedTick();
    await settle();
    assert.equal(env.fetchCalls.length, 0, "tick skips the fetch while the page is hidden");
    globalThis.document.hidden = false;

    // guards cleared: the tick fetches again
    env.fetchCalls.length = 0;
    capturedTick();
    await settle();
    assert.ok(env.fetchCalls.length > 0, "tick fetches again once no guard blocks it");
  } finally {
    restoreEnv(saved);
  }
});

// --- board_views: the interval start and its interval in ms ---
test("board_views tick starts one interval of 15000 ms and ticks once immediately", async () => {
  const saved = { document: globalThis.document, fetch: globalThis.fetch,
    location: globalThis.location, history: globalThis.history,
    localStorage: globalThis.localStorage, BoardI18n: globalThis.BoardI18n,
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  try {
    const env = viewsEnv();
    const V = loadViews();
    assert.equal(V.REFRESH_MS, 15000, "REFRESH_MS exported as 15000 (board_views.js:12)");
    await V.init(env.host);
    // the immediate tick() fetches /api/board/summary before setInterval is called
    assert.ok(env.fetchCalls.includes("/api/board/summary"), "init ticks once immediately with /api/board/summary");
    assert.equal(env.timer.intervals.length, 1, "init starts exactly one interval");
    assert.equal(env.timer.intervals[0].ms, 15000, "the interval is REFRESH_MS = 15000 (board_views.js:387)");
    // the captured tick fetches the summary again when invoked manually
    env.fetchCalls.length = 0;
    await env.timer.intervals[0].fn();
    assert.ok(env.fetchCalls.includes("/api/board/summary"), "each tick fetches /api/board/summary");
  } finally {
    restoreEnv(saved);
  }
});

// --- T009: the tick timer is cleared on pagehide and on a hashchange that
// leaves the view that started it, and restarted when the hash re-enters it
// (board_actions.js wireTeardown, board_views.js init teardown).
test("T009: board_actions tick is cleared on pagehide and when the hash leaves the queue", () => {
  const saved = { document: globalThis.document, window: globalThis.window,
    location: globalThis.location, BoardDnd: globalThis.BoardDnd, fetch: globalThis.fetch,
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  try {
    const env = actionsEnv();
    const BA = loadActions();
    BA.startAutoRefresh();
    const first = env.timer.intervals[0].handle;

    // a hashchange that stays on the queue screen does NOT clear the timer
    globalThis.location.hash = "#board";
    env.window.dispatch("hashchange");
    assert.equal(env.timer.cleared.length, 0, "staying on #board keeps the timer live");
    assert.equal(env.timer.intervals.length, 1, "staying on #board starts no second timer");

    // leaving the queue screen (a mission deep link) clears it
    globalThis.location.hash = "#board/m1";
    env.window.dispatch("hashchange");
    assert.deepEqual(env.timer.cleared, [first], "leaving the queue clears the stored handle");

    // re-entering the queue: the interval is live again, and the same hashchange
    // twice still starts exactly one timer (the refreshTimer guard)
    globalThis.location.hash = "#board";
    env.window.dispatch("hashchange");
    env.window.dispatch("hashchange");
    const second = env.timer.intervals[1].handle;
    assert.equal(env.timer.intervals.length, 2, "re-entry restarts the interval exactly once");
    assert.deepEqual(env.timer.cleared, [first], "the old handle is not cleared twice");

    // leaving again clears the restarted timer; pagehide then finds nothing new
    globalThis.location.hash = "#board/m1";
    env.window.dispatch("hashchange");
    assert.deepEqual(env.timer.cleared, [first, second], "the restarted timer is cleared exactly once");
    env.window.dispatch("pagehide");
    assert.equal(env.timer.cleared.length, 2, "pagehide after an already-cleared timer clears nothing");

    // the teardown listeners are registered in the source (board_actions.js)
    assert.ok(ACTIONS_SOURCE.includes('addEventListener("pagehide", stopAutoRefresh)'),
      "board_actions.js wires pagehide to stopAutoRefresh");
    assert.ok(ACTIONS_SOURCE.includes('window.addEventListener("hashchange"'),
      "board_actions.js registers a hashchange teardown listener");
  } finally {
    restoreEnv(saved);
  }
});

test("T009: board_views tick is cleared on pagehide and when the hash leaves the views host", async () => {
  const saved = { document: globalThis.document, window: globalThis.window,
    fetch: globalThis.fetch, location: globalThis.location, history: globalThis.history,
    localStorage: globalThis.localStorage, BoardI18n: globalThis.BoardI18n,
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
  try {
    const env = viewsEnv();
    const V = loadViews();
    await V.init(env.host);
    assert.equal(env.timer.intervals.length, 1, "init starts one interval");
    const first = env.timer.intervals[0].handle;

    // switching between views keeps the timer live (one live timer, no second)
    globalThis.location.hash = "#board/view/kan";
    env.window.dispatch("hashchange");
    env.window.dispatch("hashchange");
    assert.equal(env.timer.cleared.length, 0, "a view switch does not clear the timer");
    assert.equal(env.timer.intervals.length, 1, "a view switch starts no second interval");

    // leaving the views host (a mission deep link) clears it
    globalThis.location.hash = "#board/m1";
    env.window.dispatch("hashchange");
    assert.deepEqual(env.timer.cleared, [first], "leaving the views host clears the stored handle");

    // re-entering a view: the interval is live again, and dispatching the same
    // view hash twice still starts exactly one timer (the tickTimer guard)
    globalThis.location.hash = "#board/view/proj";
    env.window.dispatch("hashchange");
    env.window.dispatch("hashchange");
    const second = env.timer.intervals[1].handle;
    assert.equal(env.timer.intervals.length, 2, "re-entry restarts the interval exactly once");
    assert.deepEqual(env.timer.cleared, [first], "the old handle is not cleared twice");

    // leaving again clears the restarted timer; pagehide then finds nothing new
    globalThis.location.hash = "#board/m1";
    env.window.dispatch("hashchange");
    assert.deepEqual(env.timer.cleared, [first, second], "the restarted timer is cleared exactly once");
    env.window.dispatch("pagehide");
    assert.equal(env.timer.cleared.length, 2, "pagehide after an already-cleared timer clears nothing");

    assert.ok(VIEWS_SOURCE.includes("clearInterval(tickTimer)"),
      "board_views.js clears the stored tickTimer handle");
    assert.ok(VIEWS_SOURCE.includes('window.addEventListener("pagehide", stopTick)'),
      "board_views.js wires pagehide to stopTick");
  } finally {
    restoreEnv(saved);
  }
});

// --- source-text pins: exactly one interval per module, named constants ---
test("both tick timers use a single setInterval with their literal ms value", () => {
  const actionIntervals = ACTIONS_SOURCE.match(/setInterval\([^)]*\)/g) || [];
  assert.deepEqual(actionIntervals, ["setInterval(tick, 10000)"],
    "board_actions.js has exactly one setInterval: tick at 10000 ms");
  const viewsIntervals = VIEWS_SOURCE.match(/setInterval\([^)]*\)/g) || [];
  assert.deepEqual(viewsIntervals, ["setInterval(tick, REFRESH_MS)"],
    "board_views.js has exactly one setInterval: tick at REFRESH_MS");
});
