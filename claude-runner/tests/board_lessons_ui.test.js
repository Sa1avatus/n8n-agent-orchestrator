// Node test for board_lessons_ui.js (lessons list, actions, given-lessons view).
// Run: node tests/board_lessons_ui.test.js
//
// Proves:
//   1. the pure helpers: only changed fields make a diff, the list escapes lesson text
//      and offers retire for an active lesson and restore for a retired one, the
//      given-lessons view shows its empty state and its items;
//   2. a retire or edit asks for confirmation, sends the expected_version read with the
//      lesson, and writes nothing when the user declines;
//   3. a 409 conflict goes to notify and reloads; delete sends confirm=true.
"use strict";
const assert = require("assert");
const path = require("path");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
globalThis.BoardLogic = require(path.join(SRC, "board_logic.js"));
const Lessons = require(path.join(SRC, "board_lessons_ui.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed`);
}

const ACTIVE = {
  id: 7, status: "active", times_shown: 12, times_first_attempt_passed_after: 9,
  situation: "Tests <b>fail</b>", lesson: "Run & fix", updated_at: "2026-10-27T10:00:00Z",
};
const RETIRED = { ...ACTIVE, id: 8, status: "retired", situation: "Old", lesson: "Old lesson" };

const ui = {
  L: (key, en, params) => {
    let text = en;
    for (const k of Object.keys(params || {})) text = text.split("{" + k + "}").join(String(params[k]));
    return text;
  },
  notify: () => {},
  reload: () => {},
};

// --- 1. pure helpers ---
{
  assert.deepStrictEqual(Lessons.changedFields(ACTIVE, { status: "active" }), []);
  const changes = Lessons.changedFields(ACTIVE, { situation: "New", lesson: "Run & fix" });
  check("only the changed field", changes.length === 1 && changes[0].key === "situation");
  check("diff text", Lessons.diffText(changes) === 'situation: "Tests <b>fail</b>" -> "New"');

  const html = Lessons.listHtml([ACTIVE], ui);
  check("lesson text is escaped", html.includes("Tests &lt;b&gt;fail&lt;/b&gt;") && !html.includes("<b>fail"));
  check("shown and helped cells", html.includes("<td>12</td>") && html.includes("<td>9</td>"));
  check("active lesson offers retire", html.includes('data-action="retire" data-id="7"'));
  check("active lesson has no restore", !html.includes('data-action="restore"'));

  const retired = Lessons.listHtml([RETIRED], ui);
  check("retired lesson offers restore", retired.includes('data-action="restore" data-id="8"'));
  check("retired lesson has no retire", !retired.includes('data-action="retire"'));

  check("empty list state", Lessons.listHtml([], ui).includes("No lessons yet."));
  check("given: empty state", Lessons.givenHtml([], ui).includes("No lessons were given to this task."));
  const given = Lessons.givenHtml([ACTIVE], ui);
  check("given: item shows the lesson", given.includes("<li>Tests &lt;b&gt;fail&lt;/b&gt;: Run &amp; fix</li>"));
}

// --- 2 and 3. actions through mount() ---
function fakeContainer() {
  return { innerHTML: "", onclick: null };
}

function clickOn(container, action, id) {
  const button = { dataset: { action, id: String(id) } };
  return container.onclick({ target: { closest: () => button } });
}

async function runFlow() {
  const sent = [];
  const notes = [];
  let reloads = 0;
  let answer = true;
  let writeError = null;
  globalThis.confirm = () => answer;
  const flowUi = {
    ...ui,
    notify: (text) => notes.push(text),
    reload: () => { reloads += 1; },
    write: async (method, url, body) => {
      sent.push({ method, url, body });
      if (writeError) throw writeError;
      return {};
    },
  };

  const container = fakeContainer();
  Lessons.mount(container, [ACTIVE], flowUi);
  check("mount fills the container", container.innerHTML.includes("bvlessons"));

  answer = false;
  await clickOn(container, "retire", 7);
  check("declined retire writes nothing", sent.length === 0 && reloads === 0);

  answer = true;
  await clickOn(container, "retire", 7);
  check("retire posts expected_version",
    sent.length === 1 && sent[0].method === "POST" &&
    sent[0].url === "/api/board/lessons/7/retire" &&
    sent[0].body.expected_version === ACTIVE.updated_at);
  check("retire reloads", reloads === 1);

  writeError = Object.assign(new Error("conflict"), { status: 409 });
  await clickOn(container, "retire", 7);
  check("409 notifies the conflict and reloads",
    notes[notes.length - 1].startsWith("Version conflict") && reloads === 2);

  writeError = null;
  globalThis.prompt = (label, value) => (label === "Lesson text" ? "New text" : value);
  await clickOn(container, "edit", 7);
  const put = sent[sent.length - 1];
  check("edit puts the changed text with expected_version",
    put.method === "PUT" && put.url === "/api/board/lessons/7" &&
    put.body.lesson === "New text" && put.body.situation === ACTIVE.situation &&
    put.body.expected_version === ACTIVE.updated_at);

  await clickOn(container, "delete", 7);
  const del = sent[sent.length - 1];
  check("delete sends confirm=true", del.method === "DELETE" && del.url === "/api/board/lessons/7?confirm=true");
}

(async () => {
  const savedConfirm = globalThis.confirm;
  const savedPrompt = globalThis.prompt;
  try {
    await runFlow();
  } finally {
    globalThis.confirm = savedConfirm;
    globalThis.prompt = savedPrompt;
  }
  console.log("board_lessons_ui.test.js: all checks passed");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
