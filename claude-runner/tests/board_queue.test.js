// Node test for board_queue.js (the top-level board render).
// Run: node claude-runner/tests/board_queue.test.js

"use strict";
const path = require("path");
require(path.join(__dirname, "i18n_stub.js"));
const BQ = require(path.join(__dirname, "..", "src", "claude_runner", "board_queue.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- the five groups and the card fields ---
const data = {
  groups: {
    running: ["m1"],
    queued: ["m2", "m3"],
    completed: ["m4"],
    not_completed: ["m5"],
    disabled: ["m6"],
  },
  missions: [
    { mission_id: "m1", name: "Source mission" },
    { mission_id: "m2", name: "Queued one" },
    { mission_id: "m3", name: "Queued two" },
    { mission_id: "m4", name: "Done" },
    { mission_id: "m5", name: "Stopped" },
    { mission_id: "m6", name: "Off" },
  ],
  queue: [
    { position: 0, mission_id: "m1" },
    { position: 1, mission_id: "m2" },
    { position: 2, mission_id: "m3" },
    { position: 3, mission_id: "m4" },
    { position: 4, mission_id: "m5" },
    { position: 5, mission_id: "m6" },
  ],
  version: "2026-01-01T00:00:00+00:00",
  cards: {
    m1: {
      live_state: { status: "active", task_index: 1 },
      task_views: [
        { task_id: "T001", status: "completed" },
        { task_id: "T002", status: "running" },
        { task_id: "T003", status: "waiting" },
      ],
      stats: {
        worker_hours: 0.25, reviewer_minutes: 5,
        input_tokens: 400000, output_tokens: 40000,
        cache_read_tokens: 50000, cache_write_tokens: 10000,
        cost_usd: 2.35, tok_per_s: 10000,
        cache_ratio: 0.1,
        thinking_runs: 3, thinking_steps: 4,
        tasks: { T001: { attempts: 2 }, T002: { attempts: 1 } },
      },
    },
    m2: { live_state: { status: "queued", task_index: null },
      task_views: [{ task_id: "T001" }, { task_id: "T002" }],
      stats: { worker_hours: 0, reviewer_minutes: 0, input_tokens: 0,
        output_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
        cost_usd: 0, tok_per_s: 0, cache_ratio: 0,
        thinking_runs: 0, thinking_steps: 0, tasks: {} } },
  },
};

const html = BQ.renderQueue(data);

// the five group headers, in order
check("group order",
  html.indexOf("Running now") < html.indexOf("Queued") &&
  html.indexOf("Queued") < html.indexOf("Completed") &&
  html.indexOf("Completed") < html.indexOf("Not completed / stopped") &&
  html.indexOf("Not completed / stopped") < html.indexOf("Disabled"));
check("five sections", (html.match(/class="qgroup"/g) || []).length === 5);
check("all mission ids", data.missions
  .every((m) => has(html, `data-mission-id="${m.mission_id}"`)));

// m1's card fields
check("m1 name", has(html, "Source mission"));
check("m1 state", has(html, "data-group=\"running\""));
check("m1 task 2 of 3", has(html, "2 of 3"));
check("m1 progress 67%", has(html, "width:67%"));
check("m1 attempts 3", has(html, "3 attempts"));
check("m1 time 20 min", has(html, "20 min"));
check("m1 tokens 440.0K", has(html, "440.0K tokens"));
check("m1 cache 10%", has(html, "10% cache"));
check("m1 cost", has(html, "$2.35"));
check("m1 speed", has(html, "10000 tok/s"));
check("m1 thinking 75%", has(html, "75% thinking"));

// m2: no task index -> "—", zero stats
check("m2 name", has(html, "Queued one"));
check("m2 no task", has(html, ">—</span><span class=\"qbar\""));
check("m2 zero attempts", has(html, "0 attempts · 0 min"));
check("m2 no cache", has(html, "— cache"));
check("m2 zero speed", has(html, "0 tok/s"));

// links open #board/<mission_id>
check("link m1", has(html, `href="#board/m1"`));
check("link m2", has(html, `href="#board/m2"`));
check("link m6", has(html, `href="#board/m6"`));

// no drag-and-drop / no actions: no drag handlers or buttons in the cards
check("no drag", !/<(button|input)/.test(html) && !/ondrag|dragstart/.test(html) && /draggable="false"/.test(html));

// --- esc(): every value goes through esc() ---
const evilId = 'a"b<c';
const evil = {
  groups: { running: [], queued: [evilId], completed: [],
    not_completed: [], disabled: [] },
  missions: [{ mission_id: evilId, name: 'x<script>"&' }],
  queue: [{ position: 0, mission_id: evilId }],
  cards: {},
};
const evilHtml = BQ.renderQueue(evil);
check("esc script tag", has(evilHtml, "x&lt;script&gt;&quot;&amp;"));
check("esc id in href", has(evilHtml, 'href="#board/a&quot;b&lt;c"'));
check("esc name raw", !has(evilHtml, "x<script>"));
check("esc group key", has(evilHtml, 'data-group="queued"'));

// empty board: the five headers stay, cards are "—"
const empty = BQ.renderQueue({ groups: {}, missions: [], queue: [], cards: {} });
check("empty groups kept",
  empty.indexOf("Running now") !== -1 && empty.indexOf("Disabled") !== -1);
check("empty cards", empty.split("qempty").length === 6); // one per group

// --- render() on a minimal fake document ---
// 'boardqueue' is missing at first: render must create it and append it to
// #board, then clear #boardstate.
const appended = [];
const boardStub = { appendChild(el) { appended.push(el); } };
const stateStub = { textContent: "Loading the board…", className: "bad" };
globalThis.document = {
  getElementById(id) {
    if (id === "boardqueue") return null;
    if (id === "board") return boardStub;
    if (id === "boardstate") return stateStub;
    return null;
  },
  createElement(tag) {
    const el = { tagName: tag, id: "", innerHTML: "", children: [] };
    el.appendChild = (c) => { el.children.push(c); };
    return el;
  },
};
BQ.render(data);
check("boardqueue created", appended.length === 1 && appended[0].id === "boardqueue");
check("boardqueue content",
  appended[0].innerHTML.indexOf("Running now") !== -1 &&
  appended[0].innerHTML.indexOf("Source mission") !== -1);
check("boardstate cleared", stateStub.textContent === "" && stateStub.className === "");

// a second render reuses the existing #boardqueue (no second append)
globalThis.document.getElementById = (id) =>
  (id === "boardqueue" ? appended[0] : (id === "board" ? boardStub : stateStub));
BQ.render(data);
check("boardqueue reused", appended.length === 1);
delete globalThis.document;

console.log("all ok");
