/* board-scope-wrapped */
(function () {
/* Board card actions (T019B): enable/disable, first/last, "повторить
 * не завершённую", queue pause after the current mission, the "Новая
 * миссия" button and the 10 s auto-refresh.
 *
 * Loaded by board.html via <script src="/static/board_actions.js"> (before
 * board.js, which only handles the hash routing).  The controls attach to
 * the cards that board_queue.js renders in #boardqueue; attachCardActions()
 * is idempotent (dataset.actionsReady) and the auto-refresh never re-renders
 * while a drag is active (BoardDnd.DndState.dragId) or while the creation
 * form is dirty (dirtyForm()).
 *
 * Confirmations: the API reads confirm=true as a query parameter for the
 * dangerous calls (disable / stop of a running mission), so those requests
 * append ?confirm=true to the URL.  Every dangerous card button first asks
 * in confirmActions() (window.confirm), and the call is only made after the
 * user confirms; a server refusal is shown in #boardstate and the board is
 * reloaded.
 *
 * Every value from data goes through esc() or textContent; data attributes
 * are read through dataset.  No DOM writes are made with unescaped HTML.
 */
"use strict";

// esc() from board_logic.js, with the same local fallback the other
// board_*.js scripts use.
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

// Working copy of the board data (the same shape board_queue.js loads:
// { groups, missions, queue, version }).  Filled by loadBoardData() the
// first time it is needed; kept fresh after every successful action.
let boardData = null;

// The auto-refresh timer.
let refreshTimer = null;

// One same-origin POST/GET helper (the board API only).
async function fetchJson(url, options) {
  const res = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
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

// The same data board_queue.js loads: /api/board/missions + /api/board/queue.
async function loadBoardData() {
  return Object.assign(
    {},
    await fetchJson("/api/board/missions"),
    await fetchJson("/api/board/queue"),
  );
}

// POST a JSON body to one endpoint; a 409 raises the "план изменился"
// message, like the dnd module's postMoveQueue().  Board mutation routes
// (POST/PUT/DELETE under /api/board) require the X-AHAWR-Dashboard header
// (dashboard.py's board_header_guard), so every POST carries it.
async function postJson(url, body) {
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
  if (res.status === 409) {
    const err = new Error((payload.error && payload.error.message) || "план изменился, обновите");
    err.status = 409;
    throw err;
  }
  if (!res.ok) {
    const err = new Error((payload.error && payload.error.message) || payload.code
      || "HTTP " + res.status);
    err.status = res.status;
    err.body = payload;
    throw err;
  }
  return payload;
}

// Show a shell message in #boardstate (text only; no innerHTML).
function showState(text, cls) {
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = text;
    state.className = cls || "bad";
  }
}

// Re-render the DOM from the server.  The board data is loaded once
// (loadBoardData()); the cards are assembled from it and #boardqueue is
// rendered with renderQueue() (board_queue.js), so the data is not
// fetched twice; then the dnd and action controls are re-attached.
async function refreshBoard() {
  let data;
  try {
    data = await loadBoardData();
    data.cards = {};
    for (const m of data.missions || []) {
      const path = "/api/board/missions/" + encodeURIComponent(m.mission_id);
      const detail = await fetchJson(path);
      data.cards[m.mission_id] = {
        live_state: detail.live_state,
        task_views: detail.task_views,
        stats: await fetchJson(path + "/stats"),
      };
    }
  } catch (e) {
    showState(e.message || "The board could not be reloaded.");
    return;
  }
  boardData = data;
  const box = document.getElementById("boardqueue");
  if (box && typeof renderQueue === "function") {
    box.innerHTML = renderQueue(data);
  }
  if (typeof BoardDnd !== "undefined" && typeof BoardDnd.attachDnd === "function") {
    await BoardDnd.attachDnd();
  }
  attachCardActionsTo(data);
}

// Rebuild a card's action buttons from its dataset; a card that has
// dataset.actionsReady === "1" is skipped.
function attachCardActions(el, data) {
  if (el.dataset.actionsReady === "1") return;
  const missionId = el.dataset.missionId;
  const groupKey = el.dataset.group || "";
  el.dataset.actionsReady = "1";

  const group = (document.createElement("span"));
  group.className = "qactions";

  const enabled = data && data.queue
    ? (data.queue.find((e) => e.mission_id === missionId) || {}).enabled
    : null;
  if (enabled === false) {
    const btn = mkButton(group, "включить", "enable", missionId, false);
  } else {
    const btn = mkButton(group, "выключить", "disable", missionId, true);
  }

  if (groupKey === "queued") {
    mkButton(group, "в начало", "first", missionId, false);
    mkButton(group, "в конец", "last", missionId, false);
  } else if (groupKey === "running") {
    mkButton(group, "пауза", "pause", missionId, true);
    mkButton(group, "остановить", "stop", missionId, true);
  } else if (groupKey === "not_completed" || groupKey === "queued") {
    mkButton(group, "повторить", "retry", missionId, true);
  }

  el.appendChild(group);
}

// Make one small button inside the action group; the click handler stops
// the click from navigating (the buttons live inside the card's <a>).
function mkButton(group, label, action, missionId, needsConfirm) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "qact qact-" + esc(action);
  btn.textContent = label;
  btn.title = label;
  btn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (needsConfirm) confirmActions(missionId, action);
    else doAction(action, missionId);
  });
  group.appendChild(btn);
  return btn;
}

