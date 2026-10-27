/* board-scope-wrapped */
(function () {
// === pure roles core (no DOM, no fetch, no globals) ===
const MODEL_SEPARATOR = "/";
const OPTIONS_KEY = "options";
const FIRST_ATTEMPT_KEY = "1";
const MAX_ROLE_KEYS = 10;
const MAX_DISTINCT_MODELS = 8;
const KNOWN_ROLES = ["architect", "worker", "reviewer", "service", "qa", "report"];
const COMMON_OPTION_KEYS = ["fresh", "think", "max_polls", "poll_seconds", "max_retries"];
const QA_OPTION_KEYS = [...COMMON_OPTION_KEYS, "mode", "max_minutes", "max_cost_usd", "layers"];
// Mirrors QA_LAYERS and QA_DEFAULT_LAYERS in roles_config.py.
const QA_LAYERS = ["regression", "api", "ui"];
const QA_DEFAULT_LAYERS = ["ui"];
const ATTEMPT_KEY_PATTERN = /^[1-9][0-9]*$/;
const ESCALATE_SUFFIX = "_escalate";
const ESCALATE_KEYS = ["model", "above_steps", "below_score", "from_attempt"];
const MAX_SCORE = 100;
const FRESH_KEY = "fresh";
const FRESH_AUTO = "auto";
const LEGACY_OPTION_KEYS = ["think", "max_polls", "poll_seconds", "max_retries"];
const FALLBACK_ROLES = new Map([
  ["qa", ["qa", "reviewer"]],
  ["report", ["report", "worker"]],
]);
const REPR_ESCAPES = { "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t" };
const STRING_TOKEN = /"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"/y;
const SCALAR_TOKEN = /true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;

class DuplicateKeyError extends Error {
  constructor(key) {
    super(key);
    this.key = key;
  }
}

function pyRepr(text) {
  const body = String(text).replace(
    /[\\\x00-\x1f\x7f]/g,
    (ch) => REPR_ESCAPES[ch] ?? "\\x" + ch.charCodeAt(0).toString(16).padStart(2, "0"),
  );
  if (body.includes("'") && !body.includes('"')) return `"${body}"`;
  return `'${body.replace(/'/g, "\\'")}'`;
}

function report(errors, warnings = []) {
  return { errors, warnings };
}

function isDict(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// shortcut: plain objects lose integer key order, upgrade when callers pass parsed text only
function asMap(value) {
  return value instanceof Map ? value : new Map(Object.entries(value));
}

function parseJsonText(text) {
  if (typeof text !== "string") throw new SyntaxError("expected text");
  let pos = 0;
  const fail = (what) => {
    throw new SyntaxError(`${what} at char ${pos}`);
  };
  const skipSpace = () => {
    while (pos < text.length && " \t\n\r".includes(text[pos])) pos += 1;
  };
  const peek = () => {
    skipSpace();
    return text[pos];
  };
  const scan = (pattern) => {
    pattern.lastIndex = pos;
    const match = pattern.exec(text);
    if (match) pos += match[0].length;
    return match;
  };
  const parseString = () => {
    const match = scan(STRING_TOKEN);
    if (!match) fail("Invalid string");
    return JSON.parse(match[0]);
  };
  const parseScalar = () => {
    const match = scan(SCALAR_TOKEN);
    if (!match) fail("Expecting value");
    if (match[0] === "true") return true;
    if (match[0] === "false") return false;
    if (match[0] === "null") return null;
    return Number(match[0]);
  };
  const parseArray = () => {
    pos += 1;
    const items = [];
    if (peek() === "]") {
      pos += 1;
      return items;
    }
    let ch;
    do {
      items.push(parseValue());
      ch = peek();
      if (ch !== "," && ch !== "]") fail("Expecting ',' or ']'");
      pos += 1;
    } while (ch === ",");
    return items;
  };
  const parseObject = () => {
    pos += 1;
    const pairs = [];
    if (peek() === "}") {
      pos += 1;
      return new Map();
    }
    let ch;
    do {
      if (peek() !== '"') fail("Expecting property name");
      const key = parseString();
      if (peek() !== ":") fail("Expecting ':'");
      pos += 1;
      pairs.push([key, parseValue()]);
      ch = peek();
      if (ch !== "," && ch !== "}") fail("Expecting ',' or '}'");
      pos += 1;
    } while (ch === ",");
    const entries = new Map();
    for (const [key, value] of pairs) {
      if (entries.has(key)) throw new DuplicateKeyError(key);
      entries.set(key, value);
    }
    return entries;
  };
  const parseValue = () => {
    const ch = peek();
    if (ch === "{") return parseObject();
    if (ch === "[") return parseArray();
    if (ch === '"') return parseString();
    return parseScalar();
  };
  const value = parseValue();
  skipSpace();
  if (pos !== text.length) fail("Extra data");
  return value;
}

function validateRolesJson(text, providers) {
  let parsed;
  try {
    parsed = parseJsonText(text);
  } catch (err) {
    if (err instanceof DuplicateKeyError) {
      return report([`roles_json has a duplicate key ${pyRepr(err.key)}`]);
    }
    if (err instanceof SyntaxError) return report([`roles_json is not valid JSON: ${err.message}`]);
    throw err;
  }
  return validateRoles(parsed, providers);
}

function splitModel(model) {
  const at = model.indexOf(MODEL_SEPARATOR);
  return at < 0 ? [model, ""] : [model.slice(0, at), model.slice(at + 1)];
}

function validateRoles(roles, providers) {
  if (!isDict(roles)) return report(["roles_json must be a JSON object"]);
  const errors = [];
  const warnings = [];
  const models = new Set();
  for (const [role, value] of asMap(roles)) {
    if (role.endsWith(ESCALATE_SUFFIX) && KNOWN_ROLES.includes(role.slice(0, -ESCALATE_SUFFIX.length))) {
      errors.push(...checkEscalate(role, value, providers));
      if (isDict(value) && typeof asMap(value).get("model") === "string") models.add(asMap(value).get("model"));
      continue;
    }
    if (!KNOWN_ROLES.includes(role)) warnings.push(`role ${pyRepr(role)} is not a known role`);
    errors.push(...checkRole(role, value, providers));
    roleModels(value).forEach((model) => models.add(model));
  }
  if (models.size > MAX_DISTINCT_MODELS) {
    warnings.push(
      `roles_json uses ${models.size} distinct models, more than ${MAX_DISTINCT_MODELS}`,
    );
  }
  return report(errors, warnings);
}

function checkEscalate(role, value, providers) {
  const name = pyRepr(role);
  if (!isDict(value)) return [`role ${name}: value must be an object with model and above_steps`];
  const rule = asMap(value);
  const errors = [...rule.keys()]
    .filter((key) => !ESCALATE_KEYS.includes(key))
    .map((key) => `role ${name}: unknown key ${pyRepr(key)}`);
  errors.push(...checkModel(role, "model", rule.get("model"), providers));
  const steps = rule.get("above_steps");
  if (!Number.isInteger(steps) || steps < 1) {
    errors.push(`role ${name}: 'above_steps' must be a whole number of at least 1`);
  }
  const below = rule.has("below_score") ? rule.get("below_score") : 1;
  if (typeof below !== "number" || !(below > 0 && below <= MAX_SCORE)) {
    errors.push(`role ${name}: 'below_score' must be above 0 and up to ${MAX_SCORE}`);
  }
  const first = rule.has("from_attempt") ? rule.get("from_attempt") : 2;
  if (!Number.isInteger(first) || first < 2) {
    errors.push(`role ${name}: 'from_attempt' must be a whole number of at least 2`);
  }
  return errors;
}

function checkRole(role, value, providers) {
  const name = pyRepr(role);
  if (typeof value === "string") return checkModel(role, "model", value, providers);
  if (!isDict(value)) {
    return [`role ${name}: value must be a model string or an object of attempt keys`];
  }
  const attempts = asMap(value);
  const errors = [];
  if (attempts.size > MAX_ROLE_KEYS) {
    errors.push(`role ${name}: ${attempts.size} keys, at most ${MAX_ROLE_KEYS} allowed`);
  }
  if (attempts.has(OPTIONS_KEY)) errors.push(...checkOptions(role, attempts.get(OPTIONS_KEY)));
  errors.push(...checkAttempts(role, attempts, providers));
  return errors;
}

function checkAttempts(role, attempts, providers) {
  const name = pyRepr(role);
  const attemptKeys = [...attempts.keys()].filter((key) => key !== OPTIONS_KEY);
  const errors = [];
  if (attemptKeys.length > 0 && attemptKeys[0] !== FIRST_ATTEMPT_KEY) {
    errors.push(
      `role ${name}: first attempt key must be ${pyRepr(FIRST_ATTEMPT_KEY)}, ` +
        `found ${pyRepr(attemptKeys[0])}`,
    );
  }
  let previous = 0;
  for (const key of attemptKeys) {
    if (!ATTEMPT_KEY_PATTERN.test(key)) {
      errors.push(`role ${name}: attempt key ${pyRepr(key)} must be a positive integer`);
    } else if (Number(key) <= previous) {
      errors.push(`role ${name}: attempt key ${pyRepr(key)} is not greater than ${previous}`);
    } else {
      previous = Number(key);
    }
    errors.push(...checkModel(role, `attempt ${pyRepr(key)}`, attempts.get(key), providers));
  }
  return errors;
}

function checkModel(role, element, model, providers) {
  const prefix = `role ${pyRepr(role)}, ${element}`;
  if (typeof model !== "string") return [`${prefix}: model must be a string 'provider/model'`];
  const [provider, name] = splitModel(model);
  if (!name) return [`${prefix}: model ${pyRepr(model)} has no model name after the provider`];
  if (!providers.includes(provider)) return [`${prefix}: unknown provider ${pyRepr(provider)}`];
  return [];
}

function checkLayers(name, layers) {
  if (!Array.isArray(layers)) return [`role ${name}: option 'layers' must be a list of layer names`];
  const errors = [];
  const seen = new Set();
  for (const layer of layers) {
    if (typeof layer !== "string" || !QA_LAYERS.includes(layer)) {
      errors.push(
        `role ${name}: option 'layers' has unknown layer ${pyRepr(layer)}, use one of ${QA_LAYERS.join(", ")}`,
      );
    } else if (seen.has(layer)) {
      errors.push(`role ${name}: option 'layers' repeats layer ${pyRepr(layer)}`);
    } else {
      seen.add(layer);
    }
  }
  return errors;
}

function checkOptions(role, options) {
  const name = pyRepr(role);
  if (!isDict(options)) return [`role ${name}: ${pyRepr(OPTIONS_KEY)} must be an object`];
  const allowed = role === "qa" ? QA_OPTION_KEYS : COMMON_OPTION_KEYS;
  const errors = [...asMap(options).keys()]
    .filter((key) => !allowed.includes(key))
    .map((key) => `role ${name}: unknown option ${pyRepr(key)}`);
  if (role === "qa" && asMap(options).has("layers")) {
    errors.push(...checkLayers(name, asMap(options).get("layers")));
  }
  return errors;
}

function roleModels(value) {
  if (typeof value === "string") return [value];
  if (!isDict(value)) return [];
  return [...asMap(value)]
    .filter(([key, model]) => key !== OPTIONS_KEY && typeof model === "string")
    .map(([, model]) => model);
}

function mergeRoles(globalRoles, missionRoles) {
  return new Map([...asMap(globalRoles ?? {}), ...asMap(missionRoles ?? {})]);
}

function legacyRole(columns, role) {
  const provider = columns[`${role}_provider`];
  const model = columns[`${role}_model`];
  const options = {};
  for (const key of LEGACY_OPTION_KEYS) {
    const option = columns[`${role}_${key}`];
    if (option != null) options[key] = option;
  }
  const value = new Map([[OPTIONS_KEY, options]]);
  if (provider && model) value.set(FIRST_ATTEMPT_KEY, `${provider}${MODEL_SEPARATOR}${model}`);
  return value;
}

function optionsOf(value) {
  const options = isDict(value) ? asMap(value).get(OPTIONS_KEY) : undefined;
  return isDict(options) ? Object.fromEntries(asMap(options)) : {};
}

function modelForAttempt(value, attempt) {
  if (typeof value === "string") return value;
  if (!isDict(value)) return null;
  const attempts = asMap(value);
  const keys = [...attempts.keys()].filter(
    (key) => key !== OPTIONS_KEY && ATTEMPT_KEY_PATTERN.test(key) && Number(key) <= attempt,
  );
  if (keys.length === 0) return null;
  const latest = keys.reduce((best, key) => (Number(key) > Number(best) ? key : best));
  const model = attempts.get(latest);
  return typeof model === "string" ? model : null;
}

function pickModel(roles, role, attempt, legacy) {
  const chain = FALLBACK_ROLES.get(role) ?? [role];
  const roleMap = asMap(roles);
  const legacyValues = new Map(chain.map((name) => [name, legacyRole(legacy, name)]));
  let legacyOptions = {};
  for (const name of chain) {
    legacyOptions = { ...optionsOf(legacyValues.get(name)), ...legacyOptions };
  }
  for (const name of chain) {
    const model = modelForAttempt(roleMap.get(name), attempt);
    if (model !== null) {
      return { model, options: { ...legacyOptions, ...optionsOf(roleMap.get(name)) } };
    }
  }
  for (const name of chain) {
    const model = modelForAttempt(legacyValues.get(name), attempt);
    if (model !== null) return { model, options: legacyOptions };
  }
  return null;
}

function pickOrThrow(roles, role, attempt, legacy) {
  const picked = pickModel(roles, role, attempt, legacy);
  if (picked === null) {
    throw new Error(`no model configured for role ${pyRepr(role)} on attempt ${attempt}`);
  }
  return picked;
}

// The escalation model when the task used more than above_steps steps and either it is a split
// part (the split came from that many steps) or the last attempt scored too low; the score rule
// applies only from attempt from_attempt (default 2) on.
function escalatedModel(block, facts, attempt) {
  if (!isDict(block) || !facts) return null;
  const rule = asMap(block);
  const limit = rule.get("above_steps");
  if (facts.split_steps != null && facts.split_steps > limit) return rule.get("model");
  if (attempt < (rule.get("from_attempt") ?? 2) || facts.steps == null || facts.steps <= limit) return null;
  const below = rule.get("below_score");
  return below == null || (facts.score != null && facts.score < below) ? rule.get("model") : null;
}

function chosenModel(roles, role, attempt, legacy, facts) {
  const picked = pickOrThrow(roles, role, attempt, legacy);
  const escalated = escalatedModel(asMap(roles).get(role + ESCALATE_SUFFIX), facts, attempt);
  return escalated === null ? picked : { ...picked, model: escalated };
}

// facts: score, steps and split_steps of the task, read only by the <role>_escalate block.
function resolveRole(roles, role, attempt, legacy, facts = null) {
  const picked = chosenModel(roles, role, attempt, legacy, facts);
  const options = { [FRESH_KEY]: FRESH_AUTO, ...picked.options };
  const [provider, name] = splitModel(picked.model);
  const freshOption = options[FRESH_KEY];
  let fresh;
  if (freshOption === FRESH_AUTO) {
    fresh =
      attempt > 1 &&
      // the previous attempt ran escalated only when the whole task was (a split part)
      chosenModel(roles, role, attempt - 1, legacy, { split_steps: facts?.split_steps }).model !== picked.model;
  } else if (typeof freshOption === "boolean") {
    fresh = freshOption;
  } else {
    throw new Error(
      `role ${pyRepr(role)}: ${pyRepr(FRESH_KEY)} must be ${pyRepr(FRESH_AUTO)}, true or false`,
    );
  }
  return { provider, model: name, think: options.think ?? null, fresh, options };
}

function effectiveModels(roles, role, maxAttempts) {
  return Array.from({ length: maxAttempts }, (_, index) => {
    const picked = pickModel(roles, role, index + 1, {});
    return picked === null ? null : picked.model;
  });
}

function previewChain(roles, role) {
  const value = asMap(roles).get(role);
  if (typeof value === "string") return `1+ ${splitModel(value)[1]}`;
  if (!isDict(value)) return "";
  const attempts = asMap(value);
  const keys = [...attempts.keys()].filter((key) => key !== OPTIONS_KEY);
  return keys
    .map((key, index) => {
      const open = index === keys.length - 1 ? "+" : "";
      const model = attempts.get(key);
      const name = typeof model === "string" ? splitModel(model)[1] : "";
      return `${key}${open} ${name}`;
    })
    .join(", ");
}
const QA_SETTABLE_OPTIONS = ["mode", "max_minutes", "max_cost_usd", "smoke"];

function qaOptionsFrom(values) {
  const options = {};
  for (const key of QA_SETTABLE_OPTIONS) {
    const raw = values[key];
    if (raw === undefined || raw === null || raw === "") continue;
    if (key === "mode") {
      options[key] = String(raw);
      continue;
    }
    if (key === "smoke") {
      if (raw === true || raw === "true") options[key] = true;
      continue;
    }
    const number = Number(raw);
    if (!Number.isFinite(number)) throw new Error(`${key} must be a number`);
    options[key] = number;
  }
  return options;
}

// Mirrors QA_DEFAULT_OPTIONS in roles_config.py: the placeholders of the QA controls.
const QA_DEFAULT_OPTIONS = { mode: "off", max_minutes: 20, max_cost_usd: 1 };
const QA_MODES = ["off", "final", "ui_tasks", "nightly"];

function qaLayers(options) {
  const layers = asMap(options).get("layers");
  return Array.isArray(layers) && layers.length > 0 ? [...layers] : [...QA_DEFAULT_LAYERS];
}

function qaPlaceholders() {
  return Object.fromEntries(Object.entries(QA_DEFAULT_OPTIONS).map(([k, v]) => [k, String(v)]));
}

function qaEntryOf(roles) {
  const current = roles.qa;
  return typeof current === "string" ? { [FIRST_ATTEMPT_KEY]: current } : { ...(current ?? {}) };
}

// Sets or clears the QA options listed in QA_SETTABLE_OPTIONS; other options are kept.
function withQaOptions(text, options) {
  const roles = text.trim() === "" ? {} : JSON.parse(text);
  const qa = qaEntryOf(roles);
  const stored = qa[OPTIONS_KEY] ?? {};
  const touched = QA_SETTABLE_OPTIONS.some((k) => k in stored || k in options);
  if (!touched) return text;
  const next = { ...stored };
  for (const key of QA_SETTABLE_OPTIONS) delete next[key];
  Object.assign(next, options);
  if (Object.keys(next).length === 0) delete qa[OPTIONS_KEY];
  else qa[OPTIONS_KEY] = next;
  return JSON.stringify({ ...roles, qa }, null, 2);
}

function qaModelFrom(provider, model) {
  if (model === "") return "";
  if (provider === "") throw new Error("qa provider is required with a model");
  return `${provider}${MODEL_SEPARATOR}${model}`;
}

// Sets or clears the QA model (attempt 1 of the qa role).
function withQaModel(text, model) {
  const roles = text.trim() === "" ? {} : JSON.parse(text);
  const qa = qaEntryOf(roles);
  if (model === "") delete qa[FIRST_ATTEMPT_KEY];
  else qa[FIRST_ATTEMPT_KEY] = model;
  if (Object.keys(qa).length === 0) delete roles.qa;
  else roles.qa = qa;
  return JSON.stringify(roles, null, 2);
}

// The QA values of a stored roles text, as the strings the QA controls show.
function qaValuesOf(text) {
  const roles = text.trim() === "" ? {} : JSON.parse(text);
  const qa = qaEntryOf(roles);
  const model = typeof qa[FIRST_ATTEMPT_KEY] === "string" ? qa[FIRST_ATTEMPT_KEY] : "";
  const [provider, name] = model === "" ? ["", ""] : splitModel(model);
  const options = qa[OPTIONS_KEY] ?? {};
  const values = { provider, model: name, mode: "", max_minutes: "", max_cost_usd: "", smoke: "" };
  for (const key of QA_SETTABLE_OPTIONS) {
    if (options[key] !== undefined && options[key] !== null) values[key] = String(options[key]);
  }
  return values;
}

// Writes the QA model and options from the QA controls; an unchanged field keeps its stored value.
function qaRolesText(text, values) {
  const model = qaModelFrom(values.provider, values.model);
  return withQaOptions(withQaModel(text, model), qaOptionsFrom(values));
}

// Changed QA fields as { field, old, new } rows; an empty list means nothing to save.
function qaDiffRows(initial, current) {
  const rows = [];
  for (const field of ["provider", "model", ...QA_SETTABLE_OPTIONS]) {
    if (initial[field] !== current[field]) {
      rows.push({ field, old: initial[field], new: current[field] });
    }
  }
  return rows;
}
async function loadQaRoles(api, url) {
  const got = await api.fetchJson(url);
  return { text: got.roles_json, version: got.version, initial: qaValuesOf(got.roles_json) };
}

async function saveQaRoles(api, url, current, state) {
  if (qaDiffRows(state.initial, current).length === 0) return state;
  const body = { roles_json: qaRolesText(state.text, current), expected_version: state.version };
  const got = await api.postJson(url, body, "PUT");
  return { text: got.roles_json, version: got.version, initial: qaValuesOf(got.roles_json) };
}
// Suggestions for the provider and model inputs, from the rows of GET /api/board/providers.
// Empty lists (registry off or unreachable) leave the inputs as plain free text.
function providerChoices(providers) {
  return providers.filter((p) => p.enabled !== false).map((p) => p.name);
}

function modelChoices(providers, provider) {
  const row = providers.find((p) => p.name === provider);
  return row ? row.models.map((m) => m.model) : [];
}

// === end pure roles core ===

// === export (browser global + CommonJS module.exports) ===
const BoardRoles = {
  splitModel,
  validateRoles,
  validateRolesJson,
  mergeRoles,
  resolveRole,
  previewChain,
  effectiveModels,
  qaOptionsFrom,
  withQaOptions,
  QA_MODES,
  qaPlaceholders,
  qaLayers,
  qaValuesOf,
  qaRolesText,
  qaDiffRows,
  loadQaRoles,
  saveQaRoles,
  providerChoices,
  modelChoices,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardRoles;
} else if (typeof document !== "undefined") {
  globalThis.BoardRoles = BoardRoles;
}
})();
