// Node test for board_ready_ui.js (the readiness panel and Start).
// Run: node claude-runner/tests/board_ready_ui.test.js
//
// Proves:
//   1. renderHtml renders the overall status (ok/warning/error) and each
//      check row with its reason, the preflight checkbox and the Start
//      button; the mission id falls back to readiness.mission_id;
//   2. an empty readiness renders the empty table;
//   3. a hostile reason is shown escaped and never as raw markup;
//   4. renderInto keeps the preflight flag and the last server text and
//      clears the shell's #boardstate line;
//   5. with an error check a Start click asks for confirmation: a
//      declined confirmation sends no fetch, an accepted one posts
//      exactly POST /api/board/missions/{id}/start?confirmed=true with
//      the preflight flag in the body; with no error check no
//      confirmation is asked and ?confirmed=true is not sent;
//   6. the server's refusal text is shown in the panel (.rdresult);
//   7. a failed readiness load renders the escaped error state.

"use strict";
const path = require("path");
const BR = require(path.join(__dirname, "..", "src", "claude_runner", "board_ready_ui.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}
function escOf(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- container stub (innerHTML + querySelector) ---
// querySelector derives elements from the rendered innerHTML: a selector
// matches only if its class/attribute is present in the HTML, so a template
// that lacks a control makes querySelector return null and the test fails.
function makeContainer() {
  const stubs = {};
  function makeStub() {
    const s = {
      className: "", textContent: "", value: "", checked: false, handlers: {},
      appendChild: () => {},
      addEventListener: (ev, fn) => { s.handlers[ev] = fn; },
      click: () => { if (s.handlers.click) s.handlers.click(); },
    };
    return s;
  }
  const c = {
    innerHTML: "",
    appendChild: () => {},
  };
  c.querySelector = (sel) => {
    const html = c.innerHTML;
    let ok = false;
    const cls = sel.match(/^\.([\w-]+)$/);
    const name = sel.match(/^\[name="([\w-]+)"\]$/);
    const tag = sel.match(/^([a-z]+)(?:\[name="([\w-]+)"\])?$/);
    if (cls) ok = html.indexOf('class="' + cls[1] + '"') !== -1;
    else if (name) ok = html.indexOf('name="' + name[1] + '"') !== -1;
    else if (tag) ok = html.indexOf("<" + tag[1]) !== -1 &&
      (!tag[2] || html.indexOf('name="' + tag[2] + '"') !== -1);
    if (!ok) return null;
    return stubs[sel] || (stubs[sel] = makeStub());
  };
  // Re-render (assigning innerHTML) keeps the same element stubs but clears
  // the checked flag (a fresh template input), mirroring the real DOM.
  let cur = "";
  Object.defineProperty(c, "innerHTML", {
    get: () => cur,
    set: (v) => {
      for (const sel of Object.keys(stubs)) {
        const s = stubs[sel];
        s.checked = false;
        s.textContent = "";
        s.handlers = {};
      }
      cur = String(v);
    },
  });
  return c;
}

// --- fetch stub: the start route and the readiness GET ---
const fetchLog = [];
let confirmResult = true;
let startAnswer = null;   // 200 body for POST /start
let startStatus = 200;    // status for POST /start
let readinessBody = null; // 200 body for GET .../readiness

globalThis.window = { confirm: () => confirmResult };

globalThis.fetch = (url, opts) => {
  const u = String(url);
  const method = (opts && opts.method) || "GET";
  fetchLog.push({ url: u, method,
    body: opts && opts.body ? opts.body : null,
    headers: (opts && opts.headers) || null });
  if (method === "POST" && /^\/api\/board\/missions\/[^/]+\/start(\?.*)?$/.test(u)) {
    const res = {
      ok: startStatus < 400,
      status: startStatus,
      json: () => Promise.resolve(startAnswer != null ? startAnswer : {}),
    };
    return Promise.resolve(res);
  }
  if (method === "GET" && u.endsWith("/readiness")) {
    const res = { ok: true, status: 200,
      json: () => Promise.resolve(readinessBody) };
    return Promise.resolve(res);
  }
  return Promise.reject(new Error("unexpected url: " + u));
};

const stateStub = { textContent: "", className: "" };
globalThis.document = {
  getElementById: (id) => (id === "boardstate" ? stateStub : null),
  createElement: () => ({ className: "", textContent: "", appendChild: () => {} }),
};

const HOSTILE_REASON = "<img src=x onerror=alert(1)>";

// --- 1. renderHtml: ok / warning / error rows with reasons ---
const mission = { mission_id: "m_running" };
const okItems = [
  { check: "runner", status: "ok", reason: "runner settings are available" },
  { check: "plan", status: "ok", reason: "the plan is present" },
];
const okHtml = BR.renderHtml(
  { mission_id: "m_running", status: "ok", items: okItems, errors: [] }, mission);
check("an ok readiness renders the ok status line and every check row",
  has(okHtml, '<div class="rdstatusline rd-ok">ok</div>') &&
  has(okHtml, '<tr class="rdrow rd-ok">') &&
  has(okHtml, "<td class=\"rdcheck\">runner</td>") &&
  has(okHtml, "<td>runner settings are available</td>") &&
  has(okHtml, "<td>the plan is present</td>"));
check("the preflight checkbox and the Start button are rendered",
  has(okHtml, '<input type="checkbox" name="preflight" value="1">') &&
  has(okHtml, '<button type="button" class="rd-start" data-mission="m_running">Start</button>'));

const warnItems = [
  { check: "preflight", status: "warning", reason: "the preflight is older than 3600 s" },
];
const warnHtml = BR.renderHtml(
  { mission_id: "m_running", status: "warning", items: warnItems, errors: [] }, mission);
check("a warning readiness renders the warning status line and row",
  has(warnHtml, '<div class="rdstatusline rd-warning">warning</div>') &&
  has(warnHtml, '<tr class="rdrow rd-warning">') &&
  has(warnHtml, "the preflight is older than 3600 s"));

const errItems = [
  { check: "live_state", status: "error", reason: "the live state is missing" },
];
const errHtml = BR.renderHtml(
  { mission_id: "m_running", status: "error", items: errItems,
    errors: errItems, confirmed_needed: true }, mission);
check("an error readiness renders the error status line and row",
  has(errHtml, '<div class="rdstatusline rd-error">error</div>') &&
  has(errHtml, '<tr class="rdrow rd-error">') &&
  has(errHtml, "the live state is missing"));

// the mission id falls back to readiness.mission_id when the mission has none
const fallbackHtml = BR.renderHtml(
  { mission_id: "m_fallback", status: "ok", items: [], errors: [] }, {});
check("the Start button falls back to readiness.mission_id",
  has(fallbackHtml, 'data-mission="m_fallback"'));

// --- 2. an empty readiness renders the empty table ---
const emptyHtml = BR.renderHtml(null, mission);
check("a null readiness renders the warning line and the empty table",
  has(emptyHtml, '<div class="rdstatusline rd-warning">warning</div>') &&
  !has(emptyHtml, "<tr class=\"rdrow"));

// --- 3. a hostile reason is escaped, never markup ---
const hostileHtml = BR.renderHtml(
  { mission_id: "m_running", status: "error",
    items: [{ check: HOSTILE_REASON, status: "error", reason: HOSTILE_REASON }],
    errors: [{ check: HOSTILE_REASON, status: "error", reason: HOSTILE_REASON }] },
  mission);
check("a hostile reason is escaped in the check cell",
  has(hostileHtml, "<td class=\"rdcheck\">" + escOf(HOSTILE_REASON) + "</td>"));
check("a hostile reason is escaped in the reason cell",
  has(hostileHtml, "<td>" + escOf(HOSTILE_REASON) + "</td>"));
check("the hostile reason never appears as raw markup",
  !hostileHtml.includes(HOSTILE_REASON));

// --- 4. renderInto keeps the preflight flag and the last server text ---
const c4 = makeContainer();
BR.renderInto(c4, "m_running", mission, { status: "ok", items: okItems, errors: [] },
  { preflight: true, message: "last text" });
check("renderInto sets the preflight checkbox from the kept state",
  !!c4.querySelector('input[name="preflight"]') &&
  c4.querySelector('input[name="preflight"]').checked === true);
check("renderInto shows the kept server text in .rdresult",
  !!c4.querySelector(".rdresult") &&
  c4.querySelector(".rdresult").textContent === "last text");
check("renderInto clears the shell's #boardstate line",
  stateStub.textContent === "" && stateStub.className === "");
BR.renderInto(c4, "m_running", mission, { status: "ok", items: okItems, errors: [] },
  { preflight: false, message: "second text" });
check("a second renderInto keeps the panel and the new flag and text",
  has(c4.innerHTML, "runner") &&
  c4.querySelector('input[name="preflight"]').checked === false &&
  c4.querySelector(".rdresult").textContent === "second text");

(async () => {
// --- 5. the Start button: confirmation on an error, the exact start request ---
// 5a. declined confirmation on an error: no fetch at all
confirmResult = false;
const c5a = makeContainer();
BR.renderInto(c5a, "m_running", mission,
  { status: "error", items: errItems, errors: errItems }, {});
fetchLog.length = 0;
c5a.querySelector(".rd-start").click();
check("a declined start on an error posts nothing", fetchLog.length === 0);

// 5b. accepted confirmation on an error: the exact start request
confirmResult = true;
const c5b = makeContainer();
BR.renderInto(c5b, "m_running", mission,
  { status: "error", items: errItems, errors: errItems }, { preflight: true });
startAnswer = { allowed: true, reason: "start allowed" };
startStatus = 200;
fetchLog.length = 0;
c5b.querySelector(".rd-start").click();
await new Promise((r) => setTimeout(r, 10));
const start5b = fetchLog.find((f) => f.method === "POST");
check("an accepted start on an error posts exactly one start request",
  fetchLog.length === 1 && !!start5b &&
  start5b.url === "/api/board/missions/m_running/start?confirmed=true");
check("the start body carries the preflight flag from the checkbox",
  JSON.parse(start5b.body).preflight === true);
check("the start request carries the dashboard header",
  start5b.headers["X-AHAWR-Dashboard"] === "1");

// 5c. no error check: no confirmation is asked, no ?confirmed=true
const c5c = makeContainer();
BR.renderInto(c5c, "m_running", mission,
  { status: "ok", items: okItems, errors: [] }, {});
confirmResult = false; // would prove a confirm() call if one were made
fetchLog.length = 0;
c5c.querySelector(".rd-start").click();
await new Promise((r) => setTimeout(r, 10));
const start5c = fetchLog.find((f) => f.method === "POST");
check("a start without error checks is sent without confirmation",
  fetchLog.length === 1 && !!start5c &&
  start5c.url === "/api/board/missions/m_running/start");
check("the start body carries preflight false when the box is unchecked",
  JSON.parse(start5c.body).preflight === false);

// --- 6. the server's refusal text is shown in the panel ---
const c6 = makeContainer();
BR.renderInto(c6, "m_running", mission,
  { status: "error", items: errItems, errors: errItems }, {});
confirmResult = true;
startStatus = 200;
startAnswer = {
  allowed: false,
  reason: "blocked by error checks: live_state: the live state is missing",
};
fetchLog.length = 0;
c6.querySelector(".rd-start").click();
await new Promise((r) => setTimeout(r, 10));
check("the refused start shows the server's reason in .rdresult",
  c6.querySelector(".rdresult").textContent ===
    "blocked by error checks: live_state: the live state is missing");
check("the refused start posted the confirmed start URL",
  fetchLog.length === 1 &&
  fetchLog[0].url === "/api/board/missions/m_running/start?confirmed=true");

// --- 7. a failed readiness load renders the escaped error state ---
const c7 = makeContainer();
readinessBody = null;
globalThis.fetch = () => Promise.reject(new Error(HOSTILE_REASON));
await BR.render(c7, "m_running", mission);
check("a failed readiness load renders the escaped error state",
  !!c7.querySelector(".rdresult") &&
  has(c7.innerHTML, "<div class=\"rdresult\">" + escOf(HOSTILE_REASON) + "</div>") &&
  !c7.innerHTML.includes(HOSTILE_REASON));

delete globalThis.document;
delete globalThis.fetch;
delete globalThis.window;
console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
