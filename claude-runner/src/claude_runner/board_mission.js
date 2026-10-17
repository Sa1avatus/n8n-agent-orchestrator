/* board-scope-wrapped */
(function () {
/* Board mission view and run configuration form (T020).
 *
 * Loaded by board.html via <script src="/static/board_mission.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardMission(mission_id) is the render function the shell (board.js) calls
 * for the #board/<mission_id> fragment: it loads GET /api/board/missions/{id}
 * and GET /api/board/queue and renders #boardmission — the breadcrumbs
 * "Миссии / <имя>", the mission summary and the editable
 * "Конфигурация запуска" form.  The pure renderMission(data) builds the
 * view from plain objects matching the /api/board responses, so it can be
 * tested in node without a DOM.
 *
 * Data shape (assembled by BoardMission):
 *   mission:  { mission_id, name, objective, working_directory, enabled,
 *               rules, acceptance_criteria, retrieval_corpora, ... }
 *              // GET /api/board/missions/{id}
 *   queue:    [ { position, mission_id, enabled, attempts_per_task, ... } ]
 *              // GET /api/board/queue
 *   config:   { worker_model, ..., worker_think_policy, hints, required_files }
 *              // the mission config object in the same GET
 *   groups:   { running: [id, ...], ... }
 *              // derived in the view from live_state.status === "active"
 *
 * Every value taken from server data flows through esc() (board_logic.js's
 * esc, with a local fallback when that script failed to load); data
 * attributes are set through dataset.  Saving is the next task (T030).
 */
"use strict";

// esc() from board_logic.js; a local fallback keeps the view safe when that
// script has not loaded.
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

// Config keys editable while the mission is running: they take effect from
// the next task onwards (the workflow re-reads the mission config at every
// run start, so an in-flight run keeps its own values).  Mirrors
// board_plan.py's MIDRUN_CONFIG_FIELDS.
const MIDRUN_CONFIG_FIELDS = [
  "worker_model", "worker_provider",
  "reviewer_model", "reviewer_provider",
  "service_model", "service_provider",
  "worker_max_polls", "reviewer_max_polls", "architect_max_polls",
  "worker_poll_seconds", "reviewer_poll_seconds", "architect_poll_seconds",
  "worker_max_retries", "reviewer_max_retries", "architect_max_retries",
  "retry_delays", "max_attempts_per_task", "max_tasks",
  "worker_think_policy", "worker_think_policy_from_task",
  "worker_think_off_after_seconds", "worker_think_off_after_steps",
];

// Config keys a running mission may not change (board_plan.py's
// NON_EDITABLE_CONFIG_FIELDS); the Russian explanation goes next to the
// greyed field.
const NON_EDITABLE_CONFIG_FIELDS = {
  working_directory:
    "миссия идёт: рабочая папка привязана к живому сеансу Worker и не меняется во время прогона",
  state_namespace:
    "миссия идёт: ключ состояния указывает на живую строку и не меняется во время прогона",
  retrieval_corpora:
    "миссия идёт: корпуса поиска уходят в живой запрос прогона и не меняются во время прогона",
};

// "Конфигурация запуска" form layout: one row per [kind, key, label].
// kind "mission" — the value comes from the mission row;
// kind "queue"   — the value comes from the queue entry;
// kind "config"  — the value comes from the mission config object.
// The key is the config field name (mission_row fields use their row name).
const FORM_ROWS = [
  ["mission", "name", "название", "text"],
  ["mission", "objective", "цель", "textarea"],
  ["mission", "rules", "правила", "listarea"],
  ["mission", "acceptance_criteria", "критерии приёмки", "listarea"],
  ["mission", "working_directory", "рабочая папка", "text"],
  ["queue", "enabled", "включена", "bool"],
  ["queue", "attempts_per_task", "попыток на задачу (3/5/10)", "number"],
  ["config", "worker_model", "Worker: модель", "text"],
  ["config", "worker_provider", "Worker: провайдер", "text"],
  ["config", "reviewer_model", "Reviewer: модель", "text"],
  ["config", "reviewer_provider", "Reviewer: провайдер", "text"],
  ["config", "architect_model", "Architect: модель", "text"],
  ["config", "architect_provider", "Architect: провайдер", "text"],
  ["config", "max_tasks", "максимум задач", "number"],
  ["config", "worker_think_policy", "Worker: политика thinking", "json"],
  ["config", "worker_think_policy_from_task", "Worker: thinking от задачи", "number"],
  ["config", "retrieval_corpora", "корпуса поиска", "listarea"],
  ["config", "hints", "подсказки", "listarea"],
  ["config", "required_files", "required_files", "listarea"],
];

// Presets for "попыток на задачу".
const ATTEMPT_PRESETS = [3, 5, 10];

// Format a list value (rules / acceptance_criteria / corpora / hints /
// required_files) as one item per line for the "listarea" textarea:
// arrays join with newlines; an object item uses its string field
// (text/title/name) or JSON.stringify.
function fmtLines(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((x) => {
      if (typeof x === "object" && x !== null) {
        const field = ["text", "title", "name"].find((f) => typeof x[f] === "string");
        return field ? x[field] : JSON.stringify(x);
      }
      return String(x == null ? "" : x);
    }).join("\n");
  }
  return String(value);
}

// Format the worker thinking policy for the "json" textarea: a string
// stays as-is; an object or array renders as pretty JSON, never String(obj).
function fmtJson(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

// The greyed-field note for a running mission.  Only fields not applicable
// from the next task are greyed: the NON_EDITABLE config fields (working
// directory, state namespace, retrieval corpora) — every other field takes
// effect from the next task and stays editable.
function greyNote(key) {
  return NON_EDITABLE_CONFIG_FIELDS[key] || null;
}

// One form row: label, the value input, and (when running and the field is
// not applicable from the next task) the greyed note.  Returns the escaped
// HTML string of the row; the input's dataset carries kind/key so T030 can
// read the fields back without re-parsing the markup.
function fieldRow(kind, key, label, control, shown, running, reason) {
  const disabledAttr = running && reason ? " disabled" : "";
  const greyed = running && reason ? " greyed" : "";
  const rowCls = "frow-wrap" + (greyed ? " disabled" : "");
  let inputHtml;
  if (control === "textarea" || control === "listarea" || control === "json") {
    inputHtml = `<textarea class="frow" name="${esc(key)}" rows="3" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}"` + esc(disabledAttr) + `>` +
      esc(shown) + `</textarea>`;
  } else if (control === "bool") {
    const checkedAttr = shown === true ? " checked" : "";
    inputHtml = `<input type="checkbox" class="frow" name="${esc(key)}" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}" value="1"` +
      esc(checkedAttr) + esc(disabledAttr) + `>`;
  } else {
    const inputType = control === "number" ? "number" : "text";
    inputHtml = `<input type="${esc(inputType)}" class="frow" name="${esc(key)}" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}" value="${esc(shown)}"` +
      (control === "number" ? ' step="1"' : "") + esc(disabledAttr) + `>`;
  }
  const note = running && reason
    ? `<span class="frow-note">${esc(reason)}</span>` : "";
  return `<div class="${esc(rowCls)}"><label class="frow-label">${esc(label)}</label>` +
    inputHtml + note + `</div>`;
}

// Preset buttons for "попыток на задачу": dataset.preset carries the value,
// so the click handler (attached after render) only reads dataset.
function presetButtons(disabled) {
  const dAttr = disabled ? " disabled" : "";
  return `<span class="frow-presets">` + ATTEMPT_PRESETS.map((p) =>
    `<button type="button" class="frow-preset" data-preset="${esc(p)}"` + esc(dAttr) + `>` +
    esc(p) + `</button>`
  ).join("") + `</span>`;
}

// The "Конфигурация запуска" form: every listed field from the API, with
// greyed fields and explanations for a running mission.  Returns the escaped
// HTML string of the form.
function formHtml(mission, queueEntry, config, running) {
  const rows = [];
  for (const [kind, key, label, control] of FORM_ROWS) {
    let value;
    if (kind === "mission") value = mission[key];
    else if (kind === "queue") {
      // a mission that is not in board.queue yet: its own enabled flag is the best answer
      value = queueEntry && queueEntry[key] != null ? queueEntry[key] : (key === "enabled" ? mission.enabled : null);
    }
    else value = config ? config[key] : null;
    const shown = control === "listarea" ? fmtLines(value)
      : control === "json" ? fmtJson(value)
      : control === "bool" ? (value === true || String(value).toLowerCase() === "true")
      : (value == null ? "" : String(value));
    const reason = running ? greyNote(key) : null;
    rows.push(fieldRow(kind, key, label, control, shown, running, reason));
  }
  const attemptsRow = rows[6]; // the "attempts_per_task" row (index 6)
  // The attempts row gets the preset buttons after its input.
  rows[6] = attemptsRow.replace(/<\/div>$/, presetButtons(running) + `</div>`);
  return `<div class="confbox" id="confbox">` + rows.join("") + `</div>`;
}

// Pure render: breadcrumbs "Миссии / <имя>", the summary meta line, and the
// "Конфигурация запуска" form.  Returns the escaped HTML string of the
// whole #boardmission content.
function renderMission(data) {
  const mission = data.mission || {};
  const config = data.config || {};
  const queueEntry = (data.queue || []).find((e) => e.mission_id === mission.mission_id) || {};
  const live = data.live_state;
  const running = !!live && live.status === "active";
  const groups = data.groups || {};
  const inRunning = Array.isArray(groups.running) && groups.running.includes(mission.mission_id);
  const groupKey = running || inRunning ? "running" : "queued";

  const status = live && live.status ? live.status : groupKey;
  const taskN = live && live.task_index != null ? Number(live.task_index) + 1 : null;
  const metaParts = [
    "id: " + (mission.mission_id == null ? "" : mission.mission_id),
    "состояние: " + (status == null ? "—" : status),
    taskN != null ? "задача " + taskN : "задача —",
    "включена: " + (mission.enabled === true ? "да" : "нет"),
    "папка: " + (mission.working_directory == null ? "—" : mission.working_directory),
  ];
  const runningNote = running
    ? "пока миссия идёт: изменения применяются со следующей задачи; " +
      "серые поля не применяются"
    : "";

  return (
    `<div class="crumbs"><a class="back" href="#board">Миссии</a> / ` +
    `<span class="crumb-name">${esc(mission.name == null ? "" : mission.name)}</span></div>` +
    `<h2>${esc(mission.name == null ? "" : mission.name)}</h2>` +
    `<div class="meta">` +
    metaParts.map((p) => `<span>${esc(p)}</span>`).join("") +
    `</div>` +
    (runningNote ? `<div class="meta running-note">${esc(runningNote)}</div>` : "") +
    `<h3>Конфигурация запуска</h3>` +
    formHtml(mission, queueEntry, config, running)
  );
}

// Render into #boardmission, creating the container inside #board when the
// page has no #boardmission yet; a successful render clears the shell's
// #boardstate line (the shell sets it back on later errors).
function render(data) {
  let el = document.getElementById("boardmission");
  if (!el) {
    el = document.createElement("div");
    el.id = "boardmission";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  el.innerHTML = renderMission(data);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
}

// The attempts-per-task preset buttons set their input's value from
// dataset.preset (no data value is read from the DOM anywhere else).
function attachPresetButtons() {
  const box = document.getElementById("boardmission");
  if (!box) return;
  for (const btn of box.querySelectorAll(".frow-preset")) {
    if (btn.dataset.attached === "1") continue;
    btn.dataset.attached = "1";
    btn.addEventListener("click", () => {
      const input = box.querySelector('input[name="attempts_per_task"]');
      if (input) input.value = btn.dataset.preset;
    });
  }
}

// BoardMission(mission_id): load the mission and the queue, render
// #boardmission.  Called by the shell's route() for the #board/<mission_id>
// fragment.
async function BoardMission(missionId) {
  const path = "/api/board/missions/" + encodeURIComponent(missionId);
  const detail = await fetchJson(path);
  const queueData = await fetchJson("/api/board/queue");
  const data = {
    mission: detail.mission,
    live_state: detail.live_state,
    config: detail.config || {},
    queue: queueData.queue,
  };
  render(data);
  attachPresetButtons();
  const box = document.getElementById("boardmission");
  if (box && typeof BoardMissionSave !== "undefined" && BoardMissionSave.attachToBox) {
    BoardMissionSave.attachToBox(box, data);
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

// === export (browser global + CommonJS module.exports) ===
const BoardMissionView = {
  renderMission, formHtml, fieldRow, presetButtons, fmtLines, fmtJson, greyNote,
  render, BoardMission, attachPresetButtons,
  MIDRUN_CONFIG_FIELDS, NON_EDITABLE_CONFIG_FIELDS, FORM_ROWS, esc,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardMissionView;
} else {
  globalThis.BoardMission = BoardMission;
  globalThis.BoardMissionView = BoardMissionView;
  globalThis.renderMission = renderMission;
}
})();