// The confirm dialog for a dangerous action: the call is made only when
// window.confirm returns true (the server still checks and refuses without
// confirm=true when the mission is running).
function confirmActions(missionId, action) {
  const label = {
    disable: "Выключить миссию",
    stop: "Остановить миссию",
    retry: "Повторить незавершённую миссию",
    pause: "Пауза очереди после текущей миссии",
  }[action] || action;
  if (!window.confirm(label + " (" + esc(missionId) + ")?")) return;
  doAction(action, missionId);
}

// One action: build the request, call the API, then refresh the board from
// the server.  The server reads confirm=true as a query parameter (the
// stop/disable routes' confirm: bool = Query(False)), so ?confirm=true is
// appended to those URLs; retry-not-completed and set-enabled have no
// confirm parameter.  Pause is the board flag: POST /api/board/flags/queue_paused
// (the flags route is the only board route that stores a pause marker;
// there is no dedicated pause endpoint).
async function doAction(action, missionId) {
  const version = boardData ? boardData.version : null;
  let body = { mission_id: missionId };
  let url;
  if (action === "disable") {
    url = "/api/board/missions/" + encodeURIComponent(missionId) + "/disable?confirm=true";
  } else if (action === "enable") {
    url = "/api/board/queue/set-enabled";
    Object.assign(body, { enabled: true, expected_version: version });
  } else if (action === "first") {
    url = "/api/board/queue/first";
    body.expected_version = version;
  } else if (action === "last") {
    url = "/api/board/queue/last";
    body.expected_version = version;
  } else if (action === "retry") {
    url = "/api/board/queue/retry-not-completed";
  } else if (action === "pause") {
    url = "/api/board/flags/queue_paused";
    body = { value: "1" };
  } else if (action === "stop") {
    url = "/api/board/missions/" + encodeURIComponent(missionId) + "/stop?confirm=true";
  } else {
    return;
  }
  try {
    await postJson(url, body);
  } catch (e) {
    if (e.status === 409) {
      showState("план изменился, обновите");
    } else {
      showState(e.message || (action + " failed"));
    }
    // The server state may have changed (e.g. a refusal); reload it.
    await refreshBoard();
    return;
  }
  await refreshBoard();
}

// --- the "Новая миссия" button and the minimal creation form ---

// The button lives once, at the top of the queue view.
// eslint-disable-next-line no-let, prefer-const
let newMissionButton = null;

// True while the form is open and any of its fields differ from the
// initial values; the auto-refresh must not re-render in that state.
function dirtyForm() {
  if (!newMissionButton) return false;
  const form = newMissionButton.dataset.formOpen === "1";
  if (!form) return false;
  const el = document.getElementById("missionform");
  if (!el) return false;
  for (const field of el.querySelectorAll("input, textarea")) {
    if (field.value !== field.dataset.initial) return true;
  }
  return false;
}

