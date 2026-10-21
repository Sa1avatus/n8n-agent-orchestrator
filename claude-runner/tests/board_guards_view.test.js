// Node test for board_guards_view.js (the pure core of the Guards tab).
// Run: node claude-runner/tests/board_guards_view.test.js
const test = require("node:test");
const assert = require("node:assert");
const V = require("../src/claude_runner/board_guards_view.js");
const cases = require("./guards_view_cases.json");

test("normalise counts the guards by display class", () => {
  for (const c of cases.normalise) {
    assert.deepStrictEqual(V.normalise(c.guards, null).summary, c.summary, c.name);
  }
});

test("status classes", () => {
  for (const [status, cls] of Object.entries(cases.status_class)) assert.strictEqual(V.statusClass(status), cls, status);
});

test("a status always has a shape, so colour is never the only signal", () => {
  for (const status of Object.keys(cases.status_class)) assert.ok(V.statusShape(status), status);
});

test("strip pads and cuts", () => {
  for (const c of cases.strip) {
    assert.deepStrictEqual(V.strip(c.series.map((result) => ({ result })), c.length), c.expect, c.name);
  }
});

test("lanes bucket the checks of the window", () => {
  for (const c of cases.lanes) assert.deepStrictEqual(V.lanes(c.series, c.window, cases.now), c.expect, c.name);
});

test("a wire is lit only just after its guard acted", () => {
  for (const c of cases.wires) {
    const model = V.normalise({ enabled: true, guards: c.guards }, null);
    assert.deepStrictEqual([...V.activeSources(model, cases.now)], c.lit, c.name);
  }
});

test("a hostile name stays plain text in the model", () => {
  const m = V.normalise({ enabled: true, guards: [{ name: "<img onerror=x>", status: "ok", enabled: true }] }, null);
  assert.strictEqual(m.guards[0].name, "<img onerror=x>");
});

test("the flow lists every host, guard and receiver once", () => {
  const m = V.normalise({ enabled: true, guards: [{ name: "tg_relay", status: "ok", enabled: true }], heartbeats: [{ name: "docker_watchdog", status: "fresh" }] }, null);
  const f = V.flowModel(m, cases.now);
  assert.strictEqual(f.hosts.find((h) => h.id === "docker_watchdog").status, "fresh");
  assert.strictEqual(f.guards.length, 1);
  assert.strictEqual(f.receivers.length, 3);
});
