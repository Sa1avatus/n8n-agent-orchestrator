// Node tests for board_stats_popup.js (T010): the number formatting with units,
// the "-" rendering of a missing figure with its reason, the tab choice of the
// scope, the backfill labels of the Engine tab, and the load timer that close
// clears.  Fake timers and a fake document only: no real wait, no real DOM.
// Run: cd ahawr-runner && node --test tests/board_stats_popup.test.js
"use strict";
const fs = require("fs");
const path = require("node:path");
const { test } = require("node:test");
const assert = require("node:assert/strict");
require("./i18n_stub.js"); // t() reads the English table, so L() returns real texts

const SRC = path.join(__dirname, "..", "src", "ahawr_runner");
const MODULE = path.join(SRC, "board_stats_popup.js");
const SOURCE = fs.readFileSync(MODULE, "utf8");

function loadPopup() {
  delete require.cache[require.resolve(MODULE)];
  return require(MODULE);
}

function fakeElement(tag) {
  const el = {
    tag, id: "", className: "", innerHTML: "", textContent: "",
    dataset: {}, children: [], listeners: {}, bySelector: {}, focused: 0,
  };
  el.appendChild = (child) => { el.children.push(child); return el; };
  el.addEventListener = (type, fn) => {
    (el.listeners[type] || (el.listeners[type] = [])).push(fn);
    return el;
  };
  el.focus = () => { el.focused += 1; };
  el.querySelector = (selector) => {
    if (!el.bySelector[selector]) el.bySelector[selector] = fakeElement(selector);
    return el.bySelector[selector];
  };
  return el;
}

// One popup environment: fake document, fake timers, fake fetch.
// payload: the JSON the stats url answers; pending: the request never settles.
function popupEnv(payload, pending = false) {
  const saved = {
    document: globalThis.document, fetch: globalThis.fetch,
    setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout,
  };
  const board = fakeElement("div");
  board.id = "board";
  globalThis.document = {
    body: fakeElement("body"),
    activeElement: null,
    getElementById: (id) => (id === "board" ? board : null),
    createElement: fakeElement,
  };
  const timers = [];
  const cleared = [];
  globalThis.setTimeout = (fn, ms) => {
    const handle = { fn, ms, id: timers.length + 1 };
    timers.push(handle);
    return handle.id;
  };
  globalThis.clearTimeout = (id) => { cleared.push(id); };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), options: options || {} });
    if (pending) return new Promise(() => {});
    return { ok: true, status: 200, json: async () => payload };
  };
  return { board, timers, cleared, calls, restore: () => restoreEnv(saved) };
}

function restoreEnv(saved) {
  for (const key of Object.keys(saved)) {
    if (saved[key] === undefined) delete globalThis[key];
    else globalThis[key] = saved[key];
  }
}

// --- formatting: every number carries the unit of the bench table ---
test("formatValue renders each figure with its unit", () => {
  const P = loadPopup();
  assert.equal(P.formatValue(12.34, "minutes"), "12.3 min");
  assert.equal(P.formatValue(3.5, "hours"), "3.5 h");
  assert.equal(P.formatValue(41.7, "tok_s"), "41.7 tok/s");
  assert.equal(P.formatValue(1234, "tokens"), "1,234 tok");
  assert.equal(P.formatValue(2048, "mib"), "2,048 MiB");
  assert.equal(P.formatValue(8.4, "usd"), "$8.40");
  assert.equal(P.formatValue(12.5, "percent"), "12.5%");
  assert.equal(P.formatValue(4, "count"), "4");
  assert.equal(P.formatValue(2.7, "score"), "2.7");
  assert.equal(P.formatValue(true, "bool"), "yes");
  assert.equal(P.formatValue(false, "bool"), "no");
});

// --- a missing figure: "-", with the reason the server carries as the hover ---
test("a missing figure renders '-' and keeps its reason as the hover title", () => {
  const P = loadPopup();
  const html = P.summaryHtml({
    closed_tasks: 4,
    reasons: { avg_final_review_score: "no reviewed tasks" },
  });
  assert.ok(html.includes('<b title="">4</b>'), "a present figure renders its value");
  assert.ok(html.includes("<b title=\"no reviewed tasks\">-</b>"),
    "the missing figure is '-' with the reason in the title");
  assert.ok(!html.includes(">0<"), "a missing figure never renders as zero");
});

