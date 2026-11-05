(function () {
// === pure thinking core (no DOM, no fetch, no globals) ===
const DEFAULTS = {
  worker_think_policy: "",
  worker_think_policy_from_task: 0,
  worker_think_off_after_seconds: -1,
  worker_think_off_after_steps: -1,
};
const JSON_KEYS = {
  from_task: "worker_think_policy_from_task",
  off_after_seconds: "worker_think_off_after_seconds",
  off_after_steps: "worker_think_off_after_steps",
};
const JSON_MINIMUMS = { from_task: 1, off_after_seconds: 0, off_after_steps: 0 };

function jsonType(value) {
  if (value === null) return "null";
  if (typeof value === "boolean") return "boolean";
  if (Array.isArray(value)) return "array";
  if (typeof value === "object") return "object";
  if (typeof value === "string") return "string";
  return "number";
}

function checkKey(key, value, accepted, errors) {
  if (!Object.hasOwn(JSON_KEYS, key)) {
    errors.push(`thinking_json: unknown key "${key}"`);
  } else if (Number.isInteger(value) && value >= JSON_MINIMUMS[key]) {
    accepted[JSON_KEYS[key]] = value;
  } else {
    errors.push(
      `thinking_json: ${key} must be an integer >= ${JSON_MINIMUMS[key]}, ` +
        `got ${JSON.stringify(value)}`,
    );
  }
}

function parseThinking(thinkingJson, errors) {
  const source = String(thinkingJson ?? "").trim();
  if (!source) return {};
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch {
    errors.push("thinking_json: not valid JSON");
    return {};
  }
  if (jsonType(parsed) !== "object") {
    errors.push(`thinking_json: must be a JSON object, got ${jsonType(parsed)}`);
    return {};
  }
  const accepted = {};
  for (const [key, value] of Object.entries(parsed)) checkKey(key, value, accepted, errors);
  return accepted;
}

function validate(thinkingJson) {
  const errors = [];
  parseThinking(thinkingJson, errors);
  return errors;
}

function isSet(value) {
  return value !== undefined && value !== null && value !== "";
}

function flatten(thinkingJson, legacy) {
  const errors = [];
  const given = parseThinking(thinkingJson, errors);
  if (errors.length) throw new Error(errors.join("; "));
  const flat = {};
  for (const [name, fallback] of Object.entries(DEFAULTS)) {
    if (name in given) flat[name] = given[name];
    else if (isSet(legacy[name])) flat[name] = legacy[name];
    else flat[name] = fallback;
  }
  return flat;
}
// === end pure thinking core ===

// === export (browser global + CommonJS module.exports) ===
const BoardThinking = {
  DEFAULTS,
  validate,
  flatten,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardThinking;
} else if (typeof document !== "undefined") {
  globalThis.BoardThinking = BoardThinking;
}
})();
