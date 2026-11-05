// Node test for every esc() copy in the board scripts (the T001 list).
// Run: node --test tests/board_esc.test.js
//
// Pins, per copy, what today's code returns for & < > " ', null/undefined,
// numbers and non-strings, the empty string, and a backtick. No copy escapes a
// backtick, so it passes through raw. Copies that delegate to BoardLogic are
// loaded twice: with globalThis.BoardLogic defined (the delegation branch) and
// without it (the local fallback), each in its own process so the module's
// first load picks the branch it sees. Differences between copies get their
// own check. No source file is changed by this test.

"use strict";
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
require("./i18n_stub.js"); // t() for the view scripts that need it

const SRC = path.join(__dirname, "..", "src", "ahawr_runner");
const CANONICAL = "&lt;a&amp;b&gt; &quot;c&#39;d `e`";
const INPUT = "<a&b> \"c'd `e`";
const CASES = [
  ["ampersand before angle brackets", "&", "&amp;"],
  ["less than becomes &lt;", "<", "&lt;"],
  ["greater than becomes &gt;", ">", "&gt;"],
  ["double quote becomes &quot;", "\"", "&quot;"],
  ["single quote becomes &#39;", "'", "&#39;"],
  ["backtick is left raw", "`", "`"],
  ["null returns empty string", null, ""],
  ["undefined returns empty string", undefined, ""],
  ["a number is stringified", 42, "42"],
  ["an object is stringified", { a: 1 }, "[object Object]"],
  ["the empty string stays empty", "", ""],
  ["mixed hostile text", INPUT, CANONICAL],
];

// The child script runs one copy's esc in a fresh process: BoardLogic defined
// for the delegation branch, absent for the local fallback. It prints the
// outputs for CASES as JSON so the parent can compare them.
const CHILD = [
  "if (globalThis.__WITH_LOGIC__) globalThis.BoardLogic = require(process.argv[1] + '/board_logic.js');",
  "const m = require(process.argv[2]);",
  "const cases = JSON.parse(process.argv[3]);",
  "process.stdout.write(JSON.stringify(cases.map((v) => m.esc(v))) + \"\");",
].join("\n");

