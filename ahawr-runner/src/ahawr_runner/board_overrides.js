(function () {
// === pure overrides core (no DOM, no fetch, no globals) ===
const PREFIX = "overrides_json";
const RETRIEVAL_SECTION = "retrieval";
const CORPORA_NAME = "retrieval_corpora";
const SECTIONS = ["roles", "features", RETRIEVAL_SECTION];
const RETRIEVAL_NAMES = {
  enabled: "retrieval_enabled",
  url: "retrieval_url",
  corpora: CORPORA_NAME,
  timeout_ms: "retrieval_timeout_ms",
  worker_max_tokens: "retrieval_worker_max_tokens",
  reviewer_max_tokens: "retrieval_reviewer_max_tokens",
  label: "retrieval_label",
};
const DEFAULTS = {
  retrieval_enabled: false,
  retrieval_url: "http://ahawr-retrieval:8500",
  retrieval_corpora: [],
  retrieval_timeout_ms: 120000,
  retrieval_worker_max_tokens: 6000,
  retrieval_reviewer_max_tokens: 5000,
  retrieval_label: "",
};
const LEGACY_COLUMN_OVERRIDES = { [CORPORA_NAME]: "retrieval_corpora_json" };
const INTEGER_MINIMUMS = {
  timeout_ms: 1000,
  worker_max_tokens: 100,
  reviewer_max_tokens: 100,
};

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isSet(value) {
  return value !== undefined && value !== null && value !== "";
}

function loadObject(text, errors) {
  const source = String(text ?? "").trim();
  if (!source) return {};
  let given;
  try {
    given = JSON.parse(source);
  } catch {
    errors.push(`${PREFIX}: invalid JSON`);
    return {};
  }
  if (!isPlainObject(given)) {
    errors.push(`${PREFIX}: must be a JSON object`);
    return {};
  }
  return given;
}

function integerErrors(name, key, value) {
  const minimum = INTEGER_MINIMUMS[key];
  if (Number.isInteger(value) && value >= minimum) return [];
  return [`${name} must be an integer >= ${minimum}, got ${JSON.stringify(value)}`];
}

function corporaErrors(name, value) {
  if (!Array.isArray(value)) return [`${name} must be an array, got ${JSON.stringify(value)}`];
  return value.flatMap((item, index) =>
    typeof item === "string"
      ? []
      : [`${name}[${index}] must be a string, got ${JSON.stringify(item)}`],
  );
}

function fieldErrors(key, value) {
  const name = `${RETRIEVAL_SECTION}.${key}`;
  if (Object.hasOwn(INTEGER_MINIMUMS, key)) return integerErrors(name, key, value);
  if (key === "corpora") return corporaErrors(name, value);
  let valid;
  let expected;
  if (key === "enabled") {
    valid = typeof value === "boolean";
    expected = "true or false";
  } else if (key === "url") {
    valid = typeof value === "string" && value.trim() !== "";
    expected = "a non-empty string";
  } else {
    valid = typeof value === "string";
    expected = "a string";
  }
  return valid ? [] : [`${name} must be ${expected}, got ${JSON.stringify(value)}`];
}

function checkRetrieval(given, accepted, errors) {
  for (const [key, value] of Object.entries(given)) {
    if (!Object.hasOwn(RETRIEVAL_NAMES, key)) {
      errors.push(`${PREFIX}: unknown key "${RETRIEVAL_SECTION}.${key}"`);
      continue;
    }
    const messages = fieldErrors(key, value);
    errors.push(...messages.map((message) => `${PREFIX}: ${message}`));
    if (!messages.length) accepted[RETRIEVAL_NAMES[key]] = value;
  }
}

function parse(text) {
  const errors = [];
  const given = loadObject(text, errors);
  const accepted = {};
  for (const [section, value] of Object.entries(given)) {
    if (!SECTIONS.includes(section)) errors.push(`${PREFIX}: unknown key "${section}"`);
    else if (!isPlainObject(value)) errors.push(`${PREFIX}: ${section} must be an object`);
    else if (section === RETRIEVAL_SECTION) checkRetrieval(value, accepted, errors);
  }
  return { accepted, errors };
}

function validate(text) {
  return parse(text).errors;
}

function legacyValue(name, legacyRow) {
  const column = LEGACY_COLUMN_OVERRIDES[name] ?? name;
  const value = legacyRow[column];
  if (!isSet(value)) return null;
  return name === CORPORA_NAME ? JSON.parse(value) : value;
}

function resolve(text, legacyRow) {
  const { accepted, errors } = parse(text);
  if (errors.length) throw new Error(errors.join("; "));
  const resolved = {};
  for (const name of Object.keys(DEFAULTS)) {
    resolved[name] = Object.hasOwn(accepted, name) ? accepted[name] : legacyValue(name, legacyRow);
  }
  return resolved;
}

function flatten(text, legacyRow) {
  const resolved = resolve(text, legacyRow);
  const flat = {};
  for (const [name, value] of Object.entries(resolved)) {
    flat[name] = value === null ? structuredClone(DEFAULTS[name]) : value;
  }
  return flat;
}

function resolveStateKey(missionId, stateNamespace) {
  return stateNamespace || missionId;
}
// === end pure overrides core ===

// === export (browser global + CommonJS module.exports) ===
const BoardOverrides = {
  DEFAULTS,
  validate,
  resolve,
  flatten,
  resolveStateKey,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardOverrides;
} else if (typeof document !== "undefined") {
  globalThis.BoardOverrides = BoardOverrides;
}
})();
