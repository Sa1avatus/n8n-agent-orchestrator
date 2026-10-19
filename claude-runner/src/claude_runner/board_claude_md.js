/* board-scope-wrapped */
(function () {
/* Board CLAUDE.md editor (T020B).
 *
 * Loaded by board.html via <script src="/static/board_claude_md.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardClaudeMd(mission_id) renders #boardclaudem: the textarea with the
 * current text (GET /api/board/missions/{id}/claudemd), the four template
 * buttons, and the storage line — where the text is written to (the
 * working-folder file) or kept in the DB ('применяет оператор').
 * Saving is PUT /api/board/missions/{id}/claudemd; the answer's `source`
 * decides which line is shown.
 *
 * Every value from server data flows through claudeMdEsc (board_logic.js's
 * esc, with a local fallback when that script failed to load); data
 * attributes are read through dataset; the textarea is read/written through
 * .value.
 *
 * The templates are fixed UI strings (not server data), so they are written
 * into markup as-is; their text still lands in the textarea through
 * .value, where it is safe.
 */
"use strict";

// claudeMdEsc(): esc() from board_logic.js; a local fallback keeps the view
// safe when that script has not loaded (same pattern as board_mission.js).
// Named so it cannot shadow a top-level name another board script declares.
function claudeMdEsc(v) {
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

// The four templates: button id, and getters for the label and the text the
// button appends (looked up at use, so they follow the chosen language).
const CLAUDEMD_TEMPLATES = [
  {
    id: "bigfile",
    get label() { return t("claudemd.tpl.big_file.label"); },
    get text() { return t("claudemd.tpl.big_file.body"); },
  },
  {
    id: "data",
    get label() { return t("claudemd.tpl.where.label"); },
    get text() { return t("claudemd.tpl.where.body"); },
  },
  {
    id: "nodonts",
    get label() { return t("claudemd.tpl.never.label"); },
    get text() { return t("claudemd.tpl.never.body"); },
  },
  {
    id: "gguf",
    get label() { return t("claudemd.tpl.gguf.label"); },
    get text() { return t("claudemd.tpl.gguf.body"); },
  },
];

// The storage line: where the text currently lives (the GET answer) or where
// the save wrote it (the PUT answer: path, or note).  Server values go
// through claudeMdEsc(); the fixed phrases are UI strings.
function claudeMdStorageLine(answer) {
  const src = answer && answer.source ? answer.source : "";
  if (src === "file") {
    return answer.path
      ? t("claudemd.storage.file", { path: claudeMdEsc(answer.path) })
      : t("claudemd.storage.working_folder");
  }
  if (src === "db") {
    return t("claudemd.storage.db", { note: claudeMdEsc(answer.note || t("claudemd.storage.operator")) });
  }
  return t("claudemd.storage.text", { source: claudeMdEsc(src) });
}

// The editor markup.  Fixed UI strings are interpolated directly; every
// server value (the text, the path, the note) goes through claudeMdEsc().
function claudeMdEditorHtml(answer) {
  return (
    `<h3>${claudeMdEsc(t("claudemd.title"))}</h3>` +
    `<div class="claudemd">` +
      `<textarea name="claudemd_text" spellcheck="false">${claudeMdEsc(answer.text)}</textarea>` +
      `<div class="claudemd-templates">` +
      CLAUDEMD_TEMPLATES.map((tpl) =>
        `<button type="button" class="claudemd-tpl" data-tpl="${claudeMdEsc(tpl.id)}">${claudeMdEsc(tpl.label)}</button>`
      ).join("") +
      `</div>` +
      `<div class="claudemd-storage">` +
        `<span class="claudemd-storage-line"></span>` +
        `<button type="button" class="claudemd-save">${claudeMdEsc(t("claudemd.save"))}</button>` +
      `</div>` +
      `<div class="claudemd-msg"></div>` +
    `</div>`
  );
}

// Render into #boardclaudem (created inside #board when absent) and
// attach the template / save controls.
function claudeMdRender(data) {
  const answer = data.answer || {};
  let el = document.getElementById("boardclaudem");
  if (!el) {
    el = document.createElement("div");
    el.id = "boardclaudem";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  el.innerHTML = claudeMdEditorHtml(answer);
  claudeMdShowStorage(el, answer);
  claudeMdAttachTemplates(el);
  claudeMdAttachSave(el, data);
}

// Show the storage line (textContent only).
function claudeMdShowStorage(el, answer) {
  const line = el.querySelector(".claudemd-storage-line");
  if (line) line.textContent = claudeMdStorageLine(answer);
}

// Template buttons append their (fixed UI) text to the textarea through
// .value; the template id is read through dataset.
function claudeMdAttachTemplates(el) {
  for (const btn of el.querySelectorAll(".claudemd-tpl")) {
    if (btn.dataset.attached === "1") continue;
    btn.dataset.attached = "1";
    btn.addEventListener("click", () => {
      const tpl = CLAUDEMD_TEMPLATES.find((tpl) => tpl.id === btn.dataset.tpl);
      if (!tpl) return;
      const ta = el.querySelector('[name="claudemd_text"]');
      if (!ta) return;
      ta.value += tpl.text;
    });
  }
}

// The save button posts the textarea value (PUT) and shows the answer.
function claudeMdAttachSave(el, data) {
  const btn = el.querySelector(".claudemd-save");
  if (!btn || !data || !data.missionId) return;
  const msg = el.querySelector(".claudemd-msg");
  btn.addEventListener("click", () => {
    const ta = el.querySelector('[name="claudemd_text"]');
    const text = ta ? ta.value : "";
    claudeMdPut("/api/board/missions/" + encodeURIComponent(data.missionId) + "/claudemd", {
      text,
      who: "board",
    })
      .then((answer) => {
        if (msg) msg.textContent = "";
        claudeMdShowStorage(el, answer);
      })
      .catch((e) => {
        if (msg) msg.textContent = t("claudemd.save_failed", { error: e.message || t("error.unknown") });
      });
  });
}

// BoardClaudeMd(mission_id): load the current CLAUDE.md and render the
// editor.  Called by the shell (or a link) for a mission's CLAUDE.md view.
async function BoardClaudeMd(missionId) {
  const answer = await claudeMdGet("/api/board/missions/" + encodeURIComponent(missionId) + "/claudemd");
  claudeMdRender({ answer, missionId });
}

// One same-origin GET helper (the board API only); named so it cannot
// shadow the board_actions.js global.
async function claudeMdGet(url) {
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

// PUT a JSON body to one endpoint (the CLAUDE.md route, with the board
// mutation header, like the other board mutations).
async function claudeMdPut(url, body) {
  const res = await fetch(url, {
    method: "PUT",
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

// === export (browser global + CommonJS module.exports) ===
const BoardClaudeMdView = {
  esc: claudeMdEsc, TEMPLATES: CLAUDEMD_TEMPLATES, storageLine: claudeMdStorageLine,
  editorHtml: claudeMdEditorHtml, render: claudeMdRender, BoardClaudeMd,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardClaudeMdView;
} else if (typeof document !== "undefined") {
  globalThis.BoardClaudeMd = BoardClaudeMd;
  globalThis.BoardClaudeMdView = BoardClaudeMdView;
}
})();
