// The Control tab of the dashboard: the host agent status strip, the four ops buttons with their
// prepare/confirm steps, and the command history, over /api/board/ops/status and
// /api/board/ops/commands. The shell passes its helpers in mount(): t(key, English), esc(text),
// setHTML(element, html) and fetchJson(url, options) (board_actions.js); the model comes from
// BoardControlView (board_control_view.js).
(function () {
"use strict";

const V = globalThis.BoardControlView;
const POLL_MS = 5000;
const MAX_POLLS = 120;
const HISTORY_LIMIT = 20;
const STATUS_URL = "/api/board/ops/status";
const COMMANDS_URL = "/api/board/ops/commands?limit=" + HISTORY_LIMIT;
const PREPARE_URL = "/api/board/ops/commands/prepare";
const CONFIRM_URL = "/api/board/ops/commands/confirm";

let ctx = null;
let root = null;
let timer = null;
let polls = 0;
let model = null;
let pending = null;
let message = null;

function dashEsc(v) { return ctx.esc(v ? v : ctx.t("none", "—")); }

function statusRow(label, value, cls) {
  return `<div class="ct-row"><span class="ct-label">${ctx.esc(label)}</span><span class="ct-st ${ctx.esc(cls)}">${ctx.esc(value)}</span></div>`;
}

function statusHtml() {
  const agent = model.agent;
  const current = model.current;
  const rows = [
    statusRow(ctx.t("control.row_agent", "Host agent"), agent.label, agent.status === "alive" ? "ok" : "error"),
    statusRow(ctx.t("control.row_queue", "Queue"), model.queue.label, model.queue.running ? "ok" : "off"),
    `<div class="ct-row"><span class="ct-label">${ctx.esc(ctx.t("control.row_mission", "Mission"))}</span><span>${dashEsc(current.mission)}</span></div>`,
    `<div class="ct-row"><span class="ct-label">${ctx.esc(ctx.t("control.row_task", "Task"))}</span><span>${dashEsc(current.task)}</span></div>`,
  ];
  const banner = agent.banner ? `<div class="ct-banner">${ctx.esc(agent.banner)}</div>` : "";
  // rows and banner are built from escaped parts above
  return '<div class="ct-strip">' + rows.join("") + "</div>" + banner;
}

function buttonsHtml() {
  const buttons = model.buttons.map((b) => {
    const title = b.disabled_reason ? ` title="${ctx.esc(b.disabled_reason)}"` : "";
    const disabled = b.enabled ? "" : " disabled";
    return '<button type="button" data-action="' + ctx.esc(b.action) + '"' + disabled + title + ">"
      + ctx.esc(b.label) + "</button>";
  });
  // each button is built from escaped parts above
  return '<div class="ct-buttons" role="group">' + buttons.join("") + "</div>";
}

function pendingHtml() {
  if (!pending) return "";
  const preview = pending.command_preview ? `<pre class="ct-preview">${ctx.esc(pending.command_preview)}</pre>` : "";
  const interrupted = pending.interrupted_preview
    ? `<div class="ct-sub">${ctx.esc(ctx.t("control.interrupts", "Interrupts"))}: ${ctx.esc(pending.interrupted_preview)}</div>` : "";
  return `<div class="ct-confirm"><div class="ct-name"><b>${ctx.esc(pending.label)}</b></div>` +
    `<div class="ct-sub">${ctx.esc(pending.consequences)}</div>` + interrupted + preview +
    `<div class="ct-buttons"><button type="button" data-confirm>${ctx.esc(ctx.t("control.confirm", "Confirm"))}</button>` +
    `<button type="button" data-cancel>${ctx.esc(ctx.t("control.cancel", "Cancel"))}</button></div></div>`;
}

function historyHtml() {
  if (!model.history.length) {
    return `<div class="ct-sub">${ctx.esc(ctx.t("control.no_history", "No commands yet."))}</div>`;
  }
  const rows = model.history.map((c) =>
    `<div class="ct-row"><span class="ct-label">${ctx.esc(c.requested_at)}</span>` +
    `<span>${ctx.esc(c.action)}</span><span class="ct-st ${ctx.esc(c.status)}">${ctx.esc(c.status)}</span></div>`);
  return `<h3 class="ct-sec">${ctx.esc(ctx.t("control.history", "History"))}</h3>` + rows.join("");
}

function messageHtml() {
  return message ? `<div class="ct-banner">${ctx.esc(message)}</div>` : "";
}

function render() {
  if (!root || !model) return;
  const refresh = polls >= MAX_POLLS
    ? `<button type="button" data-refresh>${ctx.esc(ctx.t("control.resume_polling", "Refresh"))}</button>` : "";
  ctx.setHTML(root, statusHtml() + buttonsHtml() + messageHtml() + pendingHtml() + historyHtml() + refresh);
}

function fail(e) {
  message = e.message || ctx.t("control.error", "Could not reach the host agent.");
  render();
}

async function refresh() {
  if (!root) return;
  try {
    const [status, history] = await Promise.all([ctx.fetchJson(STATUS_URL), ctx.fetchJson(COMMANDS_URL)]);
    model = V.controlModel(status, history, new Date().toISOString(), ctx.t);
    render();
  } catch (e) {
    fail(e);
  }
}

function post(url, body) {
  return ctx.fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-AHAWR-Dashboard": "1" },
    body: JSON.stringify(body),
  });
}

async function onAction(action) {
  message = null;
  try {
    const res = await post(PREPARE_URL, { action, args: {} });
    if (res.confirm_token) {
      pending = {
        action,
        args: res.args,
        label: model.buttons.find((b) => b.action === action).label,
        confirm_token: res.confirm_token,
        consequences: res.consequences,
        command_preview: res.command_preview,
        interrupted_preview: res.interrupted_preview,
      };
      render();
      return;
    }
    message = ctx.t("control.queued", "Command queued.");
    await refresh();
  } catch (e) {
    fail(e);
  }
}

async function onConfirm() {
  if (!pending) return;
  const sent = pending;
  pending = null;
  try {
    await post(CONFIRM_URL, { action: sent.action, args: sent.args, token: sent.confirm_token });
    message = ctx.t("control.queued", "Command queued.");
    await refresh();
  } catch (e) {
    fail(e);
  }
}

function startPolling() {
  stopPolling();
  polls = 0;
  timer = setInterval(onPoll, POLL_MS);
}

function stopPolling() {
  if (timer) clearInterval(timer);
  timer = null;
}

function onPoll() {
  polls += 1;
  if (polls >= MAX_POLLS) stopPolling();
  refresh();
}

function onClick(e) {
  const action = e.target.closest("[data-action]");
  if (action) { onAction(action.dataset.action); return; }
  if (e.target.closest("[data-confirm]")) { onConfirm(); return; }
  if (e.target.closest("[data-cancel]")) { pending = null; render(); return; }
  if (e.target.closest("[data-refresh]")) { refresh(); startPolling(); }
}

function mount(el, helpers) {
  ctx = helpers;
  root = el;
  el.addEventListener("click", onClick);
  globalThis.addEventListener("pagehide", unmount);
  refresh();
  startPolling();
}

function unmount() {
  stopPolling();
  if (root) root.removeEventListener("click", onClick);
  globalThis.removeEventListener("pagehide", unmount);
  root = null;
  pending = null;
}

globalThis.BoardControl = { mount, unmount, refresh };
})();
