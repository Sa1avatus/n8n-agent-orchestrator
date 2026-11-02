(function () {
// === pure retrieval core (no DOM, no fetch, no globals) ===
const DEFAULTS = {
  retrieval_enabled: false,
  retrieval_url: "http://ahawr-retrieval:8500",
  retrieval_corpora_json: [],
  retrieval_timeout_ms: 120000,
  retrieval_worker_max_tokens: 6000,
  retrieval_reviewer_max_tokens: 5000,
  retrieval_label: "",
};
const JSON_KEYS = {
  enabled: "retrieval_enabled",
  url: "retrieval_url",
  corpora: "retrieval_corpora_json",
  timeout_ms: "retrieval_timeout_ms",
  worker_max_tokens: "retrieval_worker_max_tokens",
  reviewer_max_tokens: "retrieval_reviewer_max_tokens",
  label: "retrieval_label",
};
const INTEGER_MINIMUMS = { timeout_ms: 1000, worker_max_tokens: 100, reviewer_max_tokens: 100 };
const EXPECTED = {
  enabled: "true or false",
  url: "a non-empty string",
  corpora: "an array of non-empty strings",
  label: "a string",
};

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isNonBlankString(value) {
  return typeof value === "string" && value.trim() !== "";
}

function isValid(key, value) {
  if (key === "enabled") return typeof value === "boolean";
  if (Object.hasOwn(INTEGER_MINIMUMS, key)) {
    return Number.isInteger(value) && value >= INTEGER_MINIMUMS[key];
  }
  if (key === "corpora") return Array.isArray(value) && value.every(isNonBlankString);
  if (key === "url") return isNonBlankString(value);
  return typeof value === "string";
}

function expected(key) {
  if (Object.hasOwn(INTEGER_MINIMUMS, key)) return `an integer >= ${INTEGER_MINIMUMS[key]}`;
  return EXPECTED[key];
}

function checkKey(key, value, accepted, errors) {
  if (!Object.hasOwn(JSON_KEYS, key)) {
    errors.push(`retrieval_json: unknown key "${key}"`);
  } else if (isValid(key, value)) {
    accepted[JSON_KEYS[key]] = key === "label" ? value.trim() : value;
  } else {
    errors.push(`retrieval_json: ${key} must be ${expected(key)}, got ${JSON.stringify(value)}`);
  }
}

function parse(raw) {
  if (raw === null || raw === undefined) return { accepted: {}, errors: [] };
  let given = raw;
  if (typeof raw === "string") {
    if (raw.trim() === "") return { accepted: {}, errors: [] };
    try {
      given = JSON.parse(raw);
    } catch {
      return { accepted: {}, errors: ["retrieval_json: not valid JSON"] };
    }
  }
  if (!isPlainObject(given)) {
    return { accepted: {}, errors: ["retrieval_json: must be a JSON object"] };
  }
  const accepted = {};
  const errors = [];
  for (const [key, value] of Object.entries(given)) checkKey(key, value, accepted, errors);
  return { accepted, errors };
}

function validate(raw) {
  return parse(raw).errors;
}

function isSet(value) {
  return value !== null && value !== undefined && value !== "";
}

function flatten(raw, legacy) {
  const { accepted, errors } = parse(raw);
  if (errors.length) throw new Error(errors.join("; "));
  const flat = {};
  for (const [name, fallback] of Object.entries(DEFAULTS)) {
    if (Object.hasOwn(accepted, name)) flat[name] = accepted[name];
    else if (isSet(legacy[name])) flat[name] = legacy[name];
    else flat[name] = structuredClone(fallback);
  }
  return flat;
}
// === end pure retrieval core ===

// === export (browser global + CommonJS module.exports) ===
const BoardRetrieval = {
  DEFAULTS,
  parse,
  validate,
  flatten,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardRetrieval;
} else if (typeof document !== "undefined") {
  globalThis.BoardRetrieval = BoardRetrieval;
}
})();
