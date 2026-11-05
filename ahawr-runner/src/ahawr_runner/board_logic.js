/* board-scope-wrapped */
(function () {
/* Pure, DOM-free board logic (T017, part 1).
 *
 * Loaded by board.html via <script src="/static/board.js"> and also usable
 * from node via CommonJS require (UMD-style export below).  Every value
 * taken from server data flows through esc() or textContent in the caller;
 * nothing here reads or writes the DOM, so it is safe to run under node.
 *
 * Data shapes (plain objects, matching the /api/board/... responses):
 *   mission: { mission_id, name, status, ... }
 *   queue:   [ { position, mission_id, ... } ]
 *   groups:  { running: [id, ...], queued: [id, ...],
 *              completed: [id, ...], not_completed: [id, ...],
 *              disabled: [id, ...] }
 *   form:    { queue: [...], version: "..." }   // a dirty local form state
 *   server:  { queue: [...], version: "..." }   // the server refresh payload
 */
"use strict";

// === pure helper: esc ===
// Escape a value for HTML text interpolation.
function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// === pure function 1: reorder ===
// Reorder a queue array.  Returns a new array with contiguous positions
// (0..n-1).  Raises when mission_id is not present or is duplicated.
function reorder(queue, mission_id, to_position) {
  if (!Array.isArray(queue)) throw new Error("queue must be an array");
  const idx = queue.findIndex((e) => e.mission_id === mission_id);
  if (idx === -1) {
    throw new Error(`mission ${String(mission_id)} is not in the queue`);
  }
  const out = queue.map((e) => Object.assign({}, e));
  const [moved] = out.splice(idx, 1);
  const n = out.length;
  const at = Math.max(0, Math.min(Number(to_position) || 0, n));
  out.splice(at, 0, moved);
  out.forEach((e, i) => { e.position = i; });
  return out;
}

// === pure function 2: drop position ===
// Given a queue, the mission being dropped, the target mission, and the
// side of the target ("before" | "after"), return the index at which the
// dropped mission lands in the array with the dropped item already removed
// (i.e. an index valid for reorder(queue, mission_id, at)).  Returns idx
// (a no-op) when mission_id === target_id.  Raises when moving the running
// mission.  Between groups: the running mission must stay first, so
// inserting before a running target is refused; a drop onto a group header
// (target_id null, groups optional) lands at the front of the list.
function dropPosition(queue, mission_id, target_id, side, groups) {
  if (!Array.isArray(queue)) throw new Error("queue must be an array");
  if (side !== "before" && side !== "after") {
    throw new Error(`side must be "before" or "after", got ${String(side)}`);
  }
  const running = new Set((groups && groups.running) || []);
  if (running.has(mission_id)) {
    throw new Error(`mission ${String(mission_id)} is running and cannot be moved`);
  }
  const idx = queue.findIndex((e) => e.mission_id === mission_id);
  if (idx === -1) {
    throw new Error(`mission ${String(mission_id)} is not in the queue`);
  }
  // Drop onto a group header: no target mission, land at the front.
  if (target_id === null || target_id === undefined) return 0;
  const targetIdx = queue.findIndex((e) => e.mission_id === target_id);
  if (targetIdx === -1) {
    throw new Error(`target ${String(target_id)} is not in the queue`);
  }
  // The running mission must stay first: refuse to insert before it.
  if (running.has(target_id) && side === "before") {
    throw new Error(`cannot insert before running mission ${String(target_id)}`);
  }
  if (mission_id === target_id) return idx; // no-op
  // Index in the array with the dropped item removed.
  let at = side === "before" ? targetIdx : targetIdx + 1;
  if (idx < at) at -= 1;
  return at;
}

// Compare two queue arrays by content (mission ids, ignoring position).
function _queuesDiffer(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b)) return true;
  const ak = a.map((e) => e.mission_id).join("|");
  const bk = b.map((e) => e.mission_id).join("|");
  return ak !== bk;
}

// === pure function 3: merge server refresh into a dirty form ===
// Keep every locally-edited field; overwrite non-local fields with the server
// values; when the server queue differs from the local one, flag the form with
// `server_changed: true` so the UI can show "на сервере есть изменения".
function mergeServerIntoForm(form, server) {
  if (!form || typeof form !== "object") throw new Error("form must be an object");
  if (!server || typeof server !== "object") throw new Error("server must be an object");

  const result = Object.assign({}, form);
  const dirty = new Set(form.dirty_fields || []);
  const changed = _queuesDiffer(form.queue, server.queue);

  // Copy over non-local server fields.
  const keys = new Set([...Object.keys(form), ...Object.keys(server)]);
  for (const key of keys) {
    if (key === "dirty_fields") continue;
    if (dirty.has(key)) continue; // keep local edits
    if (key in server) result[key] = server[key];
  }

  // Flag if the queue content drifted.
  if (changed) result.server_changed = true;
  // Drop the flag when the queues are back in sync.
  if (!changed && "server_changed" in result) delete result.server_changed;

  return result;
}

