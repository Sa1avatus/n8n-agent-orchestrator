/* board-scope-wrapped */
(function () {
/* QA verdict panel for one mission (read-only).
 *
 * BoardQa.render(container, missionId) reads
 * GET /api/board/missions/{mission_id}/qa and shows the last QA verdict: the
 * verdict, the scenario, the failed step with its reason, the blocked reason,
 * the console and network errors, and the screenshots as links.  There is no
 * rerun control.  No verdict shows the empty state.
 *
 * Every value from the server flows through esc().
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

const VERDICT_LABELS = {
  pass: ["qa.panel.v.pass", "passed"],
  fail: ["qa.panel.v.fail", "failed"],
  blocked: ["qa.panel.v.blocked", "blocked"],
};

function verdictLabel(verdict) {
  const [key, en] = VERDICT_LABELS[verdict] || ["qa.panel.v.unknown", "unknown"];
  return L(key, en);
}

function rowHtml(label, value) {
  return '<div class="qa-row"><span class="qa-label">' + esc(label) + "</span> " + value + "</div>";
}

function listHtml(items) {
  return '<ul class="qa-list">' + items.map((item) => `<li>${esc(item)}</li>`).join("") + "</ul>";
}

function shotsHtml(shots) {
  const links = shots.map((shot) =>
    `<li><a href="${esc(shot.url)}" target="_blank" rel="noopener">${esc(shot.name)}</a></li>`);
  return '<ul class="qa-shots">' + links.join("") + "</ul>";
}

function verdictBodyHtml(qa) {
  const rows = [
    `<div class="qa-verdict qa-${esc(qa.verdict)}">${esc(verdictLabel(qa.verdict))}</div>`,
    rowHtml(L("qa.panel.scenario", "scenario"), esc(qa.scenario)),
  ];
  if (qa.failed_step != null) {
    const step = L("qa.panel.step", "failed step {n}", { n: qa.failed_step + 1 });
    rows.push(`<div class="qa-step qa-step-failed">${esc(step)}: ${esc(qa.failed_reason)}</div>`);
  }
  if (qa.blocked_reason) {
    rows.push(rowHtml(L("qa.panel.blocked_reason", "blocked reason"), esc(qa.blocked_reason)));
  }
  const errors = [...(qa.console_errors || []), ...(qa.network_errors || [])];
  if (errors.length) rows.push(rowHtml(L("qa.panel.errors", "console and network errors"), listHtml(errors)));
  if (qa.screenshots && qa.screenshots.length) {
    rows.push(rowHtml(L("qa.panel.shots", "screenshots"), shotsHtml(qa.screenshots)));
  }
  return rows.join("");
}

function panelHtml(qa) {
  const title = `<div class="qa-title">${esc(L("qa.panel.title", "QA verdict"))}</div>`;
  const note = `<div class="qa-note">${esc(L("qa.panel.note", "Read-only: the verdict of the last QA run."))}</div>`;
  const body = qa
    ? verdictBodyHtml(qa)
    : `<div class="qa-empty">${esc(L("qa.panel.empty", "No QA verdict for this mission yet."))}</div>`;
  return '<div class="qa-panel">' + title + note + body + "</div>";
}

async function render(container, missionId) {
  const res = await fetch(`/api/board/missions/${encodeURIComponent(missionId)}/qa`,
    { cache: "no-store" });
  if (!res.ok) throw new Error(L("qa.panel.loadfail", "Could not load the QA verdict."));
  const body = await res.json();
  container.innerHTML = panelHtml(body.verdict);
}

// === export (browser global + CommonJS module.exports) ===
const BoardQa = { render, panelHtml, esc };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardQa;
} else if (typeof document !== "undefined") {
  globalThis.BoardQa = BoardQa;
}
})();
