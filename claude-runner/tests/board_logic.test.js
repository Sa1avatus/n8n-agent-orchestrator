// Node test for board_logic.js (pure functions).
// Run: node claude-runner/tests/board_logic.test.js

"use strict";
const path = require("path");
const BL = require(path.join(__dirname, "..", "src", "claude_runner", "board_logic.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

// --- reorder ---
let q = [
  { position: 0, mission_id: "m1" },
  { position: 1, mission_id: "m2" },
  { position: 2, mission_id: "m3" },
];
let r = BL.reorder(q, "m3", 0);
check("reorder to front", eq(r, [
  { position: 0, mission_id: "m3" },
  { position: 1, mission_id: "m1" },
  { position: 2, mission_id: "m2" },
]));
// original is untouched (pure)
check("reorder is pure", eq(q, [
  { position: 0, mission_id: "m1" },
  { position: 1, mission_id: "m2" },
  { position: 2, mission_id: "m3" },
]));
r = BL.reorder(q, "m1", 1);
check("reorder to middle", eq(r, [
  { position: 0, mission_id: "m2" },
  { position: 1, mission_id: "m1" },
  { position: 2, mission_id: "m3" },
]));
r = BL.reorder(q, "m2", 99);
check("reorder past end clamps to end", eq(r, [
  { position: 0, mission_id: "m1" },
  { position: 1, mission_id: "m3" },
  { position: 2, mission_id: "m2" },
]));
check("reorder missing mission throws",
  (() => { try { BL.reorder(q, "nope", 0); return false; } catch (e) { return true; } })());
check("reorder non-array throws", (() => { try { BL.reorder("nope", 0); return false; } catch (e) { return true; } })());

// --- dropPosition (index valid for reorder) + chained with reorder ---
const groups = {
  running: ["m2"],
  queued: ["m1", "m3"],
  completed: [],
  not_completed: [],
  disabled: [],
};
const ids = (arr) => arr.map((e) => e.mission_id);
// m1 before m3: down one slot, chain with reorder
const at1 = BL.dropPosition(q, "m1", "m3", "before", groups);
check("drop before index", at1 === 1);
check("drop before chain", eq(ids(BL.reorder(q, "m1", at1)), ["m2", "m1", "m3"]));
// m3 before m1: up to the front
const at2 = BL.dropPosition(q, "m3", "m1", "before", groups);
check("drop before chain (up)", eq(ids(BL.reorder(q, "m3", at2)), ["m3", "m1", "m2"]));
// m1 after m3: to the end
const at3 = BL.dropPosition(q, "m1", "m3", "after", groups);
check("drop after index", at3 === 2);
check("drop after chain", eq(ids(BL.reorder(q, "m1", at3)), ["m2", "m3", "m1"]));
// m3 after m1: down one slot
const at4 = BL.dropPosition(q, "m3", "m1", "after", groups);
check("drop after chain", eq(ids(BL.reorder(q, "m3", at4)), ["m1", "m3", "m2"]));
// drop onto self is a no-op (index = own position)
const at5 = BL.dropPosition(q, "m1", "m1", "before", groups);
check("drop onto self index", at5 === 0);
check("drop onto self chain", eq(ids(BL.reorder(q, "m1", at5)), ["m1", "m2", "m3"]));
// running mission must stay first: cannot insert before the running target
check("running target throws", (() => { try { BL.dropPosition(q, "m1", "m2", "before", groups); return false; } catch (e) { return /running/.test(e.message); } })());
// dropping onto the running mission from after is allowed
const at6 = BL.dropPosition(q, "m1", "m2", "after", groups);
check("after running target chain", eq(ids(BL.reorder(q, "m1", at6)), ["m2", "m1", "m3"]));
// moving the running mission itself -> must throw
check("running drop throws", (() => { try { BL.dropPosition(q, "m2", "m1", "before", groups); return false; } catch (e) { return /running/.test(e.message); } })());
// drop onto a group header (target_id null) lands at the front
check("group header drop", BL.dropPosition(q, "m1", null, "before", groups) === 0);
// target not in queue -> must throw
check("missing target throws", (() => { try { BL.dropPosition(q, "m1", "nope", "before", groups); return false; } catch (e) { return /not in the queue/.test(e.message); } })());
// bad side -> must throw
check("bad side throws", (() => { try { BL.dropPosition(q, "m1", "m3", "side", groups); return false; } catch (e) { return /before|after/.test(e.message); } })());
// no groups given -> running guard is empty, m1 is fine
check("no groups", BL.dropPosition(q, "m1", "m3", "before", undefined) === 1);

// --- mergeServerIntoForm ---
const localForm = {
  version: "v1",
  queue: [
    { position: 0, mission_id: "m1", note: "local edit" },
    { position: 1, mission_id: "m2" },
  ],
  dirty_fields: ["queue", "note"],
};
const server1 = {
  version: "v2",
  queue: [
    { position: 0, mission_id: "m1", note: "server note" },
    { position: 1, mission_id: "m2" },
  ],
};
let merged = BL.mergeServerIntoForm(localForm, server1);
// dirty field (queue) keeps local note; version comes from server
check("merge keeps local queue", eq(merged.queue[0].note, "local edit"));
check("merge takes server version", merged.version === "v2");
// queue content is the same, so no server_changed flag
check("no flag when same", merged.server_changed !== true);

// server queue differs -> flag
const server2 = {
  version: "v3",
  queue: [
    { position: 0, mission_id: "m1" },
    { position: 1, mission_id: "m3" },
  ],
};
merged = BL.mergeServerIntoForm(localForm, server2);
check("flag when server queue differs", merged.server_changed === true);
check("flagged queue kept local", eq(merged.queue[0].mission_id, "m1"));

// server adds a field the form does not have -> it is taken over
const server3 = {
  version: "v4",
  queue: [
    { position: 0, mission_id: "m1" },
    { position: 1, mission_id: "m2" },
  ],
  extra: "hello",
};
merged = BL.mergeServerIntoForm(localForm, server3);
check("new server field taken over", merged.extra === "hello");

// flag cleared when queue back in sync
const dirtyForm = {
  version: "v1",
  queue: [
    { position: 0, mission_id: "m1" },
    { position: 1, mission_id: "m2" },
  ],
  dirty_fields: [],
  server_changed: true,
};
const serverSame = {
  version: "v5",
  queue: [
    { position: 0, mission_id: "m1" },
    { position: 1, mission_id: "m2" },
  ],
};
merged = BL.mergeServerIntoForm(dirtyForm, serverSame);
check("flag cleared when in sync", !("server_changed" in merged));

// bad input
check("bad form throws", (() => { try { BL.mergeServerIntoForm(null, server1); return false; } catch (e) { return true; } })());
check("bad server throws", (() => { try { BL.mergeServerIntoForm(localForm, null); return false; } catch (e) { return true; } })());

// --- parseHash / buildHash ---
check("parse empty", eq(BL.parseHash(""), { mission_id: null, task_id: null }));
check("parse board", eq(BL.parseHash("#board"), { mission_id: null, task_id: null }));
check("parse mission", eq(BL.parseHash("#board/m1"), { mission_id: "m1", task_id: null }));
check("parse mission+task", eq(BL.parseHash("#board/m1/T003"), { mission_id: "m1", task_id: "T003" }));
check("parse without #", eq(BL.parseHash("board/m1/T003"), { mission_id: "m1", task_id: "T003" }));
check("build board", BL.buildHash(null, null) === "#board");
check("build mission", BL.buildHash("m1", null) === "#board/m1");
check("build mission+task", BL.buildHash("m1", "T003") === "#board/m1/T003");
check("build round-trip", eq(BL.parseHash(BL.buildHash("m7", "T010")), { mission_id: "m7", task_id: "T010" }));
check("build bad mission throws", (() => { try { BL.buildHash("a/b", null); return false; } catch (e) { return true; } })());

// --- esc ---
check("esc quotes and angle brackets", BL.esc('"\'<x>') === "&quot;&#39;&lt;x&gt;");
check("esc amp", BL.esc("&") === "&amp;");
check("esc lt", BL.esc("<") === "&lt;");
check("esc gt", BL.esc(">") === "&gt;");
check("esc null", BL.esc(null) === "");

// --- diffConfig ---
const cfgA = { model: "m1", steps: 5, on: true, only: "a" };
const cfgB = { model: "m1", steps: 6, off: false, only: "a", extra: null };
check("diffConfig changed fields sorted",
  eq(BL.diffConfig(cfgA, cfgB), [
    { field: "off", old: null, new: false },
    { field: "on", old: true, new: null },
    { field: "steps", old: 5, new: 6 },
  ]));
check("diffConfig same config", eq(BL.diffConfig(cfgA, cfgA), []));
check("diffConfig only b key", eq(BL.diffConfig({}, { x: 1 }), [{ field: "x", old: null, new: 1 }]));
check("diffConfig only a key", eq(BL.diffConfig({ x: 1 }, {}), [{ field: "x", old: 1, new: null }]));
check("diffConfig bad a throws", (() => { try { BL.diffConfig(null, cfgB); return false; } catch (e) { return true; } })());
check("diffConfig bad b throws", (() => { try { BL.diffConfig(cfgA, null); return false; } catch (e) { return true; } })());
check("diffConfig array throws", (() => { try { BL.diffConfig([1], cfgB); return false; } catch (e) { return true; } })());

// --- thinkLayout (mirrors the server board_think.layout_preview) ---
const POLICY = {
  variants: [
    { name: "plan+pulse", weight: 2 },
    { name: "control", weight: 1 },
  ],
  from_task: 5,
};
check("thinkLayout default n=12", eq(BL.thinkLayout(POLICY), [
  null, null, null, null,
  "plan+pulse", "control", "plan+pulse",
  "plan+pulse", "control", "plan+pulse",
  "plan+pulse", "control",
]));
check("thinkLayout n=6", eq(BL.thinkLayout(POLICY, 6), [null, null, null, null, "plan+pulse", "control"]));
check("thinkLayout n=0", eq(BL.thinkLayout(POLICY, 0), []));
check("thinkLayout n negative clamps to 0", eq(BL.thinkLayout(POLICY, -3), []));
check("thinkLayout n=1 one variant", eq(BL.thinkLayout({ variants: [{ name: "p", weight: 1 }] }, 1), ["p"]));
check("thinkLayout from_task 0 n=3", eq(BL.thinkLayout({ variants: [{ name: "p", weight: 1 }], from_task: 0 }, 3), ["p", "p", "p"]));
check("thinkLayout no variants", eq(BL.thinkLayout({ variants: [] }, 3), [null, null, null]));
check("thinkLayout non-object policy", eq(BL.thinkLayout(null, 3), [null, null, null]));
check("thinkLayout bad weight", eq(BL.thinkLayout({ variants: [{ name: "p", weight: 0 }] }, 3), [null, null, null]));
check("thinkLayout bad from_task", eq(BL.thinkLayout({ variants: [{ name: "p", weight: 1 }], from_task: -1 }, 3), [null, null, null]));

console.log("all ok");
