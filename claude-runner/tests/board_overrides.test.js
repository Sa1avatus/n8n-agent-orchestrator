// Runs the shared overrides_cases.json against board_overrides.js; mirrors test_overrides_config.py.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardOverrides = require(path.join(__dirname, "..", "src", "claude_runner", "board_overrides.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "overrides_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    const expect = item.expect;
    const errors = BoardOverrides.validate(item.overrides_json);
    if ("errors" in expect) assert.deepEqual(errors, expect.errors);
    for (const message of expect.errors_contain ?? []) {
      assert.ok(errors.includes(message), message);
    }
    if ("state_key" in expect) {
      assert.equal(
        BoardOverrides.resolveStateKey(item.mission_id, item.state_namespace),
        expect.state_key,
      );
    }
    if (errors.length) {
      assert.throws(() => BoardOverrides.flatten(item.overrides_json, item.legacy), Error);
      return;
    }
    const flat = BoardOverrides.flatten(item.overrides_json, item.legacy);
    if (expect.flatten_equals === "legacy_constants") assert.deepEqual(flat, LEGACY);
    for (const [key, value] of Object.entries(expect.flatten_partial ?? {})) {
      assert.deepEqual(flat[key], value, key);
    }
  });
}
