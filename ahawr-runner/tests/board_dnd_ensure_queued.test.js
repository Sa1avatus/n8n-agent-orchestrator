// Node test for BoardDnd.ensureQueued: a waiting mission without a queue row is added once, and
// a mission that already has a row causes no write.
//
// The second half tests the dependency guard refusal of applyMove (T027): the
// "move them too" confirm and its resend with move_dependents, the plain
// message naming the ids when nothing named depends on the moved mission, and
// the body of a move the guard did not refuse (the flag-off shape, unchanged).
"use strict";
const path = require("path");
const assert = require("node:assert");
const { test } = require("node:test");

require(path.join(__dirname, "i18n_stub.js"));
const calls = [];
const moveBodies = [];
let guardBody = null; // null: the move answer is the plain ok one
let queue = [];
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), options: options || {} });
  if (String(url) === "/api/board/missions") return { ok: true, status: 200, json: async () => ({ groups: {}, missions: [] }) };
  if (String(url) === "/api/board/queue") return { ok: true, status: 200, json: async () => ({ queue, version: "v1" }) };
  if (String(url) === "/api/board/queue/add") {
    queue = queue.concat([{ position: queue.length, mission_id: JSON.parse(options.body).mission_id, enabled: true }]);
    return { ok: true, status: 200, json: async () => ({}) };
  }
  if (String(url) === "/api/board/queue/move") {
    const body = JSON.parse(options.body);
    moveBodies.push(body);
    if (guardBody) return { ok: false, status: 403, json: async () => guardBody };
    return { ok: true, status: 200, json: async () => ({ queue, version: "v2" }) };
  }
  throw new Error("unexpected " + url);
};
// The DOM the refusal path touches: #boardstate for the message, and the
// confirm dialog.  showNotice finds no #boardviewbody, so nothing else is read.
const stateEl = { textContent: "", className: "", hidden: true };
globalThis.document = { getElementById: (id) => (id === "boardstate" ? stateEl : null) };
globalThis.BoardLogic = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_logic.js"));
const confirmTexts = [];
let confirmAnswer = true;
globalThis.window = { confirm: (text) => { confirmTexts.push(text); return confirmAnswer; } };
const BoardDnd = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_dnd.js"));

// One queue the guard can refuse: m2 runs after m1, so moving m1 behind m2 is
// the refused drop of the acceptance criteria.
function guardQueue() {
  queue = [
    { position: 0, mission_id: "m1", enabled: true },
    { position: 1, mission_id: "m2", enabled: true, depends_on: ["m1"] },
  ];
  BoardDnd.DndState.data = {
    groups: { queued: ["m1", "m2"], running: [], completed: [], not_completed: [], disabled: [] },
    queue, version: "v9", missions: [],
  };
  calls.length = 0;
  moveBodies.length = 0;
  confirmTexts.length = 0;
  stateEl.textContent = "";
  guardBody = {
    status: "failed",
    error: { code: "guard", message: "mission m1 must run after m2" },
    code: "guard", status_code: 403,
    reason: "mission m1 must run after m2",
    blocked_by: ["m2"],
  };
}

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

test("a refused drop asks 'move them too' naming the dependents and resends with move_dependents", async () => {
  guardQueue();
  confirmAnswer = true;
  await BoardDnd.applyMove("m1", "m2", "after");
  assert.strictEqual(confirmTexts.length, 1, "the guard asks once");
  assert.ok(confirmTexts[0].includes("m2"), `the confirm names the dependent id: ${confirmTexts[0]}`);
  assert.deepStrictEqual(moveBodies[0], { mission_id: "m1", to_position: 1, expected_version: "v9" });
  assert.deepStrictEqual(moveBodies[1], {
    mission_id: "m1", to_position: 1, expected_version: "v9", move_dependents: true,
  });
  assert.strictEqual(moveBodies.length, 2, "the confirm resends the same position once");
});

test("a refused drop with the confirm refused sends nothing more and shows the ids", async () => {
  guardQueue();
  confirmAnswer = false;
  await BoardDnd.applyMove("m1", "m2", "after");
  assert.strictEqual(moveBodies.length, 1, "the refused confirm sends no second request");
  assert.ok(stateEl.textContent.includes("m2"), `the message names the dependency id: ${stateEl.textContent}`);
  assert.ok(stateEl.textContent.includes("m1"), "the message names the moved mission");
});

test("a refusal whose named ids do not depend on the mission asks nothing, only shows the message", async () => {
  guardQueue();
  guardBody.blocked_by = ["m0"];
  await BoardDnd.applyMove("m1", "m2", "after");
  assert.strictEqual(confirmTexts.length, 0, "nothing depends on it, so nothing is asked");
  assert.strictEqual(moveBodies.length, 1, "the refusal is not resent");
  assert.ok(stateEl.textContent.includes("m0"), `the message names the dependency id: ${stateEl.textContent}`);
});

test("a move the guard does not refuse keeps the plain three-field body and asks nothing", async () => {
  guardQueue();
  guardBody = null;
  await BoardDnd.applyMove("m1", "m2", "after");
  assert.strictEqual(confirmTexts.length, 0, "no guard refusal means no confirm");
  assert.deepStrictEqual(moveBodies[0], { mission_id: "m1", to_position: 1, expected_version: "v9" });
  assert.ok(!("move_dependents" in moveBodies[0]), "the flag-off body has no move_dependents key");
});
