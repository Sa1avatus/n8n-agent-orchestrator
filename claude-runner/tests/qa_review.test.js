// Table-driven test for the QA Review Code node of the n8n workflow.
// Run: node --test tests/qa_review.test.js (from claude-runner/)
//
// The jsCode comes from data-tables/ops/qa_workflow_patch.py (QA_REVIEW_JS) and runs
// with fake $() node lookups and workflow static data. Cases live in qa_review_cases.json,
// one per row of the fail-open / fail-closed table in docs/qa-gate.md.
"use strict";
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const assert = require("node:assert/strict");
const { execFileSync } = require("child_process");
const cases = require("./qa_review_cases.json").cases;

const OPS_DIR = path.join(__dirname, "..", "..", "data-tables", "ops");
const GATE_DOC = path.join(__dirname, "..", "docs", "qa-gate.md");
const MISSION_ID = "mission-1";
const BASE = { mission_id: MISSION_ID, task_index: 4 };
const EXTRACT = [
  "import json, sys",
  "sys.path.insert(0, sys.argv[1])",
  "import qa_workflow_patch",
  "print(json.dumps(qa_workflow_patch.QA_REVIEW_JS))",
].join("\n");

const QA_REVIEW_JS = JSON.parse(
  execFileSync("python", ["-c", EXTRACT, OPS_DIR], { encoding: "utf8" }),
);

function runReview(testCase) {
  const staticData = { [`qa_retries:${MISSION_ID}`]: testCase.retries };
  const nodes = {
    "All Tasks Completed?": { json: BASE },
    Mission: { json: { mission_id: MISSION_ID } },
    "QA Poll": { json: testCase.poll },
  };
  const lookup = (name) => {
    if (!(name in nodes)) throw new Error(`no node named ${name}`);
    return { first: () => nodes[name], last: () => nodes[name] };
  };
  const run = new Function("$", "$getWorkflowStaticData", QA_REVIEW_JS);
  const [item] = run(lookup, () => staticData);
  return { json: item.json, staticData };
}

function decisionOf(json) {
  return json.review_status === "needs_changes" ? "needs_changes" : "accept";
}

function noticeCount(json) {
  return json.qa_notice ? 1 : 0;
}

function tableRows() {
  return new Set(
    fs
      .readFileSync(GATE_DOC, "utf8")
      .split("\n")
      .map((line) => /^\| (\d+) \|/.exec(line))
      .filter(Boolean)
      .map((match) => Number(match[1])),
  );
}

test("every row of the fail-open / fail-closed table has a case", () => {
  assert.deepEqual([...new Set(cases.map((c) => c.row))].sort((a, b) => a - b), [...tableRows()].sort((a, b) => a - b));
});

for (const testCase of cases) {
  test(`row ${testCase.row}: ${testCase.name}`, () => {
    const { json, staticData } = runReview(testCase);
    const key = `qa_retries:${MISSION_ID}`;
    assert.equal(decisionOf(json), testCase.expect.decision);
    if (testCase.expect.next_task_contains === null) {
      assert.equal(json.review_next_task, undefined);
    } else {
      assert.ok(json.review_next_task.includes(testCase.expect.next_task_contains), json.review_next_task);
    }
    assert.equal(noticeCount(json), testCase.expect.notices, json.qa_notice);
    assert.equal(staticData[key], testCase.expect.retries);
  });
}

test("blocked, error, timeout and no claim never send the mission back", () => {
  const infrastructure = cases.filter((c) => c.never_rejects);
  assert.equal(infrastructure.length, 4);
  for (const testCase of infrastructure) {
    const { json } = runReview(testCase);
    assert.notEqual(decisionOf(json), "needs_changes", testCase.name);
    assert.equal(json.review_status, undefined, testCase.name);
  }
});
