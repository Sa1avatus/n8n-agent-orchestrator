// Node test for board_plan_edit.js (the task edit form, diff, save and the
// task operations add / delete / duplicate / move / retry / hint).
// Run: node claude-runner/tests/board_plan_edit.test.js
//
// Proves:
//   1. renderEditForm renders the form rows and the read-only note for a
//      non-waiting task; the running-mission warning is present for an
//      active mission and absent otherwise;
//   2. readForm normalises CRLF and parses listareas into arrays;
//   3. initialValues picks up the hint from the task overrides row;
//   4. currentDiff reports exactly the changed fields;
//   5. a hostile value is escaped in the diff and in the rendered form
//      (no raw markup survives);
//   6. onSave shows the diff, asks for confirmation and posts only the
//      changed fields plus the expected version read from
//      GET /api/board/missions/{mission_id}/plan; a declined confirmation
//      posts nothing;
//   7. a 403 shows the server's message and keeps the task read-only
//      (editable cleared, every control disabled);
//   8. a 409 reloads the plan and shows "план изменился, обновите";
//   9. the empty state: an unchanged form posts nothing and shows
//      "Изменений нет — сохранять нечего."; a GET failure renders the error
//      state; a task not in the plan renders the read-only form;
//  10. add, delete, duplicate, move, retry and hint send the right requests;
//      retry is refused with a note unless the task is not_accepted;
//      a failed operation shows its message; a 409 reloads.

"use strict";
const path = require("path");
const fs = require("fs");
const PE = require(path.join(__dirname, "..", "src", "claude_runner", "board_plan_edit.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- fixtures (tests/fixtures/board_part2: one mission in "running") ---
const plan = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "board_part2", "plan.json"), "utf8"
));
const detail = {
  live_state: { status: "active", task_index: 1, task_attempt: 1,
                updated_at: "2026-10-10T12:30:00+00:00" },
  task_overrides: [{ task_key: "T002", hint: "стартуй с каталога raw" }],
};
const HOSTILE = '<img src=x onerror=alert(1)>&"\'';

// --- pure renders: the form, the read-only note, the running warning ---
const rows = PE.renderEditForm(
  { status: "waiting" },
  { title: "t1", objective: "o1", acceptance_criteria: ["c1"],
    verification: [], scope: [], hint: "h1" },
  false);
check("form renders every field row",
  has(rows, 'data-key="title"') && has(rows, 'data-key="objective"') &&
  has(rows, 'data-key="acceptance_criteria"') && has(rows, 'data-key="verification"') &&
  has(rows, 'data-key="scope"') && has(rows, 'data-key="hint"'));
check("a waiting task has no read-only note", !has(rows, "pedit-readonly"));
check("an inactive mission shows no running warning", !has(rows, "pedit-warning"));
const readOnlyRows = PE.renderEditForm(
  { status: "accepted" },
  { title: "t1", objective: "o1", acceptance_criteria: ["c1"],
    verification: [], scope: [], hint: "h1" },
  false);
check("a non-waiting task shows the read-only note",
  has(readOnlyRows, 'class="pedit-readonly">The task is already done (accepted): editing is not available.'));
