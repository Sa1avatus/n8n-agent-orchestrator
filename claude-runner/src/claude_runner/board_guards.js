// The Guards tab of the dashboard: three views (cards, flow, timelines) over /api/guards and
// /api/guards/history. The shell passes its helpers in mount(): t(key, English), esc(text) and
// setHTML(element, html); the model comes from BoardGuardsView (board_guards_view.js).
(function () {
"use strict";

const V = globalThis.BoardGuardsView;
const REFRESH_MS = 10000;
const VIEWS = ["cards", "flow", "lanes"];
const WINDOWS = ["1h", "24h", "7d"];
const FLOW_NODE = { width: 150, height: 34, gap: 12, top: 44 };
const FLOW_COLUMN_X = { hosts: 10, guards: 250, receivers: 490 };
const PREFS_KEY = "guards.prefs";

let ctx = null;
let timer = null;
let state = { view: "cards", window: "24h", model: null, host: null };
let root = null;

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    if (VIEWS.includes(p.view)) state.view = p.view;
    if (WINDOWS.includes(p.window)) state.window = p.window;
  } catch (e) { /* storage may be blocked: the defaults stay */ }
}
function savePrefs() {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify({ view: state.view, window: state.window })); } catch (e) { /* ignored */ }
}

function badge(status) {
  const cls = V.statusClass(status);
  return `<span class="gt-st ${ctx.esc(cls)}"><span aria-hidden="true">${ctx.esc(V.statusShape(status))}</span> ${ctx.esc(ctx.t(status === "off" ? "tab.disabled" : status, status))}</span>`;
}
function dash(v) { return ctx.esc(v ? v : ctx.t("none", "—")); }
function dryMark(entry) { return entry.dry_run ? ` <span class="gt-mark">${ctx.esc(ctx.t("dry", "dry run"))}</span>` : ""; }
function ago(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 90) return `${s} ${ctx.t("unit.s", "s")}`;
  if (s < 5400) return `${Math.round(s / 60)} ${ctx.t("unit.min", "min")}`;
  return `${Math.round(s / 3600)} ${ctx.t("unit.h", "h")}`;
}

function header(model) {
  const s = model.summary;
  const master = model.enabled ? ctx.t("tab.on", "guards on") : ctx.t("tab.off", "guards off");
  const dry = model.dry_run ? ` · ${ctx.t("dry", "dry run")}` : "";
  return `<div class="gt-head"><b>${ctx.esc(master)}${ctx.esc(dry)}</b> · ${s.ok} ${ctx.esc(ctx.t("ok", "ok"))} · ${s.warning} ${ctx.esc(ctx.t("warning", "warning"))} · ${s.error} ${ctx.esc(ctx.t("error", "error"))} · ${s.off} ${ctx.esc(ctx.t("tab.disabled", "disabled"))}</div>`;
}

function cardsView(model, now) {
  const guard = (g) => {
    const cells = V.strip(g.series).map((r) => `<i class="${ctx.esc(r)}" title="${ctx.esc(ctx.t(r === "none" ? "tab.never" : r, r))}"></i>`).join("");
    const when = g.last_run ? `${ctx.esc(ago(now - g.last_run))} ${ctx.esc(ctx.t("tab.ago", "ago"))}` : ctx.esc(ctx.t("none", "—"));
    return `<div class="gt-card"><div class="gt-name">${badge(g.status)} <b>${ctx.esc(g.name)}</b>${dryMark(g)}</div>` +
      `<div class="gt-sub">${ctx.esc(ctx.t("last", "last"))}: ${dash(g.last_action)}</div>` +
      `<div class="gt-sub">${when} · ${g.error_count} ${ctx.esc(ctx.t("error", "error"))}</div><div class="gt-strip">${cells}</div></div>`;
  };
  const beat = (h) => {
    const age = h.time ? `${ctx.esc(ago(now - Date.parse(h.time) / 1000))} ${ctx.esc(ctx.t("tab.ago", "ago"))}` : ctx.esc(ctx.t("none", "—"));
    return `<div class="gt-card"><div class="gt-name">${badge(h.status)} <b>${ctx.esc(h.name)}</b> <span class="gt-mark">${ctx.esc(ctx.t("readonly", "read-only"))}</span></div>` +
      `<div class="gt-sub">${ctx.esc(ctx.t("result", "result"))}: ${dash(h.last_result)}</div><div class="gt-sub">${age}</div></div>`;
  };
  return `<div class="gt-grid">${model.guards.map(guard).join("")}</div>` +
    (model.heartbeats.length ? `<h3 class="gt-sec">${ctx.esc(ctx.t("tab.heartbeats", "Host watchers"))}</h3><div class="gt-grid">${model.heartbeats.map(beat).join("")}</div>` : "");
}

