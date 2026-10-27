// Runs the shared roles_cases.json against board_roles.js; mirrors test_roles_config.py.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardRoles = require(path.join(__dirname, "..", "src", "claude_runner", "board_roles.js"));

const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "roles_cases.json"), "utf8"));
const PROVIDERS = CASES.providers;

// Same fragments as MESSAGE_FRAGMENTS in test_roles_config.py.
const MESSAGE_FRAGMENTS = {
  first_key: "first attempt key",
  key_order: "is not greater than",
  bad_key: "must be a positive integer",
  duplicate_key: "duplicate key",
  bad_role_value: "value must be a model string or an object",
  array_model: "model must be a string",
  object_model: "model must be a string",
  unknown_provider: "unknown provider",
  empty_model: "has no model name",
  empty_value: "has no model name",
  too_many_keys: "keys, at most",
  too_many_models: "distinct models",
  unknown_option: "unknown option",
  unknown_role: "is not a known role",
  bad_escalate: "must be a whole number",
  unknown_layer: "unknown layer",
  repeated_layer: "repeats layer",
  layers_not_list: "must be a list of layer names",
};

function matches(expected, message) {
  const element = expected.element.split(".").pop();
  return message.includes(MESSAGE_FRAGMENTS[expected.code]) && message.includes(element);
}

function assertExpected(expected, actual) {
  if (expected.length === 0) assert.deepEqual(actual, []);
  for (const item of expected) {
    assert.ok(
      actual.some((message) => matches(item, message)),
      JSON.stringify({ item, actual }),
    );
  }
}

for (const bundle of CASES.validation) {
  test(`validation: ${bundle.name}`, () => {
    const report = BoardRoles.validateRolesJson(bundle.input, PROVIDERS);
    assertExpected(bundle.errors, report.errors);
    assertExpected(bundle.warnings, report.warnings);
  });
}

for (const bundle of CASES.resolve) {
  test(`resolve: ${bundle.name}`, () => {
    const roles = BoardRoles.mergeRoles(bundle.global, bundle.mission);
    const resolved = BoardRoles.resolveRole(roles, bundle.role, bundle.attempt, bundle.legacy, bundle.facts ?? null);
    const expect = bundle.expect;
    assert.equal(resolved.provider, expect.provider);
    assert.equal(resolved.model, expect.model);
    if ("think" in expect) assert.equal(resolved.think, expect.think);
    if ("fresh" in expect) assert.equal(resolved.fresh, expect.fresh);
    for (const [key, value] of Object.entries(expect.options ?? {})) {
      assert.equal(resolved.options[key] ?? null, value);
    }
  });
}

test("previewChain: haiku, local, openrouter, sonnet", () => {
  const roles = {
    worker: {
      1: "anthropic/haiku",
      2: "local/local",
      3: "openrouter/openrouter",
      4: "anthropic/sonnet",
    },
  };
  assert.equal(
    BoardRoles.previewChain(roles, "worker"),
    "1 haiku, 2 local, 3 openrouter, 4+ sonnet",
  );
});

test("previewChain: string role shows one attempt with +", () => {
  assert.equal(BoardRoles.previewChain({ reviewer: "anthropic/sonnet" }, "reviewer"), "1+ sonnet");
});

test("previewChain: options are not listed as attempts", () => {
  const roles = { worker: { 1: "anthropic/haiku", options: { think: "off" } } };
  assert.equal(BoardRoles.previewChain(roles, "worker"), "1+ haiku");
});

test("previewChain: missing role gives an empty preview", () => {
  assert.equal(BoardRoles.previewChain({}, "worker"), "");
});

test("effectiveModels: each attempt uses the latest key at or below it", () => {
  const roles = { worker: { 1: "anthropic/haiku", 3: "local/qwen" } };
  assert.deepEqual(BoardRoles.effectiveModels(roles, "worker", 4), [
    "anthropic/haiku",
    "anthropic/haiku",
    "local/qwen",
    "local/qwen",
  ]);
});

test("effectiveModels: role without any model gives null per attempt", () => {
  assert.deepEqual(BoardRoles.effectiveModels({}, "worker", 2), [null, null]);
});

const SAVED_ROLES_TEXT = '{"worker":{"1":"anthropic/haiku","3":"local/qwen"},"qa":"anthropic/sonnet"}';

test("save with nothing set: roles_json is byte-identical to the stored text", () => {
  const values = { mode: "", max_minutes: "", max_cost_usd: "" };
  const options = BoardRoles.qaOptionsFrom(values);
  assert.deepEqual(options, {});
  assert.equal(BoardRoles.withQaOptions(SAVED_ROLES_TEXT, options), SAVED_ROLES_TEXT);
  assert.equal(BoardRoles.withQaOptions("", options), "");
});

