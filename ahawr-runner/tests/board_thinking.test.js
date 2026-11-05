// Runs the shared thinking_cases.json against board_thinking.js; mirrors board_features.test.js.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardThinking = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_thinking.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "thinking_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

test("defaults are the legacy constants", () => {
  assert.deepEqual(BoardThinking.DEFAULTS, LEGACY);
});

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    const expect = item.expect;
    const errors = BoardThinking.validate(item.thinking_json);
    assert.deepEqual(errors, expect.errors);
    if (errors.length) {
      assert.throws(() => BoardThinking.flatten(item.thinking_json, item.legacy));
      return;
    }
    const flat = BoardThinking.flatten(item.thinking_json, item.legacy);
    if (expect.flatten_equals === "legacy_constants") {
      assert.deepEqual(flat, LEGACY);
    }
    for (const [key, value] of Object.entries(expect.flatten_partial ?? {})) {
      assert.equal(flat[key], value, key);
    }
  });
}

test("golden: empty or missing thinking_json flattens to the legacy constants", () => {
  for (const text of [null, undefined, "", "{}"]) {
    assert.deepEqual(BoardThinking.validate(text), []);
    assert.deepEqual(BoardThinking.flatten(text, {}), LEGACY);
  }
});
