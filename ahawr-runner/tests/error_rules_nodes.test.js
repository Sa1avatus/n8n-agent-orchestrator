// Runs the Evaluate Status and Retry Controller code of the Run Manager, after the ops patches, on the error_rule_cases of
// engine_cases.json. Each case runs with its own rules: an empty list gives LEGACY_ERROR_RULES, "recommended" the preset.
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");
const test = require("node:test");
const assert = require("node:assert/strict");
const BoardEngine = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_engine.js"));

const ROOT = path.join(__dirname, "..", "..");
const STAGED_EXPORT = path.join(ROOT, "scratchpad", "live_run_manager_export.json");
const PATCHES = ["failover_patch.py", "transient_400_patch.py", "error_rules_patch.py"];
const NODES = ["Evaluate Status", "Retry Controller"];
// The Retry Controller default for max_retries; the legacy rules carry retries null and leave the count to it.
const ROLE_MAX_RETRIES = 3;
const CASES = JSON.parse(fs.readFileSync(path.join(__dirname, "engine_cases.json"), "utf8")).error_rule_cases;

function patchedCode() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "run-manager-"));
  try {
    const copy = path.join(dir, "run_manager.json");
    fs.copyFileSync(STAGED_EXPORT, copy);
    for (const script of PATCHES) {
      const run = spawnSync("python", [path.join(ROOT, "data-tables", "ops", script), copy], { encoding: "utf8" });
      assert.equal(run.status, 0, `${script}: ${run.stderr}`);
    }
    const raw = JSON.parse(fs.readFileSync(copy, "utf8"));
    const workflow = Array.isArray(raw) ? raw[0] : raw;
    return Object.fromEntries(
      NODES.map((name) => {
        const node = workflow.nodes.find((n) => n.name === name);
        assert.ok(node, `${name} is missing from the Run Manager export`);
        return [name, node.parameters.jsCode];
      })
    );
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const CODE = patchedCode();

function runNode(name, json, upstream) {
  const dollar = (node) => {
    const data = upstream[node];
    return { isExecuted: data !== undefined, first: () => ({ json: data }), last: () => ({ json: data }) };
  };
  return new Function("$json", "$", CODE[name])(json, dollar)[0].json;
}

function evaluate(item, rules) {
  const body = { status: "failed", error: item.error.text, code: item.error.code };
  const envelope = item.error.http === null ? { body } : { body, statusCode: item.error.http };
  const normalized = { error_rules: rules, run_id: "", poll_count: 0, max_polls: 40 };
  return runNode("Evaluate Status", envelope, { "Normalize Request": normalized });
}

function retry(evaluated, retryCount, extra = {}) {
  const state = {
    retry_count: retryCount,
    max_retries: ROLE_MAX_RETRIES,
    retry_delays: [15, 30, 60],
    provider: "cloud",
    model: "worker",
    ...extra,
  };
  return runNode("Retry Controller", state, { "Evaluate Status": evaluated });
}

for (const item of CASES) {
  const recommended = item.rules === "recommended";
  const rules = recommended ? BoardEngine.RECOMMENDED_ERROR_RULES : item.rules;
  test(`error rule ${item.name} (${recommended ? "RECOMMENDED" : "LEGACY"})`, () => {
    const expect = item.expect;
    const evaluated = evaluate(item, rules);
    assert.equal(evaluated.error_rule_id, expect.rule_id ?? "");
    if (expect.rule_id === null) {
      assert.equal(evaluated.transient_error, false);
      return;
    }
    assert.equal(evaluated.transient_error, expect.action !== "fail");
    assert.equal(evaluated.context_overflow, expect.action === "compact");
    const retries = Math.min(ROLE_MAX_RETRIES, evaluated.error_retries ?? ROLE_MAX_RETRIES);
    assert.equal(retries, expect.retries);
    if (expect.action === "failover") {
      const switched = retry(evaluated, 0, {
        fallback_provider: "local",
        fallback_model: "fallback-model",
        full_input: "task",
        input: "task",
      });
      assert.equal(switched.failed_over_from, "cloud/worker");
      assert.equal(switched.model, "fallback-model");
      return;
    }
    if (retries > 0) assert.equal(retry(evaluated, retries - 1).retry_exhausted, false);
    assert.equal(retry(evaluated, retries).retry_exhausted, true);
  });
}