test("save with mode and limits set: qa options carry the typed values", () => {
  const options = BoardRoles.qaOptionsFrom({ mode: "final", max_minutes: "30", max_cost_usd: "2.5" });
  assert.deepEqual(options, { mode: "final", max_minutes: 30, max_cost_usd: 2.5 });
  const text = BoardRoles.withQaOptions(SAVED_ROLES_TEXT, options);
  const roles = JSON.parse(text);
  assert.deepEqual(roles.qa, { 1: "anthropic/sonnet", options: { mode: "final", max_minutes: 30, max_cost_usd: 2.5 } });
  assert.deepEqual(roles.worker, { 1: "anthropic/haiku", 3: "local/qwen" });
  assert.deepEqual(BoardRoles.validateRolesJson(text, PROVIDERS).errors, []);
});

test("qaOptionsFrom: a non-numeric limit is refused", () => {
  assert.throws(() => BoardRoles.qaOptionsFrom({ max_minutes: "abc" }), /max_minutes must be a number/);
});

test("qaPlaceholders: the defaults the QA controls show (off, 20, 1)", () => {
  assert.deepEqual(BoardRoles.qaPlaceholders(), { mode: "off", max_minutes: "20", max_cost_usd: "1" });
  assert.deepEqual(BoardRoles.QA_MODES, ["off", "final", "ui_tasks", "nightly"]);
});

test("qaDiffRows: unchanged QA values give no rows, so nothing is sent", () => {
  const initial = BoardRoles.qaValuesOf(SAVED_ROLES_TEXT);
  assert.deepEqual(BoardRoles.qaDiffRows(initial, { ...initial }), []);
});

test("qaDiffRows: only the changed QA fields are listed", () => {
  const initial = BoardRoles.qaValuesOf(SAVED_ROLES_TEXT);
  const rows = BoardRoles.qaDiffRows(initial, { ...initial, mode: "final", max_minutes: "" });
  assert.deepEqual(rows, [{ field: "mode", old: "", new: "final" }]);
});

test("smoke: absent reads as unchecked, an unchecked save adds nothing", () => {
  const values = BoardRoles.qaValuesOf(SAVED_ROLES_TEXT);
  assert.equal(values.smoke, "");
  assert.deepEqual(BoardRoles.qaOptionsFrom({ mode: "final", smoke: "" }), { mode: "final" });
  const text = BoardRoles.qaRolesText(SAVED_ROLES_TEXT, { ...values, mode: "final" });
  assert.deepEqual(JSON.parse(text).qa, { 1: "anthropic/sonnet", options: { mode: "final" } });
});

test("smoke: checked reads back as true and a cleared box removes the option", () => {
  const stored = '{"qa":{"1":"anthropic/sonnet","options":{"mode":"final","smoke":true}}}';
  const values = BoardRoles.qaValuesOf(stored);
  assert.equal(values.smoke, "true");
  assert.deepEqual(BoardRoles.qaOptionsFrom({ mode: "final", smoke: "true" }), { mode: "final", smoke: true });
  const cleared = BoardRoles.qaRolesText(stored, { provider: "anthropic", model: "sonnet",
    mode: "final", max_minutes: "", max_cost_usd: "", smoke: "" });
  assert.deepEqual(JSON.parse(cleared).qa, { 1: "anthropic/sonnet", options: { mode: "final" } });
  assert.deepEqual(BoardRoles.qaDiffRows({ ...values }, { ...values, smoke: "" }),
    [{ field: "smoke", old: "true", new: "" }]);
});

test("qaRolesText: setting a model writes qa attempt 1 and keeps the other roles", () => {
  const values = { provider: "local", model: "qwen", mode: "", max_minutes: "", max_cost_usd: "" };
  const roles = JSON.parse(BoardRoles.qaRolesText('{"worker":{"1":"anthropic/haiku"}}', values));
  assert.deepEqual(roles, { worker: { 1: "anthropic/haiku" }, qa: { 1: "local/qwen" } });
});

test("qaRolesText: clearing the model removes the qa entry", () => {
  const values = { provider: "", model: "", mode: "", max_minutes: "", max_cost_usd: "" };
  assert.deepEqual(JSON.parse(BoardRoles.qaRolesText(SAVED_ROLES_TEXT, values)).qa, undefined);
});

test("qaRolesText: clearing one limit removes only that option", () => {
  const set = BoardRoles.qaRolesText(SAVED_ROLES_TEXT,
    { provider: "anthropic", model: "sonnet", mode: "final", max_minutes: "30", max_cost_usd: "2" });
  const cleared = BoardRoles.qaRolesText(set,
    { provider: "anthropic", model: "sonnet", mode: "final", max_minutes: "", max_cost_usd: "2" });
  assert.deepEqual(JSON.parse(cleared).qa, { 1: "anthropic/sonnet", options: { mode: "final", max_cost_usd: 2 } });
});

