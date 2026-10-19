// Node test for board_mission_save.js (save / reset / copy controls and the
// dirty-form merge).
// Run: node claude-runner/tests/board_mission_save.test.js
//
// Proves:
//   1. readForm/parseLines round-trip the listarea values the form renders
//      (["a","b"] -> "a\nb" -> ["a","b"]);
//   2. currentDiff() uses BoardLogic.diffConfig and reports exactly the
//      fields that differ from the initial values;
//   3. diffHtml() escapes every value from data (a "<" never appears raw);
//   4. formDirty() is true while any field differs from its initial value
//      and false once the form matches the initial values;
//   5. initialValue() reads mission / queue / config fields from the server
//      data the form was rendered from;
//   6. a dirty form merged with the server refresh keeps the local edits,
//      takes the server version, and sets server_changed when the queue
//      drifted (board_logic.js's mergeServerIntoForm);
//   7. a 409 on save keeps the "план изменился, обновите" banner (refresh
//      does not overwrite it; a drifted queue adds "на сервере есть
//      изменения" alongside), and a successful save re-fetches and forces
//      a clean re-render so formDirty() is false right after it;
//   8. renderClean() re-attaches the controls after wiping the form: the
//      saveReady attach guard is reset, so the savebar comes back.

"use strict";
const path = require("path");
require(path.join(__dirname, "i18n_stub.js"));
const MS = require(path.join(__dirname, "..", "src", "claude_runner", "board_mission_save.js"));
const BL = require(path.join(__dirname, "..", "src", "claude_runner", "board_logic.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}

// --- parseLines round-trip ---
check("parseLines basic", JSON.stringify(MS.parseLines("a\nb")) === '["a","b"]');
check("parseLines blank", JSON.stringify(MS.parseLines("")) === "[]");
check("parseLines blank lines dropped",
  JSON.stringify(MS.parseLines("a\n\nb")) === '["a","b"]');
check("parseLines json line kept as string",
  JSON.stringify(MS.parseLines("[a]")) === '["[a]"]');
check("parseLines array line parsed",
  JSON.stringify(MS.parseLines("[1,2]")) === "[[1,2]]");
check("parseLines object line parsed",
  JSON.stringify(MS.parseLines('{"x":1}')) === '[{"x":1}]');

// --- readForm via a minimal DOM-free stub ---
// build a tiny box stub with querySelectorAll over a fixed element list
function stubBox(elements) {
  return {
    dataset: { saveReady: "0" },
    querySelectorAll: (sel) => elements,
    querySelector: () => null,
    closest: () => null,
  };
}
function field(key, kind, value, type, checked) {
  return {
    dataset: { key, kind },
    type: type || "text",
    value: value == null ? "" : value,
    checked: checked === true,
    closest: () => null,
  };
}

const data = {
  mission: { mission_id: "m1", name: "A", objective: "цель",
             working_directory: "/tmp/x", rules: ["r1", "r2"],
             acceptance_criteria: ["c1"], updated_at: "v1" },
  queue: [{ position: 0, mission_id: "m1", enabled: true, attempts_per_task: 5 }],
  config: { worker_model: "m1", worker_think_policy: { a: 1 } },
  version: "qv1",
};

// initial values from the server data
check("initialValue mission field", MS.initialValue("mission", "name", data) === "A");
check("initialValue queue field", MS.initialValue("queue", "attempts_per_task", data) === 5);
check("initialValue config field",
  JSON.stringify(MS.initialValue("config", "worker_think_policy", data)) === '{"a":1}' &&
  JSON.stringify(MS.initialValue("config", "worker_model", data)) === '"m1"');

// captureInitial via a stub box (dataset keys come from the form markup)
const boxEls = [
  field("name", "mission", "A"),
  field("rules", "listarea", "r1\nr2"),
  field("enabled", "queue", "", "checkbox", true),
  field("worker_model", "config", "m1"),
];
const box = stubBox(boxEls);
MS.captureInitial(box, data);
check("captureInitial mission id", MS.SaveState.missionId === "m1");
check("captureInitial name", MS.SaveState.initial.name === "A");
check("captureInitial rules (array)", JSON.stringify(MS.SaveState.initial.rules) === '["r1","r2"]');
check("captureInitial enabled (bool)", MS.SaveState.initial.enabled === true);

// real markup: dataset.kind is mission/queue/config and the control kind is dataset.control (a form that was not edited has no diff)
{
  const real = [
    { dataset: { key: "name", kind: "mission", control: "text" }, type: "text", value: "A", checked: false, closest: () => null },
    { dataset: { key: "rules", kind: "mission", control: "listarea" }, type: "textarea", value: "r1\nr2", checked: false, closest: () => null },
    { dataset: { key: "enabled", kind: "queue", control: "bool" }, type: "checkbox", value: "1", checked: true, closest: () => null },
    { dataset: { key: "attempts_per_task", kind: "queue", control: "number" }, type: "number", value: "5", checked: false, closest: () => null },
    { dataset: { key: "max_tasks", kind: "config", control: "number" }, type: "number", value: "", checked: false, closest: () => null },
    { dataset: { key: "worker_think_policy", kind: "config", control: "json" }, type: "textarea", value: '{"a": 1}', checked: false, closest: () => null },
  ];
  const realBox = stubBox(real);
  MS.captureInitial(realBox, data);
  check("real markup: untouched form has no diff", MS.currentDiff(realBox).length === 0 && MS.formDirty(realBox) === false);
  const form = MS.readForm(realBox);
  check("real markup: lists stay arrays, numbers numbers, bool bool",
    Array.isArray(form.rules) && form.attempts_per_task === 5 && form.max_tasks === null && form.enabled === true && form.worker_think_policy.a === 1);
  real[0].value = "B";
  const one = MS.currentDiff(realBox);
  check("real markup: one edit, one diff row", one.length === 1 && one[0].field === "name");
  // a mission that is not in board.queue yet takes its enabled flag from the mission row
  const noQueue = { ...data, queue: [], mission: { ...data.mission, enabled: "True" } };
  check("enabled falls back to the mission flag", MS.initialValue("queue", "enabled", noQueue, "bool") === true);
}

MS.captureInitial(box, data);  // back to the first form
// formDirty: clean form
check("formDirty clean", MS.formDirty(box) === false);
// dirty form: change a field
boxEls[0].value = "B";
check("formDirty dirty", MS.formDirty(box) === true);

// currentDiff: exactly the changed field
const diff = MS.currentDiff(box);
check("currentDiff one changed field",
  diff.length === 1 && diff[0].field === "name" &&
  diff[0].old === "A" && diff[0].new === "B");
// reset the value back
boxEls[0].value = "A";
check("currentDiff none after reset", MS.currentDiff(box).length === 0);

// diffHtml escapes data values
const d = BL.diffConfig({ a: "x<b" }, { a: "y" });
check("diffHtml escapes <", MS.diffHtml(d, { a: "A" }).indexOf("x&lt;b") !== -1);
check("diffHtml no raw <", MS.diffHtml(d, { a: "A" }).indexOf("x<b") === -1);
check("diffHtml none message",
  MS.diffHtml([], {}).indexOf("No changes.") !== -1);

// mergeServerIntoForm keeps local edits, takes server version, flags drift
const form = { version: "qv1", queue: [{ position: 0, mission_id: "m1" }],
               dirty_fields: ["name"], name: "edited" };
const server = { version: "qv2", queue: [{ position: 0, mission_id: "m2" }] };
const merged = BL.mergeServerIntoForm(form, server);
check("merge keeps local name", merged.name === "edited");
check("merge takes server version", merged.version === "qv2");
check("merge sets server_changed", merged.server_changed === true);
const same = BL.mergeServerIntoForm(form, { version: "qv2", queue: form.queue });
check("merge clears flag when in sync", !("server_changed" in same));

// --- doSave 409 / dirty banner / post-save clean re-render (fetch stubbed) ---
// The real showBanner writes box.querySelector(".savepanel").querySelector
// (".savebanner").textContent, so the panel stub returns a shared banner
// object the test can read.  document.createElement is stubbed so the
// post-save renderClean()->attachControls() path works in node.

const bannerStub = { textContent: "", hidden: true };
function panelStub() {
  return {
    querySelector: (s) =>
      s === ".savebanner" ? bannerStub : { hidden: true, innerHTML: "" },
    appendChild: () => {},
  };
}
// createElement returns an element that records its children (appendChild),
// so the panel and bar renderClean's attachControls creates can be found by
// querySelector in the box stub below.
globalThis.document = {
  createElement: () => {
    const el = { _children: [] };
    el.appendChild = (c) => el._children.push(c);
    el.addEventListener = () => {};
    return el;
  },
};

// Box stub: querySelector(".savepanel") returns a panel stub, so the real
// showBanner / diff/banner wiring works without a DOM.
function panelBox() {
  return {
    dataset: { saveReady: "0" },
    querySelectorAll: (sel) => (sel === "[data-key]" ? boxEls : []),
    querySelector: (sel) =>
      sel === ".savepanel" ? panelStub() : null,
  };
}

// A mutable "server state" the GET returns; a successful POST applies its
// fields to it, mirroring the board API (mission fields to the mission row,
// config fields to the config object).
let serverDetail = { mission: Object.assign({}, data.mission),
                      config: Object.assign({}, data.config) };
let postStatus = 200;
let postErr = {};
let getQueue = data.queue;
let getQueueVersion = data.version;
// A mutable "server state" the GET returns; a successful POST applies its
// fields to it, mirroring the board API (mission fields to the mission row,
// config fields to the config object).
globalThis.fetch = (url, options) => {
  const isPost = options && options.method === "POST";
  if (isPost && postStatus !== 409) {
    const body = JSON.parse(options.body);
    if (String(url).endsWith("/config")) Object.assign(serverDetail.config, body);
    else Object.assign(serverDetail.mission, body);
  }
  return Promise.resolve({
    ok: isPost ? postStatus !== 409 : true,
    status: isPost ? postStatus : 200,
    json: () => Promise.resolve(
      isPost
        ? (postStatus === 409 ? postErr : {})
        : (String(url).endsWith("/queue")
            ? { queue: getQueue, version: getQueueVersion }
            : serverDetail)
    ),
  });
};

// 409 on POST: the banner must read "план изменился, обновите".
boxEls[0].value = "B"; // dirty form
postStatus = 409;
postErr = { error: { message: "план изменился, обновите" } };
(async () => {
  await MS.doSave(panelBox());
  check("409 keeps banner", bannerStub.textContent === "The plan changed, refresh.");

  // 409 with a drifted server queue: the note is kept alongside.
  getQueue = [{ position: 0, mission_id: "m2" }];
  await MS.doSave(panelBox());
  check("409 + drifted queue adds note",
    bannerStub.textContent === "The plan changed, refresh. — there are changes on the server.");

  // Successful save: re-fetch + forced clean re-render, form clean after.
  postStatus = 200;
  getQueue = data.queue;
  getQueueVersion = "qv2";
  await MS.doSave(panelBox());
  check("post-save form clean", MS.formDirty(panelBox()) === false);
  check("post-save banner cleared", bannerStub.textContent === "");
  check("post-save initial refreshed",
    MS.SaveState.initial.name === "B" && MS.SaveState.data.version === "qv2");

  // renderClean re-attaches the controls after wiping the form.  The box
  // stub keeps dataset.saveReady across innerHTML wipes (the form was
  // attached once already, so it is "1"), and like the real DOM assigning
  // innerHTML clears the old panel — the savebar must be back after
  // renderClean, which only happens when attachControls re-runs.
  const rcState = { saveReady: "1" };
  let rcPanel = { _children: [{ className: "savebar" }] };
  function rcBox() {
    const b = {
      dataset: rcState,
      querySelectorAll: (sel) => (sel === "[data-key]" ? boxEls : []),
      querySelector: (sel) => {
        if (sel === ".savepanel") return rcPanel;
        if (sel === ".savebar")
          return rcPanel
            ? rcPanel._children.find((c) => c.className === "savebar")
            : null;
        return null;
      },
      prepend: (el) => { rcPanel = el; },
    };
    Object.defineProperty(b, "innerHTML", { set: () => { rcPanel = null; } });
    return b;
  }
  globalThis.BoardMissionView = { renderMission: () => "<div></div>" };
  MS.renderClean(rcBox(), data);
  check("renderClean re-attaches controls", rcBox().querySelector(".savebar") != null);
  delete globalThis.BoardMissionView;

  delete globalThis.fetch;
  delete globalThis.document;
  console.log("all ok");
})();