// === pure function 4: parse / build hash ===
// Fragments:
//   #board
//   #board/<mission_id>
//   #board/<mission_id>/<task_id>   e.g. #board/m1/T003
// parseHash returns { mission_id, task_id } (either may be null).
function parseHash(hash) {
  if (hash === null || hash === undefined) return { mission_id: null, task_id: null };
  const h = String(hash).replace(/^#/, "");
  if (h === "") return { mission_id: null, task_id: null };
  if (h === "board") return { mission_id: null, task_id: null };
  const parts = h.split("/");
  if (parts[0] !== "board") {
    throw new Error(`unrecognized hash ${String(hash)}`);
  }
  const mission_id = parts[1] || null;
  const task_id = parts[2] || null;
  if (mission_id && task_id && !/^[A-Za-z0-9._-]+$/.test(task_id)) {
    throw new Error(`bad task id ${String(task_id)}`);
  }
  return { mission_id, task_id };
}

// Build the hash for a board view; mission_id / task_id may be null.
function buildHash(mission_id, task_id) {
  if (mission_id === null || mission_id === undefined) return "#board";
  const m = String(mission_id);
  if (!/^[A-Za-z0-9._-]+$/.test(m)) {
    throw new Error(`bad mission id ${m}`);
  }
  if (task_id === null || task_id === undefined) return `#board/${m}`;
  const t = String(task_id);
  if (!/^[A-Za-z0-9._-]+$/.test(t)) {
    throw new Error(`bad task id ${t}`);
  }
  return `#board/${m}/${t}`;
}

// === pure function 5: config diff ===
// Compare two config objects field by field (top level).  Returns the
// changed fields in sorted key order as { field, old, new }; a key present
// on only one side is changed (missing side = null).  Values compare with
// deep equality via JSON.stringify (shortcut: nested object key order is
// significant; configs are flat, so no deep walk is needed).
function diffConfig(a, b) {
  if (!a || typeof a !== "object" || Array.isArray(a)) {
    throw new Error("config a must be a plain object");
  }
  if (!b || typeof b !== "object" || Array.isArray(b)) {
    throw new Error("config b must be a plain object");
  }
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort();
  const changed = [];
  for (const key of keys) {
    const av = key in a ? a[key] : null;
    const bv = key in b ? b[key] : null;
    if (JSON.stringify(av) === JSON.stringify(bv)) continue;
    changed.push({ field: key, old: av, new: bv });
  }
  return changed;
}

// === pure function 6: think-policy layout preview ===
// The variant name of each of the first n tasks (1-based), mirroring the
// server's board_think.layout_preview: the weights are laid out in order
// (each variant weight times) and task t uses position (t - 1) % total
// weight; the policy applies from from_task on (default 0), so tasks
// before from_task are null.  A broken policy (not an object, empty or
// unusable variants, non-whole from_task) yields all nulls, as the server
// yields None for every task.
function thinkLayout(policy, n = 12) {
  const count = Math.max(0, Math.floor(Number(n) || 0));
  const out = new Array(count).fill(null);
  if (!policy || typeof policy !== "object") return out;
  const variants = policy.variants;
  if (!Array.isArray(variants) || variants.length === 0) return out;
  const from_task = policy.from_task === undefined ? 0 : policy.from_task;
  if (typeof from_task !== "number" || !Number.isInteger(from_task) || from_task < 0) {
    return out;
  }
  const layout = [];
  for (const v of variants) {
    if (!v || typeof v !== "object") continue;
    if (typeof v.name !== "string" || v.name === "") continue;
    if (typeof v.weight !== "number" || !Number.isInteger(v.weight)
        || v.weight < 1 || v.weight > 20) continue;
    for (let i = 0; i < v.weight; i++) layout.push(v.name);
  }
  if (layout.length === 0) return out;
  for (let t = 1; t <= count; t++) {
    if (t < from_task) continue;
    out[t - 1] = layout[(t - 1) % layout.length];
  }
  return out;
}

// === export (browser global + CommonJS module.exports) ===
// The module is loaded in node via require() (see board_logic.test.js) and in
// the browser as a global via board.html's <script src="/static/board.js">.
const BoardLogic = { esc, reorder, dropPosition, mergeServerIntoForm,
  parseHash, buildHash, diffConfig, thinkLayout };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardLogic;
} else {
  globalThis.BoardLogic = BoardLogic;
}
})();
