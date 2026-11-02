// Node test for the lessons section of board_task.js (the task view).
// Run: node tests/board_task_lessons.test.js
//
// Proves:
//   1. with no lessons (an empty list or undefined) the task view has no
//      lessons section, and its output is the output with that section removed;
//   2. with one lesson the section shows its heading and the escaped item.
"use strict";
const path = require("path");
const fs = require("fs");
const BT = require(path.join(__dirname, "..", "src", "claude_runner", "board_task.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed`);
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

const plan = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "board_part2", "plan.json"), "utf8")
);
const stats = JSON.parse(
  fs.readFileSync(path.join(__dirname, "fixtures", "board_part2", "task_stats.json"), "utf8")
);
const view = plan.tasks[0];
const lesson = { situation: "a <b>", lesson: "c" };

const noLessons = BT.renderTask("m1", "T001", view, stats, []);
const noLessonsUndefined = BT.renderTask("m1", "T001", view, stats);
check("an empty list shows no lessons heading",
  !has(noLessons, "Lessons given to this task") && !has(noLessons, "No lessons were given"));
check("undefined lessons show no lessons heading",
  !has(noLessonsUndefined, "Lessons given to this task"));
check("undefined and empty lessons render the same output",
  noLessons === noLessonsUndefined);

const section = BT.givenLessonsHtml([lesson]);
const withLesson = BT.renderTask("m1", "T001", view, stats, [lesson]);
check("the section is exactly the given-lessons block",
  has(withLesson, section) && section.length > 0);
check("removing the section gives the no-lessons output",
  withLesson.split(section).join("") === noLessons);
check("the section shows the heading", has(withLesson, "<h4>Lessons given to this task</h4>"));
check("the section escapes the lesson item",
  has(withLesson, "<li>a &lt;b&gt;: c</li>") && !has(withLesson, "<b>"));

console.log("board_task_lessons.test.js: all checks passed");
