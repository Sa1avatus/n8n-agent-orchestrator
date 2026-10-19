// Node test for the runs page hash parser (dashboard.html: parseRunsHash).
// Run: node claude-runner/tests/dashboard_hash.test.js
//
// Proves:
//   1. "#mission=<id>" is a run filter (sent as ?mission=<id>) and removing
//      its tag drops it from the hash;
//   2. "from=board/..." is the back target, never a run filter, and is
//      accepted only for board paths;
//   3. a "#board..." hash and garbage yield no filter and no back target;
//   4. without the mission parameter the parsed filter is unchanged.

"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const HTML = path.join(__dirname, "..", "src", "claude_runner", "dashboard.html");
const text = fs.readFileSync(HTML, "utf8");
const START = "// === pure filter functions (no DOM) ===";
const END = "// === end pure filter functions ===";
const block = text.slice(text.indexOf(START), text.indexOf(END));
const { parseRunsHash, filterToQuery, removeValue } = new Function(
  block + "\nreturn { parseRunsHash, filterToQuery, removeValue };"
)();

function check(name, fn) {
  fn();
  console.log("ok - " + name);
}

check("mission parameter becomes a run filter", () => {
  const r = parseRunsHash("#mission=m1");
  assert.deepStrictEqual(r.filter, { mission: ["m1"] });
  assert.strictEqual(filterToQuery(r.filter), "mission=m1");
});

check("removing the mission tag drops it from the hash query", () => {
  const r = parseRunsHash("#mission=m1&status=completed");
  removeValue(r.filter, "mission", "m1");
  assert.strictEqual(filterToQuery(r.filter), "status=completed");
  removeValue(r.filter, "status", "completed");
  assert.strictEqual(filterToQuery(r.filter), "");
});

check("from=board is the back target and not a filter", () => {
  const r = parseRunsHash("#mission=m1&from=board%2Fm1");
  assert.deepStrictEqual(r.filter, { mission: ["m1"] });
  assert.strictEqual(r.from, "board/m1");
});

check("a board view back target keeps its query", () => {
  const r = parseRunsHash("#mission=m1&from=" + encodeURIComponent("board/view/kan?project=x"));
  assert.strictEqual(r.from, "board/view/kan?project=x");
  assert.deepStrictEqual(r.filter, { mission: ["m1"] });
});

check("a back target that is not a board path is ignored", () => {
  assert.strictEqual(parseRunsHash("#mission=m1&from=http%3A%2F%2Fevil").from, "");
  assert.strictEqual(parseRunsHash("#from=boardx").from, "");
});

check("the run part of the hash is not part of the filter", () => {
  const r = parseRunsHash("#mission=m1&from=board%2Fm1#run=R1");
  assert.deepStrictEqual(r.filter, { mission: ["m1"] });
  assert.strictEqual(r.from, "board/m1");
});

check("a board hash and garbage give no filter", () => {
  assert.deepStrictEqual(parseRunsHash("#board/m1"), { filter: {}, from: "" });
  assert.deepStrictEqual(parseRunsHash(""), { filter: {}, from: "" });
  assert.deepStrictEqual(parseRunsHash(undefined), { filter: {}, from: "" });
  assert.deepStrictEqual(parseRunsHash("#garbage&=&x"), { filter: {}, from: "" });
});

check("without mission the filter is unchanged", () => {
  const r = parseRunsHash("#role=worker&role=reviewer&status=completed");
  assert.deepStrictEqual(r.filter, { role: ["worker", "reviewer"], status: ["completed"] });
  assert.strictEqual(r.from, "");
});

console.log("all ok");
