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
// NON_EDITABLE_CONFIG_FIELDS); the explanation (a t() key) goes next to the
// greyed field.
const NON_EDITABLE_CONFIG_FIELDS = {
  working_directory: "form.locked.working_directory",
  state_namespace: "form.locked.state_namespace",
  retrieval_corpora: "form.locked.retrieval_corpora",
};

// Config form layout: one row per [kind, key, labelKey, control].
// kind "mission" — the value comes from the mission row;
// kind "queue"   — the value comes from the queue entry;
// kind "config"  — the value comes from the mission config object.
// The key is the config field name (mission_row fields use their row name).
const FORM_ROWS = [
  ["mission", "name", "form.field.name", "text"],
  ["mission", "objective", "form.field.objective", "textarea"],
  ["mission", "rules", "form.field.rules", "listarea"],
  ["mission", "acceptance_criteria", "form.field.acceptance_criteria", "listarea"],
  ["mission", "working_directory", "form.field.working_directory", "text"],
  ["queue", "enabled", "form.field.enabled", "bool"],
  ["queue", "attempts_per_task", "form.field.attempts_per_task", "number"],
  ["config", "worker_model", "form.field.worker_model", "text"],
  ["config", "worker_provider", "form.field.worker_provider", "text"],
  ["config", "reviewer_model", "form.field.reviewer_model", "text"],
  ["config", "reviewer_provider", "form.field.reviewer_provider", "text"],
  ["config", "architect_model", "form.field.architect_model", "text"],
  ["config", "architect_provider", "form.field.architect_provider", "text"],
  ["config", "max_tasks", "form.field.max_tasks", "number"],
  ["config", "worker_think_policy", "form.field.worker_think_policy", "json"],
  ["config", "worker_think_policy_from_task", "form.field.worker_think_policy_from_task", "number"],
  ["config", "retrieval_corpora", "form.field.retrieval_corpora", "listarea"],
  ["config", "hints", "form.field.hints", "listarea"],
  ["config", "required_files", "form.field.required_files", "listarea"],
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
  return NON_EDITABLE_CONFIG_FIELDS[key] ? t(NON_EDITABLE_CONFIG_FIELDS[key]) : null;
}

