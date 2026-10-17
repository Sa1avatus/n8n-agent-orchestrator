/* board-scope-wrapped */
(function () {
/* Board drag-and-drop and reordering controls (T019A).
 *
 * Loaded by board.html via <script src="/static/board_dnd.js">.  It turns the
 * queue cards that board_queue.js renders in #boardqueue into movable items:
 *
 *   - HTML5 drag: a queued card is dragged onto another card; the drop side
 *     ("before"/"after") is decided by the cursor's Y within the target.  The
 *     dragged id lives in DndState.dragId (set in dragstart); dragover never
 *     calls dataTransfer.getData() (that stalls the drag) — it only
 *     preventDefaults when a different card is being dragged and marks the
 *     side.  getData is read only in drop, with DndState.dragId as fallback.
 *   - Mobile buttons: "выше" (up) / "ниже" (down) on each queued card.
 *   - Keyboard: Enter opens the focused card, Esc goes back (document level,
 *     regardless of the event target), Alt+arrows move the focused card
 *     (left/right: previous/next slot; up/down: up/down).
 *
 * Every reorder goes through BoardLogic.dropPosition()/BoardLogic.reorder()
 * (board_logic.js), then one API function, postMoveQueue(): POST
 * /api/board/queue/move with the current queue version.  A 409 is shown as
 * "план изменился, обновите" and the board is reloaded from the server.
 *
 * Dragging between groups changes state only where safe: the running card
 * is not draggable at all, and a drop before the running card is refused by
 * BoardLogic, so the running mission stays first.  Other group changes are
 * left to the server's refresh; only the queue state changes here.
 *
 * Every value from data goes through esc() or textContent; data attributes
 * are read through el.dataset.  attachCard() is idempotent (dataset.dndReady),
 * and the MutationObserver skips ticks while attachDnd() is mutating the DOM
 * so it cannot loop on its own mutations.
 */
"use strict";

// esc() from board_logic.js, with the same local fallback board_queue.js uses.
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
// { groups, missions, queue, version, cards }) and the id of the card being
// dragged (null when not dragging).
const DndState = { data: null, dragId: null };

// Set while attachDnd() is mutating the DOM: the observer callback skips the
// tick, so the observer cannot loop on its own mutations.
let attaching = false;

// One same-origin GET helper (the board API only).
async function fetchJson(url) {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body && body.error ? body.error : "HTTP " + res.status);
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

// The one queue API call.  Sends the move with the current version; a 409
// raises an error carrying the "план изменился, обновите" message.
async function postMoveQueue(mission_id, to_position) {
  const version = DndState.data ? DndState.data.version : null;
  const res = await fetch("/api/board/queue/move", {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mission_id, to_position, expected_version: version }),
  });
  let body = null;
  try { body = await res.json(); } catch (e) { /* keep the status */ }
  if (res.status === 409) {
    const err = new Error(body && body.error && body.error.message
      ? body.error.message : "план изменился, обновите");
    err.status = 409;
    throw err;
  }
  if (!res.ok) {
    const err = new Error((body && (body.error && body.error.message ? body.error.message
      : body.code)) || "HTTP " + res.status);
    err.status = res.status;
    throw err;
  }
  return body;
}

// The pure part: given the working queue data, the mission being moved and
// the target mission, return the new queue array.  Uses BoardLogic.
// dropPosition + BoardLogic.reorder, so the running card is rejected and the
// running-first order is preserved by construction.
function moveInData(queue, mission_id, target_id, side, groups) {
  if (typeof BoardLogic === "undefined") {
    throw new Error("Board logic not loaded — cannot reorder");
  }
  const at = BoardLogic.dropPosition(queue, mission_id, target_id, side, groups);
  return BoardLogic.reorder(queue, mission_id, at);
}

