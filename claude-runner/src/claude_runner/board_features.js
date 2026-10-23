(function () {
// === pure features core (no DOM, no fetch, no globals) ===
const FEATURE_DEFAULTS = {
  plan_no_truncate: { enabled: false, max_tasks: 15, max_tasks_hard: 30 },
  adaptive_split: { enabled: false, split_steps: 150, max_splits: 6, max_total_tasks: 40 },
  run_hygiene: { enabled: false },
};
const PARAM_MINIMUMS = {
  max_tasks: 1,
  max_tasks_hard: 1,
  split_steps: 1,
  max_splits: 0,
  max_total_tasks: 1,
};

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function checkParameter(column, feature, param, value, accepted, errors) {
  const where = `${column}: ${feature}.${param}`;
  if (param === "enabled") {
    if (typeof value === "boolean") accepted[param] = value;
    else errors.push(`${where} must be true or false, got ${JSON.stringify(value)}`);
    return;
  }
  if (Number.isInteger(value) && value >= PARAM_MINIMUMS[param]) accepted[param] = value;
  else {
    errors.push(
      `${where} must be an integer >= ${PARAM_MINIMUMS[param]}, got ${JSON.stringify(value)}`,
    );
  }
}

function checkFeature(column, feature, body, errors) {
  const accepted = {};
  for (const [param, value] of Object.entries(body)) {
    if (!Object.hasOwn(FEATURE_DEFAULTS[feature], param)) {
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
    if (!Object.hasOwn(FEATURE_DEFAULTS, feature)) {
      errors.push(`${column}: unknown feature "${feature}"`);
    } else if (!isPlainObject(body)) {
      errors.push(`${column}: feature "${feature}" must be an object`);
    } else sparse[feature] = checkFeature(column, feature, body, errors);
  }
  return sparse;
}

function resolveFeatures(global, mission) {
  const features = {};
  for (const feature of Object.keys(FEATURE_DEFAULTS)) {
    features[feature] = { ...FEATURE_DEFAULTS[feature], ...global[feature], ...mission[feature] };
  }
  return features;
}

function checkLimits(features, errors) {
  const { max_tasks: maxTasks, max_tasks_hard: maxTasksHard } = features.plan_no_truncate;
  if (maxTasksHard < maxTasks) {
    errors.push(
      `plan_no_truncate.max_tasks_hard must be at least plan_no_truncate.max_tasks ` +
        `(${maxTasks}), got ${maxTasksHard}`,
    );
  }
}

function parseFeatures(text, mission_text) {
  const errors = [];
  const global = parseFeatureText(text, "features_json", errors);
  const mission = parseFeatureText(mission_text, "mission_features_json", errors);
  const features = resolveFeatures(global, mission);
  checkLimits(features, errors);
  return errors.length ? { features: null, errors } : { features, errors };
}

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
  FEATURE_DEFAULTS,
  parseFeatures,
  flatten,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardFeatures;
} else if (typeof document !== "undefined") {
  globalThis.BoardFeatures = BoardFeatures;
}
})();