// One form row: label, the value input, and (when running and the field is
// not applicable from the next task) the greyed note.  Returns the escaped
// HTML string of the row; the input's dataset carries kind/key so T030 can
// read the fields back without re-parsing the markup.
function fieldRow(kind, key, label, control, shown, running, reason, fallback) {
  const disabledAttr = running && reason ? " disabled" : "";
  const phAttr = fallback == null || fallback === "" ? ""
    : ` placeholder="${esc(fallback)}" title="${esc(t("form.default"))}"`;
  const greyed = running && reason ? " greyed" : "";
  const rowCls = "frow-wrap" + (greyed ? " disabled" : "");
  let inputHtml;
  if (control === "textarea" || control === "listarea" || control === "json") {
    inputHtml = `<textarea class="frow" name="${esc(key)}" rows="3" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}"` + phAttr + esc(disabledAttr) + `>` +
      esc(shown) + `</textarea>`;
  } else if (control === "bool") {
    const checkedAttr = shown === true ? " checked" : "";
    inputHtml = `<input type="checkbox" class="frow" name="${esc(key)}" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}" value="1"` +
      esc(checkedAttr) + esc(disabledAttr) + `>`;
  } else {
    const inputType = control === "number" ? "number" : "text";
    inputHtml = `<input type="${esc(inputType)}" class="frow" name="${esc(key)}" ` +
      `data-kind="${esc(kind)}" data-key="${esc(key)}" data-control="${esc(control)}" value="${esc(shown)}"` + phAttr +
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
function formHtml(mission, queueEntry, config, running, defaults) {
  const rows = [];
  for (const [kind, key, labelKey, control] of FORM_ROWS) {
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
    const fallbackKey = key === "attempts_per_task" ? "max_attempts_per_task" : key;
    const fallback = kind !== "mission" && defaults && defaults[fallbackKey] != null
      ? (typeof defaults[fallbackKey] === "string" ? defaults[fallbackKey] : JSON.stringify(defaults[fallbackKey])) : null;
    rows.push(fieldRow(kind, key, t(labelKey), control, shown, running, reason, fallback));
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
    t("form.id", { value: mission.mission_id == null ? "" : mission.mission_id }),
    t("form.status", { value: status == null ? "—" : status }),
    t("form.task", { n: taskN != null ? taskN : "—" }),
    t("form.enabled", { value: mission.enabled === true ? t("form.yes") : t("form.no") }),
    t("form.folder", { value: mission.working_directory == null ? "—" : mission.working_directory }),
  ];
  const runningNote = running ? t("form.midrun") : "";

  return (
    `<div class="crumbs"><a class="back" href="#board">${esc(t("form.back"))}</a> / ` +
    `<span class="crumb-name">${esc(mission.name == null ? "" : mission.name)}</span></div>` +
    `<h2>${esc(mission.name == null ? "" : mission.name)}</h2>` +
    `<div class="meta">` +
    metaParts.map((p) => `<span>${esc(p)}</span>`).join("") +
    `</div>` +
    (runningNote ? `<div class="meta running-note">${esc(runningNote)}</div>` : "") +
    `<h3>${esc(t("form.title"))}</h3>` +
    formHtml(mission, queueEntry, config, running, data.defaults)
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
    defaults: detail.defaults || {},
    queue: queueData.queue,
  };
  render(data);
  attachPresetButtons();
  const box = document.getElementById("boardmission");
  if (box) {
    // The section containers (#boardtasks, #boardthink, #boardready,
    // #boardhistory) exist only if board.html defines them; a missing one is
    // created in #board, as render() does for #boardmission.  document is
    // probed with typeof: node tests stub it, and the board module itself
    // never loads without one.
    const container = (id) => {
      const el = (typeof document !== "undefined") ? document.getElementById(id) : null;
      if (el) return el;
      if (typeof document === "undefined") return { innerHTML: "" };
      const fresh = document.createElement("div");
      fresh.id = id;
      const root = document.getElementById("board");
      if (root) root.appendChild(fresh);
      return fresh;
    };
    await renderTaskSection(container("boardtasks"), data.mission.mission_id).catch(
      (e) => {
        container("boardtasks").innerHTML =
          `<div class="terror">${esc(e.message || e)}</div>`;
      });
    if (typeof BoardPlanEdit !== "undefined") container("boardplanedit");
    if (typeof BoardThinkUI !== "undefined") {
      BoardThinkUI.render(container("boardthink"), data.mission.mission_id).catch(
        (e) => container("boardthink").innerHTML =
          `<div class="tkerror">${esc(e.message || e)}</div>`);
    }
    if (typeof BoardReadyUI !== "undefined") {
      BoardReadyUI.render(container("boardready"), data.mission.mission_id, data.mission).catch(
        (e) => container("boardready").innerHTML =
          `<div class="rdresult">${esc(e.message || e)}</div>`);
    }
    if (typeof BoardHistoryUI !== "undefined") {
      BoardHistoryUI.render(container("boardhistory"), data.mission.mission_id).catch(
        (e) => container("boardhistory").innerHTML =
          `<div class="chresult">${esc(e.message || e)}</div>`);
    }
  }
  if (box && typeof BoardMissionSave !== "undefined" && BoardMissionSave.attachToBox) {
    BoardMissionSave.attachToBox(box, data);
  }
}

// === task table: depends-on / needed-by chips, drag order, dependency select ===

let tasksView = null;

function orderRules() {
  if (typeof BoardOrder !== "undefined") return BoardOrder;
  return require("./board_order.js");
}

function tOr(key, fallback) {
  const s = t(key);
  return s === key ? fallback : s;
}

function runningWarnText() {
  return tOr("running.warn", "The mission is running: the new order applies from the next task.");
}

function planPath(missionId) {
  return "/api/board/missions/" + encodeURIComponent(missionId) + "/plan";
}

function fmtCount(v) {
  const n = Number(v) || 0;
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(Math.round(n));
}

function statsText(stats) {
  if (!stats) return "—";
  const parts = [];
  if (stats.attempts != null) {
    const max = stats.max_attempts_per_task != null ? "/" + stats.max_attempts_per_task : "";
    parts.push(t("att", { n: stats.attempts + max }));
  }
  parts.push(fmtCount((Number(stats.input_tokens) || 0) + (Number(stats.output_tokens) || 0)) + " tok");
  if (stats.cost_usd != null) parts.push("$" + Number(stats.cost_usd).toFixed(2));
  return parts.join(" · ");
}

// Plan tasks as [{ id, title, status, st, deps }]: st "g" marks a waiting
// task (the BoardOrder rules), deps come from the live plan.
function taskModel(data) {
  const planTasks = data.plan && Array.isArray(data.plan.tasks) ? data.plan.tasks : [];
  const planDeps = Object.create(null);
  planTasks.forEach((p) => {
    planDeps[p.id] = Array.isArray(p.dependencies) ? p.dependencies.slice() : [];
  });
  return (data.tasks || []).map((v) => {
    const status = v.status || "waiting";
    return {
      id: String(v.task_id),
      title: v.title == null ? "" : String(v.title),
      status,
      st: status === "waiting" ? "g" : status,
      deps: planDeps[v.task_id] || (Array.isArray(v.deps) ? v.deps.slice() : []),
    };
  });
}

function depsMap(tasks) {
  const known = new Set(tasks.map((x) => x.id));
  const m = Object.create(null);
  tasks.forEach((x) => { m[x.id] = x.deps.filter((d) => known.has(d)); });
  return m;
}

// The task, its dependencies and its dependents: the set a chip click highlights.
function highlightSet(tasks, id) {
  const deps = depsMap(tasks);
  const out = Object.create(null);
  out[id] = true;
  (deps[id] || []).forEach((d) => { out[d] = true; });
  tasks.forEach((x) => { if ((deps[x.id] || []).indexOf(id) >= 0) out[x.id] = true; });
  return out;
}

function cycleText(pair) {
  return t("w.cycle", { dep: pair[0], id: pair[1] });
}

// The refusal text when adding "taskId depends on depId" would close a cycle; null when allowed.
function dependencyRefusal(tasks, taskId, depId) {
  const pair = orderRules().wouldCycle(depsMap(tasks), taskId, depId);
  return pair ? cycleText(pair) : null;
}

// The warning box for a refused drag: the reason, and when the task can move
// together with its dependencies or dependents, the offer to do so.
function warningFor(tasks, id, r) {
  const task = tasks.find((x) => x.id === id) || { id, title: "" };
  if (r.reason !== "dependency" || !r.group) {
    return { text: t("w.bad", { pairs: r.broken.map((p) => p[0] + " → " + p[1]).join(", ") }), group: null };
  }
  // Offer "move together" only when the group order is actually a new order:
  // if it equals the current order, moving the group would be a no-op, so
  // show the refusal text without the button.
  const current = tasks.map((x) => x.id).join(",");
  if (r.group.order.join(",") === current) {
    return { text: t("w.bad", { pairs: r.broken.map((p) => p[0] + " → " + p[1]).join(", ") }), group: null };
  }
  const up = r.broken.some((p) => p[1] === id);
  const list = up
    ? r.broken.filter((p) => p[1] === id).map((p) => p[0])
    : r.broken.filter((p) => p[0] === id).map((p) => p[1]);
  const first = up
    ? t("w.up", { id, title: task.title, list: list.join(", ") })
    : t("w.down", { id, title: task.title, list: list.join(", ") });
  const second = t("w.group", {
    rel: t(up ? "rel.dependencies" : "rel.dependents"),
    list: r.group.ids.slice(1).join(", "),
    n: r.group.ids.length,
  });
  return { text: first + " " + second, group: r.group };
}

function chipsHtml(kind, owner, ids) {
  return ids.map((id) =>
    `<button type="button" class="chip chip-${esc(kind)}" data-owner="${esc(owner)}" data-chip="${esc(id)}">${esc(id)}</button>`
  ).join("");
}

function depSelectHtml(task, tasks) {
  const options = tasks
    .filter((x) => x.id !== task.id && task.deps.indexOf(x.id) < 0)
    .map((x) => `<option value="${esc(x.id)}">${esc(x.id + " " + x.title)}</option>`)
    .join("");
  return `<select class="dep-select" data-task-id="${esc(task.id)}">` +
    `<option value="">${esc(t("dep.add"))}</option>` + options + `</select>`;
}

function taskRowHtml(task, tasks, stats, hl) {
  const waiting = task.st === "g";
  const needed = tasks.filter((x) => x.deps.indexOf(task.id) >= 0).map((x) => x.id);
  const known = task.deps.filter((d) => tasks.some((x) => x.id === d));
  const cls = "trow " + (waiting ? "waiting" : "locked") + (hl && hl[task.id] ? " hl" : "");
  const draggable = waiting ? ' draggable="true"' : "";
  return `<tr class="${esc(cls)}" data-task-id="${esc(task.id)}"` + draggable + `>` +
    `<td class="tgrip">${esc(waiting ? "⠿" : "")}</td>` +
    `<td><span class="tid">${esc(task.id)}</span> <span class="tname">${esc(task.title)}</span></td>` +
    `<td class="tstatus">${esc(task.status)}</td>` +
    `<td class="tstats">${esc(statsText(stats))}</td>` +
    `<td class="chips">` + chipsHtml("dep", task.id, known) + `</td>` +
    `<td class="chips">` + chipsHtml("need", task.id, needed) + `</td>` +
    `<td>` + (waiting ? depSelectHtml(task, tasks) : "") + `</td>` +
    `</tr>`;
}

function warningHtml(w) {
  const group = w.group
    ? `<button type="button" class="btn-group" data-act="group">${esc(t("btn.group"))}</button>`
    : "";
  return `<div class="lane-warn" role="alert"><span>${esc(w.text)}</span>` + group +
    `<button type="button" class="btn-cancel" data-act="cancel">${esc(t("btn.cancel"))}</button></div>`;
}

// Pure render of the task table with its notice and warning; state carries
// tasks, stats (task id -> stats or null), highlight, warning, notice, missionId.
function renderTaskTable(state) {
  const tasks = state.tasks;
  const hl = state.highlight ? highlightSet(tasks, state.highlight) : null;
  const rows = tasks.map((x) => taskRowHtml(x, tasks, state.stats[x.id] || null, hl)).join("");
  const heads = ["", t("t.task"), t("t.status"), t("t.stat"), t("t.dep"), t("t.need"), t("dep.add")]
    .map((h) => `<th>${esc(h)}</th>`).join("");
  const runsHref = "/#mission=" + encodeURIComponent(state.missionId);
  const notice = state.notice
    ? `<div class="${esc(state.notice.bad ? "lane-notice bad" : "lane-notice")}">${esc(state.notice.text)}</div>`
    : "";
  return `<div class="tbar"><a class="runs-btn" target="_top" href="${esc(runsHref)}">${esc(t("m.runs"))}</a></div>` +
    notice + (state.warning ? warningHtml(state.warning) : "") +
    `<table class="ttable"><thead><tr>` + heads + `</tr></thead><tbody>` + rows + `</tbody></table>`;
}

function renderTasksView() {
  tasksView.container.innerHTML = renderTaskTable(tasksView);
}

async function postJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

function failText(data) {
  if (data && data.reason) return data.reason;
  if (data && data.error && data.error.message) return data.error.message;
  return t("conflict.plan_changed");
}

async function loadTasks(missionId) {
  const planResp = await fetchJson(planPath(missionId));
  const tasks = taskModel({ plan: planResp.plan, tasks: planResp.tasks });
  const stats = Object.create(null);
  await Promise.all(tasks.map(async (x) => {
    try {
      stats[x.id] = await fetchJson("/api/board/missions/" + encodeURIComponent(missionId) +
        "/tasks/" + encodeURIComponent(x.id) + "/stats");
    } catch (e) {
      stats[x.id] = null;
    }
  }));
  return { tasks, stats };
}

async function refreshTasksView() {
  const loaded = await loadTasks(tasksView.missionId);
  Object.assign(tasksView, loaded);
  renderTasksView();
}

async function saveOrder(order) {
  const v = tasksView;
  const waiting = new Set(v.tasks.filter((x) => x.st === "g").map((x) => x.id));
  const fresh = await fetchJson(planPath(v.missionId));
  const res = await postJson(planPath(v.missionId) + "/tasks/reorder", {
    order: order.filter((id) => waiting.has(id)),
    expected_version: fresh.version,
    who: "",
  });
  v.warning = null;
  if (!res.ok) {
    v.notice = { text: failText(res.data), bad: true };
    renderTasksView();
    return;
  }
  v.notice = res.data.running_warning ? { text: runningWarnText(), bad: false } : null;
  await refreshTasksView();
}

async function dropTask(dragId, dropId) {
  const v = tasksView;
  const waiting = v.tasks.filter((x) => x.st === "g").map((x) => x.id);
  const to = waiting.indexOf(dropId);
  if (to < 0 || dragId === dropId) return;
  const r = orderRules().tryMove(v.tasks, dragId, to);
  if (r.ok) return saveOrder(r.order);
  if (r.reason !== "dependency" && r.reason !== "group-invalid") return;
  v.notice = null;
  v.warning = warningFor(v.tasks, dragId, r);
  renderTasksView();
}

async function addDependency(taskId, depId) {
  const v = tasksView;
  const refusal = dependencyRefusal(v.tasks, taskId, depId);
  v.warning = null;
  if (refusal) {
    v.notice = { text: refusal, bad: true };
    renderTasksView();
    return;
  }
  const fresh = await fetchJson(planPath(v.missionId));
  const res = await postJson(planPath(v.missionId) + "/tasks/" + encodeURIComponent(taskId) + "/dependencies", {
    action: "add",
    dependency: depId,
    expected_version: fresh.version,
    who: "",
  });
  if (res.status === 422 && Array.isArray(res.data.closing_pair)) {
    v.notice = { text: cycleText(res.data.closing_pair), bad: true };
    renderTasksView();
    return;
  }
  if (!res.ok) {
    v.notice = { text: failText(res.data), bad: true };
    renderTasksView();
    return;
  }
  v.notice = res.data.running_warning ? { text: runningWarnText(), bad: false } : null;
  await refreshTasksView();
}

function bindTasksView(container) {
  container.addEventListener("dragstart", (e) => {
    const tr = e.target.closest("tr.waiting");
    if (!tr) return;
    tasksView.drag = tr.dataset.taskId;
    if (e.dataTransfer) e.dataTransfer.setData("text/plain", tr.dataset.taskId);
  });
  container.addEventListener("dragover", (e) => {
    if (tasksView.drag && e.target.closest("tr.waiting")) e.preventDefault();
  });
  container.addEventListener("drop", (e) => {
    const tr = e.target.closest("tr[data-task-id]");
    if (!tr || !tasksView.drag) return;
    e.preventDefault();
    const dragId = tasksView.drag;
    tasksView.drag = null;
    dropTask(dragId, tr.dataset.taskId).catch(reportTaskError);
  });
  container.addEventListener("dragend", () => { tasksView.drag = null; });
  container.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) {
      const owner = chip.dataset.owner;
      tasksView.highlight = tasksView.highlight === owner ? null : owner;
      const set = tasksView.highlight ? highlightSet(tasksView.tasks, owner) : {};
      tasksView.container.querySelectorAll('tr[data-task-id]').forEach((tr) => {
        tr.classList.toggle("hl", !!set[tr.dataset.taskId]);
      });
      return;
    }
    const act = e.target.closest("[data-act]");
    if (!act || !tasksView.warning) return;
    if (act.dataset.act === "group") {
      saveOrder(tasksView.warning.group.order).catch(reportTaskError);
    } else {
      tasksView.warning = null;
      renderTasksView();
    }
  });
  container.addEventListener("change", (e) => {
    const sel = e.target;
    if (!sel.matches || !sel.matches(".dep-select") || !sel.value) return;
    addDependency(sel.dataset.taskId, sel.value).catch(reportTaskError);
  });
}

function reportTaskError(err) {
  tasksView.notice = { text: err && err.message ? err.message : String(err), bad: true };
  renderTasksView();
}

let boundContainer = null;

async function renderTaskSection(container, missionId) {
  if (boundContainer !== container) {
    bindTasksView(container);
    boundContainer = container;
  }
  if (!tasksView || tasksView.missionId !== missionId) {
    tasksView = { container, missionId, highlight: null, warning: null, notice: null, drag: null, tasks: [], stats: {} };
  }
  tasksView.container = container;
  await refreshTasksView();
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
  taskModel, depsMap, highlightSet, dependencyRefusal, warningFor,
  taskRowHtml, renderTaskTable, statsText, bindTasksView,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardMissionView;
} else {
  globalThis.BoardMission = BoardMission;
  globalThis.BoardMissionView = BoardMissionView;
  globalThis.renderMission = renderMission;
}
})();