// Open or close the creation form; the form is built lazily with a
// dataset.initial per field so dirtyForm() can detect edits.
function toggleNewMission() {
  const open = newMissionButton.dataset.formOpen === "1";
  if (open) {
    newMissionButton.dataset.formOpen = "0";
    const el = document.getElementById("missionform");
    if (el) el.remove();
    return;
  }
  newMissionButton.dataset.formOpen = "1";
  const el = document.createElement("div");
  el.id = "missionform";
  // the form sits in #board before #boardqueue: BoardQueue() rewrites the innerHTML of #boardqueue (the progressive
  // draw of the cards, the auto-refresh), which would remove a form that lived inside it
  const box = document.getElementById("boardqueue");
  const root = document.getElementById("board");
  if (box && root && box.parentNode === root) root.insertBefore(el, box);
  else if (root) root.prepend(el);

  const mk = (name, label, tag) => {
    const input = document.createElement(tag || "input");
    input.name = name;
    if (tag !== "textarea") input.type = "text";
    input.value = "";
    input.dataset.initial = "";
    input.required = true;
    const labelEl = document.createElement("label");
    labelEl.textContent = label + ": ";
    labelEl.appendChild(input);
    el.appendChild(labelEl);
    el.appendChild(document.createElement("br"));
    return input;
  };
  const idIn = mk("mission_id", "mission_id");
  const nameIn = mk("name", "название");
  const objIn = mk("objective", "цель", "textarea");
  // template: "empty", a stored template or another mission (the server copies its settings); filled from the API
  const tplLabel = document.createElement("label");
  tplLabel.textContent = "шаблон: ";
  const tplSel = document.createElement("select");
  tplSel.name = "template";
  const optEmpty = document.createElement("option");
  optEmpty.value = "";
  optEmpty.textContent = "— пустая миссия —";
  tplSel.appendChild(optEmpty);
  tplLabel.appendChild(tplSel);
  el.appendChild(tplLabel);
  el.appendChild(document.createElement("br"));
  (async () => {
    try {
      const templates = (await fetchJson("/api/board/templates")).templates || [];
      for (const t of templates) {
        const o = document.createElement("option");
        o.value = "template:" + t.name;
        o.textContent = "шаблон: " + t.name;
        tplSel.appendChild(o);
      }
    } catch (e) { /* no stored templates: the missions below still work */ }
    try {
      const missions = (await fetchJson("/api/board/missions")).missions || [];
      for (const m of missions) {
        const o = document.createElement("option");
        o.value = "mission:" + m.mission_id;
        o.textContent = "как у: " + m.mission_id;
        tplSel.appendChild(o);
      }
    } catch (e) { /* the list stays short */ }
  })();
  const posIn = mk("position", "позиция в очереди");
  posIn.type = "number";
  posIn.min = "0";

  const submit = document.createElement("button");
  submit.type = "button";
  submit.textContent = "создать";
  submit.addEventListener("click", createMissionForm);
  el.appendChild(submit);
}

// Read the form values and POST /api/board/missions/create; every value
// reaches the API through the JSON body (no HTML involved).
async function createMissionForm() {
  const el = document.getElementById("missionform");
  if (!el) return;
  const body = {
    mission_id: el.querySelector('[name="mission_id"]').value.trim(),
    name: el.querySelector('[name="name"]').value.trim(),
    objective: el.querySelector('[name="objective"]').value.trim(),
  };
  const tpl = el.querySelector('[name="template"]') ? el.querySelector('[name="template"]').value : "";
  if (tpl.startsWith("mission:")) body.source_mission_id = tpl.slice("mission:".length);
  if (tpl.startsWith("template:")) body.template_name = tpl.slice("template:".length);
  const pos = el.querySelector('[name="position"]').value.trim();
  if (pos !== "") body.position = Number(pos);
  body.who = "dashboard";
  try {
    await postJson("/api/board/missions/create", body);
  } catch (e) {
    showState(e.message || "mission create failed");
    return;
  }
  toggleNewMission();
  await refreshBoard();
}

// --- auto-refresh: every 10 s, skipped while dragging or editing ---

function startAutoRefresh() {
  if (refreshTimer) return;
  refreshTimer = setInterval(tick, 10000);
}

// Stop the auto-refresh (used by the node test so the event loop exits).
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function tick() {
  // Skip while a drag is active or the creation form is dirty: a
  // re-render in that state would break the drop or lose the edits.
  if (typeof BoardDnd !== "undefined" && BoardDnd.DndState && BoardDnd.DndState.dragId) return;
  if (dirtyForm()) return;
  if (document.hidden) return;
  refreshBoard();
}

// Hook up: start the auto-refresh first (nothing above it can throw), then
// build the button (it is prepended before #boardqueue, which board_queue.js
// refills) and attach the card actions.
function hookUp() {
  startAutoRefresh();
  const root = document.getElementById("board");
  if (!root) return;
  if (!newMissionButton) {
    newMissionButton = document.createElement("button");
    newMissionButton.type = "button";
    newMissionButton.className = "qnew";
    newMissionButton.textContent = "Новая миссия";
    newMissionButton.title = "Новая миссия";
    newMissionButton.dataset.formOpen = "0";
    newMissionButton.addEventListener("click", toggleNewMission);
  }
  root.prepend(newMissionButton);
  attachCardActionsTo(boardData);
}

// === export (browser global + CommonJS module.exports) ===
const BoardActions = {
  attachCardActions,
  attachCardActionsTo,
  doAction,
  confirmActions,
  refreshBoard,
  dirtyForm,
  toggleNewMission,
  createMissionForm,
  tick,
  startAutoRefresh,
  stopAutoRefresh,
  hookUp,
};
function attachCardActionsTo(data) {
  boardData = data;
  const box = document.getElementById("boardqueue");
  if (!box) return;
  for (const el of box.querySelectorAll("[data-mission-id]")) {
    attachCardActions(el, data);
  }
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardActions;
} else if (typeof document !== "undefined") {
  globalThis.BoardActions = BoardActions;
  hookUp();
}
})();
