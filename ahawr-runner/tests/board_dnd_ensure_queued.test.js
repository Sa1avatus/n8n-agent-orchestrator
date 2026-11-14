// Node test for BoardDnd.ensureQueued: a waiting mission without a queue row is added once, and
// a mission that already has a row causes no write.
"use strict";
const path = require("path");
const assert = require("node:assert");
const { test } = require("node:test");

require(path.join(__dirname, "i18n_stub.js"));
const calls = [];
let queue = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options: options || {} });
  if (String(url) === "/api/board/missions") return { ok: true, status: 200, json: async () => ({ groups: {}, missions: [] }) };
  if (String(url) === "/api/board/queue") return { ok: true, status: 200, json: async () => ({ queue, version: "v1" }) };
  if (String(url) === "/api/board/queue/add") {
    queue = queue.concat([{ position: queue.length, mission_id: JSON.parse(options.body).mission_id, enabled: true }]);
    return { ok: true, status: 200, json: async () => ({}) };
  }
  throw new Error("unexpected " + url);
};
const BoardDnd = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_dnd.js"));

test("ensureQueued adds a mission that has no queue row, with the dashboard header", async () => {
  queue = [{ position: 0, mission_id: "m1", enabled: true }];
  calls.length = 0;
  const data = await BoardDnd.ensureQueued("m2");
  const add = calls.find((c) => c.url === "/api/board/queue/add");
  assert.ok(add);
  assert.strictEqual(add.options.headers["X-AHAWR-Dashboard"], "1");
  assert.deepStrictEqual(JSON.parse(add.options.body), { mission_id: "m2", enabled: true, expected_version: "v1" });
  assert.deepStrictEqual(data.queue.map((e) => e.mission_id), ["m1", "m2"]);
});

test("ensureQueued writes nothing for a mission that is in the queue", async () => {
  queue = [{ position: 0, mission_id: "m1", enabled: true }];
  calls.length = 0;
  await BoardDnd.ensureQueued("m1");
  assert.ok(!calls.some((c) => c.url === "/api/board/queue/add"));
});
