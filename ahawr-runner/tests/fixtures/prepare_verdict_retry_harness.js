// Runs the Prepare Verdict Retry node code with stub n8n nodes; node harness.js <code.js> <failed_over_from>.
const fs = require("fs");
const code = fs.readFileSync(process.argv[2], "utf8");
const failedOverFrom = process.argv[3];
const nodes = {
  Constants: { first: () => ({ json: { reviewer_max_provider_retries: 5, retry_delays: [1], state_namespace: "s", runner_url: "u" } }) },
  "Build Reviewer Run Input": { isExecuted: true, last: () => ({ json: {
    provider: "anthropic", model: "opus", fallback_provider: "strata", fallback_model: "qwen",
    fallback_chain: [{ provider: "openrouter", model: "free" }] } }) },
  "Reviewer Start": { isExecuted: true, last: () => ({ json: { failed_over_from: failedOverFrom } }) },
  "Reviewer Verdict Retry Start": { isExecuted: false },
  "Verdict Retry Result": { isExecuted: false },
  "Save Reviewer Run Context": { isExecuted: true, last: () => ({ json: { reviewer_session_id: "sess" } }) },
  Mission: { first: () => ({ json: { working_directory: "w" } }) },
};
const run = new Function("$", "$json", "$runIndex", code);
const out = run((name) => nodes[name], { review_parse_error: "no json" }, 0);
const j = out[0].json;
console.log(JSON.stringify({ provider: j.provider, model: j.model, fp: j.fallback_provider, fm: j.fallback_model, chain: j.fallback_chain }));
