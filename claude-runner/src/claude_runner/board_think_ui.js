/* board-scope-wrapped */
(function () {
/* Board thinking-policy panel (part C).
 *
 * Loaded by board.html via <script src="/static/board_think_ui.js"> and also
 * usable from node via CommonJS require (UMD-style export below).
 *
 * BoardThinkUI.render(container, missionId) renders the mission's thinking
 * policy panel: the policy text (a textarea, initialized from the mission
 * config's `worker_think_policy`) with a Validate button
 * (POST /api/board/thinking/policy, the server's problems listed under the
 * policy), the variant layout preview of tasks 1-12 (one POST
 * /api/board/thinking/variant per task number, at most LAYOUT_PARALLEL at a
 * time, so a broken policy is caught by the validate step instead of
 * 12x400s), and the per-variant statistics with the uplift against the
 * control (or "мало данных, нужно ещё N попыток на ветку") plus the
 * suggested weights as plain text (Thompson sampling).
 *
 * Every value taken from server data flows through thinkEsc(); the layout
 * and statistics rows are shown as plain text (textContent), not HTML.
 *
 * Data shape (from GET /api/board/missions/{id}):
 *   detail.mission: { mission_id, ... }  (a flat detail.mission_id is
 *                    accepted as well)
 *   detail.config:  { ..., worker_think_policy: {...}|string|none, ... }
 */
"use strict";

// The server's MIN_ARM (board_think.py: the uplift is computed only from
// this many attempts per variant); shown in the not-enough-data line when
// no attempt rows are available yet.
const MIN_ARM = 100;

// How many /thinking/variant calls run at once.
const LAYOUT_PARALLEL = 3;

// The panel state kept across re-renders (per container): the policy text
// (the textarea's value), the layout rows, the validate problems, the stats
// and the weights.  Every render re-wires the controls from the template,
// and every action re-renders with the kept state, so nothing is wiped.
const panels = new Map();
function panelState(el) {
  let s = panels.get(el);
  if (!s) {
    s = { text: "", layout: null, problems: null, stats: null, weights: null };
    panels.set(el, s);
  }
  return s;
}

// esc() from board_logic.js, with the same local fallback the other views use.
function thinkEsc(v) {
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

// One same-origin POST helper for this panel (its own name; the thinking
// routes are pure — no board mutation header).
async function thinkPost(url, body) {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
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

// Format a policy value for the textarea: an object is pretty-printed JSON
// (CRLF-normalised like the rest of the board), a string is used as-is.
function policyText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value.replace(/\r\n?/g, "\n");
  try {
    return JSON.stringify(value, null, 2).replace(/\r\n?/g, "\n");
  } catch (e) {
    return String(value);
  }
}

// Parse the textarea value into the policy object to post.
function policyFromText(text) {
  const s = String(text || "").trim();
  if (s === "") return null;
  try {
    const v = JSON.parse(s);
    return typeof v === "object" && v !== null ? v : { policy: v };
  } catch (e) {
    return s; // the server reports the text as broken JSON
  }
}

// The not-enough-data line: the server's own text when it answers,
// otherwise the MIN_ARM fallback (the board has no read route that serves
// per-variant attempt rows yet, so the panel always shows the fallback).
function notEnoughText(uplift) {
  if (uplift && uplift.text) return uplift.text;
  return "мало данных, нужно ещё " + MIN_ARM + " попыток на ветку";
}

// Pure render: the policy block, the layout block, the statistics block and
// the weights line.  layout is [12 strings|null] (null while not loaded),
// problems the validate answer's problems (or []), stats the thinking/stats
// answer ({variants: [...], control: ...}) or null, weights the thompson
// array or null.  Every server value flows through thinkEsc().
function renderThinkHtml(policyTextValue, layout, problems, stats, weights) {
  const rows = Array.isArray(layout) ? layout : [];
  const layoutRows = rows.map((v, i) =>
    `<tr><td class="tktask">` + thinkEsc(i + 1) + `</td><td class="tkval">` +
    (v == null ? "—" : thinkEsc(v)) + `</td></tr>`).join("");
  const probRows = (problems || []).map((p) =>
    `<div class="tkprob">` + thinkEsc(p) + `</div>`).join("");
  const controlVariant = stats && stats.control ? stats.control : "off";
  const variants = stats && Array.isArray(stats.variants) ? stats.variants : [];
  const statRows = variants.map((v) => {
    const uplift = v.uplift;
    const upliftText = v.variant === controlVariant
      ? "control"
      : (uplift && uplift.pass_diff_pct != null
        ? "+" + Number(uplift.pass_diff_pct).toFixed(1) + " пп" +
          (uplift.pass_interval ? " (90%: " +
          uplift.pass_interval.map((x) => Number(x).toFixed(1)).join("…") + ")" : "")
        : notEnoughText(uplift));
    return `<tr><td class="tkval">` + thinkEsc(v.variant) + `</td>` +
      `<td>` + thinkEsc(v.attempts) + `</td><td>` + thinkEsc(v.passes) + `</td>` +
      `<td>` + thinkEsc(v.acceptance_share) + `</td><td>` + thinkEsc(v.minutes_per_attempt) +
      `</td><td>` + thinkEsc(v.thinking_tokens) + `</td><td class="tkuplift">` +
      thinkEsc(upliftText) + `</td></tr>`;
  }).join("");
  const weightLine = Array.isArray(weights) && weights.length
    ? "recommended weights (Thompson, seed 7): " +
        weights.map((w) => thinkEsc(w.variant) + " " + w.suggested_weight).join(", ")
    : "";
  return (
    `<h3>Thinking policy</h3>` +
    `<div class="tkpolicy">` +
    `<textarea name="think_policy" spellcheck="false">` + thinkEsc(policyTextValue) + `</textarea>` +
    `<button type="button" class="tk-validate">Validate</button>` +
    `</div>` +
    `<div class="tkprobs">` +
      (probRows || `<div class="tkok">Policy is valid.</div>`) +
    `</div>` +
    `<h3>Layout (tasks 1–12)</h3>` +
    `<button type="button" class="tk-layout">Preview layout</button>` +
    (rows.length
      ? `<table class="tklayout">` + layoutRows + `</table>`
      : `<div class="tkempty">No layout yet.</div>`) +
    `<h3>Statistics</h3>` +
    `<button type="button" class="tk-stats">Statistics</button>` +
    (statRows
      ? `<table class="tkstats">` +
        `<tr><th>variant</th><th>attempts</th><th>passes</th><th>accept share</th>` +
        `<th>min/attempt</th><th>think tokens</th><th>uplift vs control</th></tr>` +
        statRows + `</table>`
      : variants.length
        ? ""
        : `<div class="tkempty">` +
          thinkEsc("No attempts recorded yet: " + notEnoughText(null)) +
          `</div>`) +
    `<div class="tkweights">` + thinkEsc(weightLine) + `</div>`
  );
}

// Resolve the container: an element or a string id (default "boardthink");
// create it inside #board when missing.
function resolveInto(container) {
  let el = container;
  if (el == null || (el && typeof el === "string")) {
    el = document.getElementById(el || "boardthink");
  }
  if (!el) {
    el = document.createElement("div");
    el.id = "boardthink";
    const root = document.getElementById("board");
    if (root) root.appendChild(el);
  }
  return el;
}

// Render into the container and re-wire the controls on every render (the
// buttons live in the template, so a re-render re-attaches them); the kept
// state (policy text, layout, problems, stats, weights) is passed through
// by the caller, so a Validate click keeps the layout and the statistics.
// Clears the shell's #boardstate line on success.
function render(container, missionId, policyTextValue, layout, problems, stats, weights) {
  const el = resolveInto(container);
  const s = panelState(el);
  s.text = policyTextValue;
  s.layout = layout;
  s.problems = problems;
  s.stats = stats;
  s.weights = weights;
  el.innerHTML = renderThinkHtml(policyTextValue, layout, problems, stats, weights);
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = "";
    state.className = "";
  }
  attachThinkControls(el, missionId);
}

// Attach the panel controls.  The buttons live in the template, which each
// render rebuilds, so the handlers are re-wired on every render; each
// action reads the kept state and re-renders with it.
function attachThinkControls(el, missionId) {
  const s = panelState(el);
  const msg = document.createElement("div");
  msg.className = "tkmsg";
  el.appendChild(msg);
  const validateBtn = el.querySelector(".tk-validate");
  if (validateBtn) {
    validateBtn.addEventListener("click", () => {
      const ta = el.querySelector('[name="think_policy"]');
      const text = ta && ta.value !== undefined ? ta.value : s.text;
      thinkPost("/api/board/thinking/policy", { policy: policyFromText(text) })
        .then((answer) => {
          const problems = Array.isArray(answer.problems) ? answer.problems : [];
          render(el, missionId, text, s.layout, problems, s.stats, s.weights);
        })
        .catch((e) => {
          if (msg) msg.textContent = "validation failed: " + (e.message || "error");
        });
    });
  }
  const layoutBtn = el.querySelector(".tk-layout");
  if (layoutBtn) {
    layoutBtn.addEventListener("click", () => {
      const ta = el.querySelector('[name="think_policy"]');
      const text = ta && ta.value !== undefined ? ta.value : s.text;
      previewLayout(el, missionId, text, s);
    });
  }
  const statsBtn = el.querySelector(".tk-stats");
  if (statsBtn) {
    statsBtn.addEventListener("click", () => {
      const ta = el.querySelector('[name="think_policy"]');
      const text = ta && ta.value !== undefined ? ta.value : s.text;
      loadStats(el, missionId, text, s);
    });
  }
}

// Preview the layout of tasks 1-12 via POST /thinking/variant per task
// number, at most LAYOUT_PARALLEL at a time (the layout route, when the
// server answers it, would do the same in one call; this keeps the preview
// working without it and rate-limits the per-task calls).
async function previewLayout(el, missionId, text, s) {
  const policy = policyFromText(text);
  const msg = el.querySelector(".tkmsg");
  const layout = new Array(12).fill(null);
  let next = 0;
  async function worker() {
    while (next < 12) {
      const i = next++;
      try {
        const answer = await thinkPost(
          "/api/board/thinking/variant",
          { policy, task_number: i + 1 });
        layout[i] = answer.variant;
      } catch (e) {
        layout[i] = null;
      }
    }
  }
  await Promise.all(Array.from({ length: Math.min(LAYOUT_PARALLEL, 12) }, worker));
  render(el, missionId, text, layout, s.problems, s.stats, s.weights);
  if (msg) msg.textContent = "";
}

// Load the per-variant statistics (POST /thinking/stats) and the suggested
// weights (POST /thinking/thompson) and render both.
async function loadStats(el, missionId, text, s) {
  const policy = policyFromText(text);
  const msg = el.querySelector(".tkmsg");
  let stats = null;
  let weights = null;
  try {
    const answer = await thinkPost("/api/board/thinking/stats", {
      policy, control: "off", attempts: [] });
    stats = answer || null;
  } catch (e) {
    stats = null;
  }
  try {
    const answer = await thinkPost("/api/board/thinking/thompson", {
      policy, seed: 7, attempts: [] });
    weights = answer && Array.isArray(answer.thompson) ? answer.thompson : null;
  } catch (e) {
    weights = null;
  }
  render(el, missionId, text, s.layout, s.problems, stats, weights);
  if (msg) msg.textContent = "";
}

// BoardThinkUI.render(container, missionId): load the mission's config and
// render the panel in the container; the policy text starts from the
// mission config's `worker_think_policy` (the JSON the mission runs with).
// Accepts both detail.mission.mission_id and a flat detail.mission_id.
async function renderThinkUI(container, missionId) {
  const path = "/api/board/missions/" + encodeURIComponent(missionId);
  let detail;
  try {
    detail = await fetch(path, { cache: "no-store" });
    detail = await detail.json().catch(() => ({}));
    const mission = detail && detail.mission ? detail.mission : detail;
    if (!mission || (mission.mission_id == null && (detail && detail.mission_id == null))) {
      throw new Error("mission not found");
    }
  } catch (e) {
    renderError(container, e.message || e);
    return;
  }
  const config = (detail && detail.config) || {};
  const policyValue = config.worker_think_policy;
  render(container, missionId, policyText(policyValue), null, null, null, null);
}

// Error state: put the escaped message in a .tkerror div in the container
// and flag the shell's #boardstate line.
function renderError(container, message) {
  resolveInto(container).innerHTML =
    `<div class="tkerror">` + thinkEsc(message) + `</div>`;
  const state = document.getElementById("boardstate");
  if (state) {
    state.textContent = message;
    state.className = "error";
  }
}

// === export (browser global + CommonJS module.exports) ===
const BoardThinkUI = {
  render: renderThinkUI, renderHtml: renderThinkHtml, renderInto: render,
  esc: thinkEsc, policyText, policyFromText, notEnoughText,
  previewLayout, loadStats, thinkPost, LAYOUT_PARALLEL, MIN_ARM,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardThinkUI;
} else if (typeof document !== "undefined") {
  globalThis.BoardThinkUI = BoardThinkUI;
}
})();