// --- the rows tab: Tasks for a mission, Attempts for a task, Missions for a project ---
test("the rows tab is Tasks for a mission, Attempts for a task, Missions for a project", () => {
  const P = loadPopup();
  const bar = (kind) => P.tabHtml("rows", { kind });
  assert.ok(bar("mission").includes("Tasks"), "mission scope labels the rows tab Tasks");
  assert.ok(bar("task").includes("Attempts"), "task scope labels the rows tab Attempts");
  assert.ok(bar("project").includes("Missions"), "project scope labels the rows tab Missions");
});

test("the rows tab renders the rows of the scope and the empty note when there are none", () => {
  const P = loadPopup();
  const missionRows = P.rowsHtml("mission", {
    tasks: [{ task_id: "t-1", attempts: 2, first_review_score: 3.1, final_review_score: 4, passed: true }],
  });
  assert.ok(missionRows.startsWith("<table class=\"sp-table\">"), "mission rows are a table");
  assert.ok(missionRows.includes("<td title=\"\">3.1</td>"), "a score keeps one decimal");
  assert.ok(missionRows.includes("<td title=\"\">yes</td>"), "passed renders as yes");

  const taskRows = P.rowsHtml("task", {
    attempts: [{
      attempt: 1, runs: ["r-1", "r-2"],
      figures: { steps: 12, output_tokens: 4321, tok_per_s: 39.2, cost_usd: 1.25 },
    }],
  });
  assert.ok(taskRows.includes("<td title=\"\">2</td>"), "the run count of an attempt is a number");
  assert.ok(taskRows.includes("<td title=\"\">4,321 tok</td>"), "output tokens carry the unit");
  assert.ok(taskRows.includes("<td title=\"\">39.2 tok/s</td>"), "generation carries tok/s");
  assert.ok(taskRows.includes("<td title=\"\">$1.25</td>"), "cost carries the dollar sign");

  const empty = P.rowsHtml("mission", {});
  assert.ok(empty.includes("No reviewed tasks in this mission yet."), "no rows render the empty note");
});

// --- the Engine tab: the backfill sources are labelled, not raw keys ---
test("engine samples are labelled per source, backfill included", () => {
  const P = loadPopup();
  const html = P.engineHtml({
    engine: {
      median_tok_s: 38.4, accepted_share_percent: 72.0, expert_slots: 3,
      min_free_vram_mib: 2048, low_vram_count: 1,
      samples_by_source: { sampled: 3, "backfill-matched": 5, "backfill-segment": 2 },
    },
  });
  assert.ok(html.includes("38.4 tok/s"), "the median tok/s carries its unit");
  assert.ok(html.includes("2,048 MiB"), "free VRAM carries MiB");
  assert.ok(html.includes("(backfill-matched)"), "the backfill source is labelled with its name");
  assert.ok(html.includes("backfilled from stored runs (backfill-matched) 5"),
    "the backfill label explains where the rows come from");
  assert.ok(html.includes("(sampled)"), "the live source is labelled too");
});

test("the Engine tab with no samples states the reason, never zeros", () => {
  const P = loadPopup();
  const html = P.engineHtml({ engine: null, engine_reason: "no samples" });
  assert.ok(html.includes("No engine samples"), "the empty engine tab is a note");
  assert.ok(html.includes("no samples"), "the server reason is shown");
  assert.ok(!html.includes(">0<"), "no zeros are printed for a missing engine");
});

// --- the load timer: started on open, cleared on close and on arrival ---
test("openStats starts one bounded load timer and closeStats clears it", async () => {
  const env = popupEnv(null, true);
  try {
    const P = loadPopup();
    const trigger = fakeElement("button");
    P.openStats({ kind: "mission", missionId: "m-1" }, trigger);
    assert.equal(env.timers.length, 1, "openStats starts exactly one load timer");
    assert.equal(env.timers[0].ms, P.LOAD_TIMEOUT_MS,
      `the timer is LOAD_TIMEOUT_MS = ${P.LOAD_TIMEOUT_MS} ms`);
    P.closeStats();
    assert.deepEqual(env.cleared, [env.timers[0].id], "closeStats clears that timer handle");
    assert.equal(trigger.focused, 1, "closeStats returns the focus to the opening button");
  } finally {
    env.restore();
  }
});

