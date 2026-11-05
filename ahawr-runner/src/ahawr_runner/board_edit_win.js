/* board-scope-wrapped */
(function () {
/* The launch settings editor as a window with steps (docs/board-mission-edit-mockup.html).
 *
 * board_mission.js renders the launch form (.confbox with one .frow-wrap row per
 * field) and board_mission_save.js reads, diffs and saves it through the
 * data-key controls.  This module does not touch that data path: it moves the
 * form rows into the pages of a window (the controls stay inside #boardmission,
 * so readForm, the dirty check and doSave work as before) and leaves a summary
 * card with an "Edit launch settings" button where the form was.
 *
 *   page 1  Mission    name, objective, rules, acceptance criteria, working folder
 *   page 2  Execution  attempts per task, roles (model + provider on one line), limits, thinking
 *   page 3  Context    hints, required files, mission overrides (overrides_json)
 *   page 4  Review     the diff (BoardMissionSave.currentDiff) and "Save changes"
 *
 * #boardmission is re-rendered after a save and on refresh; a MutationObserver
 * builds the window again on the fresh form.  While the window is open the
 * auto-refresh is paused (body.dataset.editing).
 */
"use strict";

const PAGES = [
  { id: "mission", label: "ew.s1", keys: ["name", "objective", "rules", "acceptance_criteria", "working_directory"] },
  { id: "exec", label: "ew.s2", keys: ["attempts_per_task", "enabled", "worker_model", "worker_provider", "reviewer_model",
    "reviewer_provider", "architect_model", "architect_provider", "max_tasks"] },
  { id: "ctx", label: "ew.s3", keys: ["hints", "required_files"] },
];
const ROLES = [
  ["ew.worker", "var(--r-worker, #ff8a3d)", "worker_model", "worker_provider"],
  ["ew.reviewer", "var(--r-reviewer, #b39bff)", "reviewer_model", "reviewer_provider"],
  ["ew.architect", "var(--r-architect, #ffb454)", "architect_model", "architect_provider"],
  ["ew.qa", "var(--r-qa, #ff6b8a)", "qa_model", "qa_provider"],
];
const LOCKED = ["working_directory"];
const ENGINE_KEYS = ["max_attempts_per_task", "retry_delays", "runner_url", "run_manager_workflow_id", "telegram_chat_id"];
const ENGINE_OVERRIDABLE = ENGINE_KEYS.filter((key) => key !== "telegram_chat_id");
const ENGINE_LABELS = {
  max_attempts_per_task: ["ew.eng.key.max_attempts", "Max attempts per task"],
  retry_delays: ["ew.eng.key.retry_delays", "Retry delays (seconds, JSON array)"],
  runner_url: ["ew.eng.key.runner_url", "Runner URL"],
  run_manager_workflow_id: ["ew.eng.key.run_manager_workflow_id", "Run manager workflow ID"],
  telegram_chat_id: ["ew.eng.key.telegram_chat_id", "Telegram chat ID"],
};
const QA_LIMITS = [["mode", "qa_mode"], ["max_minutes", "qa_max_minutes"],
  ["max_cost_usd", "qa_max_cost_usd"], ["smoke", "qa_smoke"]];
const QA_LABEL_KEYS = {
  provider: "ew.provider", model: "ew.model", mode: "ew.qa.mode",
  max_minutes: "ew.qa.max_minutes", max_cost_usd: "ew.qa.max_cost_usd",
  smoke: "ew.qa.smoke",
};
const QA_MODE_KEY = {
  off: "ew.qa.mode.off",
  final: "ew.qa.mode.final",
  ui_tasks: "ew.qa.mode.ui_tasks",
  nightly: "ew.qa.mode.nightly",
};
const POLL_ROLE_KEYS = { architect: "ew.architect", worker: "ew.worker", reviewer: "ew.reviewer" };
const POLL_LABEL_KEYS = {
  max_polls: "ew.poll.max_polls", poll_seconds: "ew.poll.poll_seconds", max_retries: "ew.poll.max_retries",
};
const FEATURE_ROWS = [
  ["plan_no_truncate", ["max_tasks", "max_tasks_hard"]],
  ["adaptive_split", ["split_steps", "max_splits", "max_total_tasks"]],
  ["run_hygiene", []],
];
const FEAT_LABEL_KEYS = {
  features_json: "ew.feat.global", mission_features_json: "ew.feat.mission", thinking_json: "ew.think.json",
};
const FEAT_NAME_KEY = {
  plan_no_truncate: "ew.feat.name.plan_no_truncate",
  adaptive_split: "ew.feat.name.adaptive_split",
  run_hygiene: "ew.feat.name.run_hygiene",
};
const FEAT_PARAM_KEY = {
  max_tasks: "ew.feat.p.max_tasks",
  max_tasks_hard: "ew.feat.p.max_tasks_hard",
  split_steps: "ew.feat.p.split_steps",
  max_splits: "ew.feat.p.max_splits",
  max_total_tasks: "ew.feat.p.max_total_tasks",
};
// The QA role lives in roles_json (qa), not in the form: its controls are not data-key inputs.
let qaFields = {};
let qa = null;   // { text, version, initial } from GET /api/board/missions/<id>/roles
let pollFields = {}; // polling inputs per role and key; their values are saved into the same roles_json
let pollInitial = null; // the polling values as roles_json and the legacy columns give them at load or save
// Features live in features_json (global) and mission_features_json; their controls are not data-key inputs.
let featFields = {};
let feat = null; // { state } from featuresState: the texts and versions read from the server
// The thinking_json text is the global hermes_config value; its controls are not data-key inputs.
let thinkField = null;
let think = null; // { state: { thinking_json, thinking_version } } from GET /api/board/thinking
let retField = null;
let ret = null; // { state: { retrieval_json, retrieval_version } } from GET /api/board/retrieval
let overField = null;
let overrides = null; // { state: { mission_id, overrides_json, overrides_version } } from GET /api/board/missions/<id>
let providerList = []; // rows of GET /api/board/providers; empty keeps the role inputs free text
let choiceRefreshers = [];
let engineField = {}; // override inputs by key (telegram_chat_id has none: it is read-only)
let engineView = {}; // value and source cells by key
let engineRulesBox = null; // fieldset holding the error rules table
let engineRuleRows = []; // input cells per error rule row, read when the plan is built
let engine = null; // { state: { engine_json, engine_version } } from GET /api/board/engine; null when not loaded or invalid

function tr(key, params) {
  return typeof t === "function" ? t(key, params) : key;
}

function L(key, en, params) {
  const text = tr(key, params);
  return text === key ? en : text;
}

function mk(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function save() {
  return typeof BoardMissionSave !== "undefined" ? BoardMissionSave : null;
}

function control(box, key) {
  return box.querySelector('[data-key="' + key + '"]');
}

function rowOf(box, key) {
  const c = control(box, key);
  return c ? c.closest(".frow-wrap") : null;
}

function labelOf(box, key) {
  const row = rowOf(box, key);
  const l = row && row.querySelector(".frow-label");
  return l ? l.textContent : key;
}

function show(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// --- the summary card that replaces the inline form -------------------------------------

function summaryCard(box) {
  const card = mk("div", "ew-summary");
  const cur = save() ? save().readForm(box) : {};
  // an empty field uses the default of the hermes_config profile: it is the placeholder of the control
  const eff = (key) => {
    const v = cur[key];
    if (v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length)) return show(v);
    const c = control(box, key);
    return c && c.placeholder ? c.placeholder : "—";
  };
  const items = [
    [tr("ew.sec.attempts"), eff("attempts_per_task")],
    [tr("ew.worker"), eff("worker_model") + " · " + eff("worker_provider")],
    [tr("ew.reviewer"), eff("reviewer_model") + " · " + eff("reviewer_provider")],
    [tr("ew.architect"), eff("architect_model") + " · " + eff("architect_provider")],
    [labelOf(box, "working_directory"), show(cur.working_directory)],
    [labelOf(box, "enabled"), cur.enabled ? tr("form.yes") : tr("form.no")],
  ];
  const grid = mk("div", "ew-sumgrid");
  for (const [k, v] of items) {
    const cell = mk("div", "ew-sum");
    cell.append(mk("span", "", k), mk("b", "", v));
    grid.appendChild(cell);
  }
  const actions = mk("div", "ew-actions");
  const edit = mk("button", "ew-pri", "✎ " + tr("ew.edit"));
  edit.type = "button";
  edit.addEventListener("click", () => openWin(box));
  const copy = mk("button", "", tr("save.copy_from"));
  copy.type = "button";
  copy.addEventListener("click", () => { if (save()) save().onCopy(box); });
  actions.append(edit, copy);
  card.append(mk("h3", "", tr("form.title")), grid, actions);
  return card;
}

// --- the window -------------------------------------------------------------------------

let step = 0;
let maximised = false;

function pageNode(id) {
  return document.getElementById("ew-page-" + id);
}

function labelled(caption, el) {
  const cell = mk("div", "ew-cell");
  if (!el.ariaLabel) el.ariaLabel = caption;
  cell.append(mk("small", "", caption), el);
  return cell;
}

function makeQaFields() {
  const ph = BoardRoles.qaPlaceholders();
  const fields = {};
  for (const [field, key] of [["provider", "qa_provider"], ["model", "qa_model"]].concat(QA_LIMITS)) {
    let el;
    if (field === "mode") {
      el = mk("select", "");
      const none = mk("option", "", tr("ew.qa.default", { v: ph.mode }));
      none.value = "";
      el.appendChild(none);
      for (const m of BoardRoles.QA_MODES) {
        const o = mk("option", "", tr(QA_MODE_KEY[m]));
        o.value = m;
        el.appendChild(o);
      }
    } else if (field === "smoke") {
      el = mk("input", "");
      el.type = "checkbox";
    } else if (field === "provider" || field === "model") {
      el = mk("input", "");
    } else {
      el = mk("input", "");
      el.type = "number";
      el.placeholder = ph[field];
    }
    el.dataset.extra = key;
    fields[key] = el;
  }
  return fields;
}

function makePollFields() {
  const fields = {};
  for (const role of BoardRoles.POLLING_ROLES) {
    fields[role] = {};
    for (const key of BoardRoles.POLLING_KEYS) {
      const el = mk("input", "");
      el.type = "number";
      el.step = key === "poll_seconds" ? "any" : "1";
      fields[role][key] = el;
    }
  }
  return fields;
}

function fillList(list, values) {
  list.textContent = "";
  for (const v of values) {
    const o = mk("option", "");
    o.value = v;
    list.appendChild(o);
  }
}

// The inputs stay free text; the datalists only suggest the names of the registry.
function choiceLists(provEl, modelEl, id) {
  const provList = mk("datalist", "");
  provList.id = "ew-prov-" + id;
  const modelList = mk("datalist", "");
  modelList.id = "ew-model-" + id;
  provEl.setAttribute("list", provList.id);
  modelEl.setAttribute("list", modelList.id);
  const refresh = () => {
    fillList(provList, BoardRoles.providerChoices(providerList));
    fillList(modelList, BoardRoles.modelChoices(providerList, provEl.value));
  };
  provEl.addEventListener("input", refresh);
  choiceRefreshers.push(refresh);
  refresh();
  return [provList, modelList];
}

async function loadProviders() {
  try {
    const res = await fetch("/api/board/providers", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const body = await res.json();
    providerList = Array.isArray(body.providers) ? body.providers : [];
  } catch (e) {
    console.warn("provider suggestions unavailable, free text kept", e);
    providerList = [];
  }
  for (const refresh of choiceRefreshers) refresh();
  const note = document.getElementById("ew-reg-note");
  if (note) note.textContent = providerList.length ? tr("ew.reg.hint") : "";
}

function qaCurrent() {
  const values = {};
  for (const [field, key] of [["provider", "qa_provider"], ["model", "qa_model"]].concat(QA_LIMITS)) {
    values[field] = field === "smoke" ? (qaFields[key].checked ? "true" : "") : qaFields[key].value;
  }
  return values;
}

function qaFill(values) {
  for (const [field, key] of [["provider", "qa_provider"], ["model", "qa_model"]].concat(QA_LIMITS)) {
    if (field === "smoke") qaFields[key].checked = values[field] === "true";
    else qaFields[key].value = values[field];
  }
}

function qaRows() {
  if (!qa) return [];
  return BoardRoles.qaDiffRows(qa.initial, qaCurrent()).map((d) => ({ ...d, field: "qa." + d.field }));
}

function qaProblem() {
  const values = qaCurrent();
  try {
    BoardRoles.qaOptionsFrom(values);
  } catch (e) {
    return true;
  }
  return (values.provider === "") !== (values.model === "");
}

function isDirty(box) {
  const s = save();
  return (s ? s.formDirty(box) : false) || qaRows().length > 0 || pollRows().length > 0 || featPlan().diff.length > 0 ||
    thinkPlan().diff.length > 0 || retPlan().diff.length > 0 || overridesPlan().diff.length > 0;
}

// The mission id: the save state has it once the form is attached; a quick click can come
// earlier, so the #board/<mission_id> fragment of the mission screen is the fallback.
function currentMissionId() {
  const s = save();
  if (s && s.SaveState.missionId) return s.SaveState.missionId;
  const match = /^#board\/([^/?]+)/.exec(globalThis.location ? globalThis.location.hash : "");
  return match && match[1] !== "view" ? decodeURIComponent(match[1]) : null;
}

async function loadQa() {
  const s = save();
  const id = currentMissionId();
  const note = document.getElementById("ew-qa-note");
  try {
    if (!id) throw new Error("no mission id");
    qa = await BoardRoles.loadQaRoles(s, rolesUrl());
    qaFill(qa.initial);
    pollInitial = BoardRoles.pollingValuesOf(qa.text, configOf());
    pollFill(pollInitial);
    note.textContent = "";
    for (const el of Object.values(qaFields)) el.disabled = false;
    pollEach((role, key) => { pollFields[role][key].disabled = false; });
  } catch (e) {
    qa = null;
    pollInitial = null;
    note.textContent = tr("ew.qa.loadfail");
    for (const el of Object.values(qaFields)) el.disabled = true;
    pollEach((role, key) => { pollFields[role][key].disabled = true; });
  }
}

function rolesUrl() {
  return "/api/board/missions/" + encodeURIComponent(currentMissionId()) + "/roles";
}

function configOf() {
  const s = save();
  return (s && s.SaveState.data && s.SaveState.data.config) || {};
}

function pollEach(fn) {
  for (const role of BoardRoles.POLLING_ROLES) {
    for (const key of BoardRoles.POLLING_KEYS) fn(role, key);
  }
}

function pollFill(values) {
  pollEach((role, key) => { pollFields[role][key].value = values[role][key]; });
}

function pollCurrent() {
  return Object.fromEntries(BoardRoles.POLLING_ROLES.map((role) => [
    role, Object.fromEntries(BoardRoles.POLLING_KEYS.map((key) => [key, pollFields[role][key].value])),
  ]));
}

function pollRows() {
  if (!qa || !pollInitial) return [];
  return BoardRoles.pollingDiffRows(pollInitial, pollCurrent())
    .map((d) => ({ ...d, field: "poll." + d.role + "." + d.key }));
}

function pollProblem() {
  if (pollRows().length === 0) return false;
  return BoardRoles.validateRolesJson(BoardRoles.pollingRolesText(qa.text, pollRows()), []).errors.length > 0;
}

async function saveQa() {
  if (!qa) return;
  qa = await BoardRoles.saveQaRoles(save(), rolesUrl(), qaCurrent(), qa);
}

// Runs after saveQa: both write roles_json, and the second PUT carries the version the first one returned.
async function savePolls() {
  const rows = pollRows();
  if (rows.length === 0) return;
  const body = { roles_json: BoardRoles.pollingRolesText(qa.text, rows), expected_version: qa.version };
  const got = await save().postJson(rolesUrl(), body, "PUT");
  qa = { ...qa, text: got.roles_json, version: got.version };
  pollInitial = BoardRoles.pollingValuesOf(got.roles_json, configOf());
}

function featLib() {
  return typeof BoardFeatures !== "undefined" ? BoardFeatures : null;
}

function isObj(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function plainObjectOrNull(text) {
  if (!text.trim()) return {};
  try {
    const v = JSON.parse(text);
    return isObj(v) ? v : null;
  } catch {
    return null;
  }
}

function makeFeatFields() {
  const fields = { global: mk("textarea", ""), mission: mk("textarea", "") };
  for (const [name, params] of FEATURE_ROWS) {
    const on = mk("input", "");
    on.type = "checkbox";
    on.dataset.feat = name + ".enabled";
    fields[name + ".enabled"] = on;
    for (const p of params) {
      const n = mk("input", "");
      n.type = "number";
      n.dataset.feat = name + "." + p;
      fields[name + "." + p] = n;
    }
  }
  for (const el of Object.values(fields)) el.disabled = true;
  return fields;
}

function featRowsNode() {
  const rows = mk("div", "");
  for (const [name, params] of FEATURE_ROWS) {
    const line = mk("div", "ew-feat-row");
    line.appendChild(mk("b", "", tr(FEAT_NAME_KEY[name])));
    line.appendChild(labelled(tr("ew.feat.enabled"), featFields[name + ".enabled"]));
    for (const p of params) line.appendChild(labelled(tr(FEAT_PARAM_KEY[p]), featFields[name + "." + p]));
    rows.appendChild(line);
  }
  rows.addEventListener("input", (event) => featRowChanged(event.target));
  return rows;
}

// A row change rewrites the global JSON; text that is not an object is left for the validator.
function featRowChanged(input) {
  const [name, param] = input.dataset.feat.split(".");
  const obj = plainObjectOrNull(featFields.global.value);
  if (!obj || (name in obj && !isObj(obj[name]))) return;
  const body = { ...obj[name] };
  if (param === "enabled") body.enabled = input.checked;
  else if (input.value === "") delete body[param];
  else body[param] = Number(input.value);
  obj[name] = body;
  featFields.global.value = JSON.stringify(obj, null, 2);
}

// The rows show the values the validator resolves (the defaults when the JSON is absent).
function featRowsFromText() {
  const lib = featLib();
  const parsed = lib ? lib.parseFeatures(featFields.global.value, "") : null;
  if (!parsed || !parsed.features) return;
  for (const [name, params] of FEATURE_ROWS) {
    featFields[name + ".enabled"].checked = parsed.features[name].enabled;
    for (const p of params) featFields[name + "." + p].value = parsed.features[name][p];
  }
}

function featFill(state) {
  featFields.global.value = state.features_json;
  featFields.mission.value = state.mission_features_json;
  featRowsFromText();
}

function setFeatEnabled(on, hasMission) {
  for (const [key, el] of Object.entries(featFields)) {
    el.disabled = !on || (key === "mission" && !hasMission);
  }
}

async function loadFeatures() {
  const s = save();
  const lib = featLib();
  const id = currentMissionId();
  const note = document.getElementById("ew-feat-note");
  if (!s || !lib) {
    feat = null;
    note.textContent = tr("ew.feat.nolib");
    setFeatEnabled(false);
    return;
  }
  try {
    const profile = await s.fetchJson("/api/board/features");
    const mission = id ? await s.fetchJson("/api/board/missions/" + encodeURIComponent(id) + "/features") : null;
    feat = { state: s.featuresState(profile, mission) };
    featFill(feat.state);
    note.textContent = "";
    setFeatEnabled(true, Boolean(id));
  } catch (e) {
    feat = null;
    note.textContent = tr("ew.feat.loadfail");
    setFeatEnabled(false);
  }
}

// The mission text is not sent without a mission id: its PUT URL would carry "null".
function featPlan() {
  const s = save();
  if (!feat || !s) return { diff: [], requests: [] };
  const id = currentMissionId();
  const current = {
    features_json: featFields.global.value,
    mission_features_json: id ? featFields.mission.value : feat.state.mission_features_json,
  };
  return s.featuresSavePlan(feat.state, id, current);
}

async function saveFeatures() {
  const plan = featPlan();
  for (const req of plan.requests) await save().postJson(req.url, req.body, req.method, req.headers);
  if (plan.requests.length) await loadFeatures();
}

async function loadThinking() {
  const s = save();
  const note = document.getElementById("ew-think-note");
  if (!s || !globalThis.BoardThinking) {
    think = null;
    note.textContent = L("ew.think.nolib", "The thinking module is not loaded.");
    thinkField.disabled = true;
    return;
  }
  try {
    const got = await s.fetchJson("/api/board/thinking");
    think = { state: { thinking_json: got.thinking_json || "", thinking_version: got.version } };
    thinkField.value = think.state.thinking_json;
    note.textContent = "";
    thinkField.disabled = false;
  } catch (e) {
    think = null;
    note.textContent = L("ew.think.loadfail", "Could not load the thinking settings.");
    thinkField.disabled = true;
  }
}

function thinkPlan() {
  const s = save();
  if (!think || !s) return { diff: [], requests: [] };
  return s.thinkingSavePlan(think.state, { thinking_json: thinkField.value });
}

async function saveThinking() {
  const plan = thinkPlan();
  for (const req of plan.requests) await save().postJson(req.url, req.body, req.method, req.headers);
  if (plan.requests.length) await loadThinking();
}

async function loadRetrieval() {
  const s = save();
  const note = document.getElementById("ew-ret-note");
  if (!s || !globalThis.BoardRetrieval) {
    ret = null;
    note.textContent = L("ew.ret.nolib", "The retrieval module is not loaded.");
    retField.disabled = true;
    return;
  }
  try {
    const got = await s.fetchJson("/api/board/retrieval");
    ret = { state: { retrieval_json: got.retrieval_json || "", retrieval_version: got.version } };
    retField.value = ret.state.retrieval_json;
    note.textContent = "";
    retField.disabled = false;
  } catch {
    ret = null;
    note.textContent = L("ew.ret.loadfail", "Could not load the retrieval settings.");
    retField.disabled = true;
  }
}

function retPlan() {
  const s = save();
  if (!ret || !s) return { diff: [], requests: [] };
  return s.retrievalSavePlan(ret.state, { retrieval_json: retField.value });
}

async function saveRetrieval() {
  const plan = retPlan();
  for (const req of plan.requests) await save().postJson(req.url, req.body, req.method, req.headers);
  if (plan.requests.length) await loadRetrieval();
}

// --- overrides_json of this mission (GET and POST /api/board/missions/<id>) ---------------

async function loadOverrides() {
  const s = save();
  const note = document.getElementById("ew-ov-note");
  if (!s || !globalThis.BoardOverrides) {
    overrides = null;
    note.textContent = L("ew.ov.nolib", "The overrides module is not loaded.");
    overField.disabled = true;
    return;
  }
  const missionId = currentMissionId();
  try {
    const got = await s.fetchJson("/api/board/missions/" + encodeURIComponent(missionId));
    overrides = { state: { mission_id: missionId, overrides_json: got.mission.overrides_json || "",
      overrides_version: got.mission.updated_at } };
    overField.value = overrides.state.overrides_json;
    note.textContent = "";
    overField.disabled = false;
  } catch {
    overrides = null;
    note.textContent = L("ew.ov.loadfail", "Could not load the mission overrides.");
    overField.disabled = true;
  }
}

// The save plan for overrides_json: one POST when the text changed, carrying the mission version read at load.
function overridesSavePlan(state, next) {
  if (next === state.overrides_json) return { diff: [], requests: [] };
  return {
    diff: [{ field: "overrides_json", old: state.overrides_json, new: next }],
    requests: [{ url: "/api/board/missions/" + encodeURIComponent(state.mission_id), method: "POST",
      headers: { "X-AHAWR-Dashboard": "1" },
      body: { overrides_json: next, expected_version: state.overrides_version } }],
  };
}

function overridesPlan() {
  if (!overrides) return { diff: [], requests: [] };
  return overridesSavePlan(overrides.state, overField.value);
}

// Runs after doSave, which bumps the mission's updated_at, so the version is read again first.
async function saveOverrides() {
  if (!overridesPlan().requests.length) return;
  const got = await save().fetchJson("/api/board/missions/" + encodeURIComponent(overrides.state.mission_id));
  overrides.state.overrides_version = got.mission.updated_at;
  const plan = overridesPlan();
  for (const req of plan.requests) await save().postJson(req.url, req.body, req.method, req.headers);
  await loadOverrides();
}

// --- engine_json (GET and PUT /api/board/engine) -----------------------------------------

function engineLabel(key) {
  const [i18nKey, en] = ENGINE_LABELS[key];
  return L(i18nKey, en);
}

function engineSourceText(source) {
  return source === "json" ? L("ew.eng.src.json", "json") : L("ew.eng.src.default", "default");
}

// The telegram chat id is never shown or diffed in clear text.
function engineShown(key, value) {
  if (key === "error_rules") return JSON.stringify(value);
  if (key === "telegram_chat_id") return value ? L("ew.eng.set", "set (hidden)") : L("ew.eng.unset", "not set");
  return show(value);
}

function engineMasked(values) {
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key, engineShown(key, value)]));
}

function makeEngineFields() {
  const list = mk("div", "ew-eng");
  engineField = {};
  engineView = {};
  for (const key of ENGINE_KEYS) {
    const row = mk("div", "ew-eng-row");
    const value = mk("span", "mono");
    const source = mk("small", "ew-src");
    engineView[key] = { value, source };
    row.append(mk("b", "", engineLabel(key)), value, source);
    if (ENGINE_OVERRIDABLE.includes(key)) {
      const input = mk("input", "");
      input.type = key === "max_attempts_per_task" ? "number" : "text";
      engineField[key] = input;
      row.appendChild(labelled(L("ew.eng.override", "Override (empty = default)"), input));
    } else {
      row.appendChild(mk("small", "", L("ew.eng.readonly", "Read-only here; the stored value is kept.")));
    }
    list.appendChild(row);
  }
  engineRulesBox = mk("fieldset", "ew-eng-rules");
  engineRulesBox.disabled = true;
  renderEngineRules([]);
  list.appendChild(engineRulesBox);
  return list;
}

// shortcut: legacy source not shown, GET /engine returns no legacy columns, upgrade when the route adds them
function fillEngine(raw) {
  const { accepted } = BoardEngine.parseEngine(raw);
  for (const key of ENGINE_KEYS) {
    const fromJson = Object.hasOwn(accepted, key);
    const effective = fromJson ? accepted[key] : BoardEngine.ENGINE_DEFAULTS[key];
    engineView[key].value.textContent = engineShown(key, effective);
    engineView[key].source.textContent = engineSourceText(fromJson ? "json" : "default");
    if (engineField[key]) {
      engineField[key].value = fromJson ? (Array.isArray(accepted[key]) ? JSON.stringify(accepted[key]) : String(accepted[key])) : "";
      engineField[key].placeholder = show(effective);
    }
  }
  renderEngineRules((accepted.error_rules || []).map(ruleToForm));
}

function setEngineEnabled(on) {
  for (const key of ENGINE_OVERRIDABLE) engineField[key].disabled = !on;
  engineRulesBox.disabled = !on;
}

const ENGINE_RULE_ACTIONS = ["retry", "failover", "compact", "fail"];
const ENGINE_RULE_COLUMNS = [
  ["id", "ew.eng.rule.id", "ID"],
  ["http", "ew.eng.rule.http", "HTTP codes"],
  ["words", "ew.eng.rule.words", "Words"],
  ["codes", "ew.eng.rule.codes", "Codes"],
  ["action", "ew.eng.rule.action", "Action"],
  ["retries", "ew.eng.rule.retries", "Retries"],
  ["note", "ew.eng.rule.note", "Note"],
];

function ruleToForm(rule) {
  const match = rule.match || {};
  return {
    id: rule.id,
    http: (match.http || []).join(", "),
    words: (match.words || []).join(", "),
    codes: (match.codes || []).join(", "),
    action: rule.action,
    retries: String(rule.retries),
    note: rule.note || "",
  };
}

function splitList(text) {
  return text.split(",").map((item) => item.trim()).filter(Boolean);
}

// Text the core cannot read as a number stays text, so the core's error shows what was typed.
function formToRule(form) {
  const match = {};
  const http = splitList(form.http).map((item) => (/^\d+$/.test(item) ? Number(item) : item));
  const words = splitList(form.words);
  const codes = splitList(form.codes);
  if (http.length) match.http = http;
  if (words.length) match.words = words;
  if (codes.length) match.codes = codes;
  const retries = form.retries.trim();
  const rule = { id: form.id.trim() };
  if (Object.keys(match).length) rule.match = match;
  rule.action = form.action;
  rule.retries = /^\d+$/.test(retries) ? Number(retries) : form.retries;
  if (form.note.trim()) rule.note = form.note.trim();
  return rule;
}

function readEngineRuleForms() {
  return engineRuleRows.map((cells) => Object.fromEntries(ENGINE_RULE_COLUMNS.map(([field]) => [field, cells[field].value])));
}

function changeEngineRules(change) {
  renderEngineRules(change(readEngineRuleForms()));
}

function moveEngineRule(forms, index, to) {
  if (to < 0 || to >= forms.length) return forms;
  const next = forms.slice();
  const [moved] = next.splice(index, 1);
  next.splice(to, 0, moved);
  return next;
}

function engineRuleCell(field, value) {
  if (field === "action") {
    const select = mk("select");
    for (const action of ENGINE_RULE_ACTIONS) {
      const option = mk("option", "", action);
      option.value = action;
      select.appendChild(option);
    }
    select.value = value;
    return select;
  }
  const input = mk("input");
  if (field === "retries") {
    input.type = "number";
    input.min = "0";
  }
  input.value = value;
  return input;
}

function engineRuleButton(text, onClick) {
  const button = mk("button", "", text);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

// Rebuilds the table from form values; the saved values are read again only when the plan is built.
function renderEngineRules(forms) {
  engineRuleRows = [];
  const table = mk("table", "ew-rules");
  const head = mk("tr");
  for (const [, i18nKey, en] of ENGINE_RULE_COLUMNS) head.appendChild(mk("th", "", L(i18nKey, en)));
  head.appendChild(mk("th"));
  table.appendChild(head);
  forms.forEach((form, index) => {
    const row = mk("tr");
    const cells = {};
    for (const [field] of ENGINE_RULE_COLUMNS) {
      cells[field] = engineRuleCell(field, form[field]);
      const td = mk("td");
      td.appendChild(cells[field]);
      row.appendChild(td);
    }
    const actions = mk("td");
    actions.append(
      engineRuleButton(L("ew.eng.rule.up", "Up"), () => changeEngineRules((all) => moveEngineRule(all, index, index - 1))),
      engineRuleButton(L("ew.eng.rule.down", "Down"), () => changeEngineRules((all) => moveEngineRule(all, index, index + 1))),
      engineRuleButton(L("ew.eng.rule.delete", "Delete"), () => changeEngineRules((all) => all.filter((_, i) => i !== index))),
    );
    row.appendChild(actions);
    table.appendChild(row);
    engineRuleRows.push(cells);
  });
  const toolbar = mk("div", "ew-rules-bar");
  toolbar.append(
    engineRuleButton(L("ew.eng.rule.add", "Add rule"), () => changeEngineRules((all) => all.concat([
      { id: "", http: "", words: "", codes: "", action: "fail", retries: "0", note: "" },
    ]))),
    engineRuleButton(L("ew.eng.rule.recommended", "Load recommended"), () => renderEngineRules(BoardEngine.RECOMMENDED_ERROR_RULES.map(ruleToForm))),
  );
  engineRulesBox.textContent = "";
  engineRulesBox.append(
    mk("legend", "", L("ew.eng.rules", "Error rules")),
    mk("small", "", L("ew.eng.rules.hint", "Checked in order, the first matching rule wins. Lists are comma-separated. An empty table keeps the legacy rules.")),
    toolbar,
    table,
  );
}

async function loadEngine() {
  const s = save();
  const note = document.getElementById("ew-eng-note");
  if (!s || !globalThis.BoardEngine) {
    engine = null;
    note.textContent = L("ew.eng.nolib", "The engine module is not loaded.");
    setEngineEnabled(false);
    return;
  }
  try {
    const got = await s.fetchJson("/api/board/engine");
    const raw = got.engine_json || "";
    const { errors } = BoardEngine.parseEngine(raw);
    if (errors.length) {
      engine = null;
      note.textContent = L("ew.eng.badload", "The stored engine settings are not valid ({n} error(s)): the fields are read-only.", { n: errors.length });
    } else {
      engine = { state: { engine_json: raw, engine_version: got.version } };
      fillEngine(raw);
      note.textContent = "";
    }
    setEngineEnabled(engine !== null);
  } catch {
    engine = null;
    note.textContent = L("ew.eng.loadfail", "Could not load the engine settings.");
    setEngineEnabled(false);
  }
}

function engineCandidate() {
  const next = { ...BoardEngine.parseEngine(engine.state.engine_json).accepted };
  for (const key of ENGINE_OVERRIDABLE) {
    const text = engineField[key].value.trim();
    if (!text) delete next[key];
    else next[key] = key === "max_attempts_per_task" && Number.isFinite(Number(text)) ? Number(text) : text;
  }
  const rules = readEngineRuleForms().map(formToRule);
  if (rules.length) next.error_rules = rules;
  else delete next.error_rules;
  return next;
}

function engineCandidateErrors() {
  return BoardEngine.parseEngine(JSON.stringify(engineCandidate())).errors;
}

// The save plan for engine_json: one PUT when the normalized values changed, carrying the version read at load.
function engineSavePlan(state, next) {
  const before = BoardEngine.parseEngine(state.engine_json).accepted;
  const after = BoardEngine.parseEngine(JSON.stringify(next)).accepted;
  if (JSON.stringify(before) === JSON.stringify(after)) return { diff: [], requests: [] };
  return {
    diff: [{ field: "engine_json", old: engineMasked(before), new: engineMasked(after) }],
    requests: [{ url: "/api/board/engine", method: "PUT",
      headers: { "X-AHAWR-Dashboard": "1" },
      body: { engine_json: JSON.stringify(after), expected_version: state.engine_version } }],
  };
}

function enginePlan() {
  if (!engine || engineCandidateErrors().length) return { diff: [], requests: [] };
  return engineSavePlan(engine.state, engineCandidate());
}

async function saveEngine() {
  const plan = enginePlan();
  for (const req of plan.requests) await save().postJson(req.url, req.body, req.method, req.headers);
  if (plan.requests.length) await loadEngine();
}

function fillPages(box, win) {
  qaFields = makeQaFields();
  pollFields = makePollFields();
  choiceRefreshers = [];
  featFields = makeFeatFields();
  thinkField = mk("textarea", "");
  thinkField.id = "ew-think-json";
  thinkField.disabled = true;
  retField = mk("textarea", "");
  retField.id = "ew-ret-json";
  retField.disabled = true;
  overField = mk("textarea", "");
  overField.id = "ew-ov-json";
  overField.disabled = true;
  const lockedNote = (key) => {
    const row = rowOf(box, key);
    const cur = control(box, key);
    const box2 = mk("div", "ew-lock");
    box2.append(mk("span", "", "🔒"), mk("span", "mono", cur ? cur.value.split("\n").join(", ") : ""),
      mk("small", "", labelOf(box, key) + " · " + tr("ew.locked")));
    if (row) row.hidden = true;
    return box2;
  };
  const sec = (title, hint) => {
    const s = mk("div", "ew-sec");
    s.appendChild(mk("h4", "", title));
    if (hint) s.appendChild(mk("p", "", hint));
    return s;
  };
  const move = (parent, key) => {
    const row = rowOf(box, key);
    if (row && !row.hidden) parent.appendChild(row);
  };
  const running = box.querySelector(".meta.running-note");

  // page 1
  const p1 = pageNode("mission");
  if (running) p1.appendChild(mk("div", "ew-warn", running.textContent));
  const s1 = sec(tr("ew.sec.mission"), tr("ew.hint.mission"));
  move(s1, "name"); move(s1, "objective");
  const s2 = sec(tr("ew.sec.rules"), tr("ew.hint.rules"));
  const two = mk("div", "ew-row2");
  move(two, "rules"); move(two, "acceptance_criteria");
  s2.appendChild(two);
  const s3 = sec(tr("ew.sec.where"));
  s3.appendChild(lockedNote("working_directory"));
  p1.append(s1, s2, s3);

  // page 2
  const p2 = pageNode("exec");
  const a = sec(tr("ew.sec.attempts"), tr("ew.hint.attempts"));
  move(a, "attempts_per_task");
  const r = sec(tr("ew.sec.roles"), tr("ew.hint.roles") + " " + tr("ew.hint.default"));
  const roles = mk("div", "ew-roles");
  for (const [labelKey, color, km, kp] of ROLES) {
    const line = mk("div", "ew-role");
    const name = mk("b", "");
    const dot = mk("span", "ew-dot");
    dot.style.background = color;
    name.append(dot, document.createTextNode(tr(labelKey)));
    line.appendChild(name);
    const provEl = control(box, kp) || qaFields[kp];
    const modelEl = control(box, km) || qaFields[km];
    if (provEl && modelEl) line.append(...choiceLists(provEl, modelEl, kp));
    for (const [key, cap] of [[km, tr("ew.model")], [kp, tr("ew.provider")]]) {
      const cell = mk("div", "ew-cell");
      cell.appendChild(mk("small", "", cap));
      const c = control(box, key) || qaFields[key];
      const row = rowOf(box, key);
      if (c) cell.appendChild(c);
      if (row) { row.hidden = true; }
      line.appendChild(cell);
    }
    roles.appendChild(line);
  }
  for (const role of BoardRoles.POLLING_ROLES) {
    const line = mk("div", "ew-role");
    line.appendChild(mk("b", "", tr(POLL_ROLE_KEYS[role])));
    for (const key of BoardRoles.POLLING_KEYS) {
      line.appendChild(labelled(tr(POLL_LABEL_KEYS[key]), pollFields[role][key]));
    }
    roles.appendChild(line);
  }
  const regNote = mk("small", "");
  regNote.id = "ew-reg-note";
  r.append(roles, regNote);
  const fe = sec(tr("ew.sec.features"), tr("ew.hint.features"));
  const feNote = mk("div", "ew-warn");
  feNote.id = "ew-feat-note";
  const feBox = mk("div", "ew-feat");
  featFields.global.addEventListener("input", featRowsFromText);
  feBox.append(feNote, featRowsNode(), labelled(tr("ew.feat.global"), featFields.global),
    labelled(tr("ew.feat.mission"), featFields.mission));
  fe.appendChild(feBox);
  const l = sec(tr("ew.sec.limits"));
  const two2 = mk("div", "ew-row2");
  move(two2, "max_tasks");
  l.appendChild(two2);
  move(l, "enabled");
  const th = sec(tr("ew.sec.policy"), tr("ew.hint.policy"));
  const thNote = mk("div", "ew-warn");
  thNote.id = "ew-think-note";
  th.append(thNote, labelled(L("ew.think.json", "Thinking settings (JSON)"), thinkField));
  const rt = sec(L("ew.sec.retrieval", "Retrieval"), L("ew.hint.retrieval", "Retrieval settings as one JSON object; checked when you save."));
  const rtNote = mk("div", "ew-warn");
  rtNote.id = "ew-ret-note";
  rt.append(rtNote, labelled(L("ew.ret.json", "Retrieval settings (JSON)"), retField));
  const q = sec(tr("ew.sec.qa"));
  const qaNote = mk("div", "ew-warn");
  qaNote.id = "ew-qa-note";
  const qaRow = mk("div", "ew-row2");
  for (const [field, key] of QA_LIMITS) qaRow.appendChild(labelled(tr(QA_LABEL_KEYS[field]), qaFields[key]));
  q.append(qaNote, qaRow);
  const eg = sec(L("ew.eng.sec", "Engine settings"), L("ew.eng.hint", "Loop and infrastructure values. An empty override uses the default; the source shows where each value comes from."));
  const egNote = mk("div", "ew-warn");
  egNote.id = "ew-eng-note";
  eg.append(egNote, makeEngineFields());
  p2.append(a, r, fe, q, l, th, rt, eg);

  // page 3
  const p3 = pageNode("ctx");
  const h = sec(tr("ew.sec.hints"), tr("ew.hint.hints"));
  move(h, "hints"); move(h, "required_files");
  const ov = sec(L("ew.sec.overrides", "Mission overrides"), L("ew.hint.overrides", "One JSON object per mission (roles, features, retrieval); checked when you save."));
  const ovNote = mk("div", "ew-warn");
  ovNote.id = "ew-ov-note";
  ov.append(ovNote, labelled(L("ew.ov.json", "Mission overrides (JSON)"), overField));
  p3.append(h, ov);
}

function diffLabel(box, field) {
  if (field === "engine_json") return L("ew.eng.sec", "Engine settings");
  if (field === "overrides_json") return L("ew.sec.overrides", "Mission overrides");
  if (field.startsWith("qa.")) return tr("ew.qa") + " · " + tr(QA_LABEL_KEYS[field.slice(3)]);
  if (field.startsWith("poll.")) {
    const [role, key] = field.slice(5).split(".");
    return tr(POLL_ROLE_KEYS[role]) + " · " + tr(POLL_LABEL_KEYS[key]);
  }
  return FEAT_LABEL_KEYS[field] ? tr(FEAT_LABEL_KEYS[field]) : labelOf(box, field);
}

function reviewHtml(box) {
  const page = pageNode("review");
  page.textContent = "";
  const s = save();
  const diff = (s ? s.currentDiff(box) : []).concat(qaRows(), pollRows(), featPlan().diff, thinkPlan().diff, retPlan().diff, overridesPlan().diff, enginePlan().diff);
  const sec = mk("div", "ew-sec");
  sec.appendChild(mk("h4", "", tr("ew.s4")));
  sec.appendChild(mk("p", "", diff.length ? tr("ew.changes", { n: diff.length }) : tr("ew.nochange")));
  if (diff.length) {
    const table = mk("table", "ew-diff");
    const head = mk("tr", "");
    for (const th of [tr("ew.field"), tr("ew.before"), tr("ew.after")]) head.appendChild(mk("th", "", th));
    table.appendChild(head);
    for (const d of diff) {
      const row = mk("tr", "");
      row.append(mk("td", "ew-field", diffLabel(box, d.field)),
        mk("td", "ew-old", show(d.old).slice(0, 160)), mk("td", "ew-new", show(d.new).slice(0, 160)));
      table.appendChild(row);
    }
    sec.appendChild(table);
  }
  page.appendChild(sec);
  if (box.querySelector(".meta.running-note") && diff.length) {
    page.appendChild(mk("div", "ew-warn", tr("ew.midrun")));
  }
}

function problems(box) {
  const cur = save() ? save().readForm(box) : {};
  if (!String(cur.name || "").trim() || !String(cur.objective || "").trim()) return { page: 0, text: tr("ew.invalid") };
  if (think) {
    const thinkErrors = globalThis.BoardThinking.validate(thinkField.value);
    if (thinkErrors.length) return { page: 1, text: L("ew.think.invalid", "Thinking settings: {msg}", { msg: thinkErrors.join("; ") }) };
  }
  if (ret) {
    const retErrors = globalThis.BoardRetrieval.validate(retField.value);
    if (retErrors.length) return { page: 1, text: L("ew.ret.invalid", "Retrieval settings: {msg}", { msg: retErrors.join("; ") }) };
  }
  if (overrides) {
    const overErrors = globalThis.BoardOverrides.validate(overField.value);
    if (overErrors.length) return { page: 2, text: L("ew.ov.invalid", "Mission overrides: {msg}", { msg: overErrors.join("; ") }) };
  }
  if (engine && engineCandidateErrors().length) {
    return { page: 1, text: L("ew.eng.invalid", "Engine settings: {msg}", { msg: engineCandidateErrors().join("; ") }) };
  }
  if (qaProblem()) return { page: 1, text: tr("ew.qa.invalid") };
  if (pollProblem()) return { page: 1, text: tr("ew.poll.invalid") };
  if (feat) {
    const checked = featLib().parseFeatures(featFields.global.value, featFields.mission.value);
    if (checked.errors.length) return { page: 1, text: tr("ew.feat.invalid", { msg: checked.errors.join("; ") }) };
  }
  return null;
}

function paint(box) {
  const win = document.getElementById("ew-win");
  if (!win) return;
  const total = PAGES.length + 1;
  const dirty = isDirty(box);
  document.getElementById("ew-bar").style.width = ((step + 1) / total * 100) + "%";
  const labels = PAGES.map((p) => tr(p.label)).concat([tr("ew.s4")]);
  const steps = document.getElementById("ew-steps");
  steps.textContent = "";
  labels.forEach((text, i) => {
    const b = mk("button", "ew-st" + (i === step ? " on" : i < step ? " done" : ""));
    b.type = "button";
    b.append(mk("i", "", i < step ? "✓" : String(i + 1)), document.createTextNode(text));
    b.addEventListener("click", () => { step = i; paint(box); });
    steps.appendChild(b);
  });
  for (const p of PAGES) pageNode(p.id).hidden = PAGES[step] !== p;
  pageNode("review").hidden = step !== PAGES.length;
  if (step === PAGES.length) reviewHtml(box);
  const last = step === PAGES.length;
  document.getElementById("ew-back").disabled = step === 0;
  const next = document.getElementById("ew-next");
  next.textContent = last ? tr("ew.save") : tr("ew.next") + " ›";
  next.disabled = last && !dirty;
  document.getElementById("ew-hint").textContent = labels[step] + " · " + tr("ew.step", { a: step + 1, b: total });
  document.getElementById("ew-dirty").classList.toggle("on", dirty);
  document.getElementById("ew-err").textContent = "";
  document.getElementById("ew-body").scrollTop = 0;
}

function closeWin(box, force) {
  const s = save();
  if (!force && isDirty(box) && !window.confirm(tr("ew.discard"))) return;
  if (!force && s && s.formDirty(box)) s.onReset(box);
  if (!force && qa) qaFill(qa.initial);
  if (!force && pollInitial) pollFill(pollInitial);
  if (!force && feat) featFill(feat.state);
  if (!force && think) thinkField.value = think.state.thinking_json;
  if (!force && ret) retField.value = ret.state.retrieval_json;
  if (!force && overrides) overField.value = overrides.state.overrides_json;
  if (!force && engine) fillEngine(engine.state.engine_json);
  const scrim = document.getElementById("ew-scrim");
  if (scrim) scrim.classList.remove("open");
  document.body.dataset.editing = "";
  document.removeEventListener("keydown", onKey);
  const old = box.querySelector(".ew-summary");
  if (old) old.replaceWith(summaryCard(box));
}

function onKey(event) {
  const box = document.getElementById("boardmission");
  if (event.key === "Escape" && box) closeWin(box, false);
}

async function onNext(box) {
  if (step < PAGES.length) { step += 1; paint(box); return; }
  const bad = problems(box);
  if (bad) { step = bad.page; paint(box); document.getElementById("ew-err").textContent = bad.text; return; }
  const next = document.getElementById("ew-next");
  next.disabled = true;
  try {
    await saveQa();
    await savePolls();
    await saveFeatures();
    await saveThinking();
    await saveRetrieval();
    await saveEngine();
    await save().doSave(box);          // re-renders #boardmission: the observer builds the card again
    await saveOverrides();
  } catch (e) {
    document.getElementById("ew-err").textContent = e.message || tr("ew.qa.loadfail");
    next.disabled = false;
  } finally {
    document.body.dataset.editing = "";
  }
}

function openWin(box) {
  const scrim = document.getElementById("ew-scrim");
  if (!scrim) return;
  step = 0;
  scrim.classList.add("open");
  document.body.dataset.editing = "1";
  document.addEventListener("keydown", onKey);
  paint(box);
  Promise.all([loadQa(), loadFeatures(), loadProviders(), loadThinking(), loadRetrieval(), loadOverrides(), loadEngine()]).then(() => paint(box));
}

function buildWindow(box) {
  const scrim = mk("div", "ew-scrim");
  scrim.id = "ew-scrim";
  const win = mk("div", "ew-win");
  win.id = "ew-win";
  win.setAttribute("role", "dialog");
  win.setAttribute("aria-modal", "true");
  const head = mk("div", "ew-head");
  const title = mk("h3", "", tr("ew.title"));
  const dirty = mk("span", "ew-dirty", tr("ew.unsaved"));
  dirty.id = "ew-dirty";
  const maxBtn = mk("button", "ew-ib", "⤢");
  maxBtn.type = "button";
  maxBtn.title = tr("ew.max");
  maxBtn.addEventListener("click", () => {
    maximised = !maximised;
    win.classList.toggle("max", maximised);
    maxBtn.textContent = maximised ? "⤡" : "⤢";
  });
  const closeBtn = mk("button", "ew-ib", "✕");
  closeBtn.type = "button";
  closeBtn.title = tr("ew.close");
  closeBtn.addEventListener("click", () => closeWin(box, false));
  head.append(title, dirty, mk("span", "ew-sp"), maxBtn, closeBtn);
  const prog = mk("div", "ew-prog");
  const bar = mk("i", "");
  bar.id = "ew-bar";
  prog.appendChild(bar);
  const steps = mk("div", "ew-steps");
  steps.id = "ew-steps";
  const body = mk("div", "ew-body");
  body.id = "ew-body";
  for (const p of PAGES.concat([{ id: "review" }])) {
    const page = mk("div", "ew-page");
    page.id = "ew-page-" + p.id;
    body.appendChild(page);
  }
  const foot = mk("div", "ew-foot");
  const back = mk("button", "", "‹ " + tr("ew.back"));
  back.id = "ew-back";
  back.type = "button";
  back.addEventListener("click", () => { step = Math.max(0, step - 1); paint(box); });
  const hint = mk("span", "ew-hintline");
  hint.id = "ew-hint";
  const err = mk("span", "ew-err");
  err.id = "ew-err";
  const cancel = mk("button", "", tr("ew.cancel"));
  cancel.type = "button";
  cancel.addEventListener("click", () => closeWin(box, false));
  const next = mk("button", "ew-pri", "");
  next.id = "ew-next";
  next.type = "button";
  next.addEventListener("click", () => onNext(box));
  foot.append(back, hint, err, mk("span", "ew-sp"), cancel, next);
  win.append(head, prog, steps, body, foot);
  scrim.appendChild(win);
  scrim.addEventListener("mousedown", (event) => { if (event.target === scrim) closeWin(box, false); });
  box.appendChild(scrim);
  fillPages(box, win);
  body.addEventListener("input", () => { if (document.getElementById("ew-scrim").classList.contains("open")) paint2(box); });
}

// repaint the dirty badge and the review button without leaving the page
function paint2(box) {
  const dirty = isDirty(box);
  document.getElementById("ew-dirty").classList.toggle("on", dirty);
  if (step === PAGES.length) {
    document.getElementById("ew-next").disabled = !dirty;
  }
}

function enhance(box) {
  if (!box || !box.querySelector(".confbox") || box.querySelector(".ew-summary")) return;
  if (document.getElementById("ew-scrim")) return;          // already open, being rebuilt
  const confbox = box.querySelector(".confbox");
  const card = summaryCard(box);
  confbox.before(card);
  buildWindow(box);
  confbox.hidden = true;
  const heading = confbox.previousElementSibling && confbox.previousElementSibling.previousElementSibling;
  if (heading && heading.tagName === "H3") heading.hidden = true;
}

let watching = false;
function watch() {
  if (watching || typeof MutationObserver === "undefined") return;
  const root = document.getElementById("board");
  if (!root) return;
  watching = true;
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      enhance(document.getElementById("boardmission"));
    });
  }).observe(root, { childList: true, subtree: true });
}

const BoardEditWin = { enhance, openWin, closeWin, PAGES, ROLES, problems, engineCandidate, engineSavePlan, overridesSavePlan };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardEditWin;
} else if (typeof document !== "undefined") {
  globalThis.BoardEditWin = BoardEditWin;
  watch();
}
})();
