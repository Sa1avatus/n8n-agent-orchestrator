/* board-scope-wrapped */
(function () {
/* Statistics popup (stats_popup feature flag): one modal with three tabs —
 * Summary, the rows of the scope ("Tasks" for a mission, "Attempts" for a task,
 * "Missions" for a project) and Engine.
 *
 * Loaded by board.html via <script src="/static/board_stats_popup.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardStatsPopup.attach(host, scope) adds the Statistics button to host only
 * when the stats_popup flag is on for that scope (the board flags for a project,
 * the board plus the mission flags for a mission or a task), so with the flag off
 * the board's DOM gains no button at all.
 *
 * Scope: { kind: "project" | "mission" | "task", project, missionId, taskId, name }.
 * The data comes from GET /api/board/projects/{project}/stats,
 * GET /api/board/missions/{missionId}/stats/detail and
 * GET /api/board/missions/{missionId}/tasks/{taskId}/stats/detail — every
 * request is a GET, nothing is posted here.
 *
 * Every value taken from the payload flows through esc().  A value that is
 * missing renders as "-" with the reason the figures carry in `reasons` as the
 * hover title; the Engine tab shows the sampler's reason ("no samples") or the
 * note that the sampler is off, never zeros.
 */
"use strict";

// esc() lives in the shared board_logic.js; the require() covers node loads of
// this file standalone (board.html always loads board_logic.js first).
const esc = (typeof BoardLogic !== "undefined" ? BoardLogic : require("./board_logic.js")).esc;

// The shared same-origin GET helper lives in board_actions.js; the require()
// covers node loads of this file standalone (board.html always loads
// board_actions.js first).
const fetchJson = (typeof BoardActions !== "undefined" ? BoardActions
  : require("./board_actions.js")).fetchJson;

// The feature-flag parser lives in board_features.js; the require() covers node
// loads standalone.
const popupFeatures = (typeof BoardFeatures !== "undefined" ? BoardFeatures
  : require("./board_features.js"));

// translated text; the English text is the fallback (node tests, a table without the key)
function L(key, en, params) {
  let v = en;
  try {
    if (typeof t === "function") { const r = t(key); if (r && r !== key) v = r; }
  } catch (e) { /* keep the English text */ }
  if (params) for (const k of Object.keys(params)) v = v.split("{" + k + "}").join(String(params[k]));
  return v;
}

// shortcut: the popup formats its own numbers (the Projects view keeps its
// formatters private, board_view_projects.js:34); upgrade when board_logic.js
// exports a shared number formatter.
// The unit suffix of every bench figure, spelled as in the bench table.
const UNIT_SUFFIX = {
  minutes: " min",
  hours: " h",
  tok_s: " tok/s",
  tokens: " tok",
  mib: " MiB",
};

function formatValue(value, unit) {
  if (value === null || value === undefined) return "-";
  if (typeof value === "boolean") return value ? L("sp.yes", "yes") : L("sp.no", "no");
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (unit === "usd") return "$" + number.toFixed(2);
  if (unit === "percent") return number.toFixed(1) + "%";
  if (unit === "tokens" || unit === "mib") {
    return number.toLocaleString("en-US") + UNIT_SUFFIX[unit];
  }
  if (unit === "hours" || unit === "minutes" || unit === "tok_s") {
    return number.toFixed(1) + UNIT_SUFFIX[unit];
  }
  if (unit === "score") return number.toFixed(1);
  return String(number);
}

// The Summary figures, in the order of the bench table: the payload key, the i18n
// label key (the label carries the unit), the English fallback and the unit.
const SUMMARY_FIGURES = [
  { key: "closed_tasks", labelKey: "fg.closed", labelEn: "Closed tasks", unit: "count" },
  { key: "planned_tasks", labelKey: "fg.planned", labelEn: "Planned tasks", unit: "count" },
  { key: "avg_final_review_score", labelKey: "fg.final_score", labelEn: "Avg final review score", unit: "score" },
  { key: "avg_first_review_score", labelKey: "fg.first_score", labelEn: "Avg first review score", unit: "score" },
  { key: "extra_attempts", labelKey: "fg.extra_attempts", labelEn: "Extra attempts", unit: "count" },
  { key: "worker_hours", labelKey: "fg.worker_hours", labelEn: "Worker hours", unit: "hours" },
  { key: "minutes_per_task", labelKey: "fg.minutes_per_task", labelEn: "Minutes per task", unit: "minutes" },
  { key: "steps", labelKey: "fg.steps", labelEn: "Worker steps", unit: "count" },
  { key: "steps_per_task", labelKey: "fg.steps_per_task", labelEn: "Steps per task", unit: "count" },
  { key: "output_tokens", labelKey: "fg.output_tokens", labelEn: "Output tokens", unit: "tokens" },
  { key: "tokens_per_step", labelKey: "fg.tokens_per_step", labelEn: "Tokens per step", unit: "count" },
  { key: "tok_per_s", labelKey: "fg.tok_s", labelEn: "Generation tok/s", unit: "tok_s" },
  { key: "cache_share_percent", labelKey: "fg.cache_share", labelEn: "Cache share %", unit: "percent" },
  { key: "generation_share_percent", labelKey: "fg.generation_share", labelEn: "Generation share of Worker time %", unit: "percent" },
  { key: "compactions", labelKey: "fg.compactions", labelEn: "Compactions", unit: "count" },
  { key: "reviewer_minutes", labelKey: "fg.reviewer_minutes", labelEn: "Reviewer minutes", unit: "minutes" },
  { key: "cost_usd", labelKey: "fg.cost", labelEn: "Cost USD at Sonnet price", unit: "usd" },
];

// The Engine figures from board.engine_samples (engine_samples.engine_window_figures).
const ENGINE_FIGURES = [
  { key: "median_tok_s", labelKey: "fg.engine_median", labelEn: "Median generation tok/s", unit: "tok_s" },
  { key: "accepted_share_percent", labelKey: "fg.engine_accepted", labelEn: "Accepted drafts %", unit: "percent" },
  { key: "expert_slots", labelKey: "fg.engine_expert_slots", labelEn: "Expert cache slots", unit: "count" },
  { key: "min_free_vram_mib", labelKey: "fg.engine_min_vram", labelEn: "Min free VRAM MiB", unit: "mib" },
  { key: "low_vram_count", labelKey: "fg.engine_low_vram", labelEn: "Low-VRAM warnings", unit: "count" },
];

// The rows tab per scope: the payload key holding the rows, the tab label, the
// columns (pick reads the value of one row, unit formats it).
const ROW_SCOPES = {
  project: {
    rowsKey: "missions",
    labelKey: "sp.tab.missions",
    labelEn: "Missions",
    emptyKey: "sp.no_missions",
    emptyEn: "No missions under this project yet.",
    columns: [
      { key: "mission_id", labelKey: "sp.h.mission", labelEn: "Mission", pick: (row) => row.mission_id },
      { key: "closed_tasks", labelKey: "sp.h.closed", labelEn: "Closed tasks", pick: (row) => figuresOf(row).closed_tasks },
      { key: "planned_tasks", labelKey: "sp.h.planned", labelEn: "Planned tasks", pick: (row) => figuresOf(row).planned_tasks },
      { key: "worker_hours", labelKey: "sp.h.hours", labelEn: "Worker hours", unit: "hours", pick: (row) => figuresOf(row).worker_hours },
      { key: "cost_usd", labelKey: "sp.h.cost", labelEn: "Cost USD", unit: "usd", pick: (row) => figuresOf(row).cost_usd },
    ],
  },
  mission: {
    rowsKey: "tasks",
    labelKey: "sp.tab.tasks",
    labelEn: "Tasks",
    emptyKey: "sp.no_tasks",
    emptyEn: "No reviewed tasks in this mission yet.",
    columns: [
      { key: "task_id", labelKey: "sp.h.task", labelEn: "Task", pick: (row) => row.task_id },
      { key: "attempts", labelKey: "sp.h.attempts", labelEn: "Attempts", pick: (row) => row.attempts },
      { key: "first_review_score", labelKey: "sp.h.first", labelEn: "First score", unit: "score", pick: (row) => row.first_review_score },
      { key: "final_review_score", labelKey: "sp.h.final", labelEn: "Final score", unit: "score", pick: (row) => row.final_review_score },
      { key: "passed", labelKey: "sp.h.passed", labelEn: "Passed", unit: "bool", pick: (row) => row.passed },
    ],
  },
  task: {
    rowsKey: "attempts",
    labelKey: "sp.tab.attempts",
    labelEn: "Attempts",
    emptyKey: "sp.no_attempts",
    emptyEn: "No attempts recorded for this task yet.",
    columns: [
      { key: "attempt", labelKey: "sp.h.attempt", labelEn: "Attempt", pick: (row) => row.attempt },
      { key: "runs", labelKey: "sp.h.runs", labelEn: "Runs", pick: (row) => (Array.isArray(row.runs) ? row.runs.length : null) },
      { key: "steps", labelKey: "sp.h.steps", labelEn: "Worker steps", pick: (row) => figuresOf(row).steps },
      { key: "output_tokens", labelKey: "sp.h.tokens", labelEn: "Output tokens", unit: "tokens", pick: (row) => figuresOf(row).output_tokens },
      { key: "tok_per_s", labelKey: "sp.h.tok_s", labelEn: "Generation tok/s", unit: "tok_s", pick: (row) => figuresOf(row).tok_per_s },
      { key: "cost_usd", labelKey: "sp.h.cost", labelEn: "Cost USD", unit: "usd", pick: (row) => figuresOf(row).cost_usd },
    ],
  },
};

// The GET url of one scope (the server validates the project key and the task id).
function statsUrl(scope) {
  if (scope.kind === "project") {
    return "/api/board/projects/" + encodeURIComponent(scope.project) + "/stats";
  }
  if (scope.kind === "task") {
    return "/api/board/missions/" + encodeURIComponent(scope.missionId) +
      "/tasks/" + encodeURIComponent(scope.taskId) + "/stats/detail";
  }
  return "/api/board/missions/" + encodeURIComponent(scope.missionId) + "/stats/detail";
}

function figuresOf(row) {
  return row && row.figures ? row.figures : {};
}

// One note line of the popup (an empty state, a reason, an error).
function noteHtml(text) {
  return `<div class="sp-note">${esc(text)}</div>`;
}

function summaryHtml(figures) {
  if (!figures) return "";
  const reasons = figures.reasons || {};
  let cards = "";
  for (const figure of SUMMARY_FIGURES) {
    const value = figures[figure.key];
    const reason = value === null || value === undefined
      ? (reasons[figure.key] || L("sp.no_value", "no value yet"))
      : "";
    cards += `<div class="ew-sum"><span>${esc(L(figure.labelKey, figure.labelEn))}</span>` +
      `<b title="${esc(reason)}">${esc(formatValue(value, figure.unit))}</b></div>`;
  }
  return "<div class=\"ew-sumgrid\">" + cards + "</div>";
}

function rowsHtml(kind, figures) {
  const spec = ROW_SCOPES[kind];
  if (!spec) return "";
  const rows = Array.isArray(figures[spec.rowsKey]) ? figures[spec.rowsKey] : [];
  if (rows.length === 0) return noteHtml(L(spec.emptyKey, spec.emptyEn));
  const head = spec.columns.map((column) =>
    `<th>${esc(L(column.labelKey, column.labelEn))}</th>`
  ).join("");
  let body = "";
  for (const row of rows) {
    let cells = "";
    for (const column of spec.columns) {
      const value = column.pick(row);
      const reason = value === null || value === undefined
        ? (figuresOf(row).reasons || {})[column.key] || ""
        : "";
      cells += `<td title="${esc(reason)}">${esc(formatValue(value, column.unit))}</td>`;
    }
    body += "<tr class=\"sp-row\">" + cells + "</tr>";
  }
  return "<table class=\"sp-table\"><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
}

// The sources of engine_samples rows (docs/board-schema.sql:164-165), spelled out
// so the Engine tab never shows a raw source key.
const SOURCE_LABELS = {
  sampled: { key: "sp.src.sampled", en: "live sampler" },
  "backfill-matched": { key: "sp.src.backfill_matched", en: "backfilled from stored runs" },
  "backfill-segment": { key: "sp.src.backfill_segment", en: "backfilled from log segments" },
};

// The server answers with {"engine": null, "engine_reason": "no samples"} when the
// sampler is off or the window holds no rows (board_api._engine_block), so one
// note covers both cases and no zeros are ever printed.
function engineHtml(figures) {
  const engine = figures ? figures.engine : null;
  if (!engine) {
    return noteHtml(L("sp.no_samples", "No engine samples for the window of these runs.") +
      " " + ((figures && figures.engine_reason) || ""));
  }
  let cards = "";
  for (const figure of ENGINE_FIGURES) {
    const value = engine[figure.key];
    const reason = value === null || value === undefined ? L("sp.no_value", "no value yet") : "";
    cards += `<div class="ew-sum"><span>${esc(L(figure.labelKey, figure.labelEn))}</span>` +
      `<b title="${esc(reason)}">${esc(formatValue(value, figure.unit))}</b></div>`;
  }
  const sources = engine.samples_by_source || {};
  const parts = Object.keys(sources).map((source) => {
    const label = SOURCE_LABELS[source] || { key: "sp.src.unknown", en: "unknown source" };
    return L(label.key, label.en) + " (" + source + ") " + String(sources[source]);
  });
  const line = parts.length
    ? noteHtml(L("sp.sources", "Samples in the window") + ": " + parts.join(" · "))
    : "";
  return "<div class=\"ew-sumgrid\">" + cards + "</div>" + line;
}

function errorHtml(message) {
  return `<div class="ew-warn">${esc(message)}</div>` +
    `<button type="button" class="sp-retry">${esc(L("sp.retry", "Try again"))}</button>`;
}

function tabHtml(active, scope) {
  const tabs = [
    ["summary", L("sp.tab.summary", "Summary")],
    ["rows", L(ROW_SCOPES[scope.kind].labelKey, ROW_SCOPES[scope.kind].labelEn)],
    ["engine", L("sp.tab.engine", "Engine")],
  ];
  let out = "";
  for (const [key, label] of tabs) {
    const cls = key === active ? "sp-tab on" : "sp-tab";
    out += `<button type="button" class="${esc(cls)}" data-tab="${esc(key)}">${esc(label)}</button>`;
  }
  return out;
}

function tabBody(state) {
  if (state.loading) {
    // the load is bounded: after LOAD_TIMEOUT_MS the note says the request is slow
    return noteHtml(state.timedOut
      ? L("sp.slow", "Loading… the request is slow, try again")
      : L("sp.loading", "Loading…"));
  }
  if (state.error) return errorHtml(state.error);
  if (state.tab === "rows") return rowsHtml(state.scope.kind, state.figures);
  if (state.tab === "engine") return engineHtml(state.figures);
  return summaryHtml(state.figures);
}

// The modal shell: the reuse of the launch-settings window (.ew-scrim/.ew-win)
// with the close button and the tab bar.
function shellHtml(title) {
  return `<div class="ew-win sp-win" id="stats-win">` +
    `<div class="ew-head"><h3>${esc(title)}</h3><span class="ew-sp"></span>` +
    `<button type="button" class="ew-ib sp-close" title="${esc(L("ew.close", "Close (Esc)"))}">✕</button>` +
    `</div><div class="sp-tabs" role="tablist"></div><div class="ew-body sp-body"></div></div>`;
}

let scrim = null;
let state = null;
let previousFocus = null;
let isOpen = false;

// The load of the popup is bounded: the fetch never hangs on the board, and the
// timer is cleared when the popup closes or the figures arrive.
const LOAD_TIMEOUT_MS = 8000; // milliseconds
let loadTimer = null;

function clearLoadTimer() {
  if (loadTimer === null) return;
  clearTimeout(loadTimer);
  loadTimer = null;
}

function startLoadTimer() {
  clearLoadTimer();
  loadTimer = setTimeout(() => {
    loadTimer = null;
    if (!state) return;
    state.timedOut = true;
    renderPopup();
  }, LOAD_TIMEOUT_MS);
}

// The feature flags that hide the button when stats_popup is off: the board flags
// (one GET) and the merged flags of a mission (one GET), cached with a bound size
// so a long-lived board does not grow the cache unbounded.
const FLAG_CACHE_LIMIT = 20;
const missionFlagCache = new Map();
let boardFlagsCache = null;

function mergedFeatures(globalText, missionText) {
  const parsed = popupFeatures.parseFeatures(globalText || "", missionText || "");
  return parsed.features || {};
}

async function scopeFlags(scope) {
  if (scope.kind === "project") {
    if (boardFlagsCache === null) {
      const payload = await fetchJson("/api/board/features");
      boardFlagsCache = mergedFeatures(payload.features_json, "");
    }
    return boardFlagsCache;
  }
  if (missionFlagCache.has(scope.missionId)) return missionFlagCache.get(scope.missionId);
  const globalPayload = await fetchJson("/api/board/features");
  const missionPayload = await fetchJson(
    "/api/board/missions/" + encodeURIComponent(scope.missionId) + "/features"
  );
  const features = mergedFeatures(globalPayload.features_json, missionPayload.mission_features_json);
  if (missionFlagCache.size >= FLAG_CACHE_LIMIT) {
    missionFlagCache.delete(missionFlagCache.keys().next().value);
  }
  missionFlagCache.set(scope.missionId, features);
  return features;
}

function ensureScrim() {
  if (scrim) return scrim;
  const root = globalThis.document;
  const host = root.getElementById("board") || root.body;
  scrim = root.createElement("div");
  scrim.id = "stats-scrim";
  scrim.className = "ew-scrim sp-scrim";
  scrim.innerHTML = shellHtml(L("th.stats", "Statistics"));
  scrim.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeStats();
  });
  // a click on a tab button bubbles to the scrim, so one handler covers all three
  scrim.addEventListener("click", (event) => {
    const tab = event && event.target && event.target.dataset && event.target.dataset.tab;
    if (!tab || !state) return;
    state.tab = tab;
    renderPopup();
  });
  const close = scrim.querySelector(".sp-close");
  if (close) close.addEventListener("click", closeStats);
  host.appendChild(scrim);
  return scrim;
}

