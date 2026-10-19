/* board-scope-wrapped */
(function () {
/* Board change history and rollback (part D).
 *
 * Loaded by board.html via <script src="/static/board_history_ui.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardHistoryUI.render(container, missionId) lists the entries of
 * GET /api/board/changelog, newest first; the route carries no mission
 * filter, so when a missionId is given the entries are filtered on the
 * client by their mission_id.  Each entry shows its timestamp, who and
 * action plus a Rollback button: clicking it shows the diff between the
 * entry's before_json and after_json, then posts
 * POST /api/board/changelog/{id}/rollback only after a confirmation
 * (nothing is sent without confirmation).  A 409 answer shows the
 * server's conflict text in the panel; other errors show their message.
 *
 * Every value taken from server data (and every diff value, which is a
 * JSON string) flows through historyEsc().
 *
 * Data shape:
 *   changelog entry: { id, at, who, action, mission_id, task_key,
 *                      before_json, after_json }
 *                     // GET /api/board/changelog
 */
"use strict";

// esc() from board_logic.js, with the same local fallback the other views use.
function historyEsc(v) {
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

// One same-origin POST helper for the panel (the rollback route is a board
// mutation route, so it carries the X-AHAWR-Dashboard header like the
// other board POSTs).
async function historyPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-AHAWR-Dashboard": "1",
    },
    body: JSON.stringify(body),
  });
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error((payload.error && payload.error.message) || payload.code
      || "HTTP " + res.status);
    err.status = res.status;
    err.body = payload;
    throw err;
  }
  return payload;
}

// One same-origin GET helper (the board API only).
async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body && body.error && body.error.message
      ? body.error.message : "HTTP " + res.status);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// The diff between the entry's before_json and after_json, as an HTML block.
// For object rows the changed fields are listed (field / before / after, each
// value a JSON string); for other shapes a one-line note is shown.  Every
// value passes historyEsc().
function diffHtml(entry) {
  const b = entry.before_json;
  const a = entry.after_json;
  if (b == null && a == null) {
    return `<div class="chdiff">no row value recorded for this entry</div>`;
  }
  const isObj = (v) => v != null && typeof v === "object" && !Array.isArray(v);
  if (isObj(b) && isObj(a)) {
    const keys = Object.keys(b).concat(
      Object.keys(a).filter((k) => !(k in b)));
    const rows = keys
      .map((k) => {
        const bv = k in b ? b[k] : null;
        const av = k in a ? a[k] : null;
        if (JSON.stringify(bv) === JSON.stringify(av)) return "";
        return `<tr><td>${historyEsc(k)}</td>` +
          `<td>${historyEsc(JSON.stringify(bv))}</td>` +
          `<td>${historyEsc(JSON.stringify(av))}</td></tr>`;
      })
      .join("");
    if (!rows) return `<div class="chdiff">no field changes recorded</div>`;
    return `<table class="chdiff">` +
      `<tr><th>field</th><th>before</th><th>after</th></tr>` + rows +
    `</table>`;
  }
  return `<pre class="chdiff">${historyEsc(JSON.stringify(b))} -> ` +
    `${historyEsc(JSON.stringify(a))}</pre>`;
}

// One changelog row: timestamp, who, action and the Rollback button.  The
// button is disabled when the entry carries no before value to restore.
function entryHtml(entry) {
  const id = String(entry.id == null ? "" : entry.id);
  const at = entry.at == null ? "" : entry.at;
  const who = entry.who == null ? "" : entry.who;
  const action = entry.action == null ? "" : entry.action;
  const canRoll = entry.before_json != null;
  return `<div class="chentry">` +
    `<span class="chtime">${historyEsc(at)}</span> ` +
    `<span class="chwho">${historyEsc(who)}</span> ` +
    `<span class="chaction">${historyEsc(action)}</span> ` +
    `<button type="button" class="ch-rollback" ` +
      `data-id="${historyEsc(id)}"${historyEsc(canRoll ? "" : " disabled")}>` +
    `Rollback</button>` +
    `<div class="chdiffbox"></div>` +
  `</div>`;
}

// The history panel: one row per entry plus a result line for server answers.
function renderHistoryHtml(entries) {
  const rows = (Array.isArray(entries) ? entries : []).map(entryHtml).join("");
  return (
    `<h3>Change history</h3>` +
    `<div class="chentries">` + rows + `</div>` +
    `<div class="chresult"></div>`
  );
}

// Resolve the container: an element or a string id (default "boardhistory");
// create it inside #board when missing.
function resolveInto(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardhistory");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardhistory";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
}

// The Rollback request: POST /api/board/changelog/{id}/rollback.
function rollbackRequest(entryId) {
  return historyPost(
    "/api/board/changelog/" + encodeURIComponent(entryId) + "/rollback", {});
}

// Attach the panel controls.  The buttons live in the template, which each
// render rebuilds, so the handlers are re-wired on every render.
function attachHistoryControls(el, missionId, entries) {
  const btns = el.querySelectorAll(".ch-rollback");
  for (const btn of btns) {
    btn.addEventListener("click", () => {
      const entry = (entries || []).find((e) => String(e.id) === btn.dataset.id);
      if (!entry) return;
      const row = btn.closest(".chentry");
      const box = row && row.querySelector(".chdiffbox");
      if (box) box.innerHTML = diffHtml(entry);
      const confirmed = window.confirm("Rollback this change?");
      if (!confirmed) return; // show the diff, send nothing without confirmation
      rollbackRequest(entry.id)
        .then(() => {
          const result = el.querySelector(".chresult");
          if (result) result.textContent = "rolled back";
        })
        .catch((e) => {
          const result = el.querySelector(".chresult");
          if (result) result.textContent = e.message || "rollback failed";
        });
    });
  }
}

// BoardHistoryUI.render(container, missionId): render the change history in
// the container; fetches GET /api/board/changelog and, when a missionId is
// given, filters the entries to that mission (the route has no mission
// filter of its own).
async function renderHistoryUI(container, missionId) {
  const el = resolveInto(container);
  let entries;
  try {
    const data = await fetchJson("/api/board/changelog");
    const all = data && Array.isArray(data.changelog) ? data.changelog : [];
    entries = missionId == null || missionId === ""
      ? all
      : all.filter((e) => e && e.mission_id === missionId);
  } catch (e) {
    el.innerHTML = `<div class="chresult">` +
      historyEsc(e.message || "changelog unavailable") + `</div>`;
    return;
  }
  el.innerHTML = renderHistoryHtml(entries);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
  attachHistoryControls(el, missionId, entries);
}

// === export (browser global + CommonJS module.exports) ===
const BoardHistoryUI = {
  render: renderHistoryUI,
  renderHtml: renderHistoryHtml,
  diffHtml,
  esc: historyEsc,
  rollbackRequest,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardHistoryUI;
} else if (typeof document !== "undefined") {
  globalThis.BoardHistoryUI = BoardHistoryUI;
}
})();
