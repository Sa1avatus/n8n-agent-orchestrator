/* board-scope-wrapped */
(function () {
/* Board shell bootstrap (T018): hash routing only.
 *
 * Fragments (see board_logic.js): #board, #board/<mission_id>,
 * #board/<mission_id>/<task_id>. The routing calls the render functions
 * that the other board_*.js files define; those files are loaded by
 * board.html and are tolerated when they do not exist yet (board_logic.js
 * is only needed when a deeper link is in place). Missing functions are
 * reported in #boardstate instead of breaking the page.
 */
"use strict";

// #boardstate is the only element this bootstrap writes into: #boardbar
// (and whatever the views add to #board) must survive the shell messages.
const stateEl = document.getElementById("boardstate");

// Local fallback for board_logic.js's esc(), used when that script failed
// to load so its missing deep-link message can still be shown safely.
function localEsc(v) {
  return String(v == null ? "" : v)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
function escValue(v) { return typeof esc === "function" ? esc(v) : localEsc(v); }

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.error || "HTTP " + res.status);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

function state(html, cls) {
  stateEl.className = cls || "";
  stateEl.textContent = String(html);  // textContent: nothing to escape
}

function applyStaticTexts() {
  document.title = "AHAWR " + t("app.title");
  for (const el of document.querySelectorAll("[data-i18n]")) {
    el.textContent = t(el.getAttribute("data-i18n"));
  }
}

// A deep link (#board/m1/T003) needs the hash parser from board_logic.js;
// without that script (it loads last) report the missing piece instead of
// crashing.
function needLogic() {
  if (typeof BoardLogic !== "undefined") return false;
  state(t("board.script_missing"), "bad");
  return true;
}

// A mission or task screen replaces the queue list and the view body; the whole page scrolls then
// (every panel would otherwise take its own slice of the height).
const PANELS = ["boardmission", "boardtasks", "boardplanedit", "boardthink", "boardready", "boardhistory", "boardclaudem", "boardtask"];
function showScreen(hash) {
  const deep = /^#board[/][^/]+/.test(hash || "");
  const set = (id, hidden) => { const el = document.getElementById(id); if (el) el.hidden = hidden; };
  set("boardqueue", deep);
  set("boardviewbody", deep);
  for (const id of PANELS) set(id, !deep);
  const root = document.getElementById("board");
  if (root) root.classList.toggle("bd-mission", deep);
  if (typeof BoardTabs !== "undefined") BoardTabs.clear();
}

// Resolve the current fragment and call the matching render function;
// missing functions (the other board_*.js files are not present yet) are
// reported instead of throwing.
async function route() {
  if (needLogic()) return;
  if (/^#board[/]view[/]/.test(location.hash)) return;   // the views draw themselves
  showScreen(location.hash);
  let parsed;
  try { parsed = BoardLogic.parseHash(location.hash || "#board"); }
  catch (e) { state(t("board.bad_link", { error: e.message }), "bad"); return; }
  if (parsed.task_id) {
    if (typeof BoardTask === "function") {
      try { await BoardTask(parsed.mission_id, parsed.task_id); return; }
      catch (e) { state(t("board.task_failed", { error: e.message }), "bad"); return; }
    }
    state(t("board.not_loaded"), "bad");
  } else if (parsed.mission_id) {
    if (typeof BoardMission === "function") {
      try {
        await BoardMission(parsed.mission_id);
        if (typeof BoardClaudeMd === "function") {
          try { await BoardClaudeMd(parsed.mission_id); }
          catch (e) { state(t("board.claudemd_failed", { error: e.message || e }), "bad"); }
        }
        if (typeof BoardTabs !== "undefined") BoardTabs.apply();
        return;
      }
      catch (e) { state(t("board.mission_failed", { error: e.message }), "bad"); return; }
    }
    state(t("board.not_loaded"), "bad");
  } else {
    if (typeof BoardQueue === "function") {
      try { await BoardQueue(); return; }
      catch (e) {
        // A 503 means the board store is not available (e.g. no DSN).
        const offline = e.status === 503 || /503/.test(String(e.message || ""));
        const reason = offline && e.body && e.body.reason ? String(e.body.reason) : "";
        state(reason ? t("board.offline_reason", { reason }) : t("board.offline"), "bad");
      }
      return;
    }
    state(t("board.not_loaded"), "bad");
  }
}

// the other board_*.js files (wrapped in their own scope) call the shell's fetch helper through the global
globalThis.getJSON = getJSON;

// Initial load and fragment changes.
window.addEventListener("hashchange", route);
applyStaticTexts();
BoardI18n.onChange(applyStaticTexts);
window.addEventListener("load", () => BoardI18n.load().then(route));
})();