test("qaRolesText: a model without a provider is refused", () => {
  assert.throws(() => BoardRoles.qaRolesText("{}", { provider: "", model: "qwen", mode: "", max_minutes: "", max_cost_usd: "" }),
    /qa provider is required with a model/);
});

// Source-level check only: no DOM harness runs board_edit_win.js. It proves the ROLES entry and
// the placeholder source exist in the window module; the rendered row is not exercised here.
test("board_edit_win.js (source check, not a DOM test): QA role row and placeholders are wired", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "claude_runner", "board_edit_win.js"), "utf8");
  assert.ok(src.includes('["ew.qa", "var(--r-qa, #ff6b8a)", "qa_model", "qa_provider"]'));
  assert.ok(src.includes("BoardRoles.qaPlaceholders()"));
  assert.ok(src.includes('["smoke", "qa_smoke"]'));
  assert.ok(src.includes('smoke: "ew.qa.smoke"'));
  assert.ok(src.includes('"/roles"') && src.includes("BoardRoles.saveQaRoles("));
});

const REGISTRY = [
  { name: "anthropic", enabled: true, models: [{ model: "sonnet" }, { model: "haiku" }] },
  { name: "local", enabled: false, models: [{ model: "qwen" }] },
];

test("providerChoices: enabled providers only", () => {
  assert.deepEqual(BoardRoles.providerChoices(REGISTRY), ["anthropic"]);
});

test("modelChoices: the models of the named provider, nothing for an unlisted one", () => {
  assert.deepEqual(BoardRoles.modelChoices(REGISTRY, "anthropic"), ["sonnet", "haiku"]);
  assert.deepEqual(BoardRoles.modelChoices(REGISTRY, "unlisted"), []);
});

test("providers off or unreachable: no suggestions, the inputs stay free text", () => {
  assert.deepEqual(BoardRoles.providerChoices([]), []);
  assert.deepEqual(BoardRoles.modelChoices([], "anthropic"), []);
});

test("board_edit_win.js (source check): roles and QA inputs take the registry suggestions", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "claude_runner", "board_edit_win.js"), "utf8");
  assert.ok(src.includes('fetch("/api/board/providers"'));
  assert.ok(src.includes("BoardRoles.providerChoices(providerList)"));
  assert.ok(src.includes("BoardRoles.modelChoices(providerList, provEl.value)"));
  assert.ok(src.includes("choiceLists(provEl, modelEl, kp)"));
});

test("qaLayers: missing or empty layers mean ui, a list is kept", () => {
  assert.deepEqual(BoardRoles.qaLayers({}), ["ui"]);
  assert.deepEqual(BoardRoles.qaLayers({ layers: [] }), ["ui"]);
  assert.deepEqual(BoardRoles.qaLayers({ layers: ["regression", "api"] }), ["regression", "api"]);
});

function stubApi(roles, version, puts) {
  return {
    fetchJson: async () => ({ roles_json: roles, version }),
    postJson: async (url, body, method) => {
      puts.push({ url, body, method });
      return { roles_json: body.roles_json, version: "v2" };
    },
  };
}

test("saveQaRoles: a save with nothing changed makes no PUT (stubbed fetchJson and postJson)", async () => {
  const puts = [];
  const api = stubApi(SAVED_ROLES_TEXT, "v1", puts);
  const state = await BoardRoles.loadQaRoles(api, "/api/board/roles");
  const kept = await BoardRoles.saveQaRoles(api, "/api/board/roles", { ...state.initial }, state);
  assert.equal(puts.length, 0);
  assert.equal(kept, state);
});

test("saveQaRoles: a changed mode makes exactly one PUT with expected_version (mission and defaults URLs)", async () => {
  for (const url of ["/api/board/missions/m1/roles", "/api/board/roles"]) {
    const puts = [];
    const api = stubApi(SAVED_ROLES_TEXT, "v1", puts);
    const state = await BoardRoles.loadQaRoles(api, url);
    const next = await BoardRoles.saveQaRoles(api, url, { ...state.initial, mode: "final" }, state);
    assert.equal(puts.length, 1);
    assert.equal(puts[0].url, url);
    assert.equal(puts[0].method, "PUT");
    assert.equal(puts[0].body.expected_version, "v1");
    assert.equal(JSON.parse(puts[0].body.roles_json).qa.options.mode, "final");
    assert.equal(next.version, "v2");
    assert.equal(next.initial.mode, "final");
  }
});

test("board_mission_save.js postJson sends the dashboard header (source-level check, not a DOM test)", () => {
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "claude_runner", "board_mission_save.js"), "utf8");
  assert.ok(src.includes('"X-AHAWR-Dashboard": "1"'));
  assert.ok(src.includes("async function postJson(url, body, method = \"POST\", headers = {})"));
});
