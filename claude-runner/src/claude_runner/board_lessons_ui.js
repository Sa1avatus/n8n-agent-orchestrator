/* board-scope-wrapped */
(function () {
/* Board lessons panel (T012): the mission's lessons with their status, shown
 * and helped counts, the retire, restore, edit and delete actions, and the
 * list of lessons given to one task.
 *
 * BoardLessonsUI.mount(container, lessons, ui) fills the container and handles
 * its clicks; lessons is the list the host read from GET
 * /api/board/missions/{id}/lessons. ui comes from the host: L(key, en, params),
 * write(method, url, body) (sends with the dashboard header and rejects with
 * err.status on an error answer), notify(text) and reload().
 *
 * A change shows what changes and asks for confirmation before it writes; a
 * save carries the expected_version read with the lesson. A 409 conflict goes
 * to notify and reloads. Delete asks for confirmation and sends confirm=true.
 * The panel has no timer or poller; its only listener is the container's
 * onclick, which goes away with the container.
 */
"use strict";

const STATUS_KEYS = {
  active: ["lessons.status.active", "Active"],
  retired: ["lessons.status.retired", "Retired"],
};

const ACTION_KEYS = {
  retire: ["lessons.act.retire", "Retire"],
  restore: ["lessons.act.restore", "Restore"],
  edit: ["lessons.act.edit", "Edit"],
  delete: ["lessons.act.delete", "Delete"],
};

const esc = BoardLogic.esc;

function lessonUrl(id, suffix) {
  return "/api/board/lessons/" + encodeURIComponent(id) + (suffix || "");
}

function changedFields(lesson, after) {
  return Object.keys(after)
    .filter((key) => lesson[key] !== after[key])
    .map((key) => ({ key, before: lesson[key], after: after[key] }));
}

function diffText(changes) {
  return changes
    .map((change) => change.key + ": " + JSON.stringify(change.before) + " -> " + JSON.stringify(change.after))
    .join("\n");
}

function actionButtons(lesson, ui) {
  const actions = lesson.status === "active" ? ["retire"] : ["restore"];
  actions.push("edit", "delete");
  return actions.map((action) => {
    const [key, en] = ACTION_KEYS[action];
    const label = esc(ui.L(key, en));
    return '<button type="button" data-action="' + action + '" data-id="' + esc(lesson.id) + '">' + label + "</button>";
  }).join(" ");
}

function lessonRow(lesson, ui) {
  const [statusKey, statusEn] = STATUS_KEYS[lesson.status];
  return "<tr><td>" + esc(ui.L(statusKey, statusEn)) + "</td>" +
    "<td>" + esc(lesson.times_shown) + "</td>" +
    "<td>" + esc(lesson.times_first_attempt_passed_after) + "</td>" +
    "<td>" + esc(lesson.situation) + "<br>" + esc(lesson.lesson) + "</td>" +
    "<td>" + actionButtons(lesson, ui) + "</td></tr>";
}

function listHtml(lessons, ui) {
  const heads = [
    ["lessons.col.status", "Status"],
    ["lessons.col.shown", "Shown"],
    ["lessons.col.helped", "Helped"],
    ["lessons.col.lesson", "Lesson"],
    ["lessons.col.actions", "Actions"],
  ].map(([key, en]) => "<th>" + esc(ui.L(key, en)) + "</th>").join("");
  const body = lessons.length
    ? lessons.map((lesson) => lessonRow(lesson, ui)).join("")
    : '<tr><td colspan="5">' + esc(ui.L("lessons.empty", "No lessons yet.")) + "</td></tr>";
  return '<table class="bvlessons"><thead><tr>' + heads + "</tr></thead><tbody>" + body + "</tbody></table>";
}

function givenHtml(lessons, ui) {
  const title = "<h4>" + esc(ui.L("lessons.given.title", "Lessons given to this task")) + "</h4>";
  if (!lessons.length) {
    return title + "<p>" + esc(ui.L("lessons.given.empty", "No lessons were given to this task.")) + "</p>";
  }
  const items = lessons.map((lesson) => "<li>" + esc(lesson.situation) + ": " + esc(lesson.lesson) + "</li>");
  return title + "<ul>" + items.join("") + "</ul>";
}

function failed(err, ui) {
  if (err.status !== 409) return ui.notify(err.message);
  ui.notify(ui.L("lessons.conflict", "Version conflict: the lesson was changed since it was read. The page is reloaded; check the values and try again."));
  return ui.reload();
}

async function applyChange(lesson, after, send, ui) {
  const changes = changedFields(lesson, after);
  if (!changes.length) return ui.notify(ui.L("lessons.nothing", "No changes to save."));
  const question = ui.L("lessons.confirm", "Apply these changes to lesson {id}?\n{diff}",
    { id: lesson.id, diff: diffText(changes) });
  if (!globalThis.confirm(question)) return undefined;
  try {
    await send();
    ui.notify(ui.L("lessons.saved", "Lesson {id} saved.", { id: lesson.id }));
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

function editLesson(lesson, ui) {
  const situation = globalThis.prompt(ui.L("lessons.edit.situation", "Situation"), lesson.situation);
  if (situation === null) return undefined;
  const text = globalThis.prompt(ui.L("lessons.edit.lesson", "Lesson text"), lesson.lesson);
  if (text === null) return undefined;
  const after = { situation, lesson: text };
  const send = () => ui.write("PUT", lessonUrl(lesson.id), { ...after, expected_version: lesson.updated_at });
  return applyChange(lesson, after, send, ui);
}

function setStatus(lesson, status, ui) {
  const suffix = status === "retired" ? "/retire" : "/restore";
  const send = () => ui.write("POST", lessonUrl(lesson.id, suffix), { expected_version: lesson.updated_at });
  return applyChange(lesson, { status }, send, ui);
}

async function deleteLesson(lesson, ui) {
  const question = ui.L("lessons.delete.confirm", "Delete lesson {id} for good? It cannot be undone.", { id: lesson.id });
  if (!globalThis.confirm(question)) return undefined;
  try {
    await ui.write("DELETE", lessonUrl(lesson.id, "?confirm=true"));
    ui.notify(ui.L("lessons.deleted", "Lesson {id} deleted.", { id: lesson.id }));
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

function runAction(action, lesson, ui) {
  if (action === "edit") return editLesson(lesson, ui);
  if (action === "delete") return deleteLesson(lesson, ui);
  return setStatus(lesson, action === "retire" ? "retired" : "active", ui);
}

function mount(container, lessons, ui) {
  container.innerHTML = listHtml(lessons, ui);
  container.onclick = (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return undefined;
    const lesson = lessons.find((item) => String(item.id) === button.dataset.id);
    return lesson ? runAction(button.dataset.action, lesson, ui) : undefined;
  };
}

const BoardLessonsUI = {
  mount,
  listHtml,
  givenHtml,
  changedFields,
  diffText,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardLessonsUI;
} else {
  globalThis.BoardLessonsUI = BoardLessonsUI;
}
})();
