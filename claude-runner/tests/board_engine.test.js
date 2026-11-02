// Runs the shared engine_cases.json against board_engine.js; mirrors board_features.test.js.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardEngine = require(path.join(__dirname, "..", "src", "claude_runner", "board_engine.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "engine_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

test("defaults are the legacy constants", () => {
  assert.deepEqual(BoardEngine.ENGINE_DEFAULTS, LEGACY);
});

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    const errors = BoardEngine.validateEngineJson(item.engine_json);
    const expect = item.expect;
    if ("errors" in expect) assert.deepEqual(errors, expect.errors);
    for (const message of expect.errors_contain ?? []) {
      const found = errors.some((error) => error.includes(message));
      assert.ok(found, JSON.stringify({ message, errors }));
    }
    if (errors.length) {
      assert.throws(() => BoardEngine.flatten(item.engine_json, item.legacy), Error);
      return;
    }
    const flat = BoardEngine.flatten(item.engine_json, item.legacy);
    if (expect.flatten_equals === "legacy_constants") assert.deepEqual(flat, LEGACY);
    for (const [key, value] of Object.entries(expect.flatten_partial ?? {})) {
      assert.deepEqual(flat[key], value, key);
    }
  });
}

for (const item of CASES.flatten_errors) {
  test(`flatten: ${item.name}`, () => {
    assert.deepEqual(BoardEngine.validateEngineJson(item.engine_json), []);
    if ("error_contains" in item) {
      assert.throws(
        () => BoardEngine.flatten(item.engine_json, item.legacy),
        (error) => error.message.includes(item.error_contains),
      );
    }
    for (const [key, value] of Object.entries(item.flatten_partial ?? {})) {
      assert.deepEqual(BoardEngine.flatten(item.engine_json, item.legacy)[key], value, key);
    }
  });
}

test("flatten returns independent default lists", () => {
  BoardEngine.flatten(null, {}).retry_delays.push(999);
  assert.deepEqual(BoardEngine.flatten(null, {}).retry_delays, LEGACY.retry_delays);
});

for (const item of CASES.error_rule_cases) {
  test(`error rule: ${item.name}`, () => {
    const rules = item.rules === "recommended" ? BoardEngine.RECOMMENDED_ERROR_RULES : item.rules;
    const { http, code, text } = item.error;
    const rule = BoardEngine.matchErrorRule(rules, http, code, text);
    const expect = item.expect;
    if (expect.rule_id === null) {
      assert.equal(rule, null);
      return;
    }
    assert.equal(rule.id, expect.rule_id);
    assert.equal(rule.action, expect.action);
    assert.equal(rule.retries, expect.retries);
  });
}

test("error rule presets equal the shared Python lists", () => {
  const presets = JSON.parse(
    fs.readFileSync(path.join(__dirname, "error_rule_presets.json"), "utf8"),
  );
  assert.deepEqual(BoardEngine.LEGACY_ERROR_RULES, presets.legacy);
  assert.deepEqual(BoardEngine.RECOMMENDED_ERROR_RULES, presets.recommended);
  assert.deepEqual(
    BoardEngine.validateEngineJson(JSON.stringify({ error_rules: presets.recommended })),
    [],
  );
});
