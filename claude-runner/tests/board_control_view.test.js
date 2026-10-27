// Node test for board_control_view.js (the pure core of the Control tab).
// Run: node --test tests/board_control_view.test.js   (from claude-runner/)
const test = require("node:test");
const assert = require("node:assert");
const V = require("../src/claude_runner/board_control_view.js");
const cases = require("./control_view_cases.json");

const L = (key, english) => english;

function enabledMap(buttons) {
  return Object.fromEntries(buttons.map((b) => [b.action, b.enabled]));
}

for (const c of cases.control) {
  test(`control view: ${c.name}`, () => {
    const model = V.controlModel(c.status, c.history, cases.now, L);
    const e = c.expect;
    assert.strictEqual(model.agent.status, e.agent_status);
    assert.strictEqual(model.agent.heartbeat_age_s, e.heartbeat_age_s);
    assert.strictEqual(model.agent.banner !== null, e.banner);
    assert.strictEqual(model.queue.running, e.queue_running);
    assert.strictEqual(model.current.mission, e.mission);
    assert.strictEqual(model.current.task, e.task);
    assert.deepStrictEqual(enabledMap(model.buttons), e.enabled);
    assert.deepStrictEqual(
      model.history.map(({ action, status }) => ({ action, status })),
      e.history,
    );
  });
}

test("a stale heartbeat shows the agent-not-running message", () => {
  const status = { agent: { heartbeat_at: "2026-10-25T11:59:00Z" }, process_panel: { queue: { alive: true } } };
  const model = V.controlModel(status, { commands: [] }, cases.now, L);
  assert.strictEqual(model.agent.status, "dead");
  assert.strictEqual(model.agent.label, "Agent not running");
  assert.match(model.agent.banner, /The host agent is not running/);
  assert.ok(model.buttons.every((b) => b.enabled === false));
  assert.ok(model.buttons.every((b) => b.disabled_reason === "The host agent is not running"));
});
