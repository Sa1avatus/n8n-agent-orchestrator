(function () {
// === pure engine core (no DOM, no fetch, no globals) ===
const ENGINE_COLUMN = "engine_json";
const ENGINE_DEFAULTS = {
  max_attempts_per_task: 5,
  retry_delays: [15, 30, 60, 120, 300],
  runner_url: "http://ahawr-runner:8700",
  run_manager_workflow_id: "nhjwX1G7FiVTO2Ah",
  telegram_chat_id: "",
};
const LEGACY_COLUMNS = {
  max_attempts_per_task: "max_attempts_per_task",
  retry_delays: "retry_delays_json",
  runner_url: "runner_url",
  run_manager_workflow_id: "run_manager_workflow_id",
  telegram_chat_id: "telegram_chat_id",
};
const ENGINE_KEYS = [...Object.keys(ENGINE_DEFAULTS), "error_rules"];
const MIN_MAX_ATTEMPTS = 1;
const MAX_ERROR_RULES = 40;
const MAX_RULE_RETRIES = 5;
const ERROR_ACTIONS = ["compact", "fail", "failover", "retry"];
const ERROR_RULE_KEYS = ["id", "match", "action", "retries", "note"];
const ERROR_MATCH_KEYS = ["http", "words", "codes"];
// shortcut: legacy rules use the worker default 3, upgrade when a rule can name a role's retries
const LEGACY_RULE_RETRIES = 3;
const LEGACY_ERROR_RULES = [
  {
    id: "legacy-context-code",
    match: { codes: ["context_window"] },
    action: "compact",
    retries: LEGACY_RULE_RETRIES,
  },
  {
    id: "legacy-context-words",
    match: {
      words: [
        "context window exceeded",
        "contextwindow exceeded",
        "context windowexceeded",
        "contextwindowexceeded",
        "exceeds the available context",
        "context length",
        "prompt is too long",
        "maximum context",
      ],
    },
    action: "compact",
    retries: LEGACY_RULE_RETRIES,
  },
  {
    id: "legacy-transient-http",
    match: { http: [400, 408, 409, 425, 429, 500, 502, 503, 504, 529] },
    action: "retry",
    retries: LEGACY_RULE_RETRIES,
  },
  {
    id: "legacy-transient-words",
    match: {
      words: [
        "temporarily overloaded",
        "upstream error",
        "upstream request failed",
        "service unavailable",
        "overloaded",
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "rate limit",
        "too many requests",
        "no longer available",
        "no provider serves",
        "model not found",
      ],
    },
    action: "retry",
    retries: LEGACY_RULE_RETRIES,
  },
];
const RECOMMENDED_ERROR_RULES = [
  {
    id: "weekly-limit-text",
    match: { words: ["weekly limit"] },
    action: "retry",
    retries: 2,
  },
  {
    id: "rate-limit-429",
    match: { http: [429], words: ["rate limit", "too many requests"] },
    action: "retry",
    retries: 2,
  },
  {
    id: "transient-5xx",
    match: {
      http: [408, 409, 425, 500, 502, 503, 504, 529],
      words: ["timeout", "overloaded", "upstream", "connection reset"],
    },
    action: "retry",
    retries: 2,
  },
  {
    id: "model-gone",
    match: {
      http: [400, 404],
      words: ["no longer available", "no provider serves", "model not found", "does not exist"],
    },
    action: "failover",
    retries: 0,
  },
  {
    id: "credits-402",
    match: {
      http: [402],
      words: ["insufficient credits", "credit balance", "quota exceeded", "usage limit"],
    },
    action: "failover",
    retries: 0,
  },
  {
    id: "auth-401-403",
    match: { http: [401, 403], words: ["invalid api key", "expired"] },
    action: "failover",
    retries: 0,
  },
  {
    id: "context-413",
    match: { http: [413], words: ["context length"] },
    action: "compact",
    retries: 2,
  },
  { id: "other-400", match: { http: [400] }, action: "retry", retries: 2 },
  { id: "else-fail", match: {}, action: "fail", retries: 0 },
];
const NON_JSON_CONSTANT = /(?<=[\[,:]\s*)-?(?:NaN|Infinity)\b/;

// shortcut: a NaN-like word in a string of invalid JSON is misreported, upgrade to a tokenizer
function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch (error) {
    const constant = NON_JSON_CONSTANT.exec(text);
    if (constant) throw new Error(`${constant[0]} is not allowed in JSON`);
    throw error;
  }
}

function checkAttempts(value, where) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < MIN_MAX_ATTEMPTS) {
    throw new Error(
      `${where} must be a number of at least ${MIN_MAX_ATTEMPTS}, got ${JSON.stringify(value)}`,
    );
  }
  return value;
}