function renderPopup() {
  const modal = ensureScrim();
  const tabs = modal.querySelector(".sp-tabs");
  const body = modal.querySelector(".sp-body");
  tabs.innerHTML = tabHtml(state.tab, state.scope);
  body.innerHTML = tabBody(state);
  if (state.error) {
    const retry = modal.querySelector(".sp-retry");
    if (retry) retry.addEventListener("click", () => loadStats(state.scope));
  }
}

async function loadStats(scope) {
  const current = {
    scope, tab: "summary", figures: null, error: null, loading: true, timedOut: false,
  };
  state = current;
  renderPopup();
  startLoadTimer();
  try {
    current.figures = await fetchJson(statsUrl(scope));
    current.error = null;
  } catch (error) {
    current.error = error && error.message ? error.message : String(error);
  }
  // the popup was closed or reopened while the fetch was in flight: the stale
  // result renders into nothing and its timer is already gone
  if (state !== current) return;
  clearLoadTimer();
  current.loading = false;
  renderPopup();
}

// openStats(scope, trigger): the button that opened the popup, so Esc and the
// close button return the focus to it.
function openStats(scope, trigger) {
  const modal = ensureScrim();
  previousFocus = trigger || globalThis.document.activeElement;
  isOpen = true;
  modal.className = "ew-scrim sp-scrim open";
  loadStats(scope);
  const close = modal.querySelector(".sp-close");
  if (close && typeof close.focus === "function") close.focus();
}