function loadCopy(file, withLogic) {
  const result = spawnSync("node", ["-e", CHILD, SRC, path.join(SRC, file), JSON.stringify(CASES.map((case_) => case_[1]))],
    { encoding: "utf8", env: { ...process.env, __WITH_LOGIC__: withLogic ? "1" : "" } });
  if (result.status !== 0) throw new Error(`load of ${file} failed: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

// board.js is the shell bootstrap inside an IIFE with no CommonJS export, so
// its fallback block is lifted from the source text and run in a scope.
function loadBoardShell() {
  const text = fs.readFileSync(path.join(SRC, "board.js"), "utf8");
  const start = "// Local fallback for board_logic.js's esc()";
  const end = "function escValue(v) { return typeof esc === \"function\" ? esc(v) : localEsc(v); }";
  if (!text.includes(start) || !text.includes(end)) {
    throw new Error("board.js esc fallback anchors not found");
  }
  const block = text.slice(text.indexOf(start), text.indexOf(end) + end.length);
  return {
    local: new Function(block + "\nreturn localEsc;")(),
    escValueNoGlobal: new Function(block + "\nreturn escValue;")(),
    escValueWithGlobal: new Function(
      "const esc = (v) => 'GLOBAL:' + String(v);\n" + block + "\nreturn escValue;"
    )(),
  };
}

function check(name, fn) {
  fn();
  console.log("ok - " + name);
}

// The shell hands its helpers to board_control.js / board_guards.js in mount():
// ctx.esc is whatever the caller passes in, with no fallback of its own.
function shellCtxEsc(esc) {
  const ctx = { esc: esc, t: (key, en) => en };
  return new Function("ctx", "return ctx.esc(ctx.t('none', '—'));")(ctx);
}

// Copies reachable through module.exports.esc (delegation branch and fallback).
const EXPORTED_COPIES = [
  ["canonical esc in board_logic.js", "board_logic.js"],
  ["esc copy in board_dnd.js", "board_dnd.js"],
  ["esc copy in board_mission.js", "board_mission.js"],
  ["esc copy in board_mission_save.js", "board_mission_save.js"],
  ["esc copy in board_plan_edit.js", "board_plan_edit.js"],
  ["esc copy in board_qa.js", "board_qa.js"],
  ["esc copy in board_queue.js", "board_queue.js"],
  ["esc copy in board_task.js", "board_task.js"],
  ["esc copy in board_tasks.js", "board_tasks.js"],
  ["claudeMdEsc as esc in board_claude_md.js", "board_claude_md.js"],
  ["historyEsc as esc in board_history_ui.js", "board_history_ui.js"],
  ["readyEsc as esc in board_ready_ui.js", "board_ready_ui.js"],
  ["thinkEsc as esc in board_think_ui.js", "board_think_ui.js"],
];

for (const [name, file] of EXPORTED_COPIES) {
  for (const branch of ["delegation", "fallback"]) {
    const outputs = loadCopy(file, branch === "delegation");
    CASES.forEach(([label, input, expected], i) => {
      check(`${name} (${branch} branch): ${label}`, () => {
        if (outputs[i] !== expected) {
          throw new Error(
            `${JSON.stringify(input)} -> ${JSON.stringify(outputs[i])}, expected ${JSON.stringify(expected)}`
          );
        }
      });
    });
  }
}

// board_actions.js keeps its copy private: it is not exported, so no caller can
// reuse it and the merge cannot reach it.
check("board_actions.js esc is not exported", () => {
  const exports = require(path.join(SRC, "board_actions.js"));
  if (typeof exports.esc === "function") {
    throw new Error("board_actions.js exports esc");
  }
});

// board_actions.js has no generic HTML builder to call in node: its only esc()
// call sites are a button className (mkButton) and the confirm message
// (confirmActions), both which need DOM. Pin the copy's body from the source
// text: it is byte-for-byte the canonical fallback, so its output matches every
// other copy (the CASES loop above pins those bodies).
check("board_actions.js private esc is the canonical fallback body verbatim", () => {
  const text = fs.readFileSync(path.join(SRC, "board_actions.js"), "utf8");
  const start = "// esc() from board_logic.js, with the same local fallback the other";
  if (!text.includes(start)) throw new Error("board_actions.js esc anchor not found");
  const block = text.slice(text.indexOf(start), text.indexOf(start) + 500);
  for (const rule of ["/&/g", "&amp;", "</g", "&lt;", ">/g", "&gt;", '"/g', "&quot;", "'/g", "&#39;"]) {
    if (!block.includes(rule)) throw new Error(`board_actions.js esc lacks ${rule}`);
  }
});

// board_lessons_ui.js has no copy: it binds esc straight to BoardLogic.esc at
// load time, so a merge that removes one cannot reach it without BoardLogic.
globalThis.BoardLogic = require(path.join(SRC, "board_logic.js"));
const Lessons = require(path.join(SRC, "board_lessons_ui.js"));
const lessonsUi = { L: (key, en) => globalThis.t(key, en) };
check("board_lessons_ui.js delegates to BoardLogic.esc in its list HTML", () => {
  if (Lessons.listHtml([{ id: 1, status: "active", situation: INPUT, lesson: "" }], lessonsUi)
    .indexOf(CANONICAL) < 0) {
    throw new Error("the lessons list does not show the canonical escape");
  }
});

// board.js's escValue picks a global esc when one exists, its own fallback otherwise.
const shell = loadBoardShell();
check("board.js localEsc escapes like every other copy", () => {
  CASES.forEach(([label, input, expected]) => {
    if (shell.local(input) !== expected) {
      throw new Error(`${label}: got ${JSON.stringify(shell.local(input))}`);
    }
  });
});

check("board.js escValue uses a global esc when one is defined", () => {
  if (shell.escValueWithGlobal("<x>") !== "GLOBAL:<x>") {
    throw new Error(`got ${JSON.stringify(shell.escValueWithGlobal("<x>"))}`);
  }
});

check("board.js escValue falls back to localEsc when no global esc exists", () => {
  if (shell.escValueNoGlobal("<x>") !== "&lt;x&gt;") {
    throw new Error(`got ${JSON.stringify(shell.escValueNoGlobal("<x>"))}`);
  }
});

// The shell-passed helpers have no fallback: they crash when the caller passes
// no esc, and escape nothing when it passes an identity helper.
check("shell ctx.esc crashes when the caller passes no esc", () => {
  let error = null;
  try {
    shellCtxEsc(undefined);
  } catch (e) {
    error = e;
  }
  if (!(error instanceof TypeError)) {
    throw new Error(`expected a TypeError from ctx.esc, got ${error}`);
  }
});

check("shell ctx.esc escapes nothing when the caller passes an identity helper", () => {
  if (shellCtxEsc((v) => v) !== "—") {
    throw new Error(`got ${JSON.stringify(shellCtxEsc((v) => v))}, expected the raw em dash`);
  }
});

console.log("all ok");
