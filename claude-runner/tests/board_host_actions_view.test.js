// Node test for hostActionsModel in board_guards_view.js (the host-actions panel, pure core).
// Run: node --test tests/board_host_actions_view.test.js   (from claude-runner/)
const test = require("node:test");
const assert = require("node:assert");
const V = require("../src/claude_runner/board_guards_view.js");
const cases = require("./host_actions_view_cases.json");

const L = (key, english) => english;

for (const c of cases.cases) {
  test(`host actions view: ${c.name}`, () => {
    const model = V.hostActionsModel({ enabled: c.enabled, requests: c.requests, now: cases.now }, L);
    assert.strictEqual(model.visible, c.expect.visible);
    assert.deepStrictEqual(model.rows, c.expect.rows);
  });
}

test("labels come through L with their keys", () => {
  const keys = (key, english) => `${key}|${english}`;
  const model = V.hostActionsModel({ enabled: true, requests: [], now: cases.now }, keys);
  assert.strictEqual(model.title, "host.title|Host actions");
  assert.strictEqual(model.confirm, "host.confirm|Confirm");
  assert.strictEqual(model.reject, "host.reject|Reject");
});
