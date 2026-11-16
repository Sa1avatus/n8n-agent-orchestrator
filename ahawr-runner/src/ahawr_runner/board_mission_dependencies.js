/* board-scope-wrapped */
(function () {
/* Board mission dependency render (T024): the "after: <ids>" line in the
 * queue card, the "waiting" / "blocked" chip, the editor field parsing, the
 * refusal message for a move or save rejected by the dependency rule, the
 * dependents among the ids such a refusal named, the confirm questions of the
 * "move them too" and "run anyway" step, and the move request body
 * with move_dependents / run_anyway.
 *
 * Loaded by board.html via <script src="/static/board_mission_dependencies.js">
 * and usable from node via CommonJS require (UMD-style export below).
 *
 * Render-only: the dependency state comes from the API answer
 * (dependency_state / dependency_reason), it is never recomputed here, so the
 * browser cannot disagree with the queue rule in board_dependencies.py.
 * When the mission_dependencies flag is off the API answer has no depends_on
 * key and every renderer here returns "".
 *
 * Every value taken from data goes through esc() (board_logic.js's esc).
 * Texts come from t() (board_i18n.js); no external library, no outbound
 * request.
 */
"use strict";

// esc() lives in the shared board_logic.js; the require() covers node loads of
// this file standalone (board.html always loads board_logic.js first).
const esc = (typeof BoardLogic !== "undefined" ? BoardLogic : require("./board_logic.js")).esc;

// The only two states that get a chip; "due" and a missing state render "".
const CHIP_CLASS = { waiting: "dep-chip q-dep-waiting", blocked: "dep-chip q-dep-blocked" };

// The "after: <ids>" line for one queue/mission answer; "" when the answer
// has no depends_on field (the flag is off).
function renderDependencyLine(mission) {
  if (!mission || !("depends_on" in mission)) return "";
  const ids = (mission.depends_on || []).map(esc).join(", ");
  const text = ids === "" ? t("q.dep.none") : t("q.dep", { ids: ids });
  return `<span class="q-deps">${text}</span>`;
}

// The state chip: only "waiting" and "blocked" render one; the reason the API
// gives is the tooltip.  The state is read, never recomputed.
function renderDependencyChip(mission) {
  const state = mission ? mission.dependency_state : "";
  const classes = CHIP_CLASS[state];
  if (!classes) return "";
  const ids = (mission.depends_on || []).map(esc).join(", ");
  const label = state === "waiting"
    ? t("q.dep.waiting", { ids: ids })
    : t("q.dep.blocked", { ids: ids });
  const reason = mission.dependency_reason || (
    state === "waiting" ? t("q.dep.waiting.title", { ids: ids }) : t("q.dep.blocked.title", { ids: ids })
  );
  return `<span class="${classes}" title="${esc(reason)}">${label}</span>`;
}

// The editor field text (ids separated by commas or spaces) into a list; the
// server normalizes it with board_dependencies.build_dependencies.
function parseDependencyField(text) {
  return String(text == null ? "" : text)
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

// The stored list of ids as the editor field shows it ("a, b"); the same ids
// round-trip through parseDependencyField unchanged.
function formatDependencyField(ids) {
  if (!Array.isArray(ids)) return "";
  return ids.map((id) => String(id).trim()).filter((id) => id !== "").join(", ");
}

// The POST body of one queue move: the same fields as postMoveQueue plus
// move_dependents (move the missions that depend on it too).
function buildMoveBody(missionId, toPosition, expectedVersion, moveDependents) {
  return {
    mission_id: missionId,
    to_position: toPosition,
    expected_version: expectedVersion,
    move_dependents: Boolean(moveDependents),
  };
}

// The body of the explicit "run anyway" start of a waiting or blocked mission.
function buildStartBody(runAnyway) {
  return { run_anyway: Boolean(runAnyway) };
}

// Which of the ids a guard refusal named are the dependents of the moving
// mission: the queue rows whose stored depends_on lists that mission.  Pure —
// no request, no recomputation of the rule; the refusal answer names the ids
// without saying which way each one blocks the move.
function dependentsAmong(queueRows, missionId, blockedBy) {
  const rows = Array.isArray(queueRows) ? queueRows : [];
  const ids = Array.isArray(blockedBy) ? blockedBy : [];
  return ids.filter((id) => {
    const row = rows.find((r) => r && r.mission_id === id);
    return row && (row.depends_on || []).includes(missionId);
  });
}

// A refusal of the dependency guard: the code "guard" envelope of _refusal
// (board_api.py), carried on the error the POST helpers throw.  With the
// mission_dependencies flag off the guard never answers, so a request built
// without these helpers never reaches this path.
function isDependencyRefusal(e) {
  const body = (e && e.body) || {};
  const code = body.code || (body.error && body.error.code) || "";
  return code === "guard";
}

// The ids a guard refusal named, separated for display.
function blockedIdsText(payload) {
  const ids = payload && Array.isArray(payload.blocked_by) ? payload.blocked_by : [];
  return ids.map((id) => String(id)).join(", ");
}

// The confirm question for a move the guard refused because the missions that
// depend on it stand in the way; "" when nothing named is depends on it, so the
// caller shows the plain refusal message instead of asking.
function dependencyGroupConfirm(queueRows, missionId, payload) {
  const dependents = dependentsAmong(queueRows, missionId, payload && payload.blocked_by);
  if (!dependents.length) return "";
  return t("dep.group", { ids: dependents.join(", "), n: String(dependents.length) });
}

// The confirm question for an explicit "run anyway" of a waiting or blocked
// mission, naming the dependencies the refusal listed.
function dependencyRunConfirm(payload) {
  const ids = blockedIdsText(payload);
  return ids ? t("dep.anyway") + " (" + ids + ")" : t("dep.anyway");
}

// The visible message for a refused move or save (the _refusal / _error
// envelope of board_api.py): the i18n text when the payload carries the
// structured fields, the API message itself otherwise.
function dependencyRefusalMessage(payload, missionId) {
  const body = payload || {};
  const error = body.error || {};
  const code = body.code || error.code || "";
  const message = error.message || body.reason || "";
  if (code === "cycle") {
    const pair = body.closing_pair;
    if (Array.isArray(pair) && pair.length >= 2) {
      return t("dep.cycle", { a: esc(pair[0]), b: esc(pair[1]) });
    }
  }
  // A start or "Run next" the guard refused: the payload names the state, so the
  // move wording would be wrong for it.
  if (code === "guard" && body.dependency_state) {
    return t("dep.start", { id: esc(String(missionId)), ids: blockedIdsText(body) });
  }
  if (code === "guard" && Array.isArray(body.blocked_by) && body.blocked_by.length) {
    return t("dep.order", { id: esc(String(missionId)), ids: body.blocked_by.map(esc).join(", ") });
  }
  return message;
}

// === export (browser global + CommonJS module.exports) ===
const BoardMissionDependencies = {
  renderDependencyLine, renderDependencyChip, parseDependencyField,
  formatDependencyField, buildMoveBody, buildStartBody,
  dependencyRefusalMessage, dependentsAmong, esc,
  isDependencyRefusal, blockedIdsText, dependencyGroupConfirm,
  dependencyRunConfirm,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardMissionDependencies;
} else {
  globalThis.BoardMissionDependencies = BoardMissionDependencies;
}
})();
