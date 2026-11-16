// Node test for board_run_mission.js (the "Run next" / "Run now" buttons of a kanban card).
// Run: node --test tests/board_run_mission.test.js
//
// Proves: the block reasons come from stored data (state group, queue row); with the flag off
// a card gets no node and no dataset mark (markup unchanged); with it on the two buttons exist,
// are disabled with a reason for running, disabled and failed-without-retry missions and absent
// from a completed card; a click goes through the confirm step (a refusal sends nothing) and posts
// to run-next / run-now with the queue version; the answer and the 409 reason codes are shown as
// texts; the read-only page hides the two buttons by their labels.

"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("path");

require(path.join(__dirname, "i18n_stub.js"));
const src = path.join(__dirname, "..", "src", "ahawr_runner");
const RM = require(path.join(src, "board_run_mission.js"));

function fakeEl(tag) {
  const el = { tagName: tag, dataset: {}, className: "", textContent: "", title: "", type: "",
    disabled: false, children: [], listeners: {} };
  el.appendChild = (c) => { el.children.push(c); return el; };
  el.addEventListener = (name, fn) => { el.listeners[name] = fn; };
  return el;
}
globalThis.document = { createElement: fakeEl };

const DATA = {
  version: "v1",
  queue: [
    { mission_id: "queued-1", enabled: true },
    { mission_id: "off-1", enabled: false },
    { mission_id: "bad-1", enabled: true, retry_failed: false },
    { mission_id: "bad-retry", enabled: true, retry_failed: true },
  ],
};

function card(id, group) {
  const el = fakeEl("div");
  el.dataset.missionId = id;
  el.dataset.group = group;
  return el;
}

function deps(over) {
  const log = { posts: [], shown: [], confirms: [], refreshed: 0 };
  return Object.assign({
    data: DATA,
    flagOn: true,
    post: async (url, body) => { log.posts.push({ url, body }); return { status: "resume_queued" }; },
    show: (text, cls) => log.shown.push({ text, cls }),
    refresh: async () => { log.refreshed += 1; },
    confirm: (text) => { log.confirms.push(text); return true; },
    tr: globalThis.t,
    log,
  }, over);
}

test("block reasons come from stored data", () => {
  assert.equal(RM.blockReason(DATA, "queued-1", "queued"), null);
  assert.equal(RM.blockReason(DATA, "m", "running"), "running");
  assert.equal(RM.blockReason(DATA, "m", "completed"), "completed");
  assert.equal(RM.blockReason(DATA, "off-1", "queued"), "disabled");
  assert.equal(RM.blockReason(DATA, "bad-1", "not_completed"), "failed_without_retry");
  assert.equal(RM.blockReason(DATA, "bad-retry", "not_completed"), null);
});

test("the request carries the route and the queue version", () => {
  assert.deepEqual(RM.runRequest("next", "a/b", "v9"),
    { url: "/api/board/missions/a%2Fb/run-next", body: { expected_version: "v9" } });
  assert.equal(RM.runRequest("now", "m", "v").url, "/api/board/missions/m/run-now");
});

test("flag off: the card gets no node and no mark", () => {
  const el = card("queued-1", "queued");
  assert.equal(RM.attachRunButtons(el, deps({ flagOn: false })), null);
  assert.equal(el.children.length, 0);
  assert.equal(el.dataset.runReady, undefined);
});

test("flag on: two buttons, none on a completed card, one attach per card", () => {
  const el = card("queued-1", "queued");
  const group = RM.attachRunButtons(el, deps());
  assert.deepEqual(group.children.map((b) => b.textContent), ["run next", "run now"]);
  assert.equal(group.children[0].disabled, false);
  assert.equal(RM.attachRunButtons(el, deps()), null);
  assert.equal(el.children.length, 1);
  assert.equal(RM.attachRunButtons(card("done", "completed"), deps()), null);
});

test("blocked missions get disabled buttons with the reason as title", () => {
  const cases = [["m", "running", "The mission is running."],
    ["off-1", "queued", "The mission is disabled."],
    ["bad-1", "not_completed", "The mission failed and is not set to retry."]];
  for (const [id, groupKey, title] of cases) {
    const group = RM.attachRunButtons(card(id, groupKey), deps());
    assert.ok(group.children.every((b) => b.disabled && b.title === title), id);
  }
});

test("a click asks first; a refusal sends nothing", async () => {
  const d = deps({ confirm: (text) => { d.log.confirms.push(text); return false; } });
  const group = RM.attachRunButtons(card("queued-1", "queued"), d);
  await group.children[1].listeners.click({ preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(d.log.confirms, ["Move queued-1 to the front of the queue and start the queue?"]);
  assert.equal(d.log.posts.length, 0);
});

test("run now posts with the version, shows the answer and refreshes", async () => {
  const d = deps();
  const group = RM.attachRunButtons(card("queued-1", "queued"), d);
  await group.children[1].listeners.click({ preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(d.log.posts, [{ url: "/api/board/missions/queued-1/run-now", body: { expected_version: "v1" } }]);
  assert.deepEqual(d.log.shown, [{ text: "The mission is first; the queue start was requested.", cls: "ok" }]);
  assert.equal(d.log.refreshed, 1);
});

test("answers and refusals map to texts", async () => {
  assert.equal(RM.answerKey("next", {}), "run.moved");
  assert.equal(RM.answerKey("now", { status: "after_running" }), "run.after_running");
  const d = deps({ post: async () => { throw new Error("run next refused for mission 'm': running"); } });
  const group = RM.attachRunButtons(card("queued-1", "queued"), d);
  await group.children[0].listeners.click({ preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(d.log.shown, [{ text: "The mission is running.", cls: "bad" }]);
  assert.equal(RM.refusalText(new Error("x"), globalThis.t), "x");
});

test("the read-only page hides the two buttons by their labels", () => {
  const RO = require(path.join(src, "board_readonly.js"));
  globalThis.BoardI18n = { t: globalThis.t };
  const labels = RO.writeLabels();
  assert.ok(labels.has("run next") && labels.has("run now"));
});