test("the fired load timer is not tracked any more, so closeStats clears nothing twice", async () => {
  const env = popupEnv({ figures: { closed_tasks: 2 } });
  try {
    const P = loadPopup();
    const trigger = fakeElement("button");
    P.openStats({ kind: "mission", missionId: "m-1" }, trigger);
    // fire the timeout while the figures are still loading: the note is shown
    env.timers[0].fn();
    const body = env.board.children[0].querySelector(".sp-body");
    assert.ok(body.innerHTML.includes("Loading… the request is slow"), "the slow note is rendered");
    assert.equal(env.timers.length, 1, "the load is bounded by exactly one timer");
    assert.equal(env.cleared.length, 0, "the fired timer is dropped, not cleared again");
    P.closeStats();
    assert.deepEqual(env.cleared, [], "closeStats clears nothing after the timer already fired");
  } finally {
    env.restore();
  }
});

test("the figures that arrive clear the load timer", async () => {
  const env = popupEnv({ tasks: [], engine: null, engine_reason: "no samples" });
  try {
    const P = loadPopup();
    await P.loadStats({ kind: "mission", missionId: "m-1" });
    assert.equal(env.timers.length, 1, "loadStats starts one load timer");
    assert.deepEqual(env.cleared, [env.timers[0].id], "the settled load clears the timer");
  } finally {
    env.restore();
  }
});

// --- Esc closes, the focus goes back to the button that opened the popup ---
test("Esc closes the popup and returns the focus to the opening button", () => {
  const env = popupEnv({ tasks: [] });
  try {
    const P = loadPopup();
    const trigger = fakeElement("button");
    P.openStats({ kind: "mission", missionId: "m-1" }, trigger);
    const scrim = env.board.children[0];
    assert.ok(scrim.className.includes("open"), "the scrim is open after openStats");
    for (const listener of scrim.listeners.keydown) listener({ key: "Escape" });
    assert.ok(!scrim.className.includes("open"), "Escape closes the scrim");
    assert.equal(trigger.focused, 1, "Escape returns the focus to the opening button");
    P.closeStats();
    assert.equal(trigger.focused, 1, "a second close does not focus the button again");
  } finally {
    env.restore();
  }
});

// --- attach: the Statistics button, gated by the stats_popup flag (T011) ---
test("attach adds the Statistics button when stats_popup is enabled", async () => {
  const env = popupEnv({ features_json: '{"stats_popup":{"enabled":true}}', tasks: [] });
  try {
    const P = loadPopup();
    const host = fakeElement("div");
    assert.equal(await P.attach(host, { kind: "mission", missionId: "m-1" }), true,
      "attach returns true when the flag is on");
    const button = host.children[0];
    assert.equal(host.children.length, 1, "exactly one button lands on the host");
    assert.equal(button.tag, "button", "the button is a button element");
    assert.equal(button.className, "sp-btn", "the button carries the sp-btn class");
    assert.equal(button.textContent, "Statistics",
      "the button text is the translated Statistics label");
    assert.equal(button.listeners.click.length, 1, "the button carries one click handler");
    assert.equal(host.dataset.statsAttached === "1", true,
      "the host is marked so a re-render adds no second button");
    assert.equal(await P.attach(host, { kind: "mission", missionId: "m-1" }), true,
      "re-attach on the same host adds nothing new");
    assert.equal(host.children.length, 1, "the second attach adds no button");
  } finally {
    env.restore();
  }
});

test("attach adds no button when the flag is off (default) and skips unknown scopes", async () => {
  const env = popupEnv({ tasks: [] });
  try {
    const P = loadPopup();
    const host = fakeElement("div");
    assert.equal(await P.attach(host, { kind: "project", project: "p-1" }), false,
      "stats_popup defaults to off, so no button appears");
    assert.equal(host.children.length, 0, "the host gains nothing with the flag off");
    assert.equal(await P.attach(host, { kind: "not-a-scope" }), false,
      "an unknown scope kind attaches nothing");
  } finally {
    env.restore();
  }
});

