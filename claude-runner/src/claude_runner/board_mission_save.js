/* board-scope-wrapped */
(function () {
/* Board mission form save / reset / copy (T020A).
 *
 * Loaded by board.html via <script src="/static/board_mission_save.js"> and
 * usable from node via CommonJS require (UMD-style export below).  It attaches
 * to the "Конфигурация запуска" form that board_mission.js renders in
 * #boardmission and adds three controls:
 *
 *   - "Сохранить"  — shows the diff from the initial values
 *     (BoardLogic.diffConfig), then asks for confirmation before POSTing to
 *     the board API.
 *   - "Сбросить"   — restores every field to its initial value.
 *   - "Скопировать из другой миссии" — copies another mission's fields into
 *     this form (the user then confirms saving as usual).
 *
 * A dirty form is never overwritten by a refresh: refresh() re-fetches the
 * server data and, when the form has unsaved edits, merges it with
 * BoardLogic.mergeServerIntoForm() and shows the "на сервере есть изменения"
 * banner when the server queue differs.  A 409 is shown as
 * "план изменился, обновите".
 *
 * Every value from data goes through esc() or textContent; data attributes
 * are read through dataset.  No external libraries, fonts or outbound
 * requests beyond the same-origin board API.
 */
"use strict";

// esc() from board_logic.js, with the same local fallback the other views use.
function esc(v) {
  if (typeof BoardLogic !== "undefined" && typeof BoardLogic.esc === "function") {
    return BoardLogic.esc(v);
  }
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// State: the current mission id, the initial field values captured at render
// time, the last server payload, and the last diff shown.
const SaveState = {
  missionId: null,
  initial: {},      // { key: value } the values the form was rendered with
  data: null,       // last server payload { mission, config, queue, version }
  lastDiff: [],     // last BoardLogic.diffConfig result
};

// --- pure helpers ---

// Parse one textarea's newline-separated lines into an array (what
// board_mission.js's fmtLines renders listarea values as).  JSON-looking
// lines are parsed back; the rest stay strings.
function parseLines(text) {
  const s = text == null ? "" : String(text);
  if (s === "") return [];
  return s.split("\n").map((line) => {
    const t = line.trim();
    if (t === "") return null;
    if (t[0] === "[" || t[0] === "{") {
      try { return JSON.parse(t); } catch (_) { return t; }
    }
    return t;
  }).filter((x) => x !== null);
}

// Read the current form values into a flat { key: value } map, mirroring the
// shapes board_mission.js's formHtml renders (listareas are arrays, the bool
// is a boolean, the json field is parsed, number inputs are numbers).
function readForm(box) {
  const out = {};
  for (const el of box.querySelectorAll("[data-key]")) {
    const kind = el.dataset.control || el.dataset.kind;  // the control kind (listarea/json/...); old markup: the data kind
    const key = el.dataset.key;
    const shown = el.type === "checkbox" ? el.checked : el.value;
    if (el.type === "checkbox") {
      out[key] = shown;
    } else if (kind === "listarea") {
      out[key] = String(shown).split("\n").map((l) => l.trim()).filter((l) => l !== "");
    } else if (kind === "json") {
      const t = shown.trim();
      try { out[key] = t === "" ? null : JSON.parse(t); }
      catch (_) { out[key] = t; }
    } else if (el.type === "number") {
      const t = shown.trim();
      out[key] = t === "" ? null : Number(t);
    } else {
      out[key] = String(shown);
    }
  }
  return out;
}

// One field's initial value, taken from the server data the form was rendered
// from: mission row fields (name, objective, rules, acceptance_criteria,
// working_directory), the queue entry (enabled, attempts_per_task) and the
// config object.  The value is stored in the same shape readForm() produces,
// so the dirty check and the diff compare apples-to-apples: listareas as
// arrays, the bool as a boolean, the json field as the parsed value, number
// fields as numbers, the rest as strings.
function initialValue(kind, key, data, control) {
  const mission = data.mission || {};
  const queueEntry = (data.queue || []).find((e) => e.mission_id === mission.mission_id) || {};
  const config = data.config || {};
  // "listarea" is the control kind for mission-row list fields (rules,
  // acceptance_criteria) and config list fields (retrieval_corpora, hints,
  // required_files).  Look them up in their source: mission or config.
  const ctl = control || kind;
  const v = kind === "mission" ? mission[key]
    : kind === "queue" ? (queueEntry[key] != null ? queueEntry[key] : (key === "enabled" ? mission.enabled : undefined))
    : kind === "listarea" ? (data.mission ? data.mission[key] : config[key])
    : config[key];
  if (ctl === "bool") return v === true || String(v).toLowerCase() === "true";
  if (ctl === "number") return v == null || v === "" ? null : Number(v);
  if (ctl === "text") return v == null ? "" : String(v).replace(/\r\n?/g, "\n");
  if (ctl === "listarea") {
    if (v == null) return [];
    if (Array.isArray(v)) return v;
    if (typeof v === "string") return v === "" ? [] : parseLines(v);
    return [v];
  }
  if (ctl === "json") {
    if (v == null) return null;
    if (typeof v === "string") {
      try { return JSON.parse(v); } catch (_) { return v; }
    }
    return JSON.parse(JSON.stringify(v));
  }
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v;
  if (typeof v === "object" && v !== null) return v;
  // a textarea turns CRLF into LF when the form is read: compare in that shape (the CSV import keeps CRLF)
  return v == null ? "" : String(v).replace(/\r\n?/g, "\n");
}

// Capture the initial values the moment the form is rendered (the form's
// dataset keys carry kind/key, so the initial values come from the server
// data without re-parsing the markup).
function captureInitial(box, data) {
  SaveState.missionId = data && data.mission ? data.mission.mission_id : null;
  SaveState.data = data;
  const initial = {};
  for (const el of box.querySelectorAll("[data-key]")) {
    initial[el.dataset.key] = initialValue(el.dataset.kind, el.dataset.key, data, el.dataset.control);
  }
  SaveState.initial = initial;
}

// True while any field differs from its initial value.  Both sides are
// compared in the canonical shape readForm() produces: listareas as
// arrays, the bool as a boolean, the json field as the parsed value,
// numbers as numbers, the rest as strings.
function formDirty(box) {
  const cur = readForm(box);
  const initial = SaveState.initial;
  const keys = new Set([...Object.keys(initial), ...Object.keys(cur)]);
  for (const k of keys) {
    if (JSON.stringify(initial[k]) !== JSON.stringify(cur[k])) return true;
  }
  return false;
}

// The diff from the initial values to the current form values
// (BoardLogic.diffConfig: top-level key comparison, JSON deep-equality).
function currentDiff(box) {
  const diffConfig = typeof BoardLogic !== "undefined" && BoardLogic.diffConfig
    ? BoardLogic.diffConfig
    : (a, b) => {
      const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
      const out = [];
      for (const k of keys) {
        if (JSON.stringify((a || {})[k]) !== JSON.stringify((b || {})[k])) {
          out.push({ field: k, old: (a || {})[k], new: (b || {})[k] });
        }
      }
      return out.sort((x, y) => x.field.localeCompare(y.field));
    };
  return diffConfig(SaveState.initial, readForm(box));
}

// Render the diff as escaped HTML (one row per changed field).
function diffHtml(diff, labels) {
  if (!diff || diff.length === 0) {
    return `<div class="savediff none">${esc(t("save.no_changes"))}</div>`;
  }
  const fmt = (v) => (v === null || v === undefined
    ? "—"
    : Array.isArray(v) ? v.map((x) => String(x)).join(", ")
    : typeof v === "object" ? JSON.stringify(v)
    : String(v));
  const rows = diff.map((d) => {
    const label = labels[d.field] || d.field;
    return `<div class="savediff-row"><b>${esc(label)}</b>: ` +
      `<span class="old">${esc(fmt(d.old))}</span> → ` +
      `<span class="new">${esc(fmt(d.new))}</span></div>`;
  });
  return `<div class="savediff">` + rows.join("") + `</div>`;
}

// Features state from the two GET payloads (GET /api/board/features and
// GET /api/board/missions/{id}/features).  An absent payload gives empty texts
// and null versions, so a board without features reads as an empty state.
function featuresState(profile, mission) {
  const p = profile || {};
  const m = mission || {};
  return {
    features_json: p.features_json == null ? "" : String(p.features_json),
    features_version: p.version == null ? null : p.version,
    mission_features_json: m.mission_features_json == null ? "" : String(m.mission_features_json),
    mission_features_version: m.version == null ? null : m.version,
  };
}

// The save plan for the two features texts: the diff from the state (shown
// before the confirm) and one PUT per changed text, each carrying the version
// it was read at (expected_version, the server's optimistic lock).
function featuresSavePlan(state, missionId, current) {
  const fields = [
    { key: "features_json", url: "/api/board/features", version: state.features_version },
    { key: "mission_features_json",
      url: "/api/board/missions/" + encodeURIComponent(missionId) + "/features",
      version: state.mission_features_version },
  ];
  const diff = [];
  const requests = [];
  for (const f of fields) {
    const next = current[f.key] == null ? "" : String(current[f.key]);
    if (next === state[f.key]) continue;
    diff.push({ field: f.key, old: state[f.key], new: next });
    requests.push({ url: f.url, method: "PUT",
      headers: { "X-AHAWR-Dashboard": "1" },
      body: { [f.key]: next, expected_version: f.version } });
  }
  return { diff, requests };
}

// --- panel and banner ---

// The panel (banner + diff + button bar) is created once, inside the confbox.
function ensurePanel(box) {
  let el = box.querySelector(".savepanel");
  if (el) return el;
  el = document.createElement("div");
  el.className = "savepanel";
  const banner = document.createElement("div");
  banner.className = "savebanner";
  banner.hidden = true;
  el.appendChild(banner);
  const diffBox = document.createElement("div");
  diffBox.className = "savediffbox";
  diffBox.hidden = true;
  el.appendChild(diffBox);
  box.prepend(el);
  return el;
}

// Show (or clear) the banner.  textContent only — no innerHTML.  Kept on the
// module export so node tests can override it with a stub.
function showBanner(box, text) {
  const el = ensurePanel(box);
  const banner = el.querySelector(".savebanner");
  banner.hidden = !text;
  banner.textContent = text || "";
}

// --- attach the controls to the form (idempotent via dataset.saveReady) ---

function attachControls(box) {
  if (box.dataset.saveReady === "1") return;
  box.dataset.saveReady = "1";
  const el = ensurePanel(box);
  const bar = document.createElement("div");
  bar.className = "savebar";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "savebtn";
  saveBtn.textContent = t("save.button");
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "savebtn";
  resetBtn.textContent = t("save.reset");
  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "savebtn";
  copyBtn.textContent = t("save.copy_from");
  saveBtn.addEventListener("click", () => onSave(box));
  resetBtn.addEventListener("click", () => onReset(box));
  copyBtn.addEventListener("click", () => onCopy(box));
  bar.appendChild(saveBtn);
  bar.appendChild(resetBtn);
  bar.appendChild(copyBtn);
  el.appendChild(bar);
}

// Called by board_mission.js's BoardMission() after each render: capture the
// initial values and attach the controls to the freshly rendered form.
function attachToBox(box, data) {
  if (!box) return;
  captureInitial(box, data);
  attachControls(box);
}

// --- "Сохранить": show the diff, then confirm, then POST ---

async function onSave(box) {
  const diff = currentDiff(box);
  SaveState.lastDiff = diff;
  const panel = ensurePanel(box);
  const diffBox = panel.querySelector(".savediffbox");
  diffBox.hidden = false;
  const labels = {};
  for (const el of box.querySelectorAll("[data-key]")) {
    const wrap = el.closest(".frow-wrap");
    const labelEl = wrap && wrap.querySelector(".frow-label");
    labels[el.dataset.key] = labelEl ? labelEl.textContent : el.dataset.key;
  }
  diffBox.innerHTML = diffHtml(diff, labels);
  if (diff.length === 0) {
    showBanner(box, t("save.nothing_to_save"));
    return;
  }
  if (!window.confirm(t("save.confirm"))) {
    showBanner(box, "");
    return;
  }
  await doSave(box);
}

// POST the dirty fields to the board API: mission-row fields to
// /missions/{id}, config fields to /missions/{id}/config.  A 409 shows
// "план изменился, обновите" and re-fetches.
async function doSave(box) {
  const cur = readForm(box);
  const initial = SaveState.initial;
  const missionId = SaveState.missionId;
  const data = SaveState.data;
  const missionBody = {};
  const configBody = {};
  for (const key of Object.keys(cur)) {
    if (JSON.stringify(initial[key]) === JSON.stringify(cur[key])) continue;
    const kind = kindOf(box, key);
    if (kind === "config") {
      configBody[key] = cur[key];
    } else if (kind === "queue" && key === "enabled") {
      // enabled is the mission's flag; the store's queue route reads it from
      // the mission row, so it is sent as a mission field.
      missionBody.enabled = cur[key];
    } else if (kind === "queue") {
      // attempts_per_task lives on the queue entry, not in the mission config;
      // no board route edits it from this form, so it is not posted.
      continue;
    } else {
      missionBody[key] = cur[key];
    }
  }
  try {
    if (Object.keys(missionBody).length) {
      await postJson("/api/board/missions/" + encodeURIComponent(missionId),
        Object.assign({ expected_version: data && data.mission ? data.mission.updated_at : null }, missionBody));
    }
    if (Object.keys(configBody).length) {
      await postJson("/api/board/missions/" + encodeURIComponent(missionId) + "/config",
        Object.assign({ expected_version: data && data.version ? data.version : null }, configBody));
    }
  } catch (e) {
    if (e.status === 409) {
      showBanner(box, t("conflict.plan_changed"));
      await refresh(box, t("conflict.plan_changed"));
    } else {
      showBanner(box, e.message || t("save.failed"));
      await refresh(box);
    }
    return;
  }
  // Success: re-fetch the saved state and force a clean re-render
  // (renderMission + captureInitial + attachControls), so formDirty(box)
  // is false right after the save.
  const fresh = await fetchServerData(missionId);
  renderClean(box, fresh);
  showBanner(box, "");
}

// The kind (mission/queue/config) of a key, read from the form's dataset.
function kindOf(box, key) {
  const el = box.querySelector('[data-key="' + key + '"]');
  return el ? el.dataset.kind : "mission";
}

// --- "Сбросить": restore every field to its initial value ---

function onReset(box) {
  const initial = SaveState.initial;
  for (const el of box.querySelectorAll("[data-key]")) {
    const key = el.dataset.key;
    const kind = el.dataset.kind;
    const v = initial[key];
    if (el.type === "checkbox") {
      el.checked = v === true;
    } else if (kind === "listarea") {
      el.value = Array.isArray(v) ? v.map((x) => String(x)).join("\n")
        : (v == null ? "" : String(v));
    } else if (kind === "json") {
      el.value = v == null ? "" : JSON.stringify(v, null, 2);
    } else {
      el.value = v == null ? "" : String(v);
    }
  }
  SaveState.lastDiff = [];
  showBanner(box, "");
  const panel = ensurePanel(box);
  panel.querySelector(".savediffbox").hidden = true;
}

// --- "Скопировать из другой миссии": copy another mission's fields ---

// Ask for the source mission id, load it, and copy its editable fields into
// this form (the user then confirms saving as usual).
function onCopy(box) {
  const id = window.prompt(t("save.copy_prompt"));
  if (!id) return;
  const source = id.trim();
  if (!source) return;
  loadSource(source).then((data) => {
    copyIntoForm(box, data);
  }).catch((e) => showBanner(box, e.message || t("save.load_failed")));
}

// Load the source mission and its queue entry (same endpoints as
// board_mission.js's BoardMission).
async function loadSource(id) {
  const detail = await fetchJson("/api/board/missions/" + encodeURIComponent(id));
  const queueData = await fetchJson("/api/board/queue");
  return {
    mission: detail.mission,
    config: detail.config || {},
    queue: queueData.queue,
  };
}

// Copy the source mission's editable fields into the form (the same fields
// the form renders).
function copyIntoForm(box, data) {
  const mission = data.mission || {};
  const queueEntry = (data.queue || []).find((e) => e.mission_id === mission.mission_id) || {};
  const config = data.config || {};
  for (const el of box.querySelectorAll("[data-key]")) {
    const key = el.dataset.key;
    const kind = el.dataset.kind;
    let v;
    if (kind === "mission") v = mission[key];
    else if (kind === "queue") v = queueEntry[key];
    else v = config[key];
    if (el.type === "checkbox") {
      el.checked = v === true;
    } else if (kind === "listarea") {
      el.value = Array.isArray(v) ? v.map((x) => String(x)).join("\n")
        : (v == null ? "" : String(v));
    } else if (kind === "json") {
      el.value = v == null ? "" : JSON.stringify(v, null, 2);
    } else {
      el.value = v == null ? "" : String(v);
    }
  }
  showBanner(box, t("save.copied"));
}

// --- refresh: never overwrite a dirty form; show the banner on drift ---

// Re-fetch the server data for one mission (shared by refresh and doSave).
async function fetchServerData(missionId) {
  const detail = await fetchJson("/api/board/missions/" + encodeURIComponent(missionId));
  const queueData = await fetchJson("/api/board/queue");
  return {
    mission: detail.mission,
    config: detail.config || {},
    defaults: detail.defaults || {},
    queue: queueData.queue,
    version: queueData.version,
  };
}

// Forced clean re-render: re-render the form from the server data
// (board_mission.js's renderMission), capture the initial values and attach
// the controls — formDirty(box) is false afterwards.
function renderClean(box, data) {
  try {
    if (typeof BoardMissionView !== "undefined" && BoardMissionView.renderMission) {
      box.innerHTML = BoardMissionView.renderMission(data);
    }
  } catch (e) { /* keep the existing form */ }
  // The wiped form no longer has the panel or bar; drop the attach guard
  // so attachControls re-runs on the fresh form.
  delete box.dataset.saveReady;
  captureInitial(box, data);
  attachControls(box);
}

// Re-fetch the server data for this mission.  When the form is dirty, merge
// with BoardLogic.mergeServerIntoForm() (keeping the local edits, taking the
// server version, flagging server_changed when the queue drifted) and show
// the banner; when clean, re-render the form fresh from the server.  When
// keepMessage is set (the caller already set a banner, e.g. the 409 message
// "план изменился, обновите"), refresh does not overwrite it; it keeps the
// "на сервере есть изменения" note alongside it when the server queue
// drifted.
async function refresh(box, keepMessage) {
  const missionId = SaveState.missionId;
  if (!missionId) return;
  let data;
  try {
    data = await fetchServerData(missionId);
  } catch (e) {
    showBanner(box, e.message || t("save.update_failed"));
    return;
  }
  const local = SaveState.data;
  SaveState.data = data;

  if (!formDirty(box)) {
    // Clean form: re-render from the server (board_mission.js's renderMission).
    renderClean(box, data);
    if (!keepMessage) showBanner(box, "");
    return;
  }

  // Dirty form: merge the server refresh into the form without losing the
  // local edits (board_logic.js's mergeServerIntoForm).  The locally-edited
  // fields keep their values; the queue and version carry over from the
  // server.  The banner appears when the server queue differs from the
  // local one (the last server state the form knew).
  const form = readForm(box);
  form.version = data.version;
  form.queue = local && local.queue ? local.queue : data.queue;
  form.dirty_fields = Object.keys(form).filter((k) =>
    k !== "version" && k !== "queue" &&
    JSON.stringify(form[k]) !== JSON.stringify(SaveState.initial[k]));
  const mergeServer = typeof BoardLogic !== "undefined" && BoardLogic.mergeServerIntoForm
    ? BoardLogic.mergeServerIntoForm
    : (f, s) => {
      const result = Object.assign({}, f);
      const dirty = new Set(f.dirty_fields || []);
      const keys = new Set([...Object.keys(f), ...Object.keys(s)]);
      for (const key of keys) {
        if (key === "dirty_fields") continue;
        if (dirty.has(key)) continue;
        if (key in s) result[key] = s[key];
      }
      const ak = (f.queue || []).map((e) => e.mission_id).join("|");
      const bk = (s.queue || []).map((e) => e.mission_id).join("|");
      if (ak !== bk) result.server_changed = true;
      if (ak === bk && "server_changed" in result) delete result.server_changed;
      return result;
    };
  const merged = mergeServer(form, data);
  SaveState.data = merged;
  if (keepMessage) {
    // Keep the caller's banner; add the server note alongside it when the
    // queue drifted.
    if (merged.server_changed) {
      showBanner(box, t("save.server_changed_kept", { message: keepMessage }));
    }
    return;
  }
  if (merged.server_changed) {
    showBanner(box, t("save.server_changed"));
  } else {
    showBanner(box, "");
  }
}

// One same-origin GET helper (the board API only).
async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body && body.error && body.error.message
      ? body.error.message : "HTTP " + res.status);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// Send a JSON body to one endpoint (POST, or PUT for the roles save); a 409 raises the "план изменился"
// message, like board_actions.js's postJson.  Board mutation routes require
// the X-AHAWR-Dashboard header, so every mutation carries it.
async function postJson(url, body, method = "POST", headers = {}) {
  const res = await fetch(url, {
    method,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-AHAWR-Dashboard": "1",
      ...headers,
    },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (res.status === 409) {
    const err = new Error((payload.error && payload.error.message) || t("conflict.plan_changed"));
    err.status = 409;
    throw err;
  }
  if (!res.ok) {
    const err = new Error((payload.error && payload.error.message) || payload.code
      || "HTTP " + res.status);
    err.status = res.status;
    err.body = payload;
    throw err;
  }
  return payload;
}

// === export (browser global + CommonJS module.exports) ===
const BoardMissionSave = {
  esc, SaveState, readForm, parseLines, initialValue,
  captureInitial, formDirty, currentDiff, diffHtml, featuresState, featuresSavePlan,
  ensurePanel, showBanner, attachControls, attachToBox,
  onSave, onReset, onCopy, copyIntoForm, doSave,
  fetchServerData, renderClean, refresh, fetchJson, postJson,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardMissionSave;
} else if (typeof document !== "undefined") {
  globalThis.BoardMissionSave = BoardMissionSave;
}
})();
