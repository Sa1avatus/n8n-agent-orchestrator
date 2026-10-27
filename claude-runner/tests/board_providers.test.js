"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");

const providers = require(path.join(__dirname, "..", "src", "claude_runner", "board_providers.js"));
const cases = require(path.join(__dirname, "provider_api_types_cases.json"));

const {
  API_TYPES,
  effectiveType,
  effectiveCapabilities,
  fieldsForType,
  routeNote,
  workerWarning,
  validateProviderForm,
  buildSavePayload,
} = providers;

test("the JS api_types table equals the shared cases table", () => {
  assert.deepEqual(API_TYPES, cases.table);
});

test("routeNote matches the route cases", () => {
  for (const row of cases.route_cases) {
    assert.equal(routeNote(row.api_type), row.expected);
  }
});

test("effectiveType matches the effective api_type cases", () => {
  for (const row of cases.effective_api_type_cases) {
    assert.equal(effectiveType(row.provider, row.model), row.expected);
  }
});

test("effectiveCapabilities matches the listed keys of each case", () => {
  for (const row of cases.effective_capabilities_cases) {
    const capabilities = effectiveCapabilities(row.api_type, row.model_overrides);
    for (const [key, value] of Object.entries(row.expected)) {
      assert.equal(capabilities[key], value, `${row.api_type} ${key}`);
    }
  }
});

test("workerWarning matches the worker warning cases", () => {
  for (const row of cases.worker_warning_cases) {
    assert.equal(workerWarning(row.capabilities), row.expected);
  }
});

test("fieldsForType returns the extra header fields of the type", () => {
  assert.deepEqual(fieldsForType("anthropic"), ["anthropic-version"]);
  assert.deepEqual(fieldsForType("openai-chat"), []);
});

test("unknown api_type and capability are rejected", () => {
  assert.throws(() => routeNote("nope"), /unknown api_type: nope/);
  assert.throws(
    () => effectiveCapabilities("anthropic", { bogus: 1 }),
    /unknown capability: bogus/,
  );
});

test("validateProviderForm applies the server rules to a create", () => {
  const valid = { api_type: "anthropic", base_url: "https://api.example.com", enabled: true };
  assert.equal(validateProviderForm(valid, { isNew: true, name: "example-1" }), null);
  assert.equal(
    validateProviderForm({ base_url: "https://x.example" }, { isNew: true, name: "x" }),
    "missing field(s): api_type",
  );
  assert.equal(
    validateProviderForm({ ...valid, bogus: 1 }, { isNew: true, name: "x" }),
    "unknown field(s): bogus",
  );
  assert.equal(
    validateProviderForm({ ...valid, enabled: null }, { isNew: true, name: "x" }),
    "enabled must not be null",
  );
  assert.equal(
    validateProviderForm(valid, { isNew: true, name: "Bad Name" }),
    "name must match ^[a-z0-9][a-z0-9_-]{0,63}$",
  );
});

test("validateProviderForm rejects bad URLs, types and bounds", () => {
  const base = { api_type: "anthropic", base_url: "https://api.example.com" };
  const check = (fields) => validateProviderForm(fields, { isNew: false, name: "p" });
  assert.equal(
    check({ ...base, base_url: "https://user@api.example.com" }),
    "base_url must be an http or https URL with a host and no userinfo",
  );
  assert.equal(check({ ...base, base_url: "ftp://api.example.com" }),
    "base_url must be an http or https URL with a host and no userinfo");
  assert.equal(check({ context_tokens: 1.5 }), "context_tokens must be a whole number");
  assert.equal(check({ compact_pct: 101 }), "compact_pct must be between 1 and 100");
  assert.equal(check({ notes: "x".repeat(2001) }),
    "notes must be text of at most 2000 characters");
  assert.equal(check({ api_type: "nope" }), "unknown api_type: nope");
});

test("validateProviderForm on update needs at least one field", () => {
  assert.equal(validateProviderForm({}, { isNew: false, name: "p" }), "no fields to update");
  assert.equal(validateProviderForm({ notes: "ok" }, { isNew: false, name: "p" }), null);
});

test("buildSavePayload adds the name on create and expected_version on update", () => {
  assert.deepEqual(
    buildSavePayload({ notes: "a" }, { isNew: true, name: "p", expectedVersion: "v1" }),
    { name: "p", notes: "a" },
  );
  assert.deepEqual(
    buildSavePayload({ notes: "a" }, { isNew: false, name: "p", expectedVersion: "v1" }),
    { notes: "a", expected_version: "v1" },
  );
});