test("a host without appendChild is refused before any flag is fetched", async () => {
  const env = popupEnv({ tasks: [] });
  try {
    const P = loadPopup();
    assert.equal(await P.attach({ innerHTML: "" }, { kind: "mission", missionId: "m-1" }), false,
      "a stub host without appendChild is refused");
    assert.equal(env.calls.length, 0, "no flag fetch for a refused host");
  } finally {
    env.restore();
  }
});

test("clicking the attached button opens the popup of its scope", async () => {
  const env = popupEnv({ features_json: '{"stats_popup":{"enabled":true}}', tasks: [] });
  try {
    const P = loadPopup();
    const host = fakeElement("div");
    await P.attach(host, { kind: "mission", missionId: "m-1" });
    host.children[0].listeners.click[0]();
    const scrim = env.board.children[0];
    assert.ok(scrim.className.includes("open"), "the click opens the stats scrim");
    assert.ok(scrim.innerHTML.includes("Statistics"), "the scrim is titled Statistics");
    assert.ok(env.calls.some((call) => call.url.includes("/stats/detail")),
      "the click fetches the detail figures of the scope");
  } finally {
    env.restore();
  }
});

// --- the popup requests data with GET only ---
test("the popup issues no POST, PUT or DELETE", async () => {
  assert.ok(!/method:\s*"(POST|PUT|DELETE)"/.test(SOURCE),
    "the popup source contains a mutation method");
  const env = popupEnv({ features_json: '{"stats_popup":{"enabled":true}}', tasks: [] });
  try {
    const P = loadPopup();
    assert.equal(await P.attach(fakeElement("div"), { kind: "mission", missionId: "m-1" }), true,
      "the flag fetch of attach is a GET too");
    P.openStats({ kind: "mission", missionId: "m-1" }, fakeElement("button"));
    for (const call of env.calls) {
      assert.ok(!call.options.method || call.options.method === "GET",
        `${call.url} is not a GET`);
    }
    assert.ok(env.calls.length > 0, "the popup does fetch its figures");
  } finally {
    env.restore();
  }
});

// --- i18n: every key the popup translates exists in both tables and the bundle ---
test("every translated key of the popup is in the English, Russian and bundled tables", () => {
  const keys = [...new Set((SOURCE.match(/"((?:sp|fg)\.[a-z0-9_.-]+)"/g) || []).map((k) => k.slice(1, -1)))];
  assert.ok(keys.length > 0, "the popup has translated keys");
  const en = JSON.parse(fs.readFileSync(path.join(SRC, "board_i18n_en.json"), "utf8"));
  const ru = JSON.parse(fs.readFileSync(path.join(SRC, "board_i18n_ru.json"), "utf8"));
  const bundledSource = fs.readFileSync(path.join(SRC, "board_i18n.js"), "utf8");
  const bundled = new Set((bundledSource.match(/const BUNDLED_EN = \{([\s\S]*?)\n\};/)[1]
    .match(/"([^"]+)":/g) || []).map((k) => k.slice(1, -2)));
  const missingEn = keys.filter((k) => !Object.prototype.hasOwnProperty.call(en, k));
  const missingRu = keys.filter((k) => !Object.prototype.hasOwnProperty.call(ru, k));
  const missingBundled = keys.filter((k) => !bundled.has(k));
  assert.deepEqual(missingEn, [], "keys missing in board_i18n_en.json");
  assert.deepEqual(missingRu, [], "keys missing in board_i18n_ru.json");
  assert.deepEqual(missingBundled, [], "keys missing in BUNDLED_EN of board_i18n.js");
});

// --- no second esc or fetchJson in the popup: the shared ones are imported ---
test("the popup reuses the shared esc and fetchJson instead of defining its own", () => {
  assert.ok(/BoardLogic[\s\S]{0,40}esc/.test(SOURCE), "the popup imports esc from board_logic.js");
  assert.ok(/BoardActions[\s\S]{0,40}fetchJson/.test(SOURCE), "the popup imports fetchJson from board_actions.js");
  assert.ok(!/function\s+esc\s*\(/.test(SOURCE), "the popup does not define its own esc");
  assert.ok(!/function\s+fetchJson\s*\(/.test(SOURCE), "the popup does not define its own fetchJson");
  assert.ok(!/console\.log/.test(SOURCE), "the popup logs nothing with console.log");
});