function checkDelay(element, where) {
  const number = typeof element === "string" && element.trim() ? Number(element) : element;
  if (typeof number !== "number" || !Number.isFinite(number) || number < 0) {
    throw new Error(`${where} must hold numbers >= 0, got ${JSON.stringify(element)}`);
  }
  return number;
}

function checkDelays(value, where) {
  let list = value;
  if (typeof value === "string") {
    const source = value.trim();
    if (!source) return null;
    try {
      list = parseJson(source);
    } catch (error) {
      throw new Error(`${where} is not valid JSON (${error.message})`);
    }
  }
  if (!Array.isArray(list)) {
    throw new Error(
      `${where} must be a JSON array of delays in seconds, got ${JSON.stringify(list)}`,
    );
  }
  return list.map((element) => checkDelay(element, where));
}

function checkText(key, value, where) {
  if (typeof value !== "string") {
    throw new Error(`${where} must be a string, got ${JSON.stringify(value)}`);
  }
  let text = value.trim();
  if (key === "runner_url") text = text.replace(/\/+$/, "");
  return text || null;
}

function isObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function allOf(value, test) {
  return Array.isArray(value) && value.every(test);
}

function checkMatch(match, where) {
  if (!isObject(match)) {
    throw new Error(`${where} must be a JSON object, got ${JSON.stringify(match)}`);
  }
  for (const key of Object.keys(match)) {
    if (!ERROR_MATCH_KEYS.includes(key)) throw new Error(`${where}: unknown key "${key}"`);
  }
  if (Object.hasOwn(match, "http") && !allOf(match.http, (item) => Number.isInteger(item))) {
    throw new Error(`${where}.http must be a list of integers, got ${JSON.stringify(match.http)}`);
  }
  for (const key of ["words", "codes"]) {
    if (Object.hasOwn(match, key) && !allOf(match[key], (item) => typeof item === "string")) {
      const got = JSON.stringify(match[key]);
      throw new Error(`${where}.${key} must be a list of strings, got ${got}`);
    }
  }
}

function checkRule(rule, where) {
  if (!isObject(rule)) {
    throw new Error(`${where} must be a JSON object, got ${JSON.stringify(rule)}`);
  }
  for (const key of Object.keys(rule)) {
    if (!ERROR_RULE_KEYS.includes(key)) throw new Error(`${where}: unknown key "${key}"`);
  }
  if (typeof rule.id !== "string" || !rule.id.trim()) {
    throw new Error(`${where}.id must be a non-empty string, got ${JSON.stringify(rule.id)}`);
  }
  if (!ERROR_ACTIONS.includes(rule.action)) {
    const allowed = ERROR_ACTIONS.join(", ");
    const got = JSON.stringify(rule.action);
    throw new Error(`${where}.action must be one of ${allowed}, got ${got}`);
  }
  if (!Number.isInteger(rule.retries) || rule.retries < 0 || rule.retries > MAX_RULE_RETRIES) {
    const got = JSON.stringify(rule.retries);
    const bounds = `an integer from 0 to ${MAX_RULE_RETRIES}`;
    throw new Error(`${where}.retries must be ${bounds}, got ${got}`);
  }
  if (Object.hasOwn(rule, "note") && typeof rule.note !== "string") {
    throw new Error(`${where}.note must be a string, got ${JSON.stringify(rule.note)}`);
  }
  checkMatch(Object.hasOwn(rule, "match") ? rule.match : {}, `${where}.match`);
}

