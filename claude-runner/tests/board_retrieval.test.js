// Runs the shared retrieval_cases.json against board_retrieval.js; mirrors board_thinking.test.js.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardRetrieval = require(path.join(__dirname, "..", "src", "claude_runner", "board_retrieval.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "retrieval_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

test("defaults are the legacy constants", () => {
  assert.deepEqual(BoardRetrieval.DEFAULTS, LEGACY);
});

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    const expect = item.expect;
    const errors = BoardRetrieval.validate(item.retrieval_json);
    if ("errors" in expect) assert.deepEqual(errors, expect.errors);
    for (const message of expect.errors_contain ?? []) {
      assert.ok(errors.some((error) => error.includes(message)), JSON.stringify({ message, errors }));
    }
    if (errors.length) {
      assert.throws(() => BoardRetrieval.flatten(item.retrieval_json, item.legacy));
      return;
    }
    const flat = BoardRetrieval.flatten(item.retrieval_json, item.legacy);
    if (expect.flatten_equals === "legacy_constants") {
      assert.deepEqual(flat, LEGACY);
    }
    for (const [key, value] of Object.entries(expect.flatten_partial ?? {})) {
      assert.deepEqual(flat[key], value, key);
    }
  });
}

test("golden: empty or missing retrieval_json flattens byte-for-byte to the legacy constants", () => {
  const legacyText = JSON.stringify(LEGACY);
  for (const text of [null, undefined, ""]) {
    assert.deepEqual(BoardRetrieval.validate(text), []);
    assert.equal(JSON.stringify(BoardRetrieval.flatten(text, {})), legacyText);
  }
});
