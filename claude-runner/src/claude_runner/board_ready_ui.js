/* board-scope-wrapped */
(function () {
/* Board readiness panel and start (part D).
 *
 * Loaded by board.html via <script src="/static/board_ready_ui.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardReadyUI.render(container, missionId, mission) renders the mission's
 * readiness panel: the readiness checks as ok/warning/error rows with their
 * reason (the shape of the GET /api/board/missions/{id}/readiness response,
 * so it is rendered from plain objects, ready to be tested in node without a
 * DOM), a "preflight requested" checkbox, and the Start button.  Start calls
 * POST /api/board/missions/{id}/start (whose start_guard re-runs the full
 * readiness server-side): the body carries the `preflight` flag
 * (boolean, from the checkbox), and when any check is an error the start is
 * sent only after a confirmation.  The server's refusal text (the start
 * guard's `reason`, or the HTTP error message) is shown in the panel.
 *
 * Every value taken from server data flows through readyEsc(); the status
 * and reason columns are escaped too.
 *
 * Data shape:
 *   readiness: { mission_id, status, items: [{check, status, reason, ...}],
 *                errors: [...], confirmed_needed: bool }
 *               // GET /api/board/missions/{id}/readiness
 *   mission:   { mission_id, ... }  (used for the title and the start URL)
 */
"use strict";

// esc() from board_logic.js, with the same local fallback the other views use.
function readyEsc(v) {
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

// One same-origin POST helper for the panel (the start route is a board
// mutation route, so it carries the X-AHAWR-Dashboard header like the
// other board POSTs).
async function readyPost(url, body) {
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

// A readiness row: check name, status (ok/warning/error) and the reason.
// The row class carries the status so it can be coloured.
function readinessRow(item) {
  const check = item && item.check != null ? item.check : "";
  const status = item && item.status != null ? item.status : "";
  const reason = item && item.reason != null ? item.reason : "";
  return `<tr class="rdrow rd-${readyEsc(status)}">` +
    `<td class="rdcheck">${readyEsc(check)}</td>` +
    `<td class="rdstatus">${readyEsc(status)}</td>` +
    `<td>${readyEsc(reason)}</td></tr>`;
}

// The readiness panel: the check rows, the overall status line, the
// preflight checkbox and the Start button.  Pure render: takes the
// readiness response as a plain object.
function renderReadyHtml(readiness, mission) {
  const missionId = mission && mission.mission_id != null
    ? mission.mission_id
    : (readiness && readiness.mission_id != null ? readiness.mission_id : "");
  const items = (readiness && Array.isArray(readiness.items)) ? readiness.items : [];
  const status = readiness && readiness.status != null ? readiness.status : "warning";
  const rows = items.map(readinessRow).join("");
  return (
    `<h3>Readiness</h3>` +
    `<div class="rdstatusline rd-${readyEsc(status)}">` + readyEsc(status) + `</div>` +
    `<table class="rdtable">` +
      `<tr><th>check</th><th>status</th><th>reason</th></tr>` + rows + `</table>` +
    `<div class="rdoptions">` +
      `<label class="rdopt"><input type="checkbox" name="preflight" value="1">` +
      `preflight requested</label>` +
      `<button type="button" class="rd-start" data-mission="${readyEsc(missionId)}">Start</button>` +
    `</div>` +
    `<div class="rdresult"></div>`
  );
}

// Resolve the container: an element or a string id (default "boardready");
// create it inside #board when missing.
function resolveInto(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardready");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardready";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
}

// Render into the container and re-wire the controls on every render (the
// Start button lives in the template, so a re-render re-attaches it); the
// kept state (the preflight flag and the last server text) is passed through
// by the caller, so a Start click keeps them.
function render(container, missionId, mission, readiness, opts) {
  if (opts) {
    opts.mission = mission;
    opts.readiness = readiness;
  }
  const el = resolveInto(container);
  const s = opts || {};
  el.innerHTML = renderReadyHtml(readiness, mission);
  const cb = el.querySelector('input[name="preflight"]');
  if (cb) cb.checked = s.preflight === true;
  const result = el.querySelector(".rdresult");
  if (result) result.textContent = s.message == null ? "" : s.message;
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
  attachReadyControls(el, missionId, s);
}

// The Start request: POST /api/board/missions/{id}/start.  The body carries
// the `preflight` flag (the checkbox) as documented in T002; the server's
// start_guard reads `confirmed` as a query parameter (board_api.py:500), so
// a confirmed start appends ?confirmed=true to the URL.
function startRequest(missionId, body, confirmed) {
  const url = "/api/board/missions/" + encodeURIComponent(missionId)
    + "/start" + (confirmed ? "?confirmed=true" : "");
  return readyPost(url, body);
}

// Attach the panel controls.  The buttons live in the template, which each
// render rebuilds, so the handlers are re-wired on every render; each action
// reads the kept state and re-renders with it.
function attachReadyControls(el, missionId, s) {
  const startBtn = el.querySelector(".rd-start");
  if (startBtn) {
    startBtn.addEventListener("click", () => {
      const cb = el.querySelector('input[name="preflight"]');
      const preflight = !!(cb && cb.checked);
      const readiness = s.readiness;
      const hasError = readiness && Array.isArray(readiness.items)
        ? readiness.items.some((i) => i && i.status === "error")
        : !!(readiness && readiness.errors && readiness.errors.length);
      const confirmed = hasError && window.confirm(
        "Start is blocked by error checks. Start anyway?");
      if (hasError && !confirmed) return;
      startRequest(missionId, { preflight }, confirmed)
        .then((answer) => {
          const next = { preflight, message: answer.reason, readiness };
          render(el, missionId, s.mission || {}, readiness, next);
        })
        .catch((e) => {
          const next = { preflight, message: e.message || "start failed", readiness };
          render(el, missionId, s.mission || {}, readiness, next);
        });
    });
  }
}

// BoardReadyUI.render(container, missionId, mission): render the readiness
// panel in the container; the first render loads the mission's own
// /readiness and renders it (a re-render after Start keeps the same panel
// and the server's last answer).
async function renderReadyUI(container, missionId, mission) {
  const el = resolveInto(container);
  let readiness;
  try {
    readiness = await fetchJson(
      "/api/board/missions/" + encodeURIComponent(missionId) + "/readiness");
  } catch (e) {
    el.innerHTML = `<div class="rdresult">` + readyEsc(e.message || "readiness unavailable") + `</div>`;
    return;
  }
  render(el, missionId, mission, readiness, {});
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

// === export (browser global + CommonJS module.exports) ===
const BoardReadyUI = {
  render: renderReadyUI, renderHtml: renderReadyHtml, renderInto: render,
  esc: readyEsc, startRequest,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardReadyUI;
} else if (typeof document !== "undefined") {
  globalThis.BoardReadyUI = BoardReadyUI;
}
})();