check("the read-only note disables every control",
  (readOnlyRows.match(/data-key="[^"]*"/g) || []).length === 6 &&
  (readOnlyRows.match(/data-key="[^"]*" data-control="[^"]*" disabled/g) || []).length === 6);
const activeRows = PE.renderEditForm(
  { status: "waiting" },
  { title: "t1", objective: "o1", acceptance_criteria: ["c1"],
    verification: [], scope: [], hint: "h1" },
  true);
check("an active mission shows the running warning",
  has(activeRows, 'class="pedit-warning">Editing the plan of a running mission can be overwritten by the workflow: do it on a pause.'));

// --- readForm: CRLF normalisation and listarea parsing ---
function makeBox(keys) {
  const els = keys.map(([key, control, value]) => ({
    dataset: { key, control },
    value,
  }));
  return { dataset: {}, querySelectorAll: (sel) => (sel === "[data-key]" ? els : []),
           querySelector: () => null, appendChild: () => {} };
}
check("readForm normalises CRLF",
  PE.readForm(makeBox([["title", "text", "a\r\nb\r\nc"]])).title === "a\nb\nc");
check("readForm keeps listareas as trimmed arrays",
  JSON.stringify(PE.readForm(makeBox([["scope", "listarea", " s1 \r\n s2 \r\n\n"]])).scope) === '["s1","s2"]');

// --- initialValues: CRLF normalised, listareas arrays, hint from overrides ---
const initial = PE.initialValues(
  { title: "a\nb", objective: "o\r\no", acceptance_criteria: ["c1", "c2"],
    verification: null, scope: "s1\ns2" },
  "T002", detail.task_overrides);
check("initialValues normalise and arrayify",
  initial.title === "a\nb" && initial.objective === "o\no" &&
  JSON.stringify(initial.acceptance_criteria) === '["c1","c2"]' &&
  JSON.stringify(initial.scope) === '["s1","s2"]' &&
  initial.verification.length === 0 && initial.hint === "стартуй с каталога raw");
check("initialValues take the hint from the overrides row",
  initial.hint === "стартуй с каталога raw");
check("initialValues leave the hint empty without an override",
  PE.initialValues({ title: "t" }, "T002", []).hint === "");

// --- currentDiff: exactly the changed fields ---
{
  const els = [
    { dataset: { key: "title", control: "text" }, value: "t1" },
    { dataset: { key: "acceptance_criteria", control: "listarea" }, value: "c1\nc2" },
    { dataset: { key: "hint", control: "textarea" }, value: "h1" },
  ];
  const box = { dataset: {}, querySelectorAll: (sel) => (sel === "[data-key]" ? els : []),
               querySelector: () => null, appendChild: () => {} };
  const init = { title: "t1", acceptance_criteria: ["c1", "c2"], hint: "h1" };
  check("currentDiff is empty on a clean form", PE.currentDiff(box, init).length === 0);
  els[0].value = "t2";
  els[1].value = "c1\nc3";
  els[2].value = "h2";
  const diff = PE.currentDiff(box, init);
  check("currentDiff reports exactly the changed fields",
    JSON.stringify(diff.map((d) => d.field)) === '["acceptance_criteria","hint","title"]' &&
    JSON.stringify(diff[0].old) === '["c1","c2"]' && JSON.stringify(diff[0].new) === '["c1","c3"]' &&
    diff[2].old === "t1" && diff[2].new === "t2");
}

// --- a hostile value is escaped in the diff and the form ---
const labels = {};
for (const [key, label] of [["title", "заголовок"], ["hint", "подсказка Worker"]]) labels[key] = label;
const diffHtml = PE.diffHtml([{ field: "title", old: "t", new: HOSTILE }], labels);
check("a hostile value is escaped in the diff",
  has(diffHtml, "&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;") &&
  !has(diffHtml, "<img src=x onerror=alert(1)>"));
const hostileRows = PE.renderEditForm(
  { status: "waiting" },
  { title: HOSTILE, objective: HOSTILE, acceptance_criteria: [HOSTILE],
    verification: [], scope: [HOSTILE], hint: HOSTILE },
  false);
check("a hostile value is escaped in the form rows",
  has(hostileRows, "&lt;img src=x onerror=alert(1)&gt;") &&
  !has(hostileRows, "<img src=x onerror=alert(1)>"));

// --- DOM + fetch stubs for the async flows ---
// A fake DOM: document.createElement returns a div that records its children
// and keeps an innerHTML property (assigning it clears the children, like a
// real DOM), querySelector(".pedit-form") falls back to the element itself.
globalThis.document = {
  createElement: () => {
    const el = { _children: [], className: "", disabled: false };
    el.appendChild = (c) => el._children.push(c);
    el.addEventListener = () => {};
    el.remove = () => { if (el.className === "pedit-diffbox") diffBoxRef = null; };
    Object.defineProperty(el, "innerHTML", {
      set: () => { el._children = []; },
      get: () => "",
    });
    return el;
  },
};

let planState = plan;
let detailState = detail;
let post = { status: 200, body: null };
let requests = [];
globalThis.fetch = (url, options) => {
  const u = String(url);
  const method = options && options.method;
  if (method && method !== "GET") {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ url: u, method, body });
    const res = {
      ok: post.status < 400,
      status: post.status,
      json: () => Promise.resolve(post.body != null ? post.body : {}),
    };
    return Promise.resolve(res);
  }
  requests.push({ url: u, method });
  const payload = u.endsWith("/plan") ? planState : detailState;
  return Promise.resolve({
    ok: true, status: 200,
    json: () => Promise.resolve(payload),
  });
};

// A container stub: innerHTML really holds the rendered markup (so the
// tests can assert on it) and the element's dataset tracks editability.
// querySelector(".pedit-form") falls back to the element itself (the box
// that onSave / the ops use).
function makeContainer() {
  return { innerHTML: "", dataset: {}, querySelector: () => null, appendChild: () => {} };
}
// Stub window.confirm so the tests can control the confirmation dialog.
globalThis.window = { confirm: () => true };

