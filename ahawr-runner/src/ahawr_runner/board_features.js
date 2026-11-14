(function () {
// === pure features core (no DOM, no fetch, no globals) ===
// The defaults and the minimums of every feature come from the answer of the
// features route (feature_defaults, param_minimums), handed in by the panel after
// its own GET; this file holds no copy of them and no default literal.
let featureDefaults = null;
let paramMinimums = null;

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// Stores the defaults from the route answer. A missing field is returned as error
// text for the panel's existing error display; nothing is filled in with a literal.
function setFeatureDefaults(answer) {
  const source = answer ?? {};
  const errors = [];
  if (!isPlainObject(source.feature_defaults)) {
    errors.push('the features route answer has no "feature_defaults" object');
  }
  if (!isPlainObject(source.param_minimums)) {
    errors.push('the features route answer has no "param_minimums" object');
  }
  featureDefaults = errors.length ? null : source.feature_defaults;
  paramMinimums = errors.length ? null : source.param_minimums;
  return errors;
}

function checkParameter(column, feature, param, value, accepted, errors) {
  const where = `${column}: ${feature}.${param}`;
  if (param === "enabled") {
    if (typeof value === "boolean") accepted[param] = value;
    else errors.push(`${where} must be true or false, got ${JSON.stringify(value)}`);
    return;
  }
  if (Number.isInteger(value) && value >= paramMinimums[param]) accepted[param] = value;
  else {
    errors.push(
      `${where} must be an integer >= ${paramMinimums[param]}, got ${JSON.stringify(value)}`,
    );
  }
}

function checkFeature(column, feature, body, errors) {
  const accepted = {};
  for (const [param, value] of Object.entries(body)) {
    if (!Object.hasOwn(featureDefaults[feature], param)) {
      errors.push(`${column}: unknown parameter "${param}" in feature "${feature}"`);
    } else checkParameter(column, feature, param, value, accepted, errors);
  }
  return accepted;
}

function parseFeatureText(text, column, errors) {
  const source = String(text ?? "").trim();
  if (!source) return {};
  let parsed;
  try {
    parsed = JSON.parse(source);
  } catch (error) {
    errors.push(`${column}: not valid JSON (${error.message})`);
    return {};
  }
  if (!isPlainObject(parsed)) {
    errors.push(`${column}: must be a JSON object`);
    return {};
  }
  const sparse = {};
  for (const [feature, body] of Object.entries(parsed)) {
    if (!Object.hasOwn(featureDefaults, feature)) {
      errors.push(`${column}: unknown feature "${feature}"`);
    } else if (!isPlainObject(body)) {
      errors.push(`${column}: feature "${feature}" must be an object`);
    } else sparse[feature] = checkFeature(column, feature, body, errors);
  }
  return sparse;
}

// Every feature in the route defaults is resolved: the default, then the global
// value, then the mission override, parameter by parameter.
function resolveFeatures(global, mission) {
  const features = {};
  for (const feature of Object.keys(featureDefaults)) {
    features[feature] = { ...featureDefaults[feature], ...global[feature], ...mission[feature] };
  }
  return features;
}

function checkLimits(features, errors) {
  const planNoTruncate = features.plan_no_truncate;
  if (!isPlainObject(planNoTruncate)) {
    errors.push(
      "the feature defaults from the features route answer have no \"plan_no_truncate\" object",
    );
    return;
  }
  const { max_tasks: maxTasks, max_tasks_hard: maxTasksHard } = planNoTruncate;
  if (maxTasksHard < maxTasks) {
    errors.push(
      `plan_no_truncate.max_tasks_hard must be at least plan_no_truncate.max_tasks ` +
        `(${maxTasks}), got ${maxTasksHard}`,
    );
  }
}

function parseFeatures(text, mission_text) {
  const errors = [];
  if (!isPlainObject(featureDefaults) || !isPlainObject(paramMinimums)) {
    errors.push("no feature defaults were received from the features route answer");
    return { features: null, errors };
  }
  const global = parseFeatureText(text, "features_json", errors);
  const mission = parseFeatureText(mission_text, "mission_features_json", errors);
  const features = resolveFeatures(global, mission);
  checkLimits(features, errors);
  return errors.length ? { features: null, errors } : { features, errors };
}

// shortcut: flatten needs the limit features in the route defaults; upgrade if they are absent.
function flatten(features) {
  const planNoTruncate = features.plan_no_truncate;
  const adaptiveSplit = features.adaptive_split;
  return {
    plan_no_truncate: planNoTruncate.enabled ? 1 : 0,
    max_tasks: planNoTruncate.max_tasks,
    max_tasks_hard: planNoTruncate.max_tasks_hard,
    adaptive_split: adaptiveSplit.enabled ? 1 : 0,
    split_steps: adaptiveSplit.split_steps,
    max_splits: adaptiveSplit.max_splits,
    max_total_tasks: adaptiveSplit.max_total_tasks,
    run_hygiene: features.run_hygiene.enabled ? 1 : 0,
  };
}
// === end pure features core ===

// === export (browser global + CommonJS module.exports) ===
const BoardFeatures = {
  setFeatureDefaults,
  parseFeatures,
  flatten,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardFeatures;
} else if (typeof document !== "undefined") {
  globalThis.BoardFeatures = BoardFeatures;
}
})();
