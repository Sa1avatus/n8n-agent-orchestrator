// Parse Review found no readable verdict: ask the Reviewer again, in the same session, for the JSON only.
// The number of re-asks is hermes_config.reviewer_max_retries; when used up, fail with the parse error.
const cfg = $('Constants').first().json;
const __reviewer = ($('Build Reviewer Run Input').isExecuted ? $('Build Reviewer Run Input').last().json : { model: $('Constants').first().json.reviewer_model, provider: $('Constants').first().json.reviewer_provider });
const maxRetries = Number(cfg.reviewer_max_provider_retries ?? 0);
const attempt = $runIndex; // 0 on the first re-ask
const err = String($json.review_parse_error ?? 'no valid review JSON object');
if (attempt >= maxRetries) throw new Error(`${err} (the Reviewer was re-asked ${attempt} time(s))`);
let session = '';
for (const n of ['Verdict Retry Result', 'Save Reviewer Run Context']) {
  try { if ($(n).isExecuted) { const s = String($(n).last().json.reviewer_session_id ?? ''); if (s) { session = s; break; } } } catch (_) {}
}
if (!session) throw new Error(`${err} (no Reviewer session to re-ask)`);
const prompt = [
  `Your previous reply did not contain a readable verdict (${err}).`,
  'Do NOT review again and do NOT use tools. Reply now with ONLY this JSON object, nothing before or after it:',
  '{"status":"pass" or "needs_changes","score":0-100,"reason":"a specific reason","next_task":"a specific fix; an empty string on pass"}',
  'Rules: valid JSON; no double quotes inside "reason" or "next_task" (write `code` or single quotes instead); keep the verdict you had reached.'
].join('\n');
return [{ json: {
  role: 'reviewer', run_id: '', session_id: session, input: prompt, resume_input: prompt,
  model: __reviewer.model, provider: __reviewer.provider,
  poll_seconds: cfg.reviewer_poll_seconds, max_polls: cfg.reviewer_max_polls,
  max_retries: cfg.reviewer_max_provider_retries, retry_count: 0, retry_delays: cfg.retry_delays,
  state_key: String(cfg.state_namespace ?? ''), service_model: cfg.service_model, service_provider: cfg.service_provider,
  runner_url: cfg.runner_url, runner_api_key: cfg.runner_api_key,
  working_directory: $('Mission').first().json.working_directory,
  verdict_retry: attempt + 1
} }];