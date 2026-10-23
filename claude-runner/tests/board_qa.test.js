// Node test for board_qa.js (the read-only QA verdict panel).
// Run: node --test claude-runner/tests/board_qa.test.js
//
// Proves:
//   1. a passing verdict renders the passed label and the scenario;
//   2. a failing verdict marks the failed step (one-based) with its reason
//      and links each screenshot to its server URL in a new tab;
//   3. a blocked verdict shows the blocked reason;
//   4. no verdict renders nothing;
//   5. no output contains a button (the panel has no rerun control);
//   6. a hostile value is escaped in every field;
//   7. render() fetches the mission's QA route and fills the container;
//      a failed response rejects with the load-failure message;
//   8. verdicts are grouped by layer in the order regression, api, ui,
//      and a verdict without a layer is a ui verdict;
//   9. a skipped verdict shows the skipped label and its reason.
"use strict";
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardQa = require(path.join(__dirname, "..", "src", "claude_runner", "board_qa.js"));

const SCENARIO = "kanban stale queue version shows the refresh message";

function passVerdict() {
  return { verdict: "pass", scenario: SCENARIO, screenshots: [], console_errors: [], network_errors: [] };
}

function failVerdict() {
  return {
    verdict: "fail",
    scenario: SCENARIO,
    failed_step: 2,
    failed_reason: "refresh message missing",
    screenshots: [{ name: "step-2.png", url: "/api/board/qa/screenshots/qa-ab12/step-2.png" }],
    console_errors: ["TypeError: x is undefined"],
    network_errors: [],
  };
}

function blockedVerdict() {
  return { verdict: "blocked", scenario: SCENARIO, blocked_reason: "no browser available", screenshots: [] };
}

test("a passing verdict shows the passed label and the scenario", () => {
  const html = BoardQa.panelHtml([passVerdict()]);
  assert.match(html, /qa-verdict qa-pass/);
  assert.match(html, />passed</);
  assert.ok(html.includes(SCENARIO));
});

test("a failing verdict marks the one-based failed step with its reason", () => {
  const html = BoardQa.panelHtml([failVerdict()]);
  assert.match(html, /qa-step-failed/);
  assert.ok(html.includes("failed step 3: refresh message missing"));
  assert.ok(html.includes("TypeError: x is undefined"));
});

test("screenshots are links to the server route in a new tab", () => {
  const html = BoardQa.panelHtml([failVerdict()]);
  assert.ok(html.includes('href="/api/board/qa/screenshots/qa-ab12/step-2.png"'));
  assert.ok(html.includes('target="_blank" rel="noopener"'));
});

test("a blocked verdict shows the blocked reason", () => {
  const html = BoardQa.panelHtml([blockedVerdict()]);
  assert.match(html, /qa-verdict qa-blocked/);
  assert.ok(html.includes("no browser available"));
  assert.ok(!html.includes("qa-step-failed"));
});

test("no verdict renders nothing", () => {
  assert.equal(BoardQa.panelHtml([]), "");
});

test("the panel has no button in any state", () => {
  for (const verdicts of [[passVerdict()], [failVerdict()], [blockedVerdict()], []]) {
    assert.ok(!/<button/i.test(BoardQa.panelHtml(verdicts)));
  }
});

test("a hostile value is escaped in every field", () => {
  const hostile = "<img src=x onerror=alert(1)>";
  const html = BoardQa.panelHtml([{
    verdict: "fail",
    scenario: hostile,
    failed_step: 0,
    failed_reason: hostile,
    blocked_reason: hostile,
    reason: hostile,
    screenshots: [{ name: hostile, url: `/x?"${hostile}` }],
    console_errors: [hostile],
    network_errors: [],
  }]);
  assert.ok(!html.includes("<img"));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
});

test("an unknown verdict value renders the unknown label", () => {
  const html = BoardQa.panelHtml([{ verdict: "<b>", scenario: SCENARIO, screenshots: [] }]);
  assert.ok(html.includes("unknown"));
  assert.ok(!html.includes("<b>"));
});

test("verdicts are grouped under a heading per layer in the order regression, api, ui", () => {
  const html = BoardQa.panelHtml([
    { ...passVerdict(), layer: "ui", scenario: "ui scenario" },
    { ...passVerdict(), layer: "api", scenario: "api scenario" },
    { ...failVerdict(), layer: "regression", scenario: "regression scenario" },
  ]);
  const headings = [...html.matchAll(/qa-layer-title">([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(headings, ["regression", "api", "ui"]);
  const regression = html.indexOf("regression scenario");
  const api = html.indexOf("api scenario");
  const ui = html.indexOf("ui scenario");
  assert.ok(regression < api && api < ui);
});

test("a verdict without a layer is grouped under ui", () => {
  const html = BoardQa.panelHtml([{ ...passVerdict(), layer: "api" }, passVerdict()]);
  const headings = [...html.matchAll(/qa-layer-title">([^<]+)</g)].map((m) => m[1]);
  assert.deepEqual(headings, ["api", "ui"]);
  assert.equal(html.match(/qa-layer-title/g).length, 2);
});

test("a skipped verdict shows the skipped label and its reason", () => {
  const html = BoardQa.panelHtml([{
    verdict: "skipped", scenario: SCENARIO, reason: "no QA profile", screenshots: [],
  }]);
  assert.match(html, /qa-verdict qa-skipped/);
  assert.match(html, />skipped</);
  assert.ok(html.includes("no QA profile"));
});

test("render fetches the mission QA route and fills the container", async () => {
  const realFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(url);
    return { ok: true, json: async () => ({ verdict: failVerdict() }) };
  };
  try {
    const container = { innerHTML: "" };
    await BoardQa.render(container, "m 1");
    assert.equal(calls[0], "/api/board/missions/m%201/qa");
    assert.ok(container.innerHTML.includes("failed step 3"));
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("render leaves the container empty when there is no verdict", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ verdict: null }) });
  try {
    const container = { innerHTML: "x" };
    await BoardQa.render(container, "m1");
    assert.equal(container.innerHTML, "");
  } finally {
    globalThis.fetch = realFetch;
  }
});

test("render rejects with the load-failure message when the response is not ok", async () => {
  const realFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, json: async () => ({}) });
  try {
    await assert.rejects(BoardQa.render({ innerHTML: "" }, "m1"),
      { message: "Could not load the QA verdict." });
  } finally {
    globalThis.fetch = realFetch;
  }
});
