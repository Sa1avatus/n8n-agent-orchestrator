/* board-scope-wrapped */
(function () {
/* Board plan edit form for one task (part B).
 *
 * BoardPlanEdit(missionId, taskId, container) renders the task's edit form in
 * the container: the task content fields (title, objective, acceptance
 * criteria, verification, scope, hint) as one row each.  The form shows a
 * diff of the changed fields, then asks for confirmation, then posts to
 * POST /api/board/missions/{mission_id}/tasks/{task_key} with only the
 * changed fields plus the plan version read from
 * GET /api/board/missions/{mission_id}/plan (the guarded route's expected
 * version).  A 403 shows the server's message and keeps the task read-only;
 * a 409 shows "план изменился, обновите" and reloads the plan.
 *
 * The form is read-only while the task's status is not "waiting": the
 * controls are disabled and no post is made.  When the mission's live status
 * is "active" (the mission is running) a warning is shown next to the form,
 * but the controls stay enabled: a waiting task is past the live task_index,
 * so the server still allows the edit, and the workflow may overwrite it.
 *
 * Every value taken from server data flows through esc(); list fields are
 * textareas read as arrays of lines.  CRLF is normalised to LF in both the
 * form read and the initial values, so the diff compares apples-to-apples.
 */
"use strict";

// translated text; the English text is the fallback (node tests, a table without the key)
function L(key, en, params) {
  let v = en;
  try {
    if (typeof t === "function") { const r = t(key); if (r && r !== key) v = r; }
  } catch (e) { /* keep the English text */ }
  if (params) for (const k of Object.keys(params)) v = v.split("{" + k + "}").join(String(params[k]));
  return v;
}


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

// The form fields and their labels.  "hint" is not a plan content field
// (EDITABLE_TASK_FIELDS does not include it); it is read here and sent with
// the changed fields, and the server's 403 names it as not editable.
const FIELD_ROWS = [
  ["title", "pe.f.title", "text"],
  ["objective", "pe.f.objective", "textarea"],
  ["acceptance_criteria", "pe.f.acceptance", "listarea"],
  ["verification", "pe.f.verification", "listarea"],
  ["scope", "pe.f.scope", "listarea"],
  ["hint", "pe.f.hint", "textarea"],
];

// One row of the form: the label and the value input.  Every input carries
// data-key and data-control, so readForm() reads it back by dataset.
function fieldHtml(key, label, control, shown, disabled) {
  const dAttr = disabled ? " disabled" : "";
  const shownText = Array.isArray(shown)
    ? shown.map((x) => String(x)).join("\n")
    : (shown == null ? "" : String(shown).replace(/\r\n?/g, "\n"));
  if (control === "textarea" || control === "listarea") {
    // A real <textarea>: the escaped value goes between the tags, no
    // value attribute (that is only valid on input elements).
    return (
      `<div class="pedit-row"><label class="pedit-label">${esc(label)}</label>` +
      `<textarea class="pedit" data-key="${esc(key)}" ` +
      `data-control="${esc(control)}"${dAttr}>${esc(shownText)}</textarea></div>`
    );
  }
  return (
    `<div class="pedit-row"><label class="pedit-label">${esc(label)}</label>` +
    `<input type="text" class="pedit" data-key="${esc(key)}" ` +
    `data-control="${esc(control)}"${dAttr} value="${esc(shownText)}"></div>`
  );
}

// Read-only view of the task's ui_check (the server does not accept it from the
// editor, so it is never a form row and never part of the save body).
function uiCheckHtml(check) {
  if (!check || typeof check !== "object") return "";
  const steps = Array.isArray(check.steps) ? check.steps : [];
  const items = steps.map((s) => "<li>" + esc(s) + "</li>").join("");
  const stepsHtml = steps.length ? '<ol class="pedit-steps">' + items + "</ol>" : "";
  const needed = check.needed ? L("pe.uc.yes", "yes") : L("pe.uc.no", "no");
  return (
    `<div class="pedit-uicheck"><h4>${esc(L("pe.uc.title", "UI check"))}</h4>` +
    `<div class="pedit-readonly">${esc(L("pe.uc.note", "Read-only: the UI check is set by the plan and is not edited here."))}</div>` +
    `<div>${esc(L("pe.uc.needed", "UI check needed"))}: ${esc(needed)}</div>` +
    `<div>${esc(L("pe.uc.why", "why"))}: ${esc(check.why)}</div>` +
    `<div>${esc(L("pe.uc.scenario", "scenario"))}: ${esc(check.scenario)}</div>` +
    `<div>${esc(L("pe.uc.steps", "steps"))}:</div>` + stepsHtml + "</div>"
  );
}

// Pure render: the form rows, the read-only note for a non-waiting task, and
// the running-mission warning.  Returns the escaped HTML string.  view is
// the task's TaskView ({ status, ... }) and task the plan's task object
// (the content values); running is true when the mission's live status is
// "active".
function renderEditForm(view, task, running) {
  const t = task || {};
  const status = view && view.status ? view.status : "waiting";
  const editable = status === "waiting";
  const rows = FIELD_ROWS.map(([key, label, control]) =>
    fieldHtml(key, L(label, label.replace(/^pe[.]f[.]/, "")), control, t[key], !editable));
  const readOnly = editable ? "" :
    `<div class="pedit-readonly">${esc(L("pe.readonly", "The task is already done ({status}): editing is not available.", { status }))}</div>`;
  const warning = running ?
    `<div class="pedit-warning">${esc(L("pe.warn", "Editing the plan of a running mission can be overwritten by the workflow: do it on a pause."))}</div>` : "";
  return (
    `<h3>${esc(L("pe.title", "Edit task"))}</h3>` + rows.join("") + uiCheckHtml(t.ui_check) +
    readOnly + warning
  );
}

// Read the form values into the same shape the initial values have:
// text inputs as strings (CRLF normalised to LF), listareas as arrays of
// trimmed non-empty lines.
function readForm(box) {
  const out = {};
  for (const el of box.querySelectorAll("[data-key]")) {
    const key = el.dataset.key;
    const control = el.dataset.control;
    const shown = el.value;
    if (control === "listarea") {
      out[key] = String(shown).split("\n").map((l) => l.trim()).filter((l) => l !== "");
    } else {
      out[key] = String(shown).replace(/\r\n?/g, "\n");
    }
  }
  return out;
}

// The task's initial field values in the same shape readForm() produces
// (CRLF normalised, listareas as arrays).  The hint comes from the task
// overrides row (the plan's task object carries no hint field).
function initialValues(task, taskKey, overrides) {
  const out = {};
  const hintRow = (overrides || []).find((o) => o.task_key === taskKey && o.hint != null);
  for (const [key, , control] of FIELD_ROWS) {
    let v;
    if (key === "hint") v = hintRow ? hintRow.hint : null;
    else v = task[key];
    if (control === "listarea") {
      out[key] = Array.isArray(v)
        ? v.map((x) => String(x))
        : (v == null || v === "" ? [] : parseLines(v));
    } else {
      out[key] = v == null ? "" : String(v).replace(/\r\n?/g, "\n");
    }
  }
  return out;
}

// Parse one textarea's newline-separated lines into an array (mirrors
// board_mission_save.js's parseLines; a single non-empty line is one item).
function parseLines(text) {
  const s = text == null ? "" : String(text);
  if (s === "") return [];
  return s.split("\n").map((l) => l.trim()).filter((l) => l !== "");
}

// Render the diff of the changed fields as escaped HTML.
function diffHtml(diff, labels) {
  if (!diff || diff.length === 0) return `<div class="pedit-diff none">${esc(L("pe.nodiff", "No changes."))}</div>`;
  const fmt = (v) => (v == null ? "—" :
    Array.isArray(v) ? v.map((x) => String(x)).join(", ") : String(v));
  const rows = diff.map((d) => {
    const label = labels[d.field] || d.field;
    return `<div class="pedit-diff-row"><b>${esc(label)}</b>: ` +
      `<span class="old">${esc(fmt(d.old))}</span> → ` +
      `<span class="new">${esc(fmt(d.new))}</span></div>`;
  });
  return `<div class="pedit-diff">` + rows.join("") + `</div>`;
}

// Resolve the container: an element or a string id; create the element when
// the id is not found (the caller passes a real container in the browser).
function resolveContainer(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardtaskedit");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardtaskedit";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
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

// The guarded route's writer: send one JSON body (or none) to the plan
// route with the dashboard header — POST by default, DELETE for delete;
// a 409 raises "план изменился, обновите" (like board_mission_save.js's
// postJson), any other error raises its message.
async function planEditPost(url, body, method) {
  const res = await fetch(url, {
    method: method || "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-AHAWR-Dashboard": "1",
    },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (res.status === 409) {
    const err = new Error((payload.error && payload.error.message) || "план изменился, обновите");
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

// Load the plan, the task's status and the mission's live status, then
// render the form.  Called when the edit form is opened or reloaded.
async function renderEdit(container, missionId, taskId) {
  const planPath = "/api/board/missions/" + encodeURIComponent(missionId) + "/plan";
  const detailPath = "/api/board/missions/" + encodeURIComponent(missionId);
  const planData = await fetchJson(planPath);
  const detail = await fetchJson(detailPath);
  const tasks = planData.tasks || [];
  const view = tasks.find((t) => t.task_id === taskId) || {};
  const planTasks = (planData.plan && planData.plan.tasks) || [];
  const task = planTasks.find((t) => t.id === taskId) || {};
  const overrides = detail.task_overrides || [];
  const running = detail.live_state && detail.live_state.status === "active";
  const editable = view.status === "waiting";
  const data = {
    missionId,
    taskId,
    view,
    task,
    running,
    editable,
    version: planData.version,
    initial: initialValues(task, taskId, overrides),
  };
  const el = resolveContainer(container);
  el.innerHTML = renderEditForm(view, task, running);
  // innerHTML clears the children but not the element's own dataset:
  // drop editReady so attachFormControls re-creates the Save/Reset buttons
  // on every reload (one button bar per render).
  delete el.dataset.editReady;
  el.dataset.editable = editable ? "1" : "";
  const box = el.querySelector(".pedit-form") || el;
  attachFormControls(box, data);
}

// Attach the form controls (idempotent via dataset.editReady).
function attachFormControls(box, data) {
  if (box.dataset.editReady === "1") return;
  box.dataset.editReady = "1";
  const bar = document.createElement("div");
  bar.className = "pedit-bar";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "pedit-save";
  saveBtn.textContent = L("pe.save", "Save");
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "pedit-reset";
  resetBtn.textContent = L("pe.reset", "Reset");
  saveBtn.addEventListener("click", () => onSave(box, data));
  resetBtn.addEventListener("click", () => onReset(box, data));
  bar.appendChild(saveBtn);
  bar.appendChild(resetBtn);
  box.appendChild(bar);
}

// The diff of the changed fields: current form values vs the initial values.
function currentDiff(box, initial) {
  const cur = readForm(box);
  const out = [];
  for (const key of Object.keys(cur)) {
    if (JSON.stringify(initial[key]) !== JSON.stringify(cur[key])) {
      out.push({ field: key, old: initial[key], new: cur[key] });
    }
  }
  return out.sort((a, b) => a.field.localeCompare(b.field));
}

// "Сохранить": show the diff, ask for confirmation, then post the changed
// fields plus the expected version through planEditPost.
async function onSave(box, data) {
  if (!data.editable) return;
  const labels = {};
  for (const [key, label] of FIELD_ROWS) labels[key] = label;
  const diff = currentDiff(box, data.initial);
  const diffBox = document.createElement("div");
  diffBox.className = "pedit-diffbox";
  box.appendChild(diffBox);
  diffBox.innerHTML = diffHtml(diff, labels);
  if (diff.length === 0) {
    showNote(box, L("pe.nothing", "No changes — nothing to save."));
    return;
  }
  if (!window.confirm(L("pe.confirm", "Save the changes?"))) {
    showNote(box, "");
    diffBox.remove();
    return;
  }
  const body = Object.assign({ expected_version: data.version },
    Object.fromEntries(diff.map((d) => [d.field, d.new])));
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) +
        "/tasks/" + encodeURIComponent(data.taskId), body);
    showNote(box, "");
    diffBox.remove();
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    if (e.status === 403) {
      // The task is read-only: show the server's message and keep the form
      // disabled.
      showNote(box, e.message || L("pe.unavailable", "editing is not available"));
      box.dataset.editable = "";
      for (const el of box.querySelectorAll("[data-key]")) el.disabled = true;
    } else if (e.status === 409) {
      // renderEdit rewrites box.innerHTML, so the note must come after it
      // (a note shown before would be erased by the reload).
      await renderEdit(box, data.missionId, data.taskId);
      showNote(box, L("conflict.plan_changed", "the plan changed, reload"));
    } else {
      showNote(box, e.message || L("pe.savefail", "saving failed"));
    }
    diffBox.remove();
  }
}

// "Сбросить": restore every field to its initial value.
function onReset(box, data) {
  for (const el of box.querySelectorAll("[data-key]")) {
    const key = el.dataset.key;
    const v = data.initial[key];
    el.value = Array.isArray(v) ? v.join("\n") : (v == null ? "" : String(v));
  }
  showNote(box, "");
  const diffBox = box.querySelector(".pedit-diffbox");
  if (diffBox) diffBox.remove();
}

// Show (or clear) a note under the form (textContent — no innerHTML).
function showNote(box, text) {
  let note = box.querySelector(".pedit-note");
  if (!note) {
    note = document.createElement("div");
    note.className = "pedit-note";
    box.appendChild(note);
  }
  note.textContent = text || "";
}

// === task operations (part B, group 2) ===

// Shared error handling for the task operations, mirroring onSave: a 403
// shows the server's guard message, a 409 reloads the plan and shows
// "план изменился, обновите", anything else shows the message.  (onSave's
// extra read-only disabling stays on onSave: a refused operation does not
// make the task read-only.)
async function handleOpError(e, box, data) {
  if (e.status === 409) {
    await renderEdit(box, data.missionId, data.taskId);
    showNote(box, L("conflict.plan_changed", "the plan changed, reload"));
  } else {
    showNote(box, e.message || L("pe.opfail", "the operation failed"));
  }
}

// "Добавить задачу": POST the new task after the current one (after_key is
// the current task's key) with the expected version.
async function onAddTask(box, data, fields) {
  const body = {
    expected_version: data.version,
    title: fields.title,
    objective: fields.objective,
    acceptance_criteria: fields.acceptance_criteria,
    after_key: data.taskId,
    who: "",
  };
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) + "/tasks",
      body);
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// "Удалить задачу": asks for confirmation, then DELETEs the task.  The
// route takes no expected_version (it never answers 409); confirm=true is
// sent so the server's running-mission guard is satisfied.
async function onDeleteTask(box, data) {
  if (!window.confirm(L("pe.delete", "Delete task {id}?", { id: data.taskId }))) return;
  const url = "/api/board/missions/" + encodeURIComponent(data.missionId) +
    "/tasks/" + encodeURIComponent(data.taskId) + "?confirm=true";
  try {
    await planEditPost(url, null, "DELETE");
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// "Дублировать задачу": duplicate the task right after itself (after_key
// null lets the route pick that position) with the expected version.
async function onDuplicateTask(box, data) {
  const body = {
    expected_version: data.version,
    after_key: null,
    who: "",
  };
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) +
        "/tasks/" + encodeURIComponent(data.taskId) + "/duplicate",
      body);
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// "Переместить задачу" up or down: to_position is the task's current
// 0-based position (from the TaskView) shifted by one; the server guards
// a target at/before the live task_index with 403.
async function onMoveTask(box, data, direction) {
  const index = data.view && data.view.task_index != null
    ? Number(data.view.task_index) : 0;
  const toPosition = direction === "up" ? index - 1 : index + 1;
  const body = { expected_version: data.version, to_position: toPosition, who: "" };
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) +
        "/tasks/" + encodeURIComponent(data.taskId) + "/move",
      body);
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// "Повторить задачу": offered only for a not_accepted task (the route's
// guard is the same: only not_accepted may be retried); the attempt
// counter is reset to 1.
async function onRetryTask(box, data) {
  const status = data.view && data.view.status;
  if (status !== "not_accepted") {
    showNote(box, L("pe.retryonly", "a retry is only available for a task with the status not_accepted"));
    return;
  }
  const body = { expected_version: data.version, who: "" };
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) +
        "/tasks/" + encodeURIComponent(data.taskId) + "/retry",
      body);
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// "Сохранить подсказку": POST the form's hint field to the hint route with
// the expected version (the hint lives in the task override, not the plan).
async function onSetHint(box, data) {
  const form = readForm(box);
  const body = { expected_version: data.version, hint: form.hint, who: "" };
  try {
    await planEditPost(
      "/api/board/missions/" + encodeURIComponent(data.missionId) +
        "/tasks/" + encodeURIComponent(data.taskId) + "/hint",
      body);
    showNote(box, "");
    await renderEdit(box, data.missionId, data.taskId);
  } catch (e) {
    await handleOpError(e, box, data);
  }
}

// === export (browser global + CommonJS module.exports) ===
const BoardPlanEdit = {
  renderEdit, renderEditForm, readForm, initialValues, currentDiff, diffHtml,
  planEditPost, onSave, onReset, esc,
  onAddTask, onDeleteTask, onDuplicateTask, onMoveTask, onRetryTask, onSetHint,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardPlanEdit;
} else if (typeof document !== "undefined") {
  globalThis.BoardPlanEdit = BoardPlanEdit;
}
})();