// Attach the controls to one card: up/down buttons (mobile) and the drag
// hooks.  Idempotent: a card with dataset.dndReady === "1" is skipped.
function attachCard(el, data) {
  if (el.dataset.dndReady === "1") return;
  const missionId = el.dataset.missionId;
  const groupKey = el.dataset.group || "";

  // A running card (and any other non-queued card) is not draggable and has
  // no move buttons.
  if (groupKey !== "queued") {
    return;
  }
  el.dataset.dndReady = "1";

  const buttons = document.createElement("span");
  buttons.className = "qmove";
  const up = document.createElement("button");
  up.type = "button";
  up.className = "qmove-up";
  up.textContent = "выше";
  up.title = "выше (выше по очереди)";
  const down = document.createElement("button");
  down.type = "button";
  down.className = "qmove-down";
  down.textContent = "ниже";
  down.title = "ниже (ниже по очереди)";
  buttons.appendChild(up);
  buttons.appendChild(down);
  el.appendChild(buttons);

  el.setAttribute("draggable", "true");

  el.addEventListener("dragstart", (e) => {
    DndState.dragId = missionId;
    e.dataTransfer.setData("text/plain", missionId);
    el.classList.add("qdragging");
  });
  el.addEventListener("dragend", () => {
    DndState.dragId = null;
    el.classList.remove("qdragging");
    clearDropMarks();
  });
  // dragover: no getData (it stalls the drag).  preventDefault whenever the
  // dragged card differs from this one, and mark the drop side.
  el.addEventListener("dragover", (e) => {
    if (!DndState.dragId || DndState.dragId === missionId) return;
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    const side = e.clientY < rect.top + rect.height / 2 ? "before" : "after";
    markDrop(el, side);
  });
  el.addEventListener("dragleave", () => clearDropMarks());
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    const dragId = e.dataTransfer.getData("text/plain") || DndState.dragId;
    DndState.dragId = null;
    clearDropMarks();
    if (!dragId || dragId === missionId) return;
    const rect = el.getBoundingClientRect();
    const side = e.clientY < rect.top + rect.height / 2 ? "before" : "after";
    applyMove(dragId, missionId, side);
  });

  // The buttons live inside the card's <a>: stop the click from navigating.
  up.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    moveByStep(missionId, -1);
  });
  down.addEventListener("click", (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    moveByStep(missionId, +1);
  });
}

// Mark the card that is currently receiving a drag (and the side).
function markDrop(el, side) {
  clearDropMarks();
  el.classList.add(side === "before" ? "qdrop-before" : "qdrop-after");
}

// Clear any drop marks left on the cards.
function clearDropMarks() {
  const marked = document.querySelectorAll(".qdrop-before, .qdrop-after");
  for (const el of marked) {
    el.classList.remove("qdrop-before", "qdrop-after");
  }
}

// Move a card by one slot within the queue.  "выше" = -1 (toward the front),
// "ниже" = +1 (toward the back).  The target is the adjacent card in the
// queue order.
function moveByStep(missionId, dir) {
  if (!DndState.data) return;
  const queue = DndState.data.queue || [];
  const idx = queue.findIndex((e) => e.mission_id === missionId);
  if (idx === -1) return;
  if (idx + dir < 0 || idx + dir >= queue.length) return;
  const targetId = dir === -1 ? queue[idx - 1].mission_id : queue[idx + 1].mission_id;
  const side = dir === -1 ? "before" : "after";
  applyMove(missionId, targetId, side);
}

// Apply one reorder: compute the new queue with BoardLogic, call the queue
// API, then refresh the board from the server.  A 409 shows
// "план изменился, обновите" and the board is reloaded too.
async function applyMove(missionId, targetId, side) {
  if (!DndState.data) return;
  const queue = DndState.data.queue || [];
  let newQueue;
  try {
    newQueue = moveInData(queue, missionId, targetId, side, DndState.data.groups);
  } catch (e) {
    showState(e.message || "Move refused.");
    return;
  }
  // Skip the no-op (drop onto itself): reorder() returns a fresh array, so
  // compare the mission-id sequence, not the array reference.
  const sameOrder = queue.length === newQueue.length
    && queue.every((e, i) => e.mission_id === newQueue[i].mission_id);
  if (sameOrder) return;
  const pos = newQueue.findIndex((e) => e.mission_id === missionId);
  try {
    await postMoveQueue(missionId, pos);
  } catch (e) {
    if (e.status === 409) {
      showState("план изменился, обновите");
    } else {
      showState(e.message || "Move failed.");
    }
  }
  await refreshBoard();
}