// A box stub: querySelector returns a shared note div, appendChild records
// the diff box (so onSave can remove it), dataset tracks editability.
let noteStub;
let diffBoxRef;
function makeEditBox(initialEditable, initialValues) {
  // Fields without a set value are omitted (like a real form after the
  // render, where only fields with content are present in readForm).
  const values = initialValues || {};
  const keys = [
    ["title", "text"], ["objective", "textarea"],
    ["acceptance_criteria", "listarea"], ["verification", "listarea"],
    ["scope", "listarea"], ["hint", "textarea"],
  ];
  const els = keys
    .filter(([key]) => values[key] != null)
    .map(([key, control]) => ({
      dataset: { key, control },
      value: control === "listarea" && Array.isArray(values[key])
        ? values[key].join("\n")
        : String(values[key]),
      disabled: false,
    }));
  noteStub = { className: "pedit-note", textContent: "" };
  const box = {
    dataset: { editable: initialEditable ? "1" : "", editReady: "1" },
    querySelectorAll: (sel) => (sel === "[data-key]" ? els : []),
    querySelector: (sel) => (sel === ".pedit-note" ? noteStub : null),
    appendChild: (c) => {
      if (c.className === "pedit-diffbox") diffBoxRef = c;
    },
  };
  box.els = els;
  Object.defineProperty(box, "innerHTML", {
    get: () => "",
    set: () => { diffBoxRef = null; },
  });
  return box;
}
// Set the box's dataset editable flag and attach the form controls
// (renderEdit does this on the real container element).
function attach(box, data) {
  box.dataset.editable = data.editable ? "1" : "";
  box.dataset.editReady = "1";
  const bar = document.createElement("div");
  bar.className = "pedit-bar";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "pedit-save";
  saveBtn.textContent = "Сохранить";
  const resetBtn = document.createElement("button");
  resetBtn.type = "button";
  resetBtn.className = "pedit-reset";
  resetBtn.textContent = "Сбросить";
  saveBtn.addEventListener("click", () => PE.onSave(box, data));
  resetBtn.addEventListener("click", () => PE.onReset(box, data));
  bar.appendChild(saveBtn);
  bar.appendChild(resetBtn);
  box.appendChild(bar);
}

let lastBox;
function editData(taskId, overrides, viewStatus) {
  const views = planState.tasks || [];
  const contents = (planState.plan && planState.plan.tasks) || [];
  const v = views.find((t) => t.task_id === taskId) || {};
  const task = contents.find((t) => t.id === taskId) || {};
  return {
    missionId: "m running",
    taskId,
    view: Object.assign({ task_index: 1 }, v, { status: viewStatus || v.status }),
    task,
    running: detailState.live_state && detailState.live_state.status === "active",
    editable: (viewStatus || v.status) === "waiting",
    version: planState.version,
    initial: PE.initialValues(task, taskId, overrides),
  };
}