// shortcut: JSON 1.0 passes as the integer 1 (one JS number type), upgrade with a JSON tokenizer
function checkRules(value, where) {
  if (!Array.isArray(value)) {
    throw new Error(`${where} must be a JSON array of rules, got ${JSON.stringify(value)}`);
  }
  if (value.length > MAX_ERROR_RULES) {
    throw new Error(`${where} must hold at most ${MAX_ERROR_RULES} rules, got ${value.length}`);
  }
  const ids = new Set();
  for (const [index, rule] of value.entries()) {
    checkRule(rule, `${where}[${index}]`);
    if (ids.has(rule.id)) throw new Error(`${where}: duplicate id ${JSON.stringify(rule.id)}`);
    ids.add(rule.id);
  }
  return value;
}

function normalize(key, value, where) {
  if (value === null || value === undefined) return null;
  if (key === "retry_delays") return checkDelays(value, where);
  if (key === "max_attempts_per_task") return checkAttempts(value, where);
  if (key === "error_rules") return checkRules(value, where);
  return checkText(key, value, where);
}

function parseEngine(engineJson) {
  const source = String(engineJson ?? "").trim();
  if (!source) return { accepted: {}, errors: [] };
  let parsed;
  try {
    parsed = parseJson(source);
  } catch (error) {
    return { accepted: {}, errors: [`${ENGINE_COLUMN} is not valid JSON (${error.message})`] };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { accepted: {}, errors: [`${ENGINE_COLUMN} must be a JSON object`] };
  }
  const accepted = {};
  const errors = [];
  for (const [key, value] of Object.entries(parsed)) {
    if (!ENGINE_KEYS.includes(key)) {
      errors.push(`${ENGINE_COLUMN}: unknown key "${key}"`);
      continue;
    }
    try {
      const normalized = normalize(key, value, `${ENGINE_COLUMN}.${key}`);
      if (normalized !== null) accepted[key] = normalized;
    } catch (error) {
      errors.push(error.message);
    }
  }
  return { accepted, errors };
}

function validateEngineJson(engineJson) {
  return parseEngine(engineJson).errors;
}

function flatten(engineJson, legacyRow) {
  const { accepted, errors } = parseEngine(engineJson);
  if (errors.length) throw new Error(errors.join("; "));
  const flat = {};
  for (const [key, fallback] of Object.entries(ENGINE_DEFAULTS)) {
    let value = accepted[key] ?? null;
    if (value === null) {
      const column = LEGACY_COLUMNS[key];
      value = normalize(key, legacyRow[column], column);
    }
    flat[key] = value === null ? structuredClone(fallback) : value;
  }
  return flat;
}

function ruleMatches(match, httpCode, failureCode, text) {
  if (match.http?.length && !match.http.includes(httpCode)) return false;
  if (match.words?.length && !match.words.some((word) => text.includes(word.toLowerCase()))) {
    return false;
  }
  return (
    !match.codes?.length || match.codes.some((code) => failureCode.includes(code.toLowerCase()))
  );
}

function matchErrorRule(rules, httpCode, failureCode, text) {
  const lowerCode = (failureCode ?? "").toLowerCase();
  const lowerText = (text ?? "").toLowerCase();
  for (const rule of rules?.length ? rules : LEGACY_ERROR_RULES) {
    if (ruleMatches(rule.match ?? {}, httpCode, lowerCode, lowerText)) return rule;
  }
  return null;
}
// === end pure engine core ===

// === export (browser global + CommonJS module.exports) ===
const BoardEngine = {
  ENGINE_DEFAULTS,
  LEGACY_ERROR_RULES,
  RECOMMENDED_ERROR_RULES,
  parseEngine,
  validateEngineJson,
  flatten,
  matchErrorRule,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardEngine;
} else if (typeof document !== "undefined") {
  globalThis.BoardEngine = BoardEngine;
}
})();