// Re-render the DOM from the server: BoardQueue() (board_queue.js) refetches
// and writes #boardqueue; then adopt the fresh data and re-attach.
async function refreshBoard() {
  try {
    if (typeof BoardQueue === "function") {
      await BoardQueue();
    }
    DndState.data = await loadBoardData();
  } catch (e) {
    showState(e.message || "The board could not be reloaded.");
    return;
  }
  attachDnd();
}

// Show a shell message in #boardstate (text only; no innerHTML).
function showState(text) {
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = text;
    state.className = "bad";
  }
}

// Attach the controls to every queued card in #boardqueue.  Fills
// DndState.data from the same endpoints board_queue.js loads when it is
// still null.  Re-entrant-safe: the observer skips ticks while this is
// mutating the DOM, so it cannot loop on its own mutations.
async function attachDnd() {
  if (attaching) return;
  attaching = true;
  try {
    const box = document.getElementById("boardqueue");
    if (!box) return;
    if (!DndState.data) {
      try {
        DndState.data = await loadBoardData();
      } catch (e) {
        showState(e.message || "The board could not be loaded.");
        return;
      }
    }
    const cards = box.querySelectorAll('[data-mission-id][data-group="queued"]');
    for (const el of cards) {
      attachCard(el, DndState.data);
    }
  } finally {
    attaching = false;
  }
}

// Keyboard: Esc goes back at document level (any event target); Enter opens
// the focused card; Alt+arrows move the focused queued card.
function onKeydown(e) {
  if (e.key === "Escape") {
    if (location.hash.startsWith("#board/")) {
      e.preventDefault();
      location.hash = "#board";
    }
    return;
  }
  const el = e.target;
  if (!el || !el.dataset || el.dataset.missionId === undefined) return;
  if (e.key === "Enter" && el.classList.contains("qcard")) {
    e.preventDefault();
    location.hash = "#board/" + el.dataset.missionId;
    return;
  }
  if (!e.altKey || !el.classList.contains("qcard")) return;
  const missionId = el.dataset.missionId;
  if (el.dataset.group !== "queued") return;
  let dir = null;
  if (e.key === "ArrowUp" || e.key === "ArrowLeft") dir = -1;
  else if (e.key === "ArrowDown" || e.key === "ArrowRight") dir = +1;
  if (dir === null) return;
  e.preventDefault();
  moveByStep(missionId, dir);
}

// Hook up: keyboard is handled document-level; a MutationObserver on #board
// sees the moment #boardqueue appears (it does not exist when this script
// loads) and re-attaches after each render.
function hookUp() {
  window.addEventListener("keydown", onKeydown);
  const root = document.getElementById("board");
  if (root) {
    new MutationObserver(() => {
      if (attaching) return;
      attachDnd();
    }).observe(root, { childList: true, subtree: true });
  }
  attachDnd(); // first pass is a no-op until #boardqueue exists
}

// === export (browser global + CommonJS module.exports) ===
// BoardDnd exposes the pure helpers and the state for the node test of T025;
// the browser globals and the hook are set only when a DOM is present.
const BoardDnd = {
  attachCard,
  attachDnd,
  applyMove,
  moveByStep,
  moveInData,
  postMoveQueue,
  refreshBoard,
  DndState,
  esc,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardDnd;
} else if (typeof document !== "undefined") {
  globalThis.BoardDnd = BoardDnd;
  hookUp();
}
})();
