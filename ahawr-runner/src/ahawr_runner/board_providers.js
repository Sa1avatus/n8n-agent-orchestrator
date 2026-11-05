/* board-scope-wrapped */
(function () {
"use strict";

const API_TYPES = [
  {
    id: "anthropic",
    label: "Anthropic Messages",
    endpoint_path: "/v1/messages",
    auth_style: "x-api-key",
    needs_translator: false,
    litellm_prefix: "anthropic/",
    extra_fields: ["anthropic-version"],
    capabilities: {
      tools: null,
      parallel_tools: null,
      streaming: null,
      system_message_position: "top-level system",
      prompt_caching: null,
      reasoning_or_thinking: null,
      max_tokens_field: "max_tokens",
      usage_in_stream: null,
      known_quirks: "anthropic-version header; fetched summary called it optional",
    },
    verified: true,
  },
  {
    id: "openai-chat",
    label: "OpenAI Chat Completions",
    endpoint_path: "/chat/completions",
    auth_style: "bearer",
    needs_translator: true,
    litellm_prefix: "openai/",
    extra_fields: [],
    capabilities: {
      tools: null,
      parallel_tools: null,
      streaming: null,
      system_message_position: null,
      prompt_caching: null,
      reasoning_or_thinking: null,
      max_tokens_field: "max_tokens",
      usage_in_stream: null,
      known_quirks: "many local servers implement only part of the spec; " +
        "LiteLLM drops unsupported fields",
    },
    verified: true,
  },
  {
    id: "openai-responses",
    label: "OpenAI Responses",
    endpoint_path: "/responses",
    auth_style: "bearer",
    needs_translator: true,
    litellm_prefix: "openai/",
    extra_fields: [],
    capabilities: {
      tools: null,
      parallel_tools: null,
      streaming: null,
      system_message_position: null,
      prompt_caching: null,
      reasoning_or_thinking: null,
      max_tokens_field: null,
      usage_in_stream: null,
      known_quirks: "off by default",
    },
    verified: false,
  },
];

const CAPABILITY_KEYS = [
  "tools",
  "parallel_tools",
  "streaming",
  "system_message_position",
  "prompt_caching",
  "reasoning_or_thinking",
  "max_tokens_field",
  "usage_in_stream",
  "known_quirks",
];
const NO_TOOLS_WARNING = "no_tools";
const PROVIDER_NAME_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const PROVIDER_NON_NULL = ["enabled", "api_type", "base_url"];
const PROVIDER_REQUIRED = ["api_type", "base_url"];
const BASE_URL_MAX_CHARS = 2048;

function getApiType(apiType) {
  const row = API_TYPES.find((candidate) => candidate.id === apiType);
  if (!row) throw new Error(`unknown api_type: ${apiType}`);
  return row;
}

function fieldsForType(apiType) {
  return [...getApiType(apiType).extra_fields];
}

function routeNote(apiType) {
  return getApiType(apiType).needs_translator ? "litellm" : "direct";
}

function effectiveType(provider, model) {
  const models = provider.models || {};
  const override = Object.hasOwn(models, model) ? models[model] : {};
  const apiType = Object.hasOwn(override, "api_type") ? override.api_type : provider.api_type;
  return getApiType(apiType).id;
}

function effectiveCapabilities(apiType, modelOverrides) {
  const capabilities = { ...getApiType(apiType).capabilities };
  for (const [key, value] of Object.entries(modelOverrides || {})) {
    if (!CAPABILITY_KEYS.includes(key)) throw new Error(`unknown capability: ${key}`);
    capabilities[key] = value;
  }
  return capabilities;
}

function workerWarning(capabilities) {
  return capabilities.tools === false ? NO_TOOLS_WARNING : null;
}

function textCheck(maxChars) {
  return (key, value) => (typeof value === "string" && Array.from(value).length <= maxChars
    ? null
    : `${key} must be text of at most ${maxChars} characters`);
}

function numberCheck(low, high, whole) {
  return (key, value) => {
    const kind = whole ? "a whole number" : "a number";
    const isNumber = typeof value === "number" && Number.isFinite(value);
    if (!isNumber || (whole && !Number.isInteger(value))) return `${key} must be ${kind}`;
    if (value < low || value > high) return `${key} must be between ${low} and ${high}`;
    return null;
  };
}

function flagCheck(key, value) {
  return typeof value === "boolean" ? null : `${key} must be true or false`;
}

function apiTypeCheck(key, value) {
  if (typeof value !== "string") return `${key} must be text`;
  return API_TYPES.some((row) => row.id === value) ? null : `unknown api_type: ${value}`;
}

function isHttpUrlWithHost(value) {
  const match = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)/.exec(value);
  if (!match || !["http", "https"].includes(match[1].toLowerCase()) || match[2].includes("@")) {
    return false;
  }
  try {
    return new URL(value).hostname !== "";
  } catch {
    return false;
  }
}

function baseUrlCheck(key, value) {
  if (typeof value !== "string" || value.length > BASE_URL_MAX_CHARS) {
    return `${key} must be text of at most ${BASE_URL_MAX_CHARS} characters`;
  }
  return isHttpUrlWithHost(value)
    ? null
    : `${key} must be an http or https URL with a host and no userinfo`;
}

const PROVIDER_CHECKS = {
  label: textCheck(100),
  api_type: apiTypeCheck,
  base_url: baseUrlCheck,
  enabled: flagCheck,
  context_tokens: numberCheck(1, 10000000, true),
  compact_pct: numberCheck(1, 100, false),
  compact_min_pct: numberCheck(1, 100, false),
  max_output_tokens: numberCheck(1, 1000000, true),
  tools: textCheck(500),
  search_first: flagCheck,
  retry_fresh_session: flagCheck,
  notes: textCheck(2000),
};

function checkFields(fields, checks, nonNull, required = []) {
  const unknown = Object.keys(fields).filter((key) => !Object.hasOwn(checks, key)).sort();
  if (unknown.length) return `unknown field(s): ${unknown.join(", ")}`;
  const missing = required.filter((key) => !Object.hasOwn(fields, key)).sort();
  if (missing.length) return `missing field(s): ${missing.join(", ")}`;
  for (const [key, value] of Object.entries(fields)) {
    if (value === null && nonNull.includes(key)) return `${key} must not be null`;
    if (value !== null) {
      const error = checks[key](key, value);
      if (error) return error;
    }
  }
  return null;
}

function validateProviderForm(fields, { isNew, name }) {
  if (isNew && !(typeof name === "string" && PROVIDER_NAME_RE.test(name))) {
    return `name must match ${PROVIDER_NAME_RE.source}`;
  }
  if (!isNew && Object.keys(fields).length === 0) return "no fields to update";
  return checkFields(fields, PROVIDER_CHECKS, PROVIDER_NON_NULL, isNew ? PROVIDER_REQUIRED : []);
}

function buildSavePayload(fields, { isNew, name, expectedVersion }) {
  return isNew ? { name, ...fields } : { ...fields, expected_version: expectedVersion };
}

const BoardProviders = {
  API_TYPES,
  CAPABILITY_KEYS,
  getApiType,
  fieldsForType,
  routeNote,
  effectiveType,
  effectiveCapabilities,
  workerWarning,
  validateProviderForm,
  buildSavePayload,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardProviders;
} else {
  globalThis.BoardProviders = BoardProviders;
}
})();