function flowView(model, now) {
  const flow = V.flowModel(model, now);
  const columns = { hosts: flow.hosts, guards: flow.guards, receivers: flow.receivers };
  const pos = {};
  let rows = 1;
  for (const key of Object.keys(columns)) {
    rows = Math.max(rows, columns[key].length);
    columns[key].forEach((n, i) => {
      pos[n.id] = { x: FLOW_COLUMN_X[key], y: FLOW_NODE.top + i * (FLOW_NODE.height + FLOW_NODE.gap) };
    });
  }
  const height = FLOW_NODE.top + rows * (FLOW_NODE.height + FLOW_NODE.gap);
  const titles = { hosts: ctx.t("tab.host", "Host"), guards: ctx.t("tab.container", "Container"), receivers: ctx.t("tab.receivers", "Receivers") };
  const heads = Object.keys(FLOW_COLUMN_X).map((k) => `<text x="${FLOW_COLUMN_X[k]}" y="22" class="gt-colhead">${ctx.esc(titles[k])}</text>`).join("");
  const wires = flow.wires.filter((w) => pos[w.from] && pos[w.to]).map((w) => {
    const a = pos[w.from];
    const b = pos[w.to];
    const x1 = a.x < b.x ? a.x + FLOW_NODE.width : a.x;
    const x2 = a.x < b.x ? b.x : b.x + FLOW_NODE.width;
    return `<line x1="${x1}" y1="${a.y + FLOW_NODE.height / 2}" x2="${x2}" y2="${b.y + FLOW_NODE.height / 2}" class="gt-wire${w.active ? " lit" : ""}"/>`;
  }).join("");
  const nodes = [].concat(flow.hosts, flow.guards, flow.receivers).map((n) => {
    const p = pos[n.id];
    const cls = V.statusClass(n.status);
    return `<g class="gt-node ${ctx.esc(cls)}"><rect x="${p.x}" y="${p.y}" width="${FLOW_NODE.width}" height="${FLOW_NODE.height}" rx="6"/>` +
      `<text x="${p.x + 10}" y="${p.y + 21}">${V.statusShape(n.status)} ${ctx.esc(n.id)}</text></g>`;
  }).join("");
  return `<div class="gt-flow"><svg viewBox="0 0 650 ${height}" role="img" aria-label="${ctx.esc(ctx.t("tab.flow", "Flow"))}">${heads}${wires}${nodes}</svg></div>`;
}

function lanesView(model, now) {
  const buttons = WINDOWS.map((w) => `<button type="button" data-window="${w}" class="${w === state.window ? "on" : ""}">${ctx.esc(ctx.t("tab.window." + w, w))}</button>`).join("");
  const lane = (g) => {
    const dots = V.lanes(g.series, state.window, now).map((d) =>
      `<b class="gt-dot ${ctx.esc(d.result)}${d.action ? " act" : ""}" style="left:${d.pct}%" title="${d.count}"></b>`).join("");
    return `<div class="gt-lane"><span class="gt-lname">${badge(g.status)} ${ctx.esc(g.name)}</span><div class="gt-track">${dots}</div></div>`;
  };
  return `<div class="gt-windows" role="group">${buttons}</div>${model.guards.map(lane).join("")}`;
}

