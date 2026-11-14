// Runs the shared features_cases.json against board_features.js; mirrors board_roles.test.js.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const SRC = path.join(__dirname, "..", "src", "ahawr_runner");
const BoardFeatures = require(path.join(SRC, "board_features.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "features_cases.json"), "utf8"));
const LEGACY = CASES.legacy_constants;

// The one source of the defaults: the JSON file the features route answers with.
const DEFAULTS_PATH = path.join(SRC, "features_defaults.json");
const SHIPPED_DEFAULTS = JSON.parse(fs.readFileSync(DEFAULTS_PATH, "utf8"));

// Hands the functions exactly the fields the route answer carries (feature_defaults,
// param_minimums). board_features.js keeps no copy, so every test feeds it first.
function feedShippedDefaults() {
  const errors = BoardFeatures.setFeatureDefaults({
    feature_defaults: SHIPPED_DEFAULTS.features,
    param_minimums: SHIPPED_DEFAULTS.param_minimums,
  });
  assert.deepEqual(errors, [], "features_defaults.json is accepted by setFeatureDefaults");
}

for (const item of CASES.parse) {
  test(`parse: ${item.name}`, () => {
    feedShippedDefaults();
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
  feedShippedDefaults();
  const legacyText = JSON.stringify(LEGACY);
  for (const text of ["", null, undefined]) {
    const { features, errors } = BoardFeatures.parseFeatures(text, text);
    assert.deepEqual(errors, []);
    assert.equal(JSON.stringify(BoardFeatures.flatten(features)), legacyText);
  }
});

// === pinned characterization of T005, now fed with the single defaults file ===
// shortcut: the pinned fixture was captured before think_profiles and mission_dependencies
// entered the single source, so those are the keys it adds; upgrade when the fixture is
// regenerated with the one-source defaults.
const PINED = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "board_features_pinned.json"), "utf8"));
const PYTHON_PINED = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "features_pinned.json"), "utf8"));
const PINED_EXTRA = ["think_profiles", "mission_dependencies"];

for (const [feature, forms] of Object.entries(PINED)) {
  for (const [form, pinned] of Object.entries(forms)) {
    test(`pinned with features_defaults: ${feature} / ${form}`, () => {
      feedShippedDefaults();
      const parsed = BoardFeatures.parseFeatures(pinned.inputs[0], pinned.inputs[1]);
      assert.deepEqual(parsed.errors, pinned.errors, `${feature} ${form}: errors`);
      assert.deepEqual(BoardFeatures.flatten(parsed.features), pinned.flatten, `${feature} ${form}: flatten`);
      const shared = Object.fromEntries(
        Object.entries(parsed.features).filter(([key]) => !PINED_EXTRA.includes(key)),
      );
      assert.deepEqual(shared, pinned.resolved, `${feature} ${form}: resolved features`);
    });
  }
}

for (const extra of PINED_EXTRA) {
  test(
    `features_defaults: ${extra} is resolved from the single file like in features_pinned.json`,
    () => {
      feedShippedDefaults();
      assert.ok(
        Object.hasOwn(SHIPPED_DEFAULTS.features, extra),
        `features_defaults.json must hold ${extra}, not the JS file`,
      );
      for (const [form, pinned] of Object.entries(PYTHON_PINED[extra])) {
        const parsed = BoardFeatures.parseFeatures(pinned.inputs[0], pinned.inputs[1]);
        assert.deepEqual(parsed.errors, pinned.errors, `${extra} ${form}: errors`);
        if (parsed.features) {
          assert.deepEqual(
            parsed.features,
            pinned.resolved,
            `${extra} ${form}: every feature of the pinned answer`,
          );
        }
      }
    },
  );
}

// The flag list is pinned by PINED_EXTRA, so the UI-side flag contract is checked here
// against the same defaults file the API route answers with.
test("features_defaults: mission_dependencies is a flag, off by default, on when saved", () => {
  feedShippedDefaults();
  assert.deepEqual(SHIPPED_DEFAULTS.features.mission_dependencies, { enabled: false });
  const off = BoardFeatures.parseFeatures("", "");
  assert.deepEqual(off.errors, [], "empty columns must not report errors");
  assert.deepEqual(off.features.mission_dependencies, { enabled: false });
  const on = BoardFeatures.parseFeatures('{"mission_dependencies": {"enabled": true}}', "");
  assert.deepEqual(on.errors, []);
  assert.equal(on.features.mission_dependencies.enabled, true);
  const bad = BoardFeatures.parseFeatures('{"mission_dependencies": {"enabled": "yes"}}', "");
  assert.deepEqual(bad.errors, [
    'features_json: mission_dependencies.enabled must be true or false, got "yes"',
  ]);
});

test("features_defaults: board_features.js source holds no defaults object and no default literal", () => {
  const source = fs.readFileSync(path.join(SRC, "board_features.js"), "utf8");
  assert.ok(!/FEATURE_DEFAULTS\s*=/.test(source), "board_features.js still declares a defaults object");
  assert.ok(!/PARAM_MINIMUMS\s*=/.test(source), "board_features.js still declares a minimums object");
  const params = new Set(Object.keys(SHIPPED_DEFAULTS.param_minimums));
  for (const body of Object.values(SHIPPED_DEFAULTS.features)) {
    for (const param of Object.keys(body)) params.add(param);
  }
  for (const param of params) {
    assert.ok(
      !new RegExp(`${param}\\s*:\\s*(?:true|false|\\d+)`).test(source),
      `board_features.js still carries a default literal for "${param}"`,
    );
  }
  assert.ok(!source.includes("enabled: false"), "board_features.js still carries enabled: false literals");
});

test("features_defaults: a route answer without the defaults field takes the error path, not literals", () => {
  const setErrors = BoardFeatures.setFeatureDefaults({ features_json: "", version: "2026-11-13T00:00:00+00:00" });
  assert.ok(setErrors.length > 0, "a route answer without feature_defaults must be reported, not ignored");
  const parsed = BoardFeatures.parseFeatures("", "");
  assert.equal(parsed.features, null, "no features are invented when the defaults are missing");
  assert.ok(parsed.errors.length > 0, "the parse must report the missing defaults");
  feedShippedDefaults(); // restore for the tests that follow
});