// closeStats is idempotent: the second call clears nothing and does not focus.
function closeStats() {
  if (!isOpen) return;
  isOpen = false;
  clearLoadTimer();
  if (scrim) scrim.className = "ew-scrim sp-scrim";
  state = null;
  if (previousFocus && typeof previousFocus.focus === "function") previousFocus.focus();
  previousFocus = null;
}

// attach(host, scope): the Statistics button, only when the flag says so.  The
// host gets the button once (dataset.statsAttached), so a re-rendered view does
// not add a second one.  Returns false when the flag is off.
async function attach(host, scope) {
  if (!host || !ROW_SCOPES[scope.kind] || typeof host.appendChild !== "function") return false;
  if (host.dataset && host.dataset.statsAttached === "1") return true;
  let features;
  try {
    features = await scopeFlags(scope);
  } catch (error) {
    // the flags cannot be read: no button, the reason logged, the board stays usable
    console.warn("stats popup: flags unreadable for", scope.kind, String(error.message || error));
    return false;
  }
  if (!features.stats_popup || !features.stats_popup.enabled) return false;
  const button = globalThis.document.createElement("button");
  button.type = "button";
  button.className = "sp-btn";
  button.textContent = L("th.stats", "Statistics");
  button.addEventListener("click", () => openStats(scope, button));
  host.appendChild(button);
  if (host.dataset) host.dataset.statsAttached = "1";
  return true;
}

const BoardStatsPopup = {
  SUMMARY_FIGURES,
  ENGINE_FIGURES,
  ROW_SCOPES,
  SOURCE_LABELS,
  FLAG_CACHE_LIMIT,
  LOAD_TIMEOUT_MS,
  formatValue,
  statsUrl,
  summaryHtml,
  rowsHtml,
  engineHtml,
  tabHtml,
  tabBody,
  shellHtml,
  attach,
  openStats,
  closeStats,
  loadStats,
  clearLoadTimer,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardStatsPopup;
} else if (typeof document !== "undefined") {
  globalThis.BoardStatsPopup = BoardStatsPopup;
}
})();