function viewHtml(model, now) {
  if (state.view === "flow") return flowView(model, now);
  if (state.view === "lanes") return lanesView(model, now);
  return cardsView(model, now);
}

function hostButton(id, decision, label) {
  return `<button type="button" data-host="${ctx.esc(id)}" data-decision="${decision}">${ctx.esc(label)}</button>`;
}
function hostHtml(host) {
  if (!host || !host.visible) return "";
  const rows = host.rows.map((r) => {
    const buttons = (r.confirm ? hostButton(r.id, "confirm", host.confirm) : "") + (r.reject ? hostButton(r.id, "reject", host.reject) : "");
    return `<div class="gt-card"><div class="gt-name"><b>${ctx.esc(r.service)}</b> ${ctx.esc(r.action)} · ${ctx.esc(r.state)}</div>` +
      `<div class="gt-sub">${dash(r.reason)} · ${ctx.esc(ago(r.age_s))} ${ctx.esc(ctx.t("tab.ago", "ago"))}</div><div class="gt-sub">${buttons}</div></div>`;
  }).join("");
  return `<h3 class="gt-sec">${ctx.esc(host.title)}</h3><div class="gt-grid">${rows || dash("")}</div>`;
}

function render() {
  if (!root || !state.model) return;
  const now = Date.now() / 1000;
  const tabs = VIEWS.map((v) => `<button type="button" data-view="${v}" class="${v === state.view ? "on" : ""}">${ctx.esc(ctx.t("tab.view." + v, v))}</button>`).join("");
  ctx.setHTML(root, `${header(state.model)}<div class="gt-views" role="group">${tabs}</div>${viewHtml(state.model, now)}${hostHtml(state.host)}`);
}

async function refresh() {
  try {
    const [g, h, a] = await Promise.all([
      fetch("/api/guards", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
      fetch("/api/guards/history?window=" + encodeURIComponent(state.window), { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
      fetch("/api/host-actions", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
    ]);
    state.model = V.normalise(g, h);
    state.host = V.hostActionsModel({ enabled: Boolean(a && a.enabled), requests: a && a.requests, now: Date.now() / 1000 }, (k, en) => ctx.t(k, en));
    render();
  } catch (e) {
    if (root) ctx.setHTML(root, `<div class="gt-head">${ctx.esc(ctx.t("tab.error", "Could not load the guards."))}</div>`);
  }
}

async function hostDecision(id, decision) {
  const ask = decision === "confirm" ? ctx.t("host.ask.confirm", "Run this host action?") : ctx.t("host.ask.reject", "Reject this host action?");
  if (!window.confirm(ask)) return;
  try {
    const res = await fetch(`/api/host-actions/${encodeURIComponent(id)}/${decision}`, {
      method: "POST",
      headers: { "X-AHAWR-Dashboard": "1" },
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
  } catch (e) {
    window.alert(ctx.t("host.failed", "Host action failed") + ": " + e.message);
  }
  refresh();
}

function onClick(e) {
  const v = e.target.closest("[data-view]");
  const w = e.target.closest("[data-window]");
  const d = e.target.closest("[data-host]");
  if (v) { state.view = v.dataset.view; savePrefs(); render(); }
  if (w) { state.window = w.dataset.window; savePrefs(); refresh(); }
  if (d) hostDecision(d.dataset.host, d.dataset.decision);
}

function mount(el, helpers) {
  ctx = helpers;
  root = el;
  loadPrefs();
  el.addEventListener("click", onClick);
  refresh();
  timer = setInterval(refresh, REFRESH_MS);
}
function unmount() {
  if (timer) clearInterval(timer);
  timer = null;
  if (root) root.removeEventListener("click", onClick);
  root = null;
}
function rerender() { render(); }

globalThis.BoardGuards = { mount, unmount, rerender, refresh };
})();