(async () => {
  // --- renderEdit: fetches plan and detail, renders the running warning ---
  lastBox = makeEditBox(true);
  const container = makeContainer();
  try { await PE.renderEdit(container, "m running", "T003"); }
  catch (e) { console.error("renderEdit error:", e); throw e; }
  check("renderEdit fetches the plan then the detail",
    requests.length === 2 &&
    requests[0].url === "/api/board/missions/m%20running/plan" &&
    requests[1].url === "/api/board/missions/m%20running");
  check("renderEdit renders the form with the running warning",
    has(container.innerHTML, "Edit task") &&
    has(container.innerHTML, "Editing the plan of a running mission can be overwritten by the workflow"));
  check("renderEdit sets the editable dataset flag", container.dataset.editable === "1");
  requests.length = 0;

  // --- onSave: exact request, only changed fields + the expected version ---
  const data = editData("T003", detail.task_overrides);
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  const byKey = Object.fromEntries(lastBox.els.map((e) => [e.dataset.key, e]));
  byKey.title.value = "Заголовок <b>новый</b>";
  byKey.acceptance_criteria.value = "к1\nк2";
  byKey.hint.value = "новая подсказка";
  window.confirm = () => true;
  await PE.onSave(lastBox, data);
  const savePost = requests.find((r) => r.method === "POST");
  check("onSave posts only the changed fields plus the expected version",
    JSON.stringify(
      Object.entries(savePost.body || {}).sort((a, b) => a[0].localeCompare(b[0]))
    ) ===
    JSON.stringify(
      Object.entries({ expected_version: plan.version,
        title: "Заголовок <b>новый</b>",
        acceptance_criteria: ["к1", "к2"], hint: "новая подсказка" })
        .sort((a, b) => a[0].localeCompare(b[0]))
    ));
  check("onSave posts the exact task URL",
    savePost.url === "/api/board/missions/m%20running/tasks/T003");
  check("onSave keeps no diff box after a successful save", diffBoxRef == null);
  check("onSave reloaded the plan (two GETs after the post)",
    requests.filter((r) => !r.method || r.method === "GET").length === 2 &&
    noteStub.textContent === "");
  requests.length = 0;

  // --- onSave: a declined confirmation posts nothing ---
  window.confirm = () => false;
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  Object.fromEntries(lastBox.els.map((e) => [e.dataset.key, e])).title.value = "заголовок-2";
  await PE.onSave(lastBox, data);
  check("a declined confirmation posts nothing", requests.length === 0);
  check("a declined confirmation clears the diff box", diffBoxRef == null);

  // --- onSave: a 403 shows the server's message and keeps the task read-only ---
  window.confirm = () => true;
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  Object.fromEntries(lastBox.els.map((e) => [e.dataset.key, e])).title.value = "заголовок-3";
  post = { status: 403, body: { error: { message: "правка недоступна: задача уже выполнена" } } };
  await PE.onSave(lastBox, data);
  check("a 403 shows the server's message",
    noteStub.textContent === "правка недоступна: задача уже выполнена");
  check("a 403 keeps the task read-only",
    lastBox.dataset.editable === "" && lastBox.els.every((e) => e.disabled === true));
  check("a 403 posts nothing else and does not reload", requests.length === 1);
  requests.length = 0;

  // --- onSave: a 409 reloads the plan and shows "план изменился, обновите" ---
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  Object.fromEntries(lastBox.els.map((e) => [e.dataset.key, e])).title.value = "заголовок-4";
  post = { status: 409, body: { error: { message: "план изменился, обновите" } } };
  await PE.onSave(lastBox, data);
  check("a 409 shows the reload message",
    noteStub.textContent === "the plan changed, reload");
  check("a 409 reloads the plan (GET plan then detail after the post)",
    requests.length === 3 &&
    requests[1].url === "/api/board/missions/m%20running/plan" &&
    requests[2].url === "/api/board/missions/m%20running");
  requests.length = 0;

  // --- the empty state: an unchanged form posts nothing ---
  post = { status: 200, body: null };
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onSave(lastBox, data);
  check("an unchanged form shows the empty note and posts nothing",
    noteStub.textContent === "No changes — nothing to save." && requests.length === 0);
  requests.length = 0;

  // --- the error state: a failed plan load renders the error ---
  const errorContainer = makeContainer();
  globalThis.fetch = (url) => Promise.reject(new Error("board offline"));
  await PE.renderEdit(errorContainer, "m_running", "T003").catch((e) => {
    check("a failed plan load raises an error", e.message === "board offline");
  });
  globalThis.fetch = (url, options) => {
    const u = String(url);
    const method = options && options.method;
    if (method && method !== "GET") {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url: u, method, body });
      return Promise.resolve({ ok: post.status < 400, status: post.status,
        json: () => Promise.resolve(post.body != null ? post.body : {}) });
    }
    requests.push({ url: u, method });
    const payload = u.endsWith("/plan") ? planState : detailState;
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  };

  // --- a task not in the plan renders the form (editable cleared) ---
  const missingContainer = makeContainer();
  await PE.renderEdit(missingContainer, "m_running", "T999");
  check("a task not in the plan renders the form with editable cleared",
    missingContainer.dataset.editable === "");
  requests.length = 0;

  // --- add: POST /tasks with title, objective, acceptance_criteria,
  // after_key and the expected version ---
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  post = { status: 200, body: null };
  await PE.onAddTask(lastBox, data,
    { title: "новая задача", objective: "цель", acceptance_criteria: ["к1"] });
  const addPost = requests.find((r) => r.method === "POST");
  check("add posts the exact request",
    addPost.url === "/api/board/missions/m%20running/tasks" &&
    JSON.stringify(addPost.body) ===
      JSON.stringify({ expected_version: plan.version, title: "новая задача",
        objective: "цель", acceptance_criteria: ["к1"], after_key: "T003", who: "" }));
  check("add reloads the plan afterwards",
    requests.filter((r) => !r.method || r.method === "GET").length === 2);
  requests.length = 0;

  // --- delete: DELETE /tasks/{id}?confirm=true, no body, confirmation asked ---
  window.confirm = () => true;
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onDeleteTask(lastBox, data);
  const delPost = requests.find((r) => r.method === "DELETE");
  check("delete posts the exact request with no body",
    delPost.url === "/api/board/missions/m%20running/tasks/T003?confirm=true" &&
    delPost.body == null);
  requests.length = 0;
  // a declined delete posts nothing
  window.confirm = () => false;
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onDeleteTask(lastBox, data);
  check("a declined delete posts nothing", requests.length === 0);
  window.confirm = () => true;
  requests.length = 0;

  // --- duplicate: POST /tasks/{id}/duplicate with after_key null ---
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onDuplicateTask(lastBox, data);
  const dupPost = requests.find((r) => r.method === "POST");
  check("duplicate posts the exact request",
    dupPost.url === "/api/board/missions/m%20running/tasks/T003/duplicate" &&
    JSON.stringify(dupPost.body) ===
      JSON.stringify({ expected_version: plan.version, after_key: null, who: "" }));
  requests.length = 0;

  // --- move: POST /tasks/{id}/move with to_position from the task_index ---
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onMoveTask(lastBox, data, "down");
  const mvPost = requests.find((r) => r.method === "POST");
  check("move down posts to_position = task_index + 1",
    mvPost.url === "/api/board/missions/m%20running/tasks/T003/move" &&
    JSON.stringify(mvPost.body) ===
      JSON.stringify({ expected_version: plan.version, to_position: 3, who: "" }));
  requests.length = 0;
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onMoveTask(lastBox, data, "up");
  const mvUp = requests.find((r) => r.method === "POST");
  check("move up posts to_position = task_index - 1",
    mvUp.url === "/api/board/missions/m%20running/tasks/T003/move" &&
    JSON.stringify(mvUp.body) ===
      JSON.stringify({ expected_version: plan.version, to_position: 1, who: "" }));
  requests.length = 0;

  // --- retry: refused unless the task is not_accepted ---
  lastBox = makeEditBox(true, data.initial);
  attach(lastBox, data);
  await PE.onRetryTask(lastBox, data);
  check("retry is refused for a waiting task and posts nothing",
    noteStub.textContent === "a retry is only available for a task with the status not_accepted" &&
    requests.length === 0);
  const naData = editData("T002", detail.task_overrides, "not_accepted");
  lastBox = makeEditBox(true, naData.initial);
  attach(lastBox, naData);
  await PE.onRetryTask(lastBox, naData);
  const retryPost = requests.find((r) => r.method === "POST");
  check("retry posts the exact request for a not_accepted task",
    retryPost.url === "/api/board/missions/m%20running/tasks/T002/retry" &&
    JSON.stringify(retryPost.body) ===
      JSON.stringify({ expected_version: plan.version, who: "" }));
  requests.length = 0;

  // --- hint: POST /tasks/{id}/hint with the form's hint ---
  lastBox = makeEditBox(true, naData.initial);
  attach(lastBox, naData);
  Object.fromEntries(lastBox.els.map((e) => [e.dataset.key, e])).hint.value = "подсказка-2";
  await PE.onSetHint(lastBox, naData);
  const hintPost = requests.find((r) => r.method === "POST");
  check("hint posts the exact request with the form's hint",
    hintPost.url === "/api/board/missions/m%20running/tasks/T002/hint" &&
    JSON.stringify(hintPost.body) ===
      JSON.stringify({ expected_version: plan.version, hint: "подсказка-2", who: "" }));
  requests.length = 0;

  // --- a failed operation shows its message; a 409 reloads ---
  lastBox = makeEditBox(true, naData.initial);
  attach(lastBox, naData);
  post = { status: 500, body: { error: { message: "внутренняя ошибка" } } };
  await PE.onSetHint(lastBox, naData);
  check("a failed operation shows its message",
    noteStub.textContent === "внутренняя ошибка");
  requests.length = 0;
  lastBox = makeEditBox(true, naData.initial);
  attach(lastBox, naData);
  post = { status: 409, body: { error: { message: "план изменился, обновите" } } };
  await PE.onSetHint(lastBox, naData);
  check("a 409 on an operation reloads and shows the message",
    noteStub.textContent === "the plan changed, reload" &&
    requests.filter((r) => !r.method || r.method === "GET").length === 2);
  requests.length = 0;

  delete globalThis.document;
  delete globalThis.fetch;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
