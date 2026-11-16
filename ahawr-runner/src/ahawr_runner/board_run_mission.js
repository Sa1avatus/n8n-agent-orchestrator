/* board-scope-wrapped */
(function () {
/* Run buttons of a kanban card (mission ahawr-board-run-mission): "Run next"
 * sends the mission to the front of board.queue, "Run now" does the same and
 * wakes the queue (POST /api/board/missions/{id}/run-next and /run-now).
 *
 * The buttons exist only when the run_mission_from_card flag is on (the caller
 * passes flagOn); with the flag off nothing is added to a card, so the markup
 * is byte-for-byte what it was.  The block reason of a button comes from the
 * stored board data (the queue row and the state group), never from a reason
 * text.  The read-only page hides the buttons by their labels
 * (board_readonly.js) and the server answers 403 there.
 *
 * Loaded by board.html via <script src="/static/board_run_mission.js"> and
 * usable from node via CommonJS require.  Every text goes through t().
 */
"use strict";

// The reason codes board_run_mission.py answers with; the UI maps them to texts.
const REASONS = ["disabled", "failed_without_retry", "running", "completed"];
const REASON_PATTERN = new RegExp(": (" + REASONS.join("|") + ")$");

const ACTIONS = {
  next: { path: "run-next", label: "action.run_next", confirm: "run.confirm.next" },
  now: { path: "run-now", label: "action.run_now", confirm: "run.confirm.now" },
};

// The reason a card cannot be run, or null.  Stored data only: the state group
// of the board answer, the queue row (enabled, retry_failed).
function blockReason(data, missionId, groupKey) {
  const entry = data && Array.isArray(data.queue)
    ? data.queue.find((row) => row.mission_id === missionId) : null;
  if (groupKey === "running") return "running";
  if (groupKey === "completed") return "completed";
  if (groupKey === "disabled" || (entry && entry.enabled === false)) return "disabled";
  if (groupKey === "not_completed" && !(entry && entry.retry_failed)) return "failed_without_retry";
  return null;
}

// The request of one button: the route and the body with the queue version.
function runRequest(kind, missionId, version) {
  return {
    url: "/api/board/missions/" + encodeURIComponent(missionId) + "/" + ACTIONS[kind].path,
    body: { expected_version: version },
  };
}

// The text shown after an answer: the moved / resume queued / after running codes.
function answerKey(kind, answer) {
  if (kind === "now" && answer && answer.status === "after_running") return "run.after_running";
  if (kind === "now" && answer && answer.status === "resume_queued") return "run.resume_queued";
  return "run.moved";
}

// The text of a refused request: the reason code at the end of the API message
// is translated, any other message is shown as it came.
function refusalText(error, tr) {
  const message = String((error && error.message) || "");
  const found = REASON_PATTERN.exec(message);
  return found ? tr("run.reason." + found[1]) : message || tr("run.failed");
}

// Add the two buttons to a card.  deps: { data, flagOn, post, show, refresh, confirm, tr }.
// Idempotent (dataset.runReady); a card of the Completed column and a flag that is
// off add nothing.
function attachRunButtons(card, deps) {
  if (!deps.flagOn || card.dataset.runReady === "1") return null;
  const missionId = card.dataset.missionId;
  const groupKey = card.dataset.group || "";
  if (groupKey === "completed") return null;
  card.dataset.runReady = "1";
  const reason = blockReason(deps.data, missionId, groupKey);
  const group = document.createElement("span");
  group.className = "qactions qrun";
  for (const kind of Object.keys(ACTIONS)) {
    const label = deps.tr(ACTIONS[kind].label);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "qact qact-run-" + kind;
    button.textContent = label;
    button.title = reason ? deps.tr("run.reason." + reason) : label;
    button.disabled = Boolean(reason);
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      runCard(kind, missionId, deps);
    });
    group.appendChild(button);
  }
  card.appendChild(group);
  return group;
}

// One click: the confirm step, the request, the message, the board refresh.
async function runCard(kind, missionId, deps) {
  if (!deps.confirm(deps.tr(ACTIONS[kind].confirm, { id: missionId }))) return false;
  const { url, body } = runRequest(kind, missionId, deps.data ? deps.data.version : null);
  try {
    const answer = await deps.post(url, body);
    deps.show(deps.tr(answerKey(kind, answer)), "ok");
  } catch (error) {
    deps.show(refusalText(error, deps.tr), "bad");
  }
  await deps.refresh();
  return true;
}

// === export (browser global + CommonJS module.exports) ===
const BoardRunMission = { blockReason, runRequest, answerKey, refusalText, attachRunButtons, runCard };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardRunMission;
} else {
  globalThis.BoardRunMission = BoardRunMission;
}
})();
