// Runs the shared features_cases.json against board_features.js; mirrors board_roles.test.js.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardFeatures = require(path.join(__dirname, "..", "src", "claude_runner", "board_features.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "features_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    const { features, errors } = BoardFeatures.parseFeatures(
      item.features_json,
      item.mission_features_json,
    );
    const expect = item.expect;
    if ("errors" in expect) assert.deepEqual(errors, expect.errors);
    for (const message of expect.errors_contain ?? []) {
      assert.ok(errors.includes(message), JSON.stringify({ message, errors }));
    }
    if (expect.flatten_equals === "legacy_constants") {
      assert.deepEqual(BoardFeatures.flatten(features), LEGACY);
    }
    const flat = features ? BoardFeatures.flatten(features) : {};
    for (const [key, value] of Object.entries(expect.flatten_partial ?? {})) {
      assert.equal(flat[key], value, key);
    }
  });
}

test("golden: empty or missing features_json flattens byte-for-byte to the legacy constants", () => {
  const legacyText = JSON.stringify(LEGACY);
  for (const text of ["", null, undefined]) {
    const { features, errors } = BoardFeatures.parseFeatures(text, text);
    assert.deepEqual(errors, []);
    assert.equal(JSON.stringify(BoardFeatures.flatten(features)), legacyText);
  }
});
