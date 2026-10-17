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

// The four templates: button id, button label, text the button appends.
// Each is a fixed UI string (not server data).
const CLAUDEMD_TEMPLATES = [
  {
    id: "bigfile",
    label: "большой файл: не читать целиком",
    text:
      "## большой файл: не читать целиком\n\n" +
      "Большой файл (включая GGUF и другие бинарные данные) не открывать " +
      "целиком в памяти: читать по частям (head/tail, построчно, mmap) и " +
      "не грузить весь файл в RAM.\n",
  },
  {
    id: "data",
    label: "где лежат данные",
    text:
      "## где лежат данные\n\n" +
      "Данные миссии: рабочая папка — <путь>; состояния и журналы — в " +
      "хранилище (store); корпуса поиска — в retrieval-сервисе.\n",
  },
  {
    id: "nodonts",
    label: "чего не делать",
    text:
      "## чего не делать\n\n" +
      "Не менять живые сущности прогона (рабочая папка, ключ состояния, " +
      "корпуса) во время миссии; не читать большие бинарные файлы целиком; " +
      "не удалять чужие несохранённые изменения.\n",
  },
  {
    id: "gguf",
    label: "GGUF: не разбирать руками",
    text:
      "## разбор больших бинарных файлов (GGUF)\n\n" +
      "разбор больших бинарных файлов (GGUF) руками уводит память: используйте готовые дампы и пакет gguf.\n",
  },
];

// The storage line: where the text currently lives (the GET answer) or where
// the save wrote it (the PUT answer: path, or note).  Server values go
// through claudeMdEsc(); the fixed phrases are UI strings.
function claudeMdStorageLine(answer) {
  const src = answer && answer.source ? answer.source : "";
  if (src === "file") {
    return answer.path
      ? "Запись: файл " + claudeMdEsc(answer.path)
      : "Текст: файл в рабочей папке";
  }
  if (src === "db") {
    return "Текст: в базе (config `claudemd`) — " + claudeMdEsc(answer.note || "применяет оператор");
  }
  return "Текст: " + claudeMdEsc(src);
}

// The editor markup.  Fixed UI strings are interpolated directly; every
// server value (the text, the path, the note) goes through claudeMdEsc().
function claudeMdEditorHtml(answer) {
  return (
    `<h3>CLAUDE.md</h3>` +
    `<div class="claudemd">` +
      `<textarea name="claudemd_text" spellcheck="false">${claudeMdEsc(answer.text)}</textarea>` +
      `<div class="claudemd-templates">` +
      CLAUDEMD_TEMPLATES.map((t) =>
        `<button type="button" class="claudemd-tpl" data-tpl="${claudeMdEsc(t.id)}">${claudeMdEsc(t.label)}</button>`
      ).join("") +
      `</div>` +
      `<div class="claudemd-storage">` +
        `<span class="claudemd-storage-line"></span>` +
        `<button type="button" class="claudemd-save">Сохранить</button>` +
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
      const tpl = CLAUDEMD_TEMPLATES.find((t) => t.id === btn.dataset.tpl);
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
        if (msg) msg.textContent = "сохранение не удалось: " + (e.message || "ошибка");
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
